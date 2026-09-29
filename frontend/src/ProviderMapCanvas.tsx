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
  const visibility = props.lineVisibility ?? { network: true, poles: false }

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

  useEffect(() => {
    let live = true
    setConfig(null); setError('')
    setRaster(false)
    props.onLineOptionsChange?.(false)
    const controller = new AbortController()
    mapSource(controller.signal).then(async source => {
      if (source === 'gis') return api.gisBasemap()
      const value = await mobileMapConfig(controller.signal)
      if (value.map_provider !== 'yandex21') throw new Error('Для карты mobilemap требуется провайдер yandex21')
      if (live) { setRaster(value.line_render === 'raster'); props.onLineOptionsChange?.(value.line_render === 'raster') }
      return { provider: 'yandex' as const, mobilemap: true,
        scriptUrl: value.yandex_maps_api_key ? `https://api-maps.yandex.ru/2.1/?apikey=${encodeURIComponent(value.yandex_maps_api_key)}&lang=ru_RU&csp=true` : null,
        pointIconSize: value.point_icon_size, pointCircleSize: value.point_circle_size,
        pointFixedSizeMaxZoom: value.point_fixed_size_max_zoom, iconFixed: value.icon_fixed }
    }).then(value => { if (live) setConfig(value) })
      .catch(cause => { if (live) setError(cause instanceof Error ? cause.message : 'Не удалось получить настройки карты') })
    return () => { live = false; controller.abort() }
  }, [attempt, props.onLineOptionsChange])

  useEffect(() => { props.onLineOptionsChange?.(false) }, [props.mapId, props.onLineOptionsChange])

  if (error) return <div className="gis-map-canvas"><div className="gis-map-error"><p>{error}</p><button type="button" className="outline-button" onClick={() => setAttempt(value => value + 1)}>Повторить загрузку</button></div></div>
  if (!config) return <div className="gis-map-canvas"><p className="gis-map-status">Загрузка карты…</p></div>
  if (config.provider === 'yandex-v3') return <YandexV3MapCanvas {...props} config={config} />
  return <>
    {tileError && <div className="gis-map-notice" role="alert">{tileError} <button type="button" onClick={() => setAttempt(value => value + 1)}>Повторить загрузку линий</button></div>}
    <YandexMapCanvas {...props} config={config} lineTiles={raster && version && props.mapId ? { mapId: props.mapId, version, ...visibility } : null} />
  </>
}
