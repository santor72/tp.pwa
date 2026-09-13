"""Stateful HTTP fake for the isolated runtime Compose only. Never deploy in prod."""
import asyncio

from fastapi import FastAPI, HTTPException, Request


def create_app(*, contact_ids=(5,)):
    app = FastAPI()
    tables = {name: {} for name in ('invoices', 'rows', 'payments', 'products', 'comments', 'activities')}
    calls = []
    next_id = 100
    held_method = None
    waiting = 0
    release = asyncio.Event()
    release.set()

    @app.middleware('http')
    async def hold_committed_response(request, call_next):
        nonlocal waiting
        response = await call_next(request)
        if request.url.path == f'/rest/1/test/{held_method}.json' and not release.is_set():
            waiting += 1
            try:
                await release.wait()
            finally:
                waiting -= 1
        return response

    @app.post('/_control/hold/{method}')
    async def hold(method: str):
        nonlocal held_method
        if waiting:
            raise HTTPException(409, 'Release the pending response first')
        held_method = method
        release.clear()
        return {'held_method': held_method}

    @app.post('/_control/release')
    async def release_response():
        release.set()
        return {'released': True}

    @app.get('/_control/held')
    async def held():
        return {'method': held_method, 'waiting': waiting}

    def add(table, fields):
        nonlocal next_id
        next_id += 1
        tables[table][next_id] = {'id': next_id, 'ID': next_id, **fields}
        return next_id

    def matching(table, filters):
        def match(row, key, value):
            actual = row.get(key.lstrip('=%'))
            return str(value) in str(actual or '') if key.startswith('%') else str(actual) == str(value)
        return [row for row in tables[table].values() if all(match(row, k, v) for k, v in filters.items())]

    @app.get('/health')
    async def health(): return {'status': 'ok'}

    @app.get('/_control/state')
    async def state(): return {'tables': tables, 'calls': calls}

    @app.post('/_control/pay/{payment_id}')
    async def pay(payment_id: int):
        if payment_id not in tables['payments']: raise HTTPException(404)
        tables['payments'][payment_id]['paid'] = True
        return {'paid': True}

    @app.post('/rest/1/test/{method}.json')
    async def rest(method: str, request: Request):
        params = await request.json()
        calls.append(method)
        fields = params.get('fields', {})
        if method == 'crm.duplicate.findbycomm':
            result = {'CONTACT': list(contact_ids)} if params['entity_type'] == 'CONTACT' else {}
        elif method == 'crm.contact.get':
            if int(params['id']) not in contact_ids: raise HTTPException(404)
            result = {'ID': int(params['id']), 'NAME': 'Тест', 'LAST_NAME': 'Стенд', 'EMAIL': []}
        elif method == 'crm.item.add':
            result = {'item': {'id': add('invoices', fields)}}
        elif method == 'crm.item.list':
            result = {'items': matching('invoices', params.get('filter', {}))}
        elif method == 'crm.item.get':
            result = {'item': tables['invoices'][int(params['id'])]}
        elif method == 'crm.item.update':
            tables['invoices'][int(params['id'])].update(fields)
            result = True
        elif method == 'crm.item.productrow.add':
            result = {'productRow': {'id': add('rows', fields)}}
        elif method == 'crm.item.productrow.list':
            result = {'productRows': matching('rows', params.get('filter', {}))}
        elif method == 'crm.item.payment.add':
            result = add('payments', {'entityId': params['entityId'], 'paid': False})
        elif method == 'crm.item.payment.list':
            result = matching('payments', {'entityId': params['entityId']})
        elif method == 'crm.item.payment.get':
            result = tables['payments'][int(params['id'])]
        elif method == 'crm.item.payment.delete':
            del tables['payments'][int(params['id'])]
            result = True
        elif method == 'crm.item.payment.product.add':
            result = add('products', {'paymentId': params['paymentId'], 'entityId': params['rowId']})
        elif method == 'crm.item.payment.product.list':
            result = matching('products', {'paymentId': params['paymentId']})
        elif method == 'salescenter.payment.getPublicUrl':
            if int(params['id']) not in tables['payments']: raise HTTPException(404)
            result = {'url': f'https://payment.example.test/{params["id"]}', 'qr': 'data:image/png;base64,AA=='}
        elif method == 'crm.timeline.comment.add':
            result = add('comments', fields)
        elif method == 'crm.timeline.comment.list':
            result = matching('comments', params.get('filter', {}))
        elif method == 'crm.activity.add':
            result = add('activities', fields)
        elif method == 'crm.activity.list':
            result = matching('activities', params.get('filter', {}))
        elif method == 'crm.activity.update':
            tables['activities'][int(params['id'])].update(fields)
            result = True
        else:
            raise HTTPException(400, f'Unsupported fake method: {method}')
        return {'result': result}

    return app


app = create_app()
