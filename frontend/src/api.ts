export type UserProfile = {
  id: number | string
  email: string
  first_name: string | null
  status: string | null
  user_permissions: Record<string, unknown>
}

export type Session = { user: UserProfile; csrf_token: string }
export type DomofonAddress = { locid: number; loctext: string }
export type DomofonOperationResult = { ok: true; reason: string }
export type DomofonCreatePayload = {
  locid: number
  field_flat: number
  field_podezd: number
  client_name: string
  phone?: string
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
  client_name: string
  description: string
  scheduled_at: string | null
  kind: 'connection' | 'repair'
  completed: boolean
  tags: Record<string, unknown>
  comments: TicketComment[]
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
  tickets: (day: TicketDay) => request<Ticket[]>(`/api/tickets/${day}`),
  setTicketCompletion: (ticketId: number, day: TicketDay, completed: boolean, csrfToken: string) =>
    request<Ticket>(`/api/tickets/${ticketId}/completion`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
      body: JSON.stringify({ day, completed }),
    }),
  connectDomofon: (serviceLogin: string, csrfToken: string) => request<DomofonOperationResult>('/api/domofon/connect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify({ service_login: serviceLogin }),
  }),
  addresses: () => request<DomofonAddress[]>('/api/domofon/addresses'),
  createDomofon: (body: DomofonCreatePayload, csrfToken: string) => request<DomofonOperationResult>('/api/domofon/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify(body),
  }),
}
