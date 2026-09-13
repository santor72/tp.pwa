"""Independent, horizontally scalable payment service entrypoints."""
import argparse
import asyncio
import os
import signal
from uuid import uuid4

from redis.asyncio import Redis

from app.config import get_settings
from app.logging import configure_logging
from app.payment_consumer import PaymentConsumer
from app.payment_events import PaymentStream
from app.payment_job_executor import PaymentJobExecutor
from app.payment_outbox import OutboxRelay
from app.payment_recovery import PaymentRecovery
from app.payment_runtime_registry import PaymentRuntimeRegistry
from app.payment_health import PaymentHealthMonitor
from app.payment_retention import PaymentStreamRetention, PaymentJobRetention
from app.services import create_application_services

ROLES = ('relay', 'formation', 'reconciliation', 'recovery-formation', 'recovery-reconciliation', 'legacy', 'backfill', 'health', 'set-mode')


async def run(role, argument=None):
    settings = get_settings()
    configure_logging(settings.log_level)
    if role not in ROLES:
        raise ValueError('unknown payment runtime role')
    if role == 'legacy' and settings.payment_processing_mode != 'legacy':
        raise RuntimeError('Legacy worker is disabled in events mode')
    if role not in {'legacy', 'backfill', 'health', 'set-mode'} and settings.payment_processing_mode != 'events':
        raise RuntimeError('Event workers require PAYMENT_PROCESSING_MODE=events')
    cache = Redis.from_url(settings.cache_redis_url, decode_responses=True)
    transport = Redis.from_url(settings.payment_events_redis_url, decode_responses=True, socket_connect_timeout=3, socket_timeout=5)
    services = create_application_services(settings, cache)
    repository = services.payment_repository.event_queue
    registry = PaymentRuntimeRegistry(services.sessions, settings.payment_processing_mode)
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    owner = f'{role}:{os.getpid()}:{uuid4().hex}'
    streams = {kind: PaymentStream(transport, settings.payment_events_prefix, kind) for kind in ('formation', 'reconciliation')}
    tasks = []
    try:
        if role == 'health':
            if not await registry.healthy(argument):
                raise RuntimeError('Payment runtime heartbeat is missing')
            return
        if role == 'set-mode':
            await registry.set_mode(argument)
            return
        await registry.register(owner, role)
        tasks.append(asyncio.create_task(registry.heartbeat(owner, role, stop)))
        if role == 'backfill':
            while await repository.backfill():
                pass
            return
        if role in {'relay', 'legacy'}:
            tasks.append(asyncio.create_task(PaymentHealthMonitor(services.sessions, settings, streams).run(stop)))
            if settings.payment_job_retention_enabled:
                tasks.append(asyncio.create_task(PaymentJobRetention(repository, settings).run(stop)))
        if role == 'relay':
            if settings.payment_stream_retention_enabled:
                tasks.append(asyncio.create_task(PaymentStreamRetention(services.sessions, settings, streams).run(stop)))
            relay = OutboxRelay(repository, streams, poll_seconds=settings.payment_outbox_poll_seconds)
            tasks.append(asyncio.create_task(relay.run(settings.database_url, stop)))
        else:
            kinds = ('formation', 'reconciliation') if role == 'legacy' else (role.removeprefix('recovery-'),)
            for kind in kinds:
                concurrency = getattr(settings, f'payment_{kind}_concurrency')
                for index in range(concurrency):
                    name = f'{owner}:{kind}:{index}'
                    executor = PaymentJobExecutor(services, repository, settings, name)
                    if role == 'legacy' or role.startswith('recovery-'):
                        interval = settings.payment_outbox_poll_seconds if role == 'legacy' else settings.payment_recovery_interval_seconds
                        handler = PaymentRecovery(repository, executor, kind, interval=interval)
                    else:
                        handler = PaymentConsumer(streams[kind], repository, executor, name, reclaim_idle_ms=settings.payment_reclaim_idle_ms)
                    tasks.append(asyncio.create_task(handler.run(stop)))
        stopper = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait([*tasks, stopper], return_when=asyncio.FIRST_COMPLETED)
        stop.set()
        stopper.cancel()
        await asyncio.gather(stopper, return_exceptions=True)
        # Unexpected task failure must not leave a seemingly healthy empty process.
        for task in done:
            if task in tasks:
                task.result()
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=settings.payment_shutdown_grace_seconds)
        except TimeoutError:
            pass  # Cancellation leaves the job recoverable when its lease expires.
    finally:
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await registry.unregister(owner)
        except Exception:
            pass  # Heartbeat record expires; do not mask an original failure.
        await services.close()
        await cache.aclose()
        await transport.aclose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=ROLES)
    parser.add_argument('argument', nargs='?')
    args = parser.parse_args()
    asyncio.run(run(args.role, args.argument))


if __name__ == '__main__':
    main()
