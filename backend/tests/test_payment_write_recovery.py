"""Read-only evidence matrix for interrupted external writes."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.payment_write_recovery import recover_write, recovery_spec

ORIGIN = '11111111-1111-1111-1111-111111111111'
MARKER = f'Операция ТехПортала: {ORIGIN}; событие: formation'

CASES = [
    ('crm.contact.add', {'fields': {'ORIGIN_ID': ORIGIN}}, 'find_by_origin', [{'ID': '7'}], [], 7),
    ('crm.lead.add', {'fields': {'ORIGIN_ID': ORIGIN}}, 'find_by_origin', [{'ID': '7'}], [], 7),
    ('crm.item.add', {'fields': {'xmlId': ORIGIN}}, 'call', {'items': [{'id': 7}]}, {'items': []}, {'item': {'id': 7}}),
    ('crm.item.productrow.add', {'fields': {'ownerId': 1, 'productId': 2}}, 'call',
        {'productRows': [{'id': 7, 'productId': 2}]}, {'productRows': []}, {'productRow': {'id': 7}}),
    ('crm.item.payment.add', {'entityId': 1}, 'call', [{'id': 7}], [], 7),
    ('crm.item.payment.product.add', {'paymentId': 1, 'rowId': 2}, 'call', [{'id': 7, 'entityId': 2}], [], 7),
    ('crm.lead.contact.add', {'id': 1, 'fields': {'CONTACT_ID': 7}}, 'lead_contacts', [{'CONTACT_ID': 7}], [], True),
    ('crm.timeline.comment.add', {'fields': {'COMMENT': MARKER, 'ENTITY_TYPE': 'contact', 'ENTITY_ID': 1}},
        'timeline_has_marker', True, False, None),
    ('crm.activity.add', {'fields': {'DESCRIPTION': MARKER}}, 'activity_id_by_marker', 7, None, 7),
    ('crm.contact.update', {'id': 1, 'fields': {'PHONE': [{'VALUE': '+79990000001', 'VALUE_TYPE': 'WORK'}]}},
        'get_contact', {'PHONE': [{'ID': '99', 'VALUE': '+79990000001', 'VALUE_TYPE': 'WORK'}]}, {'PHONE': []}, True),
    ('crm.lead.update', {'id': 1, 'fields': {'PARENT_ID_1032': 7}}, 'get_lead', {'PARENT_ID_1032': 7}, {}, True),
    ('crm.item.update', {'id': 1, 'fields': {'stageId': 'TEST:SEND'}}, 'get_invoice', {'stageId': 'TEST:SEND'}, {}, True),
    ('crm.activity.update', {'id': 1, 'fields': {'COMPLETED': 'Y'}}, 'call', {'COMPLETED': 'Y'}, {'COMPLETED': 'N'}, True),
    ('crm.item.payment.delete', {'id': 7}, 'call', [], [{'id': 7}], True),
]


@pytest.mark.asyncio
@pytest.mark.parametrize('method,params,reader,present,absent,expected', CASES, ids=[c[0] for c in CASES])
@pytest.mark.parametrize('visible', [False, True])
async def test_recovery_requires_read_evidence(method, params, reader, present, absent, expected, visible):
    read = AsyncMock(return_value=present if visible else absent)
    bitrix = SimpleNamespace(**{reader: read})
    spec = recovery_spec(method, params)
    assert spec
    result = await recover_write(bitrix, SimpleNamespace(method=method, recovery=spec),
        SimpleNamespace(bitrix_invoice_id=1))
    read.assert_awaited_once()
    if not visible:
        assert result is None
    else:
        assert result is not None
        assert json.loads(result) == expected
    if reader == 'call':
        called_method = read.await_args.args[0]
        assert called_method.endswith(('.list', '.get'))  # Never repeat the write.


@pytest.mark.asyncio
@pytest.mark.parametrize('method,params,reader,present,absent,expected', CASES[:6], ids=[c[0] for c in CASES[:6]])
async def test_ambiguous_creation_is_not_recovered(method, params, reader, present, absent, expected):
    if isinstance(present, list):
        ambiguous = present * 2
    else:
        ambiguous = {key: rows * 2 for key, rows in present.items()}
    bitrix = SimpleNamespace(**{reader: AsyncMock(return_value=ambiguous)})
    assert await recover_write(bitrix, SimpleNamespace(method=method, recovery=recovery_spec(method, params)),
        SimpleNamespace(bitrix_invoice_id=1)) is None
