"use client"
import { useState, useRef, useEffect } from "react"
import Map, { Layer, Source, NavigationControl } from "react-map-gl/maplibre"
import "maplibre-gl/dist/maplibre-gl.css"
import { Layers, ZoomIn, ZoomOut, Maximize, Flame } from "lucide-react"

const MAPBOX__TOKEN = "" // Use with MapLibre free tiles or your own tile server

export interface HeatmapDataPoint {
  lat: number
  lng: number
  weight?: number
}

export interface HeatmapConfig {
  intensity: number // 0-1
  radius: number // 像素
  colorStops: [number, string][] // [weight, color] pairs
}

export function MapPanel({
  heatmapData = [],
  heatmapConfig,
  showHeatmap = true,
}: {
  heatmapData?: HeatmapDataPoint[]
  heatmapConfig?: HeatmapConfig
  showHeatmap?: boolean
}) {
  const [showLayer, setShowLayer] = useState(false)
  const mapRef = useRef<any>(null)

  const mapStyle = "https://demotiles.maplibre.org/style.json"

  // Default heatmap config
  const defaultConfig: HeatmapConfig = heatmapConfig || {
    intensity: 1,
    radius: 30,
    colorStops: [
      [0, "rgba(0,0,255,0)"],
      [0.2, "rgba(0,0,255,0.3)"],
      [0.4, "rgba(0,255,255,0.5)"],
      [0.6, "rgba(0,255,0,0.7)"],
      [0.8, "rgba(255,255,0,0.8)"],
      [1, "rgba(255,0,0,0.9)"],
    ],
  }

  // Convert heatmap data to GeoJSON with aggregation
  const heatmapGeoJSON = heatmapData.length > 0 ? {
    type: "FeatureCollection" as const,
    features: heatmapData.map((point) => ({
      type: "Feature" as const,
      properties: {
        weight: point.weight || 1,
      },
      geometry: {
        type: "Point" as const,
        coordinates: [point.lng, point.lat],
      },
    })),
  } : null

  // Build heatmap paint properties dynamically
  const buildHeatmapPaint = (config: HeatmapConfig) => {
    const paint: any = {
      "heatmap-weight": ["get", "weight"],
      "heatmap-intensity": config.intensity,
      "heatmap-radius": config.radius,
      "heatmap-opacity": 0.7,
    }

    // Build color gradient
    const stops = config.colorStops
    const expressions: (string | number)[] = []
    for (let i = 0; i < stops.length; i++) {
      expressions.push(stops[i][0], stops[i][1])
    }
    paint["heatmap-color"] = ["interpolate", ["linear"], ["heatmap-density"], ...expressions]

    return paint
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border p-4">
        <div className="flex items-center gap-2">
          <Layers className="h-5 w-5 text-primary" />
          <h1 className="font-semibold">地图</h1>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowLayer(!showLayer)}
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-border hover:bg-muted transition-colors"
            title="图层管理"
          >
            <Layers className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Map */}
      <div className="flex-1 relative">
        <Map
          ref={mapRef}
          initialViewState={{
            longitude: 116.4074,
            latitude: 39.9042,
            zoom: 4,
          }}
          style={{ width: "100%", height: "100%" }}
          mapStyle={mapStyle}
          attributionControl={false}
        >
          <NavigationControl position="bottom-right" />
          
          {/* Heatmap Layer for data aggregation visualization */}
          {showHeatmap && heatmapGeoJSON && (
            <Source id="heatmap-source" type="geojson" data={heatmapGeoJSON}>
              <Layer
                id="heatmap-layer"
                type="heatmap"
                paint={buildHeatmapPaint(defaultConfig)}
              />
            </Source>
          )}

          {/* Default point layer */}
          <Source id="data" type="geojson" data={{
            type: "FeatureCollection",
            features: []
          }}>
            <Layer
              id="point"
              type="circle"
              paint={{
                "circle-radius": 8,
                "circle-color": "#3b82f6",
                "circle-stroke-width": 2,
                "circle-stroke-color": "#ffffff",
              }}
            />
          </Source>
        </Map>

        {/* Zoom controls */}
        <div className="absolute bottom-4 left-4 flex flex-col gap-2">
          <button
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-background shadow border border-border hover:bg-muted transition-colors"
            title="放大"
          >
            <ZoomIn className="h-4 w-4" />
          </button>
          <button
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-background shadow border border-border hover:bg-muted transition-colors"
            title="缩小"
          >
            <ZoomOut className="h-4 w-4" />
          </button>
          <button
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-background shadow border border-border hover:bg-muted transition-colors"
            title="全屏"
          >
            <Maximize className="h-4 w-4" />
          </button>
        </div>

        {/* Layer panel (conditional) */}
        {showLayer && (
          <div className="absolute top-16 left-4 bg-background rounded-lg shadow-lg border border-border p-3 w-48">
            <h3 className="font-semibold mb-2 text-sm">图层列表</h3>
            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" defaultChecked className="rounded" />
                底图图层
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" defaultChecked={showHeatmap} className="rounded" />
                热力图
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="rounded" />
                分析结果
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="rounded" />
                标记点
              </label>
            </div>
          </div>
        )}
      </div>

      {/* Footer - Map info */}
      <div className="border-t border-border p-2 text-xs text-muted-foreground">
        <div className="flex justify-between">
          <span>MapLibre GL JS</span>
          <span id="coordinates">经度：0.00, 纬度：0.00</span>
        </div>
      </div>
    </div>
  )
}