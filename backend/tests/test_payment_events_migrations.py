"""Exercise actual upgrade/downgrade SQL, not Base.metadata.create_all."""
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import Base, PaymentTransaction
from app.payment_event_repository import PaymentEventRepository
from test_payment_repository_postgres import transaction_values

pytestmark = pytest.mark.skipif(not os.getenv('PAYMENT_EVENTS_TEST_DATABASE_URL'), reason='isolated test PostgreSQL required')


@pytest.mark.asyncio
@pytest.mark.parametrize('payment_status', ['draft', 'link_created', 'paid'])
async def test_event_migrations_preserve_old_payment_and_support_backfill_and_roundtrip(payment_status):
    url = os.environ['PAYMENT_EVENTS_TEST_DATABASE_URL']
    assert url.rsplit('/', 1)[-1] == 'payment_events_test'
    schema = 'migration_' + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / 'alembic.ini'))
    config.set_main_option('script_location', str(backend / 'alembic'))

    async def migrate(direction, revision):
        def run(connection):
            config.attributes['connection'] = connection
            getattr(command, direction)(config, revision)
        async with engine.begin() as connection:
            await connection.run_sync(run)

    tid, uid, key = uuid4(), uuid4(), uuid4()
    values = transaction_values(uid, now=datetime.now(UTC))
    values.update(status=payment_status, current_step=payment_status)
    if payment_status != 'draft':
        values.update(bitrix_contact_id=5, bitrix_lead_id=6, bitrix_invoice_id=10,
            bitrix_product_row_id=11, bitrix_payment_id=12, payment_product_linked=True,
            payment_short_url='https://payment.example.test/synthetic', send_status='send_failed')
    try:
        await migrate('upgrade', '20260911_01')
        def old_payment(connection):
            metadata = MetaData()
            users = Table('users', metadata, autoload_with=connection)
            payments = Table('payment_transactions', metadata, autoload_with=connection)
            connection.execute(users.insert().values(id=uid, techportal_user_id='migration-test', email='test@example.test', permissions={}))
            connection.execute(payments.insert().values(id=tid, idempotency_key=key, **values))
        async with engine.begin() as connection:
            await connection.run_sync(old_payment)

        await migrate('upgrade', 'head')
        repo = PaymentEventRepository(sessions)
        async with sessions() as session:
            tx = await session.get(PaymentTransaction, tid)
            assert tx.idempotency_key == key and tx.client_resolution == {}
            assert tx.status == payment_status and tx.actual_amount == values['actual_amount']
            if payment_status != 'draft':
                assert tx.bitrix_invoice_id == 10 and tx.bitrix_payment_id == 12
                assert tx.payment_short_url == values['payment_short_url']
            assert await session.scalar(text('SELECT mode FROM payment_runtime_control WHERE id = 1')) == 'legacy'
        assert await repo.backfill() == 1
        assert await repo.backfill() == 0
        event = await repo.claim_outbox(30)
        assert event is not None
        claim = await repo.claim(event.job_id, 'migration-test', 30)
        assert claim is not None
        await repo.begin_write(claim, 'migration-marker', 'crm.contact.add', recovery={'origin': str(tid)})
        await repo.confirm_write(claim, 'migration-marker', '42')
        assert await repo.finish(claim)

        new_tables = {'payment_jobs', 'payment_outbox', 'payment_execution_leases', 'payment_external_writes',
            'payment_request_budgets', 'payment_event_quarantine', 'payment_callback_inbox',
            'payment_runtime_control', 'payment_runtime_members', 'payment_job_archive'}
        def check_models(connection):
            def include(obj, name, object_type, reflected, compare_to):
                return name in new_tables if object_type == 'table' else True
            context = MigrationContext.configure(connection, opts={'compare_type': True, 'include_object': include})
            assert compare_metadata(context, Base.metadata) == []
        async with engine.connect() as connection:
            await connection.run_sync(check_models)

        await migrate('downgrade', '20260911_01')
        async with sessions() as session:
            assert await session.scalar(text('SELECT idempotency_key FROM payment_transactions WHERE id=:id'), {'id': tid}) == key
            assert await session.scalar(text('SELECT status FROM payment_transactions WHERE id=:id'), {'id': tid}) == payment_status
            assert await session.scalar(text("SELECT count(*) FROM information_schema.tables WHERE table_schema=:schema AND table_name='payment_jobs'"), {'schema': schema}) == 0
        await migrate('upgrade', 'head')
        async with sessions() as session:
            tx = await session.get(PaymentTransaction, tid)
            assert tx.client_resolution == {} and tx.status == payment_status
        assert await repo.backfill() == 1
        assert await repo.backfill() == 0
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        await admin.dispose()
