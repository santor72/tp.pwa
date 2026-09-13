# Событийные платежи: переключение, масштабирование и восстановление

Статус: инструкция по текущей реализации. Полное внедрение ещё не подтверждено
итоговым аудитом плана. Команды ниже не выполнялись на рабочем сервере.

## Перед переключением

1. Завершить проверки `payment-events-outbox-plan.md`, зафиксировать проверенную
   версию кода/образов. Проверить резервную копию PostgreSQL и способ восстановления.
2. Выбрать окно обслуживания. Остановка API в этой инструкции временно затрагивает
   всё приложение, не только платежи. Предупредить сотрудников, закрыть внешний
   доступ к API на время переключения. Битрикс продолжает принимать оплаты по
   уже отправленным ссылкам; callback во время остановки API может не доставиться.
   После запуска известные открытые оплаты должны быть подтверждены сверкой.
3. Записать текущие `PAYMENT_PROCESSING_MODE`, Compose profile, версии образов,
   число обработчиков и состояние очередей. Не выводить полный `.env` или
   `docker compose config` с раскрытыми секретами в общие логи.
4. Согласовать общий бюджет `PAYMENT_BITRIX_REQUESTS_PER_SECOND`: default 1 запрос/с
   — консервативная настройка приложения, не заявленный лимит портала. Увеличение
   worker не увеличивает этот бюджет. Другие приложения портала он не ограничивает.
5. Собирать API, мигратор и все worker из одной версии исходников. Новые процессы
   требуют новых миграций. Старый бинарник не понимает jobs/leases и не может
   работать параллельно с новым executor.

Команды выполнять из каталога проекта, с теми же Compose-файлами и project name,
что используются для приложения. Если сервер использует `-f`/`-p`, добавить их
ко всем командам ниже. Не смешивать проект приложения с `tp-payment-events-test`.

## Переключение legacy → events

Сначала собрать образы; этот шаг сам по себе не запускает обработчики:

```bash
COMPOSE_PROFILES=events docker compose build api migrate payment-outbox-relay payment-formation-worker payment-reconciliation-worker payment-recovery-formation payment-recovery-reconciliation frontend admin-frontend
```

После закрытия внешнего доступа остановить API и все варианты обработчиков:

```bash
docker compose --profile legacy --profile events stop -t 40 api payment-worker payment-outbox-relay payment-formation-worker payment-reconciliation-worker payment-recovery-formation payment-recovery-reconciliation
docker compose --profile legacy --profile events ps --all
```

Убедиться, что нет оставшихся старых реплик, ручных запусков или обработчиков
в другом Compose-проекте. Не использовать `down -v` и не удалять тома.

Дождаться завершения внешних запросов. Если старый обработчик был принудительно
остановлен во время CRM write, сверить результат в Битриксе перед продолжением.
У старого кода нет нового журнала неизвестных записей: нельзя считать его
автоматически защищённым новым механизмом восстановления.

Применить миграции:

```bash
docker compose run --rm --no-deps migrate
```

Они добавляют таблицы jobs/outbox/leases и служебный режим `legacy`, сохраняют
старые оплаты. PostgreSQL и Redis должны быть уже запущены; `--no-deps` намеренно
не запускает посторонние сервисы.

В `.env` установить:

```dotenv
COMPOSE_PROFILES=events
PAYMENT_PROCESSING_MODE=events
PAYMENT_EVENTS_REDIS_URL=redis://redis:6379/3
PAYMENT_EVENTS_PREFIX=tp-pwa
PAYMENT_FORMATION_CONCURRENCY=1
PAYMENT_RECONCILIATION_CONCURRENCY=1
PAYMENT_TELEMETRY_ENABLED=false
```

URL приведён для встроенного Redis. Префикс должен отличаться у окружений,
использующих один Redis. Отдельная DB изолирует имена, но не память/CPU Redis.
Не менять префикс/DB для «очистки» зависшей очереди.

Переключить режим в PostgreSQL и восстановить задания старых операций:

```bash
COMPOSE_PROFILES=events docker compose run --rm --no-deps payment-outbox-relay python -m app.payment_runtime set-mode events
COMPOSE_PROFILES=events docker compose run --rm --no-deps payment-outbox-relay python -m app.payment_runtime backfill
```

`set-mode` откажет при живых участниках runtime или активных leases. Не обходить
проверку прямым UPDATE. Проверить остановку процессов и дождаться истечения
владения; по умолчанию lease 90 с, heartbeat процесса живёт 30 с. Одна смена
переменной в `.env` не останавливает старый контейнер. Backfill идемпотентен.

Запустить по одному экземпляру каждой роли и API:

```bash
COMPOSE_PROFILES=events docker compose up -d --no-deps --scale payment-formation-worker=1 --scale payment-reconciliation-worker=1 api payment-outbox-relay payment-formation-worker payment-reconciliation-worker payment-recovery-formation payment-recovery-reconciliation
COMPOSE_PROFILES=events docker compose up -d --no-deps frontend admin-frontend
COMPOSE_PROFILES=events docker compose ps
```

До открытия доступа проверить health контейнеров, отсутствие mode mismatch,
прогресс старых операций, состояние очередей в админке и логи
`payment.queue.attention` / `payment.queue.health_unavailable`. Только после
этого снять обслуживание. Реальная тестовая операция с Битриксом требует отдельно
согласованных клиента, товара и суммы; это не часть автоматического smoke.

## Независимое масштабирование

```bash
COMPOSE_PROFILES=events docker compose up -d --no-deps --scale payment-formation-worker=2 --scale payment-reconciliation-worker=1 payment-formation-worker payment-reconciliation-worker
```

Сначала менять число реплик одной роли и наблюдать очередь, RPS/429 и время
готовности ссылки. Для четырёх formation использовать `=4`. Количество процессов
и `PAYMENT_*_CONCURRENCY` перемножаются; не увеличивать оба параметра одновременно
без измерений. Consumer group общая для всех экземпляров одного вида работы.

Relay публикует, formation формирует и выполняет команды, reconciliation сверяет
статус, две recovery-роли восстанавливают доставку и выполняют задания напрямую
через PostgreSQL. Не добавлять отдельную consumer group для каждой реплики.

## Сбои и ручной повтор

- Redis недоступен: не перезапускать старый worker. Recovery-роли работают через
  PostgreSQL и общий executor/lease/лимитер. Начальный обход — раз в 30 с,
  это не гарантия завершения за 30 с при backlog или занятой оплате.
- Worker остановлен: pending будет перехвачен; право выполнения определяется
  PostgreSQL lease. Unknown write сначала сверяется, а не повторяется вслепую.
- PostgreSQL недоступен: новые оплаты не принимаются без сохранения, обработка
  не должна начинать следующий внешний шаг без проверки владения.
- `failed` / `needs_reconciliation`: открыть карточку в «Скорость формирования»,
  посмотреть код и журнал Битрикса. «Повторить обработку» создаёт durable задание
  с аудитом администратора, не стирает unknown write. Если доказательств результата
  нет, повтор может снова остановиться на сверке; это ожидаемая защита от дублей.
- Старый API возобновления всей операции сохраняет вид и параметры единственного
  остановленного задания (в том числе отмену и выбор клиента). Если таких заданий
  несколько, требуется выбрать конкретное в диагностике — API не угадывает
  намерение администратора. Для unknown write использовать диагностическую
  карточку задания: старый API resume такие операции не возобновляет.
- Не выполнять вручную XADD, XACK, FLUSHDB, FLUSHALL или удаление lease/журнала
  ради разблокировки. Не удалять pending сообщения как способ восстановления.

Детали порогов и диагностики — в `payment-timing-diagnostics.md`.
Внешние оповещения людям по этим логам настраиваются отдельно.

## Очистка истории Stream (опционально)

Первый этап очистки удаляет только сообщения завершённых (`completed` или
`superseded`) заданий старше согласованного срока. Идентичность события проверяется
по jobs/outbox или компактному архиву PostgreSQL. Журнал внешних действий
сохраняется; защита от повторной обработки не исчезает.

Тот же механизм очищает старые записи карантина повреждённых событий:
сначала тело сообщения в Redis после ACK всех групп, затем запись Postgres
после проверки отсутствия тела и pending. Диагностика хранится не меньше
PAYMENT_STREAM_RETENTION_DAYS. Обход таблицы порционный, с курсором; pending
сохраняется без ограничения по возрасту. При недоступном Redis очистки таблицы
нет. Счётчик удаления — в логе `payment.quarantine.retention`; raw сообщений
не сохраняется. Dry-run ничего не удаляет. Рабочая очистка требует согласования
срока, автоматические тесты выполняют её только в изолированной схеме.

По умолчанию `PAYMENT_STREAM_RETENTION_ENABLED=false`. После согласования срока
можно включить этот флаг у relay. Настройки:
`PAYMENT_STREAM_RETENTION_DAYS=30`, `PAYMENT_STREAM_RETENTION_BATCH_SIZE=50`,
`PAYMENT_STREAM_RETENTION_INTERVAL_SECONDS=600`. 30 дней — предлагаемое значение,
не автоматическое решение о хранении данных рабочего окружения.

За проход проверяется ограниченная страница каждого Stream. Cursor переносится
на следующий проход, после конца начинается новый обход; после рестарта обход
начинается сначала. Наличие старого pending не блокирует обход следующих страниц.
В Redis атомарно проверяется, что сообщение прочитано и не является pending
ни в одной группе, затем выполняется XDEL. Неизвестные группы тоже защищены;
при отсутствии групп или числе групп больше восьми удаления не выполняются.
Битые/неизвестные сообщения этим механизмом не удаляются — ими занимается consumer.

Логи `payment.stream.retention` содержат scanned/eligible/deleted, без содержимого
сообщений. Eligible — кандидаты по PostgreSQL; Redis может сохранить их как pending
или непрочитанные, поэтому deleted может быть меньше. Ошибки дают
`payment.stream.retention_failed`. Очистка Stream не заменяет восстановление
очереди и не должна включаться вместо разбора зависших заданий.

## Архив завершённых jobs/outbox (опционально)

Начиная с миграции `20260912_01`, `PAYMENT_JOB_RETENTION_ENABLED=true` включает
в relay или совместимом legacy-worker перенос старых заданий в
`payment_job_archive`. По умолчанию флаг выключен. Настройки:
`PAYMENT_JOB_RETENTION_DAYS=30`, `PAYMENT_JOB_RETENTION_BATCH_SIZE=50`,
`PAYMENT_JOB_RETENTION_INTERVAL_SECONDS=600`. Срок согласовать до включения.

Кандидаты — только completed/superseded старше срока у paid/canceled/expired
операций без незавершённых или ошибочных jobs, неизвестных write и живого lease.
У paid должны быть завершены все четыре признака CRM-последействий. Активная
публикация outbox защищена publisher lease. Последнее поколение каждого вида
задания остаётся в основной таблице: это опора generation и backfill.

В одной транзакции сохраняются event/job/transaction ID, вид, версия, generation,
финальное состояние и время завершения; затем удаляются полные job/outbox строки.
Параллельные очистители используют блокировки оплаты и SKIP LOCKED. Старое
pending-сообщение остаётся валидным по архиву и подтверждается без CRM-вызовов.
Очистка Stream также понимает архив, поэтому последовательность двух очисток
не приводит к зависшим сообщениям.

Это сокращение технической истории, а не удаление платежей или бизнес-аудита.
Из архива нельзя восстановить все параметры и диагностические поля старого job;
для этого нужна резервная копия. Админка показывает оставшуюся полную историю
jobs, а не записи компактного архива. Архивные идентификаторы и внешний журнал
автоматически не удаляются: они нужны для защиты от старых повторных событий.
В логах — `payment.jobs.archived` с количеством и `payment.jobs.retention_failed`.

## Откат на совместимый PostgreSQL polling

Это смена режима **текущей версии**, не возврат старого бинарника и не downgrade БД.
Закрыть внешний доступ, остановить API и все worker командой из раздела
переключения. Дождаться освобождения leases и проверить неизвестные CRM-записи.

В `.env` установить `COMPOSE_PROFILES=legacy` и `PAYMENT_PROCESSING_MODE=legacy`.
Собрать совместимый worker из той же проверенной версии и переключить DB mode:

```bash
COMPOSE_PROFILES=legacy docker compose build payment-worker
COMPOSE_PROFILES=legacy docker compose run --rm --no-deps payment-worker python -m app.payment_runtime set-mode legacy
COMPOSE_PROFILES=legacy docker compose up -d --no-deps api payment-worker
COMPOSE_PROFILES=legacy docker compose exec payment-worker python -m app.payment_runtime health legacy
```

Проверить health API, прогресс заданий, отсутствие живых event workers и только
затем открыть доступ. Уже принятые команды и журнал остаются в PostgreSQL.
Redis-сообщения не удалять: при будущем возвращении events завершённые задания
распознаются как уже обработанные. В legacy используется тот же executor;
исходный смешанный `process_once` не является рабочим fallback.

Alembic downgrade проверен только на одноразовой тестовой схеме. Он удаляет новые
таблицы и служебные данные и запрещён как автоматический оперативный откат.
Возврат старых образов требует отдельной проверки совместимости и плана данных.
