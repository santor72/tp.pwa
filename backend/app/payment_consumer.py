"""Redis delivery adapter. Never ACK uncommitted business work."""
import asyncio
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class PaymentConsumer:
    def __init__(self, stream, repository, executor, owner, *, reclaim_idle_ms=90000):
        self.stream = stream
        self.repository = repository
        self.executor = executor
        self.owner = owner
        self.reclaim_idle_ms = reclaim_idle_ms
        self.cursor = "0-0"

    async def handle(self, message_id, fields):
        received_at = datetime.now(UTC)
        try:
            event = self.stream.decode(fields)
        except (ValueError, TypeError):
            await self.repository.quarantine(self.stream.key, message_id, 'INVALID_ENVELOPE')
            await self.stream.ack(message_id)
            return
        if not await self.repository.valid_event(event):
            await self.repository.quarantine(self.stream.key, message_id, 'EVENT_MISMATCH')
            await self.stream.ack(message_id)
            return
        if await self.executor.execute(event.job_id, event_id=event.event_id,
            stream_message_id=message_id, received_at=received_at):
            await self.stream.ack(message_id)

    async def once(self, stop=None):
        if stop is not None and stop.is_set():
            return
        self.cursor, pending = await self.stream.reclaim(self.owner, idle_ms=self.reclaim_idle_ms, cursor=self.cursor)
        for message_id, fields in pending:
            if stop is not None and stop.is_set():
                return
            await self.handle(message_id, fields)
        if stop is not None and stop.is_set():
            return
        for message_id, fields in await self.stream.read(self.owner, block_ms=1000):
            if stop is not None and stop.is_set():
                return
            await self.handle(message_id, fields)

    async def run(self, stop):
        while not stop.is_set():
            try:
                await self.stream.ensure_group()
                await self.once(stop)
            except Exception:
                logger.warning('payment.consumer.unavailable')
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except TimeoutError:
                    pass
