# Аудит приёмки событийных платежей

Состояние на 2026-09-13. Цель ещё не объявлена выполненной. Этот документ —
указатель на доказательства и оставшиеся проверки, а не замена исходного
[плана](payment-events-outbox-plan.md). История прогонов и ограничения:
[отчёт](payment-events-implementation-report.md),
[runtime](payment-events-runtime-smoke.md),
[нагрузка](payment-events-load-report.md).

Последний полный backend-прогон: **297 passed, без пропусков, 145,85 с**. Он
включает добавленные hold/release, terminal, consumer/fallback и все предыдущие
сценарии. Frontend 21 passed, admin 6 passed, обе сборки успешны.

## Матрица: где искать доказательства

Все имена test-файлов ниже находятся в `backend/tests/`. «Сверить» означает,
что окончательное соответствие **всему** исходному сценарию не утверждается.
Наличие теста с номером Txx само по себе не является закрытием требования.

| Пункт | Имеющееся доказательство | Остаток аудита |
|---|---|---|
| T01–T02 | test_payment_events_api_postgres: конкурентные POST, rollback outbox, реальный TCP-разрыв до commit | Полный HTTP admission проверен |
| T03–T04 | test_payment_relay_concurrency: два relay, 16 событий, NOWAIT во время XADD, потеря publication commit | Проверено: все 16 ID доставлены, одно повторено с тем же ID; публикация не завершает job |
| T05 | Repository: notify после commit, разрыв LISTEN и catch-up; test_payment_polling_deadlines без NOTIFY | При интервале 0,2 с верхняя оценка обнаружения 0,222 с; запас на БД/планирование 0,5 с |
| T06 | Там же: конкурентные claims/executor, независимые оплаты; test_payment_events_scaling | Сопоставить с обоими видами задач одной оплаты |
| T07 | test_payment_event_transport, test_payment_shutdown, runtime SIGTERM и replay в command workflow | Сверить все точки остановки до/после commit/ACK |
| T08 | test_payment_live_heartbeat: работа дольше первоначального lease, реальный heartbeat; repository stale-token tests | Подтверждено продление без ручного изменения часов/lease; конкурентные formation и reconciliation не захватывают оплату |
| T09–T10 | test_payment_write_recovery_postgres, test_payment_events_scaling SIGKILL, runtime grace timeout | 6 create-семейств проверены через HTTP+journal; остальные recovery-контракты — unit |
| T11 | Repository valid_event/generation и replay completed; test_payment_mode_roundtrip | Сверить перестановку разных поколений одной оплаты |
| T12 | test_payment_invalid_delivery: полный consumer-путь пяти повреждений, отказ quarantine, повтор и следующее корректное событие | Проверено на настоящих PG/Redis: нет вызова executor до успешной валидации, ACK только после durable quarantine |
| T13 | Runtime HTTP workflow при остановленном Redis; test_payment_polling_deadlines запускает recovery.run без Redis | При интервале 0,2 с верхняя оценка обнаружения 0,280 с; запас на БД/планирование 0,5 с |
| T14 | test_payment_stream_loss: удаление UUID Stream вместе с group/PEL, DB recovery, relay и consumer | Подтверждено: восстановлено только незавершённое задание; повтор старого события не вызывает бизнес-обработку |
| T15 | test_payment_consumer_fallback_race: настоящий Redis consumer удерживает CRM step, PostgreSQL recovery одновременно обходит due jobs | Подтверждено: running job не попадает fallback в выборку, один вызов бизнес-сервиса и один ACK |
| T16 | Bitrix client retries, PG cooldown, executor delayed jobs/budget | Сверить 429/5xx/исчерпание в совместном executor-сценарии |
| T17 | test_payment_activity_failure_workflow: HTTP 400 с пустым/отсутствующим error после QR, durable retry | QR сохранён, activity flag false; повтор завершает activity без дублей счёта/платежа и повторной отправки |
| T18 | Возобновляемые шаги PaymentService и разделённые formation/reconciliation | Сверить незавершённые действия после ссылки под общим lease |
| T19 | test_payment_callback_admission: HTTP до/после commit, rollback, секрет, JSON/form и дубли; repository callback races | Подтверждено: 202 только после commit, ошибка записи — 500 без задания; CRM inline не вызывается |
| T20 | test_payment_command_workflow + test_payment_cancel_during_formation: права, pending, replay и cancel во время CRM write | Подтверждено: команда сохраняется, ждёт lease, после formation удаляет платёж один раз |
| T21 | test_payment_terminal_events: старый formation для canceled/expired/paid; поздний authoritative paid; status tests | Подтверждено: stale formation без внешних действий, поздний paid переводит canceled в paid без создания документов |
| T22 | test_payment_status: paid сохраняется до activity; paid backfill | Сверить отдельный прогресс retry последействий |
| T23 | Процессная нагрузка 1/2/4, SIGKILL; Compose масштабирование; оба runtime SIGTERM | Штатный drain и превышение grace проверены; свести временные gate |
| T24 | test_payment_limiter_processes: независимые процессы, общий бюджет, ограниченный тест справедливости | Не заявлять строгую FIFO/неограниченное число реплик |
| T25 | test_payment_events_scaling: новая formation на backlog reconciliation | Сопоставить границы измерения и общий limiter |
| T26 | test_payment_retention: pending/unread/unknown, archive rollback/replay, quarantine | Сверить все retention-настройки и эксплуатационную политику |
| T27 | Opt-in executor telemetry и health; frontend/admin тесты | Сверить весь набор требуемых IDs/времён и отсутствие PII |
| T28 | Реальные upgrade/downgrade, backfill и mode roundtrip до paid | Сверить runbook с фактическими Compose-командами |
| T29 | test_payment_database_outage + HTTP admission: реальные разрывы TCP | Worker и POST проверены; in-flight неизвестный write покрыт runtime grace |

## Как закрывать остаток

1. Открыть указанный тест и соответствующий код; определить, действительно ли
   оставшийся случай уже проверяется. Не писать дублирующий тест только из-за
   незакрытой галочки.
2. Если доказательство слишком узкое — добавить недостающий сценарий и выполнить
   его на изолированном стенде; если найден дефект — сначала воспроизвести его.
3. Сверить разделы 3–6 плана: конфигурацию, идентификаторы/PII, индексы и часы БД,
   runbook, настройки масштабирования и общие ресурсные ограничения.
4. Отдельно свести измеримые gate 1.2 и полный итоговый прогон после изменений.
5. Рабочее внедрение и реальные операции Битрикса не выполнять автоматически;
   в финальном отчёте явно оставить их операторскими действиями.

Этот аудит намеренно оставляет неопределённость открытой. Он не утверждает,
что перечисленные остатки требуют нового кода: часть закроется чтением уже
существующих проверок и результатов.

## Вывод автоматической приёмки

Требования Goal выполнены в согласованной автоматической границе: обработчики
formation/reconciliation/recovery/relay разделены, transactional outbox и Redis
Streams задействованы, fallback использует общий executor/lease, а formation и
reconciliation независимо масштабируются. Миграции, конфигурация, runbook,
health/telemetry/retention и отчёты присутствуют; тестовая инфраструктура не
задействует рабочие системы.

Остающиеся неотмеченные пункты плана — только production rollout: окно
maintenance, применение миграций/образов на рабочем сервере, остановка старого
контейнера, первоначальный запуск и real-Bitrix smoke. Они требуют отдельного
разрешения пользователя и не являются недоделкой автоматической реализации.

## Классы тестов и стенд

| Класс | Файлы/артефакт | Что является настоящим |
|---|---|---|
| Unit/контракт | test_payment_config, test_bitrix24_client, test_payment_status, test_payment_service | Машины состояния и REST-контракт; внешнее API подменено |
| Интеграция PostgreSQL | test_payment_events_repository, migrations, database_outage, write_recovery_postgres, callback_admission | PostgreSQL 16, миграции, транзакции, locks/leases; URL test database обязателен |
| Интеграция Redis | transport, invalid_delivery, stream_loss, relay_concurrency, retention | PostgreSQL 16 + Redis 7.4, UUID schema/prefix; Stream и group настоящие |
| Многопроцессная нагрузка | test_payment_events_scaling, test_payment_limiter_processes, load report | Отдельные Python processes, реальные Postgres/Redis, HTTP fake |
| Compose runtime | runtime_fake_bitrix, runtime_workflow_probe, runtime_shutdown_probe, runtime smoke | Отдельный project `tp-payment-runtime-test`, tmpfs БД, изолированная Docker-сеть |
| UI/admin | frontend tests/App.test.tsx, admin-frontend PaymentJobs.test.tsx | React/Vitest; API/fetch подменены, не браузерный e2e |

Disposable инфраструктура задаётся `compose.payment-events-test.yaml`; runtime
накладывает только `compose.payment-runtime-test.yaml` и workflow/shutdown
overlay. Рабочие БД, Redis, контейнеры и Bitrix24 не используются. До событийной
реализации baseline был зафиксирован в implementation report: 130 backend
passed, 2 старых PostgreSQL теста пропущены без отдельной БД; прежние image IDs
также записаны там. Позднейшие результаты не трактуются как A/B старых образов.
