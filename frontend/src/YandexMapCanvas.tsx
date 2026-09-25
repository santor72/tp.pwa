import { useEffect, useRef, useState } from 'react'

import type { GisFeature } from './api'
import type { ConfiguredGisMapCanvas, GisMapCanvasProps } from './GisMapCanvas'
import { renderInBatches } from './GisRenderQueue'

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

const LINE_SHOW_PIXELS = 12
const LINE_HIDE_PIXELS = 8
const POINT_ICON_SIZE = 32
const POINT_CIRCLE_SIZE = 22
type Point = [number, number]

function screenPoint([longitude, latitude]: Point, zoom: number): Point {
  const world = 256 * 2 ** zoom; const latitudeRadians = Math.max(-85.05112878, Math.min(85.05112878, latitude)) * Math.PI / 180
  return [(longitude + 180) / 360 * world, (1 - Math.asinh(Math.tan(latitudeRadians)) / Math.PI) / 2 * world]
}
function clippedSegment(start: Point, end: Point, bounds: [number, number, number, number]): [Point, Point] | null {
  let [x0, y0] = start; let [x1, y1] = end; const [west, south, east, north] = bounds
  const code = (x: number, y: number) => (x < west ? 1 : x > east ? 2 : 0) | (y < south ? 4 : y > north ? 8 : 0)
  for (;;) {
    const startCode = code(x0, y0); const endCode = code(x1, y1)
    if (!(startCode | endCode)) return [[x0, y0], [x1, y1]]
    if (startCode & endCode) return null
    const outside = startCode || endCode; let x = 0; let y = 0
    if (outside & 8) { x = x0 + (x1 - x0) * (north - y0) / (y1 - y0); y = north }
    else if (outside & 4) { x = x0 + (x1 - x0) * (south - y0) / (y1 - y0); y = south }
    else if (outside & 2) { y = y0 + (y1 - y0) * (east - x0) / (x1 - x0); x = east }
    else { y = y0 + (y1 - y0) * (west - x0) / (x1 - x0); x = west }
    if (outside === startCode) { x0 = x; y0 = y } else { x1 = x; y1 = y }
  }
}
export const YandexMapCanvas: ConfiguredGisMapCanvas = ({ config, data, position, view, onViewChange, onBoundsChange, onInteractionChange, onSelect, onLocate, locating, initialMapView }) => {
  const node = useRef<HTMLDivElement>(null); const map = useRef<any>(null); const ymaps = useRef<any>(null)
  const pointObjects = useRef<any>(null); const lineObjects = useRef<any>(null); const positionObject = useRef<any>(null); const viewRef = useRef(view); const [basemap, setBasemap] = useState<Basemap>(storedBasemap)
  const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [fullscreen, setFullscreen] = useState(false)
  const [attempt, setAttempt] = useState(0); const [mapVersion, setMapVersion] = useState(0); const [pointIconSize, setPointIconSize] = useState(POINT_ICON_SIZE); const [pointCircleSize, setPointCircleSize] = useState(POINT_CIRCLE_SIZE); const [pointFixedSizeMaxZoom, setPointFixedSizeMaxZoom] = useState(15)
  const root = useRef<HTMLDivElement>(null)
  const selectRef = useRef(onSelect)
  const renderController = useRef<AbortController | null>(null)
  const pointFeatures = useRef(new Map<string, GisFeature>()); const pointVersions = useRef(new Map<string, string>()); const lineVersions = useRef(new Map<string, string>()); const lineVisibility = useRef(new Map<string, boolean>())
  const interacting = useRef(false); const pendingData = useRef<GisMapCanvasProps['data'] | undefined>(undefined); const [renderedData, setRenderedData] = useState(data)
  viewRef.current = view; selectRef.current = onSelect

  useEffect(() => {
    if (interacting.current) { pendingData.current = data; return }
    setRenderedData(data)
  }, [data])

  useEffect(() => {
    let live = true
    let renderFrame: number | null = null
    setLoading(true); setError('')
    if (!config.scriptUrl) {
      setError('Карта недоступна: не настроен ключ API Яндекс Карт'); setLoading(false)
      return () => { live = false }
    }
    setPointIconSize(config.pointIconSize ?? POINT_ICON_SIZE); setPointCircleSize(config.pointCircleSize ?? POINT_CIRCLE_SIZE); setPointFixedSizeMaxZoom(config.pointFixedSizeMaxZoom ?? 15)
    loadSdk(config.scriptUrl).then(sdk => {
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
        if (feature.properties.interactive !== false) selectRef.current(feature)
      })
      const report = () => {
        const center = map.current.getCenter(); const bounds = map.current.getBounds()
        onViewChange({ longitude: center[1], latitude: center[0], zoom: map.current.getZoom() })
        onBoundsChange([bounds[0][1], bounds[0][0], bounds[1][1], bounds[1][0]])
      }
      const actionBegin = () => { renderController.current?.abort(); interacting.current = true; pendingData.current = undefined; onInteractionChange(true) }
      const actionEnd = () => {
        interacting.current = false
        if (pendingData.current !== undefined) { setRenderedData(pendingData.current); pendingData.current = undefined }
        onInteractionChange(false); report()
        renderFrame = window.requestAnimationFrame(() => { renderFrame = null; if (!interacting.current) setMapVersion(value => value + 1) })
      }
      const boundsChange = () => { if (!interacting.current) report() }
      map.current.events.add('actionbegin', actionBegin)
      map.current.events.add('actionend', actionEnd)
      map.current.events.add('boundschange', boundsChange)
      report(); setLoading(false)
    }).catch(cause => { if (live) { setError(cause instanceof Error ? cause.message : 'Не удалось загрузить карту'); setLoading(false) } })
    return () => { live = false; if (renderFrame !== null) window.cancelAnimationFrame(renderFrame); renderController.current?.abort(); onInteractionChange(false); if (map.current) { map.current.destroy(); map.current = null; pointObjects.current = null; lineObjects.current = null; positionObject.current = null; pointFeatures.current.clear(); pointVersions.current.clear(); lineVersions.current.clear() } }
    // Component lifecycle owns the Yandex map. Changes below update this instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt, config.scriptUrl])

  useEffect(() => {
    const instance = map.current
    if (!instance) return
    const center = instance.getCenter()
    if (Math.abs(center[0] - view.latitude) > .000001 || Math.abs(center[1] - view.longitude) > .000001 || instance.getZoom() !== view.zoom) {
      instance.setCenter([view.latitude, view.longitude], view.zoom, { duration: 0 })
    }
  }, [view.latitude, view.longitude, view.zoom])

  useEffect(() => {
    const points = pointObjects.current; const lines = lineObjects.current
    if (!points || !lines) return
    renderController.current?.abort()
    const controller = new AbortController(); renderController.current = controller; const mapBounds = map.current?.getBounds()
    const sync = async () => {
      const nextPointIds = new Set<string>(); const nextLineIds = new Set<string>()
      const applied = await renderInBatches(renderedData?.features ?? [], feature => {
        if (feature.geometry.type === 'Point') {
          nextPointIds.add(feature.id)
          const props = feature.properties
          const interactivity = props.interactive === false ? { interactivityModel: 'default#transparent' } : {}
          const fixedSize = view.zoom < pointFixedSizeMaxZoom; const scale = fixedSize ? 1 : props.iconScale || 1; const iconSize = fixedSize ? pointIconSize : POINT_ICON_SIZE * scale; const circleSize = fixedSize ? pointCircleSize : POINT_CIRCLE_SIZE * scale
          const markerColor = color(props.iconColor, '#0288d1'); const assetId = props.iconId
          const options = assetId && UUID.test(assetId)
            ? { ...interactivity, iconLayout: 'default#image', iconImageHref: `/api/gis/assets/${assetId}${props.recolorIcon ? `?color=${markerColor.slice(1)}` : ''}`, iconImageSize: [iconSize, iconSize], iconImageOffset: [-iconSize / 2, -iconSize / 2] }
            : props.markerShape === 'pin'
              ? { ...interactivity, preset: 'islands#blueDotIcon', iconColor: markerColor, iconImageSize: [iconSize, iconSize] }
              : { ...interactivity, preset: 'islands#circleIcon', iconColor: markerColor, iconImageSize: [circleSize, circleSize] }
          // The feature itself can stay unchanged while its presentation changes
          // at the fixed-size zoom threshold. Include the resolved options so
          // existing objects are recreated with the current icon dimensions.
          const version = JSON.stringify({ feature, options })
          if (pointVersions.current.get(feature.id) !== version) {
            if (pointVersions.current.has(feature.id)) points.remove([feature.id])
            points.add({ type: 'Feature', id: feature.id, geometry: { type: feature.geometry.type, coordinates: yandexCoordinates(feature.geometry) }, properties: feature.properties, options })
            pointVersions.current.set(feature.id, version)
          }
          pointFeatures.current.set(feature.id, feature)
        } else if (feature.geometry.type === 'LineString' && mapBounds) {
        const bounds: [number, number, number, number] = [mapBounds[0][1], mapBounds[0][0], mapBounds[1][1], mapBounds[1][0]]
        const source = feature.geometry.coordinates as Point[]; const visible: Point[] = []
        for (let index = 1; index < source.length; index++) { const segment = clippedSegment(source[index - 1], source[index], bounds); if (segment) { if (!visible.length || visible[visible.length - 1][0] !== segment[0][0] || visible[visible.length - 1][1] !== segment[0][1]) visible.push(segment[0]); visible.push(segment[1]) } }
        const line = visible; const pixels = line.map(point => screenPoint(point, view.zoom)); const length = pixels.slice(1).reduce((total, point, index) => total + Math.hypot(point[0] - pixels[index][0], point[1] - pixels[index][1]), 0)
        const shown = lineVisibility.current.get(feature.id) === true ? length >= LINE_HIDE_PIXELS : length > LINE_SHOW_PIXELS
        lineVisibility.current.set(feature.id, shown)
        if (shown && line.length > 1) {
          nextLineIds.add(feature.id)
          const visibleFeature = { ...feature, geometry: { type: 'LineString' as const, coordinates: line } }
          const version = JSON.stringify(visibleFeature)
          if (lineVersions.current.get(feature.id) !== version) {
            if (lineVersions.current.has(feature.id)) lines.remove([feature.id])
            const props = feature.properties
            lines.add({ type: 'Feature', id: feature.id, geometry: { type: 'LineString', coordinates: yandexCoordinates(visibleFeature.geometry) }, properties: feature.properties, options: { strokeColor: color(props.lineColor, '#2563eb'), strokeWidth: props.lineWidth || 5, strokeOpacity: props.lineOpacity ?? 1, strokeLineCap: 'round', strokeLineJoin: 'round', interactivityModel: 'default#transparent' } })
            lineVersions.current.set(feature.id, version)
          }
        }
        }
      }, controller.signal)
      if (!applied) return
      const stalePoints = [...pointVersions.current.keys()].filter(id => !nextPointIds.has(id))
      const staleLines = [...lineVersions.current.keys()].filter(id => !nextLineIds.has(id))
      await renderInBatches(stalePoints, id => { points.remove([id]); pointVersions.current.delete(id); pointFeatures.current.delete(id) }, controller.signal)
      await renderInBatches(staleLines, id => { lines.remove([id]); lineVersions.current.delete(id); lineVisibility.current.delete(id) }, controller.signal)
    }
    void sync()
    return () => { controller.abort(); if (renderController.current === controller) renderController.current = null }
  }, [renderedData, mapVersion, view.longitude, view.latitude, view.zoom, pointIconSize, pointCircleSize, pointFixedSizeMaxZoom])

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
  return <div ref={root} className="gis-map-canvas" role="application" aria-label="Карта сети" onInvalidCapture={event => event.preventDefault()} onClickCapture={event => {
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
