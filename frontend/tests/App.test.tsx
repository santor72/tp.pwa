import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../src/App'

const session = {
  user: { id: 3, email: 'user@example.test', first_name: 'Иван', status: 'active' },
  csrf_token: 'csrf-test',
}

const ticket = {
  id: 32412,
  address: 'СНТ Волга, участок 96',
  client_phone: '79254553958',
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

async function openDomofon() {
  await screen.findByRole('heading', { name: 'Заявки сегодня' })
  fireEvent.click(screen.getByRole('button', { name: /Домофон/ }))
  await screen.findByRole('heading', { name: 'Домофоны' })
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('Домофоны', () => {
  it('проходит пошаговое создание пользователя и отправляет все поля', async () => {
    let createRequest: RequestInit | undefined
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([])
      if (path === '/api/domofon/addresses') {
        return json([{ locid: 4217, loctext: 'Земская улица, 5' }])
      }
      if (path === '/api/domofon/create') {
        createRequest = init
        return json({ ok: true, reason: 'Создан пользователь 15600453' })
      }
      throw new Error(`Неожиданный запрос: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    await openDomofon()
    expect(screen.queryByLabelText(/^Квартира/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Создать нового пользователя/ }))
    expect(await screen.findByRole('heading', { name: 'Выберите адрес' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /Земская улица, 5/ }))
    expect(await screen.findByRole('heading', { name: 'Новый пользователь' })).toBeTruthy()

    fireEvent.change(screen.getByLabelText(/^Квартира/), { target: { value: '143' } })
    fireEvent.change(screen.getByLabelText(/^Подъезд/), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText(/^ФИО/), { target: { value: 'Иванов Иван' } })
    fireEvent.change(screen.getByLabelText('Номер телефона'), { target: { value: '+79990000000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Создать пользователя' }))

    expect(await screen.findByRole('heading', { name: 'Пользователь создан' })).toBeTruthy()
    expect(screen.getByText('Создан пользователь 15600453')).toBeTruthy()
    expect(JSON.parse(String(createRequest?.body))).toEqual({
      locid: 4217,
      field_flat: 143,
      field_podezd: 2,
      client_name: 'Иванов Иван',
      phone: '+79990000000',
    })
    expect(new Headers(createRequest?.headers).get('X-CSRF-Token')).toBe('csrf-test')
  })

  it('подключает услугу существующему пользователю', async () => {
    let connectRequest: RequestInit | undefined
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([])
      if (path === '/api/domofon/connect') {
        connectRequest = init
        return json({ ok: true, reason: 'Услуга домофона подключена' })
      }
      throw new Error(`Неожиданный запрос: ${path}`)
    }))

    render(<App />)
    await openDomofon()

    fireEvent.change(screen.getByLabelText('Логин клиента'), { target: { value: 'client-10' } })
    fireEvent.click(screen.getByRole('button', { name: 'Подключить услугу' }))

    expect(await screen.findByRole('heading', { name: 'Услуга подключена' })).toBeTruthy()
    expect(screen.getByText('Услуга домофона подключена')).toBeTruthy()
    expect(JSON.parse(String(connectRequest?.body))).toEqual({ service_login: 'client-10' })
  })

  it('не показывает успех при ошибке ESB', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/auth/session') return json(session)
      if (path === '/api/tickets/today') return json([])
      if (path === '/api/domofon/connect') {
        return json({ code: 'ESB_CALL_FAILED', message: 'Пользователь не найден' }, 502)
      }
      throw new Error(`Неожиданный запрос: ${path}`)
    }))

    render(<App />)
    await openDomofon()

    fireEvent.change(screen.getByLabelText('Логин клиента'), { target: { value: 'unknown' } })
    fireEvent.click(screen.getByRole('button', { name: 'Подключить услугу' }))

    expect((await screen.findByRole('alert')).textContent).toContain('Пользователь не найден')
    await waitFor(() => expect(screen.queryByText('Операция выполнена')).toBeNull())
    expect(screen.getByRole('heading', { name: 'Домофоны' })).toBeTruthy()
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
    expect(screen.getByRole('link', { name: '79254553958' }).getAttribute('href')).toBe('tel:79254553958')
    fireEvent.click(card)

    expect(await screen.findByRole('heading', { name: 'Заявка №32412' })).toBeTruthy()
    expect(screen.getByText('Денис Денис')).toBeTruthy()
    expect(screen.getByText('Установить оборудование')).toBeTruthy()
    expect(screen.getByText('Константин')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'https://files.example/photo.jpg' })).toBeTruthy()
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
    expect(await screen.findByText('На этот день заявок нет')).toBeTruthy()
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

    await waitFor(() => expect(card.className).toContain('ticket-completed'))
    expect(JSON.parse(String(completionRequest?.body))).toEqual({ day: 'today', completed: true })
    expect(new Headers(completionRequest?.headers).get('X-CSRF-Token')).toBe('csrf-test')
    expect(screen.queryByRole('heading', { name: 'Заявка №32412' })).toBeNull()
  })
})
