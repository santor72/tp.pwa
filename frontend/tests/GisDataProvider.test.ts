import { afterEach, describe, expect, it, vi } from 'vitest'
import { gisDataProvider } from '../src/GisDataProvider'

afterEach(() => vi.unstubAllGlobals())

function respond(value: unknown) {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(value)))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('GIS data contract', () => {
  it('returns arrays and longitude/latitude bounds instead of transport envelopes', async () => {
    respond({ rows: [{ id: 'map', name: 'Network' }] })
    expect(await gisDataProvider.maps()).toEqual([{ id: 'map', name: 'Network' }])
    respond({ rows: [{ id: 'layer' }] })
    expect(await gisDataProvider.layers('map')).toEqual([{ id: 'layer' }])
    respond({ xmin: 37, ymin: 55, xmax: 38, ymax: 56 })
    expect(await gisDataProvider.bounds('map')).toEqual([37, 55, 38, 56])
  })

  it('preserves viewport metadata, normalizes icons and passes cancellation to fetch', async () => {
    const iconId = '12345678-1234-1234-1234-123456789abc'
    const collection = { type: 'FeatureCollection', truncated: true, limit: 100, features: [
      { type: 'Feature', id: 'one', geometry: { type: 'Point', coordinates: [37, 55] }, properties: { id: 'one', layer_id: 'layer', kind: 'node', iconId, recolorIcon: true, iconColor: '#112233' } },
    ] }
    const fetch = respond(collection)
    const controller = new AbortController()
    const result = await gisDataProvider.features({ mapId: 'map', bounds: [37, 55, 38, 56], layerIds: ['a', 'b'], zoom: 14.8 }, controller.signal)
    expect(fetch).toHaveBeenCalledWith('/api/gis/maps/map/features?bbox=37,55,38,56&zoom=14&layers=a%2Cb', expect.objectContaining({ signal: controller.signal, credentials: 'include' }))
    expect(result).toMatchObject({ truncated: true, limit: 100 })
    expect(result.features[0].properties.iconUrl).toBe(`/api/gis/assets/${iconId}?color=112233`)
    expect(result.features[0].properties).not.toHaveProperty('iconId')
    expect(result.features[0].geometry).toEqual(collection.features[0].geometry)
  })

  it('does not build asset URLs from invalid GIS icon IDs', async () => {
    respond({ type: 'FeatureCollection', truncated: false, limit: 100, features: [
      { properties: { iconId: '../bad' } },
    ] })
    const result = await gisDataProvider.features({ mapId: 'map', bounds: [0, 0, 1, 1], layerIds: [], zoom: 15 })
    expect(result.features[0].properties.iconUrl).toBeUndefined()
  })

  it('preserves session expiry and backend errors', async () => {
    const expired = vi.fn()
    window.addEventListener('auth-expired', expired)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'EXPIRED', message: 'Session expired' }), { status: 401 })))
    try {
      await expect(gisDataProvider.maps()).rejects.toMatchObject({ status: 401, code: 'EXPIRED' })
      expect(expired).toHaveBeenCalledOnce()
    } finally {
      window.removeEventListener('auth-expired', expired)
    }
  })

  it('propagates aborted requests without replacing AbortError', async () => {
    const error = new DOMException('Aborted', 'AbortError')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(error))
    await expect(gisDataProvider.feature('object')).rejects.toBe(error)
  })
})
