import type { ComponentType } from 'react'

import type { GisFeature, GisFeatureCollection } from './api'

/** Provider-neutral input for a GIS map engine. Coordinates use GeoJSON order. */
export type GisMapView = { longitude: number; latitude: number; zoom: number }
export type GisPosition = { longitude: number; latitude: number; accuracy: number }
export type GisBounds = [west: number, south: number, east: number, north: number]

export type GisMapCanvasProps = {
  data: GisFeatureCollection | null
  position: GisPosition | null
  view: GisMapView
  onViewChange: (view: GisMapView) => void
  onBoundsChange: (bounds: GisBounds) => void
  onSelect: (feature: GisFeature) => void
  onLocate: () => void
  locating: boolean
  initialMapView: (coordinates: [longitude: number, latitude: number][]) => GisMapView
}

/** A map-engine implementation that can be selected by the application. */
export type GisMapCanvas = ComponentType<GisMapCanvasProps>
