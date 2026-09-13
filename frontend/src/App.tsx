import {
  FormEvent,
  KeyboardEvent,
  PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { paymentCommands } from './paymentCommands'

import {
  api,
  ApiError,
  PaymentAddress,
  PaymentCandidate,
  PaymentProduct,
  PaymentTransaction,
  Session,
  MessengerLink,
  MessengerLinkCreate,
  Ticket,
  TicketDay,
} from './api'

type AppScreen = 'loading' | 'login' | 'app'
type AppTab = TicketDay | 'payments' | 'settings'
type PaymentScreen = 'address' | 'product' | 'client' | 'amount' | 'progress' | 'ambiguous' | 'result'
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

function SuccessBox({ text }: { text: string }) {
  return <div role="status" className="success-box"><span aria-hidden="true">☎</span>{text}</div>
}

function PhoneButton({ phone, busy, onDial }: { phone: string; busy: boolean; onDial: (phone: string) => void }) {
  return <button className="phone-button" type="button" disabled={busy} onClick={event => { event.stopPropagation(); onDial(phone) }}>
    {busy ? 'Соединение…' : phone}
  </button>
}

function formatPaymentPhone(value: string): string {
  let digits = value.replace(/\D/g, '')
  if (digits.startsWith('8')) digits = `7${digits.slice(1)}`
  if (digits && !digits.startsWith('7')) digits = `7${digits}`
  digits = digits.slice(0, 11)
  if (!digits) return ''
  const national = digits.slice(1)
  if (national.length <= 3) return `+7${national ? ` (${national}` : ''}`
  if (national.length <= 6) return `+7 (${national.slice(0, 3)}) ${national.slice(3)}`
  if (national.length <= 8) return `+7 (${national.slice(0, 3)}) ${national.slice(3, 6)}-${national.slice(6)}`
  return `+7 (${national.slice(0, 3)}) ${national.slice(3, 6)}-${national.slice(6, 8)}-${national.slice(8)}`
}

function paymentPhoneE164(value: string): string {
  let digits = value.replace(/\D/g, '')
  if (digits.startsWith('8')) digits = `7${digits.slice(1)}`
  return digits.startsWith('7') ? `+${digits}` : value
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

const ACTIVE_PAYMENT_KEY = 'tp-pwa:active-payment'

function Payments({ session }: { session: Session }) {
  const [screen, setScreen] = useState<PaymentScreen>('address')
  const [locations, setLocations] = useState<PaymentAddress[]>([])
  const [products, setProducts] = useState<PaymentProduct[]>([])
  const [selectedLocation, setSelectedLocation] = useState<PaymentAddress | null>(null)
  const [selectedProduct, setSelectedProduct] = useState<PaymentProduct | null>(null)
  const [locationSearch, setLocationSearch] = useState('')
  const [apartment, setApartment] = useState('')
  const [firstName, setFirstName] = useState('')
  const [secondName, setSecondName] = useState('')
  const [lastName, setLastName] = useState('')
  const [phone, setPhone] = useState('')
  const [email, setEmail] = useState('')
  const [amount, setAmount] = useState('')
  const [transactionId, setTransactionId] = useState<string | null>(() => sessionStorage.getItem(ACTIVE_PAYMENT_KEY))
  const [transaction, setTransaction] = useState<PaymentTransaction | null>(null)
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [showInstructions, setShowInstructions] = useState(false)
  const telemetryEnabled = session.payment_telemetry_enabled === true
  const timing = useRef<{ start: number; accepted?: number; link?: number; sent: boolean; background: boolean; restored: boolean }>({
    start: telemetryEnabled ? performance.now() : 0, sent: false, background: document.hidden, restored: true,
  })
  useEffect(() => {
    if (!telemetryEnabled) return
    const trackVisibility = () => { if (document.hidden) timing.current.background = true }
    document.addEventListener('visibilitychange', trackVisibility)
    return () => document.removeEventListener('visibilitychange', trackVisibility)
  }, [telemetryEnabled])

  function reportQr(qrError: boolean) {
    const measurement = timing.current
    if (!telemetryEnabled || !transactionId || measurement.sent) return
    measurement.sent = true
    void api.paymentTiming(transactionId, {
      accepted_ms: measurement.accepted, link_ms: measurement.link,
      qr_ms: performance.now() - measurement.start, qr_error: qrError,
      background: measurement.background, restored: measurement.restored,
    }, session.csrf_token).catch(() => { /* Telemetry must never interrupt payment. */ })
  }

  const visibleLocations = useMemo(() => {
    const query = locationSearch.trim().toLocaleLowerCase('ru')
    return query ? locations.filter(item => item.loctext.toLocaleLowerCase('ru').includes(query)) : locations
  }, [locationSearch, locations])

  useEffect(() => {
    let active = true
    api.paymentAddresses().then(value => { if (active) setLocations(value) }).catch(cause => { if (active) setError(errorMessage(cause)) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!transactionId) return
    sessionStorage.setItem(ACTIVE_PAYMENT_KEY, transactionId)
    let active = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const poll = async () => {
      try {
        const value = await api.payment(transactionId)
        if (!active) return
        if (telemetryEnabled && (value.payment_short_url || value.payment_url) && timing.current.link === undefined) {
          timing.current.link = performance.now() - timing.current.start
        }
        setTransaction(value)
        setError('')
        if (value.status === 'client_selection_required') setScreen('ambiguous')
        else if (value.payment_short_url || value.payment_url || ['send_failed', 'send_queued', 'sent', 'paid', 'failed', 'expired', 'canceled'].includes(value.status)) setScreen('result')
        else setScreen('progress')
        if (paymentCommands(value).pending || !['paid', 'failed', 'expired', 'canceled'].includes(value.status)) timer = setTimeout(poll, 2500)
      } catch (cause) {
        if (active) { setError(errorMessage(cause)); timer = setTimeout(poll, 4000) }
      }
    }
    poll()
    return () => { active = false; if (timer) clearTimeout(timer) }
  }, [transactionId, telemetryEnabled])

  function reset() {
    setScreen('address')
    setSelectedLocation(null)
    setSelectedProduct(null)
    setLocationSearch('')
    setApartment('')
    setFirstName('')
    setSecondName('')
    setLastName('')
    setPhone('')
    setEmail('')
    setAmount('')
    setTransaction(null)
    setTransactionId(null)
    setIdempotencyKey(crypto.randomUUID())
    sessionStorage.removeItem(ACTIVE_PAYMENT_KEY)
    setError('')
  }

  async function openProducts(address: PaymentAddress | null) {
    setLoading(true)
    setError('')
    try {
      setSelectedLocation(address)
      setApartment('')
      if (products.length === 0) setProducts(await api.paymentProducts())
      setScreen('product')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setLoading(false)
    }
  }

  function chooseProduct(product: PaymentProduct) {
    setSelectedProduct(product)
    setAmount(product.default_amount)
    setScreen('client')
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!selectedProduct) return
    if (telemetryEnabled) timing.current = { start: performance.now(), sent: false, background: document.hidden, restored: false }
    setLoading(true)
    setError('')
    try {
      const response = await api.createPayment({
        idempotency_key: idempotencyKey,
        ...(selectedLocation ? { address: selectedLocation, apartment: apartment.trim() } : {}),
        product_id: selectedProduct.product_id,
        first_name: firstName.trim(), second_name: secondName.trim() || undefined,
        last_name: lastName.trim(), phone: paymentPhoneE164(phone), email: email.trim() || undefined, amount: amount.replace(',', '.'),
      }, session.csrf_token)
      if (telemetryEnabled) timing.current.accepted = performance.now() - timing.current.start
      setTransactionId(response.id)
      setScreen('progress')
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setLoading(false)
    }
  }

  async function selectCandidate(candidate: PaymentCandidate) {
    if (!transactionId) return
    setLoading(true); setError('')
    try { setTransaction(await api.selectPaymentClient(transactionId, candidate, session.csrf_token)); setScreen('progress') }
    catch (cause) { setError(errorMessage(cause)) }
    finally { setLoading(false) }
  }

  async function resend() {
    if (!transactionId) return
    setLoading(true); setError('')
    try { setTransaction(await api.resendPayment(transactionId, session.csrf_token)) }
    catch (cause) { setError(errorMessage(cause)) }
    finally { setLoading(false) }
  }

  async function cancelPayment() {
    if (!transactionId || !window.confirm('Отменить оплату и сделать ссылку недействительной?')) return
    setLoading(true); setError('')
    try { setTransaction(await api.cancelPayment(transactionId, session.csrf_token)) }
    catch (cause) { setError(errorMessage(cause)) }
    finally { setLoading(false) }
  }

  if (screen === 'address') {
    return (
      <section className="step-content">
        <div className="step-heading"><h1>Оплата</h1><button className="payment-instructions-button" type="button" onClick={() => setShowInstructions(true)}>Как принять оплату</button><p>Шаг 1 из 4 · выберите адрес клиента</p></div>
        <input className="search-input" type="search" placeholder="Поиск по адресу" value={locationSearch} onChange={event => setLocationSearch(event.target.value)} />
        <button className="outline-button" disabled={loading} onClick={() => openProducts(null)}>{loading ? 'Загрузка…' : 'Пропустить адрес'}</button>
        {error && <ErrorBox text={error} />}
        <div className="location-grid">
          {visibleLocations.map(location => (
            <button className="location-card" key={location.locid} onClick={() => openProducts(location)}>
              <span className="location-icon"><LocationIcon /></span>
              <strong>{location.loctext}</strong>
            </button>
          ))}
        </div>
        {visibleLocations.length === 0 && <div className="panel empty-state">Адреса не найдены</div>}
        {showInstructions && <div className="payment-instructions-backdrop" role="presentation" onClick={() => setShowInstructions(false)}>
          <section className="payment-instructions" role="dialog" aria-modal="true" aria-labelledby="payment-instructions-title" onClick={event => event.stopPropagation()}>
            <div className="payment-instructions-heading"><h2 id="payment-instructions-title">Как принять оплату</h2><button className="icon-button" type="button" aria-label="Закрыть инструкцию" onClick={() => setShowInstructions(false)}>×</button></div>
            <ol>
              <li>Выберите адрес клиента. Чтобы найти его быстрее, начните вводить адрес в строке поиска. Если адреса нет в списке или он не нужен, нажмите «Пропустить адрес».</li>
              <li>Выберите услугу или товар.</li>
              <li>Введите фамилию, имя и телефон клиента. Если выбрали адрес, укажите квартиру. E-mail указывать не обязательно.</li>
              <li>Проверьте сумму. При необходимости измените её.</li>
              <li>Нажмите «Сформировать оплату» и дождитесь результата.</li>
              <li>Клиенту придёт сообщение на e-mail, если вы указали его. Отправка СМС пока не работает.</li>
              <li>Предложите клиенту оплатить по ссылке из сообщения или отсканировать QR-код с экрана вашего телефона.</li>
            </ol>
            <button className="primary-button" type="button" onClick={() => setShowInstructions(false)}>Понятно</button>
          </section>
        </div>}
      </section>
    )
  }
  if (screen === 'product') {
    return (
      <section className="step-content">
        <div className="step-heading with-back">
          <button className="icon-button" aria-label="Назад" onClick={() => setScreen('address')}><BackIcon /></button>
          <div><h1>Выберите услугу</h1><p>Шаг 2 из 4</p></div>
        </div>
        <div className="product-grid">{products.map(product => <button className="panel product-card" key={product.product_id} onClick={() => chooseProduct(product)}><strong>{product.title}</strong><span>{product.default_amount} {product.currency}</span></button>)}</div>
        {products.length === 0 && <div className="panel empty-state">Нет доступных услуг</div>}
      </section>
    )
  }
  if (screen === 'client' && selectedProduct) {
    return (
      <section className="step-content">
        <div className="step-heading with-back"><button className="icon-button" aria-label="Назад" onClick={() => setScreen('product')}><BackIcon /></button><div><h1>Данные клиента</h1><p>Шаг 3 из 4</p></div></div>
        <form className="panel create-form" onSubmit={event => { event.preventDefault(); setScreen('amount') }}>
          <div className="field-row"><Field label="Фамилия" required><input value={lastName} onChange={event => setLastName(event.target.value)} autoComplete="family-name" required /></Field><Field label="Имя" required><input value={firstName} onChange={event => setFirstName(event.target.value)} autoComplete="given-name" required /></Field></div>
          <Field label="Отчество"><input value={secondName} onChange={event => setSecondName(event.target.value)} autoComplete="additional-name" /></Field>
          <Field label="Телефон" required><input type="tel" value={phone} onChange={event => setPhone(formatPaymentPhone(event.target.value))} autoComplete="tel" inputMode="tel" pattern={String.raw`\+7 \(\d{3}\) \d{3}-\d{2}-\d{2}`} title="Введите номер в формате +7 (999) 123-45-67" placeholder="+7 (999) 123-45-67" maxLength={18} required /></Field>
          <Field label="E-mail"><input type="email" value={email} onChange={event => setEmail(event.target.value)} autoComplete="email" placeholder="client@example.com" /></Field>
          {selectedLocation && <Field label="Квартира / офис" required><input value={apartment} onChange={event => setApartment(event.target.value)} placeholder="42, 12А или офис 3" required /></Field>}
          <button className="primary-button">Продолжить</button>
        </form>
      </section>
    )
  }
  if (screen === 'amount' && selectedProduct) {
    return (
      <section className="step-content">
        <div className="step-heading with-back"><button className="icon-button" aria-label="Назад" onClick={() => setScreen('client')}><BackIcon /></button><div><h1>Сумма и подтверждение</h1><p>Шаг 4 из 4</p></div></div>
        <form className="panel create-form" onSubmit={submit}>
          <div className="payment-summary"><strong>{selectedProduct.title}</strong><span>{lastName} {firstName} {secondName}</span><span>{phone}</span>{email && <span>{email}</span>}<span>{selectedLocation ? `${selectedLocation.loctext}, ${apartment}` : 'Без адреса'}</span></div>
          <Field label={`Сумма, ${selectedProduct.currency}`} required><input inputMode="decimal" value={amount} onChange={event => setAmount(event.target.value)} readOnly={!selectedProduct.price_override_allowed} required /></Field>
          {error && <ErrorBox text={error} />}
          <button className="primary-button" disabled={loading}>{loading ? 'Формирование…' : 'Сформировать оплату'}</button>
        </form>
      </section>
    )
  }
  if (screen === 'ambiguous' && transaction) {
    return <section className="step-content"><div className="step-heading"><h1>Уточните клиента</h1><p>Найдено несколько совпадений</p></div>{error && <ErrorBox text={error} />}<div className="product-grid">{transaction.candidates.map(candidate => <button className="panel product-card" disabled={loading} key={`${candidate.entity_type}-${candidate.entity_id}`} onClick={() => selectCandidate(candidate)}><strong>{candidate.display_name}</strong><span>{candidate.entity_type === 'contact' ? 'Контакт' : 'Лид'}</span></button>)}</div></section>
  }
  if (screen === 'result' && transaction) {
    const command = paymentCommands(transaction)
    const link = transaction.payment_short_url || transaction.payment_url
    const paid = transaction.status === 'paid'
    const stopped = ['failed', 'expired', 'canceled'].includes(transaction.status)
    const heading = paid ? 'Оплата получена' : stopped && !link ? 'Оплата не сформирована' : 'Ссылка сформирована'
    const notice = paid
      ? 'Битрикс24 подтвердил оплату'
      : stopped && !link
        ? transaction.error_message || 'Операция остановлена. Сообщите администратору её ID.'
        : transaction.status === 'send_failed'
          ? 'SMS не настроено — передайте ссылку клиенту'
          : 'Ссылка поставлена в очередь отправки'
    const hideLink = command.canceling || ['canceled', 'expired'].includes(transaction.status)
    return <section className="step-content">
      <div className="step-heading"><h1>{transaction.status === 'canceled' ? 'Оплата отменена' : transaction.status === 'expired' ? 'Срок оплаты истёк' : heading}</h1><p>{transaction.product_title} · {transaction.actual_amount} {transaction.currency}</p></div>
      {error && <ErrorBox text={error} />}
      {command.pending && <p role="status">{command.message}</p>}
      <div className="panel payment-result">
        <div className="success-icon">{paid ? '✓' : stopped && !link ? '!' : '₽'}</div>
        <strong>{hideLink ? 'Не используйте прежнюю ссылку для оплаты.' : notice}</strong>
        {stopped && !link && <small>ID операции: {transaction.id}</small>}
        {!hideLink && transaction.payment_qr && <img className="payment-qr" src={transaction.payment_qr} alt="QR-код оплаты" onLoad={() => reportQr(false)} onError={() => reportQr(true)} />}
        {!hideLink && link && <a className="payment-link" href={link} target="_blank" rel="noreferrer">Открыть ссылку на оплату</a>}
      </div>
      {!paid && !stopped && link && <button className="outline-button" disabled={loading || command.pending} onClick={resend}>{loading ? 'Отправка…' : 'Отправить повторно'}</button>}
      {!paid && !stopped && <button className="outline-button danger-button" disabled={loading || command.pending} onClick={cancelPayment}>Отменить оплату</button>}
      <button className="primary-button" disabled={command.pending} onClick={reset}>Новая оплата</button>
    </section>
  }
  const pendingCommand = transaction ? paymentCommands(transaction) : null
  return <section className="step-content"><div className="step-heading"><h1>Формируем оплату</h1><p>Не закрывайте экран</p></div>{error && <ErrorBox text={error} />}{pendingCommand?.pending && <p role="status">{pendingCommand.message}</p>}<div className="panel payment-progress"><span className="spinner" aria-hidden="true" /><strong>{transaction?.current_step === 'resolving_client' ? 'Ищем клиента…' : transaction?.current_step === 'invoice_created' ? 'Добавляем товар…' : transaction?.current_step === 'product_added' ? 'Создаём оплату…' : transaction?.current_step === 'payment_created' ? 'Формируем ссылку…' : 'Создаём платёжный документ…'}</strong></div></section>
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
  dialing,
  onOpen,
  onToggle,
  onDial,
}: {
  ticket: Ticket
  busy: boolean
  editable: boolean
  showMasters: boolean
  dialing: boolean
  onOpen: () => void
  onToggle: () => void
  onDial: (phone: string) => void
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
      {ticket.client_phones.length > 0
        ? <div className="ticket-phones">{ticket.client_phones.map(phone => <PhoneButton key={phone} phone={phone} busy={dialing} onDial={onDial} />)}</div>
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
  dialing,
  onBack,
  onToggle,
  onDial,
}: {
  ticket: Ticket
  busy: boolean
  editable: boolean
  dialing: boolean
  onBack: () => void
  onToggle: (comment?: string) => void
  onDial: (phone: string) => void
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
          <div><dt>Телефоны</dt><dd>{ticket.client_phones.length > 0 ? <div className="ticket-phones">{ticket.client_phones.map(phone => <PhoneButton key={phone} phone={phone} busy={dialing} onDial={onDial} />)}</div> : 'Не указаны'}</dd></div>
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
  const [dialingId, setDialingId] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [dialNotice, setDialNotice] = useState('')
  const dialNoticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
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

  async function dial(ticket: Ticket, phone: string) {
    if (dialingId !== null) return
    setDialingId(ticket.id)
    setError('')
    setDialNotice('')
    try {
      await api.dialPhone(phone, session.csrf_token)
      if (dialNoticeTimer.current) clearTimeout(dialNoticeTimer.current)
      setDialNotice(`Соединение с ${phone}. Ожидайте звонка.`)
      dialNoticeTimer.current = setTimeout(() => setDialNotice(''), 4_000)
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setDialingId(null)
    }
  }

  useEffect(() => () => { if (dialNoticeTimer.current) clearTimeout(dialNoticeTimer.current) }, [])

  const selected = tickets.find(ticket => ticket.id === selectedId)
  const visibleTickets = showClosed ? tickets : tickets.filter(ticket => !ticket.completed)
  if (selected) {
    return (
      <>
        {error && <ErrorBox text={error} />}
        {dialNotice && <SuccessBox text={dialNotice} />}
        <TicketDetails
          ticket={selected}
          busy={busyId === selected.id}
          dialing={dialingId === selected.id}
          editable={selected.can_change_completion !== false}
          onBack={() => { setSelectedId(null); setError('') }}
          onToggle={comment => toggle(selected, comment)}
          onDial={phone => dial(selected, phone)}
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
      {dialNotice && <SuccessBox text={dialNotice} />}
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
                  dialing={dialingId === ticket.id}
                  editable={ticket.can_change_completion !== false}
                  showMasters={scope === 'all'}
                  onOpen={() => setSelectedId(ticket.id)}
                  onToggle={() => ticket.kind === 'repair' && !ticket.completed ? setSelectedId(ticket.id) : toggle(ticket)}
                  onDial={phone => dial(ticket, phone)}
                />
              ))}
            </div>}
    </section>
  )
}

function AppShell({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const [tab, setTab] = useState<AppTab>('today')
  const paymentsAllowed = session.capabilities.payments
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
        : tab === 'payments' && paymentsAllowed
        ? <Payments session={session} />
        : <Tickets key={ticketDay} day={ticketDay} session={session} />}
      <nav className="bottom-nav" aria-label="Основные разделы">
        <button className={tab === 'today' ? 'active' : ''} onClick={() => setTab('today')} aria-label="Сегодня" title="Сегодня"><span aria-hidden="true">●</span></button>
        <button className={tab === 'tomorrow' ? 'active' : ''} onClick={() => setTab('tomorrow')} aria-label="Завтра" title="Завтра"><span aria-hidden="true">◐</span></button>
        {paymentsAllowed && <button className={tab === 'payments' ? 'active' : ''} onClick={() => setTab('payments')} aria-label="Оплата" title="Оплата"><span aria-hidden="true">₽</span></button>}
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
