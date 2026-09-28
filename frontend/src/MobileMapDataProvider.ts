import { request } from './http'
import type { GisFeature, GisFeatureCollection, GisFeatureDetails, GisLayer, GisMap, MapDataProvider } from './MapDataProvider'

export type MobileMapConfig = {
  map_provider: string
  yandex_maps_api_key: string
  point_detail_zoom: number
  point_icon_size: number
  point_circle_size: number
  point_fixed_size_max_zoom: number
  icon_fixed?: boolean
  line_render: 'vector' | 'raster'
}
export type MobileMapTiles = { lines_tile_version: string | null }
let cachedConfig: { value: MobileMapConfig; until: number } | null = null
export function resetMobileMapConfig() { cachedConfig = null }
export async function mobileMapConfig(signal?: AbortSignal): Promise<MobileMapConfig> {
  if (cachedConfig && cachedConfig.until > Date.now()) return cachedConfig.value
  const value = await request<MobileMapConfig>('/api/mobilemap/config', { signal })
  cachedConfig = { value, until: Date.now() + 30_000 }
  return value
}
export const mobileMapTiles = (mapId: string, signal?: AbortSignal) => request<MobileMapTiles>(`/api/mobilemap/maps/${encodeURIComponent(mapId)}/tiles/meta`, { signal })

const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i
export const mobileMapDataProvider: MapDataProvider = {
  async maps(signal) { return (await request<{ rows: GisMap[] }>('/api/mobilemap/maps', { signal })).rows },
  async layers(id, signal) { return (await request<{ rows: GisLayer[] }>(`/api/mobilemap/maps/${encodeURIComponent(id)}/layers`, { signal })).rows.map(layer => ({ ...layer, defaultVisible: true })) },
  async bounds(id, signal) {
    const b = await request<{ xmin: number; ymin: number; xmax: number; ymax: number }>(`/api/mobilemap/maps/${encodeURIComponent(id)}/bounds`, { signal })
    return [b.xmin, b.ymin, b.xmax, b.ymax]
  },
  async features({ mapId, bounds, zoom, layerIds }, signal) {
    const level = Math.max(0, Math.min(24, Math.floor(Number.isFinite(zoom) ? zoom : 15)))
    const query = new URLSearchParams({ bbox: bounds.join(','), zoom: String(level) })
    if (layerIds.length) query.set('layers', layerIds.join(','))
    const data = await request<{ features: (GisFeature & { properties: GisFeature['properties'] & { style?: Record<string, unknown> } })[]; truncated: boolean; thinned: boolean }>(`/api/mobilemap/maps/${encodeURIComponent(mapId)}/features?${query}`, { signal })
    return {
      type: 'FeatureCollection', truncated: data.truncated || data.thinned, limit: 0,
      features: data.features.map(feature => {
        const { style = {}, ...properties } = feature.properties
        const iconId = style.iconId
        return { ...feature, properties: { ...style, ...properties,
          iconUrl: typeof iconId === 'string' && UUID.test(iconId) ? `/api/mobilemap/assets/${iconId}` : undefined,
          // Every point returned by mobilemap can open its card at any zoom.
          interactive: feature.geometry.type === 'Point',
        } }
      }),
    } as GisFeatureCollection
  },
  feature: (id, signal) => request<GisFeatureDetails>(`/api/mobilemap/features/${encodeURIComponent(id)}`, { signal }),
}
