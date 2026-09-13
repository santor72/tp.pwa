"""At-least-once publisher. Notification is an accelerator, never the queue."""
import asyncio
import logging
import random

import asyncpg
from sqlalchemy.engine import make_url

logger = logging.getLogger(__name__)


class OutboxRelay:
    def __init__(self, repository, streams, *, lease_seconds=30, poll_seconds=1):
        self.repository = repository
        self.streams = streams
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds

    async def once(self):
        row = await self.repository.claim_outbox(self.lease_seconds)
        if row is None:
            return False
        try:
            event = await self.repository.event_for(row.event_id)
            if event is None:
                return True
            kind = event.event_type.removeprefix("payment.").removesuffix("_requested")
            message_id = await self.streams[kind].publish(event)
            await self.repository.published(row.event_id, row.publisher_token, message_id)
        except Exception:
            # A failed DB commit after XADD is indistinguishable from a failed XADD.
            # Republish the same event ID; consumers consult the durable job state.
            delay = min(60, 2 ** min(row.publish_attempts, 6)) + random.uniform(0, 0.5)
            await self.repository.publish_failed(row.event_id, row.publisher_token, delay)
            logger.warning("payment.outbox.publish_failed")
        return True

    async def run(self, database_url, stop):
        wake = asyncio.Event()
        listener = asyncio.create_task(self.listen(database_url, wake, stop))
        try:
            while not stop.is_set():
                wake.clear()
                try:
                    if await self.once():
                        continue
                except Exception:
                    logger.warning("payment.outbox.scan_failed")
                # Finite polling also delivers future available_at jobs and covers
                # notifications that arrived before the LISTEN connection existed.
                try:
                    await asyncio.wait_for(wake.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)

    async def listen(self, database_url, wake, stop):
        url = make_url(database_url).set(drivername="postgresql")
        connection = None
        try:
            while not stop.is_set():
                try:
                    connection = await asyncpg.connect(url.render_as_string(hide_password=False), timeout=5)
                    await connection.add_listener("payment_outbox_ready", lambda *_: wake.set())
                    wake.set()  # Catch-up after subscribing/reconnecting.
                    while not stop.is_set() and not connection.is_closed():
                        try:
                            await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
                        except TimeoutError:
                            pass
                except Exception:
                    logger.warning("payment.outbox.listener_disconnected")
                finally:
                    if connection is not None and not connection.is_closed():
                        await connection.close(timeout=2)
                    connection = None
                if not stop.is_set():
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
                    except TimeoutError:
                        pass
        finally:
            if connection is not None and not connection.is_closed():
                await connection.close(timeout=2)
