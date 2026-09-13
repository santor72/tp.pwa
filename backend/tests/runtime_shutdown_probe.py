"""Run inside tp-payment-runtime-test only; deterministic SIGTERM checkpoints."""
import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import PaymentTransaction, PaymentJob, PaymentExternalWrite, User
from app.payment_event_repository import PaymentEventRepository


async def run(action):
    engine = create_async_engine('postgresql+asyncpg://events_test:events_test@postgres:5432/payment_events_test')
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with httpx.AsyncClient(base_url='http://fake-bitrix:8000', timeout=5) as http:
            if action == 'prepare':
                async with sessions.begin() as db:
                    assert not await db.scalar(select(PaymentTransaction.id).limit(1)), 'requires fresh runtime test DB'
                    user = User(techportal_user_id='shutdown-test', email='shutdown@example.test', permissions={})
                    db.add(user)
                    await db.flush()
                    uid = user.id
                await http.post('/_control/hold/crm.item.add')
                repo = PaymentEventRepository(sessions, expected_mode='events')
                for _ in range(2):
                    await repo.create_payment(uuid4(), dict(user_id=uid, employee_external_id='shutdown-test',
                        product_id=49839, product_title='Test', catalog_amount=Decimal('100'), actual_amount=Decimal('100'),
                        currency='RUB', first_name='Test', last_name='Test', phone_normalized='+79990000001',
                        status='draft', current_step='draft', expires_at=datetime.now(UTC) + timedelta(hours=1)))
                async with asyncio.timeout(20):
                    while (await http.get('/_control/held')).json()['waiting'] != 1:
                        await asyncio.sleep(0.1)
                print({'checkpoint': 'remote_invoice_committed_response_held'}, flush=True)
            elif action == 'release':
                await http.post('/_control/release')
            if action == 'verify-recovered':
                async with asyncio.timeout(30):
                    while True:
                        async with sessions() as db:
                            pending = list(await db.scalars(select(PaymentJob.state).where(PaymentJob.kind == 'formation')))
                        if len(pending) == 2 and all(state == 'completed' for state in pending):
                            break
                        await asyncio.sleep(0.1)
            async with sessions() as db:
                payments = list(await db.scalars(select(PaymentTransaction).order_by(PaymentTransaction.created_at)))
                jobs = list(await db.scalars(select(PaymentJob).where(PaymentJob.kind == 'formation').order_by(PaymentJob.created_at)))
                unknown = list(await db.scalars(select(PaymentExternalWrite).where(
                    PaymentExternalWrite.state.in_({'unknown', 'in_flight'}))))
            state = (await http.get('/_control/state')).json()
            print({'payments': [p.status for p in payments], 'formation_jobs': [j.state for j in jobs],
                'entity_counts': {name: len(rows) for name, rows in state['tables'].items()}}, flush=True)
            if action == 'verify-drained':
                assert [j.state for j in jobs] == ['completed', 'ready']
                assert len(state['tables']['invoices']) == len(state['tables']['payments']) == 1
                assert payments[0].payment_qr and not payments[1].payment_qr
            if action == 'verify-interrupted':
                assert [j.state for j in jobs] == ['running', 'ready']
                assert len(unknown) == 1 and unknown[0].method == 'crm.item.add'
                assert len(state['tables']['invoices']) == 1 and not state['tables']['payments']
                assert not any(p.payment_qr for p in payments)
            if action == 'verify-recovered':
                assert len(payments) == 2 and all(p.payment_qr for p in payments)
                assert all(j.state == 'completed' for j in jobs)
                assert len(state['tables']['invoices']) == len(state['tables']['payments']) == 2
                assert not unknown
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'release', 'status', 'verify-drained', 'verify-interrupted', 'verify-recovered'])
    asyncio.run(run(parser.parse_args().action))
