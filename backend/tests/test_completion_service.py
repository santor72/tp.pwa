from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.actors import Actor
from app.completion_service import ConnectionCompletionService
from app.errors import ServiceUnavailableError


class FakeRepository:
    def __init__(self):
        self.rows = {}

    async def create_or_get(self, **values):
        row = SimpleNamespace(id=uuid4(), created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
                              gis_report_id=None, last_error_code=None, last_error_message=None,
                              attempt_count=0, next_attempt_at=None, **values)
        self.rows[row.id] = row
        return row, True

    async def get(self, operation_id):
        return self.rows.get(operation_id)

    async def update(self, operation_id, **values):
        row = self.rows[operation_id]
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(UTC)
        return row


class FakeTickets:
    def __init__(self):
        self.asserted = []
        self.marked = []
        self.recorded = True

    async def assert_connection_assigned(self, user_id, day, ticket_id):
        self.asserted.append((user_id, day, ticket_id))

    async def mark_connection_completed(self, user_id, day, ticket_id, comment):
        self.marked.append((user_id, day, ticket_id, comment))

    async def connection_completion_recorded(self, user_id, ticket_id, comment):
        return self.recorded


class FakeStorage:
    def __init__(self):
        self.objects = {}

    async def put(self, key, content, content_type):
        self.objects[key] = content
        return f'https://photos.example/{key}'

    async def get(self, key):
        return self.objects[key]


class FakeGis:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.feature_ids = []
        self.reports = []
        self.receipt = None

    async def feature(self, feature_id):
        self.feature_ids.append(feature_id)
        return {'id': feature_id, 'title': 'Муфта'}

    async def create_report(self, metadata, photos):
        self.reports.append((metadata, photos))
        if self.fail:
            raise ServiceUnavailableError('GIS временно недоступна')
        return {'id': 'gis-report'}

    async def report_by_external_id(self, external_report_id):
        return self.receipt


def service(*, gis=None):
    return ConnectionCompletionService(FakeRepository(), FakeTickets(), FakeStorage(), gis or FakeGis())


@pytest.mark.asyncio
async def test_connection_completion_adds_s3_links_to_techportal_comment_without_gis():
    subject = service()
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    text='  Подключили  ', feature_id=None, technician_name='Монтажник',
                                    photos=[{'name': 'work.jpg', 'content_type': 'image/jpeg', 'content': b'photo'}])
    operation = await subject.mark_techportal(operation.id)

    assert operation.completion_status == 'completed'
    assert operation.gis_status == 'not_requested'
    assert subject._tickets.marked == [('17', 'today', 12, f'Подключили\nhttps://photos.example/{operation.photos[0]["key"]}')]
    assert subject._gis.reports == []


@pytest.mark.asyncio
async def test_selected_feature_is_checked_and_sent_to_gis_after_techportal_mark():
    gis = FakeGis()
    subject = service(gis=gis)
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    feature_id = uuid4()
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    text='Подключили', feature_id=feature_id, technician_name='Монтажник', photos=[])
    operation = await subject.mark_techportal(operation.id)

    assert gis.feature_ids == [str(feature_id)]
    assert subject._tickets.marked
    assert operation.gis_status == 'delivered'
    metadata, photos = gis.reports[0]
    assert metadata['ticket_id'] == 12
    assert metadata['feature_id'] == str(feature_id)
    assert metadata['completion_id'] == str(operation.id)
    assert photos == []


@pytest.mark.asyncio
async def test_gis_temporary_failure_keeps_confirmed_completion_for_retry():
    subject = service(gis=FakeGis(fail=True))
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    text='Подключили', feature_id=uuid4(), technician_name='Монтажник', photos=[])
    operation = await subject.mark_techportal(operation.id)

    assert operation.completion_status == 'completed'
    assert operation.gis_status == 'retry_wait'
    assert operation.attempt_count == 1
    assert operation.next_attempt_at is not None


@pytest.mark.asyncio
async def test_existing_gis_receipt_is_used_without_creating_duplicate_report():
    gis = FakeGis()
    gis.receipt = {'id': 'already-delivered'}
    subject = service(gis=gis)
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    text='Подключили', feature_id=uuid4(), technician_name='Монтажник', photos=[])
    operation = await subject.mark_techportal(operation.id)

    assert operation.gis_status == 'delivered'
    assert operation.gis_report_id == 'already-delivered'
    assert gis.reports == []


@pytest.mark.asyncio
async def test_reconciliation_confirms_unknown_techportal_write_before_finishing_operation():
    subject = service()
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    text='Подключили', feature_id=None, technician_name='Монтажник', photos=[])
    await subject._repository.update(operation.id, completion_status='completion_unknown')

    operation = await subject.reconcile_techportal(operation.id)

    assert operation.completion_status == 'completed'
    assert operation.gis_status == 'not_requested'


@pytest.mark.asyncio
async def test_reconciliation_without_matching_techportal_comment_requires_review():
    subject = service()
    subject._tickets.recorded = False
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    text='Подключили', feature_id=None, technician_name='Монтажник', photos=[])
    await subject._repository.update(operation.id, completion_status='completion_unknown')

    operation = await subject.reconcile_techportal(operation.id)

    assert operation.completion_status == 'needs_review'
    assert operation.last_error_code == 'TECHPORTAL_COMPLETION_UNCONFIRMED'
