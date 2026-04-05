"use client"
import { useState, useRef, useCallback, useEffect } from "react"
import Map, { NavigationControl, MapRef, ViewStateChangeEvent } from "react-map-gl/maplibre"
import maplibregl from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"
import { Layers, ZoomIn, ZoomOut, Maximize, MapPin, Eye, EyeOff, RotateCcw, Target, Trash2, ChevronDown } from "lucide-react"
import type { GeoJsonLayer } from "@/app/page"

interface MapPanelProps {
  layer: GeoJsonLayer[]
  onRemoveLayer: (id: string) => void
  onToggleLayer: (id: string) => void
  analysisResult?: any  // 保留旧接口兼容
}

// Map layer types
type LayerType = "raster" | "style"

interface MapStyleOption {
  name: string
  url: string
  type: LayerType
}

// Map base layer options
const MAP_STYLES: MapStyleOption[] = [
  { name: "ESRI Topo", url: "https://server.arcgisonline.com/ArcGIS/Rest/services/World_Topo_Map/MapServer/Tile/{z}/{y}/{x}", type: "raster" },
  { name: "ESRI Imagery", url: "https://basemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", type: "raster" },
  { name: "Carto Voyager", url: "https://basemaps.cartocdn.com/GL/VOYAGER-GL-STYLE/style.json", type: "style" },
  { name: "Carto Dark", url: "https://basemaps.cartocdn.com/GL/DARK_MATTER-GL-STYLE/style.json", type: "style" },
  { name: "OSM Bright", url: "https://basemaps.cartocdn.com/GL/OSM_BRIGHT-GL-STYLE/style.json", type: "style" },
]

const DEFAULT_VIEW_STATE = {
  longitude: 116.4074,
  latitude: 39.9042,
  zoom: 4,
}

export function MapPanel({ layer, onRemoveLayer, onToggleLayer, analysisResult }: MapPanelProps) {
  const [showLayerPanel, setShowLayerPanel] = useState(false)
  const [showLayerSelector, setShowLayerSelector] = useState(false)
  const [selectedLayer, setSelectedLayer] = useState(0)
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

    // Remove old geojson layers and source
    const style = map.getStyle()
    if (style) {
      for (const layer of style.layer || []) {
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

    // Add new layer
    for (const layer of layer) {
      if (!layer.visible) continue

      const sourceId = `geojson-${layer.id}`
      if (map.getSource(sourceId)) continue

      map.addSource(sourceId, {
        type: "geojson",
        data: layer.geojson,
      })

      const features = layer.geojson.feature || []
      const hasPolygons = features.some((f: any) => f.geometry?.type === "Polygon" || f.geometry?.type === "MultiPolygon")
      const hasLines = feature.some((f: any) => ["LineString", "MultiLineString"].includes(f.geometry?.type))
      const hasPoints = feature.some((f: any) => f.geometry?.type === "Point")

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
  }, [layer])

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

  const handleLayerSelect = (index: number) => {
    setSelectedLayer(index)
    setShowLayerSelector(false)
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border p-4">
        <div className="flex items-center gap-2">
          <Layers className="h-5 w-5 text-primary" />
          <h1 className="font-semibold">地图</h1>
          {layer.length > 0 && (
            <span className="text-xs text-muted-foreground">({layer.length} 图层)</span>
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
          mapStyle={MAP_STYLES[selectedLayer].url}
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

        {/* Floating Control - Right Side (Layer Selector) */}
        <div className="absolute top-4 right-4 z-10">
          <div className="relative">
            <button
              onClick={() => setShowLayerSelector(!showLayerSelector)}
              className="flex h-9 items-center gap-1.5 rounded-lg bg-background shadow-md border border-border px-3 hover:bg-muted transition-colors"
            >
              <span className="text-sm">{MAP_STYLES[selectedLayer].name}</span>
              <ChevronDown className={`h-4 w-4 transition-transform ${showLayerSelector ? "rotate-180" : ""}`} />
            </button>

            {/* Dropdown */}
            {showLayerSelector && (
              <div className="absolute top-10 right-0 bg-background rounded-lg shadow-lg border border-border py-1 min-w-36 z-20">
                {MAP_STYLES.map((style, index) => (
                  <button
                    key={style.name}
                    onClick={() => handleLayerSelect(index)}
                    className={`w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors ${
                      index === selectedLayer ? "text-primary font-medium" : ""
                    }`}
                  >
                    {style.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Layer Control Panel */}
        {showLayerPanel && (
          <div className="absolute top-16 left-4 bg-background rounded-lg shadow-lg border border-border p-3 w-60 z-10">
            <h3 className="font-semibold mb-3 text-sm">图层控制</h3>
            {layer.length === 0 ? (
              <p className="text-xs text-muted-foreground">暂无图层，通过对话添加</p>
            ) : (
              <div className="space-y-2">
                {layer.map(layer => (
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
          <span>MapLibre GL JS · {MAP_STYLES[selectedLayer].name}</span>
          <span className="font-mono">
            经度: {coordinates.lng}°, 纬度: {coordinates.lat}°
          </span>
          <span>缩放: {viewState.zoom.toFixed(1)}</span>
        </div>
      </div>
    </div>
  )
}