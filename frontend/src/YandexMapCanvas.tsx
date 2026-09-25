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

export const YandexMapCanvas: GisMapCanvas = ({ data, position, view, onViewChange, onBoundsChange, onInteractionChange, onSelect, onLocate, locating, initialMapView }: GisMapCanvasProps) => {
  const node = useRef<HTMLDivElement>(null); const map = useRef<any>(null); const ymaps = useRef<any>(null)
  const pointObjects = useRef<any>(null); const lineObjects = useRef<any>(null); const positionObject = useRef<any>(null); const viewRef = useRef(view); const [basemap, setBasemap] = useState<Basemap>(storedBasemap)
  const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [fullscreen, setFullscreen] = useState(false)
  const [attempt, setAttempt] = useState(0); const [mapVersion, setMapVersion] = useState(0)
  const root = useRef<HTMLDivElement>(null)
  const selectRef = useRef(onSelect); const onViewChangeRef = useRef(onViewChange); const initialMapViewRef = useRef(initialMapView)
  const pointFeatures = useRef(new Map<string, GisFeature>()); const pointVersions = useRef(new Map<string, string>()); const lineVersions = useRef(new Map<string, string>())
  const interacting = useRef(false); const pendingData = useRef<GisMapCanvasProps['data'] | undefined>(undefined); const [renderedData, setRenderedData] = useState(data)
  viewRef.current = view; selectRef.current = onSelect; onViewChangeRef.current = onViewChange; initialMapViewRef.current = initialMapView

  useEffect(() => {
    if (interacting.current) { pendingData.current = data; return }
    setRenderedData(data)
  }, [data])

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
      pointObjects.current = new sdk.ObjectManager({ clusterize: false, geoObjectOpenBalloonOnClick: false })
      lineObjects.current = new sdk.ObjectManager({ clusterize: false, geoObjectInteractivityModel: 'default#transparent' })
      positionObject.current = new sdk.GeoObjectCollection()
      map.current.geoObjects.add(lineObjects.current)
      map.current.geoObjects.add(pointObjects.current)
      map.current.geoObjects.add(positionObject.current)
      setMapVersion(value => value + 1)
      pointObjects.current.objects.events.add('click', (event: any) => {
        const feature = pointFeatures.current.get(String(event.get('objectId')))
        if (!feature) return
        if (feature.properties.cluster) {
          const bounds = feature.properties.bbox
          if (bounds) onViewChangeRef.current(initialMapViewRef.current([[bounds[0], bounds[1]], [bounds[2], bounds[3]]]))
          return
        }
        selectRef.current(feature)
      })
      const report = () => {
        const center = map.current.getCenter(); const bounds = map.current.getBounds()
        onViewChange({ longitude: center[1], latitude: center[0], zoom: map.current.getZoom() })
        onBoundsChange([bounds[0][1], bounds[0][0], bounds[1][1], bounds[1][0]])
      }
      const actionBegin = () => { interacting.current = true; pendingData.current = undefined; onInteractionChange(true) }
      const actionEnd = () => {
        interacting.current = false
        if (pendingData.current !== undefined) { setRenderedData(pendingData.current); pendingData.current = undefined }
        onInteractionChange(false)
        report()
      }
      map.current.events.add('actionbegin', actionBegin)
      map.current.events.add('actionend', actionEnd)
      report(); setLoading(false)
    }).catch(cause => { if (live) { setError(cause instanceof Error ? cause.message : 'Не удалось загрузить карту'); setLoading(false) } })
    return () => { live = false; onInteractionChange(false); if (map.current) { map.current.destroy(); map.current = null; pointObjects.current = null; lineObjects.current = null; positionObject.current = null; pointFeatures.current.clear(); pointVersions.current.clear(); lineVersions.current.clear() } }
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
    const points = pointObjects.current; const lines = lineObjects.current
    if (!points || !lines) return
    const nextPoints = new Map<string, GisFeature>(); const nextLines = new Map<string, GisFeature>()
    for (const feature of renderedData?.features ?? []) {
      if (feature.geometry.type === 'Point') nextPoints.set(feature.id, feature)
      else if (feature.geometry.type === 'LineString') nextLines.set(feature.id, feature)
    }
    const sync = (manager: any, current: React.MutableRefObject<Map<string, string>>, next: Map<string, GisFeature>, options: (feature: GisFeature) => Record<string, unknown>) => {
      for (const id of current.current.keys()) if (!next.has(id)) manager.remove(id)
      const versions = new Map<string, string>()
      for (const [id, feature] of next) {
        const version = JSON.stringify(feature)
        versions.set(id, version)
        if (current.current.get(id) === version) continue
        if (current.current.has(id)) manager.remove(id)
        manager.add({ type: 'Feature', id, geometry: { type: feature.geometry.type, coordinates: yandexCoordinates(feature.geometry) }, properties: feature.properties, options: options(feature) })
      }
      current.current = versions
    }
    sync(points, pointVersions, nextPoints, feature => {
      const props = feature.properties
      if (props.cluster) return { preset: 'islands#blueCircleIcon', iconColor: '#2563eb', iconContent: String(props.count ?? '') }
      const scale = props.iconScale || 1; const markerColor = color(props.iconColor, '#0288d1'); const assetId = props.iconId
      if (assetId && UUID.test(assetId)) {
        const suffix = props.recolorIcon ? `?color=${markerColor.slice(1)}` : ''
        return { iconLayout: 'default#image', iconImageHref: `/api/gis/assets/${assetId}${suffix}`, iconImageSize: [32 * scale, 32 * scale], iconImageOffset: [-16 * scale, -16 * scale] }
      }
      return props.markerShape === 'pin' ? { preset: 'islands#blueDotIcon', iconColor: markerColor } : { preset: 'islands#circleIcon', iconColor: markerColor, iconImageSize: [22 * scale, 22 * scale] }
    })
    pointFeatures.current = nextPoints
    sync(lines, lineVersions, nextLines, feature => {
      const props = feature.properties
      return { strokeColor: color(props.lineColor, '#2563eb'), strokeWidth: props.lineWidth || 5, strokeOpacity: props.lineOpacity ?? 1, strokeLineCap: 'round', strokeLineJoin: 'round', interactivityModel: 'default#transparent' }
    })
  }, [renderedData, mapVersion])

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
  const showLoadedObjects = () => {
    const coordinates = (renderedData?.features ?? []).flatMap(feature => feature.geometry.type === 'Point' ? [feature.geometry.coordinates as [number, number]] : feature.geometry.type === 'LineString' ? feature.geometry.coordinates as [number, number][] : (feature.geometry.coordinates as [number, number][][]).flat())
    if (coordinates.length) onViewChange(initialMapView(coordinates))
  }
  return <div ref={root} className="gis-map-canvas" role="application" aria-label="Карта сети" onClickCapture={event => {
    const button = event.target instanceof Element ? event.target.closest('button') : null
    if (button && !button.hasAttribute('type')) event.preventDefault()
  }}>
    <div ref={node} className="gis-yandex-map" />
    {loading && <p className="gis-map-status">Загрузка карты…</p>}
    {error && <div className="gis-map-error"><p>{error}</p><button type="button" className="outline-button" onClick={retry}>Повторить загрузку</button></div>}
    {!error && <><div className="gis-map-controls" onPointerDown={event => event.stopPropagation()}>
      <button type="button" onClick={() => map.current?.setZoom(map.current.getZoom() + 1)} aria-label="Увеличить масштаб">+</button><button type="button" onClick={() => map.current?.setZoom(map.current.getZoom() - 1)} aria-label="Уменьшить масштаб">−</button><button type="button" onClick={onLocate} disabled={locating} aria-label="Показать моё местоположение">⌖</button><button type="button" onClick={showLoadedObjects} disabled={!renderedData?.features.length} aria-label="Показать загруженные объекты">⌂</button><button type="button" onClick={() => void (document.fullscreenElement ? document.exitFullscreen() : root.current?.requestFullscreen())} aria-label={fullscreen ? 'Свернуть карту' : 'Открыть карту на весь экран'}>{fullscreen ? '⊡' : '⛶'}</button>
    </div><label className="gis-basemap-picker"><span>Подложка</span><select value={basemap} disabled={!map.current} onChange={event => switchBasemap(event.target.value as Basemap)} aria-label="Подложка карты"><option value="map">Схема</option><option value="hybrid">Гибрид</option></select></label></>}
  </div>
}
