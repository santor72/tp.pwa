# Compose runtime smoke — 2026-09-12

## Дополнение: истечение grace period во время CRM write

Проверка выполнена на том же обновлённом образе, что и SIGTERM-сценарий ниже
(`cf3c7f6a3074286a721bbb1daeb066a90d4e9708961c86e95093a7c5e8bb2888`).
К трём тестовым Compose-файлам добавлен `compose.payment-shutdown-test.yaml`:
grace 2 с, lease 6 с, heartbeat 1 с, reclaim idle 1 с только у formation worker.
Значения продуктивной конфигурации не менялись.

`runtime_shutdown_probe.py prepare` создал две оплаты и дождался сохранения
счёта на fake при удерживаемом HTTP-ответе. После SIGTERM ответ **не** освобождали:
`docker wait` подтвердил самостоятельный выход 0 по истечении grace. Проверка
`verify-interrupted` прошла: задания running/ready, одна in_flight запись
crm.item.add в журнале, один удалённый счёт, ни платежей, ни QR.

Затем выполнены release и запуск formation worker новым процессом.
`verify-recovered` прошла: два completed задания, два QR, ровно по два счёта,
товарных строки, платежа, связи товара, комментария и активности; неизвестных
write-записей не осталось. Redis XPENDING formation-workers-v1 = 0.
Так проверено восстановление после локальной отмены HTTP-корутины без повторного
создания уже сохранённого счёта. Точная wall-clock граница восстановления здесь
не измерялась; отдельный процессный SIGKILL-тест проверяет свой временной gate.

После проверки удалён только `tp-payment-runtime-test` со всеми четырьмя
Compose-файлами; его две синтетические оплаты в tmpfs потеряны намеренно.
Рабочие данные и контейнеры приложения не затронуты.

Проверен отдельный проект `tp-payment-runtime-test`, не Compose приложения.
Файлы: `compose.payment-events-test.yaml` + `compose.payment-runtime-test.yaml`.
Образ собран из текущего рабочего дерева через штатный backend/Dockerfile:
`sha256:17b6e6bf001da8e65060cfaf6448bf0b4e42283326164aa99a28e85061088be2`.
В overlay нет подстановок `.env`: тестовые адреса/пароли заданы явно,
BX24_WEBHOOK пустой, сеть `internal=true` (проверено через Docker inspect).

## Что выполнено

1. Сборка `tp-payment-runtime-test:local`.
2. `up --wait`: PostgreSQL/Redis healthy, Alembic upgrade head и initialize
   set-mode events завершились успешно. API, relay, formation, reconciliation
   и обе recovery-роли получили healthy через штатные проверки приложения.
3. Formation увеличен до 2 реплик при одной reconciliation. В таблице
   payment_runtime_members получены ровно 2/1 живых записи этих ролей.
4. Formation увеличен до 4, reconciliation — до 2. Все контейнеры healthy;
   PostgreSQL подтвердил 4/2, по одной API/relay/recovery-formation/
   recovery-reconciliation. Это реальные контейнеры, не asyncio-задачи.
5. Штатный `stop -t 40` API и обработчиков. После остановки число строк
   payment_runtime_members = 0, активных leases = 0 (не только истёкший heartbeat).
6. Проект удалён через `down`, без `-v`. PostgreSQL этого проекта использовал
   tmpfs; тестовая схема потеряна вместе с контейнером, операций оплаты было 0.
   Образ оставлен для следующих тестов. Проект `tp-payment-events-test` и
   рабочие контейнеры приложения не останавливались.

## Воспроизведение

Из корня репозитория, только с указанным тестовым project name:

```bash
docker compose --env-file /dev/null -p tp-payment-runtime-test -f compose.payment-events-test.yaml -f compose.payment-runtime-test.yaml build api
docker compose --env-file /dev/null -p tp-payment-runtime-test -f compose.payment-events-test.yaml -f compose.payment-runtime-test.yaml up -d --no-build --wait --wait-timeout 90
docker compose --env-file /dev/null -p tp-payment-runtime-test -f compose.payment-events-test.yaml -f compose.payment-runtime-test.yaml up -d --no-deps --wait --scale payment-formation-worker=4 --scale payment-reconciliation-worker=2 payment-formation-worker payment-reconciliation-worker
```

По завершении сначала остановить API и пять worker-сервисов с `stop -t 40`,
проверить реестр живых процессов, затем выполнить `down` для **этого же**
тестового проекта с **этими же двумя** Compose-файлами. Не подставлять project
name приложения или основной compose.yaml.

## Что этот smoke не доказывает

Платежи не создавались: проверены сборка, миграции, mode initialization,
подключения, heartbeat, масштабирование и штатное завершение без нагрузки.
Полный PaymentService → HTTP fake Битрикс → reconciliation в контейнерах,
callback/API-команды, отключение Redis и обратное переключение под нагрузкой
требуют отдельной проверки. Нагрузочные результаты с отдельными процессами
описаны в payment-events-load-report.md и не выдаются за результаты этого smoke.

## Дополнительный HTTP workflow smoke (2026-09-12)

Поверх двух тестовых Compose-файлов добавлен
`compose.payment-workflow-test.yaml`: stateful HTTP fake Битрикса в той же
изолированной сети, без выхода на реальный портал. Использован тот же образ,
две реплики formation и две reconciliation, relay и обе recovery-роли.
Все сервисы перед проверкой healthy.

`backend/tests/runtime_workflow_probe.py` запущен отдельным одноразовым контейнером
в сети `tp-payment-runtime-test_default` с флагом `--compose-network`. Флаг
разрешает исключительно имена postgres/api/fake-bitrix, база строго
payment_events_test. Это позволяет проверять internal-сеть без публикации портов.

Результаты двух последовательных запусков:

| Условия | Оплаты | Результат | HTTP-вызовы fake |
|---|---:|---|---:|
| Redis работает | 2 | paid, все formation/paid комментарии и активности | 45 |
| Контейнер Redis остановлен | 2 | paid через PostgreSQL fallback, те же последействия | 44 |

В каждом запуске появились ровно 2 счёта, 2 товарные строки, 2 платежа,
2 привязки товара, 4 комментария и 4 активности. QR и ссылка проверены до
подтверждения оплаты. Callback отправлен по HTTP в настоящий API-контейнер,
ответ 202. После двух запусков PostgreSQL подтвердил 4 операции paid.
Число HTTP-вызовов зависит от фоновых сверок, это не фиксированный benchmark.
Дополнительно unit/in-process проверка fake и настоящих сервисов:
`PYTHONPATH=. .venv/bin/pytest -q tests/test_runtime_fake_bitrix.py` — 1 passed.

Ограничения: начальные задания создаются через PaymentEventRepository прямо
в PostgreSQL, не через авторизованный POST /api/payments. Проверена ветка
существующего контакта без email и адреса. Этот smoke не доказывает полный
пользовательский E2E, все ветки resolver, справедливость лимитера или достижение
целевых показателей нагрузки. Реальные Битрикс, SMS и платежи не использовались.

### Дополнение: приём через API с настоящим PostgreSQL

`tests/test_payment_events_api_postgres.py` — 2 passed (2026-09-12).
Два конкурентных ASGI HTTP POST проходят session/CSRF dependencies, настоящий
PaymentService и PaymentRepository/PaymentEventRepository. Оба получают 202
с одним ID; в PostgreSQL ровно одна оплата, одно задание и одна запись outbox.
GET этой операции возвращает 200; сотрудник-владелец сохранён корректно.
При искусственной ошибке после вставки задания/outbox оба POST получают 500,
а все три таблицы остаются пустыми: общая транзакция откатывается.

Тест использует отдельную UUID-схему в payment_events_test и удаляет только её.
Каталог и хранилище сессии подменены; вход через ТП и браузер не проверяются.
Запуск из backend с PAYMENT_EVENTS_TEST_DATABASE_URL тестового стенда:
`PYTHONPATH=. .venv/bin/pytest -q tests/test_payment_events_api_postgres.py`.

### Общий лимитер в отдельных процессах

`tests/test_payment_limiter_processes.py` — 1 passed (2026-09-12, 5,51 с).
Три независимых OS-процесса с собственными SQLAlchemy engine и одним ключом
интеграции выполнили по 8 допусков. Redis не подключался. При общем бюджете
20 RPS интервал между первым и последним из 24 допусков — 1,327 с
(минимальный ожидаемый 23/20 = 1,15 с с небольшим допуском измерения).
Таким образом проверено, что бюджет не становится 60 RPS при трёх процессах.
Все три процесса завершились успешно. Это не доказательство FIFO или отсутствия
голодания под бесконечной нагрузкой: существующий механизм использует конкуренцию
за текущий слот, справедливость остаётся отдельным критерием проверки.

Дополнительный тест постоянной конкуренции (по 6 секунд, без пауз между
запросами, 20 RPS, отдельные OS-процессы) — 2 passed за 22,04 с:

| Состав процессов | Число допусков formation / reconciliation / fallback | Максимальная пауза любого процесса |
|---|---|---:|
| 1 / 1 / 1 | 30 / 35 / 36 | 0,946 с |
| 4 / 1 / 1 | 17+27+14+15 / 12 / 15 | 2,025 с |

Порог теста задан до запуска: каждому процессу не менее 5 допусков,
максимальная пауза менее 3 секунд. Обе конфигурации прошли; сверка продолжает
работать при четырёх formation. Это воспроизводимый конечный stress gate,
не гарантия FIFO, равных долей или верхней границы ожидания для любого числа
реплик. Нагрузки с иным RPS/числом процессов требуют отдельного измерения.
Запуск: `pytest -q -s tests/test_payment_limiter_processes.py -k sustained`
с тем же тестовым DATABASE_URL и PYTHONPATH.

Регрессия backend после этих дополнений: 196 passed, 5 skipped за 38,90 с.
Запуск с PAYMENT_EVENTS_TEST_DATABASE_URL и PAYMENT_EVENTS_TEST_REDIS_URL
изолированного стенда, `PYTHONPATH=. .venv/bin/pytest -q`. Пропущены три
нагрузочных opt-in сценария и два старых PostgreSQL-теста, требующие отдельной
PAYMENT_TEST_DATABASE_URL; пропуски не считаются пройденными проверками.
# SIGTERM во время формирования — 2026-09-12

Отдельный повторный запуск проекта `tp-payment-runtime-test` из трёх тестовых
Compose-файлов; рабочие сервисы не менялись. Образ пересобран из текущего кода:
`tp-payment-runtime-test:local`, manifest
`sha256:cf3c7f6a3074286a721bbb1daeb066a90d4e9708961c86e95093a7c5e8bb2888`.
Один formation, один reconciliation, relay и API; recovery-formation не запускался,
чтобы проверить именно остановку приёма новых заданий consumer.

Воспроизведение: `runtime_shutdown_probe.py` запускается внутри тестовой Docker
сети с тем же образом, файл монтируется read-only в `/app/runtime_shutdown_probe.py`.
Он использует только тестовые service names/credentials. Последовательность:

1. `prepare` требует пустую тестовую БД, создаёт две синтетические оплаты и ждёт
   контрольной точки: fake уже сохранил счёт, но удерживает ответ crm.item.add.
2. `docker kill --signal=TERM tp-payment-runtime-test-payment-formation-worker-1`.
3. `release` освобождает ответ до истечения HTTP timeout и shutdown grace.
4. После выхода контейнера `verify-drained`: первое formation job completed,
   второе ready; QR только у первой оплаты. `docker inspect` подтвердил exited 0.
5. `docker start tp-payment-runtime-test-payment-formation-worker-1`;
   после обработки `verify-recovered`: оба задания completed, два QR, ровно
   два счёта, две товарные строки, два платежа, две связи товара, по два
   комментария и активности формирования. Дублей нет.

Все три checkpoint/assertion команды прошли. Новый hold/release механизм fake
отдельно проверен в test_runtime_fake_bitrix.py: **2 passed за 0,79 с**.
Проверен drain в пределах grace, **не** истечение grace во время незавершённого
HTTP-запроса. Последний сценарий ещё требует отдельного runtime прогона.
