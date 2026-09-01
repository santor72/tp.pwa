export type UserRole = 'admin' | 'manager' | 'user'
export type Capabilities = { payments: boolean; messenger_settings: boolean; all_tickets: boolean }

export type UserProfile = {
  id: number | string
  email: string
  first_name: string | null
  status: string | null
  role: UserRole
  user_permissions: Record<string, unknown>
}

export type Session = { user: UserProfile; csrf_token: string; capabilities: Capabilities }
export type PaymentAddress = { locid: number; loctext: string }
export type PaymentProduct = { product_id: number; title: string; default_amount: string; currency: string; price_override_allowed: boolean }
export type PaymentCandidate = { entity_type: 'contact' | 'lead'; entity_id: number; display_name: string; phone_hint?: string | null }
export type PaymentStatus = 'draft' | 'resolving_client' | 'client_selection_required' | 'client_resolved' | 'invoice_created' | 'product_added' | 'payment_created' | 'link_created' | 'send_queued' | 'sent' | 'paid' | 'send_failed' | 'failed' | 'expired' | 'canceled'
export type PaymentAccepted = { id: string; status: PaymentStatus }
export type PaymentTransaction = PaymentAccepted & {
  current_step: string
  send_status: string | null
  product_title: string
  catalog_amount: string
  actual_amount: string
  currency: string
  payment_url: string | null
  payment_short_url: string | null
  payment_qr: string | null
  candidates: PaymentCandidate[]
  error_code: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}
export type PaymentCreatePayload = {
  idempotency_key: string
  address?: PaymentAddress
  apartment?: string
  product_id: number
  first_name: string
  second_name?: string
  last_name: string
  phone: string
  amount: string
}
export type TicketDay = 'today' | 'tomorrow'
export type TicketComment = {
  created_at: string | null
  author: string
  text: string
}
export type Ticket = {
  id: number
  address: string
  client_phone: string
  client_phones: string[]
  client_name: string
  description: string
  scheduled_at: string | null
  kind: 'connection' | 'repair'
  completed: boolean
  tags: Record<string, unknown>
  comments: TicketComment[]
  assigned_masters: string[]
  can_change_completion: boolean
}
export type MessengerLink = {
  provider: 'telegram'
  linked_at: string
  username: string | null
  display_name: string | null
}
export type MessengerLinkCreate = {
  provider: 'telegram'
  deep_link: string
  expires_at: string
}

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message)
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    headers: { Accept: 'application/json', ...init.headers },
    ...init,
  })
  if (response.status === 204) return undefined as T
  const data = await response.json().catch(() => ({}))
  if (response.status === 401 && path !== '/api/auth/login') {
    window.dispatchEvent(new Event('auth-expired'))
  }
  if (!response.ok) {
    throw new ApiError(response.status, data.code ?? 'REQUEST_FAILED', data.message ?? 'Ошибка запроса')
  }
  return data as T
}

export const api = {
  session: () => request<Session>('/api/auth/session'),
  login: (email: string, password: string) => request<Session>('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  }),
  logout: (csrfToken: string) => request<void>('/api/auth/logout', {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrfToken },
  }),
  tickets: (day: TicketDay, scope: 'assigned' | 'all' = 'assigned') => request<Ticket[]>(`/api/tickets/${day}${scope === 'all' ? '?scope=all' : ''}`),
  setTicketCompletion: (ticketId: number, day: TicketDay, completed: boolean, csrfToken: string, comment?: string) =>
    request<Ticket>(`/api/tickets/${ticketId}/completion`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
      body: JSON.stringify({ day, completed, ...(comment ? { comment } : {}) }),
    }),
  dialPhone: (phone: string, csrfToken: string) => request<void>('/api/conversations/dial', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify({ phone }),
  }),
  paymentAddresses: () => request<PaymentAddress[]>('/api/payments/addresses'),
  paymentProducts: () => request<PaymentProduct[]>('/api/payments/products'),
  createPayment: (body: PaymentCreatePayload, csrfToken: string) => request<PaymentAccepted>('/api/payments', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify(body),
  }),
  payment: (id: string) => request<PaymentTransaction>(`/api/payments/${id}`),
  selectPaymentClient: (id: string, candidate: PaymentCandidate, csrfToken: string) => request<PaymentTransaction>(`/api/payments/${id}/client-selection`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify({ entity_type: candidate.entity_type, entity_id: candidate.entity_id }),
  }),
  resendPayment: (id: string, csrfToken: string) => request<PaymentTransaction>(`/api/payments/${id}/resend`, {
    method: 'POST', headers: { 'X-CSRF-Token': csrfToken },
  }),
  cancelPayment: (id: string, csrfToken: string) => request<PaymentTransaction>(`/api/payments/${id}/cancel`, {
    method: 'POST', headers: { 'X-CSRF-Token': csrfToken },
  }),
  messengerLinks: () => request<MessengerLink[]>('/api/messenger-links'),
  createTelegramLink: (csrfToken: string) => request<MessengerLinkCreate>('/api/messenger-links/telegram', {
    method: 'POST', headers: { 'X-CSRF-Token': csrfToken },
  }),
  revokeTelegramLink: (csrfToken: string) => request<void>('/api/messenger-links/telegram', {
    method: 'DELETE', headers: { 'X-CSRF-Token': csrfToken },
  }),
}
