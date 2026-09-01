import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio

from app.database import create_engine, create_session_factory
from app.errors import PaymentStateError
from app.models import User
from app.repositories import PaymentRepository


pytestmark = pytest.mark.skipif(
    not os.getenv("PAYMENT_TEST_DATABASE_URL"),
    reason="PAYMENT_TEST_DATABASE_URL is required for PostgreSQL repository tests",
)


@pytest_asyncio.fixture
async def repository():
    database_url = os.environ["PAYMENT_TEST_DATABASE_URL"]
    engine = create_engine(database_url)
    sessions = create_session_factory(engine)
    user = User(
        techportal_user_id=f"payment-test-{uuid4()}",
        email=f"payment-{uuid4()}@example.test",
        first_name="Тест",
        status="active",
        permissions={"client": {"create": True}},
    )
    async with sessions() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    try:
        yield PaymentRepository(sessions), user.id
    finally:
        await engine.dispose()


def transaction_values(user_id, *, now: datetime, payment_id: int | None = None, expired: bool = False) -> dict:
    return {
        "user_id": user_id,
        "employee_external_id": "test",
        "product_id": 49793,
        "product_title": "Тестовая оплата",
        "catalog_amount": Decimal("10.00"),
        "actual_amount": Decimal("10.00"),
        "currency": "RUB",
        "first_name": "Иван",
        "last_name": "Иванов",
        "phone_normalized": "+79990000000",
        "bitrix_payment_id": payment_id,
        "status": "draft" if payment_id is None else "send_queued",
        "current_step": "draft" if payment_id is None else "send",
        "next_attempt_at": now,
        "expires_at": now - timedelta(seconds=1) if expired else now + timedelta(hours=1),
    }


@pytest.mark.asyncio
async def test_postgres_idempotency_worker_claim_and_expiry(repository) -> None:
    payments, user_id = repository
    now = datetime.now(UTC)
    key = uuid4()
    first, second = await asyncio.gather(
        payments.create_or_get(idempotency_key=key, values=transaction_values(user_id, now=now)),
        payments.create_or_get(idempotency_key=key, values=transaction_values(user_id, now=now)),
    )
    assert first[0].id == second[0].id
    assert sum((first[1], second[1])) == 1
    await payments.update(first[0].id, next_attempt_at=now + timedelta(hours=1))
    await payments.update(first[0].id, status="paid", current_step="paid")
    with pytest.raises(PaymentStateError):
        await payments.update(first[0].id, status="send_queued")

    created_ids = set()
    for _ in range(4):
        transaction, created = await payments.create_or_get(
            idempotency_key=uuid4(), values=transaction_values(user_id, now=now),
        )
        assert created
        created_ids.add(transaction.id)
    claimed_a, claimed_b = await asyncio.gather(
        payments.claim_batch({"draft"}, now, 2),
        payments.claim_batch({"draft"}, now, 2),
    )
    assert set(claimed_a).isdisjoint(claimed_b)
    assert set(claimed_a) | set(claimed_b) == created_ids

    local_due, _ = await payments.create_or_get(
        idempotency_key=uuid4(), values=transaction_values(user_id, now=now, expired=True),
    )
    bitrix_due, _ = await payments.create_or_get(
        idempotency_key=uuid4(), values=transaction_values(user_id, now=now, payment_id=987654321, expired=True),
    )
    assert await payments.expire_due(now) == 1
    assert (await payments.get(local_due.id)).status == "expired"
    assert (await payments.get(bitrix_due.id)).status == "send_queued"
