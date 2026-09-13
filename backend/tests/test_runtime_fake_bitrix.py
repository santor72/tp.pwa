from types import SimpleNamespace
import asyncio

import httpx
import pytest

from app.bitrix24_client import Bitrix24Client
from app.config import Settings
from app.payment_client_resolver import PaymentClientResolver
from app.payments import PaymentService
from app.payment_status import PaymentStatusHandler
from runtime_fake_bitrix import create_app
from test_payment_service import FakeRepository, make_transaction


@pytest.mark.asyncio
async def test_http_fake_can_hold_response_after_committing_remote_write():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url='http://fake') as http:
        assert (await http.post('/_control/hold/crm.item.add')).status_code == 200
        task = asyncio.create_task(http.post('/rest/1/test/crm.item.add.json', json={'fields': {'xmlId': 'hold-test'}}))
        try:
            async with asyncio.timeout(2):
                while (await http.get('/_control/held')).json()['waiting'] != 1:
                    await asyncio.sleep(0.01)
            assert not task.done()
            state = (await http.get('/_control/state')).json()
            assert len(state['tables']['invoices']) == 1
            assert (await http.post('/_control/hold/crm.item.add')).status_code == 409
            await http.post('/_control/release')
            response = await asyncio.wait_for(task, 2)
            assert response.status_code == 200
            assert str(response.json()['result']['item']['id']) in state['tables']['invoices']
            assert (await http.get('/_control/held')).json()['waiting'] == 0
        finally:
            await http.post('/_control/release')
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_http_fake_covers_formation_send_and_paid_followups():
    app = create_app()
    settings = Settings(_env_file=None, bx24_webhook='http://fake/rest/1/test',
        bx24_payment_link_field='ufCrmTestLink', bx24_payment_send_trigger='stageId=TEST:SEND',
        bx24_payment_create_activity=True)
    tx = make_transaction()
    tx.email = None
    repo = FakeRepository(tx)
    async def by_payment(payment_id): return tx if tx.bitrix_payment_id == payment_id else None
    repo.get_by_payment_id = by_payment
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fake') as http:
        bitrix = Bitrix24Client(settings, http)
        service = PaymentService(settings, repo, SimpleNamespace(), PaymentClientResolver(settings, bitrix), bitrix)
        result = await service.process(tx.id)
        assert result.status == 'send_queued'
        assert result.payment_url and result.formation_timeline_created and result.formation_activity_created
        await http.post(f'/_control/pay/{tx.bitrix_payment_id}')
        result = await PaymentStatusHandler(settings, repo, bitrix).handle(tx.bitrix_payment_id)
        assert result.status == 'paid' and result.paid_timeline_created and result.paid_activity_created
        state = (await http.get('/_control/state')).json()
        assert len(state['tables']['invoices']) == len(state['tables']['payments']) == 1
        assert len(state['tables']['comments']) == len(state['tables']['activities']) == 2
        assert (await http.post('/rest/1/test/unknown.method.json', json={})).status_code == 400
