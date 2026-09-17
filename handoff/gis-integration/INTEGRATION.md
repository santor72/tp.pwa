# Состав и подключение

Исходная версия в локальном package.json: `0.2.20-pilot.20260914`.
Точные SHA-256 исходного архива и файлов записаны в MANIFEST.json.

`files/` содержит новые файлы с путями относительно корня GIS:

- integration-routes.ts — чтение, приём отчёта и сверка квитанции;
- integration-openapi.ts — текущий документ OpenAPI;
- lib/integration-config.ts — токен, клиент и разрешённые карты/геометрии;
- tests/integration-config.test.ts — тесты конфигурации;
- prisma/migrations/20260916090000_integration_api/migration.sql — квитанции.

`baseline/` и `reference/` содержат только server.ts, prisma/schema.prisma и
lib/db.ts. Они нужны для трёхстороннего сравнения; не копируй целиком server.ts
или схему поверх актуального проекта.

## Изменения существующих файлов

В server.ts импортировать `installIntegration` и вызвать после `installReports`.
Для `/integration/v1/*` разрешить серверный запрос без браузерного Origin при
корректном Host, сохранив обязательную Bearer-проверку самого маршрута.
Предложенный patch использует наличие Authorization лишь для исключения
из Origin-проверки — это не авторизация. Учитывай актуальную proxy/Host-модель GIS.
Исключение не должно распространяться на обычные `/api/*`.

В prisma/schema.prisma добавить IntegrationReportReceipt и обратную связь
FeatureHistoryEntry.integration_receipt. Уникальность `(client_id, external_report_id)`
и внешний ключ на feature_history_entries согласовать с текущей схемой.
При совпадении имени миграции сначала сравнить её содержимое.

В lib/db.ts заменить URL.pathname на fileURLToPath для файловых путей `.env`
и `.data`: это исправляет каталоги с кириллицей. Не менять pathname URL базы.
Если проблема уже исправлена upstream, дополнительная правка не требуется.

## Настройки

```dotenv
GIS_INTEGRATION_CLIENT_ID=tp-pwa
GIS_INTEGRATION_TOKEN_SHA256=<64 hex символа SHA-256 случайного секрета>
GIS_INTEGRATION_MAP_IDS=<UUID опубликованных карт через запятую>
GIS_INTEGRATION_REPORT_GEOMETRY_TYPES=Point,LineString
GIS_INTEGRATION_PHOTO_RETENTION_DAYS=365
```

В production обязательны хеш токена и непустой список карт. Секрет передаётся
владельцу backend PWA отдельно. GIS_INTEGRATION_TOKEN (исходный секрет)
предназначен для локальных тестов. `client_id` нельзя менять при ротации
токена: это пространство имён квитанций и защиты от дублей.

Чтение: maps, maps/{id}/layers, bounds, features?bbox=west,south,east,north&layers=…,
search?q=…, features/{id}, assets/{id}. Все пути под `/integration/v1`.
Сверка: `GET /integration/v1/reports/by-external-id/{external_report_id}`.

Создание: `POST /integration/v1/reports`, multipart: поле `metadata` — JSON-текст,
до пяти повторяющихся полей `photos`. Пример metadata (только тестовые UUID):

```json
{
  "external_report_id": "11111111-1111-4111-8111-111111111111",
  "ticket_id": 123,
  "completion_id": "22222222-2222-4222-8222-222222222222",
  "feature_id": "33333333-3333-4333-8333-333333333333",
  "technician": {"id": "installer-123", "name": "Тестовый монтажник"},
  "occurred_at": "2026-09-16T12:00:00Z",
  "text": "Тестовый отчёт подключения"
}
```

Лимиты: JPEG/PNG/WebP по содержимому, 10 MiB на файл; до 16384 пикселей
на сторону и 50 млн пикселей; нужен текст или фото. Текст до 10000 символов.
Порядок фотографий влияет на fingerprint. Повтор передаёт прежние метаданные,
время и те же байты фото. Успех означает сохранение GIS, не доставку Telegram.

`retention_until` фиксирует минимум хранения (365 дней по умолчанию), а не
выполняет резервное копирование или очистку. Нужен постоянный фотоархив GIS.
Новая миграция для этого поля не нужна: оно записывается в attachments.
