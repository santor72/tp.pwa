import { afterEach, expect, it, vi } from 'vitest'
import { mobileMapDataProvider, resetMobileMapConfig } from '../src/MobileMapDataProvider'

const icon = '12345678-1234-4234-8234-123456789012'
afterEach(() => { vi.unstubAllGlobals(); resetMobileMapConfig() })

it.each([0, 5, 13.9, 14, 15, 24])('keeps mobilemap points clickable at zoom %s without fetching a detail threshold', async zoom => {
  const urls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    urls.push(input)
    return new Response(JSON.stringify(input.endsWith('/config') ? { point_detail_zoom: 14 } : {
      truncated: false, thinned: true,
      features: [{ type: 'Feature', id: 'original', geometry: { type: 'Point', coordinates: [37,55] }, properties: { id: 'original', layer_id: 'layer', kind: 'Point', style: { iconId: icon, iconColor: '#123456', iconScale: 2 } } }],
    }), { headers: { 'Content-Type': 'application/json' } })
  }))
  const query = { mapId: 'map', bounds: [37,55,38,56] as [number,number,number,number], layerIds: ['layer'], zoom }
  const data = await mobileMapDataProvider.features(query)
  expect(data.truncated).toBe(true)
  expect(data.features[0].id).toBe('original')
  expect(data.features[0].properties).toMatchObject({ iconUrl: `/api/mobilemap/assets/${icon}`, iconColor: '#123456', iconScale: 2, interactive: true })
  const close = await mobileMapDataProvider.features({ ...query, zoom: 14 })
  expect(close.features[0].properties.interactive).toBe(true)
  expect(urls.filter(url => url.endsWith('/config'))).toHaveLength(0)
  expect(urls[0]).toContain('zoom=' + Math.floor(zoom))
})
