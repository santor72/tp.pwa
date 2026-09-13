from types import SimpleNamespace

import pytest

from app.config import Settings
from app.payment_health import PaymentHealthMonitor


@pytest.mark.asyncio
@pytest.mark.parametrize('redis_unavailable', [False, True])
async def test_monitor_pending_failure_is_not_reported_as_zero(monkeypatch, caplog, redis_unavailable):
    async def snapshot(*args):
        return {'alerts': [], 'backlog': [], 'unpublished': 0, 'unknown_writes': 0}
    monkeypatch.setattr('app.payment_health.queue_health', snapshot)
    class Redis:
        async def xpending(self, *args):
            if redis_unavailable: raise RuntimeError('private-connection-string')
            return {'pending': 3}
    stream = SimpleNamespace(redis=Redis(), key='test-stream', group='test-group')
    monitor = PaymentHealthMonitor(None, Settings(_env_file=None, payment_processing_mode='events'), {'formation': stream})
    result = await monitor.once()
    assert result['pending']['formation'] == (None if redis_unavailable else 3)
    assert result['alerts'] == (['FORMATION_STREAM_UNAVAILABLE'] if redis_unavailable else [])
    assert 'private-connection-string' not in caplog.text
