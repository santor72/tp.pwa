"""Explicit localhost-only probe for the disposable Compose HTTP workflow."""
import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import User, PaymentTransaction
from app.payment_event_repository import PaymentEventRepository


async def run(database_url, api_url, fake_url, compose_network=False):
    assert database_url.rsplit('/', 1)[-1] == 'payment_events_test'
    hosts = tuple(urlsplit(url).hostname for url in (database_url, api_url, fake_url))
    assert hosts == (('postgres', 'api', 'fake-bitrix') if compose_network else ('127.0.0.1',) * 3)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f'{fake_url}/_control/state')
            response.raise_for_status()
            baseline = response.json()
            initial_counts = {name: len(rows) for name, rows in baseline['tables'].items()}
        async with sessions.begin() as session:
            user = User(techportal_user_id='runtime-' + uuid4().hex, email='runtime@example.test', permissions={})
            session.add(user)
            await session.flush()
            user_id = user.id
        repo = PaymentEventRepository(sessions, expected_mode='events')
        ids = []
        for _ in range(2):
            now = datetime.now(UTC)
            tx, _ = await repo.create_payment(uuid4(), dict(user_id=user_id, employee_external_id='runtime-test',
                product_id=49839, product_title='Тестовая услуга', catalog_amount=Decimal('100'), actual_amount=Decimal('100'),
                currency='RUB', first_name='Тест', last_name='Стенд', phone_normalized='+79990000001',
                status='draft', current_step='draft', expires_at=now + timedelta(hours=1)))
            ids.append(tx.id)

        async def until(predicate):
            async with asyncio.timeout(60):
                while True:
                    async with sessions() as session:
                        rows = list(await session.scalars(select(PaymentTransaction).where(PaymentTransaction.id.in_(ids))))
                    if all(predicate(row) for row in rows): return rows
                    if any(row.status == 'failed' for row in rows):
                        raise AssertionError([(row.current_step, row.last_error_code) for row in rows])
                    await asyncio.sleep(0.1)

        rows = await until(lambda row: row.status == 'send_queued' and row.formation_timeline_created and row.formation_activity_created)
        async with httpx.AsyncClient(timeout=10) as client:
            for row in rows:
                assert row.payment_url and row.payment_qr
                assert (await client.post(f'{fake_url}/_control/pay/{row.bitrix_payment_id}')).status_code == 200
                response = await client.post(f'{api_url}/api/webhooks/bitrix24/payments',
                    headers={'x-bitrix-token': 'runtime-callback-test-token'}, json={'data': {'FIELDS': {'ID': row.bitrix_payment_id}}})
                assert response.status_code == 202, response.status_code
            await until(lambda row: row.status == 'paid' and row.paid_timeline_created and row.paid_activity_created)
            state = (await client.get(f'{fake_url}/_control/state')).json()
            counts = {name: len(rows) - initial_counts[name] for name, rows in state['tables'].items()}
            assert counts == {'invoices': 2, 'rows': 2, 'payments': 2, 'products': 2, 'comments': 4, 'activities': 4}, counts
            print({'result': 'passed', 'payments': 2, 'entity_counts': counts, 'rest_calls': len(state['calls']) - len(baseline['calls'])})
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--api-url', required=True)
    parser.add_argument('--fake-url', required=True)
    parser.add_argument('--compose-network', action='store_true', help='Use only the isolated Compose service names')
    args = parser.parse_args()
    asyncio.run(run(args.database_url, args.api_url, args.fake_url, args.compose_network))
