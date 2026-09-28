import { useEffect, useState } from 'react'

import { api } from './api'
import type { GisBasemapConfig } from './api'
import type { GisMapCanvas } from './GisMapCanvas'
import { YandexMapCanvas } from './YandexMapCanvas'
import { YandexV3MapCanvas } from './YandexV3MapCanvas'
import { mapSource } from './selectedMapDataProvider'
import { mobileMapConfig, mobileMapTiles } from './MobileMapDataProvider'

export const ProviderMapCanvas: GisMapCanvas = props => {
  const [config, setConfig] = useState<GisBasemapConfig | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [raster, setRaster] = useState(false)
  const [version, setVersion] = useState<string | null>(null)
  const [tileError, setTileError] = useState('')
  const [visibility, setVisibility] = useState({ network: true, poles: false })

  useEffect(() => {
    let saved = { network: true, poles: false }
    try {
      const value = JSON.parse(localStorage.getItem(`tp-pwa.mobilemap.lines:${props.mapId}`) || 'null')
      if (value) saved = { network: value.network !== false, poles: value.poles === true }
    } catch { /* retain defaults */ }
    setVisibility(saved)
  }, [props.mapId])

  useEffect(() => {
    setVersion(null); setTileError('')
    if (!raster || !props.mapId) return
    let controller: AbortController | null = null
    const refresh = () => {
      if (document.visibilityState === 'hidden') return
      controller?.abort(); controller = new AbortController()
      const signal = controller.signal
      mobileMapTiles(props.mapId!, signal).then(meta => {
        if (!signal.aborted) { setVersion(meta.lines_tile_version); setTileError('') }
      }).catch(cause => { if (!signal.aborted) setTileError(cause instanceof Error ? cause.message : 'Не удалось загрузить линии') })
    }
    refresh()
    document.addEventListener('visibilitychange', refresh)
    return () => { controller?.abort(); document.removeEventListener('visibilitychange', refresh) }
  }, [raster, props.mapId, attempt])

  const toggle = (key: 'network' | 'poles') => {
    const next = { ...visibility, [key]: !visibility[key] }
    setVisibility(next)
    try { localStorage.setItem(`tp-pwa.mobilemap.lines:${props.mapId}`, JSON.stringify(next)) } catch { /* retain session preference */ }
  }

  useEffect(() => {
    let live = true
    setConfig(null); setError('')
    const controller = new AbortController()
    mapSource(controller.signal).then(async source => {
      if (source === 'gis') return api.gisBasemap()
      const value = await mobileMapConfig(controller.signal)
      if (value.map_provider !== 'yandex21') throw new Error('Для карты mobilemap требуется провайдер yandex21')
      if (live) setRaster(value.line_render === 'raster')
      return { provider: 'yandex' as const, mobilemap: true,
        scriptUrl: value.yandex_maps_api_key ? `https://api-maps.yandex.ru/2.1/?apikey=${encodeURIComponent(value.yandex_maps_api_key)}&lang=ru_RU&csp=true` : null,
        pointIconSize: value.point_icon_size, pointCircleSize: value.point_circle_size,
        pointFixedSizeMaxZoom: value.point_fixed_size_max_zoom, iconFixed: value.icon_fixed }
    }).then(value => { if (live) setConfig(value) })
      .catch(cause => { if (live) setError(cause instanceof Error ? cause.message : 'Не удалось получить настройки карты') })
    return () => { live = false; controller.abort() }
  }, [attempt])

  if (error) return <div className="gis-map-canvas"><div className="gis-map-error"><p>{error}</p><button type="button" className="outline-button" onClick={() => setAttempt(value => value + 1)}>Повторить загрузку</button></div></div>
  if (!config) return <div className="gis-map-canvas"><p className="gis-map-status">Загрузка карты…</p></div>
  if (config.provider === 'yandex-v3') return <YandexV3MapCanvas {...props} config={config} />
  return <>
    {raster && <div className="gis-line-controls" aria-label="Слои линий">
      <label><input type="checkbox" checked={visibility.network} onChange={() => toggle('network')} /> Сеть (линии)</label>
      <label><input type="checkbox" checked={visibility.poles} onChange={() => toggle('poles')} /> Линии столбов</label>
      {tileError ? <span role="alert">{tileError} <button type="button" onClick={() => setAttempt(value => value + 1)}>Повторить</button></span> : !version && <span>Источник ещё не опубликовал тайлы линий</span>}
    </div>}
    <YandexMapCanvas {...props} config={config} lineTiles={raster && version && props.mapId ? { mapId: props.mapId, version, ...visibility } : null} />
  </>
}
