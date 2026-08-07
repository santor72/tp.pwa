import httpx
import pytest

from app.auth_provider import TechPortalAuthProvider
from app.config import Settings


@pytest.mark.asyncio
async def test_authenticate_extracts_user_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    permissions = {
        "map": {"lines": "w", "clients": True},
        "client": {"block": True, "groups": "r"},
        "tickets": {"all": True, "create": True, "execution": True},
        "calendar": True,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/csrf-token":
            return httpx.Response(200, json={"_csrf": "upstream-csrf"}, headers={"set-cookie": "tp-session=employee-session; Path=/"})
        assert request.url.path == "/api/techportal-user/login"
        return httpx.Response(
            200,
            json={
                "id": 7,
                "email": "tech@example.test",
                "firstName": "Техник",
                "status": "active",
                "properties": {"permissions": permissions},
            },
        )

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def async_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", async_client)

    authenticated = await TechPortalAuthProvider(
        Settings(tp_base_url="https://tp.example"),
    ).authenticate("tech@example.test", "password")

    assert authenticated.user.user_permissions == permissions
    assert authenticated.cookies == {"tp-session": "employee-session"}


@pytest.mark.asyncio
async def test_authenticate_defaults_missing_permissions_to_empty_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/csrf-token":
            return httpx.Response(200, json={"_csrf": "upstream-csrf"})
        return httpx.Response(200, json={"id": 7, "email": "tech@example.test"})

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def async_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", async_client)

    authenticated = await TechPortalAuthProvider(
        Settings(tp_base_url="https://tp.example"),
    ).authenticate("tech@example.test", "password")

    assert authenticated.user.user_permissions == {}
