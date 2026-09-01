"""Persistence ports and their SQLAlchemy implementation for messenger data."""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import MessengerIdentity, PaymentTransaction, PaymentTransactionEvent, User
from app.errors import PaymentStateError
from app.schemas import UserProfile


PAYMENT_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"resolving_client", "failed", "expired", "canceled", "paid"},
    "resolving_client": {"client_selection_required", "client_resolved", "failed", "expired", "canceled", "paid"},
    "client_selection_required": {"client_resolved", "failed", "expired", "canceled", "paid"},
    "client_resolved": {"invoice_created", "failed", "expired", "canceled", "paid"},
    "invoice_created": {"product_added", "failed", "expired", "canceled", "paid"},
    "product_added": {"payment_created", "failed", "expired", "canceled", "paid"},
    "payment_created": {"link_created", "failed", "expired", "canceled", "paid"},
    "link_created": {"send_queued", "send_failed", "failed", "expired", "canceled", "paid"},
    "send_queued": {"sent", "send_failed", "failed", "expired", "canceled", "paid"},
    "sent": {"send_queued", "send_failed", "failed", "expired", "canceled", "paid"},
    "send_failed": {"send_queued", "sent", "failed", "expired", "canceled", "paid"},
    "failed": {"draft", "paid"},
    "expired": {"paid"},
    "canceled": {"paid"},
    "paid": set(),
}


def validate_payment_status_transition(current: str, target: str) -> None:
    if current == target:
        return
    if target not in PAYMENT_STATUS_TRANSITIONS.get(current, set()):
        raise PaymentStateError(f"Недопустимый переход платёжной операции: {current} → {target}")


class UserRepository(Protocol):
    async def upsert(self, session: AsyncSession, profile: UserProfile, now: datetime) -> User: ...
    async def find_id_by_techportal_id(self, session: AsyncSession, techportal_user_id: str) -> UUID | None: ...


class MessengerIdentityRepository(Protocol):
    async def active_for_provider_user(self, session: AsyncSession, provider: str, provider_user_id: str) -> tuple[MessengerIdentity, User] | None: ...
    async def active_for_user(self, session: AsyncSession, user_id: UUID, provider: str) -> MessengerIdentity | None: ...
    async def list_active(self, session: AsyncSession, user_id: UUID) -> list[MessengerIdentity]: ...


class SqlAlchemyMessengerRepository:
    async def upsert(self, session: AsyncSession, profile: UserProfile, now: datetime) -> User:
        user = await session.scalar(select(User).where(User.techportal_user_id == str(profile.id)).with_for_update())
        values = {
            "email": profile.email,
            "first_name": profile.first_name,
            "status": profile.status,
            "permissions": profile.user_permissions,
            "last_login_at": now,
        }
        if user is None:
            user = User(techportal_user_id=str(profile.id), **values)
            session.add(user)
        else:
            for name, value in values.items():
                setattr(user, name, value)
        await session.flush()
        return user

    async def find_id_by_techportal_id(self, session: AsyncSession, techportal_user_id: str) -> UUID | None:
        return await session.scalar(select(User.id).where(User.techportal_user_id == techportal_user_id))

    async def active_for_provider_user(self, session: AsyncSession, provider: str, provider_user_id: str) -> tuple[MessengerIdentity, User] | None:
        row = await session.execute(
            select(MessengerIdentity, User)
            .join(User, User.id == MessengerIdentity.user_id)
            .where(MessengerIdentity.provider == provider, MessengerIdentity.provider_user_id == provider_user_id, MessengerIdentity.revoked_at.is_(None))
        )
        return row.first()

    async def active_for_user(self, session: AsyncSession, user_id: UUID, provider: str) -> MessengerIdentity | None:
        return await session.scalar(
            select(MessengerIdentity).where(MessengerIdentity.user_id == user_id, MessengerIdentity.provider == provider, MessengerIdentity.revoked_at.is_(None))
        )

    async def list_active(self, session: AsyncSession, user_id: UUID) -> list[MessengerIdentity]:
        result = await session.scalars(select(MessengerIdentity).where(MessengerIdentity.user_id == user_id, MessengerIdentity.revoked_at.is_(None)))
        return list(result)


class PaymentRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_or_get(self, *, idempotency_key: UUID, values: dict) -> tuple[PaymentTransaction, bool]:
        async with self._sessions() as session:
            existing = await session.scalar(select(PaymentTransaction).where(PaymentTransaction.idempotency_key == idempotency_key))
            if existing is not None:
                return existing, False
            transaction = PaymentTransaction(idempotency_key=idempotency_key, **values)
            session.add(transaction)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(select(PaymentTransaction).where(PaymentTransaction.idempotency_key == idempotency_key))
                if existing is None:
                    raise
                return existing, False
            await session.refresh(transaction)
            return transaction, True

    async def get(self, transaction_id: UUID, user_id: UUID | None = None) -> PaymentTransaction | None:
        async with self._sessions() as session:
            query = select(PaymentTransaction).where(PaymentTransaction.id == transaction_id)
            if user_id is not None:
                query = query.where(PaymentTransaction.user_id == user_id)
            return await session.scalar(query)

    async def get_by_payment_id(self, payment_id: int) -> PaymentTransaction | None:
        async with self._sessions() as session:
            return await session.scalar(select(PaymentTransaction).where(PaymentTransaction.bitrix_payment_id == payment_id))

    async def list_recent(self, user_id: UUID, limit: int = 20) -> list[PaymentTransaction]:
        async with self._sessions() as session:
            result = await session.scalars(
                select(PaymentTransaction)
                .where(PaymentTransaction.user_id == user_id)
                .order_by(PaymentTransaction.created_at.desc())
                .limit(limit)
            )
            return list(result)

    async def update(self, transaction_id: UUID, **values) -> PaymentTransaction:
        async with self._sessions() as session:
            transaction = await session.scalar(
                select(PaymentTransaction).where(PaymentTransaction.id == transaction_id).with_for_update()
            )
            if transaction is None:
                raise LookupError("payment transaction not found")
            if "status" in values:
                validate_payment_status_transition(transaction.status, values["status"])
            for name, value in values.items():
                setattr(transaction, name, value)
            transaction.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(transaction)
            return transaction

    async def add_event(
        self,
        transaction_id: UUID,
        event_type: str,
        safe_payload: dict | None = None,
        external_request_id: str | None = None,
    ) -> None:
        async with self._sessions() as session:
            session.add(PaymentTransactionEvent(
                transaction_id=transaction_id,
                event_type=event_type,
                safe_payload=safe_payload or {},
                external_request_id=external_request_id,
            ))
            await session.commit()

    async def claim_batch(self, statuses: set[str], now: datetime, limit: int) -> list[UUID]:
        """Claim work transactionally by moving its retry deadline into the future."""
        async with self._sessions() as session:
            rows = list(await session.scalars(
                select(PaymentTransaction)
                .where(
                    PaymentTransaction.status.in_(statuses),
                    or_(PaymentTransaction.next_attempt_at.is_(None), PaymentTransaction.next_attempt_at <= now),
                )
                .order_by(PaymentTransaction.updated_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            ))
            for row in rows:
                row.next_attempt_at = datetime.fromtimestamp(now.timestamp() + 300, UTC)
            await session.commit()
            return [row.id for row in rows]

    async def expire_due(self, now: datetime) -> int:
        async with self._sessions() as session:
            rows = list(await session.scalars(
                select(PaymentTransaction).where(
                    PaymentTransaction.expires_at <= now,
                    PaymentTransaction.bitrix_payment_id.is_(None),
                    or_(PaymentTransaction.next_attempt_at.is_(None), PaymentTransaction.next_attempt_at <= now),
                    PaymentTransaction.status.not_in({"paid", "expired", "canceled"}),
                ).with_for_update(skip_locked=True)
            ))
            for row in rows:
                row.status = "expired"
                row.current_step = "expired"
            await session.commit()
            return len(rows)
