from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.auth_provider import TechPortalAuthProvider
from app.cache_store import CacheStore
from app.config import Settings
from app.database import create_engine, create_session_factory
from app.bitrix24_client import Bitrix24Client
from app.esb_client import EsbClient
from app.payment_addresses import PaymentAddressService
from app.payment_catalog import PaymentProductCatalog
from app.payment_client_resolver import PaymentClientResolver
from app.payment_status import PaymentStatusHandler
from app.payments import PaymentService
from app.messenger_links import MessengerLinkService
from app.repositories import PaymentRepository, SqlAlchemyMessengerRepository
from app.techportal_client import TechPortalClient
from app.tickets import TicketService


@dataclass(slots=True)
class ApplicationServices:
    engine: AsyncEngine
    sessions: async_sessionmaker
    auth_provider: TechPortalAuthProvider
    ticket_service: TicketService
    payment_addresses: PaymentAddressService
    payment_catalog: PaymentProductCatalog
    payment_repository: PaymentRepository
    payment_service: PaymentService
    payment_status: PaymentStatusHandler
    bitrix: Bitrix24Client
    messenger_links: MessengerLinkService

    async def close(self) -> None:
        await self.bitrix.close()
        await self.engine.dispose()


def create_application_services(settings: Settings, cache_redis: Redis) -> ApplicationServices:
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    cache = CacheStore(cache_redis)
    bitrix = Bitrix24Client(settings)
    payment_repository = PaymentRepository(sessions)
    payment_catalog = PaymentProductCatalog(settings, bitrix, cache)
    resolver = PaymentClientResolver(settings, bitrix)
    return ApplicationServices(
        engine=engine,
        sessions=sessions,
        auth_provider=TechPortalAuthProvider(settings),
        ticket_service=TicketService(settings, TechPortalClient(settings), cache),
        payment_addresses=PaymentAddressService(settings, EsbClient(settings), cache),
        payment_catalog=payment_catalog,
        payment_repository=payment_repository,
        payment_service=PaymentService(settings, payment_repository, payment_catalog, resolver, bitrix),
        payment_status=PaymentStatusHandler(settings, payment_repository, bitrix),
        bitrix=bitrix,
        messenger_links=MessengerLinkService(sessions, settings.tg_link_token_ttl_seconds, SqlAlchemyMessengerRepository()),
    )
