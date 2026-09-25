import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../src/App'

const session = {
  payment_telemetry_enabled: true,
  user: {
    id: 3,
    email: 'user@example.test',
    first_name: 'Иван',
    status: 'active',
    role: 'user',
    user_permissions: {
      client: { create: true },
      tickets: { all: true, execution: true },
    },
  },
  csrf_token: 'csrf-test',
  capabilities: { payments: true, gis: true, gis_tickets: true, connection_photos: true, messenger_settings: true, all_tickets: false },
}

const ticket = {
  id: 32412,
  address: 'СНТ Волга, участок 96',
  client_phone: '79254553958',
  client_phones: ['79254553958', '79031234567'],
  client_name: 'Денис Денис',
  description: 'Установить оборудование',
  scheduled_at: '2026-07-30T12:00:00+03:00',
  kind: 'connection',
  completed: false,
  tags: { 'Новое подключение': {} },
  comments: [{
    created_at: '2026-07-30T11:13:21+03:00',
    author: 'Константин',
    text: 'https://files.example/photo.jpg',
  }],
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

async function openPayments() {
  await screen.findByRole('heading', { name: 'Заявки сегодня' })
  fireEvent.click(screen.getByRole('button', { name: /Оплата/ }))
  await screen.findByRole('heading', { name: 'Оплата' })
}

afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

beforeEach(() => {
  let container: HTMLElement | null = null
  const object = () => {
    const handlers: Record<string, () => void> = {}
    return { events: { add: (name: string, handler: () => void) => { handlers[name] = handler } }, _handlers: handlers }
  }
  class Collection {
    items: ReturnType<typeof object>[] = []
    add(item: ReturnType<typeof object>) { this.items.push(item); const marker = document.createElement('button'); marker.className = 'ymaps-feature'; marker.onclick = () => item._handlers.click?.(); container?.append(marker); return this }
    removeAll() { this.items = []; container?.querySelectorAll('.ymaps-feature').forEach(node => node.remove()) }
  }
  class ObjectManager {
    private handlers: Record<string, (event: { get: (name: string) => string }) => void> = {}
    private markers = new globalThis.Map<string, HTMLButtonElement>()
    objects = { events: { add: (name: string, handler: (event: { get: (name: string) => string }) => void) => { this.handlers[name] = handler } } }
    add(feature: { id: string }) {
      const marker = document.createElement('button')
      marker.className = 'ymaps-feature'
      marker.onclick = () => this.handlers.click?.({ get: () => feature.id })
      this.markers.set(feature.id, marker); container?.append(marker)
    }
    remove(ids: string | string[]) { for (const id of Array.isArray(ids) ? ids : [ids]) { this.markers.get(id)?.remove(); this.markers.delete(id) } }
  }
  class Map {
    geoObjects = { add: () => undefined }
    constructor(element: HTMLElement) { container = element }
    getCenter() { return [55.1, 37.2] }
    getBounds() { return [[55, 37.1], [55.2, 37.3]] }
    getZoom() { return 14 }
    setCenter() {} setZoom() {} setType() {} destroy() {} container = { fitToViewport() {} }
    events = { add() {} }
  }
  function GeoObject() { return object() }
  vi.stubGlobal('ymaps', { ready: (callback: () => void) => callback(), Map, ObjectManager, GeoObjectCollection: Collection, Circle: GeoObject, Placemark: GeoObject, Polygon: GeoObject, Polyline: GeoObject })
})

describe('Платёжный терминал', () => {
  it('помечает восстановленный экран и ошибку QR без ошибки для сотрудника', async () => {
    const id = '3a2cf25b-8daa-4c91-9e86-c0aa3722a68c'
    sessionStorage.setItem('tp-pwa:active-payment', id)
    let sent: Record<string, unknown> | undefined
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today' || path === '/api/payments/addresses') return json([])
      if (path.endsWith('/timing')) { sent = JSON.parse(String(init?.body)); throw new Error('offline') }
      if (path === `/api/payments/${id}`) return json({ id, status: 'send_failed', product_title: 'Товар', actual_amount: '100', currency: 'RUB', payment_url: 'https://pay.example/s', payment_qr: 'https://pay.example/qr.png' })
      throw new Error(path)
    }))
    render(<App />)
    await screen.findByRole('heading', { name: 'Заявки сегодня' })
    fireEvent.click(screen.getByRole('button', { name: /Оплата/ }))
    fireEvent.error(await screen.findByRole('img', { name: 'QR-код оплаты' }))
    await waitFor(() => expect(sent).toMatchObject({ restored: true, qr_error: true }))
    expect(screen.queryByText('offline')).toBeNull()
    expect(screen.getByRole('link', { name: 'Открыть ссылку на оплату' })).toBeTruthy()
  })

  it('показывает инструкцию по оплате с первого шага', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/auth/session') return json(session)
      if (String(input) === '/api/tickets/today') return json([])
      if (String(input) === '/api/payments/addresses') return json([])
      throw new Error(`Неожиданный запрос: ${input}`)
    }))

    render(<App />)
    await openPayments()
    fireEvent.click(screen.getByRole('button', { name: 'Как принять оплату' }))

    expect(screen.getByRole('dialog', { name: 'Как принять оплату' })).toBeTruthy()
    expect(screen.getByText(/Отправка СМС пока не работает/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Понятно' }))
    expect(screen.queryByRole('dialog', { name: 'Как принять оплату' })).toBeNull()
  })

  it('скрывает раздел без разрешения tickets.execution', async () => {
    const restrictedSession = {
      ...session,
      user: {
        ...session.user,
        user_permissions: { client: { create: false } },
      },
      capabilities: { ...session.capabilities, payments: false },
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(restrictedSession)
      if (path === '/api/tickets/today') return json([])
      throw new Error(`Неожиданный запрос: ${path}`)
    }))

    render(<App />)

    await screen.findByRole('heading', { name: 'Заявки сегодня' })
    expect(screen.queryByRole('button', { name: /Оплата/ })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Оплата' })).toBeNull()
  })

  it.each([true, false, undefined])('проходит мастер с адресом, телеметрия: %s', async (enabled) => {
    let createRequest: RequestInit | undefined
    let timingRequest: RequestInit | undefined
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/auth/session') return json({ ...session, payment_telemetry_enabled: enabled })
      if (path === '/api/tickets/today') return json([])
      if (path === '/api/payments/addresses') {
        return json([{ locid: 4217, loctext: 'Земская улица, 5' }])
      }
      if (path === '/api/payments/products') return json([{ product_id: 123, title: 'Подключение', default_amount: '1500.00', currency: 'RUB', price_override_allowed: true }])
      if (path === '/api/payments') {
        createRequest = init
        return json({ id: '3a2cf25b-8daa-4c91-9e86-c0aa3722a68c', status: 'draft' }, 202)
      }
      if (path.endsWith('/timing')) { timingRequest = init; return new Response(null, { status: 204 }) }
      if (path === '/api/payments/3a2cf25b-8daa-4c91-9e86-c0aa3722a68c') return json({
        id: '3a2cf25b-8daa-4c91-9e86-c0aa3722a68c', status: 'send_failed', current_step: 'send', send_status: 'send_failed',
        product_title: 'Подключение', catalog_amount: '1500.00', actual_amount: '1700.00', currency: 'RUB',
        payment_url: 'https://pay.example/full', payment_short_url: 'https://pay.example/s', payment_qr: 'data:image/png;base64,AA==',
        candidates: [], error_code: null, error_message: null, created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:01Z',
      })
      throw new Error(`Неожиданный запрос: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    await openPayments()
    fireEvent.click(screen.getByRole('button', { name: /Земская улица, 5/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Подключение/ }))
    fireEvent.change(screen.getByLabelText(/^Фамилия/), { target: { value: 'Иванов' } })
    fireEvent.change(screen.getByLabelText(/^Имя/), { target: { value: 'Иван' } })
    fireEvent.change(screen.getByLabelText(/^Телефон/), { target: { value: '+79990000000' } })
    fireEvent.change(screen.getByLabelText(/^E-mail/), { target: { value: 'ivan@example.com' } })
    fireEvent.change(screen.getByLabelText(/^Квартира/), { target: { value: '12А' } })
    fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }))
    fireEvent.change(screen.getByLabelText(/^Сумма/), { target: { value: '1700.00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сформировать оплату' }))

    expect(await screen.findByRole('heading', { name: 'Ссылка сформирована' })).toBeTruthy()
    expect(screen.getByText(/SMS не настроено/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Открыть ссылку на оплату' }).getAttribute('href')).toBe('https://pay.example/s')
    expect(JSON.parse(String(createRequest?.body))).toMatchObject({
      address: { locid: 4217, loctext: 'Земская улица, 5' }, apartment: '12А', product_id: 123,
      first_name: 'Иван', last_name: 'Иванов', amount: '1700.00',
      phone: '+79990000000',
      email: 'ivan@example.com',
    })
    expect(JSON.parse(String(createRequest?.body)).idempotency_key).toMatch(/^[0-9a-f-]{36}$/)
    expect(new Headers(createRequest?.headers).get('X-CSRF-Token')).toBe('csrf-test')
    const qr = screen.getByRole('img', { name: 'QR-код оплаты' })
    fireEvent.load(qr)
    fireEvent.load(qr)
    if (!enabled) {
      expect(timingRequest).toBeUndefined()
      expect(fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/timing'))).toHaveLength(0)
      return
    }
    await waitFor(() => expect(timingRequest).toBeTruthy())
    const timingBody = JSON.parse(String(timingRequest?.body))
    expect(timingBody.restored).toBe(false)
    expect(timingBody.qr_error).toBe(false)
    expect(timingBody.accepted_ms).toBeLessThanOrEqual(timingBody.link_ms)
    expect(timingBody.link_ms).toBeLessThanOrEqual(timingBody.qr_ms)
    expect(new Headers(timingRequest?.headers).get('X-CSRF-Token')).toBe('csrf-test')
    expect(fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/timing'))).toHaveLength(1)
  })

  it('пропускает адрес и не показывает квартиру', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([])
      if (path === '/api/payments/addresses') return json([])
      if (path === '/api/payments/products') return json([{ product_id: 123, title: 'Услуга', default_amount: '10.00', currency: 'RUB', price_override_allowed: false }])
      throw new Error(`Неожиданный запрос: ${path}`)
    }))

    render(<App />)
    await openPayments()
    fireEvent.click(screen.getByRole('button', { name: 'Пропустить адрес' }))
    fireEvent.click(await screen.findByRole('button', { name: /Услуга/ }))
    expect(screen.queryByLabelText(/^Квартира/)).toBeNull()
  })

  it('фильтрует адреса без учёта регистра', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([])
      if (path === '/api/payments/addresses') return json([{ locid: 1, loctext: 'Земская улица' }, { locid: 2, loctext: 'Лесная улица' }])
      throw new Error(`Неожиданный запрос: ${path}`)
    }))

    render(<App />)
    await openPayments()
    await screen.findByRole('button', { name: /Земская/ })
    fireEvent.change(screen.getByPlaceholderText('Поиск по адресу'), { target: { value: 'ЛЕСНАЯ' } })
    expect(screen.queryByRole('button', { name: /Земская/ })).toBeNull()
    expect(screen.getByRole('button', { name: /Лесная/ })).toBeTruthy()
  })

  it.each(['cancel', 'resend'])('восстанавливает ожидание %s и блокирует повторные команды', async command => {
    const id = '3a2cf25b-8daa-4c91-9e86-c0aa3722a68c'
    sessionStorage.setItem('tp-pwa:active-payment', id)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today' || path === '/api/payments/addresses') return json([])
      if (path === `/api/payments/${id}`) return json({
        id, status: 'sent', current_step: 'send', pending_commands: [command], send_status: 'sent',
        product_title: 'Услуга', catalog_amount: '10.00', actual_amount: '10.00', currency: 'RUB',
        payment_url: 'https://pay.example/test', payment_short_url: null, payment_qr: 'data:image/png;base64,AA==',
        candidates: [], error_code: null, error_message: null,
        created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:01Z',
      })
      throw new Error(`Неожиданный запрос: ${path}`)
    }))
    render(<App />)
    await openPayments()
    expect(await screen.findByText(command === 'cancel' ? /Отмена принята/ : /Повторная отправка принята/)).toBeTruthy()
    if (command === 'cancel') {
      expect(screen.queryByRole('img', { name: 'QR-код оплаты' })).toBeNull()
      expect(screen.queryByRole('link', { name: 'Открыть ссылку на оплату' })).toBeNull()
    } else {
      expect(screen.getByRole('img', { name: 'QR-код оплаты' })).toBeTruthy()
    }
    for (const name of ['Отправить повторно', 'Отменить оплату', 'Новая оплата']) {
      expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true)
    }
  })

  it.each(['select', 'resume'])('показывает очередь %s и продолжает polling до QR', async command => {
    const id = '3a2cf25b-8daa-4c91-9e86-c0aa3722a68c'
    sessionStorage.setItem('tp-pwa:active-payment', id)
    let finished = false
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today' || path === '/api/payments/addresses') return json([])
      if (path === `/api/payments/${id}`) return json({
        id, status: finished ? 'send_queued' : 'resolving_client', current_step: finished ? 'send' : `${command}_queued`,
        pending_commands: finished ? [] : [command], send_status: null,
        product_title: 'Услуга', catalog_amount: '10.00', actual_amount: '10.00', currency: 'RUB',
        payment_url: finished ? 'https://pay.example/test' : null, payment_short_url: null,
        payment_qr: finished ? 'data:image/png;base64,AA==' : null,
        candidates: [], error_code: null, error_message: null,
        created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:01Z',
      })
      throw new Error(`Неожиданный запрос: ${path}`)
    }))
    render(<App />)
    await openPayments()
    expect(await screen.findByText(command === 'select' ? /Клиент выбран/ : /Повторная обработка принята/)).toBeTruthy()
    expect(screen.queryByRole('img', { name: 'QR-код оплаты' })).toBeNull()
    finished = true
    expect(await screen.findByRole('img', { name: 'QR-код оплаты' }, { timeout: 4000 })).toBeTruthy()
    expect(screen.queryByText(/Ожидаем продолжения формирования оплаты/)).toBeNull()
  })

  it('останавливает polling и показывает ID окончательно неуспешной операции', async () => {
    const id = '3a2cf25b-8daa-4c91-9e86-c0aa3722a68c'
    sessionStorage.setItem('tp-pwa:active-payment', id)
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([])
      if (path === '/api/payments/addresses') return json([])
      if (path === `/api/payments/${id}`) return json({
        id, status: 'failed', current_step: 'payment_created', send_status: null,
        product_title: 'Услуга', catalog_amount: '10.00', actual_amount: '10.00', currency: 'RUB',
        payment_url: null, payment_short_url: null, payment_qr: null, candidates: [],
        error_code: 'PAYMENT_PROCESSING_FAILED', error_message: 'Внешняя операция временно не выполнена',
        created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:01Z',
      })
      throw new Error(`Неожиданный запрос: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await openPayments()

    expect(await screen.findByRole('heading', { name: 'Оплата не сформирована' })).toBeTruthy()
    expect(screen.getByText(`ID операции: ${id}`)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Отправить повторно' })).toBeNull()
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(fetchMock.mock.calls.filter(call => String(call[0]) === `/api/payments/${id}`)).toHaveLength(1)
  })
})

describe('Карта сети', () => {
  it('получает карту только через внутренний API и показывает объекты выбранных слоёв', async () => {
    const mapId = '11111111-1111-4111-8111-111111111111'
    const layerId = '22222222-2222-4222-8222-222222222222'
    const featureId = '33333333-3333-4333-8333-333333333333'
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([])
      if (path === '/api/gis/basemap') return json({ provider: 'yandex', scriptUrl: 'https://api-maps.yandex.ru/2.1/?apikey=test&lang=ru_RU&csp=true' })
      if (path === '/api/gis/maps') return json({ rows: [{ id: mapId, name: 'Чехов', created_at: '2026-09-16T09:00:00Z', report: { total: 1 } }] })
      if (path === `/api/gis/maps/${mapId}/layers`) return json({ rows: [{ id: layerId, name: 'Муфты', position: 1, count: 1, version: 1 }] })
      if (path === `/api/gis/maps/${mapId}/bounds`) return json({ xmin: 37.1, ymin: 55, xmax: 37.3, ymax: 55.2 })
      if (path.startsWith(`/api/gis/maps/${mapId}/features?bbox=`)) return json({ type: 'FeatureCollection', truncated: false, limit: 40000, features: [{ type: 'Feature', id: featureId, geometry: { type: 'Point', coordinates: [37.2, 55.1] }, properties: { id: featureId, layer_id: layerId, kind: 'Point', title: 'Муфта 1', iconColor: '#0288d1' } }] })
      if (path === `/api/gis/features/${featureId}`) return json({ id: featureId, layer_id: layerId, map_id: mapId, layer_name: 'Муфты', title: 'Муфта 1', number: 42, kind: 'Point', description: 'Адрес: Чехов<br>Кол-во подъездов: 2', geometry: { type: 'Point', coordinates: [37.2, 55.1] }, style: {}, version: 3 })
      throw new Error(`Неожиданный запрос: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    await screen.findByRole('heading', { name: 'Заявки сегодня' })
    fireEvent.click(screen.getByRole('button', { name: 'Карта' }))
    expect(await screen.findByRole('heading', { name: 'Карта сети' })).toBeTruthy()
    expect(await screen.findByRole('application', { name: 'Карта сети' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Увеличить масштаб' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Уменьшить масштаб' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Показать загруженные объекты' })).toBeTruthy()
    const basemapPicker = screen.getByRole('combobox', { name: 'Подложка карты' })
    await waitFor(() => expect(basemapPicker.disabled).toBe(false))
    fireEvent.change(basemapPicker, { target: { value: 'hybrid' } })
    expect(localStorage.getItem('tp-pwa.gis.basemap')).toBe('hybrid')
    const map = screen.getByRole('application', { name: 'Карта сети' })
    await waitFor(() => expect(map.querySelector('.ymaps-feature')).not.toBeNull())
    fireEvent.click(map.querySelector('.ymaps-feature')!)
    expect(await screen.findByText('Муфта 1')).toBeTruthy()
    expect(await screen.findByRole('dialog', { name: 'Карточка объекта' })).toBeTruthy()
    expect(await screen.findByText(/Адрес: Чехов/)).toBeTruthy()
    expect(screen.queryByText(/<br>/)).toBeNull()
    expect(fetchMock.mock.calls.some(([path]) => String(path).startsWith('https://'))).toBe(false)
  })

  it('отправляет текстовый отчёт выбранного объекта через защищённый маршрут PWA', async () => {
    const mapId = '11111111-1111-4111-8111-111111111111'
    const layerId = '22222222-2222-4222-8222-222222222222'
    const featureId = '33333333-3333-4333-8333-333333333333'
    let reportRequest: RequestInit | undefined
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([])
      if (path === '/api/gis/basemap') return json({ provider: 'yandex', scriptUrl: 'https://api-maps.yandex.ru/2.1/?apikey=test&lang=ru_RU&csp=true' })
      if (path === '/api/gis/maps') return json({ rows: [{ id: mapId, name: 'Чехов', created_at: '2026-09-16T09:00:00Z', report: { total: 1 } }] })
      if (path === `/api/gis/maps/${mapId}/layers`) return json({ rows: [{ id: layerId, name: 'Муфты', position: 1, count: 1, version: 1 }] })
      if (path === `/api/gis/maps/${mapId}/bounds`) return json({ xmin: 37.1, ymin: 55, xmax: 37.3, ymax: 55.2 })
      if (path.startsWith(`/api/gis/maps/${mapId}/features?bbox=`)) return json({ type: 'FeatureCollection', truncated: false, limit: 40000, features: [{ type: 'Feature', id: featureId, geometry: { type: 'Point', coordinates: [37.2, 55.1] }, properties: { id: featureId, layer_id: layerId, kind: 'Point', title: 'Муфта 1', iconColor: '#0288d1' } }] })
      if (path === `/api/gis/features/${featureId}`) return json({ id: featureId, layer_id: layerId, map_id: mapId, layer_name: 'Муфты', title: 'Муфта 1', number: 42, kind: 'Point', description: '', geometry: { type: 'Point', coordinates: [37.2, 55.1] }, style: {}, version: 3 })
      if (path === '/api/gis/reports') { reportRequest = init; return json({ id: 'report-id', external_report_id: 'external-id', repeated: false, retention_until: null }, 201) }
      throw new Error(`Неожиданный запрос: ${path}`)
    }))
    render(<App />)
    await screen.findByRole('heading', { name: 'Заявки сегодня' })
    fireEvent.click(screen.getByRole('button', { name: 'Карта' }))
    const map = await screen.findByRole('application', { name: 'Карта сети' })
    await waitFor(() => expect(map.querySelector('.ymaps-feature')).not.toBeNull())
    fireEvent.click(map.querySelector('.ymaps-feature')!)
    const text = await screen.findByLabelText('Описание работ')
    fireEvent.change(text, { target: { value: 'Заменили муфту' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить отчёт' }))
    expect(await screen.findByText('Отчёт отправлен')).toBeTruthy()
    const body = reportRequest?.body as FormData
    expect(body.get('feature_id')).toBe(featureId)
    expect(body.get('text')).toBe('Заменили муфту')
    expect(body.get('external_report_id')).toMatch(/^[0-9a-f-]{36}$/)
    expect(new Headers(reportRequest?.headers).get('X-CSRF-Token')).toBe('csrf-test')
  })
})

describe('Capabilities', () => {
  it('скрывает карту и выбор объекта GIS для роли без GIS capability', async () => {
    const restrictedSession = { ...session, capabilities: { ...session.capabilities, gis: false, gis_tickets: false } }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/auth/session') return json(restrictedSession)
      if (String(input) === '/api/tickets/today') return json([ticket])
      throw new Error(`Неожиданный запрос: ${input}`)
    }))

    render(<App />)

    await screen.findByRole('heading', { name: 'Заявки сегодня' })
    expect(screen.queryByRole('button', { name: 'Карта' })).toBeNull()
    fireEvent.click(await screen.findByRole('button', { name: /СНТ Волга/ }))
    expect(screen.queryByRole('button', { name: 'Выбрать объект на карте' })).toBeNull()
  })

  it('показывает выбор объекта в заявке без доступа к экрану карты', async () => {
    const restrictedSession = { ...session, capabilities: { ...session.capabilities, gis: false, gis_tickets: true } }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/auth/session') return json(restrictedSession)
      if (String(input) === '/api/tickets/today') return json([ticket])
      throw new Error(`Неожиданный запрос: ${input}`)
    }))

    render(<App />)

    await screen.findByRole('heading', { name: 'Заявки сегодня' })
    expect(screen.queryByRole('button', { name: 'Карта' })).toBeNull()
    fireEvent.click(await screen.findByRole('button', { name: /СНТ Волга/ }))
    expect(screen.getByRole('button', { name: 'Выбрать объект на карте' })).toBeTruthy()
  })

  it('скрывает фотографии подключения без S3 capability', async () => {
    const restrictedSession = { ...session, capabilities: { ...session.capabilities, connection_photos: false } }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/auth/session') return json(restrictedSession)
      if (String(input) === '/api/tickets/today') return json([ticket])
      throw new Error(`Неожиданный запрос: ${input}`)
    }))

    render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: /СНТ Волга/ }))
    expect(screen.queryByLabelText('Фотографии выполнения')).toBeNull()
    expect(screen.getByText('Отчёт для ТехПортала').parentElement?.textContent).not.toContain('*')
  })

  it('оставляет настройки карты, когда messenger_settings отключён', async () => {
    const restrictedSession = { ...session, capabilities: { ...session.capabilities, messenger_settings: false } }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/auth/session') return json(restrictedSession)
      if (String(input) === '/api/tickets/today') return json([])
      throw new Error(`Неожиданный запрос: ${input}`)
    }))

    render(<App />)

    await screen.findByRole('heading', { name: 'Заявки сегодня' })
    fireEvent.click(screen.getByRole('button', { name: 'Настройки' }))
    expect(await screen.findByLabelText('Высота карты')).toBeTruthy()
    expect(screen.queryByText('Telegram')).toBeNull()
  })

  it('запрашивает общий список только после включения переключателя', async () => {
    const managerSession = {
      ...session,
      user: { ...session.user, role: 'manager' },
      capabilities: { ...session.capabilities, all_tickets: true },
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(managerSession)
      if (path === '/api/tickets/today') return json([])
      if (path === '/api/tickets/today?scope=all') return json([{
        ...ticket,
        assigned_masters: ['Иван Иванов', 'Пётр Петров'],
        can_change_completion: false,
      }])
      throw new Error(`Неожиданный запрос: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    await screen.findByRole('heading', { name: 'Заявки сегодня' })
    fireEvent.click(screen.getByLabelText('Все заявки'))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/tickets/today?scope=all', expect.any(Object)))
    expect(await screen.findByText('Назначены: Иван Иванов, Пётр Петров')).toBeTruthy()
    expect(screen.queryByText('Удерживайте карточку, чтобы изменить статус')).toBeNull()
    expect(localStorage.getItem('tp-pwa:tickets-scope')).toBe('all')
  })

  it('передаёт выбранную бригаду в серверный фильтр общего списка', async () => {
    const managerSession = {
      ...session,
      user: { ...session.user, role: 'manager' },
      capabilities: { ...session.capabilities, all_tickets: true },
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(managerSession)
      if (path === '/api/tickets/today' || path === '/api/tickets/today?scope=all') return json([])
      if (path === '/api/tickets/filters') return json({
        masters: [{ id: '87', name: 'Иван Иванов' }],
        brigades: [{ id: '4', name: 'Монтажники', master_ids: ['87'] }],
      })
      if (path === '/api/tickets/today?scope=all&brigade_ids=4') return json([{
        ...ticket, assigned_masters: ['Иван Иванов'], can_change_completion: false,
      }])
      throw new Error(`Неожиданный запрос: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    fireEvent.click(await screen.findByLabelText('Все заявки'))
    fireEvent.click(await screen.findByRole('button', { name: 'Фильтр' }))
    const brigades = await screen.findByLabelText('Бригады')
    fireEvent.change(brigades, { target: { value: '4' } })
    expect(fetchMock).not.toHaveBeenCalledWith('/api/tickets/today?scope=all&brigade_ids=4', expect.any(Object))
    fireEvent.click(screen.getByRole('button', { name: 'Применить' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/tickets/today?scope=all&brigade_ids=4', expect.any(Object)))
    expect(await screen.findByRole('button', { name: /СНТ Волга/ })).toBeTruthy()
    expect(localStorage.getItem('tp-pwa:tickets-filter:3')).toContain('4')
  })
})

describe('Фильтр закрытых заявок', () => {
  it('по умолчанию скрывает исполненные заявки и сохраняет выбор', async () => {
    const completedTicket = { ...ticket, completed: true }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/auth/session') return json(session)
      if (String(input) === '/api/tickets/today') return json([completedTicket])
      throw new Error(`Неожиданный запрос: ${input}`)
    }))

    render(<App />)

    expect(await screen.findByText('Неисполненных заявок нет')).toBeTruthy()
    const checkbox = screen.getByLabelText('Закрытые') as HTMLInputElement
    expect(checkbox.checked).toBe(false)
    fireEvent.click(checkbox)
    expect(await screen.findByText(ticket.address)).toBeTruthy()
    expect(localStorage.getItem('tp-pwa:show-closed-tickets')).toBe('true')
  })
})

describe('Привязка Telegram', () => {
  it('создаёт deep-link из PWA', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([])
      if (path === '/api/messenger-links') return json([])
      if (path === '/api/messenger-links/telegram') return json({
        provider: 'telegram', deep_link: 'https://t.me/example_bot?start=token', expires_at: '2026-07-31T13:00:00Z',
      })
      throw new Error(`Неожиданный запрос: ${path}`)
    }))

    render(<App />)
    await screen.findByRole('heading', { name: 'Заявки сегодня' })
    fireEvent.click(screen.getByRole('button', { name: /Настройки/ }))
    expect(await screen.findByRole('heading', { name: 'Настройки' })).toBeTruthy()
    expect(await screen.findByRole('button', { name: 'Подключить Telegram' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Подключить Telegram' }))
    expect((await screen.findByRole('link', { name: 'Открыть Telegram' })).getAttribute('href')).toBe('https://t.me/example_bot?start=token')
  })
})

describe('Заявки', () => {
  it('показывает карточку и открывает подробности коротким нажатием', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([ticket])
      throw new Error(`Неожиданный запрос: ${path}`)
    }))

    render(<App />)

    const card = await screen.findByRole('button', { name: /СНТ Волга/ })
    expect(card.className).toContain('ticket-connection')
    expect(screen.getByText(/Назначено:/).textContent).toContain('12:00')
    expect(screen.getByRole('button', { name: '79254553958' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '79031234567' })).toBeTruthy()
    fireEvent.click(card)

    expect(await screen.findByRole('heading', { name: 'Заявка №32412' })).toBeTruthy()
    expect(screen.getByText('Денис Денис')).toBeTruthy()
    expect(screen.getByText('Установить оборудование')).toBeTruthy()
    expect(screen.getByText('Константин')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'https://files.example/photo.jpg' })).toBeTruthy()
  })

  it('запрашивает дозвон через станцию с CSRF-защитой', async () => {
    let dialRequest: RequestInit | undefined
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([ticket])
      if (path === '/api/conversations/dial') {
        dialRequest = init
        return new Response(null, { status: 204 })
      }
      throw new Error(`Неожиданный запрос: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '79031234567' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/conversations/dial', expect.any(Object)))
    expect(JSON.parse(String(dialRequest?.body))).toEqual({ phone: '79031234567' })
    expect(new Headers(dialRequest?.headers).get('X-CSRF-Token')).toBe('csrf-test')
    expect(screen.getByRole('status').textContent).toContain('Ожидайте звонка')
  })

  it('загружает отдельный список для вкладки «Завтра»', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([ticket])
      if (path === '/api/tickets/tomorrow') return json([])
      throw new Error(`Неожиданный запрос: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await screen.findByRole('button', { name: /СНТ Волга/ })
    fireEvent.click(screen.getByRole('button', { name: /Завтра/ }))

    expect(await screen.findByRole('heading', { name: 'Заявки завтра' })).toBeTruthy()
    expect(await screen.findByText('Неисполненных заявок нет')).toBeTruthy()
    expect(fetchMock).toHaveBeenCalledWith('/api/tickets/tomorrow', expect.any(Object))
  })

  it('долгим нажатием открывает форму отчёта подключения', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([ticket])
      throw new Error(`Неожиданный запрос: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    const card = await screen.findByRole('button', { name: /СНТ Волга/ })
    vi.useFakeTimers()
    fireEvent.pointerDown(card)
    await vi.advanceTimersByTimeAsync(600)
    vi.useRealTimers()

    expect(await screen.findByRole('heading', { name: 'Заявка №32412' })).toBeTruthy()
    expect(screen.getByLabelText('Отчёт для ТехПортала')).toBeTruthy()
    expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith('/completion'))).toBe(false)
  })

  it('отправляет подключение без текста и фото защищённым multipart-запросом', async () => {
    let completionRequest: RequestInit | undefined
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([ticket])
      if (path === '/api/tickets/32412/connection-completion') {
        completionRequest = init
        return json({ id: '11111111-1111-4111-8111-111111111111', ticket_id: 32412, completion_status: 'completed', gis_status: 'not_requested', gis_report_id: null, error_code: null, error_message: null, created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z' })
      }
      throw new Error(`Неожиданный запрос: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /СНТ Волга/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Отметить выполненной' }))
    expect(await screen.findByText('Заявка отмечена выполненной. Отчёт добавлен в ТехПортал.')).toBeTruthy()
    const form = completionRequest?.body as FormData
    expect(form.get('day')).toBe('today')
    expect(form.get('techportal_text')).toBe('')
    expect(form.get('gis_text')).toBe('')
    expect(form.get('idempotency_key')).toMatch(/^[0-9a-f-]{36}$/)
    expect(new Headers(completionRequest?.headers).get('X-CSRF-Token')).toBe('csrf-test')
  })

  it('завершает ремонт тем же отчётом, требуя текст и предлагая GIS и фото', async () => {
    const repair = { ...ticket, kind: 'repair', tags: { 'Заявка на выезд': {} } }
    let completionRequest: RequestInit | undefined
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([repair])
      if (path === '/api/tickets/32412/ticket-completion') {
        completionRequest = init
        return json({ id: '11111111-1111-4111-8111-111111111111', ticket_id: 32412, completion_status: 'completed', gis_status: 'not_requested', gis_report_id: null, error_code: null, error_message: null, created_at: '2026-09-23T10:00:00Z', updated_at: '2026-09-23T10:00:00Z' })
      }
      throw new Error(`Неожиданный запрос: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /СНТ Волга/ }))

    expect(screen.getByLabelText('Что выполнено')).toBeTruthy()
    expect(screen.getByText('Объект GIS — необязательно')).toBeTruthy()
    expect(screen.getByLabelText('Фотографии выполнения')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Завершить ремонт' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('Что выполнено'), { target: { value: 'Заменили кабель' } })
    fireEvent.click(screen.getByRole('button', { name: 'Завершить ремонт' }))

    expect(await screen.findByText('Заявка отмечена выполненной. Отчёт добавлен в ТехПортал.')).toBeTruthy()
    const form = completionRequest?.body as FormData
    expect(form.get('ticket_kind')).toBe('repair')
    expect(form.get('techportal_text')).toBe('Заменили кабель')
    expect(form.get('gis_text')).toBe('')
  })

  it('выбирает объект подключения полноценной картой и запоминает карту', async () => {
    const mapId = '11111111-1111-4111-8111-111111111111'
    const layerId = '22222222-2222-4222-8222-222222222222'
    const featureId = '33333333-3333-4333-8333-333333333333'
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([ticket])
      if (path === '/api/gis/basemap') return json({ provider: 'yandex', scriptUrl: 'https://api-maps.yandex.ru/2.1/?apikey=test&lang=ru_RU&csp=true' })
      if (path === '/api/gis/maps') return json({ rows: [{ id: mapId, name: 'Чехов', created_at: '2026-09-16T09:00:00Z', report: { total: 1 } }] })
      if (path === `/api/gis/maps/${mapId}/layers`) return json({ rows: [{ id: layerId, name: 'Муфты', position: 1, count: 1, version: 1 }] })
      if (path === `/api/gis/maps/${mapId}/bounds`) return json({ xmin: 37.1, ymin: 55, xmax: 37.3, ymax: 55.2 })
      if (path.startsWith(`/api/gis/maps/${mapId}/features?bbox=`)) return json({ type: 'FeatureCollection', truncated: false, limit: 40000, features: [{ type: 'Feature', id: featureId, geometry: { type: 'Point', coordinates: [37.2, 55.1] }, properties: { id: featureId, layer_id: layerId, kind: 'Point', title: 'Муфта 1', iconColor: '#0288d1' } }] })
      if (path === `/api/gis/features/${featureId}`) return json({ id: featureId, layer_id: layerId, map_id: mapId, layer_name: 'Муфты', title: 'Муфта 1', number: 42, kind: 'Point', description: '', geometry: { type: 'Point', coordinates: [37.2, 55.1] }, style: {}, version: 3 })
      throw new Error(`Неожиданный запрос: ${path}`)
    }))
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /СНТ Волга/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать объект на карте' }))
    const map = await screen.findByRole('application', { name: 'Карта сети' })
    expect(screen.queryByText(/^Слои/)).toBeNull()
    await waitFor(() => expect(map.querySelector('.ymaps-feature')).not.toBeNull())
    fireEvent.click(map.querySelector('.ymaps-feature')!)
    expect(await screen.findByRole('button', { name: 'Выбрать этот объект' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать этот объект' }))
    expect(await screen.findByLabelText('Отчёт для GIS')).toBeTruthy()
    expect(localStorage.getItem('tp-pwa.gis.selected-map')).toBe(mapId)
  })
})
