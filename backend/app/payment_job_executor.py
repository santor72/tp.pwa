"""Same fenced execution path for Redis consumers and Postgres fallback."""
import asyncio
from datetime import UTC, datetime, timedelta

from app.errors import ApiError
from app.payment_event_repository import FINISHED_JOB_STATES
from app.payment_execution import PaymentExecution, PaymentLeaseLost, PaymentWriteUnknown, current_execution
from app.payment_write_recovery import recover_write
from app.payment_telemetry import Trace, current_trace, current_parent, span, elapsed


class PaymentJobExecutor:
    def __init__(self, services, repository, settings, owner):
        self.services = services
        self.repository = repository
        self.settings = settings
        self.owner = owner

    async def execute(self, job_id, *, event_id=None, stream_message_id=None, received_at=None):
        if not self.settings.payment_telemetry_enabled:
            return await self._execute(job_id)
        trace = Trace()
        token = current_trace.set(trace)
        parent_token = current_parent.set(None)
        try:
            with span('job_execution', 'operation', job_id=str(job_id),
                event_id=str(event_id) if event_id else None, consumer=self.owner,
                source='redis' if event_id else 'database'):
                return await self._execute(job_id, stream_message_id=stream_message_id, received_at=received_at)
        finally:
            current_trace.reset(token)
            current_parent.reset(parent_token)
            await trace.save(self.services.payment_repository)

    async def _execute(self, job_id, *, stream_message_id=None, received_at=None):
        job = await self.repository.get_job(job_id)
        if job is None or job.state in FINISHED_JOB_STATES:
            return True
        trace = current_trace.get()
        if trace is not None:
            trace.transaction_id = job.transaction_id
            if stream_message_id and received_at:
                # Redis-generated ID carries XADD time. Unlike the DB publication
                # marker it is available even if delivery races publisher commit.
                try:
                    published = datetime.fromtimestamp(int(stream_message_id.split('-')[0]) / 1000, UTC)
                except (ValueError, TypeError, OverflowError, OSError):
                    published = None
                if published is not None:
                    elapsed(job.available_at, 'outbox_wait', duration_ms=(published - job.available_at).total_seconds() * 1000,
                        cross_system_clocks=True)
                    elapsed(published, 'transport_wait', duration_ms=(received_at - published).total_seconds() * 1000,
                        cross_system_clocks=True)
        with span('lease_acquire', 'wait') as timing:
            claim = await self.repository.claim(job_id, self.owner, self.settings.payment_lease_seconds)
            timing['acquired'] = claim is not None
        if claim is None:
            return False
        # A callback/retry may update the queued job between the initial read and
        # lease acquisition. Use the committed intent/budget under our ownership.
        job = await self.repository.get_job(job_id)
        if job is None:
            return False
        with span('job_attempt', 'marker', kind_name=job.kind, generation=job.generation, attempt=job.attempt_count + 1):
            pass
        elapsed(job.available_at, 'job_ready_wait')
        execution = PaymentExecution(self.repository, claim, scope=job.details.get('scope', job.kind))
        token = current_execution.set(execution)
        heartbeat = asyncio.create_task(self.heartbeat(claim))
        work = asyncio.create_task(self.perform(job))
        try:
            done, _ = await asyncio.wait({heartbeat, work}, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat  # raises if ownership/storage is lost
                raise PaymentLeaseLost()
            result = await work
            next_kind, next_at = None, None
            if result and result.bitrix_payment_id and result.status not in {'paid', 'canceled', 'expired', 'failed'}:
                next_kind = 'reconciliation'
                next_at = datetime.now(UTC) + timedelta(seconds=self.settings.bx24_payment_poll_interval_seconds)
            elif result and result.bitrix_payment_id and result.status == 'paid' and (
                not result.paid_timeline_created or not result.paid_activity_created
            ):
                next_kind = 'reconciliation'
                next_at = datetime.now(UTC)
            return await self.repository.finish(claim, next_kind=next_kind, next_at=next_at)
        except PaymentLeaseLost:
            return False  # do not ACK; a new owner must reconcile the durable work
        except PaymentWriteUnknown:
            attempt = job.attempt_count + 1
            retry = attempt <= 3
            return await self.repository.finish(claim, state='superseded' if retry else 'needs_reconciliation', error='EXTERNAL_WRITE_UNKNOWN',
                next_kind=job.kind if retry else None, next_at=datetime.now(UTC) + timedelta(seconds=2 ** attempt),
                details=job.details, attempt_count=attempt)
        except Exception as exc:
            attempt = job.attempt_count + 1
            retry = attempt <= self.settings.bx24_worker_max_retries
            code = exc.code if isinstance(exc, ApiError) else 'PAYMENT_PROCESSING_FAILED'
            try:
                return await self.repository.finish(claim, state='superseded' if retry else 'failed', error=code,
                    next_kind=job.kind if retry else None,
                    next_at=datetime.now(UTC) + timedelta(seconds=min(300, 2 ** min(attempt, 8))),
                    details=job.details, attempt_count=attempt)
            except PaymentLeaseLost:
                return False
        finally:
            for task in (heartbeat, work):
                if not task.done(): task.cancel()
            await asyncio.gather(heartbeat, work, return_exceptions=True)
            current_execution.reset(token)

    async def heartbeat(self, claim):
        while True:
            await asyncio.sleep(self.settings.payment_heartbeat_seconds)
            try:
                renewed = await self.repository.renew(claim, self.settings.payment_lease_seconds)
            except Exception as exc:
                raise PaymentLeaseLost() from exc
            if not renewed:
                raise PaymentLeaseLost()

    async def perform(self, job):
        transaction = await self.services.payment_repository.get(job.transaction_id)
        if transaction is None:
            return None
        for write in await self.repository.unknown_writes(job.transaction_id):
            result = await recover_write(self.services.bitrix, write, transaction)
            if result is None:
                raise PaymentWriteUnknown()
            await self.repository.confirm_recovered(current_execution.get().claim, write.id, result)
        if job.kind == 'formation':
            action = job.details.get('action')
            if action in {'cancel', 'resend'}:
                result = await self.services.payment_service.execute_command(transaction, action)
                if action == 'cancel' and result.bitrix_payment_id and result.status not in {'paid', 'canceled', 'expired', 'failed'}:
                    return await self.services.payment_status.handle(result.bitrix_payment_id)
                return result
            if transaction.status not in {'paid', 'canceled', 'expired', 'failed'} and not transaction.bitrix_payment_id and transaction.expires_at <= datetime.now(UTC):
                return await self.services.payment_repository.update(transaction.id, status='expired', current_step='expired', next_attempt_at=None)
            if job.details.get('selection'):
                return await self.services.payment_service.process(job.transaction_id, selected=job.details['selection'])
            return await self.services.payment_service.process(job.transaction_id)
        if job.kind == 'reconciliation' and transaction.bitrix_payment_id:
            return await self.services.payment_status.handle(transaction.bitrix_payment_id)
        return transaction
