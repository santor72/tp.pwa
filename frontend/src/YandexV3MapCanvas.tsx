import { useEffect, useRef, useState } from 'react'

import type { GisFeature } from './api'
import type { ConfiguredGisMapCanvas, GisMapCanvasProps } from './GisMapCanvas'
import { renderInBatches } from './GisRenderQueue'

const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i
const POINT_ICON_SIZE = 32
const POINT_CIRCLE_SIZE = 22
let sdkPromise: Promise<any> | null = null

function loadSdk(url: string): Promise<any> {
  if ((window as any).ymaps3) return Promise.resolve((window as any).ymaps3).then(async sdk => { await sdk.ready; return sdk })
  if (sdkPromise) return sdkPromise
  sdkPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    const timeout = window.setTimeout(() => fail(new Error('Превышено время ожидания API Яндекс Карт v3')), 20_000)
    const fail = (error: Error) => { window.clearTimeout(timeout); script.remove(); sdkPromise = null; reject(error) }
    script.src = url; script.async = true
    script.onerror = () => fail(new Error('Не удалось загрузить API Яндекс Карт v3'))
    script.onload = async () => {
      try {
        const sdk = (window as any).ymaps3
        if (!sdk?.ready) return fail(new Error('API Яндекс Карт v3 загрузился некорректно'))
        await sdk.ready; window.clearTimeout(timeout); resolve(sdk)
      } catch (cause) { fail(cause instanceof Error ? cause : new Error('Не удалось инициализировать API Яндекс Карт v3')) }
    }
    document.head.append(script)
  })
  return sdkPromise
}

function color(value: string | undefined, fallback: string) { return /^#[a-f\d]{6}$/i.test(value ?? '') ? value! : fallback }
function withOpacity(value: string, opacity: number) {
  const alpha = Math.max(0, Math.min(255, Math.round(opacity * 255))).toString(16).padStart(2, '0')
  return `${value}${alpha}`
}

function markerElement(feature: GisFeature, size: number, circleSize: number, onSelect: () => void): HTMLElement {
  const props = feature.properties
  const markerColor = color(props.iconColor, '#0288d1')
  const element = document.createElement('button')
  element.type = 'button'; element.className = 'gis-v3-marker'; element.style.width = `${size}px`; element.style.height = `${size}px`
  element.disabled = props.interactive === false
  element.setAttribute('aria-label', props.title || 'Объект карты')
  if (props.iconId && UUID.test(props.iconId)) {
    const image = document.createElement('img')
    const suffix = props.recolorIcon ? `?color=${markerColor.slice(1)}` : ''
    image.src = `/api/gis/assets/${props.iconId}${suffix}`; image.alt = ''; image.draggable = false
    element.append(image)
  } else if (props.markerShape === 'pin') {
    element.classList.add('gis-v3-marker-pin'); element.style.setProperty('--gis-marker-color', markerColor)
  } else {
    element.classList.add('gis-v3-marker-circle'); element.style.width = `${circleSize}px`; element.style.height = `${circleSize}px`; element.style.setProperty('--gis-marker-color', markerColor)
  }
  element.addEventListener('click', event => { event.stopPropagation(); if (props.interactive !== false) onSelect() })
  return element
}

function positionElement(): HTMLElement {
  const element = document.createElement('div'); element.className = 'gis-v3-position'; element.setAttribute('aria-label', 'Моё местоположение'); return element
}

export const YandexV3MapCanvas: ConfiguredGisMapCanvas = ({ config, data, position, view, onViewChange, onBoundsChange, onInteractionChange, onSelect, onLocate, locating, initialMapView }) => {
  const node = useRef<HTMLDivElement>(null); const root = useRef<HTMLDivElement>(null); const map = useRef<any>(null); const sdk = useRef<any>(null)
  const points = useRef(new Map<string, { version: string; entity: any }>()); const lines = useRef(new Map<string, { version: string; entity: any }>())
  const positionMarker = useRef<any>(null); const viewRef = useRef(view); const selectRef = useRef(onSelect)
  const renderController = useRef<AbortController | null>(null)
  const interacting = useRef(false); const pendingData = useRef<GisMapCanvasProps['data'] | undefined>(undefined)
  const [renderedData, setRenderedData] = useState(data); const [loading, setLoading] = useState(true); const [error, setError] = useState('')
  const [fullscreen, setFullscreen] = useState(false); const [attempt, setAttempt] = useState(0); const [mapVersion, setMapVersion] = useState(0)
  viewRef.current = view; selectRef.current = onSelect

  useEffect(() => {
    if (interacting.current) { pendingData.current = data; return }
    setRenderedData(data)
  }, [data])

  useEffect(() => {
    let live = true
    let renderFrame: number | null = null
    setLoading(true); setError('')
    if (!config.scriptUrl) { setError('Карта недоступна: не настроен ключ API Яндекс Карт'); setLoading(false); return () => { live = false } }
    loadSdk(config.scriptUrl).then(api => {
      if (!live || !node.current) return
      sdk.current = api
      const report = () => {
        const instance = map.current; if (!instance) return
        const center = instance.center; const bounds = instance.bounds
        onViewChange({ longitude: center[0], latitude: center[1], zoom: instance.zoom })
        onBoundsChange([bounds[0][0], bounds[0][1], bounds[1][0], bounds[1][1]])
      }
      const listener = new api.YMapListener({
        onUpdate: ({ mapInAction }: { mapInAction?: boolean }) => { if (!interacting.current && !mapInAction) report() },
        onActionStart: () => { renderController.current?.abort(); interacting.current = true; pendingData.current = undefined; onInteractionChange(true) },
        onActionEnd: () => {
          interacting.current = false
          if (pendingData.current !== undefined) { setRenderedData(pendingData.current); pendingData.current = undefined }
          onInteractionChange(false); report()
          renderFrame = window.requestAnimationFrame(() => { renderFrame = null; if (!interacting.current) setMapVersion(value => value + 1) })
        },
      })
      map.current = new api.YMap(node.current, {
        location: { center: [viewRef.current.longitude, viewRef.current.latitude], zoom: viewRef.current.zoom },
        mode: 'vector', zoomRounding: 'smooth', behaviors: ['drag', 'pinchZoom', 'scrollZoom', 'dblClick', 'oneFingerZoom'],
      }, [new api.YMapDefaultSchemeLayer({}), new api.YMapDefaultFeaturesLayer({ zIndex: 1800 }), listener])
      setMapVersion(value => value + 1); setLoading(false); window.setTimeout(report, 0)
    }).catch(cause => { if (live) { setError(cause instanceof Error ? cause.message : 'Не удалось загрузить карту'); setLoading(false) } })
    return () => {
      live = false; if (renderFrame !== null) window.cancelAnimationFrame(renderFrame); renderController.current?.abort(); onInteractionChange(false)
      if (map.current) map.current.destroy()
      map.current = null; sdk.current = null; points.current.clear(); lines.current.clear(); positionMarker.current = null
    }
    // The component lifecycle owns the v3 map; data and view are synchronized below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt, config.scriptUrl])

  useEffect(() => {
    const instance = map.current; if (!instance) return
    const center = instance.center
    if (Math.abs(center[0] - view.longitude) > .000001 || Math.abs(center[1] - view.latitude) > .000001 || Math.abs(instance.zoom - view.zoom) > .000001) {
      instance.update({ location: { center: [view.longitude, view.latitude], zoom: view.zoom, duration: 0 } })
    }
  }, [view.longitude, view.latitude, view.zoom])

  useEffect(() => {
    const instance = map.current; const api = sdk.current; if (!instance || !api) return
    renderController.current?.abort()
    const controller = new AbortController(); renderController.current = controller
    const sync = async () => {
      const nextPointIds = new Set<string>(); const nextLineIds = new Set<string>()
      const applied = await renderInBatches(renderedData?.features ?? [], feature => {
        if (controller.signal.aborted) return
      if (feature.geometry.type === 'Point') {
        nextPointIds.add(feature.id)
        const props = feature.properties; const fixedSize = view.zoom < (config.pointFixedSizeMaxZoom ?? 15); const scale = fixedSize ? 1 : props.iconScale || 1
        const size = fixedSize ? config.pointIconSize ?? POINT_ICON_SIZE : POINT_ICON_SIZE * scale
        const circleSize = fixedSize ? config.pointCircleSize ?? POINT_CIRCLE_SIZE : POINT_CIRCLE_SIZE * scale
        const version = JSON.stringify([feature, size, circleSize]); const current = points.current.get(feature.id)
        if (current?.version === version) return
        if (current) instance.removeChild(current.entity)
        const entity = new api.YMapMarker({ coordinates: feature.geometry.coordinates, id: feature.id, zIndex: 2000 }, markerElement(feature, size, circleSize, () => selectRef.current(feature)))
        instance.addChild(entity); points.current.set(feature.id, { version, entity })
      } else if (feature.geometry.type === 'LineString') {
        nextLineIds.add(feature.id)
        const props = feature.properties; const version = JSON.stringify(feature); const current = lines.current.get(feature.id)
        if (current?.version === version) return
        if (current) instance.removeChild(current.entity)
        const entity = new api.YMapFeature({ id: feature.id, geometry: feature.geometry, style: { stroke: [{ width: props.lineWidth || 5, color: withOpacity(color(props.lineColor, '#2563eb'), props.lineOpacity ?? 1) }] } })
        instance.addChild(entity); lines.current.set(feature.id, { version, entity })
      }
      }, controller.signal)
      if (!applied) return
      const stalePoints = [...points.current].filter(([id]) => !nextPointIds.has(id))
      const staleLines = [...lines.current].filter(([id]) => !nextLineIds.has(id))
      await renderInBatches(stalePoints, ([id, current]) => { instance.removeChild(current.entity); points.current.delete(id) }, controller.signal)
      await renderInBatches(staleLines, ([id, current]) => { instance.removeChild(current.entity); lines.current.delete(id) }, controller.signal)
    }
    void sync()
    return () => { controller.abort(); if (renderController.current === controller) renderController.current = null }
  }, [renderedData, mapVersion, view.zoom, config.pointIconSize, config.pointCircleSize, config.pointFixedSizeMaxZoom])

  useEffect(() => {
    const instance = map.current; const api = sdk.current; if (!instance || !api) return
    if (positionMarker.current) instance.removeChild(positionMarker.current)
    positionMarker.current = position ? new api.YMapMarker({ coordinates: [position.longitude, position.latitude], zIndex: 2100 }, positionElement()) : null
    if (positionMarker.current) instance.addChild(positionMarker.current)
  }, [position, mapVersion])

  useEffect(() => {
    const update = () => setFullscreen(document.fullscreenElement === root.current)
    document.addEventListener('fullscreenchange', update); return () => document.removeEventListener('fullscreenchange', update)
  }, [])

  const retry = () => { sdkPromise = null; setAttempt(value => value + 1) }
  const changeZoom = (delta: number) => map.current?.update({ location: { center: map.current.center, zoom: map.current.zoom + delta, duration: 0 } })
  const showLoadedObjects = () => {
    const coordinates = (renderedData?.features ?? []).flatMap(feature => feature.geometry.type === 'Point' ? [feature.geometry.coordinates as [number, number]] : feature.geometry.type === 'LineString' ? feature.geometry.coordinates as [number, number][] : (feature.geometry.coordinates as [number, number][][]).flat())
    if (coordinates.length) onViewChange(initialMapView(coordinates))
  }
  return <div ref={root} className="gis-map-canvas" role="application" aria-label="Карта сети">
    <div ref={node} className="gis-yandex-map" />
    {loading && <p className="gis-map-status">Загрузка карты…</p>}
    {error && <div className="gis-map-error"><p>{error}</p><button type="button" className="outline-button" onClick={retry}>Повторить загрузку</button></div>}
    {!error && <div className="gis-map-controls" onPointerDown={event => event.stopPropagation()}>
      <button type="button" onClick={() => changeZoom(1)} aria-label="Увеличить масштаб">+</button><button type="button" onClick={() => changeZoom(-1)} aria-label="Уменьшить масштаб">−</button><button type="button" onClick={onLocate} disabled={locating} aria-label="Показать моё местоположение">⌖</button><button type="button" onClick={showLoadedObjects} disabled={!renderedData?.features.length} aria-label="Показать загруженные объекты">⌂</button><button type="button" onClick={() => void (document.fullscreenElement ? document.exitFullscreen() : root.current?.requestFullscreen())} aria-label={fullscreen ? 'Свернуть карту' : 'Открыть карту на весь экран'}>{fullscreen ? '⊡' : '⛶'}</button>
    </div>}
  </div>
}
