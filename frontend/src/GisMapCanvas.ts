import type { ComponentType } from 'react'

import type { GisBasemapConfig } from './api'
import type { GisBounds, GisFeature, GisFeatureCollection } from './MapDataProvider'
export type { GisBounds } from './MapDataProvider'

/** Provider-neutral input for a GIS map engine. Coordinates use GeoJSON order. */
export type GisMapView = { longitude: number; latitude: number; zoom: number }
export type GisPosition = { longitude: number; latitude: number; accuracy: number }

export type GisMapCanvasProps = {
  mapId?: string
  lineTiles?: { mapId: string; version: string; network: boolean; poles: boolean } | null
  lineVisibility?: { network: boolean; poles: boolean }
  onLineVisibilityChange?: (value: { network: boolean; poles: boolean }) => void
  onLineOptionsChange?: (available: boolean) => void
  data: GisFeatureCollection | null
  position: GisPosition | null
  view: GisMapView
  onViewChange: (view: GisMapView) => void
  onBoundsChange: (bounds: GisBounds) => void
  onInteractionChange: (active: boolean) => void
  onSelect: (feature: GisFeature) => void
  onLocate: () => void
  locating: boolean
  initialMapView: (coordinates: [longitude: number, latitude: number][]) => GisMapView
}

/** A map-engine implementation that can be selected by the application. */
export type GisMapCanvas = ComponentType<GisMapCanvasProps>
export type ConfiguredGisMapCanvasProps = GisMapCanvasProps & { config: GisBasemapConfig }
export type ConfiguredGisMapCanvas = ComponentType<ConfiguredGisMapCanvasProps>
