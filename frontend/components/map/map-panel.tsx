"use client"
import { useState, useRef, useCallback, useEffect, useMemo } from "react"
import Map, { NavigationControl, MapRef, ViewStateChangeEvent } from "react-map-gl/maplibre"
import maplibregl from "maplibre-gl"
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
  { name: "OSM 地图", url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", type: "raster" },
  { name: "OSM 地形", url: "https://tile.opentopomap.org/{z}/{x}/{y}.png", type: "raster" },
  { name: "ESRI 影像", url: "https://server.arcgisonline.com/ArcGIS/Rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", type: "raster" },
  { name: "ESRI 地形", url: "https://server.arcgisonline.com/ArcGIS/Rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}", type: "raster" },
  { name: "Carto 浅色", url: "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png", type: "raster" },
  { name: "Carto 深色", url: "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png", type: "raster" },
]

function getMapStyle(option: MapStyleOption): maplibregl.StyleSpecification {
  if (option.type === "raster") {
    return {
      version: 8,
      sources: {
        "raster-tiles": {
          type: "raster",
          tiles: [option.url],
          tileSize: 256,
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        },
      },
      layers: [
        {
          id: "raster-tiles-layer",
          type: "raster",
          source: "raster-tiles",
          minzoom: 0,
          maxzoom: 22,
        },
      ],
    }
  }
  return option.url
}

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
  const [mapReady, setMapReady] = useState(false)
  const mapRef = useRef<MapRef>(null)
  const lastAnalysisCenter = useRef<string>("")

  const currentMapStyle = useMemo(
    () => getMapStyle(MAP_STYLES[selectedBaseLayer]),
    [selectedBaseLayer]
  )

  // Apply analysis results to map
  useEffect(() => {
    if (analysisResult?.center) {
      const centerKey = `${analysisResult.center[0]},${analysisResult.center[1]},${analysisResult.zoom}`
      if (centerKey !== lastAnalysisCenter.current) {
        lastAnalysisCenter.current = centerKey
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
    }
  }, [analysisResult])

  // Dynamic layer rendering — 依赖 layers, mapReady, currentMapStyle
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map || !mapReady) return

    const renderLayers = () => {
      if (!map.isStyleLoaded()) {
        map.once('styledata', renderLayers)
        return
      }

      // Step 1: Remove old custom layers and sources
      try {
        const style = map.getStyle()
        if (style) {
          for (const layer of style.layers || []) {
            if (layer.id.startsWith("custom-")) {
              try { map.removeLayer(layer.id) } catch (e) { console.warn("[MapPanel] removeLayer failed:", layer.id, e) }
            }
          }
          for (const sourceId of Object.keys(style.sources || {})) {
            if (sourceId.startsWith("custom-")) {
              try { map.removeSource(sourceId) } catch (e) { console.warn("[MapPanel] removeSource failed:", sourceId, e) }
            }
          }
        }
      } catch (e) {
        console.error("[MapPanel] cleanup error:", e)
      }

      // Step 2: Add new layers
      for (const layer of layers) {
        if (!layer.visible || !layer.source) continue

        const sourceId = `custom-${layer.id}`

        // Safety: force remove if somehow still exists
        try {
          if (map.getSource(sourceId)) {
            console.warn("[MapPanel] source still exists after cleanup, force removing:", sourceId)
            // Remove layers using this source first
            const s = map.getStyle()
            for (const l of s?.layers || []) {
              if (l.source === sourceId) {
                try { map.removeLayer(l.id) } catch {}
              }
            }
            map.removeSource(sourceId)
          }
        } catch (e) {
          console.warn("[MapPanel] force remove failed:", e)
        }

        try {
          if (layer.type === "raster" || layer.type === "tile") {
            map.addSource(sourceId, { type: "raster", tiles: [layer.source], tileSize: 256 })
            map.addLayer({
              id: `custom-${layer.id}-${layer.type}`,
              type: "raster",
              source: sourceId,
              paint: { "raster-opacity": layer.opacity || 1 },
            })
          } else if (layer.type === "vector" || layer.type === "heatmap") {
            const src = layer.source
            if (typeof src !== "object" || src.type !== "FeatureCollection") {
              console.warn("[MapPanel] skipping non-FeatureCollection source for:", layer.id, "sourceType:", typeof src, src?.type)
              continue
            }

            map.addSource(sourceId, { type: "geojson", data: src })

            const features = src.feature || src.features || []
            const color = layer.style?.color || "#3b82f6"

            const hasPolygons = features.some((f: any) => f.geometry?.type === "Polygon" || f.geometry?.type === "MultiPolygon")
            const hasLines = features.some((f: any) => ["LineString", "MultiLineString"].includes(f.geometry?.type))
            const hasPoints = features.some((f: any) => f.geometry?.type === "Point")
            const hasWeight = features.some((f: any) => f.properties?.weight !== undefined)

            // 热力图模式：强制开启热力图渲染，不依赖点数阈值
            const isHeatmapMode = layer.type === "heatmap" || layer.style?.renderType === "heatmap"

            if (hasPolygons) {
              map.addLayer({
                id: `custom-${layer.id}-fill`, type: "fill", source: sourceId,
                paint: { "fill-color": color, "fill-opacity": (layer.opacity || 1) * 0.3 },
                filter: ["any", ["in", "$type", "Polygon"]],
              })
              map.addLayer({
                id: `custom-${layer.id}-outline`, type: "line", source: sourceId,
                paint: { "line-color": color, "line-width": 2, "line-opacity": layer.opacity || 1 },
                filter: ["any", ["in", "$type", "Polygon"]],
              })
            }
            if (hasLines) {
              map.addLayer({
                id: `custom-${layer.id}-line`, type: "line", source: sourceId,
                paint: { "line-color": color, "line-width": 3, "line-opacity": layer.opacity || 1 },
                filter: ["any", ["in", "$type", "LineString"]],
              })
            }
            if (hasPoints || isHeatmapMode) {
              // Add heatmap layer for points (when enough features for meaningful density)
              // 热力图模式：强制渲染不管点数多少
              const pointCount = features.filter((f: any) => f.geometry?.type === "Point").length
              if (isHeatmapMode || pointCount >= 5) {
                try {
                  // 热力图模式：使用更大的半径和强度确保可见性
                  const heatmapRadius = isHeatmapMode
                    ? ["interpolate", ["linear"], ["zoom"],
                        6, 15,
                        9, 25,
                        12, 40,
                        15, 50,
                      ]
                    : ["interpolate", ["linear"], ["zoom"],
                        6, 10,
                        9, 20,
                        12, 30,
                        15, 40,
                      ]
                  map.addLayer({
                    id: `custom-${layer.id}-heatmap`, type: "heatmap", source: sourceId,
                    filter: ["any", ["in", "$type", "Point"]],
                    paint: {
                      "heatmap-weight": hasWeight ? ["get", "weight"] : 1,
                      "heatmap-intensity": isHeatmapMode ? 2 : (hasWeight ? 1.5 : 1),
                      "heatmap-color": [
                        "interpolate", ["linear"], ["heatmap-density"],
                        0, "rgba(0, 0, 255, 0)",
                        0.2, "rgba(0, 200, 255, 0.6)",
                        0.4, "rgba(0, 255, 100, 0.7)",
                        0.6, "rgba(255, 255, 0, 0.8)",
                        0.8, "rgba(255, 128, 0, 0.9)",
                        1, "rgba(255, 0, 0, 1)",
                      ],
                      "heatmap-radius": heatmapRadius,
                      "heatmap-opacity": isHeatmapMode ? 0.7 : (hasWeight ? 0.8 : 0.6),
                    },
                  })
                } catch (e) {
                  console.warn("[MapPanel] heatmap not supported:", e)
                }
              }

              // Circle layer - 在热力图模式时隐藏点，只显示热力图
              if (!isHeatmapMode) {
                const circleRadius = hasWeight
                  ? ["interpolate", ["linear"], ["get", "weight"], 0, 4, 0.5, 6, 1, 8]
                  : 7
                map.addLayer({
                  id: `custom-${layer.id}-point`, type: "circle", source: sourceId,
                  filter: ["any", ["in", "$type", "Point"]],
                  paint: {
                    "circle-radius": circleRadius,
                    "circle-color": color, "circle-stroke-width": 2,
                    "circle-stroke-color": "#fff", "circle-opacity": layer.opacity || 1,
                  },
                })

                // Label layer
                map.addLayer({
                  id: `custom-${layer.id}-label`, type: "symbol", source: sourceId,
                  filter: ["any", ["in", "$type", "Point"]],
                  layout: {
                    "text-field": ["get", "name"], "text-size": 11,
                    "text-offset": [0, 1.2], "text-anchor": "top", "text-optional": true,
                  },
                  paint: { "text-color": "#fff", "text-halo-color": "#000", "text-halo-width": 1.5 },
                })
              }
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
          console.log("[MapPanel] layer added successfully:", layer.id, "features:", layer.type === "vector" && typeof layer.source === "object" ? (layer.source as any).features?.length : "N/A")
        } catch (e) {
          console.error("[MapPanel] ERROR adding layer:", layer.id, e)
          // Try to clean up partial state
          try {
            const s = map.getStyle()
            for (const l of s?.layers || []) {
              if (l.source === sourceId) { try { map.removeLayer(l.id) } catch {} }
            }
            if (map.getSource(sourceId)) { try { map.removeSource(sourceId) } catch {} }
          } catch {}
        }
      }
    }

    renderLayers()
    return () => { map.off('styledata', renderLayers) }
  }, [layers, mapReady, currentMapStyle])

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
      <div className="flex-1 relative min-h-0">
        <Map
          ref={mapRef}
          {...viewState}
          onMove={handleMove}
          onMouseMove={handleMouseMove}
          onLoad={() => setMapReady(true)}
          style={{ position: "absolute", inset: 0 }}
          mapStyle={currentMapStyle}
          attributionControl={false}
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
