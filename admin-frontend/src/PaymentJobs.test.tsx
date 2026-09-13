import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PaymentJobs, { PaymentJob, retryPaymentJob } from './PaymentJobs'

const job: PaymentJob = {
  id: 'job-1', transaction_id: 'tx-1', kind: 'formation', state: 'needs_reconciliation',
  generation: 2, attempt_count: 3, error: 'EXTERNAL_WRITE_UNKNOWN',
  available_at: '2026-09-11T10:00:00Z', started_at: null, finished_at: null,
}
afterEach(() => vi.unstubAllGlobals())
describe('Payment jobs', () => {
  it('shows safe diagnostics and permits retries only for stopped jobs', () => {
    const html = renderToStaticMarkup(<PaymentJobs jobs={[job, { ...job, id: 'job-2', state: 'completed' }]} busy={false} onRetry={() => {}} />)
    expect(html).toContain('Нужна сверка результата Битрикса')
    expect(html).toContain('EXTERNAL_WRITE_UNKNOWN')
    expect(html.match(/Повторить обработку/g)).toHaveLength(1)
    expect(html).toContain('не подтверждает оплату')
  })
  it('disables retry while submission is in progress', () => {
    expect(renderToStaticMarkup(<PaymentJobs jobs={[job]} busy onRetry={() => {}} />)).toContain('disabled=""')
  })
  it('sends session credentials and CSRF, returns durable job id', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ job_id: 'new-job' }) })
    vi.stubGlobal('fetch', fetch)
    expect(await retryPaymentJob('job-1', 'csrf-test')).toBe('new-job')
    expect(fetch).toHaveBeenCalledWith('/api/admin/payment-jobs/job-1/retry', {
      method: 'POST', credentials: 'include', headers: { 'X-CSRF-Token': 'csrf-test', Accept: 'application/json' },
    })
  })
  it.each([403, 409, 503])('does not treat HTTP %s as accepted', async status => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status }))
    await expect(retryPaymentJob('job-1', 'csrf-test')).rejects.toThrow()
  })
})
