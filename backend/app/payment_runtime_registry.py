"""DB interlock: configuration alone cannot safely switch live payment workers."""
import asyncio
import socket
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from app.errors import ApiError
from app.models import PaymentExecutionLease, PaymentRuntimeControl, PaymentRuntimeMember


async def require_mode(session, mode):
    await session.execute(insert(PaymentRuntimeControl).values(id=1, mode='legacy').on_conflict_do_nothing(index_elements=['id']))
    control = await session.get(PaymentRuntimeControl, 1, with_for_update=True)
    if control.mode != mode:
        raise ApiError(503, 'PAYMENT_MODE_MISMATCH', 'Режим обработки платежей не согласован; требуется переключение сервисов')


class PaymentRuntimeRegistry:
    def __init__(self, sessions, mode):
        self.sessions = sessions
        self.mode = mode

    async def register(self, owner, role):
        async with self.sessions.begin() as session:
            await require_mode(session, self.mode)
            await session.execute(insert(PaymentRuntimeMember).values(owner=owner, role=role,
                host=socket.gethostname(), mode=self.mode, healthy_until=func.clock_timestamp() + timedelta(seconds=30))
                .on_conflict_do_update(index_elements=['owner'], set_={'healthy_until': func.clock_timestamp() + timedelta(seconds=30)}))

    async def unregister(self, owner):
        async with self.sessions.begin() as session:
            await session.execute(delete(PaymentRuntimeMember).where(PaymentRuntimeMember.owner == owner))

    async def heartbeat(self, owner, role, stop):
        while not stop.is_set():
            await self.register(owner, role)
            try:
                await asyncio.wait_for(stop.wait(), timeout=10)
            except TimeoutError:
                pass

    async def healthy(self, role):
        async with self.sessions.begin() as session:
            await require_mode(session, self.mode)
            return await session.scalar(select(PaymentRuntimeMember.owner).where(
                PaymentRuntimeMember.role == role, PaymentRuntimeMember.host == socket.gethostname(),
                PaymentRuntimeMember.mode == self.mode, PaymentRuntimeMember.healthy_until > func.clock_timestamp()).limit(1)) is not None

    async def set_mode(self, target):
        if target not in {'legacy', 'events', 'maintenance'}:
            raise ValueError('invalid payment mode')
        async with self.sessions.begin() as session:
            await session.execute(insert(PaymentRuntimeControl).values(id=1, mode='legacy').on_conflict_do_nothing(index_elements=['id']))
            control = await session.get(PaymentRuntimeControl, 1, with_for_update=True)
            if control.mode == target:
                return
            active = await session.scalar(select(PaymentRuntimeMember.owner).where(
                PaymentRuntimeMember.healthy_until > func.clock_timestamp()).limit(1))
            leased = await session.scalar(select(PaymentExecutionLease.transaction_id).where(
                PaymentExecutionLease.lease_until > func.clock_timestamp()).limit(1))
            if active or leased:
                raise RuntimeError('Stop payment API/workers and wait for active execution leases before switching mode')
            control.mode = target
