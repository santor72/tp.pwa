"""Postgres recovery path, deliberately independent of Redis availability."""
import asyncio
import logging

logger = logging.getLogger(__name__)


class PaymentRecovery:
    def __init__(self, repository, executor, kind, *, interval=30, batch_size=10):
        self.repository = repository
        self.executor = executor
        self.kind = kind
        self.interval = interval
        self.batch_size = batch_size

    async def once(self, stop=None):
        if stop is not None and stop.is_set():
            return 0
        await self.repository.backfill()
        if self.kind == 'reconciliation':
            await self.repository.recover_callbacks()
        await self.repository.recover_publications(self.interval)
        ids = await self.repository.due_jobs(self.kind, self.batch_size)
        executed = 0
        for job_id in ids:
            if stop is not None and stop.is_set():
                break
            await self.executor.execute(job_id)
            executed += 1
        return executed

    async def run(self, stop):
        while not stop.is_set():
            try:
                await self.once(stop)
            except Exception:
                logger.warning('payment.recovery.unavailable')
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
            except TimeoutError:
                pass
