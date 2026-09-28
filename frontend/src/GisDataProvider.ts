import { request } from './http'
import type { GisFeature, GisFeatureCollection, GisFeatureDetails, GisLayer, GisMap, MapDataProvider } from './MapDataProvider'

/** The GIS endpoint uses integer zoom thresholds while vector maps expose fractional zoom. */
export function gisRequestZoom(zoom: number): number {
  return Math.max(1, Math.min(23, Math.floor(Number.isFinite(zoom) ? zoom : 15)))
}

type GisWireCollection = Omit<GisFeatureCollection, 'features'> & {
  features: (Omit<GisFeature, 'properties'> & { properties: GisFeature['properties'] & { iconId?: string | null; recolorIcon?: boolean } })[]
}

const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i

/** Current GIS adapter. Authentication stays in our backend, never in the browser. */
export const gisDataProvider: MapDataProvider = {
  async maps(signal) {
    return (await request<{ rows: GisMap[] }>('/api/gis/maps', { signal })).rows
  },
  async layers(mapId, signal) {
    return (await request<{ rows: GisLayer[] }>(`/api/gis/maps/${encodeURIComponent(mapId)}/layers`, { signal })).rows
  },
  async bounds(mapId, signal) {
    const value = await request<{ xmin: number; ymin: number; xmax: number; ymax: number }>(`/api/gis/maps/${encodeURIComponent(mapId)}/bounds`, { signal })
    return [value.xmin, value.ymin, value.xmax, value.ymax]
  },
  async features({ mapId, bounds, layerIds, zoom }, signal) {
    const value = await request<GisWireCollection>(`/api/gis/maps/${encodeURIComponent(mapId)}/features?bbox=${bounds.join(',')}&zoom=${gisRequestZoom(zoom)}${layerIds.length ? `&layers=${encodeURIComponent(layerIds.join(','))}` : ''}`, { signal })
    return { ...value, features: value.features.map(feature => {
      const { iconId, recolorIcon, ...properties } = feature.properties
      const color = /^#[a-f\d]{6}$/i.test(properties.iconColor ?? '') ? properties.iconColor! : '#0288d1'
      const iconUrl = iconId && UUID.test(iconId)
        ? `/api/gis/assets/${iconId}${recolorIcon ? `?color=${color.slice(1)}` : ''}`
        : undefined
      return { ...feature, properties: { ...properties, iconUrl } }
    }) }
  },
  feature: (id, signal) => request<GisFeatureDetails>(`/api/gis/features/${encodeURIComponent(id)}`, { signal }),
}
