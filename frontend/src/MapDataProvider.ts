/** Shared map data model. Coordinates use longitude/latitude (GeoJSON order).
 * IDs are opaque strings; renderers must not infer a backend or URL from them.
 */
export type GisBounds = [west: number, south: number, east: number, north: number]
export type GisMap = { id: string; name: string; created_at: string; report: { total: number } }
export type GisLayer = { id: string; name: string; position: number; count: number; version: number; defaultVisible?: boolean }
export type GisFeatureStyle = {
  iconUrl?: string; iconColor?: string; iconScale?: number; markerShape?: 'pin' | 'circle'
  lineColor?: string; lineWidth?: number; lineOpacity?: number
  fillColor?: string; fillOpacity?: number
}
export type GisFeature = { type: 'Feature'; id: string; geometry: { type: 'Point' | 'LineString' | 'Polygon'; coordinates: unknown }; properties: { id: string; layer_id: string; title?: string; number?: number; kind: string; interactive?: boolean } & GisFeatureStyle }
export type GisFeatureCollection = { type: 'FeatureCollection'; truncated: boolean; limit: number; features: GisFeature[] }
export type GisFeatureDetails = { id: string; layer_id: string; map_id: string; layer_name: string; title: string; number: number; kind: string; description: string; geometry: GisFeature['geometry']; style: Record<string, unknown>; version: number }

export type MapFeatureQuery = {
  mapId: string
  bounds: GisBounds
  layerIds: string[]
  zoom: number
}

/** Read operations only. Reject on failure; forward AbortSignal to the transport.
 * Empty layerIds means all layers. A collection is a viewport snapshot, not a delta.
 * Preserve truncated/limit so consumers can detect incomplete results.
 */
export interface MapDataProvider {
  maps(signal?: AbortSignal): Promise<GisMap[]>
  layers(mapId: string, signal?: AbortSignal): Promise<GisLayer[]>
  bounds(mapId: string, signal?: AbortSignal): Promise<GisBounds>
  features(query: MapFeatureQuery, signal?: AbortSignal): Promise<GisFeatureCollection>
  feature(featureId: string, signal?: AbortSignal): Promise<GisFeatureDetails>
}
