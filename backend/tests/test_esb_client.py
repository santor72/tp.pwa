import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.errors import ApiError, EsbCallError, EsbResponseError, ServiceUnavailableError
from app.esb_client import EsbClient


def settings() -> Settings:
    return Settings(esb_base_url="https://esb.example/api", esb_base_token="system-token")


def test_esb_base_url_defaults_to_https_when_scheme_is_missing() -> None:
    assert Settings(esb_base_url="esb.example/api").esb_base_url == "https://esb.example/api"
    with pytest.raises(ValidationError):
        Settings(esb_base_url="ftp://esb.example/api")


@pytest.mark.asyncio
async def test_esb_client_sends_bearer_and_query_parameters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer system-token"
        assert request.url == httpx.URL("https://esb.example/api/flat-search?locid=4217&field_flat=143")
        return httpx.Response(200, json={"ok": True, "flats": []})

    result = await EsbClient(settings(), transport=httpx.MockTransport(handler)).request(
        "GET",
        "flat-search",
        params={"locid": 4217, "field_flat": 143},
    )

    assert result == {"ok": True, "flats": []}


@pytest.mark.asyncio
async def test_esb_client_maps_ok_false_to_error() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"ok": False, "reason": "Операция отклонена"})
    )

    with pytest.raises(EsbCallError, match="Операция отклонена"):
        await EsbClient(settings(), transport=transport).request("GET", "locations")


@pytest.mark.asyncio
async def test_esb_client_maps_auth_and_invalid_json() -> None:
    auth_transport = httpx.MockTransport(lambda _: httpx.Response(401, json={"detail": "no"}))
    with pytest.raises(ApiError) as auth_error:
        await EsbClient(settings(), transport=auth_transport).request("GET", "locations")
    assert auth_error.value.code == "ESB_AUTH_FAILED"

    invalid_transport = httpx.MockTransport(lambda _: httpx.Response(200, text="<html>"))
    with pytest.raises(EsbResponseError):
        await EsbClient(settings(), transport=invalid_transport).request("GET", "locations")


@pytest.mark.asyncio
async def test_esb_client_maps_timeout_to_service_unavailable() -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    with pytest.raises(ServiceUnavailableError):
        await EsbClient(settings(), transport=httpx.MockTransport(timeout)).request("GET", "locations")
