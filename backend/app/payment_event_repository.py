"""Durable payment work. All owners lock payment -> job -> lease, in that order."""
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import exists, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert

from app.models import PaymentCallbackInbox, PaymentEventQuarantine, PaymentExecutionLease, PaymentExternalWrite, PaymentJob, PaymentOutbox, PaymentRequestBudget, PaymentTransaction, PaymentTransactionEvent
from app.payment_events import PaymentEvent
from app.payment_execution import PaymentLeaseLost, PaymentWriteUnknown

ACTIVE_JOB_STATES = {"ready", "running", "retry_wait"}
FINISHED_JOB_STATES = {"completed", "superseded", "failed", "needs_reconciliation"}
EVENT_TYPES = {"formation": "payment.formation_requested", "reconciliation": "payment.reconciliation_requested"}


def resumable_status(tx):
    if not tx.bitrix_contact_id: return 'draft'
    if not tx.bitrix_invoice_id: return 'client_resolved'
    if not tx.bitrix_product_row_id: return 'invoice_created'
    if not tx.bitrix_payment_id: return 'product_added'
    if not tx.payment_product_linked or not (tx.payment_url or tx.payment_short_url): return 'payment_created'
    return tx.send_status if tx.send_status in {'send_queued', 'sent', 'send_failed'} else 'link_created'


@dataclass(frozen=True)
class PaymentClaim:
    job_id: UUID
    transaction_id: UUID
    owner: str
    token: int


class PaymentEventRepository:
    def __init__(self, sessions, expected_mode=None):
        self.sessions = sessions
        self.expected_mode = expected_mode

    async def check_mode(self, session):
        if self.expected_mode is not None:
            from app.payment_runtime_registry import require_mode
            await require_mode(session, self.expected_mode)

    async def get_job(self, job_id):
        async with self.sessions() as session:
            return await session.get(PaymentJob, job_id)

    async def due_jobs(self, kind, limit=10):
        async with self.sessions() as session:
            return list(await session.scalars(select(PaymentJob.id).outerjoin(PaymentExecutionLease,
                PaymentExecutionLease.transaction_id == PaymentJob.transaction_id).where(
                    PaymentJob.kind == kind, PaymentJob.state.in_(ACTIVE_JOB_STATES),
                    PaymentJob.available_at <= func.clock_timestamp(),
                    or_(PaymentExecutionLease.transaction_id.is_(None), PaymentExecutionLease.lease_until <= func.clock_timestamp()),
                ).order_by(PaymentJob.available_at, PaymentJob.id).limit(limit)))

    async def recover_publications(self, stale_seconds=30, limit=100):
        async with self.sessions.begin() as session:
            now = await session.scalar(select(func.clock_timestamp()))
            rows = list(await session.scalars(select(PaymentOutbox).join(PaymentJob, PaymentJob.id == PaymentOutbox.job_id)
                .outerjoin(PaymentExecutionLease, PaymentExecutionLease.transaction_id == PaymentJob.transaction_id).where(
                    PaymentOutbox.completed_at.is_(None), PaymentJob.state.in_(ACTIVE_JOB_STATES), PaymentJob.available_at <= now,
                    PaymentOutbox.last_published_at <= now - timedelta(seconds=stale_seconds),
                    or_(PaymentExecutionLease.transaction_id.is_(None), PaymentExecutionLease.lease_until <= now),
                ).order_by(PaymentOutbox.last_published_at).with_for_update(of=PaymentOutbox, skip_locked=True).limit(limit)))
            for row in rows:
                row.last_published_at = None
                row.next_publish_at = now
            return len(rows)

    async def backfill(self, limit=100):
        """Only operations with no jobs at all: do not resurrect exhausted work."""
        async with self.sessions.begin() as session:
            rows = list(await session.scalars(select(PaymentTransaction).where(
                ~exists(select(PaymentJob.id).where(PaymentJob.transaction_id == PaymentTransaction.id)),
                or_(PaymentTransaction.status.in_({'draft', 'resolving_client', 'client_resolved', 'invoice_created',
                    'product_added', 'payment_created', 'link_created', 'send_queued', 'sent', 'send_failed'}),
                    (PaymentTransaction.status == 'paid') &
                    ((PaymentTransaction.paid_timeline_created == False) | (PaymentTransaction.paid_activity_created == False) |
                     (PaymentTransaction.formation_timeline_created == False) | (PaymentTransaction.formation_activity_created == False))),
            ).order_by(PaymentTransaction.created_at).with_for_update(skip_locked=True).limit(limit)))
            for tx in rows:
                formed = tx.bitrix_payment_id and tx.formation_timeline_created and tx.formation_activity_created
                kind = 'reconciliation' if formed else 'formation'
                await self.enqueue_in_session(session, tx.id, kind, available_at=tx.next_attempt_at)
            return len(rows)

    async def recover_callbacks(self, limit=100):
        # Avoid taking inbox then payment locks concurrently with callback ingress.
        async with self.sessions() as session:
            ids = list(await session.scalars(select(PaymentCallbackInbox.payment_id)
                .where(PaymentCallbackInbox.available_at <= func.clock_timestamp()).limit(limit)))
        for payment_id in ids:
            async with self.sessions.begin() as session:
                tx = await session.scalar(select(PaymentTransaction).where(PaymentTransaction.bitrix_payment_id == payment_id).with_for_update())
                inbox = await session.get(PaymentCallbackInbox, payment_id, with_for_update=True)
                if inbox is None:
                    continue
                now = await session.scalar(select(func.clock_timestamp()))
                if tx is not None:
                    await self.request_reconciliation_in_session(session, tx.id, now)
                    await session.delete(inbox)
                elif inbox.expires_at <= now:
                    await session.delete(inbox)
                else:
                    inbox.available_at = now + timedelta(seconds=30)
        return len(ids)

    async def unknown_writes(self, transaction_id):
        async with self.sessions() as session:
            return list(await session.scalars(select(PaymentExternalWrite).where(
                PaymentExternalWrite.transaction_id == transaction_id, PaymentExternalWrite.state.in_({"in_flight", "unknown"}))))

    async def reserve_request(self, integration_key, rps):
        async with self.sessions.begin() as session:
            now = await session.scalar(select(func.clock_timestamp()))
            await session.execute(insert(PaymentRequestBudget).values(integration_key=integration_key,
                next_slot_at=now, cooldown_until=now).on_conflict_do_nothing(index_elements=["integration_key"]))
            row = await session.get(PaymentRequestBudget, integration_key, with_for_update=True)
            now = await session.scalar(select(func.clock_timestamp()))
            slot = max(now, row.next_slot_at, row.cooldown_until)
            delay = max(0, (slot - now).total_seconds())
            if delay == 0:
                row.next_slot_at = now + timedelta(seconds=1 / rps)
            return delay

    async def cooldown(self, integration_key, seconds):
        async with self.sessions.begin() as session:
            await session.execute(update(PaymentRequestBudget).where(PaymentRequestBudget.integration_key == integration_key)
                .values(cooldown_until=func.greatest(PaymentRequestBudget.cooldown_until,
                                                   func.clock_timestamp() + timedelta(seconds=seconds))))

    async def cooldown_remaining(self, integration_key):
        async with self.sessions() as session:
            value = await session.scalar(select(func.extract('epoch', PaymentRequestBudget.cooldown_until - func.clock_timestamp()))
                                         .where(PaymentRequestBudget.integration_key == integration_key))
            return max(0, float(value or 0))

    async def create_payment(self, key, values):
        async with self.sessions() as session:
            try:
                async with session.begin():
                    await self.check_mode(session)
                    old = await session.scalar(select(PaymentTransaction).where(PaymentTransaction.idempotency_key == key))
                    if old is not None:
                        return old, False
                    payment = PaymentTransaction(idempotency_key=key, **values)
                    session.add(payment)
                    await session.flush()
                    await self.enqueue_in_session(session, payment.id, "formation")
                return payment, True
            except IntegrityError:
                await session.rollback()
                old = await session.scalar(select(PaymentTransaction).where(PaymentTransaction.idempotency_key == key))
                if old is None:
                    raise
                return old, False

    async def enqueue_in_session(self, session, transaction_id, kind, *, available_at=None, details=None, attempt_count=0):
        """Caller holds payment lock (or is creating payment in this transaction)."""
        if kind not in EVENT_TYPES:
            raise ValueError("unknown payment job kind")
        generation = int(await session.scalar(select(func.max(PaymentJob.generation)).where(
            PaymentJob.transaction_id == transaction_id, PaymentJob.kind == kind)) or 0) + 1
        now = await session.scalar(select(func.clock_timestamp()))
        job = PaymentJob(transaction_id=transaction_id, kind=kind, generation=generation,
            available_at=available_at or now, details=details or {}, attempt_count=attempt_count)
        session.add(job)
        await session.flush()
        session.add(PaymentOutbox(job_id=job.id, event_type=EVENT_TYPES[kind],
            available_at=job.available_at, next_publish_at=job.available_at))
        await session.flush()
        await session.execute(text("SELECT pg_notify('payment_outbox_ready', '')"))
        return job

    async def queue_command(self, transaction_id, user_id, action, *, selection=None, admin=False):
        from app.errors import PaymentNotFoundError, PaymentStateError
        async with self.sessions.begin() as session:
            await self.check_mode(session)
            tx = await session.scalar(select(PaymentTransaction).where(PaymentTransaction.id == transaction_id).with_for_update())
            if tx is None or (not admin and tx.user_id != user_id):
                raise PaymentNotFoundError()
            now = await session.scalar(select(func.clock_timestamp()))
            details = {'action': action}
            if action == 'select':
                if tx.status != 'client_selection_required' or selection not in {
                    (item['entity_type'], int(item['entity_id'])) for item in tx.candidate_snapshot
                }:
                    raise PaymentStateError('Выбранный клиент отсутствует среди кандидатов')
                tx.status = 'resolving_client'
                details['selection'] = list(selection)
            elif action == 'resume':
                if not admin or tx.status != 'failed':
                    raise PaymentStateError()
                if await session.scalar(select(PaymentExternalWrite.id).where(PaymentExternalWrite.transaction_id == tx.id,
                    PaymentExternalWrite.state.in_({'in_flight', 'unknown'})).limit(1)):
                    raise PaymentStateError('Сначала требуется сверка неизвестного результата Битрикса')
                stopped = list(await session.scalars(select(PaymentJob).where(PaymentJob.transaction_id == tx.id,
                    PaymentJob.state.in_({'failed', 'needs_reconciliation'})).with_for_update()))
                if len(stopped) > 1:
                    raise PaymentStateError('Выберите конкретное остановленное задание в диагностике')
                if stopped:
                    await self.retry_in_session(session, tx, stopped[0], user_id)
                    return tx
                # Pre-outbox historical failures have no job intent to restore.
                tx.status = resumable_status(tx)
                tx.retry_count = 0
            elif action in {'cancel', 'resend'}:
                if tx.status in {'paid', 'canceled', 'expired', 'failed'}:
                    raise PaymentStateError()
                if action == 'resend' and not (tx.payment_url or tx.payment_short_url):
                    raise PaymentStateError('Платёжная ссылка ещё не сформирована')
                active = list(await session.scalars(select(PaymentJob).where(PaymentJob.transaction_id == tx.id,
                    PaymentJob.state.in_(ACTIVE_JOB_STATES))))
                if any(j.details.get('action') == action for j in active):
                    return tx
                details['scope'] = action + ':' + uuid4().hex
            else:
                raise ValueError('unknown payment command')
            tx.current_step = action + '_queued'
            tx.updated_at = now
            await self.enqueue_in_session(session, tx.id, 'formation', details=details)
            session.add(PaymentTransactionEvent(transaction_id=tx.id, event_type='payment.command_queued',
                safe_payload={'action': action, 'user_id': str(user_id)}))
            return tx

    async def pending_commands(self, transaction_id):
        async with self.sessions() as session:
            rows = await session.scalars(select(PaymentJob.details).where(
                PaymentJob.transaction_id == transaction_id, PaymentJob.state.in_(ACTIVE_JOB_STATES))
                .order_by(PaymentJob.generation))
            return list(dict.fromkeys(row['action'] for row in rows if row.get('action') in {'cancel', 'resend', 'select', 'resume'}))

    async def receive_callback(self, payment_id, ttl_seconds=86400):
        async with self.sessions.begin() as session:
            await self.check_mode(session)
            now = await session.scalar(select(func.clock_timestamp()))
            tx = await session.scalar(select(PaymentTransaction).where(PaymentTransaction.bitrix_payment_id == payment_id).with_for_update())
            if tx is None:
                await session.execute(insert(PaymentCallbackInbox).values(payment_id=payment_id, received_at=now,
                    available_at=now, expires_at=now + timedelta(seconds=ttl_seconds))
                    .on_conflict_do_update(index_elements=['payment_id'], set_={'received_at': now, 'available_at': now}))
                return
            await self.request_reconciliation_in_session(session, tx.id, now)

    async def request_reconciliation_in_session(self, session, transaction_id, now):
        """Caller holds the payment lock. A running read cannot consume a new callback."""
        job = await session.scalar(select(PaymentJob).where(PaymentJob.transaction_id == transaction_id,
            PaymentJob.kind == 'reconciliation', PaymentJob.state.in_({'ready', 'retry_wait'}))
            .order_by(PaymentJob.generation).limit(1))
        if job is None:
            await self.enqueue_in_session(session, transaction_id, 'reconciliation', available_at=now)
        else:
            job.available_at = now
            await session.execute(update(PaymentOutbox).where(PaymentOutbox.job_id == job.id)
                .values(available_at=now, next_publish_at=now, last_published_at=None))
            await session.execute(text("SELECT pg_notify('payment_outbox_ready', '')"))

    async def claim(self, job_id, owner, lease_seconds):
        async with self.sessions.begin() as session:
            await self.check_mode(session)
            transaction_id = await session.scalar(select(PaymentJob.transaction_id).where(PaymentJob.id == job_id))
            if transaction_id is None:
                return None
            await session.scalar(select(PaymentTransaction).where(PaymentTransaction.id == transaction_id).with_for_update())
            job = await session.scalar(select(PaymentJob).where(PaymentJob.id == job_id).with_for_update())
            now = await session.scalar(select(func.clock_timestamp()))
            if job.state not in ACTIVE_JOB_STATES or job.available_at > now:
                return None
            lease = await session.get(PaymentExecutionLease, transaction_id, with_for_update=True)
            if lease is not None and lease.lease_until > now:
                return None
            if lease is None:
                lease = PaymentExecutionLease(transaction_id=transaction_id, owner=owner,
                    fencing_token=1, lease_until=now + timedelta(seconds=lease_seconds))
                session.add(lease)
            else:
                lease.owner = owner
                lease.fencing_token += 1
                lease.lease_until = now + timedelta(seconds=lease_seconds)
            job.state = "running"
            job.started_at = now
            return PaymentClaim(job.id, transaction_id, owner, lease.fencing_token)

    async def renew(self, claim, lease_seconds):
        async with self.sessions.begin() as session:
            result = await session.execute(update(PaymentExecutionLease).where(
                PaymentExecutionLease.transaction_id == claim.transaction_id,
                PaymentExecutionLease.owner == claim.owner,
                PaymentExecutionLease.fencing_token == claim.token,
                PaymentExecutionLease.lease_until > func.clock_timestamp(),
            ).values(lease_until=func.clock_timestamp() + timedelta(seconds=lease_seconds)))
            return result.rowcount == 1

    async def owns(self, claim):
        async with self.sessions() as session:
            return await session.scalar(select(PaymentExecutionLease.transaction_id).where(
                PaymentExecutionLease.transaction_id == claim.transaction_id,
                PaymentExecutionLease.owner == claim.owner, PaymentExecutionLease.fencing_token == claim.token,
                PaymentExecutionLease.lease_until > func.clock_timestamp())) is not None

    async def begin_write(self, claim, marker, method, recovery=None):
        async with self.sessions.begin() as session:
            if not await self.owns_in_session(session, claim):
                raise PaymentLeaseLost()
            row = await session.scalar(select(PaymentExternalWrite).where(
                PaymentExternalWrite.transaction_id == claim.transaction_id, PaymentExternalWrite.marker == marker))
            if row is not None:
                if row.state == "confirmed":
                    return row.remote_id or "null"
                if row.state in {"in_flight", "unknown"}:
                    raise PaymentWriteUnknown()
                row.state = "in_flight"
                row.fencing_token = claim.token
                row.updated_at = await session.scalar(select(func.clock_timestamp()))
            else:
                session.add(PaymentExternalWrite(transaction_id=claim.transaction_id, marker=marker,
                    method=method, state="in_flight", fencing_token=claim.token, recovery=recovery or {}))
            return None

    async def set_resolution(self, claim, resolution):
        async with self.sessions.begin() as session:
            if not await self.owns_in_session(session, claim):
                raise PaymentLeaseLost()
            tx = await session.get(PaymentTransaction, claim.transaction_id)
            if not tx.client_resolution:
                tx.client_resolution = resolution

    async def confirm_recovered(self, claim, write_id, result):
        async with self.sessions.begin() as session:
            if not await self.owns_in_session(session, claim):
                raise PaymentLeaseLost()
            row = await session.get(PaymentExternalWrite, write_id, with_for_update=True)
            if row is None or row.transaction_id != claim.transaction_id:
                raise PaymentLeaseLost()
            row.state = 'confirmed'
            row.remote_id = result
            row.fencing_token = claim.token
            row.updated_at = await session.scalar(select(func.clock_timestamp()))

    async def confirm_write(self, claim, marker, result):
        async with self.sessions.begin() as session:
            if not await self.owns_in_session(session, claim):
                raise PaymentLeaseLost()
            await session.execute(update(PaymentExternalWrite).where(
                PaymentExternalWrite.transaction_id == claim.transaction_id,
                PaymentExternalWrite.marker == marker, PaymentExternalWrite.fencing_token == claim.token,
            ).values(state="confirmed", remote_id=result, updated_at=func.clock_timestamp()))

    async def reject_write(self, claim, marker):
        """A definite request rejection may be retried; uncertain network results may not."""
        async with self.sessions.begin() as session:
            if not await self.owns_in_session(session, claim):
                raise PaymentLeaseLost()
            await session.execute(update(PaymentExternalWrite).where(
                PaymentExternalWrite.transaction_id == claim.transaction_id,
                PaymentExternalWrite.marker == marker, PaymentExternalWrite.fencing_token == claim.token,
            ).values(state="prepared", updated_at=func.clock_timestamp()))

    async def owns_in_session(self, session, claim):
        await session.scalar(select(PaymentTransaction).where(PaymentTransaction.id == claim.transaction_id).with_for_update())
        lease = await session.get(PaymentExecutionLease, claim.transaction_id, with_for_update=True)
        now = await session.scalar(select(func.clock_timestamp()))
        return lease is not None and lease.owner == claim.owner and lease.fencing_token == claim.token and lease.lease_until > now

    async def finish(self, claim, *, state="completed", next_kind=None, next_at=None, error=None, details=None, attempt_count=0):
        if state not in FINISHED_JOB_STATES:
            raise ValueError("invalid job outcome")
        async with self.sessions.begin() as session:
            if not await self.owns_in_session(session, claim):
                return False
            job = await session.get(PaymentJob, claim.job_id, with_for_update=True)
            if job.state != "running":
                return False
            now = await session.scalar(select(func.clock_timestamp()))
            job.state = state
            job.last_safe_error = error
            job.finished_at = now
            if error:
                tx = await session.get(PaymentTransaction, claim.transaction_id)
                tx.last_error_code = error
                tx.last_error_message = 'Требуется сверка результата Битрикса' if state == 'needs_reconciliation' else 'Внешняя операция временно не выполнена'
                tx.retry_count = attempt_count
                if state in {'failed', 'needs_reconciliation'} and tx.status not in {'paid', 'canceled', 'expired'}:
                    tx.status = 'failed'
                    tx.current_step = state
            await session.execute(update(PaymentOutbox).where(PaymentOutbox.job_id == job.id).values(completed_at=now))
            if next_kind == 'reconciliation':
                # A callback may already have requested an earlier check. Do not
                # create a second perpetual polling chain or postpone that check.
                queued = await session.scalar(select(PaymentJob).where(
                    PaymentJob.transaction_id == claim.transaction_id,
                    PaymentJob.kind == 'reconciliation', PaymentJob.state.in_(ACTIVE_JOB_STATES))
                    .order_by(PaymentJob.generation).limit(1))
                if queued is not None:
                    if error:
                        # A callback received before the failed read must not
                        # fork a retry chain or reset its attempt/backoff budget.
                        queued.attempt_count = max(queued.attempt_count, attempt_count)
                        queued.last_safe_error = error
                        queued.available_at = max(queued.available_at, next_at or now)
                        await session.execute(update(PaymentOutbox).where(PaymentOutbox.job_id == queued.id)
                            .values(available_at=queued.available_at, next_publish_at=queued.available_at,
                                last_published_at=None))
                    next_kind = None
            if next_kind:
                await self.enqueue_in_session(session, claim.transaction_id, next_kind,
                    available_at=next_at, details=details, attempt_count=attempt_count)
            lease = await session.get(PaymentExecutionLease, claim.transaction_id)
            lease.lease_until = now
            return True

    async def claim_outbox(self, lease_seconds):
        async with self.sessions.begin() as session:
            now = await session.scalar(select(func.clock_timestamp()))
            row = await session.scalar(select(PaymentOutbox).where(
                PaymentOutbox.completed_at.is_(None), PaymentOutbox.last_published_at.is_(None),
                PaymentOutbox.available_at <= now, PaymentOutbox.next_publish_at <= now,
                or_(PaymentOutbox.publisher_lease_until.is_(None), PaymentOutbox.publisher_lease_until <= now),
            ).order_by(PaymentOutbox.next_publish_at, PaymentOutbox.event_id)
                .with_for_update(skip_locked=True).limit(1))
            if row is None:
                return None
            row.publisher_token = uuid4()
            row.publisher_lease_until = now + timedelta(seconds=lease_seconds)
            row.publish_attempts += 1
            return row

    async def published(self, event_id, token, message_id):
        async with self.sessions.begin() as session:
            result = await session.execute(update(PaymentOutbox).where(
                PaymentOutbox.event_id == event_id, PaymentOutbox.publisher_token == token,
                PaymentOutbox.publisher_lease_until > func.clock_timestamp(), PaymentOutbox.completed_at.is_(None),
            ).values(last_published_at=func.clock_timestamp(), stream_message_id=message_id,
                     publisher_token=None, publisher_lease_until=None))
            return result.rowcount == 1

    async def event_for(self, event_id):
        async with self.sessions() as session:
            row = (await session.execute(select(PaymentOutbox, PaymentJob).join(PaymentJob, PaymentJob.id == PaymentOutbox.job_id)
                .where(PaymentOutbox.event_id == event_id))).first()
            if row is None:
                return None
            outbox, job = row
            return PaymentEvent(event_id=outbox.event_id, job_id=job.id, transaction_id=job.transaction_id,
                event_type=outbox.event_type, schema_version=outbox.schema_version, generation=job.generation)

    async def valid_event(self, event):
        if await self.event_for(event.event_id) == event:
            return True
        from app.models import PaymentJobArchive
        async with self.sessions() as session:
            row = await session.scalar(select(PaymentJobArchive).where(PaymentJobArchive.event_id == event.event_id))
            return row is not None and row.state in {'completed', 'superseded'} and (row.job_id, row.transaction_id, row.event_type, row.schema_version, row.generation) == (
                event.job_id, event.transaction_id, event.event_type, event.schema_version, event.generation)

    async def publish_failed(self, event_id, token, delay):
        async with self.sessions.begin() as session:
            await session.execute(update(PaymentOutbox).where(
                PaymentOutbox.event_id == event_id, PaymentOutbox.publisher_token == token,
                PaymentOutbox.completed_at.is_(None),
            ).values(next_publish_at=func.clock_timestamp() + timedelta(seconds=delay),
                     publisher_token=None, publisher_lease_until=None))

    async def quarantine(self, stream, message_id, reason):
        # The caller supplies a fixed safe reason, never an exception/raw message.
        if reason not in {"INVALID_ENVELOPE", "EVENT_MISMATCH"}:
            raise ValueError("unsafe quarantine reason")
        async with self.sessions.begin() as session:
            await session.execute(insert(PaymentEventQuarantine).values(id=uuid4(), stream=stream,
                message_id=message_id, reason=reason).on_conflict_do_nothing(index_elements=["stream", "message_id"]))

    async def retry_job(self, job_id, admin_id):
        from app.errors import PaymentNotFoundError
        async with self.sessions.begin() as session:
            await self.check_mode(session)
            tid = await session.scalar(select(PaymentJob.transaction_id).where(PaymentJob.id == job_id))
            if tid is None: raise PaymentNotFoundError()
            tx = await session.scalar(select(PaymentTransaction).where(PaymentTransaction.id == tid).with_for_update())
            job = await session.get(PaymentJob, job_id, with_for_update=True)
            return await self.retry_in_session(session, tx, job, admin_id)

    async def retry_in_session(self, session, tx, job, admin_id):
        """Both admin APIs preserve the failed command under the same payment lock."""
        from app.errors import PaymentStateError
        if job.state not in {'failed', 'needs_reconciliation'}:
            raise PaymentStateError('Повтор доступен только для остановленного задания')
        lease = await session.get(PaymentExecutionLease, tx.id)
        now = await session.scalar(select(func.clock_timestamp()))
        if lease and lease.lease_until > now:
            raise PaymentStateError('Операция уже обрабатывается')
        if tx.status == 'failed':
            tx.status = resumable_status(tx)
        tx.retry_count = 0
        tx.current_step = 'resume_queued'
        tx.updated_at = now
        job.state = 'superseded'
        # Keep cancel/select/resend intent and its write scope. Plain background
        # jobs need an explicit pending command so polling does not hide resume.
        details = {'action': 'resume', **job.details}
        result = await self.enqueue_in_session(session, tx.id, job.kind, details=details)
        session.add(PaymentTransactionEvent(transaction_id=tx.id, event_type='payment.job_retry_requested',
            safe_payload={'job_id': str(job.id), 'new_job_id': str(result.id), 'admin_user_id': str(admin_id)}))
        return result.id
