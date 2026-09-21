import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import create_engine, create_session_factory
from app.errors import PaymentStateError
from app.models import Base, User
from app.repositories import PaymentRepository


pytestmark = pytest.mark.skipif(
    not os.getenv("PAYMENT_TEST_DATABASE_URL"),
    reason="PAYMENT_TEST_DATABASE_URL is required for PostgreSQL repository tests",
)


@pytest_asyncio.fixture
async def repository():
    database_url = os.environ["PAYMENT_TEST_DATABASE_URL"]
    assert database_url.rsplit('/', 1)[-1] in {'payment_events_test', 'payment_test'}, 'isolated test database required'
    schema = 'legacy_payment_' + uuid4().hex
    admin = create_engine(database_url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_async_engine(database_url, connect_args={'server_settings': {'search_path': schema}})
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
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
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        await admin.dispose()


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
async def test_telemetry_all_states_stats_filters_and_browser_dedup(repository):
    repo, user_id = repository
    now = datetime.now(UTC)
    created = []
    for i in range(3):
        values = transaction_values(user_id, now=now)
        values["employee_external_id"] = str(user_id)
        values["next_attempt_at"] = now + timedelta(hours=1)
        row, _ = await repo.create_or_get(idempotency_key=uuid4(), values=values)
        created.append(row)
        span = dict(id=uuid4(), parent_id=None, name="browser_qr", kind="browser", started_at=now,
            duration_ms=(i + 1) * 1000, outcome="ok", details={"background": i == 2, "restored": False})
        await repo.save_browser_timing(row.id, [span], uuid4())
        await repo.save_browser_timing(row.id, [{**span, "id": uuid4()}], uuid4())
        assert len(await repo.timing_detail(row.id)) == 1
    rows, spans, total, stats = await repo.timing_list(date_from=now - timedelta(minutes=1), date_to=now + timedelta(minutes=1),
        phone=None, employee=str(user_id), status="draft", page=1, page_size=1)
    assert len(rows) == len(spans) == 1
    assert total == 3
    assert stats["samples"] == 2  # Statistics cover all pages and exclude background.
    assert stats["median_ms"] == 1500
    assert stats["p95_ms"] == 1950


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


@pytest.mark.asyncio
async def test_admin_list_includes_unpaid_and_filters_paid_only(repository) -> None:
    payments, user_id = repository
    now = datetime.now(UTC)
    unpaid_values = transaction_values(user_id, now=now)
    unpaid_values.update(address_text="ул. Тестовая, 1", apartment="235")
    unpaid, _ = await payments.create_or_get(idempotency_key=uuid4(), values=unpaid_values)

    paid, _ = await payments.create_or_get(
        idempotency_key=uuid4(), values=transaction_values(user_id, now=now),
    )
    await payments.update(paid.id, status="paid", current_step="paid", paid_at=now)

    all_rows, all_total = await payments.admin_list(
        date_from=now - timedelta(minutes=1), date_to=now + timedelta(minutes=1),
        phone=None, employee=None, paid_only=False, page=1, page_size=50,
    )
    assert all_total == 2
    assert {row.id for row in all_rows} == {unpaid.id, paid.id}
    listed_unpaid = next(row for row in all_rows if row.id == unpaid.id)
    assert listed_unpaid.address_text == "ул. Тестовая, 1"
    assert listed_unpaid.apartment == "235"

    paid_rows, paid_total = await payments.admin_list(
        date_from=now - timedelta(minutes=1), date_to=now + timedelta(minutes=1),
        phone=None, employee=None, paid_only=True, page=1, page_size=50,
    )
    assert paid_total == 1
    assert [row.id for row in paid_rows] == [paid.id]
