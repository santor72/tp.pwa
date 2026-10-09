"""Keep active users' upstream TechPortal sessions alive."""

import asyncio
import logging

from app.errors import ApiError, TechPortalAuthError
from app.logging import audit
from app.session_store import SessionStore
from app.techportal_client import TechPortalClient

logger = logging.getLogger(__name__)
KEEPALIVE_INTERVAL_SECONDS = 24 * 60 * 60
SCAN_INTERVAL_SECONDS = 60 * 60


async def run_techportal_keepalive(
    store: SessionStore,
    client: TechPortalClient,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            sessions = await store.active_sessions()
            for session_id, session in sessions:
                if stop.is_set():
                    break
                if not session.upstream_cookies:
                    continue
                if not await store.claim_upstream_keepalive(session_id, KEEPALIVE_INTERVAL_SECONDS):
                    continue
                try:
                    cookies = await client.keepalive(session.upstream_cookies)
                except TechPortalAuthError as exc:
                    audit(
                        logger,
                        "auth.techportal.keepalive.failed",
                        user_id=session.user.id,
                        result="failure",
                        code=exc.code,
                    )
                    continue
                except ApiError as exc:
                    logger.warning(
                        "Не удалось продлить сессию ТехПортала",
                        extra={
                            "event": "techportal.keepalive.unavailable",
                            "fields": {"user_id": session.user.id, "code": exc.code},
                        },
                    )
                    continue

                if await store.update_upstream_cookies_if_active(session_id, cookies):
                    audit(
                        logger,
                        "auth.techportal.keepalive.succeeded",
                        user_id=session.user.id,
                        result="success",
                    )
        except ApiError as exc:
            logger.warning(
                "Не удалось прочитать активные сессии для keepalive",
                extra={"event": "techportal.keepalive.unavailable", "fields": {"code": exc.code}},
            )
        except Exception as exc:
            logger.warning(
                "Фоновое продление сессий ТехПортала завершилось ошибкой",
                extra={"event": "techportal.keepalive.unavailable", "fields": {"error": type(exc).__name__}},
            )

        try:
            await asyncio.wait_for(stop.wait(), timeout=SCAN_INTERVAL_SECONDS)
        except TimeoutError:
            pass
