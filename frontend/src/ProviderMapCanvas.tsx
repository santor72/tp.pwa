import { useEffect, useState } from 'react'

import { api } from './api'
import type { GisBasemapConfig } from './api'
import type { GisMapCanvas } from './GisMapCanvas'
import { YandexMapCanvas } from './YandexMapCanvas'
import { YandexV3MapCanvas } from './YandexV3MapCanvas'

export const ProviderMapCanvas: GisMapCanvas = props => {
  const [config, setConfig] = useState<GisBasemapConfig | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let live = true
    setConfig(null); setError('')
    api.gisBasemap()
      .then(value => { if (live) setConfig(value) })
      .catch(cause => { if (live) setError(cause instanceof Error ? cause.message : 'Не удалось получить настройки карты') })
    return () => { live = false }
  }, [attempt])

  if (error) return <div className="gis-map-canvas"><div className="gis-map-error"><p>{error}</p><button type="button" className="outline-button" onClick={() => setAttempt(value => value + 1)}>Повторить загрузку</button></div></div>
  if (!config) return <div className="gis-map-canvas"><p className="gis-map-status">Загрузка карты…</p></div>
  if (config.provider === 'yandex-v3') return <YandexV3MapCanvas {...props} config={config} />
  return <YandexMapCanvas {...props} config={config} />
}
