import asyncio
import logging
import signal
from datetime import UTC, datetime

from redis.asyncio import Redis

from app.config import get_settings
from app.errors import PaymentStateError
from app.logging import audit, configure_logging
from app.payments import POLL_STATES, WORK_STATES
from app.services import create_application_services

logger = logging.getLogger(__name__)


async def process_once(services, settings, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    await services.payment_repository.expire_due(now)
    ids = await services.payment_repository.claim_batch(WORK_STATES | POLL_STATES, now, settings.bx24_worker_batch_size)
    for transaction_id in ids:
        try:
            transaction = await services.payment_repository.get(transaction_id)
            if (
                transaction and transaction.status in POLL_STATES and transaction.bitrix_payment_id
                and transaction.formation_timeline_created and transaction.formation_activity_created
            ):
                await services.payment_status.handle(transaction.bitrix_payment_id)
            else:
                await services.payment_service.process(transaction_id)
        except PaymentStateError:
            audit(logger, "payment.worker.superseded", transaction_id=str(transaction_id))
        except Exception as exc:
            await services.payment_service.mark_failure(transaction_id, exc)
            audit(logger, "payment.worker.failed", transaction_id=str(transaction_id), error=type(exc).__name__)
    return len(ids)


async def run() -> None:
    # Compatible DB-polling fallback uses the same jobs and fencing as events.
    from app.payment_runtime import run as run_runtime
    await run_runtime('legacy')

if __name__ == "__main__":
    asyncio.run(run())
