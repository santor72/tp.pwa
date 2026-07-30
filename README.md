# ТехПортал PWA

PWA для разъездных специалистов. Текущая версия реализует авторизацию через
ТехПортал, собственные Redis-сессии, экраны заявок «Сегодня» и «Завтра»,
структурированные аудит-логи и рабочие сценарии раздела «Домофоны» через ESB.

Документация реализации заявок «Сегодня» и «Завтра»:
[`docs/tickets.md`](docs/tickets.md).

## Запуск

1. Заполните `.env` на основе `.env.example`. Значения `TP_LOGIN` и
   `TP_PASSWORD` нужны только для ручной проверки авторизации; приложение
   получает учётные данные пользователя из формы входа. Для заявок обязательны
   `TP_BASE_URL` (полный базовый путь API, например `/api/ext`) и
   `TP_BASE_TOKEN`. Для Домофонов обязательны `ESB_BASE_URL` и
   `ESB_BASE_TOKEN`.
2. Запустите контейнеры:

   ```bash
   docker compose up --build
   ```

3. Откройте `http://localhost:8081`.

Frontend и API работают на одном origin. В Docker Compose параметр `Secure`
cookie отключён только для локального HTTP. В production он должен быть включён
и приложение должно работать за HTTPS.

## Сервисы

- `frontend` — Nginx со статической PWA и reverse proxy `/api`;
- `api` — FastAPI;
- `redis` — DB 0 для сессий, DB 1 для кэша;
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

Заявки фильтруются по ID исполнителя из серверной сессии. Даты фильтра
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
