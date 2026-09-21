import json

import httpx
import pytest

from app.config import Settings
from app.errors import ApiError, ServiceUnavailableError, TechPortalNotConfiguredError
from app.techportal_client import TechPortalClient


def settings() -> Settings:
    return Settings(
        tp_base_url="https://tp.example/api/v1",
        tp_base_token="system-token",
    )


def test_api_base_path_does_not_change_auth_origin() -> None:
    assert settings().tp_origin_url == "https://tp.example"


@pytest.mark.asyncio
async def test_ticket_list_uses_bearer_full_base_path_and_page_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://tp.example/api/v1/tickets/get")
        assert request.headers["Authorization"] == "Bearer system-token"
        payload = json.loads(request.content)
        assert payload["page"] == 0
        assert payload["filters"]["and"][0] == {
            "tags": {},
            "createdBy": [],
            "masterIds": [87],
            "scheduledTo": "30.07.2026",
            "scheduledFrom": "30.07.2026",
        }
        return httpx.Response(200, json=[])

    result = await TechPortalClient(
        settings(),
        transport=httpx.MockTransport(handler),
    ).tickets(87, "30.07.2026")

    assert result == []


@pytest.mark.asyncio
async def test_all_tickets_omits_master_filter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "masterIds" not in payload["filters"]["and"][0]
        return httpx.Response(200, json=[])

    assert await TechPortalClient(settings(), transport=httpx.MockTransport(handler)).tickets(None, "30.07.2026") == []


@pytest.mark.asyncio
async def test_all_tickets_passes_explicit_master_filter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["filters"]["and"][0]["masterIds"] == [87, "112"]
        return httpx.Response(200, json=[])

    result = await TechPortalClient(settings(), transport=httpx.MockTransport(handler)).tickets(
        None, "30.07.2026", [87, "112"],
    )
    assert result == []


@pytest.mark.asyncio
async def test_brigades_uses_techportal_directory_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == httpx.URL("https://tp.example/api/v1/techportal-user/brigades")
        return httpx.Response(200, json=[{"id": 4, "name": "Монтажники", "masterIds": [87, 112]}])

    brigades = await TechPortalClient(settings(), transport=httpx.MockTransport(handler)).brigades()
    assert brigades[0].id == 4
    assert brigades[0].master_ids == [87, 112]


@pytest.mark.asyncio
async def test_ticket_list_loads_all_pages_and_deduplicates_tickets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = json.loads(request.content)["page"]
        pages = {
            0: [{"id": 1, "masters": [87], "tags": {}}],
            1: [{"id": 1, "masters": [87], "tags": {}}, {"id": 2, "masters": [87], "tags": {}}],
            2: [],
        }
        return httpx.Response(200, json=pages[page])

    result = await TechPortalClient(settings(), transport=httpx.MockTransport(handler)).tickets(87, "30.07.2026")

    assert [ticket.id for ticket in result] == [1, 2]


@pytest.mark.asyncio
async def test_persist_sends_complete_tags_element() -> None:
    tags = {"Новое подключение": {}, "Работы произведены": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://tp.example/api/v1/tickets/persist")
        assert json.loads(request.content) == {"ticket": {"id": 32412, "tags": tags}}
        return httpx.Response(
            200,
            json={"id": 32412, "masters": [87], "tags": tags},
        )

    ticket = await TechPortalClient(
        settings(),
        transport=httpx.MockTransport(handler),
    ).persist_ticket(32412, tags)

    assert ticket.tags == tags


@pytest.mark.asyncio
async def test_dial_uses_origin_api_path_and_accepts_empty_success_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://tp.example/csrf-token"):
            assert request.headers["cookie"] == "tp-session=employee-session"
            return httpx.Response(200, json={"_csrf": "employee-csrf"})
        assert request.url == httpx.URL("https://tp.example/api/conversations/dial")
        assert request.headers["cookie"] == "tp-session=employee-session"
        assert "Authorization" not in request.headers
        assert request.headers["X-CSRF-Token"] == "employee-csrf"
        assert json.loads(request.content) == {"phone": "+79254553958"}
        return httpx.Response(204)

    await TechPortalClient(settings(), transport=httpx.MockTransport(handler)).dial(
        "+79254553958", {"tp-session": "employee-session"},
    )


@pytest.mark.asyncio
async def test_client_maps_configuration_auth_and_network_errors() -> None:
    with pytest.raises(TechPortalNotConfiguredError):
        await TechPortalClient(Settings(tp_base_token="")).users()

    auth_client = TechPortalClient(
        settings(),
        transport=httpx.MockTransport(lambda _: httpx.Response(401, json={})),
    )
    with pytest.raises(ApiError) as auth_error:
        await auth_client.users()
    assert auth_error.value.code == "TECHPORTAL_AUTH_FAILED"

    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    with pytest.raises(ServiceUnavailableError):
        await TechPortalClient(
            settings(),
            transport=httpx.MockTransport(timeout),
        ).users()
