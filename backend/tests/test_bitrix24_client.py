import json

import httpx
import pytest

from app.bitrix24_client import Bitrix24Client
from app.config import Settings
from app.errors import Bitrix24Error


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"result": None}, {"error": "", "error_description": "private"}])
async def test_t17_activity_http_400_without_error_is_not_success(body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(400, json=body))) as http:
        client = Bitrix24Client(Settings(_env_file=None, bx24_webhook="https://bx.test/rest/1/secret"), http)
        with pytest.raises(Bitrix24Error) as caught:
            await client.add_activity({"SUBJECT": "test"})
        assert "private" not in str(caught.value)


@pytest.mark.asyncio
async def test_activity_successful_null_result_remains_supported():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"result": None}))) as http:
        client = Bitrix24Client(Settings(_env_file=None, bx24_webhook="https://bx.test/rest/1/secret"), http)
        assert await client.add_activity({"SUBJECT": "test"}) is None


@pytest.mark.asyncio
async def test_bitrix_client_calls_relative_method_and_unwraps_result() -> None:
    seen: list[httpx.Request] = []
    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {"ok": True}})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Bitrix24Client(Settings(bx24_webhook="https://bx.test/rest/1/secret"), http)
    assert await client.call("crm.lead.list", {"filter": {}}) == {"ok": True}
    assert seen[0].url == httpx.URL("https://bx.test/rest/1/secret/crm.lead.list.json")
    await http.aclose()


@pytest.mark.asyncio
async def test_bitrix_error_does_not_expose_webhook_or_response_body() -> None:
    secret = "webhook-secret"
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "BAD", "error_description": f"phone +79991234567 {secret}"})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Bitrix24Client(Settings(bx24_webhook=f"https://bx.test/rest/1/{secret}"), http)
    with pytest.raises(Bitrix24Error) as caught:
        await client.call("crm.lead.add", {"fields": {"PHONE": "+79991234567"}}, read=False)
    assert secret not in str(caught.value)
    assert "+79991234567" not in str(caught.value)
    await http.aclose()


@pytest.mark.asyncio
async def test_write_is_not_retried_after_unknown_failure() -> None:
    calls = 0
    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout")
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Bitrix24Client(Settings(bx24_webhook="https://bx.test/rest/1/secret"), http)
    with pytest.raises(Bitrix24Error):
        await client.call("crm.lead.add", {}, read=False)
    assert calls == 1
    await http.aclose()


@pytest.mark.asyncio
async def test_payment_rest_wrappers_use_bitrix24_contract() -> None:
    seen: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        seen.append((method, body))
        responses = {
            "crm.item.productrow.add": {"result": {"productRow": {"id": 11}}},
            "crm.item.payment.add": {"result": 12},
            "crm.item.payment.product.add": {"result": 13},
            "salescenter.payment.getPublicUrl": {"result": {"payment": {"url": "https://pay.test/full", "shortUrl": "https://pay.test/s", "qr": "AA=="}}},
        }
        return httpx.Response(200, json=responses[method])

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Bitrix24Client(Settings(bx24_webhook="https://bx.test/rest/1/secret"), http)
    assert await client.add_product_row(10, {"productId": 123, "price": "1500.00", "quantity": 1}) == 11
    assert await client.create_payment(10) == 12
    assert await client.add_payment_product(12, 11) == 13
    links = await client.payment_public_url(12)

    assert seen == [
        ("crm.item.productrow.add", {"fields": {"ownerId": 10, "ownerType": "SI", "productId": 123, "price": "1500.00", "quantity": 1}}),
        ("crm.item.payment.add", {"entityTypeId": 31, "entityId": 10}),
        ("crm.item.payment.product.add", {"paymentId": 12, "rowId": 11, "quantity": 1}),
        ("salescenter.payment.getPublicUrl", {"id": 12}),
    ]
    assert links["qr"] == "data:image/png;base64,AA=="
    await http.aclose()


@pytest.mark.asyncio
async def test_payment_product_recovery_uses_entity_id_from_real_list_shape() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": [{"id": 13, "paymentId": 12, "entityId": 11, "quantity": 1}]})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Bitrix24Client(Settings(bx24_webhook="https://bx.test/rest/1/secret"), http)
    assert await client.payment_product_linked(12, 11) is True
    await http.aclose()


@pytest.mark.asyncio
async def test_get_invoice_unwraps_universal_item() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"entityTypeId": 31, "id": 10}
        return httpx.Response(200, json={"result": {"item": {"id": 10, "stageId": "DT31_1:SENT"}}})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Bitrix24Client(Settings(bx24_webhook="https://bx.test/rest/1/secret"), http)
    assert await client.get_invoice(10) == {"id": 10, "stageId": "DT31_1:SENT"}
    await http.aclose()
