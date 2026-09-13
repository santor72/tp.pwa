export type PaymentJob = {
  id: string; transaction_id: string; kind: string; state: string; generation: number;
  attempt_count: number; error: string | null; available_at: string;
  started_at: string | null; finished_at: string | null;
}

export async function retryPaymentJob(id: string, csrf: string): Promise<string> {
  const response = await fetch(`/api/admin/payment-jobs/${encodeURIComponent(id)}/retry`, {
    method: 'POST', credentials: 'include', headers: { 'X-CSRF-Token': csrf, Accept: 'application/json' },
  })
  if (!response.ok) throw new Error(response.status === 409
    ? 'Состояние задания изменилось. Обновите карточку.'
    : 'Не удалось принять повтор. Проверьте доступ и обновите карточку перед новой попыткой.')
  const result = await response.json()
  return result.job_id
}

const states: Record<string, string> = {
  ready: 'Ожидает запуска', running: 'Выполняется', retry_wait: 'Ожидает повтора',
  completed: 'Завершено', superseded: 'Заменено следующим заданием',
  failed: 'Ошибка', needs_reconciliation: 'Нужна сверка результата Битрикса',
}

export default function PaymentJobs({ jobs, busy, onRetry }: {
  jobs: PaymentJob[]; busy: boolean; onRetry: (job: PaymentJob) => void;
}) {
  return <section aria-label="Фоновые задания">
    <h3>Фоновые задания</h3>
    <p>Доступны и при выключенных замерах. Повтор ставит задание в очередь, а не подтверждает оплату.
      Неизвестный результат сначала проверяется в Битриксе; слепого повторного создания нет.</p>
    {!jobs.length && <p>Заданий пока нет.</p>}
    {jobs.map(job => <div key={job.id}>
      <strong>{job.kind === 'formation' ? 'Формирование' : 'Сверка статуса'} — {states[job.state] || job.state}</strong>
      <small>ID: {job.id} · поколение {job.generation} · повторов: {job.attempt_count}</small>
      <small>Не раньше: {new Date(job.available_at).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })} (МСК)</small>
      {job.error && <p>Причина: {job.error}</p>}
      {['failed', 'needs_reconciliation'].includes(job.state) &&
        <button disabled={busy} onClick={() => onRetry(job)}>Повторить обработку</button>}
    </div>)}
  </section>
}
