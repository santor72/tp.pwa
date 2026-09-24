import {
  FormEvent,
  KeyboardEvent,
  PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { paymentCommands } from './paymentCommands'
import { YandexMapCanvas } from './YandexMapCanvas'
import type { GisMapCanvasProps, GisMapView, GisPosition } from './GisMapCanvas'

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
  TicketFilters,
  TicketFilterSelection,
  GisFeature,
  GisFeatureCollection,
  GisFeatureDetails,
  GisLayer,
  GisMap,
  GisReportReceipt,
  ConnectionCompletion,
} from './api'

type AppScreen = 'loading' | 'login' | 'app'
type AppTab = TicketDay | 'payments' | 'settings' | 'map'
type PaymentScreen = 'address' | 'product' | 'client' | 'amount' | 'progress' | 'ambiguous' | 'result'
type GisMapHeight = 'compact' | 'standard' | 'large' | 'custom'
const SHOW_CLOSED_TICKETS_KEY = 'tp-pwa:show-closed-tickets'
const TICKETS_SCOPE_KEY = 'tp-pwa:tickets-scope'
const TICKETS_FILTER_KEY_PREFIX = 'tp-pwa:tickets-filter:'
const GIS_SELECTED_MAP_KEY = 'tp-pwa.gis.selected-map'
const GIS_MAP_VIEW_KEY = 'tp-pwa.gis.map-view'
const GIS_MAP_HEIGHT_KEY = 'tp-pwa.gis.map-height'
const GIS_MAP_HEIGHT_PERCENT_KEY = 'tp-pwa.gis.map-height-percent'
const CONNECTION_COMPLETION_KEY_PREFIX = 'tp-pwa:connection-completion:'

function storedGisMapHeight(): GisMapHeight {
  try {
    const value = window.localStorage.getItem(GIS_MAP_HEIGHT_KEY)
    return value === 'compact' || value === 'large' || value === 'custom' ? value : 'standard'
  } catch { return 'standard' }
}

function storedGisMapHeightPercent(): number {
  try {
    const value = Number(window.localStorage.getItem(GIS_MAP_HEIGHT_PERCENT_KEY))
    return Number.isInteger(value) && value >= 35 && value <= 90 ? value : 60
  } catch { return 60 }
}

function applyGisMapHeight(value: GisMapHeight, percent: number) {
  document.documentElement.dataset.gisMapHeight = value
  document.documentElement.style.setProperty('--gis-map-custom-height', `${percent}dvh`)
}

function connectionCompletionKey(ticketId: number, kind: Ticket['kind']) {
  return kind === 'connection' ? `${CONNECTION_COMPLETION_KEY_PREFIX}${ticketId}` : `tp-pwa:repair-completion:${ticketId}`
}

function ticketFilterKey(userId: string | number) {
  return `${TICKETS_FILTER_KEY_PREFIX}${userId}`
}

function storedTicketFilter(userId: string | number): TicketFilterSelection {
  try {
    const parsed = JSON.parse(localStorage.getItem(ticketFilterKey(userId)) || '{}')
    return {
      brigadeIds: Array.isArray(parsed.brigadeIds) ? parsed.brigadeIds.filter((id: unknown): id is string => typeof id === 'string') : [],
      masterIds: Array.isArray(parsed.masterIds) ? parsed.masterIds.filter((id: unknown): id is string => typeof id === 'string') : [],
    }
  } catch { return { brigadeIds: [], masterIds: [] } }
}

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

function Settings({ session, gisAllowed, mapHeight, mapHeightPercent, onMapHeightChange, onMapHeightPercentChange }: { session: Session; gisAllowed: boolean; mapHeight: GisMapHeight; mapHeightPercent: number; onMapHeightChange: (value: GisMapHeight) => void; onMapHeightPercentChange: (value: number) => void }) {
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
  useEffect(() => { if (session.capabilities.messenger_settings) void refresh() }, [session.capabilities.messenger_settings])
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
    <div className="step-heading"><h1>Настройки</h1><p>Параметры приложения и рабочих сервисов</p></div>
    {gisAllowed && <section className="panel map-size-settings">
      <div><h2>Размер карты</h2><p>Выберите высоту карты в обычном режиме. Разворот ⛶ всегда открывает её на весь экран.</p></div>
      <label className="field"><span>Высота карты</span><select aria-label="Высота карты" value={mapHeight} onChange={event => onMapHeightChange(event.target.value as GisMapHeight)}><option value="compact">Компактная</option><option value="standard">Стандартная</option><option value="large">Большая</option><option value="custom">Точно</option></select></label>
      {mapHeight === 'custom' && <label className="field map-size-slider"><span>Точная высота: {mapHeightPercent}% экрана</span><input aria-label="Точная высота карты" type="range" min="35" max="90" step="1" value={mapHeightPercent} onChange={event => onMapHeightPercentChange(Number(event.target.value))} /><output>{mapHeightPercent}%</output></label>}
    </section>}
    {session.capabilities.messenger_settings && <section className="panel telegram-linking">
      <div><h2>Telegram</h2><p>{link ? 'Подключён' : 'Подключите бота к учётной записи'}</p></div>
      {error && <ErrorBox text={error} />}
      {loading && <p>Загрузка…</p>}
      {!loading && link && <><p>{link.username ? `@${link.username}` : link.display_name || 'Подключённый аккаунт'}</p><button className="outline-button" onClick={disconnect}>Отключить</button></>}
      {!loading && !link && !created && <button className="primary-button" onClick={connect}>Подключить Telegram</button>}
      {!loading && created && <><p>Откройте ссылку до {formatDateTime(created.expires_at)}.</p><a className="primary-button telegram-link" href={created.deep_link}>Открыть Telegram</a><button className="outline-button" onClick={refresh}>Проверить статус</button><button className="outline-button" onClick={connect}>Создать новую ссылку</button></>}
    </section>}
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
    const addressConflict = transaction.candidates.some(candidate => candidate.matched_by === 'address_conflict')
    return <section className="step-content"><div className="step-heading"><h1>{addressConflict ? 'Адреса не совпадают' : 'Уточните клиента'}</h1><p>{addressConflict ? 'У контакта уже указан другой адрес. Выберите, какой адрес использовать.' : 'Найдено несколько совпадений'}</p></div>{error && <ErrorBox text={error} />}<div className="product-grid">{transaction.candidates.map(candidate => <button className="panel product-card" disabled={loading} key={`${candidate.entity_type}-${candidate.entity_id}-${candidate.action ?? ''}`} onClick={() => selectCandidate(candidate)}><strong>{candidate.display_name}</strong><span>{candidate.action === 'keep_contact_address' ? 'Оставить адрес в карточке контакта' : candidate.action === 'apply_selected_address' ? 'Записать выбранный адрес' : candidate.entity_type === 'contact' ? 'Контакт' : 'Лид'}</span></button>)}</div></section>
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
      if (!ticket.completed) onOpen()
      else onToggle()
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

function GisFeaturePicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [open, setOpen] = useState(false)
  const [maps, setMaps] = useState<GisMap[]>([])
  const [mapId, setMapId] = useState('')
  const [data, setData] = useState<GisFeatureCollection | null>(null)
  const [layers, setLayers] = useState<GisLayer[]>([])
  const [selected, setSelected] = useState<GisFeature | null>(null)
  const [mapView, setMapView] = useState<GisMapView | null>(null)
  const [fallbackBounds, setFallbackBounds] = useState<[number, number, number, number] | null>(null)
  const [viewportBounds, setViewportBounds] = useState<[number, number, number, number] | null>(null)
  const [position, setPosition] = useState<GisPosition | null>(null)
  const [locating, setLocating] = useState(false)
  const [locationNotice, setLocationNotice] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [detailsLoading, setDetailsLoading] = useState(false)
  const [detailsError, setDetailsError] = useState('')
  const [details, setDetails] = useState<GisFeatureDetails | null>(null)
  const initialPositionApplied = useRef(false)
  const savedViewRestored = useRef(false)

  useEffect(() => {
    if (!open || maps.length) return
    setLoading(true); setError('')
    api.gisMaps().then(result => {
      setMaps(result.rows)
      let savedId = ''
      try { savedId = window.localStorage.getItem(GIS_SELECTED_MAP_KEY) || '' } catch { /* storage may be unavailable */ }
      setMapId(result.rows.find(map => map.id === savedId)?.id || result.rows[0]?.id || '')
    }).catch(cause => setError(errorMessage(cause))).finally(() => setLoading(false))
  }, [open, maps.length])

  useEffect(() => {
    if (!mapId) return
    try { window.localStorage.setItem(GIS_SELECTED_MAP_KEY, mapId) } catch { /* storage may be unavailable */ }
  }, [mapId])

  useEffect(() => {
    if (!open || !mapId || !maps.length) return
    let active = true
    setLoading(true); setError(''); setData(null); setLayers([]); setFallbackBounds(null); setViewportBounds(null); setMapView(null)
    initialPositionApplied.current = false
    savedViewRestored.current = false
    try {
      const saved = window.sessionStorage.getItem(`${GIS_MAP_VIEW_KEY}:${mapId}`)
      if (saved) {
        const parsed = JSON.parse(saved) as Partial<GisMapView>
        if (typeof parsed.longitude === 'number' && typeof parsed.latitude === 'number' && typeof parsed.zoom === 'number') {
          setMapView({ longitude: parsed.longitude, latitude: parsed.latitude, zoom: parsed.zoom })
          savedViewRestored.current = true
          initialPositionApplied.current = true
        }
      }
    } catch { /* storage may be unavailable or contain invalid data */ }
    Promise.all([api.gisBounds(mapId), api.gisLayers(mapId)]).then(([bounds, result]) => {
      if (!active) return
      setFallbackBounds([bounds.xmin, bounds.ymin, bounds.xmax, bounds.ymax])
      setLayers(result.rows)
    }).catch(cause => active && setError(errorMessage(cause))).finally(() => active && setLoading(false))
    return () => { active = false }
  }, [open, mapId, maps.length])

  useEffect(() => {
    if (!open || mapView || !fallbackBounds) return
    if (position && !initialPositionApplied.current) {
      setMapView({ longitude: position.longitude, latitude: position.latitude, zoom: GIS_DEFAULT_ZOOM })
      initialPositionApplied.current = true
    } else if (!position) {
      setMapView({ longitude: (fallbackBounds[0] + fallbackBounds[2]) / 2, latitude: (fallbackBounds[1] + fallbackBounds[3]) / 2, zoom: 13 })
    }
  }, [open, fallbackBounds?.join(','), mapView, position?.latitude, position?.longitude])

  const handleViewChange = useCallback((nextView: GisMapView) => {
    setMapView(nextView)
    if (mapId) {
      try { window.sessionStorage.setItem(`${GIS_MAP_VIEW_KEY}:${mapId}`, JSON.stringify(nextView)) } catch { /* storage may be unavailable */ }
    }
  }, [mapId])
  const updateViewportBounds = useCallback((bounds: [number, number, number, number]) => {
    setViewportBounds(previous => previous?.every((item, index) => Math.abs(item - bounds[index]) < .000001) ? previous : bounds)
  }, [])
  useEffect(() => {
    if (!open || !mapId || !viewportBounds || !layers.length) return
    let active = true
    const timer = window.setTimeout(() => api.gisFeatures(mapId, viewportBounds, layers.map(layer => layer.id)).then(result => active && setData(result)).catch(cause => active && setError(errorMessage(cause))), 250)
    return () => { active = false; window.clearTimeout(timer) }
  }, [open, mapId, layers.map(layer => layer.id).join(','), viewportBounds?.join(',')])
  useEffect(() => {
    if (!open || !navigator.geolocation) { if (open) setLocationNotice('Геолокация не поддерживается устройством'); return }
    const watch = navigator.geolocation.watchPosition(value => {
      const next = { longitude: value.coords.longitude, latitude: value.coords.latitude, accuracy: value.coords.accuracy }
      setPosition(next)
      if (!savedViewRestored.current && !initialPositionApplied.current) {
        setMapView({ longitude: next.longitude, latitude: next.latitude, zoom: GIS_DEFAULT_ZOOM }); initialPositionApplied.current = true
      }
      setLocationNotice('Ваше местоположение показано синим маркером')
    }, () => setLocationNotice('Местоположение недоступно. Картой можно пользоваться вручную.'), { enableHighAccuracy: true, maximumAge: 30_000, timeout: 12_000 })
    return () => navigator.geolocation.clearWatch(watch)
  }, [open])
  const locate = useCallback(() => {
    if (!navigator.geolocation) { setLocationNotice('Геолокация не поддерживается устройством'); return }
    setLocating(true)
    navigator.geolocation.getCurrentPosition(value => {
      const next = { longitude: value.coords.longitude, latitude: value.coords.latitude, accuracy: value.coords.accuracy }
      setPosition(next); handleViewChange({ longitude: next.longitude, latitude: next.latitude, zoom: GIS_DEFAULT_ZOOM }); setLocationNotice('Карта центрирована на вашем местоположении'); setLocating(false)
    }, () => { setLocationNotice('Местоположение недоступно. Картой можно пользоваться вручную.'); setLocating(false) }, { enableHighAccuracy: true, maximumAge: 0, timeout: 12_000 })
  }, [handleViewChange])
  const openFeature = (feature: GisFeature) => {
    setSelected(feature); setDetails(null); setDetailsError(''); setDetailsLoading(true)
    api.gisFeature(feature.id).then(setDetails).catch(cause => setDetailsError(errorMessage(cause))).finally(() => setDetailsLoading(false))
  }
  const closePicker = () => { setOpen(false); setSelected(null); setDetails(null); setDetailsError('') }
  const chosenFeature = selected || data?.features.find(feature => feature.id === value) || null
  const selectedTitle = chosenFeature ? chosenFeature.properties.title || (chosenFeature.properties.number !== undefined ? `Объект №${chosenFeature.properties.number}` : 'Объект сети') : ''
  const selectedLayer = details?.layer_name || layers.find(layer => layer.id === selected?.properties.layer_id)?.name || 'Объект сети'
  return <>
    <button type="button" className="outline-button gis-picker-button" onClick={() => setOpen(true)}>{value ? `Объект: ${selectedTitle || value}` : 'Выбрать объект на карте'}</button>
    {value && <button type="button" className="gis-clear-feature" onClick={() => onChange('')}>Не отправлять в GIS</button>}
    {open && <div className="gis-feature-backdrop" onClick={closePicker}>
      <aside className="gis-feature-card gis-picker" role="dialog" aria-modal="true" aria-label="Выбрать объект GIS" onClick={event => event.stopPropagation()}>
        <div className="gis-feature-handle" />
        <header><div><small>Объект необязателен</small><h2>Выбрать объект GIS</h2></div><button type="button" onClick={closePicker} aria-label="Закрыть выбор объекта">×</button></header>
        {maps.length > 1 && <label className="field"><span>Карта</span><select value={mapId} onChange={event => { setMapId(event.target.value); try { window.localStorage.setItem(GIS_SELECTED_MAP_KEY, event.target.value) } catch { /* storage may be unavailable */ } }}>{maps.map(map => <option key={map.id} value={map.id}>{map.name}</option>)}</select></label>}
        {loading && <p className="gis-feature-loading">Загружаем объекты…</p>}
        {error && <ErrorBox text={error} />}
        {!selected && <>
          {locationNotice && <p className="gis-map-notice">{locationNotice}</p>}
          {mapView && <MapCanvas data={data} position={position} view={mapView} onViewChange={handleViewChange} onBoundsChange={updateViewportBounds} onSelect={openFeature} onLocate={locate} locating={locating} />}
          {data?.truncated && <div className="error-box">Показана не вся сеть. Уточните область на карте.</div>}
        </>}
        {selected && <section className="gis-picker-selection" aria-label="Подтверждение объекта GIS">
          <header><div><small>{selectedLayer}</small><h3>{details?.title || selectedTitle}</h3></div><button type="button" onClick={() => setSelected(null)} aria-label="Вернуться к карте">×</button></header>
          {detailsLoading && <p className="gis-feature-loading">Загружаем карточку объекта…</p>}
          {detailsError && <ErrorBox text={detailsError} />}
          <div className="gis-picker-actions"><button type="button" className="outline-button" onClick={() => setSelected(null)}>Назад к карте</button><button type="button" className="primary-button" onClick={() => { onChange(selected.id); closePicker() }}>Выбрать этот объект</button></div>
        </section>}
      </aside>
    </div>}
  </>
}

function TicketDetails({
  ticket,
  busy,
  editable,
  dialing,
  onBack,
  onToggle,
  onDial,
  onCompleteTicket,
  gisAllowed,
  connectionPhotosAllowed,
  gisPhotosAllowed,
}: {
  ticket: Ticket
  busy: boolean
  editable: boolean
  dialing: boolean
  onBack: () => void
  onToggle: (comment?: string) => void
  onDial: (phone: string) => void
  onCompleteTicket: (techportalText: string, gisText: string, photos: File[], featureId: string | undefined, idempotencyKey: string) => Promise<ConnectionCompletion>
  gisAllowed: boolean
  connectionPhotosAllowed: boolean
  gisPhotosAllowed: boolean
}) {
  const [techportalText, setTechportalText] = useState('')
  const [gisText, setGisText] = useState('')
  const [photos, setPhotos] = useState<File[]>([])
  const [featureId, setFeatureId] = useState('')
  const [completion, setCompletion] = useState<ConnectionCompletion | null>(null)
  const [completionError, setCompletionError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const completionIdempotencyKey = useRef(createUuid())
  const photosForTechPortal = connectionPhotosAllowed && photos.length > 0
  const needsComment = ticket.kind === 'repair' && !ticket.completed
  const isReport = !ticket.completed
  useEffect(() => {
    if (!connectionPhotosAllowed && !featureId) setPhotos([])
  }, [connectionPhotosAllowed, featureId])
  useEffect(() => {
    const operationId = sessionStorage.getItem(connectionCompletionKey(ticket.id, ticket.kind))
    if (!operationId) return
    let active = true
    api.connectionCompletion(operationId)
      .then(value => { if (active) setCompletion(value) })
      .catch(() => undefined)
    return () => { active = false }
  }, [ticket.id, ticket.kind])
  useEffect(() => {
    if (completion?.ticket_id === ticket.id) sessionStorage.setItem(connectionCompletionKey(ticket.id, ticket.kind), completion.id)
  }, [completion, ticket.id, ticket.kind])
  useEffect(() => {
    if (!completion || !['prepared', 'marking'].includes(completion.completion_status) && !['pending', 'sending', 'retry_wait'].includes(completion.gis_status)) return
    const timer = window.setInterval(() => api.connectionCompletion(completion.id).then(setCompletion).catch(() => undefined), 3_000)
    return () => window.clearInterval(timer)
  }, [completion?.id, completion?.completion_status, completion?.gis_status])
  async function submitReport() {
    const portalText = techportalText.trim()
    const mapText = gisText.trim()
    if (needsComment && !portalText) { setCompletionError('Опишите выполненные работы'); return }
    if (!needsComment && !portalText && !photosForTechPortal) { setCompletionError('Добавьте текст для ТехПортала или фотографию'); return }
    if (featureId.trim() && !mapText && !photos.length) { setCompletionError('Добавьте текст отчёта GIS или фотографию'); return }
    if (featureId.trim() && photos.length && !gisPhotosAllowed) { setCompletionError('Для отправки фотографий в GIS требуется настроенное S3-хранилище'); return }
    setSubmitting(true); setCompletionError('')
    try {
      setCompletion(await onCompleteTicket(portalText, mapText, photos, featureId.trim() || undefined, completionIdempotencyKey.current))
    } catch (cause) {
      setCompletionError(errorMessage(cause))
    } finally {
      setSubmitting(false)
    }
  }
  const completionNotice = completion?.completion_status === 'completed'
    ? completion.gis_status === 'delivered' ? 'Заявка отмечена выполненной. Отчёт передан в GIS.'
      : completion.gis_status === 'not_requested' ? 'Заявка отмечена выполненной. Отчёт добавлен в ТехПортал.'
      : 'Заявка отмечена выполненной. Отчёт отправляется в GIS.'
    : completion?.completion_status === 'completion_unknown' ? 'Проверяем результат отметки выполнения.'
      : completion?.completion_status === 'completion_failed' ? completion.error_message || 'Не удалось отметить заявку выполненной.' : ''
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
        {editable && isReport && <>
          <Field label={needsComment ? 'Что выполнено' : 'Отчёт для ТехПортала'} required={needsComment || !connectionPhotosAllowed}><textarea aria-label={needsComment ? 'Что выполнено' : 'Отчёт для ТехПортала'} value={techportalText} onChange={event => setTechportalText(event.target.value)} rows={needsComment ? 4 : 3} placeholder={needsComment ? 'Опишите выполненные работы' : 'Комментарий о выполненных работах'} /></Field>
          {gisAllowed && <div className="field"><span>Объект GIS — необязательно</span><GisFeaturePicker value={featureId} onChange={setFeatureId} /></div>}
          {(connectionPhotosAllowed || (featureId && gisPhotosAllowed)) && <Field label={connectionPhotosAllowed ? 'Фотографии' : 'Фотографии для GIS'}><input aria-label="Фотографии выполнения" type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={event => setPhotos(Array.from(event.target.files ?? []))} /></Field>}
          {featureId && <Field label="Отчёт для GIS" required={!gisPhotosAllowed}><textarea aria-label="Отчёт для GIS" value={gisText} onChange={event => setGisText(event.target.value)} rows={3} placeholder="Описание для отчёта GIS" /></Field>}
          {completionError && <ErrorBox text={completionError} />}
        </>}
        {completionNotice && <div className={completion?.completion_status === 'completed' ? 'success-box' : 'error-box'}>{completionNotice}</div>}
        {editable && <button className="primary-button" disabled={busy || submitting || (needsComment && !techportalText.trim())} onClick={() => isReport ? void submitReport() : onToggle()}>
          {submitting ? 'Сохраняем…' : busy ? 'Сохранение…' : isReport ? (needsComment ? 'Завершить ремонт' : 'Отметить выполненной') : 'Вернуть в работу'}
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
  const [filters, setFilters] = useState<TicketFilters | null>(null)
  const [filterSelection, setFilterSelection] = useState<TicketFilterSelection>(() => storedTicketFilter(session.user.id))
  const [draftFilterSelection, setDraftFilterSelection] = useState<TicketFilterSelection>(() => storedTicketFilter(session.user.id))
  const [filterScreen, setFilterScreen] = useState(false)
  const [filtersError, setFiltersError] = useState('')
  const canViewAll = session.capabilities.all_tickets

  useEffect(() => { localStorage.setItem(SHOW_CLOSED_TICKETS_KEY, String(showClosed)) }, [showClosed])
  useEffect(() => {
    if (!canViewAll) {
      setScope('assigned')
      return
    }
    localStorage.setItem(TICKETS_SCOPE_KEY, scope)
  }, [canViewAll, scope])
  useEffect(() => { localStorage.setItem(ticketFilterKey(session.user.id), JSON.stringify(filterSelection)) }, [filterSelection, session.user.id])

  useEffect(() => {
    let active = true
    if (scope !== 'all') { setFilters(null); setFiltersError(''); setFilterScreen(false); return () => { active = false } }
    api.ticketFilters()
      .then(value => {
        if (!active) return
        setFilters(value)
        const brigadeIds = new Set(value.brigades.map(item => item.id))
        const masterIds = new Set(value.masters.map(item => item.id))
        setFilterSelection(current => ({
          brigadeIds: current.brigadeIds.filter(id => brigadeIds.has(id)),
          masterIds: current.masterIds.filter(id => masterIds.has(id)),
        }))
      })
      .catch(cause => { if (active) setFiltersError(errorMessage(cause)) })
    return () => { active = false }
  }, [scope])

  async function load() {
    setLoading(true)
    setError('')
    try {
      setTickets(await api.tickets(day, scope, filterSelection))
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
    api.tickets(day, scope, filterSelection)
      .then(value => { if (active) setTickets(value) })
      .catch(cause => { if (active) setError(errorMessage(cause)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [day, scope, filterSelection])

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
          onCompleteTicket={async (techportalText, gisText, photos, featureId, idempotencyKey) => {
            const completion = await api.completeConnection(selected.id, { day, ticketKind: selected.kind, idempotencyKey, techportalText, gisText, photos, featureId }, session.csrf_token)
            if (completion.completion_status === 'completed') {
              setTickets(current => current.map(item => item.id === selected.id ? { ...item, completed: true } : item))
            }
            return completion
          }}
          gisAllowed={session.capabilities.gis}
          connectionPhotosAllowed={session.capabilities.connection_photos}
          gisPhotosAllowed={session.capabilities.gis_photos}
        />
      </>
    )
  }

  const filterSummary = (() => {
    if (!filterSelection.brigadeIds.length && !filterSelection.masterIds.length) return 'Фильтр не задан'
    const brigadeNames = filterSelection.brigadeIds.map(id => filters?.brigades.find(item => item.id === id)?.name || id)
    const masterNames = filterSelection.masterIds.map(id => filters?.masters.find(item => item.id === id)?.name || id)
    return [
      brigadeNames.length ? `Бригада: ${brigadeNames.join(', ')}` : '',
      masterNames.length ? `Мастер: ${masterNames.join(', ')}` : '',
    ].filter(Boolean).join(' · ')
  })()

  if (filterScreen) {
    return <section className="tickets-screen ticket-filter-screen">
      <div className="tickets-heading">
        <div><h1>Фильтр заявок</h1><p>Выберите бригаду или мастера</p></div>
        <button className="icon-button" aria-label="Назад к заявкам" onClick={() => setFilterScreen(false)}><BackIcon /></button>
      </div>
      {filtersError && <ErrorBox text={filtersError} />}
      {!filters && !filtersError && <div className="panel empty-state">Загрузка фильтров…</div>}
      {filters && <section className="ticket-filters panel">
        <div className="ticket-filter-controls">
          <label>Бригады
            <select aria-label="Бригады" value={draftFilterSelection.brigadeIds[0] || ''} onChange={event => { const value = event.currentTarget.value; setDraftFilterSelection(current => ({ ...current, brigadeIds: value ? [value] : [] })) }}>
              <option value="">Все бригады</option>
              {filters.brigades.map(brigade => <option key={brigade.id} value={brigade.id}>{brigade.name}</option>)}
            </select>
          </label>
          <label>Мастера
            <select aria-label="Мастера" value={draftFilterSelection.masterIds[0] || ''} onChange={event => { const value = event.currentTarget.value; setDraftFilterSelection(current => ({ ...current, masterIds: value ? [value] : [] })) }}>
              <option value="">Все мастера</option>
              {filters.masters.map(master => <option key={master.id} value={master.id}>{master.name}</option>)}
            </select>
          </label>
        </div>
        <div className="ticket-filter-actions">
          <button className="outline-button" type="button" onClick={() => setDraftFilterSelection({ brigadeIds: [], masterIds: [] })}>Сбросить</button>
          <button className="primary-button" type="button" onClick={() => { setFilterSelection(draftFilterSelection); setFilterScreen(false) }}>Применить</button>
        </div>
      </section>}
    </section>
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
      {scope === 'all' && <div className="ticket-filter-summary"><button className="outline-button" type="button" onClick={() => { setDraftFilterSelection(filterSelection); setFilterScreen(true) }}>Фильтр</button><small>{filterSummary}</small></div>}
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

function coordinatePairs(geometry: GisFeature['geometry']): [number, number][] {
  if (geometry.type === 'Point') return [geometry.coordinates as [number, number]]
  if (geometry.type === 'LineString') return geometry.coordinates as [number, number][]
  return (geometry.coordinates as [number, number][][]).flat()
}

const GIS_DEFAULT_ZOOM = 14
function initialMapView(coordinates: [number, number][]): GisMapView {
  const longitudes = coordinates.map(point => point[0]); const latitudes = coordinates.map(point => point[1])
  const longitude = (Math.min(...longitudes) + Math.max(...longitudes)) / 2
  const latitude = (Math.min(...latitudes) + Math.max(...latitudes)) / 2
  const span = Math.max(Math.max(...longitudes) - Math.min(...longitudes), Math.max(...latitudes) - Math.min(...latitudes), .003)
  return { longitude, latitude, zoom: Math.max(8, Math.min(18, Math.floor(Math.log2(300 / span)))) }
}

function MapCanvas(props: Omit<GisMapCanvasProps, 'initialMapView'>) {
  return <YandexMapCanvas {...props} initialMapView={initialMapView} />
}

function objectGeometryLabel(feature: GisFeatureDetails): string {
  const points = coordinatePairs(feature.geometry)
  if (feature.geometry.type === 'Point') return `${points[0][1].toFixed(5)}, ${points[0][0].toFixed(5)}`
  const radians = (value: number) => value * Math.PI / 180
  const distance = points.slice(1).reduce((total, point, index) => {
    const previous = points[index]; const dLatitude = radians(point[1] - previous[1]); const dLongitude = radians(point[0] - previous[0])
    const a = Math.sin(dLatitude / 2) ** 2 + Math.cos(radians(previous[1])) * Math.cos(radians(point[1])) * Math.sin(dLongitude / 2) ** 2
    return total + 6_371_000 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
  }, 0)
  return `${Math.round(distance).toLocaleString('ru-RU')} м${feature.geometry.type === 'Polygon' ? ' · периметр' : ''}`
}

function plainGisDescription(value: string): string {
  // GIS descriptions originate in imported markup. The PWA never inserts that
  // markup into the DOM: preserve line breaks, then render text only.
  const source = value
    .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, '')
    .replace(/<br\s*\/?\s*>/gi, '\n')
    .replace(/<\/(?:p|div|li|tr|h[1-6])\s*>/gi, '\n')
  const element = document.createElement('div')
  element.innerHTML = source
  return (element.textContent ?? '').replace(/\n[\t ]*/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
}

function createUuid(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, character => {
    const value = Math.floor(Math.random() * 16)
    return (character === 'x' ? value : value & 3 | 8).toString(16)
  })
}

function GisReportForm({ featureId, csrfToken }: { featureId: string; csrfToken: string }) {
  const [text, setText] = useState('')
  const [photos, setPhotos] = useState<File[]>([])
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const [receipt, setReceipt] = useState<GisReportReceipt | null>(null)
  const ids = useRef<{ externalReportId: string; completionId: string } | null>(null)

  const selectPhotos = (selected: File[]) => {
    if (selected.length > 5) { setError('В одном отчёте можно загрузить до 5 фотографий'); return }
    if (selected.some(photo => !['image/jpeg', 'image/png', 'image/webp'].includes(photo.type))) { setError('Допустимы фотографии JPEG, PNG или WebP'); return }
    if (selected.some(photo => photo.size > 10 * 1024 * 1024)) { setError('Размер одной фотографии — не более 10 МБ'); return }
    setError(''); setPhotos(selected)
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    const value = text.trim()
    if (!value && !photos.length) { setError('Добавьте текст или фотографию'); return }
    if (!ids.current) ids.current = { externalReportId: createUuid(), completionId: createUuid() }
    setSending(true); setError('')
    try {
      setReceipt(await api.createGisReport({ featureId, externalReportId: ids.current.externalReportId, completionId: ids.current.completionId, text: value, photos }, csrfToken))
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setSending(false)
    }
  }
  if (receipt) return <section className="gis-report-form"><h3>Отчёт отправлен</h3><p>GIS сохранила отчёт по выбранному объекту.</p></section>
  return <form className="gis-report-form" onSubmit={submit}>
    <h3>Новый отчёт</h3>
    <Field label="Описание работ"><textarea value={text} onChange={event => { setText(event.target.value); ids.current = null }} maxLength={10_000} rows={4} placeholder="Что выполнено" /></Field>
    <Field label="Фотографии"><input aria-label="Фотографии" type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={event => { selectPhotos(Array.from(event.target.files ?? [])); ids.current = null }} /></Field>
    {photos.length > 0 && <p className="gis-report-photos">Выбрано фотографий: {photos.length}</p>}
    {error && <ErrorBox text={error} />}
    <button className="primary-button" disabled={sending}>{sending ? 'Отправляем…' : 'Отправить отчёт'}</button>
  </form>
}

function GisFeatureCard({ feature, details, loading, error, csrfToken, onClose }: { feature: GisFeature | null; details: GisFeatureDetails | null; loading: boolean; error: string; csrfToken: string; onClose: () => void }) {
  if (!feature) return null
  const detailRows = details ? [
    [details.geometry.type === 'Point' ? 'Координаты' : 'Длина', objectGeometryLabel(details)],
    ['Версия', details.version],
  ].filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== '') : []
  const description = details ? plainGisDescription(details.description || '') : ''
  const title = details?.title?.trim() || feature.properties.title?.trim() || (feature.properties.number !== undefined ? `Объект №${feature.properties.number}` : 'Объект сети')
  const layerName = details?.layer_name?.trim() || 'Объект сети'
  return <div className="gis-feature-backdrop" onClick={onClose}>
    <aside className="gis-feature-card" role="dialog" aria-modal="true" aria-label="Карточка объекта" onClick={event => event.stopPropagation()}>
      <div className="gis-feature-handle" />
      <header><div><small>{layerName}</small><h2>{title}</h2></div><button type="button" onClick={onClose} aria-label="Закрыть карточку объекта">×</button></header>
      {loading && <p className="gis-feature-loading">Загружаем карточку объекта…</p>}
      {error && <p className="error-box">{error}</p>}
      {details && <>
        {detailRows.length > 0 && <dl className="gis-feature-details">
          {detailRows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>)}
        </dl>}
        {description && <section className="gis-feature-description"><h3>Описание</h3><p>{description}</p></section>}
        <GisReportForm featureId={details.id} csrfToken={csrfToken} />
      </>}
    </aside>
  </div>
}

function MapScreen({ session }: { session: Session }) {
  const [maps, setMaps] = useState<GisMap[]>([])
  const [current, setCurrent] = useState<GisMap | null>(null)
  const [layers, setLayers] = useState<GisLayer[]>([])
  const [selectedLayers, setSelectedLayers] = useState<string[]>([])
  const [data, setData] = useState<GisFeatureCollection | null>(null)
  const [selected, setSelected] = useState<GisFeature | null>(null)
  const [selectedDetails, setSelectedDetails] = useState<GisFeatureDetails | null>(null)
  const [detailsLoading, setDetailsLoading] = useState(false)
  const [detailsError, setDetailsError] = useState('')
  const [position, setPosition] = useState<GisPosition | null>(null)
  const [fallbackBounds, setFallbackBounds] = useState<[number, number, number, number] | null>(null)
  const [mapView, setMapView] = useState<GisMapView | null>(null)
  const [viewportBounds, setViewportBounds] = useState<[number, number, number, number] | null>(null)
  const [locationNotice, setLocationNotice] = useState('')
  const [locating, setLocating] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const initialPositionApplied = useRef(false)
  const savedViewRestored = useRef(false)

  useEffect(() => {
    let active = true
    api.gisMaps().then(result => {
      if (!active) return
      setMaps(result.rows)
      let savedId = ''
      try { savedId = window.localStorage.getItem(GIS_SELECTED_MAP_KEY) || '' } catch { /* storage may be unavailable */ }
      setCurrent(result.rows.find(map => map.id === savedId) ?? result.rows[0] ?? null)
    }).catch(cause => active && setError(errorMessage(cause))).finally(() => active && setLoading(false))
    return () => { active = false }
  }, [])
  useEffect(() => {
    if (!current) return
    try { window.localStorage.setItem(GIS_SELECTED_MAP_KEY, current.id) } catch { /* storage may be unavailable */ }
  }, [current?.id])
  useEffect(() => {
    if (!current) return
    let active = true
    setLoading(true); setError(''); setData(null); setSelected(null); setSelectedDetails(null); setFallbackBounds(null); setMapView(null); setViewportBounds(null)
    initialPositionApplied.current = false
    savedViewRestored.current = false
    try {
      const saved = window.sessionStorage.getItem(`${GIS_MAP_VIEW_KEY}:${current.id}`)
      if (saved) {
        const parsed = JSON.parse(saved) as Partial<GisMapView>
        if (typeof parsed.longitude === 'number' && typeof parsed.latitude === 'number' && typeof parsed.zoom === 'number') {
          setMapView({ longitude: parsed.longitude, latitude: parsed.latitude, zoom: parsed.zoom })
          savedViewRestored.current = true
          initialPositionApplied.current = true
        }
      }
    } catch { /* storage may be unavailable or contain invalid data */ }
    api.gisLayers(current.id).then(result => { if (active) { setLayers(result.rows); setSelectedLayers(result.rows.map(layer => layer.id)) } }).catch(cause => active && setError(errorMessage(cause))).finally(() => active && setLoading(false))
    api.gisBounds(current.id).then(result => active && setFallbackBounds([result.xmin, result.ymin, result.xmax, result.ymax])).catch(() => active && setFallbackBounds(null))
    return () => { active = false }
  }, [current?.id])
  useEffect(() => {
    if (mapView || !current || !fallbackBounds) return
    // Prefer the installer position. If it is not available, open a useful
    // neighbourhood around the network centre, never the whole map extent.
    if (position) setMapView({ longitude: position.longitude, latitude: position.latitude, zoom: GIS_DEFAULT_ZOOM })
    else setMapView({ longitude: (fallbackBounds[0] + fallbackBounds[2]) / 2, latitude: (fallbackBounds[1] + fallbackBounds[3]) / 2, zoom: 13 })
  }, [current?.id, fallbackBounds?.join(','), mapView, position?.latitude, position?.longitude])
  const handleViewChange = useCallback((nextView: GisMapView) => {
    setMapView(previous => {
      if (previous && Math.abs(previous.longitude - nextView.longitude) < .000001 && Math.abs(previous.latitude - nextView.latitude) < .000001 && previous.zoom === nextView.zoom) return previous
      if (current) {
        try { window.sessionStorage.setItem(`${GIS_MAP_VIEW_KEY}:${current.id}`, JSON.stringify(nextView)) } catch { /* storage may be unavailable */ }
      }
      return nextView
    })
  }, [current?.id])
  const updateViewportBounds = useCallback((bounds: [number, number, number, number]) => {
    setViewportBounds(previous => previous?.every((value, index) => Math.abs(value - bounds[index]) < .000001) ? previous : bounds)
  }, [])
  useEffect(() => {
    if (!current || !selectedLayers.length || !viewportBounds) return
    let active = true
    const timer = window.setTimeout(() => api.gisFeatures(current.id, viewportBounds, selectedLayers).then(result => active && setData(result)).catch(cause => active && setError(errorMessage(cause))), 250)
    return () => { active = false; window.clearTimeout(timer) }
  }, [current?.id, selectedLayers.join(','), viewportBounds?.join(',')])
  useEffect(() => {
    if (!navigator.geolocation) { setLocationNotice('Геолокация не поддерживается устройством'); return }
    const watch = navigator.geolocation.watchPosition(
      value => {
        const nextPosition = { longitude: value.coords.longitude, latitude: value.coords.latitude, accuracy: value.coords.accuracy }
        setPosition(nextPosition)
        if (!savedViewRestored.current && !initialPositionApplied.current) {
          setMapView({ longitude: nextPosition.longitude, latitude: nextPosition.latitude, zoom: GIS_DEFAULT_ZOOM })
          initialPositionApplied.current = true
        }
        setLocationNotice('Ваше местоположение показано синим маркером')
      },
      () => setLocationNotice('Местоположение недоступно. Картой можно пользоваться вручную.'),
      { enableHighAccuracy: true, maximumAge: 30_000, timeout: 12_000 },
    )
    return () => navigator.geolocation.clearWatch(watch)
  }, [])
  const locate = useCallback(() => {
    if (!navigator.geolocation) {
      setLocationNotice('Геолокация не поддерживается устройством')
      return
    }
    setLocating(true)
    navigator.geolocation.getCurrentPosition(
      value => {
        const nextPosition = { longitude: value.coords.longitude, latitude: value.coords.latitude, accuracy: value.coords.accuracy }
        setPosition(nextPosition)
        handleViewChange({ longitude: nextPosition.longitude, latitude: nextPosition.latitude, zoom: GIS_DEFAULT_ZOOM })
        setLocationNotice('Карта центрирована на вашем местоположении')
        setLocating(false)
      },
      () => {
        setLocationNotice('Местоположение недоступно. Картой можно пользоваться вручную.')
        setLocating(false)
      },
      { enableHighAccuracy: true, maximumAge: 0, timeout: 12_000 },
    )
  }, [handleViewChange])
  const toggleLayer = (layerId: string) => setSelectedLayers(currentLayers => currentLayers.includes(layerId) ? currentLayers.filter(value => value !== layerId) : [...currentLayers, layerId])
  const openFeature = useCallback((feature: GisFeature) => {
    setSelected(feature); setSelectedDetails(null); setDetailsError(''); setDetailsLoading(true)
    api.gisFeature(feature.id).then(setSelectedDetails).catch(cause => setDetailsError(errorMessage(cause))).finally(() => setDetailsLoading(false))
  }, [])
  const closeFeature = () => { setSelected(null); setSelectedDetails(null); setDetailsError('') }
  return <section className="map-screen">
    <div className="step-heading"><h1>Карта сети</h1><p>{locationNotice || 'Определяем местоположение…'}</p></div>
    {error && <ErrorBox text={error} />}
    {loading && <div className="panel empty-state">Загрузка карты…</div>}
    {!loading && maps.length === 0 && <div className="panel empty-state">Нет доступных карт</div>}
    {maps.length > 1 && <label className="field"><span>Карта</span><select value={current?.id ?? ''} onChange={event => setCurrent(maps.find(map => map.id === event.target.value) ?? null)}>{maps.map(map => <option key={map.id} value={map.id}>{map.name}</option>)}</select></label>}
    {layers.length > 0 && <details className="gis-layers-panel"><summary>Слои <span>{selectedLayers.length} из {layers.length}</span></summary><div className="gis-layers">{layers.map(layer => <label key={layer.id}><input type="checkbox" checked={selectedLayers.includes(layer.id)} onChange={() => toggleLayer(layer.id)} />{layer.name}</label>)}</div></details>}
    {mapView && <MapCanvas data={data} position={position} view={mapView} onViewChange={handleViewChange} onBoundsChange={updateViewportBounds} onSelect={openFeature} onLocate={locate} locating={locating} />}
    {data?.truncated && <div className="error-box">Показана не вся сеть. Уточните область на карте.</div>}
    <GisFeatureCard key={selected?.id} feature={selected} details={selectedDetails} loading={detailsLoading} error={detailsError} csrfToken={session.csrf_token} onClose={closeFeature} />
  </section>
}

function AppShell({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const [tab, setTab] = useState<AppTab>('today')
  const [mapHeight, setMapHeight] = useState<GisMapHeight>(storedGisMapHeight)
  const [mapHeightPercent, setMapHeightPercent] = useState(storedGisMapHeightPercent)
  const paymentsAllowed = session.capabilities.payments
  const gisAllowed = session.capabilities.gis
  const messengerSettingsAllowed = session.capabilities.messenger_settings
  const ticketDay: TicketDay = tab === 'today' || tab === 'tomorrow' ? tab : 'today'
  useEffect(() => { applyGisMapHeight(mapHeight, mapHeightPercent); try { window.localStorage.setItem(GIS_MAP_HEIGHT_KEY, mapHeight); window.localStorage.setItem(GIS_MAP_HEIGHT_PERCENT_KEY, String(mapHeightPercent)) } catch { /* storage may be unavailable */ } }, [mapHeight, mapHeightPercent])
  return (
    <main className="app-page with-navigation">
      <header className="app-header">
        <div><strong>ТехПортал</strong><span>{session.user.first_name || session.user.email}</span></div>
        <div className="header-actions"><button className="logout-button" onClick={onLogout}>Выйти</button></div>
      </header>
      {tab === 'settings' && (messengerSettingsAllowed || gisAllowed)
        ? <Settings session={session} gisAllowed={gisAllowed} mapHeight={mapHeight} mapHeightPercent={mapHeightPercent} onMapHeightChange={setMapHeight} onMapHeightPercentChange={setMapHeightPercent} />
        : tab === 'payments' && paymentsAllowed
        ? <Payments session={session} />
        : tab === 'map' && gisAllowed
        ? <MapScreen session={session} />
        : <Tickets key={ticketDay} day={ticketDay} session={session} />}
      <nav className="bottom-nav" aria-label="Основные разделы">
        <button className={tab === 'today' ? 'active' : ''} onClick={() => setTab('today')} aria-label="Сегодня" title="Сегодня"><span aria-hidden="true">●</span></button>
        <button className={tab === 'tomorrow' ? 'active' : ''} onClick={() => setTab('tomorrow')} aria-label="Завтра" title="Завтра"><span aria-hidden="true">◐</span></button>
        {gisAllowed && <button className={tab === 'map' ? 'active' : ''} onClick={() => setTab('map')} aria-label="Карта" title="Карта"><span aria-hidden="true">⌖</span></button>}
        {paymentsAllowed && <button className={tab === 'payments' ? 'active' : ''} onClick={() => setTab('payments')} aria-label="Оплата" title="Оплата"><span aria-hidden="true">₽</span></button>}
        {(messengerSettingsAllowed || gisAllowed) && <button className={tab === 'settings' ? 'active' : ''} onClick={() => setTab('settings')} aria-label="Настройки" title="Настройки"><span aria-hidden="true">⚙</span></button>}
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
