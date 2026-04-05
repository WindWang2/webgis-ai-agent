"use client"
import { useState, useRef, useCallback, useEffect } from "react"
import Map, { NavigationControl, MapRef, ViewStateChangeEvent } from "react-map-gl/maplibre"
import maplibregl from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"
import { Layers, ZoomIn, ZoomOut, Maximize, MapPin, Eye, EyeOff, RotateCcw, Target, Trash2 } from "lucide-react"
import type { GeoJsonLayer } from "@/app/page"

interface MapPanelProps {
  layers: GeoJsonLayer[]
  onRemoveLayer: (id: string) => void
  onToggleLayer: (id: string) => void
  analysisResult?: any  // 保留旧接口兼容
}

// Free tile servers for MapLibre
const FREE_TILE_SERVERS = [
  "https://demotiles.maplibre.org/style.json",
  "https://tiles.openstreetmap.org/{z}/{x}/{y}.png",
]

const DEFAULT_VIEW_STATE = {
  longitude: 116.4074,
  latitude: 39.9042,
  zoom: 4,
}

export function MapPanel({ layers, onRemoveLayer, onToggleLayer, analysisResult }: MapPanelProps) {
  const [showLayerPanel, setShowLayerPanel] = useState(false)
  const [coordinates, setCoordinates] = useState({ lng: 0, lat: 0 })
  const [viewState, setViewState] = useState(DEFAULT_VIEW_STATE)
  const mapRef = useRef<MapRef>(null)

  // Apply analysis results to map (legacy)
  useEffect(() => {
    if (analysisResult?.center) {
      setViewState(prev => ({
        ...prev,
        longitude: analysisResult.center[0],
        latitude: analysisResult.center[1],
        zoom: analysisResult.zoom || prev.zoom,
      }))
      mapRef.current?.flyTo({
        center: analysisResult.center,
        zoom: analysisResult.zoom || 10,
        duration: 1500,
      })
    }
  }, [analysisResult])

  // Dynamic GeoJSON layer rendering
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map || !map.isStyleLoaded()) return

    // Remove old geojson layers and sources
    const style = map.getStyle()
    if (style) {
      for (const layer of style.layers || []) {
        if (layer.id.startsWith("geojson-")) {
          map.removeLayer(layer.id)
        }
      }
      for (const sourceId of Object.keys(style.sources || {})) {
        if (sourceId.startsWith("geojson-")) {
          map.removeSource(sourceId)
        }
      }
    }

    // Add new layers
    for (const layer of layers) {
      if (!layer.visible) continue

      const sourceId = `geojson-${layer.id}`
      if (map.getSource(sourceId)) continue

      map.addSource(sourceId, {
        type: "geojson",
        data: layer.geojson,
      })

      const features = layer.geojson.features || []
      const hasPolygons = features.some((f: any) => f.geometry?.type === "Polygon" || f.geometry?.type === "MultiPolygon")
      const hasLines = features.some((f: any) => ["LineString", "MultiLineString"].includes(f.geometry?.type))
      const hasPoints = features.some((f: any) => f.geometry?.type === "Point")

      const color = layer.color || "#3b82f6"

      if (hasPolygons) {
        map.addLayer({
          id: `geojson-${layer.id}-fill`,
          type: "fill",
          source: sourceId,
          paint: {
            "fill-color": color,
            "fill-opacity": 0.3,
          },
          filter: ["any", ["in", "$type", "Polygon"]],
        })
        map.addLayer({
          id: `geojson-${layer.id}-outline`,
          type: "line",
          source: sourceId,
          paint: {
            "line-color": color,
            "line-width": 2,
          },
          filter: ["any", ["in", "$type", "Polygon"]],
        })
      }
      if (hasLines) {
        map.addLayer({
          id: `geojson-${layer.id}-line`,
          type: "line",
          source: sourceId,
          paint: {
            "line-color": color,
            "line-width": 3,
          },
          filter: ["any", ["in", "$type", "LineString"]],
        })
      }
      if (hasPoints) {
        map.addLayer({
          id: `geojson-${layer.id}-point`,
          type: "circle",
          source: sourceId,
          paint: {
            "circle-radius": 6,
            "circle-color": color,
            "circle-stroke-width": 2,
            "circle-stroke-color": "#fff",
          },
          filter: ["any", ["in", "$type", "Point"]],
        })
      }

      // Auto-fit bounds
      if (features.length > 0) {
        const bounds = new maplibregl.LngLatBounds()
        features.forEach((f: any) => {
          if (!f.geometry) return
          if (f.geometry.type === "Point") {
            bounds.extend(f.geometry.coordinates as [number, number])
          } else if (f.geometry.coordinates) {
            const coords = f.geometry.type === "Polygon"
              ? f.geometry.coordinates[0]
              : Array.isArray(f.geometry.coordinates[0]?.[0])
                ? f.geometry.coordinates.flat()
                : f.geometry.coordinates
            ;(Array.isArray(coords[0]) ? coords : [coords]).forEach((c: number[]) => {
              bounds.extend(c as [number, number])
            })
          }
        })
        if (bounds.isEmpty()) return
        map.fitBounds(bounds, { padding: 50, maxZoom: 15 })
      }
    }
  }, [layers])

  const handleMove = useCallback((evt: ViewStateChangeEvent) => {
    setViewState(evt.viewState)
  }, [])

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleMouseMove = useCallback((e: any) => {
    setCoordinates({
      lng: Number(e.lngLat.lng.toFixed(4)),
      lat: Number(e.lngLat.lat.toFixed(4)),
    })
  }, [])

  const handleZoomIn = () => mapRef.current?.zoomIn({ duration: 300 })
  const handleZoomOut = () => mapRef.current?.zoomOut({ duration: 300 })
  const handleReset = () => {
    mapRef.current?.flyTo({
      center: [DEFAULT_VIEW_STATE.longitude, DEFAULT_VIEW_STATE.latitude],
      zoom: DEFAULT_VIEW_STATE.zoom,
      duration: 1000,
    })
  }
  const handleLocate = () => {
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          mapRef.current?.flyTo({
            center: [pos.coords.longitude, pos.coords.latitude],
            zoom: 14,
            duration: 1000,
          })
        },
        (err) => console.warn("Location denied:", err),
        { enableHighAccuracy: true }
      )
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border p-4">
        <div className="flex items-center gap-2">
          <Layers className="h-5 w-5 text-primary" />
          <h1 className="font-semibold">地图</h1>
          {layers.length > 0 && (
            <span className="text-xs text-muted-foreground">({layers.length} 图层)</span>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowLayerPanel(!showLayerPanel)}
            className={`flex h-8 w-8 items-center justify-center rounded-lg border transition-colors ${
              showLayerPanel ? "bg-primary text-primary-foreground border-primary" : "border-border hover:bg-muted"
            }`}
            title="图层管理"
          >
            <Layers className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Map Container */}
      <div className="flex-1 relative">
        <Map
          ref={mapRef}
          {...viewState}
          onMove={handleMove}
          onMouseMove={handleMouseMove}
          style={{ width: "100%", height: "100%" }}
          mapStyle={FREE_TILE_SERVERS[0]}
          attributionControl={false}
          reuseMaps
        >
          <NavigationControl position="bottom-right" />
        </Map>

        {/* Floating Controls - Left Side */}
        <div className="absolute top-4 left-4 flex flex-col gap-2">
          <button onClick={handleZoomIn} className="flex h-9 w-9 items-center justify-center rounded-lg bg-background shadow-md border border-border hover:bg-muted transition-colors" title="放大">
            <ZoomIn className="h-4 w-4" />
          </button>
          <button onClick={handleZoomOut} className="flex h-9 w-9 items-center justify-center rounded-lg bg-background shadow-md border border-border hover:bg-muted transition-colors" title="缩小">
            <ZoomOut className="h-4 w-4" />
          </button>
          <button onClick={handleReset} className="flex h-9 w-9 items-center justify-center rounded-lg bg-background shadow-md border border-border hover:bg-muted transition-colors" title="复位">
            <RotateCcw className="h-4 w-4" />
          </button>
          <button onClick={handleLocate} className="flex h-9 w-9 items-center justify-center rounded-lg bg-background shadow-md border border-border hover:bg-muted transition-colors" title="定位">
            <Target className="h-4 w-4" />
          </button>
        </div>

        {/* Layer Control Panel */}
        {showLayerPanel && (
          <div className="absolute top-16 left-4 bg-background rounded-lg shadow-lg border border-border p-3 w-60 z-10">
            <h3 className="font-semibold mb-3 text-sm">图层控制</h3>
            {layers.length === 0 ? (
              <p className="text-xs text-muted-foreground">暂无图层，通过对话添加</p>
            ) : (
              <div className="space-y-2">
                {layers.map(layer => (
                  <div key={layer.id} className="flex items-center gap-2 text-sm">
                    <button onClick={() => onToggleLayer(layer.id)} className="hover:bg-muted rounded p-0.5">
                      {layer.visible ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4 text-muted-foreground" />}
                    </button>
                    <span className="w-3 h-3 rounded-full" style={{ backgroundColor: layer.color }} />
                    <span className="flex-1 truncate">{layer.name}</span>
                    <button onClick={() => onRemoveLayer(layer.id)} className="hover:text-destructive">
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Footer - Coordinates Display */}
      <div className="border-t border-border p-2 text-xs text-muted-foreground bg-muted/30">
        <div className="flex justify-between items-center">
          <span>MapLibre GL JS • OSM Tile</span>
          <span className="font-mono">
            经度: {coordinates.lng}°, 纬度: {coordinates.lat}°
          </span>
          <span>缩放: {viewState.zoom.toFixed(1)}</span>
        </div>
      </div>
    </div>
  )
}
