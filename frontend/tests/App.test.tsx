import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../src/App'

const session = {
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
  capabilities: { payments: true, messenger_settings: true, all_tickets: false },
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

describe('Платёжный терминал', () => {
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

  it('проходит мастер с адресом и показывает ссылку при ошибке SMS', async () => {
    let createRequest: RequestInit | undefined
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([])
      if (path === '/api/payments/addresses') {
        return json([{ locid: 4217, loctext: 'Земская улица, 5' }])
      }
      if (path === '/api/payments/products') return json([{ product_id: 123, title: 'Подключение', default_amount: '1500.00', currency: 'RUB', price_override_allowed: true }])
      if (path === '/api/payments') {
        createRequest = init
        return json({ id: '3a2cf25b-8daa-4c91-9e86-c0aa3722a68c', status: 'draft' }, 202)
      }
      if (path === '/api/payments/3a2cf25b-8daa-4c91-9e86-c0aa3722a68c') return json({
        id: '3a2cf25b-8daa-4c91-9e86-c0aa3722a68c', status: 'send_failed', current_step: 'send', send_status: 'send_failed',
        product_title: 'Подключение', catalog_amount: '1500.00', actual_amount: '1700.00', currency: 'RUB',
        payment_url: 'https://pay.example/full', payment_short_url: 'https://pay.example/s', payment_qr: null,
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

describe('Capabilities', () => {
  it('скрывает настройки, когда messenger_settings отключён', async () => {
    const restrictedSession = { ...session, capabilities: { ...session.capabilities, messenger_settings: false } }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/auth/session') return json(restrictedSession)
      if (String(input) === '/api/tickets/today') return json([])
      throw new Error(`Неожиданный запрос: ${input}`)
    }))

    render(<App />)

    await screen.findByRole('heading', { name: 'Заявки сегодня' })
    expect(screen.queryByRole('button', { name: 'Настройки' })).toBeNull()
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

  it('долгим нажатием меняет статус и передаёт CSRF', async () => {
    let completionRequest: RequestInit | undefined
    const completedTicket = {
      ...ticket,
      completed: true,
      tags: { ...ticket.tags, 'Работы произведены': {} },
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([ticket])
      if (path === '/api/tickets/32412/completion') {
        completionRequest = init
        return json(completedTicket)
      }
      throw new Error(`Неожиданный запрос: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    const card = await screen.findByRole('button', { name: /СНТ Волга/ })
    vi.useFakeTimers()
    fireEvent.pointerDown(card)
    await vi.advanceTimersByTimeAsync(600)
    vi.useRealTimers()

    expect(await screen.findByText('Неисполненных заявок нет')).toBeTruthy()
    expect(JSON.parse(String(completionRequest?.body))).toEqual({ day: 'today', completed: true })
    expect(new Headers(completionRequest?.headers).get('X-CSRF-Token')).toBe('csrf-test')
    expect(screen.queryByRole('heading', { name: 'Заявка №32412' })).toBeNull()
  })
})
