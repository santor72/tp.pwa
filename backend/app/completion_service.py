import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.actors import Actor
from app.completion_repository import CompletionRepository
from app.errors import ApiError, ServiceUnavailableError, TechPortalCallError
from app.gis_client import GisClient
from app.object_storage import ObjectStorage
from app.tickets import TicketService


class ConnectionCompletionService:
    def __init__(self, repository: CompletionRepository, tickets: TicketService, storage: ObjectStorage, gis: GisClient) -> None:
        self._repository = repository
        self._tickets = tickets
        self._storage = storage
        self._gis = gis

    async def begin(self, actor: Actor, *, ticket_id: int, day: str, idempotency_key: UUID,
                    text: str, feature_id: UUID | None, photos: list[dict[str, Any]], technician_name: str) -> Any:
        await self._tickets.assert_connection_assigned(actor.techportal_user_id, day, ticket_id)
        feature_snapshot = await self._gis.feature(str(feature_id)) if feature_id else {}
        operation, created = await self._repository.create_or_get(
            idempotency_key=idempotency_key, ticket_id=ticket_id, day=day, user_id=actor.user_id,
            technician_external_id=actor.techportal_user_id, technician_name=technician_name,
            feature_id=feature_id, feature_snapshot=feature_snapshot, external_report_id=uuid4() if feature_id else None,
            report_text=text, photos=[], completion_status='prepared',
            gis_status='not_ready' if feature_id else 'not_requested',
        )
        if not created:
            if (operation.user_id != actor.user_id or operation.ticket_id != ticket_id or operation.day != day
                    or operation.report_text != text or operation.feature_id != feature_id):
                raise ApiError(409, 'COMPLETION_IDEMPOTENCY_CONFLICT', 'Этот ключ уже использован для другого отчёта')
            return operation
        uploaded = []
        try:
            for index, photo in enumerate(photos):
                key = f'completion/{operation.id}/{index + 1}-{uuid4().hex}'
                public_url = await self._storage.put(key, photo['content'], photo['content_type'])
                uploaded.append({'key': key, 'name': photo['name'], 'content_type': photo['content_type'],
                                 'size': len(photo['content']), 'sha256': hashlib.sha256(photo['content']).hexdigest(),
                                 'public_url': public_url})
        except ApiError as exc:
            await self._repository.update(operation.id, completion_status='completion_failed',
                                          last_error_code=exc.code, last_error_message=exc.message)
            raise
        comment = self._comment(text, uploaded)
        return await self._repository.update(operation.id, photos=uploaded, techportal_comment=comment, completion_status='marking')

    async def mark_techportal(self, operation_id: UUID) -> Any:
        operation = await self._repository.get(operation_id)
        if operation is None or operation.completion_status != 'marking':
            return operation
        try:
            await self._tickets.mark_connection_completed(operation.technician_external_id, operation.day,
                                                          operation.ticket_id, operation.techportal_comment or '')
        except (ServiceUnavailableError, TechPortalCallError) as exc:
            return await self._repository.update(operation_id, completion_status='completion_unknown',
                                                 next_attempt_at=datetime.now(UTC) + timedelta(minutes=1), lease_until=None,
                                                 last_error_code=exc.code, last_error_message=exc.message)
        except ApiError as exc:
            return await self._repository.update(operation_id, completion_status='completion_failed',
                                                 last_error_code=exc.code, last_error_message=exc.message)
        operation = await self._repository.update(operation_id, completion_status='completed',
                                                  last_error_code=None, last_error_message=None,
                                                  gis_status='pending' if operation.feature_id else 'not_requested',
                                                  next_attempt_at=datetime.now(UTC) if operation.feature_id else None)
        if operation.feature_id:
            return await self.send_gis(operation.id)
        return operation

    async def reconcile_techportal(self, operation_id: UUID) -> Any:
        operation = await self._repository.get(operation_id)
        if operation is None or operation.completion_status != 'completion_unknown':
            return operation
        try:
            recorded = await self._tickets.connection_completion_recorded(
                operation.technician_external_id, operation.ticket_id, operation.techportal_comment or '')
        except (ServiceUnavailableError, TechPortalCallError) as exc:
            return await self._repository.update(operation_id, lease_until=None,
                next_attempt_at=datetime.now(UTC) + timedelta(minutes=1), last_error_code=exc.code, last_error_message=exc.message)
        except ApiError as exc:
            return await self._repository.update(operation_id, completion_status='needs_review', lease_until=None,
                last_error_code=exc.code, last_error_message=exc.message)
        if not recorded:
            return await self._repository.update(operation_id, completion_status='needs_review', lease_until=None,
                last_error_code='TECHPORTAL_COMPLETION_UNCONFIRMED',
                last_error_message='Не удалось подтвердить запись комментария и тега в ТехПортале')
        operation = await self._repository.update(operation_id, completion_status='completed', lease_until=None,
            gis_status='pending' if operation.feature_id else 'not_requested',
            next_attempt_at=datetime.now(UTC) if operation.feature_id else None,
            last_error_code=None, last_error_message=None)
        return await self.send_gis(operation.id) if operation.feature_id else operation

    async def get_for_user(self, operation_id: UUID, user_id: UUID) -> Any:
        operation = await self._repository.get(operation_id)
        if operation is None:
            return None
        return operation if operation.user_id == user_id else None

    async def send_gis(self, operation_id: UUID) -> Any:
        operation = await self._repository.get(operation_id)
        if (operation is None or operation.completion_status != 'completed' or not operation.feature_id
                or operation.gis_status not in {'pending', 'retry_wait', 'sending', 'not_ready'}):
            return operation
        if operation.gis_status != 'sending':
            operation = await self._repository.update(operation_id, gis_status='sending',
                                                      lease_until=datetime.now(UTC) + timedelta(minutes=5))
        try:
            receipt = await self._gis.report_by_external_id(str(operation.external_report_id))
            if receipt is not None:
                return await self._mark_gis_delivered(operation_id, receipt)
            photos = [(str(photo['name']), await self._storage.get(str(photo['key'])), str(photo['content_type']))
                      for photo in operation.photos]
            receipt = await self._gis.create_report({
                'external_report_id': str(operation.external_report_id), 'ticket_id': operation.ticket_id,
                'completion_id': str(operation.id), 'feature_id': str(operation.feature_id),
                'technician': {'id': operation.technician_external_id, 'name': operation.technician_name},
                'occurred_at': operation.created_at.astimezone(UTC).isoformat().replace('+00:00', 'Z'),
                'text': operation.report_text,
            }, photos)
        except ServiceUnavailableError as exc:
            try:
                receipt = await self._gis.report_by_external_id(str(operation.external_report_id))
            except ServiceUnavailableError:
                receipt = None
            if receipt is not None:
                return await self._mark_gis_delivered(operation_id, receipt)
            return await self._repository.update(operation_id, gis_status='retry_wait',
                attempt_count=operation.attempt_count + 1, next_attempt_at=datetime.now(UTC) + timedelta(minutes=1),
                lease_until=None, last_error_code=exc.code, last_error_message=exc.message)
        except ApiError as exc:
            return await self._repository.update(operation_id, gis_status='needs_attention',
                lease_until=None, last_error_code=exc.code, last_error_message=exc.message)
        return await self._mark_gis_delivered(operation_id, receipt)

    async def _mark_gis_delivered(self, operation_id: UUID, receipt: dict[str, Any]) -> Any:
        return await self._repository.update(operation_id, gis_status='delivered', gis_report_id=str(receipt.get('id')),
                                             next_attempt_at=None, lease_until=None, last_error_code=None, last_error_message=None)

    @staticmethod
    def _comment(text: str, photos: list[dict[str, Any]]) -> str:
        return '\n'.join(part for part in [text.strip(), *(photo['public_url'] for photo in photos)] if part).strip()
