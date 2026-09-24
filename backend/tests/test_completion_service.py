from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.actors import Actor
from app.completion_service import ConnectionCompletionService
from app.errors import ApiError, ServiceUnavailableError
from app.techportal_photo_delivery import PhotoSaveResult, TechPortalPhotoDelivery


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
        self.gis_context_calls = []

    async def assert_connection_assigned(self, user_id, day, ticket_id):
        self.asserted.append((user_id, day, ticket_id))

    async def assert_ticket_assigned(self, user_id, day, ticket_id, ticket_kind):
        self.asserted.append((user_id, day, ticket_id))

    async def mark_connection_completed(self, user_id, day, ticket_id, comment):
        self.marked.append((user_id, day, ticket_id, comment))

    async def mark_ticket_completed(self, user_id, day, ticket_id, comment, ticket_kind):
        self.marked.append((user_id, day, ticket_id, comment))

    async def connection_completion_recorded(self, user_id, ticket_id, comment):
        return self.recorded

    async def ticket_completion_recorded(self, user_id, ticket_id, comment, ticket_kind):
        return self.recorded

    async def gis_subscriber(self, user_id, ticket_id, ticket_kind):
        self.gis_context_calls.append((user_id, ticket_id, ticket_kind))
        return {'login': '35509398', 'address': 'СНТ Волга, дом 12, кв. 34'}


class FakeStorage:
    name = 's3'
    configured = True

    def __init__(self):
        self.objects = {}

    async def put(self, key, content, content_type):
        self.objects[key] = content
        return f'https://photos.example/{key}'

    async def save_photo(self, key, content, content_type, *, filename, context):
        url = await self.put(key, content, content_type)
        return PhotoSaveResult(comment_text=url, metadata={'key': key, 'public_url': url})

    async def stage_for_gis(self, key, content, content_type):
        self.objects[key] = content
        return {'key': key}

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
async def test_repair_requires_text_even_with_photo_before_creating_operation():
    subject = service()
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')

    with pytest.raises(ApiError) as error:
        await subject.begin(actor, ticket_id=12, ticket_kind='repair', day='today', idempotency_key=uuid4(),
                            techportal_text='', gis_text='', feature_id=None, technician_name='Иван',
                            photos=[{'name': 'work.jpg', 'content_type': 'image/jpeg', 'content': b'photo'}])

    assert error.value.code == 'REPAIR_COMMENT_REQUIRED'
    assert subject._repository.rows == {}


@pytest.mark.asyncio
async def test_repair_uses_same_photo_and_gis_delivery_as_connection():
    subject = service()
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, ticket_kind='repair', day='today', idempotency_key=uuid4(),
                                    techportal_text='Заменили кабель', gis_text='Заменили кабель', feature_id=uuid4(),
                                    technician_name='Иван', photos=[{'name': 'work.jpg', 'content_type': 'image/jpeg', 'content': b'photo'}])
    operation = await subject.mark_techportal(operation.id)

    assert operation.ticket_kind == 'repair'
    assert operation.completion_status == 'completed'
    assert operation.gis_status == 'delivered'
    assert subject._tickets.marked[0][3].startswith('Иван\nЗаменили кабель\nhttps://photos.example/')
    assert subject._gis.reports[0][1] == [('work.jpg', b'photo', 'image/jpeg')]


@pytest.mark.asyncio
async def test_connection_completion_adds_s3_links_to_techportal_comment_without_gis():
    subject = service()
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    techportal_text='  В ТехПортал  ', gis_text='В GIS', feature_id=None, technician_name='Иван', technician_last_name='Иванов',
                                    photos=[{'name': 'work.jpg', 'content_type': 'image/jpeg', 'content': b'photo'}])
    operation = await subject.mark_techportal(operation.id)

    assert operation.completion_status == 'completed'
    assert operation.gis_status == 'not_requested'
    assert subject._tickets.marked == [('17', 'today', 12, f'Иван Иванов\nВ ТехПортал\nhttps://photos.example/{operation.photos[0]["key"]}')]
    assert subject._gis.reports == []


@pytest.mark.asyncio
async def test_connection_completion_adds_text_from_each_configured_adapter():
    class ExtraAdapter:
        name = 'extra'
        configured = True

        async def save_photo(self, key, content, content_type, *, filename, context):
            return PhotoSaveResult(comment_text='Файл во втором хранилище')

    subject = service()
    subject._photo_delivery = TechPortalPhotoDelivery([subject._storage, ExtraAdapter()])
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    techportal_text='Работа выполнена', gis_text='', feature_id=None,
                                    technician_name='Иван', photos=[{
                                        'name': 'work.jpg', 'content_type': 'image/jpeg', 'content': b'photo',
                                    }])
    await subject.mark_techportal(operation.id)

    assert subject._tickets.marked[0][3] == (
        f'Иван\nРабота выполнена\nhttps://photos.example/{operation.photos[0]["key"]}'
        '\nФайл во втором хранилище'
    )
    assert [copy['adapter'] for copy in operation.photos[0]['copies']] == ['s3', 'extra']


@pytest.mark.asyncio
async def test_techportal_upload_adapter_adds_no_file_text_to_comment_and_does_not_store_cookies():
    class TicketUploadAdapter:
        name = 'techportal'
        configured = True

        async def save_photo(self, key, content, content_type, *, filename, context):
            assert context.ticket_id == 12
            assert context.upstream_cookies == {'tp-session': 'employee'}
            assert filename == 'work.jpg'
            return PhotoSaveResult(comment_text='')

    subject = service()
    subject._photo_delivery = TechPortalPhotoDelivery([TicketUploadAdapter()])
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    techportal_text='Работа выполнена', gis_text='', feature_id=None,
                                    technician_name='Иван', photos=[{
                                        'name': 'work.jpg', 'content_type': 'image/jpeg', 'content': b'photo',
                                    }], upstream_cookies={'tp-session': 'employee'})
    await subject.mark_techportal(operation.id)

    assert subject._tickets.marked[0][3] == 'Иван\nРабота выполнена'
    assert operation.photos[0]['copies'] == [{'adapter': 'techportal', 'comment_text': ''}]
    assert 'upstream_cookies' not in operation.__dict__


@pytest.mark.asyncio
async def test_techportal_photo_alone_can_complete_connection_without_report_text():
    class TicketUploadAdapter:
        name = 'techportal'
        configured = True

        async def save_photo(self, key, content, content_type, *, filename, context):
            return PhotoSaveResult(comment_text='')

    subject = service()
    subject._photo_delivery = TechPortalPhotoDelivery([TicketUploadAdapter()])
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    techportal_text='', gis_text='', feature_id=None,
                                    technician_name='Иван', photos=[{
                                        'name': 'work.jpg', 'content_type': 'image/jpeg', 'content': b'photo',
                                    }], upstream_cookies={'tp-session': 'employee'})
    operation = await subject.mark_techportal(operation.id)

    assert operation.completion_status == 'completed'


@pytest.mark.asyncio
async def test_connection_can_complete_without_report_text_or_photos():
    subject = service()
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')

    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    techportal_text='', gis_text='', feature_id=None,
                                    technician_name='Иван', photos=[])
    operation = await subject.mark_techportal(operation.id)

    assert operation.completion_status == 'completed'
    assert subject._tickets.marked[0][3] == 'Иван'
    assert subject._tickets.marked[0][3] == 'Иван'


@pytest.mark.asyncio
async def test_gis_photos_require_the_s3_adapter_even_when_another_adapter_is_available():
    class ExtraAdapter:
        name = 'extra'
        configured = True

        async def save_photo(self, key, content, content_type, *, filename, context):
            return PhotoSaveResult(comment_text='Фото сохранено')

    subject = service()
    subject._storage.configured = False
    subject._photo_delivery = TechPortalPhotoDelivery([subject._storage, ExtraAdapter()])
    assert subject.photos_available is True
    assert subject.gis_photos_available is False
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')

    with pytest.raises(ApiError) as error:
        await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                            techportal_text='ТП', gis_text='GIS', feature_id=uuid4(), technician_name='Иван',
                            photos=[{'name': 'work.jpg', 'content_type': 'image/jpeg', 'content': b'photo'}])

    assert error.value.code == 'GIS_PHOTO_STORAGE_NOT_CONFIGURED'
    assert subject._repository.rows == {}


@pytest.mark.asyncio
async def test_gis_reads_s3_copy_when_another_adapter_writes_first():
    class ExtraAdapter:
        name = 'extra'
        configured = True

        async def save_photo(self, key, content, content_type, *, filename, context):
            return PhotoSaveResult(comment_text='Другая ссылка', metadata={'key': 'extra-key'})

    subject = service()
    subject._photo_delivery = TechPortalPhotoDelivery([ExtraAdapter(), subject._storage])
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    techportal_text='ТП', gis_text='GIS', feature_id=uuid4(), technician_name='Иван',
                                    photos=[{'name': 'work.jpg', 'content_type': 'image/jpeg', 'content': b'photo'}])
    operation = await subject.mark_techportal(operation.id)

    assert operation.gis_status == 'delivered'
    assert subject._gis.reports[0][1] == [('work.jpg', b'photo', 'image/jpeg')]


@pytest.mark.asyncio
async def test_gis_only_s3_stores_photo_without_adding_link_to_techportal_comment():
    subject = service()
    subject._storage.techportal_enabled = False
    assert subject.photos_available is False
    assert subject.gis_photos_available is True
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    techportal_text='Подключили', gis_text='', feature_id=uuid4(),
                                    technician_name='Иван', photos=[{
                                        'name': 'work.jpg', 'content_type': 'image/jpeg', 'content': b'photo',
                                    }])
    operation = await subject.mark_techportal(operation.id)

    assert operation.gis_status == 'delivered'
    assert subject._tickets.marked[0][3] == 'Иван\nПодключили'
    assert operation.photos[0]['copies'] == [{
        'adapter': 's3', 'comment_text': '', 'key': operation.photos[0]['copies'][0]['key'],
    }]
    assert subject._gis.reports[0][1] == [('work.jpg', b'photo', 'image/jpeg')]


@pytest.mark.asyncio
async def test_gis_only_s3_stages_photo_after_other_techportal_adapter():
    class ExtraAdapter:
        name = 'extra'
        configured = True

        async def save_photo(self, key, content, content_type, *, filename, context):
            return PhotoSaveResult(comment_text='Ссылка другого хранилища', metadata={'key': 'extra-key'})

    subject = service()
    subject._storage.techportal_enabled = False
    subject._photo_delivery = TechPortalPhotoDelivery([subject._storage, ExtraAdapter()])
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    techportal_text='', gis_text='', feature_id=uuid4(), technician_name='Иван',
                                    photos=[{'name': 'work.jpg', 'content_type': 'image/jpeg', 'content': b'photo'}])
    operation = await subject.mark_techportal(operation.id)

    assert operation.gis_status == 'delivered'
    assert subject._tickets.marked[0][3] == 'Иван\nСсылка другого хранилища'
    assert [copy['adapter'] for copy in operation.photos[0]['copies']] == ['extra', 's3']
    assert subject._gis.reports[0][1] == [('work.jpg', b'photo', 'image/jpeg')]


@pytest.mark.asyncio
async def test_gis_only_s3_can_complete_connection_without_techportal_report():
    subject = service()
    subject._storage.techportal_enabled = False
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')

    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    techportal_text='', gis_text='GIS', feature_id=uuid4(), technician_name='Иван',
                                    photos=[{'name': 'work.jpg', 'content_type': 'image/jpeg', 'content': b'photo'}])

    assert operation.completion_status == 'marking'


@pytest.mark.asyncio
async def test_selected_feature_is_checked_and_sent_to_gis_after_techportal_mark():
    gis = FakeGis()
    subject = service(gis=gis)
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    feature_id = uuid4()
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    techportal_text='ТП', gis_text='GIS', feature_id=feature_id, technician_name='Монтажник', photos=[])
    operation = await subject.mark_techportal(operation.id)

    assert gis.feature_ids == [str(feature_id)]
    assert subject._tickets.marked
    assert operation.gis_status == 'delivered'
    metadata, photos = gis.reports[0]
    assert metadata['ticket_id'] == 12
    assert metadata['feature_id'] == str(feature_id)
    assert metadata['completion_id'] == str(operation.id)
    assert metadata['text'] == 'GIS'
    assert metadata['subscriber'] == {'login': '35509398', 'address': 'СНТ Волга, дом 12, кв. 34'}
    assert subject._tickets.gis_context_calls == [('17', 12, 'connection')]
    assert metadata['technician'] == {'id': '17', 'name': 'Монтажник', 'last_name': ''}
    assert subject._tickets.marked[0][3] == 'Монтажник\nТП'
    assert photos == []


@pytest.mark.asyncio
async def test_photos_allow_empty_techportal_text_and_gis_text_without_object():
    subject = service()
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    techportal_text='', gis_text='', feature_id=None, technician_name='Монтажник',
                                    photos=[{'name': 'work.jpg', 'content_type': 'image/jpeg', 'content': b'photo'}])
    operation = await subject.mark_techportal(operation.id)

    assert operation.completion_status == 'completed'
    assert subject._tickets.marked[0][3].startswith('Монтажник\nhttps://photos.example/')


@pytest.mark.asyncio
async def test_gis_temporary_failure_keeps_confirmed_completion_for_retry():
    subject = service(gis=FakeGis(fail=True))
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    techportal_text='ТП', gis_text='GIS', feature_id=uuid4(), technician_name='Монтажник', photos=[])
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
                                    techportal_text='ТП', gis_text='GIS', feature_id=uuid4(), technician_name='Монтажник', photos=[])
    operation = await subject.mark_techportal(operation.id)

    assert operation.gis_status == 'delivered'
    assert operation.gis_report_id == 'already-delivered'
    assert gis.reports == []


@pytest.mark.asyncio
async def test_reconciliation_confirms_unknown_techportal_write_before_finishing_operation():
    subject = service()
    actor = Actor(user_id=uuid4(), techportal_user_id='17', channel='pwa')
    operation = await subject.begin(actor, ticket_id=12, day='today', idempotency_key=uuid4(),
                                    techportal_text='ТП', gis_text='', feature_id=None, technician_name='Монтажник', photos=[])
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
                                    techportal_text='ТП', gis_text='', feature_id=None, technician_name='Монтажник', photos=[])
    await subject._repository.update(operation.id, completion_status='completion_unknown')

    operation = await subject.reconcile_techportal(operation.id)

    assert operation.completion_status == 'needs_review'
    assert operation.last_error_code == 'TECHPORTAL_COMPLETION_UNCONFIRMED'
