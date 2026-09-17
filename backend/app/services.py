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
from app.payment_event_repository import PaymentEventRepository
from app.payment_limiter import PaymentRequestLimiter
from app.payments import PaymentService
from app.messenger_links import MessengerLinkService
from app.repositories import PaymentRepository, SqlAlchemyMessengerRepository
from app.techportal_client import TechPortalClient
from app.tickets import TicketService
from app.gis_client import GisClient
from app.object_storage import ObjectStorage
from app.completion_repository import CompletionRepository
from app.completion_service import ConnectionCompletionService


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
    gis_client: GisClient
    completion_repository: CompletionRepository
    connection_completion_service: ConnectionCompletionService

    async def close(self) -> None:
        await self.bitrix.close()
        await self.gis_client.close()
        await self.engine.dispose()


def create_application_services(settings: Settings, cache_redis: Redis) -> ApplicationServices:
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    cache = CacheStore(cache_redis)
    bitrix = Bitrix24Client(settings)
    events = PaymentEventRepository(sessions, expected_mode=settings.payment_processing_mode)
    payment_repository = PaymentRepository(sessions, events=events)
    bitrix.limiter = PaymentRequestLimiter(events, settings)
    payment_catalog = PaymentProductCatalog(settings, bitrix, cache)
    resolver = PaymentClientResolver(settings, bitrix)
    ticket_service = TicketService(settings, TechPortalClient(settings), cache)
    gis_client = GisClient(settings)
    completion_repository = CompletionRepository(sessions)
    return ApplicationServices(
        engine=engine,
        sessions=sessions,
        auth_provider=TechPortalAuthProvider(settings),
        ticket_service=ticket_service,
        payment_addresses=PaymentAddressService(settings, EsbClient(settings), cache),
        payment_catalog=payment_catalog,
        payment_repository=payment_repository,
        payment_service=PaymentService(settings, payment_repository, payment_catalog, resolver, bitrix),
        payment_status=PaymentStatusHandler(settings, payment_repository, bitrix),
        bitrix=bitrix,
        messenger_links=MessengerLinkService(sessions, settings.tg_link_token_ttl_seconds, SqlAlchemyMessengerRepository()),
        gis_client=gis_client,
        completion_repository=completion_repository,
        connection_completion_service=ConnectionCompletionService(completion_repository, ticket_service, ObjectStorage(settings), gis_client),
    )
