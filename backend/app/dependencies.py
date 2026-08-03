import secrets
import logging

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.actors import Actor
from app.errors import CsrfError, OriginError, SessionExpiredError
from app.logging import audit, stable_hash

logger = logging.getLogger(__name__)
from app.schemas import SessionData
from app.session_store import SessionStore


def get_session_store(request: Request) -> SessionStore:
    return request.app.state.session_store


async def require_session(
    request: Request,
    store: SessionStore = Depends(get_session_store),
    settings: Settings = Depends(get_settings),
) -> tuple[str, SessionData]:
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        raise SessionExpiredError()
    try:
        session = await store.get(session_id)
    except SessionExpiredError:
        audit(logger, "auth.session.expired", result="failure", session_hash=stable_hash(session_id))
        raise
    return session_id, session


def verify_origin(request: Request, settings: Settings) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in settings.origin_set:
        raise OriginError()


async def require_csrf(
    request: Request,
    session_pair: tuple[str, SessionData] = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> tuple[str, SessionData]:
    verify_origin(request, settings)
    supplied = request.headers.get("x-csrf-token", "")
    if not secrets.compare_digest(supplied, session_pair[1].csrf_token):
        raise CsrfError()
    return session_pair


async def actor_from_session(request: Request, session: SessionData) -> Actor:
    internal_user_id = session.internal_user_id
    if internal_user_id is not None:
        return Actor(user_id=internal_user_id, techportal_user_id=str(session.user.id), channel="pwa", permissions=session.user.user_permissions, role=session.user.role)
    user_id = await request.app.state.messenger_links.user_id_for_techportal_user(session.user.id)
    if user_id is None:
        from app.errors import ApiError
        raise ApiError(503, "USER_STORAGE_UNAVAILABLE", "Не удалось найти пользователя")
    return Actor(user_id=user_id, techportal_user_id=str(session.user.id), channel="pwa", permissions=session.user.user_permissions, role=session.user.role)
