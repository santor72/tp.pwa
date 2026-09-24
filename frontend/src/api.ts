export type UserRole = 'admin' | 'manager' | 'user'
export type Capabilities = { payments: boolean; gis: boolean; connection_photos: boolean; gis_photos: boolean; messenger_settings: boolean; all_tickets: boolean }

export type UserProfile = {
  id: number | string
  email: string
  first_name: string | null
  last_name: string | null
  status: string | null
  role: UserRole
  user_permissions: Record<string, unknown>
}

export type Session = { user: UserProfile; csrf_token: string; capabilities: Capabilities; payment_telemetry_enabled?: boolean }
export type PaymentAddress = { locid: number; loctext: string }
export type PaymentProduct = { product_id: number; title: string; default_amount: string; currency: string; price_override_allowed: boolean }
export type PaymentCandidate = { entity_type: 'contact' | 'lead'; entity_id: number; display_name: string; phone_hint?: string | null; matched_by?: 'address' | 'address_conflict' | null; action?: 'keep_contact_address' | 'apply_selected_address' | null }
export type PaymentStatus = 'draft' | 'resolving_client' | 'client_selection_required' | 'client_resolved' | 'invoice_created' | 'product_added' | 'payment_created' | 'link_created' | 'send_queued' | 'sent' | 'paid' | 'send_failed' | 'failed' | 'expired' | 'canceled'
export type PaymentAccepted = { id: string; status: PaymentStatus }
export type PaymentTransaction = PaymentAccepted & {
  current_step: string
  pending_commands?: string[]
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
  email?: string
  amount: string
}
export type TicketDay = 'today' | 'tomorrow'
export type TicketFilterMaster = { id: string; name: string }
export type TicketFilterBrigade = { id: string; name: string; master_ids: string[] }
export type TicketFilters = { masters: TicketFilterMaster[]; brigades: TicketFilterBrigade[] }
export type TicketFilterSelection = { brigadeIds: string[]; masterIds: string[] }
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
export type GisMap = { id: string; name: string; created_at: string; report: { total: number } }
export type GisLayer = { id: string; name: string; position: number; count: number; version: number }
export type GisFeatureStyle = {
  iconColor?: string; iconId?: string | null; iconScale?: number; markerShape?: 'pin' | 'circle'; recolorIcon?: boolean
  lineColor?: string; lineWidth?: number; lineOpacity?: number
  fillColor?: string; fillOpacity?: number
}
export type GisFeature = { type: 'Feature'; id: string; geometry: { type: 'Point' | 'LineString' | 'Polygon'; coordinates: unknown }; properties: { id: string; layer_id: string; title?: string; number?: number; kind: string } & GisFeatureStyle }
export type GisFeatureCollection = { type: 'FeatureCollection'; truncated: boolean; limit: number; features: GisFeature[] }
export type GisFeatureDetails = { id: string; layer_id: string; map_id: string; layer_name: string; title: string; number: number; kind: string; description: string; geometry: GisFeature['geometry']; style: Record<string, unknown>; version: number }
export type GisReportReceipt = { id: string; external_report_id: string; repeated: boolean; retention_until: string | null }
export type GisMapReport = { featureId: string; externalReportId: string; completionId: string; text: string; photos: File[] }
export type ConnectionCompletion = { id: string; ticket_id: number; completion_status: string; gis_status: string; gis_report_id: string | null; error_code: string | null; error_message: string | null; created_at: string; updated_at: string }
export type ConnectionCompletionPayload = { day: TicketDay; ticketKind?: Ticket['kind']; idempotencyKey: string; techportalText: string; gisText: string; featureId?: string; photos: File[] }

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
    if (response.status === 413) {
      throw new ApiError(413, data.code ?? 'REQUEST_TOO_LARGE', data.message ?? 'Размер вложений превышает допустимый для отправки. Уменьшите размер или количество фотографий.')
    }
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
  tickets: (day: TicketDay, scope: 'assigned' | 'all' = 'assigned', filter: TicketFilterSelection = { brigadeIds: [], masterIds: [] }) => {
    const query = new URLSearchParams()
    if (scope === 'all') query.set('scope', 'all')
    if (scope === 'all' && filter.brigadeIds.length) query.set('brigade_ids', filter.brigadeIds.join(','))
    if (scope === 'all' && filter.masterIds.length) query.set('master_ids', filter.masterIds.join(','))
    const suffix = query.toString()
    return request<Ticket[]>(`/api/tickets/${day}${suffix ? `?${suffix}` : ''}`)
  },
  ticketFilters: () => request<TicketFilters>('/api/tickets/filters'),
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
  paymentTiming: (id: string, body: { accepted_ms?: number; link_ms?: number; qr_ms: number; qr_error: boolean; background: boolean; restored: boolean }, csrfToken: string) => request<void>(`/api/payments/${id}/timing`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken }, body: JSON.stringify(body),
  }),
  selectPaymentClient: (id: string, candidate: PaymentCandidate, csrfToken: string) => request<PaymentTransaction>(`/api/payments/${id}/client-selection`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify({ entity_type: candidate.entity_type, entity_id: candidate.entity_id, action: candidate.action }),
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
  gisMaps: () => request<{ rows: GisMap[] }>('/api/gis/maps'),
  gisBasemap: () => request<{ provider: 'yandex'; scriptUrl: string | null }>('/api/gis/basemap'),
  gisLayers: (mapId: string) => request<{ rows: GisLayer[] }>(`/api/gis/maps/${mapId}/layers`),
  gisBounds: (mapId: string) => request<{ xmin: number; ymin: number; xmax: number; ymax: number }>(`/api/gis/maps/${mapId}/bounds`),
  gisFeatures: (mapId: string, bbox: [number, number, number, number], layers: string[]) => request<GisFeatureCollection>(`/api/gis/maps/${mapId}/features?bbox=${bbox.join(',')}${layers.length ? `&layers=${encodeURIComponent(layers.join(','))}` : ''}`),
  gisFeature: (featureId: string) => request<GisFeatureDetails>(`/api/gis/features/${featureId}`),
  createGisReport: (report: GisMapReport, csrfToken: string) => {
    const body = new FormData()
    body.set('feature_id', report.featureId)
    body.set('external_report_id', report.externalReportId)
    body.set('completion_id', report.completionId)
    body.set('text', report.text)
    report.photos.forEach(photo => body.append('photos', photo, photo.name))
    return request<GisReportReceipt>('/api/gis/reports', { method: 'POST', headers: { 'X-CSRF-Token': csrfToken }, body })
  },
  completeConnection: (ticketId: number, payload: ConnectionCompletionPayload, csrfToken: string) => {
    const body = new FormData()
    body.set('day', payload.day)
    body.set('ticket_kind', payload.ticketKind ?? 'connection')
    body.set('idempotency_key', payload.idempotencyKey)
    body.set('techportal_text', payload.techportalText)
    body.set('gis_text', payload.gisText)
    if (payload.featureId) body.set('feature_id', payload.featureId)
    payload.photos.forEach(photo => body.append('photos', photo, photo.name))
    const endpoint = payload.ticketKind === 'repair' ? 'ticket-completion' : 'connection-completion'
    return request<ConnectionCompletion>(`/api/tickets/${ticketId}/${endpoint}`, { method: 'POST', headers: { 'X-CSRF-Token': csrfToken }, body })
  },
  connectionCompletion: (operationId: string) => request<ConnectionCompletion>(`/api/tickets/connection-completions/${operationId}`),
}
