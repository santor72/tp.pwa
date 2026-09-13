import { useEffect, useState } from 'react'
import PaymentJobs, { PaymentJob, retryPaymentJob } from './PaymentJobs'

type Span = { id: string; parent_id: string | null; run_id: string; name: string; kind: string; started_at: string; duration_ms: number; outcome: string; details: Record<string, unknown> }
type Item = { id: string; created_at: string; employee: string | null; phone: string; status: string; queue_ms: number | null; formation_ms: number | null; link_ms: number | null; qr_ms: number | null; human_ms: number; rest_calls: number; rest_retries: number; worker_retries: number; measured: boolean; browser_flags: { background: boolean; restored: boolean }[] }
type Detail = Item & { spans: Span[]; jobs: PaymentJob[] }
type Data = { items: (Item & { job_issues?: PaymentJob[] })[]; total: number; stats: { samples: number; median_ms: number | null; p95_ms: number | null; failed: number } }
type QueueHealth = { checked_at: string; alerts: string[]; active_leases: number; unpublished: number; unknown_writes: number; backlog: { kind: string; count: number; oldest_due_seconds: number }[] }
const ms = (value: number | null) => value === null ? 'Нет замера' : `${(value / 1000).toFixed(2)} с`
const date = (s: string) => new Date(s).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })
const names: Record<string, string> = { http_accept: 'HTTP-запрос формирования', request_validation: 'Проверка запроса и доступа', selection_queue: 'Очередь после выбора', retry_schedule: 'Планирование повтора', retry_wait: 'Ожидание повтора', retry_delay: 'Назначенная пауза', rest_backoff: 'Пауза между REST-попытками', accept: 'Приём запроса', worker: 'Запуск обработчика', selection: 'Выбор клиента', initial_queue: 'Ожидание первого запуска', human_wait: 'Ожидание выбора сотрудника', link_ready: 'От создания операции до ссылки (включая ожидания)', resolve_client: 'Поиск и обновление клиента', invoice: 'Счёт', product: 'Товарная строка', payment_document: 'Платёж', payment_product: 'Связь платежа с товаром', public_link: 'Получение ссылки и QR', timeline: 'Комментарии', send: 'Запуск отправки', activity: 'Активность', catalog: 'Каталог', catalog_cache: 'Кеш каталога', browser_accept: 'Телефон: принятие запроса', browser_link: 'Телефон: получение ссылки', browser_qr: 'Телефон: загрузка QR', worker_attempt: 'Номер попытки' }
async function get<T>(path: string): Promise<T> {
  const response = await fetch(path, { credentials: 'include' })
  if (!response.ok) throw new Error('Не удалось загрузить замеры. Проверьте доступ и повторите запрос.')
  return response.json()
}

Object.assign(names, {
  job_execution: 'Запуск фонового задания', job_attempt: 'Вид задания и попытка',
  outbox_wait: 'От готовности задания до публикации в Redis',
  transport_wait: 'От публикации до получения обработчиком',
  lease_acquire: 'Запрос захвата оплаты (не всё ожидание занятого обработчика)',
  job_ready_wait: 'От готовности задания до успешного захвата',
  limiter_wait: 'Ожидание общего бюджета запросов Битрикса',
})

export default function Timings({ csrf }: { csrf: string }) {
  const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Moscow' })
  const [from, setFrom] = useState(today), [to, setTo] = useState(today)
  const [phone, setPhone] = useState(''), [employee, setEmployee] = useState(''), [status, setStatus] = useState('')
  const [page, setPage] = useState(1), [data, setData] = useState<Data | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [retryBusy, setRetryBusy] = useState(false), [jobMessage, setJobMessage] = useState('')
  const [health, setHealth] = useState<QueueHealth | null>(null)
  async function load(next = 1) {
    setBusy(true); setError('')
    try {
      const query = new URLSearchParams({ date_from: from, date_to: to, phone, employee, status, page: String(next) })
      const [list, queues] = await Promise.all([
        get<Data>(`/api/admin/payment-timings?${query}`),
        get<QueueHealth>('/api/admin/payment-jobs/health').catch(() => null),
      ])
      setData(list); setHealth(queues); setPage(next)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  async function open(id: string) {
    setJobMessage('')
    try { setDetail(await get<Detail>(`/api/admin/payment-timings/${id}`)) } catch (e) { setError((e as Error).message) }
  }
  async function retry(job: PaymentJob) {
    if (retryBusy || !window.confirm('Поставить задание на повторную обработку? Неизвестный результат будет сначала проверен в Битриксе.')) return
    setRetryBusy(true); setJobMessage('')
    try {
      const id = await retryPaymentJob(job.id, csrf)
      setJobMessage(`Повтор принят. Новое задание: ${id}. Это ещё не результат выполнения.`)
      try {
        const updated = await get<Detail>(`/api/admin/payment-timings/${job.transaction_id}`)
        setDetail(current => current?.id === updated.id ? updated : current)
      } catch { setJobMessage(`Повтор принят (${id}), но карточку обновить не удалось. Обновите её вручную.`) }
    } catch (e) { setJobMessage((e as Error).message) } finally { setRetryBusy(false) }
  }
  useEffect(() => { void load() }, [])
  const longest = detail?.spans.filter(s => s.kind === 'step' && s.outcome !== 'skipped').reduce<Span | null>((a, b) => !a || a.duration_ms < b.duration_ms ? b : a, null)
  return <section className="shell">
    <h1>Скорость формирования</h1>
    <p>Все статусы. Период — по дате создания операции, московское время. Замеры появляются после завершения запуска обработчика.</p>
    <section aria-label="Состояние очередей">
      <h2>Состояние очередей</h2>
      <p>По всем операциям, независимо от фильтров и включения замеров. Обновляется кнопкой «Обновить».</p>
      {health ? <>
        <p>Проверено: {date(health.checked_at)}. Выполняется оплат: {health.active_leases}.
          Не отправлено событий: {health.unpublished}. Неизвестных результатов: {health.unknown_writes}.</p>
        {health.backlog.map(item => <p key={item.kind}>{item.kind === 'formation' ? 'Формирование' : 'Сверка статуса'}:
          ожидает {item.count}, самое старое — {Math.round(item.oldest_due_seconds)} с.</p>)}
        {health.alerts.length ? <p className="error" role="status">Требуют внимания: {health.alerts.join(', ')}</p>
          : <p>Пороговые предупреждения отсутствуют. Это не подтверждение оплаты или доступности Битрикса.</p>}
      </> : <p>Нет данных о состоянии очередей. Повторите обновление.</p>}
    </section>
    <section className="filters">
      <label>С <input type="date" value={from} onChange={e => setFrom(e.target.value)} /></label>
      <label>По <input type="date" value={to} onChange={e => setTo(e.target.value)} /></label>
      <label>Телефон <input value={phone} onChange={e => setPhone(e.target.value)} /></label>
      <label>Сотрудник <input value={employee} onChange={e => setEmployee(e.target.value)} /></label>
      <label>Статус <select value={status} onChange={e => setStatus(e.target.value)}><option value="">Все</option>{['draft', 'resolving_client', 'client_selection_required', 'client_resolved', 'invoice_created', 'product_added', 'payment_created', 'link_created', 'send_queued', 'sent', 'send_failed', 'paid', 'failed', 'expired', 'canceled'].map(s => <option key={s}>{s}</option>)}</select></label>
      <button disabled={busy} onClick={() => load()}>Обновить</button>
    </section>
    {error && <p className="error">{error}</p>}
    {data && <>
      <p>Операций: {data.total}. Ошибочных (failed): {data.stats.failed}. От кнопки до QR: медиана {ms(data.stats.median_ms)}, p95 {ms(data.stats.p95_ms)}.</p>
      <p>Замеры телефона для статистики: {data.stats.samples} из {data.total} ({data.total ? Math.round(data.stats.samples / data.total * 100) : 0}%). Фоновые и восстановленные сессии исключены. Это данные браузера, не подтверждение оплаты.</p>
      <div className="table-wrap"><table><thead><tr><th>Создана</th><th>Сотрудник / телефон</th><th>Статус</th><th>Очередь</th><th>Этапы до ссылки</th><th>До ссылки*</th><th>До QR на телефоне</th><th>REST / повторы</th></tr></thead><tbody>
        {data.items.map(item => <tr key={item.id} onClick={() => open(item.id)}><td>{date(item.created_at)}</td><td>{item.employee || '—'}<small>{item.phone}</small></td><td>{item.status}{!!item.job_issues?.length && <small className="error">Остановлено заданий: {item.job_issues.length}</small>}</td><td>{ms(item.queue_ms)}</td><td>{ms(item.formation_ms)}</td><td>{ms(item.link_ms)}</td><td>{ms(item.qr_ms)}{item.browser_flags.some(f => f.background || f.restored) && <small>Фон / восстановление</small>}</td><td>{item.measured ? `${item.rest_calls} / ${item.rest_retries}` : 'Нет замера'}<small>Повторы worker: {item.worker_retries}</small></td></tr>)}
      </tbody></table></div>
      <p>* От создания операции до сохранения ссылки, включая очередь, повторы и выбор сотрудника.</p>
      <nav><button disabled={busy || page === 1} onClick={() => load(page - 1)}>←</button><span>{page} / {Math.max(1, Math.ceil(data.total / 50))}</span><button disabled={busy || page * 50 >= data.total} onClick={() => load(page + 1)}>→</button></nav>
    </>}
    {detail && <div className="modal" onClick={() => setDetail(null)}><article onClick={e => e.stopPropagation()}><button className="close" onClick={() => setDetail(null)}>×</button><h2>Этапы формирования</h2><p>{detail.id}</p><p>Ожидание выбора сотрудника: {ms(detail.human_ms)}. Вложенные интервалы нельзя суммировать с родительскими.</p>
      {jobMessage && <p role="status">{jobMessage}</p>}
      <button disabled={retryBusy} onClick={() => open(detail.id)}>Обновить карточку</button>
      <PaymentJobs jobs={detail.jobs || []} busy={retryBusy} onRetry={retry} />
      {!detail.spans.length && <p>Нет замеров: операция создана до обновления, ещё не обработана или запись телеметрии не удалась.</p>}
      {detail.spans.filter(s => s.kind !== 'rest' && s.kind !== 'db').map(s => <div key={s.id} style={{ borderLeft: s.id === longest?.id ? '4px solid #d97706' : undefined, padding: '8px' }}>
        <strong>{names[s.name] || s.name}: {ms(s.duration_ms)}</strong> — {s.outcome}<small>{date(s.started_at)} · запуск {s.run_id.slice(0, 8)}</small>
        {Object.keys(s.details).length > 0 && <small>{JSON.stringify(s.details)}</small>}
        <details><summary>Вложенные запросы и этапы</summary>{detail.spans.filter(c => c.parent_id === s.id).map(c => <p key={c.id}>{names[c.name] || c.name}: {ms(c.duration_ms)} · {c.outcome}<small>{JSON.stringify(c.details)}</small></p>)}</details>
      </div>)}
    </article></div>}
  </section>
}
