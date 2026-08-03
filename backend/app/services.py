from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.auth_provider import TechPortalAuthProvider
from app.cache_store import CacheStore
from app.config import Settings
from app.database import create_engine, create_session_factory
from app.domofon import DomofonService
from app.esb_client import EsbClient
from app.messenger_links import MessengerLinkService
from app.repositories import SqlAlchemyMessengerRepository
from app.techportal_client import TechPortalClient
from app.tickets import TicketService


@dataclass(slots=True)
class ApplicationServices:
    engine: AsyncEngine
    sessions: async_sessionmaker
    auth_provider: TechPortalAuthProvider
    ticket_service: TicketService
    domofon_service: DomofonService
    messenger_links: MessengerLinkService

    async def close(self) -> None:
        await self.engine.dispose()


def create_application_services(settings: Settings, cache_redis: Redis) -> ApplicationServices:
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    cache = CacheStore(cache_redis)
    return ApplicationServices(
        engine=engine,
        sessions=sessions,
        auth_provider=TechPortalAuthProvider(settings),
        ticket_service=TicketService(settings, TechPortalClient(settings), cache),
        domofon_service=DomofonService(settings, EsbClient(settings), cache),
        messenger_links=MessengerLinkService(sessions, settings.tg_link_token_ttl_seconds, SqlAlchemyMessengerRepository()),
    )
