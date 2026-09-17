from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import ConnectionCompletionOperation


class CompletionRepository:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self._sessions = sessions

    async def create_or_get(self, **values) -> tuple[ConnectionCompletionOperation, bool]:
        async with self._sessions() as session:
            existing = await session.scalar(select(ConnectionCompletionOperation).where(
                ConnectionCompletionOperation.idempotency_key == values['idempotency_key']))
            if existing is not None:
                return existing, False
            operation = ConnectionCompletionOperation(**values)
            session.add(operation)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(select(ConnectionCompletionOperation).where(
                    ConnectionCompletionOperation.idempotency_key == values['idempotency_key']))
                if existing is not None:
                    return existing, False
                raise
            await session.refresh(operation)
            return operation, True

    async def get(self, operation_id: UUID) -> ConnectionCompletionOperation | None:
        async with self._sessions() as session:
            return await session.get(ConnectionCompletionOperation, operation_id)

    async def update(self, operation_id: UUID, **values) -> ConnectionCompletionOperation:
        async with self._sessions() as session:
            operation = await session.scalar(select(ConnectionCompletionOperation).where(
                ConnectionCompletionOperation.id == operation_id).with_for_update())
            if operation is None:
                raise LookupError(operation_id)
            for key, value in values.items():
                setattr(operation, key, value)
            await session.commit()
            await session.refresh(operation)
            return operation

    async def claim_gis(self, limit: int, now: datetime | None = None) -> list[UUID]:
        now = now or datetime.now(UTC)
        async with self._sessions() as session:
            rows = list(await session.scalars(select(ConnectionCompletionOperation).where(
                ConnectionCompletionOperation.completion_status == 'completed',
                or_(
                    (ConnectionCompletionOperation.gis_status.in_({'pending', 'retry_wait'})) &
                    (ConnectionCompletionOperation.next_attempt_at.is_(None) |
                     (ConnectionCompletionOperation.next_attempt_at <= now)),
                    (ConnectionCompletionOperation.gis_status == 'sending') &
                    (ConnectionCompletionOperation.lease_until <= now),
                ),
            ).order_by(ConnectionCompletionOperation.created_at).with_for_update(skip_locked=True).limit(limit)))
            for row in rows:
                row.gis_status = 'sending'
                row.lease_until = now + timedelta(minutes=5)
            await session.commit()
            return [row.id for row in rows]

    async def claim_completion_reconciliation(self, limit: int, now: datetime | None = None) -> list[UUID]:
        now = now or datetime.now(UTC)
        async with self._sessions() as session:
            rows = list(await session.scalars(select(ConnectionCompletionOperation).where(
                ConnectionCompletionOperation.completion_status == 'completion_unknown',
                (ConnectionCompletionOperation.next_attempt_at.is_(None) |
                 (ConnectionCompletionOperation.next_attempt_at <= now)),
                (ConnectionCompletionOperation.lease_until.is_(None) |
                 (ConnectionCompletionOperation.lease_until <= now)),
            ).order_by(ConnectionCompletionOperation.created_at).with_for_update(skip_locked=True).limit(limit)))
            for row in rows:
                row.lease_until = now + timedelta(minutes=5)
            await session.commit()
            return [row.id for row in rows]
