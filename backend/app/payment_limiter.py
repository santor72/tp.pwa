"""Shared request budget across API, event workers and Redis-free fallback.

Contenders retry against PostgreSQL; acquisition order is not FIFO.
"""
import asyncio
import hashlib

from app.payment_execution import current_execution
from app.payment_telemetry import span


class PaymentRequestLimiter:
    def __init__(self, repository, settings):
        self.repository = repository
        self.settings = settings
        self.key = hashlib.sha256(settings.bx24_webhook_url.encode()).hexdigest()

    async def acquire(self):
        with span("limiter_wait", "wait"):
            while True:
                delay = await self.repository.reserve_request(self.key, self.settings.payment_bitrix_requests_per_second)
                if not delay:
                    break
                await asyncio.sleep(min(delay, 1))
            execution = current_execution.get()
            if execution is not None:
                await execution.check()

    async def limited(self):
        await self.repository.cooldown(self.key, self.settings.payment_bitrix_cooldown_seconds)
