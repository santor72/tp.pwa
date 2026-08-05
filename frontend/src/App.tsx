import {
  FormEvent,
  KeyboardEvent,
  PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import {
  api,
  ApiError,
  DomofonAddress,
  DomofonOperationResult,
  Session,
  MessengerLink,
  MessengerLinkCreate,
  Ticket,
  TicketDay,
} from './api'

type AppScreen = 'loading' | 'login' | 'app'
type AppTab = TicketDay | 'domofon' | 'settings'
type DomofonScreen = 'main' | 'connect-result' | 'addresses' | 'create-form' | 'create-result'
const SHOW_CLOSED_TICKETS_KEY = 'tp-pwa:show-closed-tickets'
const TICKETS_SCOPE_KEY = 'tp-pwa:tickets-scope'

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Не удалось связаться с сервером'
}

function BackIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6" /></svg>
}

function LocationIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z" /><circle cx="12" cy="10" r="2.5" /></svg>
}

function Login({ onSession }: { onSession: (session: Session) => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault()
    setLoading(true)
    setError('')
    try {
      onSession(await api.login(email.trim(), password))
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="login-page">
      <section className="login-shell">
        <div className="brand-mark">ТП</div>
        <h1>ТехПортал</h1>
        <p className="login-subtitle">Вход для специалиста</p>
        <form className="panel login-panel" onSubmit={submit}>
          <Field label="Логин">
            <input value={email} onChange={event => setEmail(event.target.value)} autoComplete="username" required />
          </Field>
          <Field label="Пароль">
            <input type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required />
          </Field>
          {error && <ErrorBox text={error} />}
          <button className="primary-button" disabled={loading}>{loading ? 'Вход…' : 'Войти'}</button>
        </form>
      </section>
    </main>
  )
}

function Field({ label, required = false, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return <label className="field"><span>{label}{required && <b> *</b>}</span>{children}</label>
}

function ErrorBox({ text }: { text: string }) {
  return <div role="alert" className="error-box">{text}</div>
}

function Settings({ session }: { session: Session }) {
  const [link, setLink] = useState<MessengerLink | null>(null)
  const [created, setCreated] = useState<MessengerLinkCreate | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function refresh() {
    setLoading(true); setError('')
    try {
      const active = (await api.messengerLinks()).find(item => item.provider === 'telegram') ?? null
      setLink(active)
      if (active) setCreated(null)
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setLoading(false) }
  }
  useEffect(() => { void refresh() }, [])
  async function connect() {
    setLoading(true); setError('')
    try { setCreated(await api.createTelegramLink(session.csrf_token)); setLink(null) }
    catch (cause) { setError(errorMessage(cause)) }
    finally { setLoading(false) }
  }
  async function disconnect() {
    setLoading(true); setError('')
    try { await api.revokeTelegramLink(session.csrf_token); setLink(null); setCreated(null) }
    catch (cause) { setError(errorMessage(cause)) }
    finally { setLoading(false) }
  }
  return <section className="step-content settings-screen">
    <div className="step-heading"><h1>Настройки</h1><p>Подключение рабочих сервисов</p></div>
    <section className="panel telegram-linking">
    <div><h2>Telegram</h2><p>{link ? 'Подключён' : 'Подключите бота к учётной записи'}</p></div>
    {error && <ErrorBox text={error} />}
    {loading && <p>Загрузка…</p>}
    {!loading && link && <><p>{link.username ? `@${link.username}` : link.display_name || 'Подключённый аккаунт'}</p><button className="outline-button" onClick={disconnect}>Отключить</button></>}
    {!loading && !link && !created && <button className="primary-button" onClick={connect}>Подключить Telegram</button>}
    {!loading && created && <><p>Откройте ссылку до {formatDateTime(created.expires_at)}.</p><a className="primary-button telegram-link" href={created.deep_link}>Открыть Telegram</a><button className="outline-button" onClick={refresh}>Проверить статус</button><button className="outline-button" onClick={connect}>Создать новую ссылку</button></>}
    </section>
  </section>
}

function ResultCard({ title, result, onBack }: { title: string; result: DomofonOperationResult; onBack: () => void }) {
  return (
    <section className="step-content">
      <div className="step-heading"><h1>{title}</h1></div>
      <div className="panel result-panel">
        <div className="success-icon">✓</div>
        <div><strong>Операция выполнена</strong><p>{result.reason}</p></div>
      </div>
      <button className="primary-button" onClick={onBack}>На главную</button>
    </section>
  )
}

function Domofon({ session }: { session: Session }) {
  const [screen, setScreen] = useState<DomofonScreen>('main')
  const [serviceLogin, setServiceLogin] = useState('')
  const [locations, setLocations] = useState<DomofonAddress[]>([])
  const [selectedLocation, setSelectedLocation] = useState<DomofonAddress | null>(null)
  const [locationSearch, setLocationSearch] = useState('')
  const [flat, setFlat] = useState('')
  const [entrance, setEntrance] = useState('')
  const [clientName, setClientName] = useState('')
  const [phone, setPhone] = useState('')
  const [result, setResult] = useState<DomofonOperationResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const visibleLocations = useMemo(() => {
    const query = locationSearch.trim().toLocaleLowerCase('ru')
    return query ? locations.filter(item => item.loctext.toLocaleLowerCase('ru').includes(query)) : locations
  }, [locationSearch, locations])

  function reset() {
    setScreen('main')
    setServiceLogin('')
    setLocations([])
    setSelectedLocation(null)
    setLocationSearch('')
    setFlat('')
    setEntrance('')
    setClientName('')
    setPhone('')
    setResult(null)
    setError('')
  }

  async function connect(event: FormEvent) {
    event.preventDefault()
    setLoading(true)
    setError('')
    try {
      const response = await api.connectDomofon(serviceLogin.trim(), session.csrf_token)
      setResult(response)
      setScreen('connect-result')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setLoading(false)
    }
  }

  async function openAddresses() {
    setLoading(true)
    setError('')
    try {
      setLocations(await api.addresses())
      setScreen('addresses')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setLoading(false)
    }
  }

  async function createUser(event: FormEvent) {
    event.preventDefault()
    if (!selectedLocation) return
    const flatNumber = Number(flat)
    const entranceNumber = Number(entrance)
    if (!Number.isInteger(flatNumber) || !Number.isInteger(entranceNumber)) {
      setError('Квартира и подъезд должны быть целыми числами')
      return
    }
    setLoading(true)
    setError('')
    try {
      const response = await api.createDomofon({
        locid: selectedLocation.locid,
        field_flat: flatNumber,
        field_podezd: entranceNumber,
        client_name: clientName.trim(),
        ...(phone.trim() ? { phone: phone.trim() } : {}),
      }, session.csrf_token)
      setResult(response)
      setScreen('create-result')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setLoading(false)
    }
  }

  let content: React.ReactNode

  if (screen === 'connect-result' && result) {
    content = <ResultCard title="Услуга подключена" result={result} onBack={reset} />
  } else if (screen === 'create-result' && result) {
    content = <ResultCard title="Пользователь создан" result={result} onBack={reset} />
  } else if (screen === 'addresses') {
    content = (
      <section className="step-content">
        <div className="step-heading with-back">
          <button className="icon-button" aria-label="Назад" onClick={reset}><BackIcon /></button>
          <div><h1>Выберите адрес</h1><p>Дом, в котором создаётся пользователь</p></div>
        </div>
        <input className="search-input" type="search" placeholder="Поиск по адресу" value={locationSearch} onChange={event => setLocationSearch(event.target.value)} />
        <div className="location-grid">
          {visibleLocations.map(location => (
            <button className="location-card" key={location.locid} onClick={() => { setSelectedLocation(location); setError(''); setScreen('create-form') }}>
              <span className="location-icon"><LocationIcon /></span>
              <strong>{location.loctext}</strong>
              <small>ID: {location.locid}</small>
            </button>
          ))}
        </div>
        {visibleLocations.length === 0 && <div className="panel empty-state">Адреса не найдены</div>}
      </section>
    )
  } else if (screen === 'create-form' && selectedLocation) {
    content = (
      <section className="step-content">
        <div className="step-heading with-back">
          <button className="icon-button" aria-label="Назад" onClick={() => { setError(''); setScreen('addresses') }}><BackIcon /></button>
          <div><h1>Новый пользователь</h1><p>{selectedLocation.loctext}</p></div>
        </div>
        <form className="panel create-form" onSubmit={createUser}>
          <div className="field-row">
            <Field label="Квартира" required><input type="number" step="1" inputMode="numeric" value={flat} onChange={event => setFlat(event.target.value)} required /></Field>
            <Field label="Подъезд" required><input type="number" step="1" inputMode="numeric" value={entrance} onChange={event => setEntrance(event.target.value)} required /></Field>
          </div>
          <Field label="ФИО" required><input value={clientName} onChange={event => setClientName(event.target.value)} autoComplete="name" required /></Field>
          <Field label="Номер телефона"><input type="tel" value={phone} onChange={event => setPhone(event.target.value)} autoComplete="tel" placeholder="+7 999 123-45-67" /></Field>
          {error && <ErrorBox text={error} />}
          <button className="primary-button" disabled={loading}>{loading ? 'Создание…' : 'Создать пользователя'}</button>
        </form>
      </section>
    )
  } else {
    content = (
      <section className="step-content">
        <div className="step-heading"><h1>Домофоны</h1><p>Подключение услуги и создание пользователей</p></div>
        <form className="panel" onSubmit={connect}>
          <Field label="Логин клиента">
            <input value={serviceLogin} onChange={event => setServiceLogin(event.target.value)} placeholder="Введите логин" required />
          </Field>
          <button className="primary-button" disabled={loading || !serviceLogin.trim()}>{loading ? 'Подключение…' : 'Подключить услугу'}</button>
        </form>
        <div className="divider"><span>или</span></div>
        <button className="outline-button" disabled={loading} onClick={openAddresses}>
          <span>＋</span>{loading ? 'Загрузка адресов…' : 'Создать нового пользователя'}
        </button>
        {error && <ErrorBox text={error} />}
      </section>
    )
  }

  return content
}

function formatDateTime(value: string | null): string {
  if (!value) return 'Дата не указана'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('ru-RU', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'Europe/Moscow',
  }).format(parsed)
}

function formatTime(value: string | null): string {
  if (!value) return 'не указано'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return 'не указано'
  return new Intl.DateTimeFormat('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Moscow',
  }).format(parsed)
}

function ticketDateLabel(day: TicketDay): string {
  const date = new Date(Date.now() + (day === 'tomorrow' ? 86_400_000 : 0))
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    timeZone: 'Europe/Moscow',
  }).format(date)
}

function TicketCard({
  ticket,
  busy,
  editable,
  showMasters,
  onOpen,
  onToggle,
}: {
  ticket: Ticket
  busy: boolean
  editable: boolean
  showMasters: boolean
  onOpen: () => void
  onToggle: () => void
}) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const longPressTriggered = useRef(false)
  const [pressed, setPressed] = useState(false)

  function cancelPress() {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    setPressed(false)
  }

  function startPress(event: ReactPointerEvent<HTMLElement>) {
    if (busy || !editable || (event.target as HTMLElement).closest('a')) return
    longPressTriggered.current = false
    setPressed(true)
    timer.current = setTimeout(() => {
      timer.current = null
      longPressTriggered.current = true
      setPressed(false)
      navigator.vibrate?.(40)
      onToggle()
    }, 600)
  }

  function finishPress() {
    cancelPress()
  }

  function openAfterTap() {
    if (longPressTriggered.current) {
      longPressTriggered.current = false
      return
    }
    if (!busy) onOpen()
  }

  function keyboardOpen(event: KeyboardEvent<HTMLElement>) {
    if (event.key === 'Enter') onOpen()
  }

  useEffect(() => cancelPress, [])

  return (
    <article
      className={`ticket-card ticket-${ticket.completed ? 'completed' : ticket.kind}${pressed ? ' is-pressed' : ''}`}
      role="button"
      tabIndex={0}
      aria-label={`Заявка ${ticket.address || ticket.id}`}
      aria-busy={busy}
      onPointerDown={startPress}
      onPointerUp={finishPress}
      onPointerCancel={cancelPress}
      onPointerLeave={cancelPress}
      onClick={openAfterTap}
      onKeyDown={keyboardOpen}
      onContextMenu={event => event.preventDefault()}
    >
      <div className="ticket-card-top">
        <span className="ticket-kind">{ticket.kind === 'connection' ? 'Новое подключение' : 'Ремонт'}</span>
        <span className="ticket-status">{busy ? 'Изменение…' : ticket.completed ? 'Исполнена' : 'Не исполнена'}</span>
      </div>
      <strong className="ticket-address">{ticket.address || 'Адрес не указан'}</strong>
      <time className="ticket-scheduled" dateTime={ticket.scheduled_at || undefined}>
        Назначено: <strong>{formatTime(ticket.scheduled_at)}</strong>
      </time>
      {ticket.client_phone
        ? <a href={`tel:${ticket.client_phone}`} onClick={event => event.stopPropagation()}>{ticket.client_phone}</a>
        : <span className="ticket-muted">Телефон не указан</span>}
      {showMasters && ticket.assigned_masters.length > 0 && <p className="ticket-masters">Назначены: {ticket.assigned_masters.join(', ')}</p>}
      {editable && <small className="long-press-hint">Удерживайте карточку, чтобы изменить статус</small>}
    </article>
  )
}

function CommentValue({ value }: { value: string }) {
  if (/^https?:\/\/\S+$/i.test(value)) {
    return <a href={value} target="_blank" rel="noreferrer">{value}</a>
  }
  return <span>{value || 'Пустой комментарий'}</span>
}

function TicketDetails({
  ticket,
  busy,
  editable,
  onBack,
  onToggle,
}: {
  ticket: Ticket
  busy: boolean
  editable: boolean
  onBack: () => void
  onToggle: (comment?: string) => void
}) {
  const [comment, setComment] = useState('')
  const needsComment = ticket.kind === 'repair' && !ticket.completed
  return (
    <section className="ticket-details">
      <div className="step-heading with-back">
        <button className="icon-button" aria-label="Назад к заявкам" onClick={onBack}><BackIcon /></button>
        <div><h1>Заявка №{ticket.id}</h1><p>{formatDateTime(ticket.scheduled_at)}</p></div>
      </div>
      <div className={`panel ticket-detail-main ticket-${ticket.completed ? 'completed' : ticket.kind}`}>
        <div className="ticket-card-top">
          <span className="ticket-kind">{ticket.kind === 'connection' ? 'Новое подключение' : 'Ремонт'}</span>
          <span className="ticket-status">{ticket.completed ? 'Исполнена' : 'Не исполнена'}</span>
        </div>
        <dl>
          <div><dt>Клиент</dt><dd>{ticket.client_name || 'Не указан'}</dd></div>
          <div><dt>Адрес</dt><dd>{ticket.address || 'Не указан'}</dd></div>
          <div><dt>Телефон</dt><dd>{ticket.client_phone ? <a href={`tel:${ticket.client_phone}`}>{ticket.client_phone}</a> : 'Не указан'}</dd></div>
        </dl>
        <div className="ticket-description">
          <h2>Описание</h2>
          <p>{ticket.description || 'Описание отсутствует'}</p>
        </div>
        {editable && needsComment && <Field label="Что выполнено" required><textarea value={comment} onChange={event => setComment(event.target.value)} rows={4} placeholder="Опишите выполненные работы" /></Field>}
        {editable && <button className="primary-button" disabled={busy || (needsComment && !comment.trim())} onClick={() => onToggle(needsComment ? comment.trim() : undefined)}>
          {busy ? 'Сохранение…' : needsComment ? 'Завершить ремонт' : ticket.completed ? 'Вернуть в работу' : 'Отметить исполненной'}
        </button>}
      </div>
      <section className="comments-section">
        <h2>Комментарии <span>{ticket.comments.length}</span></h2>
        {ticket.comments.length === 0
          ? <div className="panel empty-state">Комментариев пока нет</div>
          : <div className="comment-list">
              {ticket.comments.map((comment, index) => (
                <article className="comment-card" key={`${comment.created_at}-${index}`}>
                  <div><strong>{comment.author}</strong><time>{formatDateTime(comment.created_at)}</time></div>
                  <p><CommentValue value={comment.text} /></p>
                </article>
              ))}
            </div>}
      </section>
    </section>
  )
}

function Tickets({ day, session }: { day: TicketDay; session: Session }) {
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [scope, setScope] = useState<'assigned' | 'all'>(() => (
    session.capabilities.all_tickets && localStorage.getItem(TICKETS_SCOPE_KEY) === 'all' ? 'all' : 'assigned'
  ))
  const [showClosed, setShowClosed] = useState(() => localStorage.getItem(SHOW_CLOSED_TICKETS_KEY) === 'true')
  const canViewAll = session.capabilities.all_tickets

  useEffect(() => { localStorage.setItem(SHOW_CLOSED_TICKETS_KEY, String(showClosed)) }, [showClosed])
  useEffect(() => {
    if (!canViewAll) {
      setScope('assigned')
      return
    }
    localStorage.setItem(TICKETS_SCOPE_KEY, scope)
  }, [canViewAll, scope])

  async function load() {
    setLoading(true)
    setError('')
    try {
      setTickets(await api.tickets(day, scope))
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    api.tickets(day, scope)
      .then(value => { if (active) setTickets(value) })
      .catch(cause => { if (active) setError(errorMessage(cause)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [day, scope])

  async function toggle(ticket: Ticket, comment?: string) {
    if (busyId !== null) return
    setBusyId(ticket.id)
    setError('')
    try {
      const updated = await api.setTicketCompletion(ticket.id, day, !ticket.completed, session.csrf_token, comment)
      setTickets(current => current.map(item => item.id === updated.id ? updated : item))
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusyId(null)
    }
  }

  const selected = tickets.find(ticket => ticket.id === selectedId)
  const visibleTickets = showClosed ? tickets : tickets.filter(ticket => !ticket.completed)
  if (selected) {
    return (
      <>
        {error && <ErrorBox text={error} />}
        <TicketDetails
          ticket={selected}
          busy={busyId === selected.id}
          editable={selected.can_change_completion !== false}
          onBack={() => { setSelectedId(null); setError('') }}
          onToggle={comment => toggle(selected, comment)}
        />
      </>
    )
  }

  return (
    <section className="tickets-screen">
      <div className="tickets-heading">
        <div>
          <h1>{day === 'today' ? 'Заявки сегодня' : 'Заявки завтра'}</h1>
          <p>{ticketDateLabel(day)}</p>
        </div>
        <div className="tickets-actions">
          {canViewAll && <label className="tickets-scope"><input type="checkbox" checked={scope === 'all'} onChange={event => { setSelectedId(null); setScope(event.target.checked ? 'all' : 'assigned') }} />Все заявки</label>}
          <label className="tickets-scope"><input type="checkbox" checked={showClosed} onChange={event => setShowClosed(event.target.checked)} />Закрытые</label>
          <button className="refresh-button" onClick={load} disabled={loading} aria-label="Обновить заявки">↻</button>
        </div>
      </div>
      {error && <ErrorBox text={error} />}
      {loading
        ? <div className="panel empty-state">Загрузка заявок…</div>
        : visibleTickets.length === 0
          ? <div className="panel empty-state">Неисполненных заявок нет</div>
          : <div className="ticket-list">
              {visibleTickets.map(ticket => (
                <TicketCard
                  key={ticket.id}
                  ticket={ticket}
                  busy={busyId === ticket.id}
                  editable={ticket.can_change_completion !== false}
                  showMasters={scope === 'all'}
                  onOpen={() => setSelectedId(ticket.id)}
                  onToggle={() => ticket.kind === 'repair' && !ticket.completed ? setSelectedId(ticket.id) : toggle(ticket)}
                />
              ))}
            </div>}
    </section>
  )
}

function AppShell({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const [tab, setTab] = useState<AppTab>('today')
  const domofonAllowed = session.capabilities.domofon
  const messengerSettingsAllowed = session.capabilities.messenger_settings
  const ticketDay: TicketDay = tab === 'today' || tab === 'tomorrow' ? tab : 'today'
  return (
    <main className="app-page with-navigation">
      <header className="app-header">
        <div><strong>ТехПортал</strong><span>{session.user.first_name || session.user.email}</span></div>
        <div className="header-actions"><button className="logout-button" onClick={onLogout}>Выйти</button></div>
      </header>
      {tab === 'settings' && messengerSettingsAllowed
        ? <Settings session={session} />
        : tab === 'domofon' && domofonAllowed
        ? <Domofon session={session} />
        : <Tickets key={ticketDay} day={ticketDay} session={session} />}
      <nav className="bottom-nav" aria-label="Основные разделы">
        <button className={tab === 'today' ? 'active' : ''} onClick={() => setTab('today')} aria-label="Сегодня" title="Сегодня"><span aria-hidden="true">●</span></button>
        <button className={tab === 'tomorrow' ? 'active' : ''} onClick={() => setTab('tomorrow')} aria-label="Завтра" title="Завтра"><span aria-hidden="true">◐</span></button>
        {domofonAllowed && <button className={tab === 'domofon' ? 'active' : ''} onClick={() => setTab('domofon')} aria-label="Домофон" title="Домофон"><span aria-hidden="true">⌂</span></button>}
        {messengerSettingsAllowed && <button className={tab === 'settings' ? 'active' : ''} onClick={() => setTab('settings')} aria-label="Настройки" title="Настройки"><span aria-hidden="true">⚙</span></button>}
      </nav>
    </main>
  )
}

export default function App() {
  const [screen, setScreen] = useState<AppScreen>('loading')
  const [session, setSession] = useState<Session | null>(null)

  useEffect(() => {
    api.session()
      .then(value => { setSession(value); setScreen('app') })
      .catch(() => setScreen('login'))
  }, [])

  useEffect(() => {
    const reset = () => { setSession(null); setScreen('login') }
    window.addEventListener('auth-expired', reset)
    return () => window.removeEventListener('auth-expired', reset)
  }, [])

  async function logout() {
    if (!session) return
    try {
      await api.logout(session.csrf_token)
    } finally {
      setSession(null)
      setScreen('login')
    }
  }

  if (screen === 'loading') return <main className="loading-page">Загрузка…</main>
  if (screen === 'login') return <Login onSession={value => { setSession(value); setScreen('app') }} />
  return session ? <AppShell session={session} onLogout={logout} /> : null
}
