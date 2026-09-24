import { useEffect, useRef, useState } from 'react'

import { api } from './api'
import type { GisFeature } from './api'
import type { GisMapCanvas, GisMapCanvasProps } from './GisMapCanvas'

type Basemap = 'map' | 'hybrid'

const BASEMAP_KEY = 'tp-pwa.gis.basemap'
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i
let sdkPromise: Promise<any> | null = null

export function storedBasemap(storage: Pick<Storage, 'getItem'> = localStorage): Basemap {
  try { return storage.getItem(BASEMAP_KEY) === 'hybrid' ? 'hybrid' : 'map' } catch { return 'map' }
}

function loadSdk(url: string): Promise<any> {
  if ((window as any).ymaps) return Promise.resolve((window as any).ymaps)
  if (sdkPromise) return sdkPromise
  sdkPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    const timeout = window.setTimeout(() => fail(new Error('Превышено время ожидания API Яндекс Карт')), 20_000)
    const fail = (error: Error) => { window.clearTimeout(timeout); script.remove(); sdkPromise = null; reject(error) }
    script.src = url; script.async = true; script.onerror = () => fail(new Error('Не удалось загрузить API Яндекс Карт'))
    script.onload = () => {
      const ymaps = (window as any).ymaps
      if (!ymaps?.ready) return fail(new Error('API Яндекс Карт загрузился некорректно'))
      ymaps.ready(() => { window.clearTimeout(timeout); resolve(ymaps) })
    }
    document.head.append(script)
  })
  return sdkPromise
}

export function yandexCoordinates(geometry: GisFeature['geometry']): any {
  if (geometry.type === 'Point') { const [lng, lat] = geometry.coordinates as [number, number]; return [lat, lng] }
  if (geometry.type === 'LineString') return (geometry.coordinates as [number, number][]).map(([lng, lat]) => [lat, lng])
  return (geometry.coordinates as [number, number][][]).map(ring => ring.map(([lng, lat]) => [lat, lng]))
}

function color(value: string | undefined, fallback: string) { return /^#[a-f\d]{6}$/i.test(value ?? '') ? value! : fallback }

export const YandexMapCanvas: GisMapCanvas = ({ data, position, view, onViewChange, onBoundsChange, onSelect, onLocate, locating, initialMapView }: GisMapCanvasProps) => {
  const node = useRef<HTMLDivElement>(null); const map = useRef<any>(null); const ymaps = useRef<any>(null)
  const featureObjects = useRef<any>(null); const positionObject = useRef<any>(null); const viewRef = useRef(view); const [basemap, setBasemap] = useState<Basemap>(storedBasemap)
  const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [fullscreen, setFullscreen] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const root = useRef<HTMLDivElement>(null)
  const selectRef = useRef(onSelect)
  viewRef.current = view; selectRef.current = onSelect

  useEffect(() => {
    let live = true
    setLoading(true); setError('')
    api.gisBasemap().then(result => {
      if (result.provider !== 'yandex') throw new Error(`Карта недоступна: провайдер ${result.provider} не поддерживается`) 
      if (!result.scriptUrl) throw new Error('Карта недоступна: не настроен ключ API Яндекс Карт')
      return loadSdk(result.scriptUrl)
    }).then(sdk => {
      if (!live || !node.current) return
      ymaps.current = sdk
      map.current = new sdk.Map(node.current, { center: [viewRef.current.latitude, viewRef.current.longitude], zoom: viewRef.current.zoom, type: `yandex#${basemap}`, controls: [] }, { suppressMapOpenBlock: true })
      featureObjects.current = new sdk.GeoObjectCollection()
      positionObject.current = new sdk.GeoObjectCollection()
      map.current.geoObjects.add(featureObjects.current)
      map.current.geoObjects.add(positionObject.current)
      const report = () => {
        const center = map.current.getCenter(); const bounds = map.current.getBounds()
        onViewChange({ longitude: center[1], latitude: center[0], zoom: map.current.getZoom() })
        onBoundsChange([bounds[0][1], bounds[0][0], bounds[1][1], bounds[1][0]])
      }
      map.current.events.add('boundschange', report); report(); setLoading(false)
    }).catch(cause => { if (live) { setError(cause instanceof Error ? cause.message : 'Не удалось загрузить карту'); setLoading(false) } })
    return () => { live = false; if (map.current) { map.current.destroy(); map.current = null; featureObjects.current = null; positionObject.current = null } }
    // Component lifecycle owns the Yandex map. Changes below update this instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt])

  useEffect(() => {
    const instance = map.current
    if (!instance) return
    const center = instance.getCenter()
    if (Math.abs(center[0] - view.latitude) > .000001 || Math.abs(center[1] - view.longitude) > .000001 || instance.getZoom() !== view.zoom) instance.setCenter([view.latitude, view.longitude], view.zoom, { duration: 0 })
  }, [view.latitude, view.longitude, view.zoom])

  useEffect(() => {
    const instance = map.current; const sdk = ymaps.current; const collection = featureObjects.current
    if (!instance || !sdk || !collection) return
    collection.removeAll()
    const add = (object: any, feature?: GisFeature) => { if (feature) object.events.add('click', () => selectRef.current(feature)); collection.add(object) }
    for (const feature of data?.features ?? []) {
      const props = feature.properties
      if (feature.geometry.type === 'Polygon') add(new sdk.Polygon(yandexCoordinates(feature.geometry), {}, { strokeColor: color(props.lineColor, color(props.fillColor, '#ab47bc')), strokeWidth: props.lineWidth || 3, strokeOpacity: props.lineOpacity ?? 1, fillColor: color(props.fillColor, '#ab47bc'), fillOpacity: props.fillOpacity ?? .2 }), feature)
      else if (feature.geometry.type === 'LineString') add(new sdk.Polyline(yandexCoordinates(feature.geometry), {}, { strokeColor: color(props.lineColor, '#2563eb'), strokeWidth: props.lineWidth || 5, strokeOpacity: props.lineOpacity ?? 1, strokeLineCap: 'round', strokeLineJoin: 'round' }), feature)
      else {
        const point = yandexCoordinates(feature.geometry); const scale = props.iconScale || 1; const markerColor = color(props.iconColor, '#0288d1'); const assetId = props.iconId
        if (assetId && UUID.test(assetId)) {
          const suffix = props.recolorIcon ? `?color=${markerColor.slice(1)}` : ''
          add(new sdk.Placemark(point, {}, { iconLayout: 'default#image', iconImageHref: `/api/gis/assets/${assetId}${suffix}`, iconImageSize: [32 * scale, 32 * scale], iconImageOffset: [-16 * scale, -16 * scale] }), feature)
        } else add(new sdk.Placemark(point, {}, props.markerShape === 'pin' ? { preset: 'islands#blueDotIcon', iconColor: markerColor } : { preset: 'islands#circleIcon', iconColor: markerColor, iconImageSize: [22 * scale, 22 * scale] }), feature)
      }
    }
  }, [data])

  useEffect(() => {
    const sdk = ymaps.current; const collection = positionObject.current
    if (!sdk || !collection) return
    collection.removeAll()
    if (position) collection.add(new sdk.Placemark([position.latitude, position.longitude], {}, { preset: 'islands#blueCircleIcon' }))
  }, [position])

  useEffect(() => {
    const update = () => { setFullscreen(document.fullscreenElement === root.current); map.current?.container.fitToViewport() }
    document.addEventListener('fullscreenchange', update); return () => document.removeEventListener('fullscreenchange', update)
  }, [])

  useEffect(() => {
    if (!root.current || !globalThis.ResizeObserver) return
    const observer = new ResizeObserver(() => map.current?.container.fitToViewport())
    observer.observe(root.current)
    return () => observer.disconnect()
  }, [])

  const retry = () => { sdkPromise = null; setAttempt(value => value + 1) }
  const switchBasemap = (next: Basemap) => {
    if (!map.current || next === basemap) return
    try { map.current.setType(`yandex#${next}`); setBasemap(next); try { localStorage.setItem(BASEMAP_KEY, next) } catch { /* retain this session's choice */ } } catch { setError('Не удалось переключить подложку') }
  }
  const coordinates = (data?.features ?? []).flatMap(feature => feature.geometry.type === 'Point' ? [feature.geometry.coordinates as [number, number]] : feature.geometry.type === 'LineString' ? feature.geometry.coordinates as [number, number][] : (feature.geometry.coordinates as [number, number][][]).flat())
  return <div ref={root} className="gis-map-canvas" role="application" aria-label="Карта сети">
    <div ref={node} className="gis-yandex-map" />
    {loading && <p className="gis-map-status">Загрузка карты…</p>}
    {error && <div className="gis-map-error"><p>{error}</p><button type="button" className="outline-button" onClick={retry}>Повторить загрузку</button></div>}
    {!error && <><div className="gis-map-controls" onPointerDown={event => event.stopPropagation()}>
      <button type="button" onClick={() => map.current?.setZoom(map.current.getZoom() + 1)} aria-label="Увеличить масштаб">+</button><button type="button" onClick={() => map.current?.setZoom(map.current.getZoom() - 1)} aria-label="Уменьшить масштаб">−</button><button type="button" onClick={onLocate} disabled={locating} aria-label="Показать моё местоположение">⌖</button><button type="button" onClick={() => coordinates.length && onViewChange(initialMapView(coordinates))} disabled={!coordinates.length} aria-label="Показать загруженные объекты">⌂</button><button type="button" onClick={() => void (document.fullscreenElement ? document.exitFullscreen() : root.current?.requestFullscreen())} aria-label={fullscreen ? 'Свернуть карту' : 'Открыть карту на весь экран'}>{fullscreen ? '⊡' : '⛶'}</button>
    </div><label className="gis-basemap-picker"><span>Подложка</span><select value={basemap} disabled={!map.current} onChange={event => switchBasemap(event.target.value as Basemap)} aria-label="Подложка карты"><option value="map">Схема</option><option value="hybrid">Гибрид</option></select></label></>}
  </div>
}
