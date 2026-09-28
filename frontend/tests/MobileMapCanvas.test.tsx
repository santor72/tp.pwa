import { act, cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { YandexMapCanvas } from '../src/YandexMapCanvas'
import type { ConfiguredGisMapCanvasProps } from '../src/GisMapCanvas'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('uses mobilemap feature interactivity directly without a second PWA zoom gate', async () => {
  let zoom = 13
  let click: ((event: any) => void) | undefined
  const added: any[] = []
  class FakeMap {
    geoObjects = { add: vi.fn() }; container = { fitToViewport: vi.fn() }; events = { add: vi.fn() }
    getCenter() { return [55, 37] } getBounds() { return [[54, 36], [56, 38]] } getZoom() { return zoom }
    setCenter() {} destroy() {}
  }
  class Collection { add() {} removeAll() {} }
  class ObjectManager {
    objects = { events: { add: (_name: string, handler: (event: any) => void) => { click = handler } } }
    add(feature: any) { added.push(feature) } remove() {}
  }
  vi.stubGlobal('ymaps', { ready: (fn: () => void) => fn(), Map: FakeMap, ObjectManager, GeoObjectCollection: Collection })
  const onSelect = vi.fn()
  const feature = { type: 'Feature' as const, id: 'point-1', geometry: { type: 'Point' as const, coordinates: [37, 55] as [number, number] }, properties: { interactive: false } }
  const props: ConfiguredGisMapCanvasProps = {
    config: { provider: 'yandex', scriptUrl: 'sdk', mobilemap: true },
    data: { type: 'FeatureCollection', features: [feature] }, position: null,
    view: { longitude: 37, latitude: 55, zoom: 13 }, onViewChange: vi.fn(), onBoundsChange: vi.fn(),
    onInteractionChange: vi.fn(), onSelect, onLocate: vi.fn(), locating: false,
    initialMapView: () => ({ longitude: 37, latitude: 55, zoom: 14 }), lineTiles: null,
  }
  const { rerender } = render(<YandexMapCanvas {...props} />)
  await waitFor(() => expect(added).toHaveLength(1))
  expect(added[0].options.interactivityModel).toBe('default#transparent')
  act(() => click?.({ get: () => 'point-1' }))
  expect(onSelect).not.toHaveBeenCalled()
  const interactiveFeature = { ...feature, properties: { interactive: true } }
  rerender(<YandexMapCanvas {...props} data={{ ...props.data!, features: [interactiveFeature] }} />)
  await waitFor(() => expect(added).toHaveLength(2))
  act(() => click?.({ get: () => 'point-1' }))
  expect(onSelect).toHaveBeenCalledWith(interactiveFeature)
})

it('uses Mercator Retina tiles, keeps layers while moving, replaces versions and removes hidden layers', async () => {
  const add = vi.fn(), remove = vi.fn()
  const events: Record<string, () => void> = {}
  const projection = {}
  class FakeMap {
    layers = { add, remove }; geoObjects = { add: vi.fn() }; container = { fitToViewport: vi.fn() }
    events = { add: (name: string, handler: () => void) => { events[name] = handler } }
    getCenter() { return [55,37] } getBounds() { return [[54,36],[56,38]] } getZoom() { return 14 }
    setCenter() {} destroy() {}
  }
  class Collection { add() {} removeAll() {} }
  class ObjectManager { objects = { events: { add() {} } }; add() {} remove() {} }
  class Layer { constructor(public url: string, public options: unknown) {} }
  vi.stubGlobal('devicePixelRatio', 2)
  vi.stubGlobal('ymaps', { ready: (fn: () => void) => fn(), Map: FakeMap, ObjectManager, GeoObjectCollection: Collection, Layer, projection: { sphericalMercator: projection } })
  const props: ConfiguredGisMapCanvasProps = {
    config: { provider: 'yandex', scriptUrl: 'sdk', mobilemap: true },
    data: null, position: null, view: { longitude: 37, latitude: 55, zoom: 14 },
    onViewChange: vi.fn(), onBoundsChange: vi.fn(), onInteractionChange: vi.fn(), onSelect: vi.fn(), onLocate: vi.fn(), locating: false,
    initialMapView: () => ({ longitude: 37, latitude: 55, zoom: 14 }),
    lineTiles: { mapId: 'map', version: 'v1', network: true, poles: false },
  }
  const { rerender } = render(<YandexMapCanvas {...props} />)
  await waitFor(() => expect(add).toHaveBeenCalledTimes(1))
  expect(add.mock.calls[0][0].url).toBe('/api/mobilemap/maps/map/tiles/lines/network/v1/%z/%x/%y@2x.webp')
  expect(add.mock.calls[0][0].options.projection).toBe(projection)
  rerender(<YandexMapCanvas {...props} view={{ ...props.view, longitude: 37.1 }} />)
  expect(add).toHaveBeenCalledTimes(1)
  rerender(<YandexMapCanvas {...props} lineTiles={{ ...props.lineTiles!, version: 'v2', poles: true }} />)
  await waitFor(() => expect(add).toHaveBeenCalledTimes(3))
  expect(remove).toHaveBeenCalledTimes(1)
  expect(add.mock.calls[2][0].url).toContain('/poles/v2/')
  rerender(<YandexMapCanvas {...props} lineTiles={null} />)
  await waitFor(() => expect(remove).toHaveBeenCalledTimes(3))
})
