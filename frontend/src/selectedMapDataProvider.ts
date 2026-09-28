import type { MapDataProvider } from './MapDataProvider'
import { gisDataProvider } from './GisDataProvider'
import { mobileMapDataProvider } from './MobileMapDataProvider'
import { request } from './http'

let cachedSource: { value: 'gis' | 'mobilemap'; until: number } | null = null
export function resetMapSource() { cachedSource = null }
export async function mapSource(signal?: AbortSignal): Promise<'gis' | 'mobilemap'> {
  if (cachedSource && cachedSource.until > Date.now()) return cachedSource.value
  const value = (await request<{ source: 'gis' | 'mobilemap' }>('/api/mobilemap/source', { signal })).source
  cachedSource = { value, until: Date.now() + 30_000 }
  return value
}
async function provider(signal?: AbortSignal) {
  return await mapSource(signal) === 'mobilemap' ? mobileMapDataProvider : gisDataProvider
}
export const mapDataProvider: MapDataProvider = {
  maps: async signal => (await provider(signal)).maps(signal),
  layers: async (id, signal) => (await provider(signal)).layers(id, signal),
  bounds: async (id, signal) => (await provider(signal)).bounds(id, signal),
  features: async (query, signal) => (await provider(signal)).features(query, signal),
  feature: async (id, signal) => (await provider(signal)).feature(id, signal),
}
