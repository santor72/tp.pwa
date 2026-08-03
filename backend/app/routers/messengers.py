from fastapi import APIRouter, Depends, Request, Response

from app.config import Settings, get_settings
from app.capabilities import capabilities_for_actor
from app.dependencies import actor_from_session, require_csrf, require_session
from app.errors import ApiError, PermissionDeniedError
from app.schemas import MessengerLinkCreateResponse, MessengerLinkResponse, SessionData

router = APIRouter(prefix="/api/messenger-links", tags=["messengers"])


@router.get("", response_model=list[MessengerLinkResponse])
async def list_messenger_links(
    request: Request,
    session_pair: tuple[str, SessionData] = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> list[MessengerLinkResponse]:
    _, session = session_pair
    actor = await actor_from_session(request, session)
    if not capabilities_for_actor(actor, settings.messenger_show).messenger_settings:
        raise PermissionDeniedError()
    identities = await request.app.state.messenger_links.list_active(actor.user_id)
    return [
        MessengerLinkResponse(provider="telegram", linked_at=item.linked_at, username=item.username, display_name=item.display_name)
        for item in identities
        if item.provider == "telegram"
    ]


@router.post("/telegram", response_model=MessengerLinkCreateResponse)
async def create_telegram_link(
    request: Request,
    session_pair: tuple[str, SessionData] = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
) -> MessengerLinkCreateResponse:
    if not settings.telegram_bot_username:
        raise ApiError(503, "TELEGRAM_NOT_CONFIGURED", "Telegram-бот не настроен")
    _, session = session_pair
    actor = await actor_from_session(request, session)
    if not capabilities_for_actor(actor, settings.messenger_show).messenger_settings:
        raise PermissionDeniedError()
    result = await request.app.state.messenger_links.create(actor.user_id, "telegram")
    return MessengerLinkCreateResponse(
        provider="telegram",
        deep_link=f"https://t.me/{settings.telegram_bot_username}?start={result.token}",
        expires_at=result.expires_at,
    )


@router.delete("/telegram", status_code=204)
async def revoke_telegram_link(
    request: Request,
    session_pair: tuple[str, SessionData] = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
) -> Response:
    _, session = session_pair
    actor = await actor_from_session(request, session)
    if not capabilities_for_actor(actor, settings.messenger_show).messenger_settings:
        raise PermissionDeniedError()
    await request.app.state.messenger_links.revoke(actor.user_id, "telegram")
    return Response(status_code=204)
