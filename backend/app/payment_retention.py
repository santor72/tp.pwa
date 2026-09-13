"""Bounded cleanup with durable job/archive identities for duplicate delivery."""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.orm import aliased

from app.models import PaymentJob, PaymentOutbox, PaymentJobArchive, PaymentTransaction, PaymentExecutionLease, PaymentExternalWrite, PaymentEventQuarantine
from app.logging import audit

logger = logging.getLogger(__name__)

# Check group progress and pending atomically with deletion. A read cannot slip
# between XPENDING and XDEL. Unexpected groups are protected too, not destroyed.
DELETE_ACKNOWLEDGED = r'''
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
local groups = redis.call('XINFO', 'GROUPS', KEYS[1])
if #groups == 0 or #groups > 8 then return 0 end
local function less(a, b)
  local am, as = string.match(a, '^(%d+)%-(%d+)$')
  local bm, bs = string.match(b, '^(%d+)%-(%d+)$')
  if #am ~= #bm then return #am < #bm end
  if am ~= bm then return am < bm end
  if #as ~= #bs then return #as < #bs end
  return as < bs
end
local removed = 0
for _, id in ipairs(ARGV) do
  local safe = true
  for _, group in ipairs(groups) do
    local name, delivered
    for i = 1, #group, 2 do
      if group[i] == 'name' then name = group[i + 1] end
      if group[i] == 'last-delivered-id' then delivered = group[i + 1] end
    end
    if not name or not delivered or less(delivered, id) then safe = false; break end
    if #redis.call('XPENDING', KEYS[1], name, id, id, 1) > 0 then safe = false; break end
  end
  if safe then removed = removed + redis.call('XDEL', KEYS[1], id) end
end
return removed
'''


QUARANTINE_MESSAGE_GONE = r'''
if redis.call('EXISTS', KEYS[1]) == 0 then return 1 end
if #redis.call('XRANGE', KEYS[1], ARGV[1], ARGV[1], 'COUNT', 1) > 0 then return 0 end
local groups = redis.call('XINFO', 'GROUPS', KEYS[1])
if #groups > 8 then return 0 end
for _, group in ipairs(groups) do
  local name
  for i = 1, #group, 2 do
    if group[i] == 'name' then name = group[i + 1] end
  end
  if not name or #redis.call('XPENDING', KEYS[1], name, ARGV[1], ARGV[1], 1) > 0 then return 0 end
end
return 1
'''


class PaymentStreamRetention:
    def __init__(self, sessions, settings, streams):
        self.sessions, self.settings, self.streams = sessions, settings, streams
        self.cursors = {kind: '-' for kind in streams}
        self.quarantine_cursors = {kind: None for kind in streams}

    async def once(self, *, dry_run=False):
        result = {'scanned': 0, 'eligible': 0, 'deleted': 0}
        if not self.settings.payment_stream_retention_enabled:
            return result
        for kind, stream in self.streams.items():
            messages = await stream.redis.xrange(stream.key, min=self.cursors[kind], max='+',
                count=self.settings.payment_stream_retention_batch_size)
            self.cursors[kind] = '(' + messages[-1][0] if messages else '-'
            result['scanned'] += len(messages)
            events = {}
            for message_id, fields in messages:
                try:
                    events[message_id] = stream.decode(fields)
                except (ValueError, TypeError):
                    continue  # Quarantine is the consumer's responsibility.
            async with self.sessions() as session:
                cutoff = await session.scalar(select(func.clock_timestamp())) - timedelta(days=self.settings.payment_stream_retention_days)
                quarantined = set(await session.scalars(select(PaymentEventQuarantine.message_id).where(
                    PaymentEventQuarantine.stream == stream.key,
                    PaymentEventQuarantine.message_id.in_([mid for mid, _ in messages]),
                    PaymentEventQuarantine.created_at < cutoff)))
                rows = (await session.execute(select(PaymentOutbox, PaymentJob)
                    .join(PaymentJob, PaymentJob.id == PaymentOutbox.job_id)
                    .where(PaymentOutbox.event_id.in_([e.event_id for e in events.values()]),
                        PaymentOutbox.completed_at.is_not(None), PaymentJob.state.in_({'completed', 'superseded'}),
                        PaymentJob.finished_at < cutoff))).all()
                archived = {r.event_id: r for r in await session.scalars(select(PaymentJobArchive).where(
                    PaymentJobArchive.event_id.in_([e.event_id for e in events.values()]),
                    PaymentJobArchive.state.in_({'completed', 'superseded'}), PaymentJobArchive.finished_at < cutoff))}
            known = {outbox.event_id: (outbox, job) for outbox, job in rows}
            eligible = list(quarantined)
            for message_id, event in events.items():
                if message_id in quarantined:
                    continue
                pair = known.get(event.event_id)
                if pair is None:
                    old = archived.get(event.event_id)
                    if old and (old.job_id, old.transaction_id, old.generation, old.event_type, old.schema_version) == (
                        event.job_id, event.transaction_id, event.generation, event.event_type, event.schema_version):
                        eligible.append(message_id)
                    continue
                outbox, job = pair
                if (event.job_id == job.id and event.transaction_id == job.transaction_id and
                    event.generation == job.generation and event.event_type == outbox.event_type and
                    event.schema_version == outbox.schema_version):
                    eligible.append(message_id)
            result['eligible'] += len(eligible)
            if eligible and not dry_run:
                result['deleted'] += await stream.redis.eval(DELETE_ACKNOWLEDGED, 1, stream.key, *eligible)
            if not dry_run:
                await self.prune_quarantine(kind, stream, cutoff)
        return result

    async def prune_quarantine(self, kind, stream, cutoff):
        query = select(PaymentEventQuarantine).where(PaymentEventQuarantine.stream == stream.key,
            PaymentEventQuarantine.created_at < cutoff)
        cursor = self.quarantine_cursors[kind]
        if cursor is not None:
            query = query.where(PaymentEventQuarantine.id > cursor)
        async with self.sessions() as session:
            rows = list(await session.scalars(query.order_by(PaymentEventQuarantine.id)
                .limit(self.settings.payment_stream_retention_batch_size)))
        self.quarantine_cursors[kind] = rows[-1].id if rows else None
        gone = []
        for row in rows:
            if await stream.redis.eval(QUARANTINE_MESSAGE_GONE, 1, stream.key, row.message_id):
                gone.append(row.id)
        if gone:
            async with self.sessions.begin() as session:
                result = await session.execute(delete(PaymentEventQuarantine).where(
                    PaymentEventQuarantine.id.in_(gone), PaymentEventQuarantine.created_at < cutoff))
            audit(logger, 'payment.quarantine.retention', deleted=result.rowcount)

    async def run(self, stop):
        while not stop.is_set():
            try:
                audit(logger, 'payment.stream.retention', **await self.once())
            except Exception:
                logger.warning('payment.stream.retention_failed')
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.settings.payment_stream_retention_interval_seconds)
            except TimeoutError:
                pass


class PaymentJobRetention:
    def __init__(self, repository, settings):
        self.repository, self.settings = repository, settings

    async def once(self, *, dry_run=False):
        if not self.settings.payment_job_retention_enabled:
            return 0
        other, newer = aliased(PaymentJob), aliased(PaymentJob)
        async with self.repository.sessions.begin() as session:
            await self.repository.check_mode(session)
            now = await session.scalar(select(func.clock_timestamp()))
            cutoff = now - timedelta(days=self.settings.payment_job_retention_days)
            rows = (await session.execute(select(PaymentJob, PaymentOutbox)
                .join(PaymentTransaction, PaymentTransaction.id == PaymentJob.transaction_id)
                .join(PaymentOutbox, PaymentOutbox.job_id == PaymentJob.id)
                .outerjoin(PaymentExecutionLease, PaymentExecutionLease.transaction_id == PaymentJob.transaction_id)
                .where(PaymentJob.state.in_({'completed', 'superseded'}), PaymentJob.finished_at < cutoff,
                    PaymentOutbox.completed_at.is_not(None),
                    or_(PaymentOutbox.publisher_lease_until.is_(None), PaymentOutbox.publisher_lease_until <= now),
                    PaymentTransaction.status.in_({'paid', 'canceled', 'expired'}),
                    or_(PaymentTransaction.status != 'paid',
                        PaymentTransaction.formation_timeline_created & PaymentTransaction.formation_activity_created &
                        PaymentTransaction.paid_timeline_created & PaymentTransaction.paid_activity_created),
                    or_(PaymentExecutionLease.transaction_id.is_(None), PaymentExecutionLease.lease_until <= now),
                    ~exists(select(other.id).where(other.transaction_id == PaymentJob.transaction_id,
                        other.state.not_in({'completed', 'superseded'}))),
                    ~exists(select(PaymentExternalWrite.id).where(PaymentExternalWrite.transaction_id == PaymentJob.transaction_id,
                        PaymentExternalWrite.state.in_({'in_flight', 'unknown'}))),
                    exists(select(newer.id).where(newer.transaction_id == PaymentJob.transaction_id,
                        newer.kind == PaymentJob.kind, newer.generation > PaymentJob.generation)))
                .order_by(PaymentJob.finished_at, PaymentJob.id)
                .with_for_update(of=PaymentTransaction, skip_locked=True)
                .limit(self.settings.payment_job_retention_batch_size))).all()
            if dry_run:
                return len(rows)
            for job, outbox in rows:
                session.add(PaymentJobArchive(job_id=job.id, event_id=outbox.event_id,
                    transaction_id=job.transaction_id, event_type=outbox.event_type,
                    schema_version=outbox.schema_version, kind=job.kind, generation=job.generation,
                    state=job.state, finished_at=job.finished_at))
                await session.flush()
                # FK cascade removes the large outbox row in the same transaction.
                # Keep the newest job of each kind: generation and backfill anchors.
                await session.execute(delete(PaymentJob).where(PaymentJob.id == job.id))
            return len(rows)

    async def run(self, stop):
        while not stop.is_set():
            try:
                audit(logger, 'payment.jobs.archived', count=await self.once())
            except Exception:
                logger.warning('payment.jobs.retention_failed')
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.settings.payment_job_retention_interval_seconds)
            except TimeoutError:
                pass
