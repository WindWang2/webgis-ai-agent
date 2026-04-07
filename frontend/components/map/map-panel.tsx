"use client"
import { useState, useRef, useCallback, useEffect } from "react"
import Map, { NavigationControl, MapRef, ViewStateChangeEvent } from "react-map-gl/maplibre"
import maplibregl from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"
import { Layers, ZoomIn, ZoomOut, Maximize, MapPin, Eye, EyeOff, RotateCcw, Target, Trash2, ChevronDown, Plus, Edit, Settings } from "lucide-react"
import { LayerCard } from "@/components/layer-card"
import type { Layer } from "@/lib/types/layer"

interface MapPanelProps {
  layers: Layer[]
  onRemoveLayer: (id: string) => void
  onToggleLayer: (id: string) => void
  onEditLayer: (layer: Layer) => void
  analysisResult?: any
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

export function MapPanel({ layers, onRemoveLayer, onToggleLayer, onEditLayer, analysisResult }: MapPanelProps) {
  const [showLayerPanel, setShowLayerPanel] = useState(false)
  const [showLayerSelector, setShowLayerSelector] = useState(false)
  const [selectedBaseLayer, setSelectedBaseLayer] = useState(0)
  const [coordinates, setCoordinates] = useState({ lng: 0, lat: 0 })
  const [viewState, setViewState] = useState(DEFAULT_VIEW_STATE)
  const mapRef = useRef<MapRef>(null)

  // Apply analysis results to map
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

  // Dynamic layer rendering
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map || !map.isStyleLoaded()) return

    // Remove old layers and sources
    const style = map.getStyle()
    if (style) {
      for (const layer of style.layers || []) {
        if (layer.id.startsWith("custom-")) {
          map.removeLayer(layer.id)
        }
      }
      for (const sourceId of Object.keys(style.sources || {})) {
        if (sourceId.startsWith("custom-")) {
          map.removeSource(sourceId)
        }
      }
    }

    // Add new layers
    for (const layer of layers) {
      if (!layer.visible || !layer.source) continue

      const sourceId = `custom-${layer.id}`
      if (map.getSource(sourceId)) continue

      if (layer.type === "raster") {
        // Raster layer
        map.addSource(sourceId, {
          type: "raster",
          tiles: [layer.source],
          tileSize: 256,
        })
        map.addLayer({
          id: `custom-${layer.id}-raster`,
          type: "raster",
          source: sourceId,
          paint: {
            "raster-opacity": layer.opacity || 1,
          },
        })
      } else if (layer.type === "vector") {
        // Vector layer (GeoJSON)
        if (typeof layer.source === "object" && layer.source.type === "FeatureCollection") {
          map.addSource(sourceId, {
            type: "geojson",
            data: layer.source,
          })

          // Determine geometry types
          const features = layer.source.features || []
          const hasPolygons = features.some((f: any) => f.geometry?.type === "Polygon" || f.geometry?.type === "MultiPolygon")
          const hasLines = features.some((f: any) => ["LineString", "MultiLineString"].includes(f.geometry?.type))
          const hasPoints = features.some((f: any) => f.geometry?.type === "Point")

          const color = (layer.style as any)?.color || "#3b82f6"

          if (hasPolygons) {
            map.addLayer({
              id: `custom-${layer.id}-fill`,
              type: "fill",
              source: sourceId,
              paint: {
                "fill-color": color,
                "fill-opacity": (layer.opacity || 1) * 0.3,
              },
              filter: ["any", ["in", "$type", "Polygon"]],
            })
            map.addLayer({
              id: `custom-${layer.id}-outline`,
              type: "line",
              source: sourceId,
              paint: {
                "line-color": color,
                "line-width": 2,
                "line-opacity": layer.opacity || 1,
              },
              filter: ["any", ["in", "$type", "Polygon"]],
            })
          }
          if (hasLines) {
            map.addLayer({
              id: `custom-${layer.id}-line`,
              type: "line",
              source: sourceId,
              paint: {
                "line-color": color,
                "line-width": 3,
                "line-opacity": layer.opacity || 1,
              },
              filter: ["any", ["in", "$type", "LineString"]],
            })
          }
          if (hasPoints) {
            map.addLayer({
              id: `custom-${layer.id}-point`,
              type: "circle",
              source: sourceId,
              paint: {
                "circle-radius": 6,
                "circle-color": color,
                "circle-stroke-width": 2,
                "circle-stroke-color": "#fff",
                "circle-opacity": layer.opacity || 1,
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
            if (!bounds.isEmpty()) {
              map.fitBounds(bounds, { padding: 50, maxZoom: 15 })
            }
          }
        }
      } else if (layer.type === "tile") {
        // Tile layer
        map.addSource(sourceId, {
          type: "raster",
          tiles: [layer.source],
          tileSize: 256,
        })
        map.addLayer({
          id: `custom-${layer.id}-tile`,
          type: "raster",
          source: sourceId,
          paint: {
            "raster-opacity": layer.opacity || 1,
          },
        })
      }
    }
  }, [layers])

  const handleMove = useCallback((evt: ViewStateChangeEvent) => {
    setViewState(evt.viewState)
  }, [])

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

  const handleBaseLayerSelect = (index: number) => {
    setSelectedBaseLayer(index)
    setShowLayerSelector(false)
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
          mapStyle={MAP_STYLES[selectedBaseLayer].url}
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

        {/* Floating Control - Right Side (Base Layer Selector) */}
        <div className="absolute top-4 right-4 z-10">
          <div className="relative">
            <button
              onClick={() => setShowLayerSelector(!showLayerSelector)}
              className="flex h-9 items-center gap-1.5 rounded-lg bg-background shadow-md border border-border px-3 hover:bg-muted transition-colors"
            >
              <span className="text-sm">{MAP_STYLES[selectedBaseLayer].name}</span>
              <ChevronDown className={`h-4 w-4 transition-transform ${showLayerSelector ? "rotate-180" : ""}`} />
            </button>

            {/* Dropdown */}
            {showLayerSelector && (
              <div className="absolute top-10 right-0 bg-background rounded-lg shadow-lg border border-border py-1 min-w-36 z-20">
                {MAP_STYLES.map((style, index) => (
                  <button
                    key={style.name}
                    onClick={() => handleBaseLayerSelect(index)}
                    className={`w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors ${
                      index === selectedBaseLayer ? "text-primary font-medium" : ""
                    }`}
                  >
                    {style.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Layer Management Panel */}
        {showLayerPanel && (
          <div className="absolute top-16 right-4 bg-background rounded-lg shadow-lg border border-border p-4 w-72 z-10 max-h-[70vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-sm">图层管理</h3>
              <button className="flex h-6 w-6 items-center justify-center rounded hover:bg-muted text-muted-foreground">
                <Plus className="h-3.5 w-3.5" />
              </button>
            </div>
            
            {layers.length === 0 ? (
              <p className="text-xs text-muted-foreground text-center py-6">
                暂无图层<br />通过AI对话添加或上传图层数据
              </p>
            ) : (
              <div className="space-y-3">
                {layers.map(layer => (
                  <LayerCard
                    key={layer.id}
                    layer={layer}
                    onToggle={onToggleLayer}
                    onDelete={onRemoveLayer}
                    onEdit={onEditLayer}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Footer - Coordinates Display */}
      <div className="border-t border-border p-2 text-xs text-muted-foreground bg-muted/30">
        <div className="flex justify-between items-center">
          <span>MapLibre GL JS · {MAP_STYLES[selectedBaseLayer].name}</span>
          <span className="font-mono">
            经度: {coordinates.lng}°, 纬度: {coordinates.lat}°
          </span>
          <span>缩放: {viewState.zoom.toFixed(1)}</span>
        </div>
      </div>
    </div>
  )
}
