"use client"

import { useState, useRef, useEffect } from "react"
import Map, { Layer, Source, NavigationControl } from "react-map-gl"
import "maplibre-gl/dist/maplibre-gl.css"
import { Layers, ZoomIn, ZoomOut, Maximize } from "lucide-react"

const MAPBOX_TOKEN = "" // Use with MapLibre free tiles or your own tile server

export function MapPanel() {
  const [showLayers, setShowLayers] = useState(false)
  const mapRef = useRef<any>(null)

  const mapStyle = "https://demotiles.maplibre.org/style.json"

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
            onClick={() => setShowLayers(!showLayers)}
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
          
          {/* Example layer - can be replaced with dynamic layers */}
          <Source id="data" type="geojson" data={{
            type: "FeatureCollection",
            features: []
          }}>
            <Layer
              id="points"
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
        {showLayers && (
          <div className="absolute top-16 left-4 bg-background rounded-lg shadow-lg border border-border p-3 w-48">
            <h3 className="font-semibold mb-2 text-sm">图层列表</h3>
            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" defaultChecked className="rounded" />
                底图图层
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
