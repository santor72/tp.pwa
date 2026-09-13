"""Operational queue health: aggregate, read-only and independent of tracing."""
import asyncio
import logging

from sqlalchemy import func, or_, select

from app.models import PaymentExecutionLease, PaymentExternalWrite, PaymentJob, PaymentOutbox
from app.logging import audit

logger = logging.getLogger(__name__)


async def queue_health(sessions, settings):
    async with sessions() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        counts = [dict(kind=kind, state=state, count=count) for kind, state, count in
            (await session.execute(select(PaymentJob.kind, PaymentJob.state, func.count())
                .group_by(PaymentJob.kind, PaymentJob.state))).all()]
        overdue = list((await session.execute(select(PaymentJob.kind, func.count(), func.min(PaymentJob.available_at))
            .outerjoin(PaymentExecutionLease, PaymentExecutionLease.transaction_id == PaymentJob.transaction_id)
            .where(PaymentJob.state.in_({'ready', 'retry_wait', 'running'}), PaymentJob.available_at <= now,
                or_(PaymentExecutionLease.transaction_id.is_(None), PaymentExecutionLease.lease_until <= now))
            .group_by(PaymentJob.kind))).all())
        backlog = [dict(kind=kind, count=count, oldest_due_seconds=max(0, (now - oldest).total_seconds()))
            for kind, count, oldest in overdue]
        unpublished, oldest = (await session.execute(select(func.count(), func.min(PaymentOutbox.available_at))
            .where(PaymentOutbox.completed_at.is_(None), PaymentOutbox.last_published_at.is_(None),
                PaymentOutbox.available_at <= now))).one()
        unknown = await session.scalar(select(func.count()).select_from(PaymentExternalWrite)
            .outerjoin(PaymentExecutionLease, PaymentExecutionLease.transaction_id == PaymentExternalWrite.transaction_id)
            .where(PaymentExternalWrite.state.in_({'in_flight', 'unknown'}),
                or_(PaymentExecutionLease.transaction_id.is_(None), PaymentExecutionLease.lease_until <= now)))
        active_leases = await session.scalar(select(func.count()).select_from(PaymentExecutionLease)
            .where(PaymentExecutionLease.lease_until > now))
    publication_age = max(0, (now - oldest).total_seconds()) if oldest else 0
    alerts = []
    for item in backlog:
        if item['oldest_due_seconds'] >= settings.payment_health_due_age_seconds:
            alerts.append(f"{item['kind'].upper()}_BACKLOG_STALLED")
    if publication_age >= settings.payment_health_outbox_age_seconds and settings.payment_processing_mode == 'events':
        alerts.append('OUTBOX_PUBLICATION_STALLED')
    if unknown: alerts.append('EXTERNAL_WRITES_NEED_RECONCILIATION')
    if any(item['state'] in {'failed', 'needs_reconciliation'} and item['count'] for item in counts):
        alerts.append('JOBS_REQUIRE_ATTENTION')
    return dict(checked_at=now, counts=counts, backlog=backlog, unpublished=unpublished,
        oldest_unpublished_seconds=publication_age, unknown_writes=unknown, active_leases=active_leases,
        alerts=alerts)


class PaymentHealthMonitor:
    def __init__(self, sessions, settings, streams):
        self.sessions, self.settings, self.streams = sessions, settings, streams

    async def once(self):
        snapshot = await queue_health(self.sessions, self.settings)
        pending = {}
        if self.settings.payment_processing_mode == 'events':
            for kind, stream in self.streams.items():
                try:
                    value = await stream.redis.xpending(stream.key, stream.group)
                    pending[kind] = value['pending']
                except Exception:
                    pending[kind] = None
                    snapshot['alerts'].append(f'{kind.upper()}_STREAM_UNAVAILABLE')
        audit(logger, 'payment.queue.attention' if snapshot['alerts'] else 'payment.queue.healthy',
            alerts=snapshot['alerts'], backlog=snapshot['backlog'], unpublished=snapshot['unpublished'],
            unknown_writes=snapshot['unknown_writes'], pending=pending)
        return {**snapshot, 'pending': pending}

    async def run(self, stop):
        while not stop.is_set():
            try:
                await self.once()
            except Exception:
                logger.warning('payment.queue.health_unavailable')
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.settings.payment_health_interval_seconds)
            except TimeoutError:
                pass
