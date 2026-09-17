"""Retries only GIS delivery after the completion comment and tag are confirmed."""
import asyncio
import signal

from redis.asyncio import Redis

from app.config import get_settings
from app.logging import configure_logging
from app.services import create_application_services


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    cache = Redis.from_url(settings.cache_redis_url, decode_responses=True)
    services = create_application_services(settings, cache)
    stop = asyncio.Event()
    for value in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(value, stop.set)
    try:
        while not stop.is_set():
            for operation_id in await services.completion_repository.claim_completion_reconciliation(settings.gis_completion_worker_batch_size):
                await services.connection_completion_service.reconcile_techportal(operation_id)
            for operation_id in await services.completion_repository.claim_gis(settings.gis_completion_worker_batch_size):
                await services.connection_completion_service.send_gis(operation_id)
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.gis_completion_worker_poll_seconds)
            except TimeoutError:
                pass
    finally:
        await services.close()
        await cache.aclose()


if __name__ == '__main__':
    asyncio.run(run())
