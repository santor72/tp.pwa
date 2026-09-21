# ТехПортал PWA

PWA для разъездных специалистов. Текущая версия реализует авторизацию через
ТехПортал, собственные Redis-сессии, экраны заявок «Сегодня» и «Завтра»,
структурированные аудит-логи и платёжный терминал на базе Bitrix24.

Инструкция для специалистов: [`docs/user-guide.md`](docs/user-guide.md) или
[`docs/user-guide.html`](docs/user-guide.html).

Документация реализации заявок «Сегодня» и «Завтра»:
[`docs/tickets.md`](docs/tickets.md).

Правила ролей, точечных прав и capabilities:
[`docs/permissions.md`](docs/permissions.md).

Интеграция с Точка GIS (запланирована):
[архитектура](docs/gis-architecture.md),
[поэтапный план разработки](docs/gis-implementation-plan.md),
[описание PR для GIS](docs/gis-pr-tochka-gis.md).

Просмотр прежних фотоотчётов и истории объекта GIS (отдельное расширение карточки):
[архитектура](docs/gis-object-history-architecture.md),
[план реализации](docs/gis-object-history-implementation-plan.md).

Событийная обработка платежей (в разработке):
[план](docs/payment-events-outbox-plan.md),
[переключение, масштабирование и откат](docs/payment-events-runbook.md),
[отчёт нагрузочных проверок](docs/payment-events-load-report.md).
До завершения итоговой проверки не переключайте рабочий сервер на `events`.

## Запуск

1. Заполните `.env` на основе `.env.example`. Значения `TP_LOGIN` и
   `TP_PASSWORD` нужны только для ручной проверки авторизации; приложение
   получает учётные данные пользователя из формы входа. Для заявок обязательны
   `TP_BASE_URL` (полный базовый путь API, например `/api/ext`) и
   `TP_BASE_TOKEN`. Справочник адресов использует `ESB_BASE_URL` и
   `ESB_BASE_TOKEN`. Для платежей обязательны `BX24_WEBHOOK`, непустой
   `BX24_PAYMENT_PRODUCTS`, активная платёжная система Bitrix24 и настройки
   суммы из `.env.example`. Для Telegram обязательны `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_BOT_USERNAME`, `TG_ACCESS_GROUPS` и `HTTPS_PROXY`. Long polling
   бота не запускается без `HTTPS_PROXY`; он применяется ко всем обращениям к
   Telegram API.
2. Для совместимого worker должны быть заданы `COMPOSE_PROFILES=legacy` и
   `PAYMENT_PROCESSING_MODE=legacy` (как в `.env.example`). Если `.env` создан
   раньше этих настроек, добавьте их: сервис worker теперь выбирается профилем.
   Не включайте одновременно профили `legacy` и `events` при запуске приложения.
   Запустите контейнеры:

   ```bash
   docker compose up --build
   ```

3. Откройте `http://localhost:8081`. Реестр оплат администратора доступен на
   `http://localhost:8082/admin/`.

Для локальной интеграции с GIS сначала запустите GIS, затем явно подключите
его Compose-конфигурацию:

```bash
(cd tochka-gis/current && docker compose up -d --build gis)
docker compose -f compose.yaml -f compose.gis-local.yaml up -d --build
```

Frontend и API работают на одном origin. В Docker Compose параметр `Secure`
cookie отключён только для локального HTTP. В production он должен быть включён
и приложение должно работать за HTTPS.

Для фотоотчётов заполните `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`,
`S3_BUCKET` и `S3_PUBLIC_BASE_URL`. `S3_ENDPOINT_URL` может указывать на
внутренний HTTP либо доступный API внешний HTTPS S3 endpoint. Compose запускает SeaweedFS, создаёт bucket
из `S3_BUCKET` и открывает его S3 API только на `127.0.0.1:8333` (порт можно
изменить через `SEAWEEDFS_S3_PORT`). Внешний nginx должен проксировать этот
порт на HTTPS-домен из `S3_PUBLIC_BASE_URL`; URL должен включать bucket, например
`https://s2.svc.point.online/techportal-reports`.

`GIS_VISIBLE_TECHPORTAL_ROLES` ограничивает карту и выбор объекта GIS списком
значений роли ТехПортала через запятую, например `admin,manager,user`. Пустое
значение отключает GIS-интерфейс для всех пользователей.

При незаполненной S3-конфигурации форма закрытия подключения не показывает
загрузку фотографий и требует текстовый отчёт. Это не влияет на фотографии
самостоятельных отчётов с карты: они передаются напрямую в GIS.

## Production

Создайте production override и укажите в нём публичный HTTPS origin:

```bash
cp compose.prod.yaml.sample compose.prod.yaml
```

Затем запустите приложение:

```bash
docker compose \
  -f compose.yaml \
  -f compose.prod.yaml \
  up -d --build
```

Проверка состояния и health-check:

```bash
docker compose -f compose.yaml -f compose.prod.yaml ps
curl http://127.0.0.1:8081/health/live
curl http://127.0.0.1:8081/health/ready
```

Оба health-check должны вернуть `{"status":"ok"}`. Порт `8081` доступен только
на loopback-интерфейсе; публичный HTTPS reverse proxy должен перенаправлять
запросы на `127.0.0.1:8081`.

## Сервисы

- `frontend` — Nginx со статической PWA и reverse proxy `/api`;
- `admin-frontend` — отдельная read-only админка реестра оплат; локально порт
  `8082`, базовый путь задаётся `ADMIN_BASE_PATH`;
- `api` — FastAPI;
- `telegram-bot` — единственный aiogram long-polling процесс без публичного порта;
- `payment-worker` — продолжение платёжных операций и резервная проверка статуса;
- `postgres` — пользователи, привязки мессенджеров и платёжные транзакции;
- `seaweedfs` — S3-совместимое хранилище фотоотчётов с постоянным Docker volume;
- `seaweedfs-init` — одноразово создаёт bucket, заданный в `S3_BUCKET`;
- `migrate` — одноразово выполняет Alembic-миграции до запуска API и бота;
- `redis` — DB 0 для сессий, DB 1 для кэша, DB 2 для Telegram FSM/access;
- `vector` — читает Docker logs и выводит JSON в console sink.

## API

- `POST /api/auth/login`
- `GET /api/auth/session`
- `POST /api/auth/logout`
- `GET /api/tickets/today`
- `GET /api/tickets/tomorrow`
- `POST /api/tickets/{ticket_id}/completion`
- `GET /api/payments/addresses`
- `GET /api/payments/products`
- `POST /api/payments`
- `GET /api/payments/{transaction_id}`
- `POST /api/payments/{transaction_id}/client-selection`
- `POST /api/payments/{transaction_id}/resend`
- `GET /api/payments/recent`
- `GET /api/admin/payments`
- `GET /api/admin/payments/{transaction_id}`
- `POST /api/webhooks/bitrix24/payments`
- `GET /api/messenger-links`
- `POST /api/messenger-links/telegram`
- `DELETE /api/messenger-links/telegram`

Backend использует:

- `POST /tickets/get` относительно `TP_BASE_URL` для заявок на выбранную дату;
- `GET /techportal-user/list` для авторов комментариев;
- `POST /tickets/persist` для установки и снятия тега
  `Работы произведены`;
- `GET /locations` в ESB для справочника адресов;
- REST Bitrix24 для каталога, контактов/лидов, CRM-счетов, товаров, платежей,
  публичной ссылки и timeline.

Платёж не связан с заявкой. Frontend создаёт ключ идемпотентности один раз,
backend сохраняет каждый подтверждённый внешний шаг в PostgreSQL, а worker
продолжает операцию после сбоев. Источник статуса `paid` — повторное чтение
платежа Bitrix24 после webhook `OnPaymentEntitySaved` либо резервный polling.
Полные телефоны, webhook и платёжные ссылки не журналируются.

Заявки по умолчанию фильтруются по ID исполнителя из серверной сессии. Режим
`?scope=all` доступен только пользователям с соответствующей capability. Даты фильтра
вычисляются в `Europe/Moscow`, а UTC-время ТехПортала преобразуется в московское.
Справочник имён авторов комментариев кэшируется в Redis.

## Проверки

```bash
docker compose up --build
curl http://localhost:8081/health/live
curl http://localhost:8081/health/ready
```

Тесты backend:

```bash
docker build -f backend/Dockerfile -t techportal-api .
docker run --rm -v "$PWD/backend:/app" -w /app techportal-api \
  sh -c "pip install -r requirements-dev.txt && pytest"
```

Тесты пошагового frontend:

```bash
docker build --target build -f frontend/Dockerfile -t techportal-frontend-test .
docker run --rm techportal-frontend-test pnpm test
```

## Telegram runbook

- При ротации `TELEGRAM_BOT_TOKEN` замените значение в `.env` и выполните
  `docker compose up -d --force-recreate telegram-bot`. Токен не должен
  попадать в compose override, image или логи.
- При изменении `TG_ACCESS_GROUPS` добавьте бота в новые группы с правами
  администратора, обновите `.env` и перезапустите только `telegram-bot`.
  Ключ кэша содержит хэш списка групп, поэтому прежние access-решения не
  используются.
- Для отзыва связи специалист использует «Отключить» в PWA. Это немедленно
  блокирует следующий Telegram update; состояние FSM/access cache можно
  безопасно очистить вместе с Redis DB 2.
- Для отката миграции сначала остановите API и bot, затем выполните
  `docker compose run --rm migrate alembic -c alembic.ini downgrade -1`.
  Перед откатом production-БД создайте резервную копию.

## Платёжный runbook

- При ротации `BX24_WEBHOOK` замените значение в `.env` и пересоздайте `api` и
  `payment-worker`; старое значение отзовите в Bitrix24.
- Зависшую операцию безопасно продолжает worker с последнего сохранённого шага.
  Повторный пользовательский `POST` должен содержать исходный
  `idempotency_key`; новый ключ означает новую оплату.
- До production проверьте активную платёжную систему, онлайн-кассу, поле
  `BX24_PAYMENT_LINK_FIELD`, робот по `BX24_PAYMENT_SEND_TRIGGER` и внешний
  webhook с секретом `BX24_PAYMENT_WEBHOOK_TOKEN`.
- Если SMS не настроено, операция остаётся в `send_failed`, а короткая ссылка и
  QR доступны сотруднику; успешная отправка не симулируется.
- Комментарий о сформированной оплате остаётся в таймлайне Контакта и Лида.
  Создание отдельного CRM activity выключено по умолчанию
  (`BX24_PAYMENT_CREATE_ACTIVITY=false`); включайте его только если портал
  принимает этот тип CRM-дела.
- Для события `OnPaymentEntitySaved` зарегистрируйте исходящий WEBHOOK в Bitrix24 
  endpoint  - <адрес приложения>`/api/webhooks/bitrix24/payments`.
  полученный токен авторизации нужно записать в `BX24_PAYMENT_WEBHOOK_TOKEN` ;
  polling остаётся резервным  способом подтверждения оплаты.
- После отправки SMS робот должен перевести счёт со стадии
  `BX24_PAYMENT_SEND_TRIGGER` на следующую стадию. Иначе «Отправить повторно»
  может не вызвать робота повторно.
- Окончательно неуспешную операцию может возобновить только администратор через
  `POST /api/admin/payments/{transaction_id}/resume`; действие сохраняется в
  журнале операций.
- Настройте внешний мониторинг `/health/ready` и alert по событиям логов
  `payment.worker.failed`, `BX24_RATE_LIMITED` и операциям в статусе `failed`.
  Платёжные ссылки, телефоны и webhook-секреты не должны передаваться в alert.

### Диагностика скорости оплаты

Сбор замеров по умолчанию выключен: `PAYMENT_TELEMETRY_ENABLED=false`.
Для включения задайте `true` в `.env`, пересоздайте `api` и `payment-worker`
и обновите страницу приложения. Сохранённая история доступна и при выключенном сборе.

В админке доступна вкладка «Скорость формирования»: очередь, этапы Битрикс,
повторы запросов и время до загрузки QR на телефоне. Включает операции всех
статусов; фильтры работают по дате создания, телефону и сотруднику.
Перед обновлением сервисов примените миграцию `20260911_01` (`alembic upgrade head`).
Подробности замеров, ограничения и порядок развёртывания:
[Диагностика скорости формирования оплаты](docs/payment-timing-diagnostics.md).
