# ТехПортал PWA

PWA для разъездных специалистов. Текущая версия реализует авторизацию через
ТехПортал, собственные Redis-сессии, экраны заявок «Сегодня» и «Завтра»,
структурированные аудит-логи и рабочие сценарии раздела «Домофоны» через ESB.

Инструкция для специалистов: [`docs/user-guide.md`](docs/user-guide.md) или
[`docs/user-guide.html`](docs/user-guide.html).

Документация реализации заявок «Сегодня» и «Завтра»:
[`docs/tickets.md`](docs/tickets.md).

Правила ролей, точечных прав и capabilities:
[`docs/permissions.md`](docs/permissions.md).

## Запуск

1. Заполните `.env` на основе `.env.example`. Значения `TP_LOGIN` и
   `TP_PASSWORD` нужны только для ручной проверки авторизации; приложение
   получает учётные данные пользователя из формы входа. Для заявок обязательны
   `TP_BASE_URL` (полный базовый путь API, например `/api/ext`) и
   `TP_BASE_TOKEN`. Для Домофонов обязательны `ESB_BASE_URL` и
   `ESB_BASE_TOKEN`. Для Telegram обязательны `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_BOT_USERNAME`, `TG_ACCESS_GROUPS` и `HTTPS_PROXY`. Long polling
   бота не запускается без `HTTPS_PROXY`; он применяется ко всем обращениям к
   Telegram API.
2. Запустите контейнеры:

   ```bash
   docker compose up --build
   ```

3. Откройте `http://localhost:8081`.

Frontend и API работают на одном origin. В Docker Compose параметр `Secure`
cookie отключён только для локального HTTP. В production он должен быть включён
и приложение должно работать за HTTPS.

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
- `api` — FastAPI;
- `telegram-bot` — единственный aiogram long-polling процесс без публичного порта;
- `postgres` — постоянные пользователи, привязки мессенджеров и одноразовые ссылки;
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
- `POST /api/domofon/connect`
- `GET /api/domofon/addresses`
- `POST /api/domofon/create`
- `GET /api/messenger-links`
- `POST /api/messenger-links/telegram`
- `DELETE /api/messenger-links/telegram`

Backend использует:

- `POST /tickets/get` относительно `TP_BASE_URL` для заявок на выбранную дату;
- `GET /techportal-user/list` для авторов комментариев;
- `POST /tickets/persist` для установки и снятия тега
  `Работы произведены`;
- `GET /locations` для адресов;
- `POST /add-domofon-to-user` для подключения услуги;
- `GET /flat-search` перед созданием пользователя;
- `POST /domofon-new-user` для создания пользователя.

Ответы ESB нормализуются, а внутренние поля квартиры и секреты не передаются
frontend. Без `ESB_BASE_URL` и `ESB_BASE_TOKEN` маршруты Домофона возвращают
`503 ESB_NOT_CONFIGURED`; успешные ответы не имитируются.

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
