"""Read-only reconciliation of interrupted CRM writes. No blind write retries."""
import hashlib
import json
import re

from app.payment_execution import safe_write_result

MARKER = re.compile(r'Операция ТехПортала: [0-9a-f-]{36}; событие: [a-z-]+')


def field_hash(value):
    if isinstance(value, list):
        value = [{k: v for k, v in item.items() if k != 'ID'} if isinstance(item, dict) else item for item in value]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def recovery_spec(method, params):
    fields = params.get('fields', {})
    if method in {'crm.lead.add', 'crm.contact.add'}:
        origin = str(fields.get('ORIGIN_ID', ''))
        if re.fullmatch(r'[0-9a-f-]{36}(?::lead:[0-9]+)?', origin):
            return {'origin': origin}
    if method == 'crm.item.add':
        return {'xml_id': fields.get('xmlId')}
    if method == 'crm.item.productrow.add':
        return {'invoice_id': fields.get('ownerId'), 'product_id': fields.get('productId')}
    if method == 'crm.item.payment.add':
        return {'invoice_id': params.get('entityId')}
    if method == 'crm.item.payment.product.add':
        return {'payment_id': params.get('paymentId'), 'row_id': params.get('rowId')}
    if method == 'crm.lead.contact.add':
        return {'lead_id': params.get('id'), 'contact_id': fields.get('CONTACT_ID')}
    if method in {'crm.timeline.comment.add', 'crm.activity.add'}:
        marker = MARKER.search(str(fields.get('COMMENT', fields.get('DESCRIPTION', ''))))
        if marker:
            return {'marker': marker.group(), 'entity_type': fields.get('ENTITY_TYPE'), 'entity_id': fields.get('ENTITY_ID')}
    if method in {'crm.contact.update', 'crm.lead.update', 'crm.item.update', 'crm.activity.update'}:
        return {'id': params.get('id'), 'expected': {k: field_hash(v) for k, v in fields.items()}}
    if method == 'crm.item.payment.delete':
        return {'id': params.get('id')}
    return {}


def unique_id(rows):
    if isinstance(rows, list) and len(rows) == 1:
        value = rows[0].get('id', rows[0].get('ID'))
        if str(value).isdecimal():
            return int(value)
    return None


async def recover_write(bitrix, write, transaction):
    """Return sanitized JSON result, or None when absence/ambiguity is not proof."""
    method, spec = write.method, write.recovery
    if not spec:
        return None
    result = None
    if method in {'crm.lead.add', 'crm.contact.add'}:
        result = unique_id(await bitrix.find_by_origin('lead' if method == 'crm.lead.add' else 'contact', spec['origin']))
    elif method == 'crm.item.add':
        response = await bitrix.call('crm.item.list', {'entityTypeId': 31, 'filter': {'=xmlId': spec['xml_id']}, 'select': ['id']})
        value = unique_id(response.get('items', []))
        if value: result = {'item': {'id': value}}
    elif method == 'crm.item.productrow.add':
        response = await bitrix.call('crm.item.productrow.list', {'filter': {'=ownerId': spec['invoice_id'], '=ownerType': 'SI'}})
        rows = response.get('productRows', response.get('items', []))
        value = unique_id([r for r in rows if int(r.get('productId', r.get('PRODUCT_ID', 0))) == spec['product_id']])
        if value: result = {'productRow': {'id': value}}
    elif method == 'crm.item.payment.add':
        response = await bitrix.call('crm.item.payment.list', {'entityTypeId': 31, 'entityId': spec['invoice_id']})
        rows = response if isinstance(response, list) else response.get('payments', response.get('items', []))
        result = unique_id(rows)
    elif method == 'crm.item.payment.product.add':
        response = await bitrix.call('crm.item.payment.product.list', {'paymentId': spec['payment_id'], 'filter': {}})
        rows = response if isinstance(response, list) else response.get('products', response.get('items', []))
        result = unique_id([r for r in rows if int(r.get('entityId', r.get('rowId', 0))) == spec['row_id']])
    elif method == 'crm.lead.contact.add':
        rows = await bitrix.lead_contacts(spec['lead_id'])
        if any(int(r.get('CONTACT_ID', 0)) == spec['contact_id'] for r in rows): result = True
    elif method == 'crm.timeline.comment.add':
        if await bitrix.timeline_has_marker(spec['entity_type'], spec['entity_id'], spec['marker']): return 'null'
    elif method == 'crm.activity.add':
        result = await bitrix.activity_id_by_marker(spec['marker'])
    elif method.endswith('.update'):
        if method == 'crm.item.update':
            entity = await bitrix.get_invoice(spec['id'])
        elif method == 'crm.contact.update':
            entity = await bitrix.get_contact(spec['id'])
        elif method == 'crm.lead.update':
            entity = await bitrix.get_lead(spec['id'])
        else:
            entity = await bitrix.call('crm.activity.get', {'id': spec['id']}, read=True)
        if all(field_hash(entity.get(key)) == expected for key, expected in spec['expected'].items()): result = True
    elif method == 'crm.item.payment.delete' and transaction.bitrix_invoice_id:
        response = await bitrix.call('crm.item.payment.list', {'entityTypeId': 31, 'entityId': transaction.bitrix_invoice_id})
        rows = response if isinstance(response, list) else response.get('payments', response.get('items', []))
        # Empty first page is conclusive; a non-empty/paginated list is not.
        if rows == []: result = True
    return safe_write_result(result) if result is not None else None
