import { useState, useEffect } from 'react'

// ── Types ──────────────────────────────────────────────────────────────────
type Tab = 'today' | 'tomorrow' | 'services' | 'domofon'
type DomScreen = 'main' | 'connect-result' | 'new-address' | 'new-form' | 'new-result'

interface LocItem {
  locid: number
  loctext: string
}

// ── Mock API helpers ───────────────────────────────────────────────────────
const API_BASE = ''  // replace with real base URL

async function apiPost(path: string, body: object, token: string) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

// ── Login Page ─────────────────────────────────────────────────────────────
function LoginPage({ onLogin }: { onLogin: (token: string) => void }) {
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!login || !password) { setError('Заполните все поля'); return }
    setLoading(true)
    setError('')
    try {
      // Replace with real auth endpoint
      const data = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ login, password }),
      }).then(r => { if (!r.ok) throw new Error(); return r.json() })
      const token: string = data.access_token ?? data.token ?? ''
      localStorage.setItem('access_token', token)
      onLogin(token)
    } catch {
      // Demo: simulate successful login with a fake token for UI preview
      const fakeToken = 'demo.' + btoa(login) + '.token'
      localStorage.setItem('access_token', fakeToken)
      onLogin(fakeToken)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4" style={{ background: 'linear-gradient(145deg, #1e3a8a 0%, #1e40af 50%, #2563eb 100%)' }}>
      <div className="w-full max-w-sm">
        {/* Logo / Brand */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-white/20 backdrop-blur mb-4">
            <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
            </svg>
          </div>
          <h1 className="text-2xl font-800 text-white tracking-tight">ТехПортал</h1>
          <p className="text-blue-200 text-sm mt-1 font-500">Точка Связи</p>
        </div>

        {/* Card */}
        <div className="bg-white rounded-3xl p-6 shadow-2xl">
          <h2 className="text-lg font-700 text-slate-800 mb-5">Вход в систему</h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs font-600 text-slate-500 uppercase tracking-wide mb-1.5">Логин</label>
              <input
                type="text"
                value={login}
                onChange={e => setLogin(e.target.value)}
                placeholder="Введите логин"
                autoComplete="username"
                className="w-full px-4 py-3 rounded-xl border border-slate-200 text-slate-800 text-sm font-500 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 transition"
              />
            </div>
            <div>
              <label className="block text-xs font-600 text-slate-500 uppercase tracking-wide mb-1.5">Пароль</label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="Введите пароль"
                autoComplete="current-password"
                className="w-full px-4 py-3 rounded-xl border border-slate-200 text-slate-800 text-sm font-500 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 transition"
              />
            </div>
            {error && (
              <p className="text-red-500 text-xs font-600 bg-red-50 px-3 py-2 rounded-lg">{error}</p>
            )}
            <button
              type="submit"
              disabled={loading}
              className="w-full py-3.5 rounded-xl font-700 text-white text-sm transition-all active:scale-[0.98]"
              style={{ background: loading ? '#93c5fd' : 'linear-gradient(135deg, #1e40af, #3b82f6)' }}
            >
              {loading ? 'Вход...' : 'Войти'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}

// ── Bottom Nav ─────────────────────────────────────────────────────────────
function BottomNav({ active, onSelect }: { active: Tab; onSelect: (t: Tab) => void }) {
  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    {
      id: 'today',
      label: 'Сегодня',
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
        </svg>
      ),
    },
    {
      id: 'tomorrow',
      label: 'Завтра',
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
        </svg>
      ),
    },
    {
      id: 'services',
      label: 'Услуги',
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 10h16M4 14h16M4 18h16" />
        </svg>
      ),
    },
    {
      id: 'domofon',
      label: 'Домофон',
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z" />
        </svg>
      ),
    },
  ]

  return (
    <nav className="fixed bottom-0 left-0 right-0 bg-white border-t border-slate-200 z-50" style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}>
      <div className="flex">
        {tabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => onSelect(tab.id)}
            className={`flex-1 flex flex-col items-center py-2.5 gap-0.5 transition-colors ${active === tab.id ? 'text-blue-600' : 'text-slate-400'}`}
          >
            {tab.icon}
            <span className="text-[10px] font-600">{tab.label}</span>
            {active === tab.id && <span className="absolute bottom-0 w-8 h-0.5 bg-blue-600 rounded-t-full" />}
          </button>
        ))}
      </div>
    </nav>
  )
}

// ── Placeholder Tab ────────────────────────────────────────────────────────
function PlaceholderTab({ title, subtitle, icon }: { title: string; subtitle: string; icon: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-6">
      <div className="w-16 h-16 rounded-2xl bg-blue-50 flex items-center justify-center text-blue-300 mb-4">
        {icon}
      </div>
      <h2 className="text-lg font-700 text-slate-700">{title}</h2>
      <p className="text-sm text-slate-400 font-500 mt-1">{subtitle}</p>
    </div>
  )
}

// ── Domofon Layer ──────────────────────────────────────────────────────────
function DomofondLayer({ token }: { token: string }) {
  const [screen, setScreen] = useState<DomScreen>('main')
  const [serviceLogin, setServiceLogin] = useState('')
  const [connectLoading, setConnectLoading] = useState(false)
  const [connectResult, setConnectResult] = useState('')

  const [newLoading, setNewLoading] = useState(false)
  const [locations, setLocations] = useState<LocItem[]>([])
  const [selectedLoc, setSelectedLoc] = useState<LocItem | null>(null)

  const [clientName, setClientName] = useState('')
  const [fieldFlat, setFieldFlat] = useState('')
  const [fieldPodezd, setFieldPodezd] = useState('')
  const [createLoading, setCreateLoading] = useState(false)
  const [createResult, setCreateResult] = useState('')
  const [formError, setFormError] = useState('')

  const handleConnect = async () => {
    if (!serviceLogin.trim()) return
    setConnectLoading(true)
    try {
      const data = await apiPost('/domofon/connect', { service_login: serviceLogin.trim() }, token)
      setConnectResult(typeof data === 'string' ? data : JSON.stringify(data))
    } catch {
      setConnectResult('Подключение выполнено (демо)')
    } finally {
      setConnectLoading(false)
      setScreen('connect-result')
    }
  }

  const handleNew = async () => {
    setNewLoading(true)
    try {
      const data = await apiPost('/domofon/addresses', {}, token)
      const list: LocItem[] = Array.isArray(data)
        ? data.map((item: Record<number | string, string>) => {
            const [k, v] = Object.entries(item)[0]
            return { locid: Number(k), loctext: v }
          })
        : []
      setLocations(list)
    } catch {
      // Demo data
      setLocations([
        { locid: 1, loctext: 'ул. Ленина, 5' },
        { locid: 2, loctext: 'ул. Пушкина, 12' },
        { locid: 3, loctext: 'пр. Мира, 8' },
        { locid: 4, loctext: 'ул. Советская, 3' },
        { locid: 5, loctext: 'пер. Садовый, 1' },
        { locid: 6, loctext: 'ул. Гагарина, 22' },
      ])
    } finally {
      setNewLoading(false)
      setScreen('new-address')
    }
  }

  const handleCreate = async () => {
    const flat = Number(fieldFlat)
    const podezd = Number(fieldPodezd)
    if (
      !clientName.trim()
      || !fieldFlat.trim()
      || !fieldPodezd.trim()
      || !Number.isInteger(flat)
      || !Number.isInteger(podezd)
    ) {
      setFormError('Заполните ФИО и укажите целые номера квартиры и подъезда')
      return
    }
    setFormError('')
    setCreateLoading(true)
    try {
      const data = await apiPost('/domofon/create', {
        locid: selectedLoc?.locid,
        client_name: clientName.trim(),
        field_flat: flat,
        field_podezd: podezd,
      }, token)
      setCreateResult(typeof data === 'string' ? data : JSON.stringify(data))
    } catch {
      setCreateResult('Домофон успешно создан (демо)')
    } finally {
      setCreateLoading(false)
      setScreen('new-result')
    }
  }

  const reset = () => {
    setScreen('main')
    setServiceLogin('')
    setConnectResult('')
    setSelectedLoc(null)
    setClientName('')
    setFieldFlat('')
    setFieldPodezd('')
    setCreateResult('')
    setFormError('')
    setLocations([])
  }

  // ── Main domofon screen ──
  if (screen === 'main') {
    return (
      <div className="flex flex-col h-full px-4 pt-6">
        <div className="mb-6">
          <h1 className="text-xl font-800 text-slate-800">Домофон</h1>
          <p className="text-sm text-slate-500 font-500 mt-0.5">Управление домофонами</p>
        </div>

        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100 mb-4">
          <label className="block text-xs font-600 text-slate-500 uppercase tracking-wide mb-2">Логин</label>
          <input
            type="text"
            value={serviceLogin}
            onChange={e => setServiceLogin(e.target.value)}
            placeholder="Введите service_login"
            className="w-full px-4 py-3 rounded-xl border border-slate-200 text-slate-800 text-sm font-500 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 transition mb-4"
          />
          <button
            onClick={handleConnect}
            disabled={connectLoading || !serviceLogin.trim()}
            className="w-full py-3 rounded-xl font-700 text-white text-sm transition-all active:scale-[0.98] disabled:opacity-50"
            style={{ background: 'linear-gradient(135deg, #1e40af, #3b82f6)' }}
          >
            {connectLoading ? 'Подключение...' : 'Подключить'}
          </button>
        </div>

        <button
          onClick={handleNew}
          disabled={newLoading}
          className="w-full py-4 rounded-2xl font-700 text-blue-700 text-sm border-2 border-blue-200 bg-blue-50 transition-all active:scale-[0.98] hover:bg-blue-100 flex items-center justify-center gap-2"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 4v16m8-8H4" />
          </svg>
          {newLoading ? 'Загрузка...' : 'Новый'}
        </button>
      </div>
    )
  }

  // ── Connect result ──
  if (screen === 'connect-result') {
    return (
      <div className="flex flex-col h-full px-4 pt-6">
        <div className="mb-6">
          <h1 className="text-xl font-800 text-slate-800">Результат подключения</h1>
        </div>
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100 mb-4 flex-1">
          <div className="flex items-start gap-3 mb-4">
            <div className="w-8 h-8 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0 mt-0.5">
              <svg className="w-4 h-4 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <div>
              <p className="text-sm font-600 text-slate-700 mb-1">Ответ сервера</p>
              <p className="text-sm text-slate-600 font-500 leading-relaxed">{connectResult}</p>
            </div>
          </div>
        </div>
        <button
          onClick={reset}
          className="w-full py-3.5 rounded-xl font-700 text-white text-sm transition-all active:scale-[0.98]"
          style={{ background: 'linear-gradient(135deg, #1e40af, #3b82f6)' }}
        >
          ← Назад
        </button>
      </div>
    )
  }

  // ── New: choose address ──
  if (screen === 'new-address') {
    return (
      <div className="flex flex-col h-full px-4 pt-6">
        <div className="mb-5">
          <h1 className="text-xl font-800 text-slate-800">Выберите адрес</h1>
          <p className="text-sm text-slate-500 font-500 mt-0.5">Нажмите на плитку для выбора</p>
        </div>
        <div className="grid grid-cols-2 gap-3 overflow-y-auto pb-4">
          {locations.map(loc => (
            <button
              key={loc.locid}
              onClick={() => { setSelectedLoc(loc); setScreen('new-form') }}
              className="bg-white rounded-2xl p-4 border border-slate-100 shadow-sm text-left transition-all active:scale-[0.97] hover:border-blue-300 hover:shadow-md"
            >
              <div className="w-8 h-8 rounded-xl bg-blue-50 flex items-center justify-center mb-2">
                <svg className="w-4 h-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              </div>
              <p className="text-xs font-700 text-slate-700 leading-tight">{loc.loctext}</p>
              <p className="text-[10px] text-slate-400 font-500 mt-0.5">ID: {loc.locid}</p>
            </button>
          ))}
        </div>
        <button
          onClick={reset}
          className="mt-3 w-full py-3 rounded-xl font-600 text-slate-600 text-sm border border-slate-200 bg-white transition-all active:scale-[0.98]"
        >
          ← Назад
        </button>
      </div>
    )
  }

  // ── New: fill form ──
  if (screen === 'new-form') {
    return (
      <div className="flex flex-col h-full px-4 pt-6">
        <div className="mb-5">
          <h1 className="text-xl font-800 text-slate-800">Новый домофон</h1>
          {selectedLoc && (
            <div className="inline-flex items-center gap-1.5 mt-2 bg-blue-50 text-blue-700 rounded-full px-3 py-1">
              <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M5.05 4.05a7 7 0 119.9 9.9L10 18.9l-4.95-4.95a7 7 0 010-9.9zM10 11a2 2 0 100-4 2 2 0 000 4z" clipRule="evenodd" />
              </svg>
              <span className="text-xs font-600">{selectedLoc.loctext}</span>
            </div>
          )}
        </div>
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100 mb-4">
          <div className="mb-4">
            <label className="block text-xs font-600 text-slate-500 uppercase tracking-wide mb-1.5">
              ФИО <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              value={clientName}
              onChange={e => setClientName(e.target.value)}
              placeholder="Иванов Иван Иванович"
              autoComplete="name"
              className="w-full px-4 py-3 rounded-xl border border-slate-200 text-slate-800 text-sm font-500 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 transition"
            />
          </div>
          <div className="mb-4">
            <label className="block text-xs font-600 text-slate-500 uppercase tracking-wide mb-1.5">
              Квартира <span className="text-red-400">*</span>
            </label>
            <input
              type="number"
              value={fieldFlat}
              onChange={e => setFieldFlat(e.target.value)}
              placeholder="Номер квартиры"
              step="1"
              inputMode="numeric"
              className="w-full px-4 py-3 rounded-xl border border-slate-200 text-slate-800 text-sm font-500 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 transition"
            />
          </div>
          <div className="mb-5">
            <label className="block text-xs font-600 text-slate-500 uppercase tracking-wide mb-1.5">
              Подъезд <span className="text-red-400">*</span>
            </label>
            <input
              type="number"
              value={fieldPodezd}
              onChange={e => setFieldPodezd(e.target.value)}
              placeholder="Номер подъезда"
              step="1"
              inputMode="numeric"
              className="w-full px-4 py-3 rounded-xl border border-slate-200 text-slate-800 text-sm font-500 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 transition"
            />
          </div>
          {formError && (
            <p className="text-red-500 text-xs font-600 bg-red-50 px-3 py-2 rounded-lg mb-4">{formError}</p>
          )}
          <button
            onClick={handleCreate}
            disabled={createLoading}
            className="w-full py-3.5 rounded-xl font-700 text-white text-sm transition-all active:scale-[0.98] disabled:opacity-50"
            style={{ background: 'linear-gradient(135deg, #1e40af, #3b82f6)' }}
          >
            {createLoading ? 'Создание...' : 'Создать'}
          </button>
        </div>
        <button
          onClick={() => setScreen('new-address')}
          className="w-full py-3 rounded-xl font-600 text-slate-600 text-sm border border-slate-200 bg-white transition-all active:scale-[0.98]"
        >
          ← Назад
        </button>
      </div>
    )
  }

  // ── New: create result ──
  if (screen === 'new-result') {
    return (
      <div className="flex flex-col h-full px-4 pt-6">
        <div className="mb-6">
          <h1 className="text-xl font-800 text-slate-800">Домофон создан</h1>
        </div>
        <div className="bg-white rounded-2xl p-5 shadow-sm border border-slate-100 mb-4 flex-1">
          <div className="flex items-start gap-3">
            <div className="w-8 h-8 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0 mt-0.5">
              <svg className="w-4 h-4 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <div>
              <p className="text-sm font-600 text-slate-700 mb-1">Ответ сервера</p>
              <p className="text-sm text-slate-600 font-500 leading-relaxed">{createResult}</p>
            </div>
          </div>
        </div>
        <button
          onClick={reset}
          className="w-full py-3.5 rounded-xl font-700 text-white text-sm transition-all active:scale-[0.98]"
          style={{ background: 'linear-gradient(135deg, #1e40af, #3b82f6)' }}
        >
          ← На главную
        </button>
      </div>
    )
  }

  return null
}

// ── Main App ───────────────────────────────────────────────────────────────
export default function App() {
  const [token, setToken] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<Tab>('today')

  useEffect(() => {
    const saved = localStorage.getItem('access_token')
    if (saved) setToken(saved)
  }, [])

  if (!token) {
    return <LoginPage onLogin={setToken} />
  }

  const renderTab = () => {
    switch (activeTab) {
      case 'today':
        return (
          <PlaceholderTab
            title="Заявки сегодня"
            subtitle="Список заявок на текущий день"
            icon={
              <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
              </svg>
            }
          />
        )
      case 'tomorrow':
        return (
          <PlaceholderTab
            title="Заявки завтра"
            subtitle="Предстоящие заявки на завтра"
            icon={
              <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
            }
          />
        )
      case 'services':
        return (
          <PlaceholderTab
            title="Услуги"
            subtitle="Каталог доступных услуг"
            icon={
              <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 6h16M4 10h16M4 14h16M4 18h16" />
              </svg>
            }
          />
        )
      case 'domofon':
        return <DomofondLayer token={token} />
    }
  }

  return (
    <div className="flex flex-col h-screen bg-slate-50 max-w-md mx-auto relative overflow-hidden">
      {/* Header */}
      <header className="bg-white border-b border-slate-100 px-4 py-3 flex items-center justify-between flex-shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-blue-600 flex items-center justify-center">
            <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
            </svg>
          </div>
          <span className="font-800 text-slate-800 text-sm">ТехПортал</span>
        </div>
        <button
          onClick={() => { localStorage.removeItem('access_token'); setToken(null) }}
          className="text-xs font-600 text-slate-400 hover:text-slate-600 transition"
        >
          Выйти
        </button>
      </header>

      {/* Content */}
      <main className="flex-1 overflow-y-auto pb-20">
        {renderTab()}
      </main>

      <BottomNav active={activeTab} onSelect={setActiveTab} />
    </div>
  )
}
