"use client"
import { useState, useRef, useCallback, useEffect, useMemo } from "react"
import Map, { NavigationControl, MapRef, ViewStateChangeEvent } from "react-map-gl/maplibre"
import maplibregl from "maplibre-gl"
import { Layers, ZoomIn, ZoomOut, Maximize, MapPin, Eye, EyeOff, RotateCcw, Target, Trash2, ChevronDown, Plus, Edit, Settings, Download } from "lucide-react"
import { LayerCard } from "@/components/layer-card"
import type { Layer } from "@/lib/types/layer"
import { MapActionHandler } from "./map-action-handler"
import { ThematicLegend } from "./thematic-legend"

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
        console.log("[MapPanel] Processing layer:", layer.id, "type:", layer.type, "visible:", layer.visible, "style:", layer.style)
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
          } else if (layer.type === "heatmap" && (layer.source as any)?.image) {
            // 栅格热力图：image source
            const src = layer.source as any
            const [west, south, east, north] = src.bbox
            map.addSource(sourceId, {
              type: "image",
              url: src.image,
              coordinates: [
                [west, north],  // top-left
                [east, north],  // top-right
                [east, south],  // bottom-right
                [west, south],  // bottom-left
              ],
            } as any)
            map.addLayer({
              id: `custom-${layer.id}-raster`,
              type: "raster",
              source: sourceId,
              paint: {
                "raster-opacity": layer.opacity ?? 0.85,
                "raster-resampling": "linear",
              },
            })

            // Auto-fit
            const bounds = new maplibregl.LngLatBounds([west, south], [east, north])
            map.fitBounds(bounds, { padding: 50, maxZoom: 14 })
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
            const isHeatmapMode = layer.type === "heatmap" || layer.style?.renderType === "heatmap"

            if (hasPolygons) {
              if (isHeatmapMode && hasWeight) {
                // 栅格热力图：用 choropleth fill 渲染网格单元，颜色按 weight 插值
                map.addLayer({
                  id: `custom-${layer.id}-heatgrid`, type: "fill", source: sourceId,
                  filter: ["==", "$type", "Polygon"],
                  paint: {
                    "fill-color": [
                      "interpolate", ["linear"], ["get", "weight"],
                      0,    "rgba(0,0,0,0)",
                      0.05, "rgba(0,32,160,0.75)",
                      0.2,  "rgba(0,120,255,0.82)",
                      0.4,  "rgba(0,220,180,0.87)",
                      0.6,  "rgba(255,220,0,0.92)",
                      0.8,  "rgba(255,90,0,0.96)",
                      1.0,  "rgba(200,0,0,1.0)",
                    ],
                    "fill-opacity": layer.opacity || 1,
                    "fill-antialias": false,
                  },
                })
              } else {
                map.addLayer({
                  id: `custom-${layer.id}-fill`, type: "fill", source: sourceId,
                  paint: { 
                    "fill-color": ["coalesce", ["get", "fill_color"], color], 
                    "fill-opacity": (layer.opacity || 1) * 0.3 
                  },
                  filter: ["==", "$type", "Polygon"],
                })
                map.addLayer({
                  id: `custom-${layer.id}-outline`, type: "line", source: sourceId,
                  paint: { 
                    "line-color": ["coalesce", ["get", "stroke_color"], ["get", "fill_color"], color], 
                    "line-width": ["coalesce", ["get", "stroke_width"], 2], 
                    "line-opacity": layer.opacity || 1 
                  },
                  filter: ["==", "$type", "Polygon"],
                })
              }
            }
            if (hasLines) {
              map.addLayer({
                id: `custom-${layer.id}-line`, type: "line", source: sourceId,
                paint: { 
                  "line-color": ["coalesce", ["get", "fill_color"], color], 
                  "line-width": 3, 
                  "line-opacity": layer.opacity || 1 
                },
                filter: ["==", "$type", "LineString"],
              })
            }
            if (hasPoints && !isHeatmapMode) {
              const circleRadius = hasWeight
                ? ["interpolate", ["linear"], ["get", "weight"], 0, 4, 0.5, 6, 1, 8]
                : 7
              map.addLayer({
                id: `custom-${layer.id}-point`, type: "circle", source: sourceId,
                filter: ["==", "$type", "Point"],
                paint: {
                  "circle-radius": circleRadius,
                  "circle-color": ["coalesce", ["get", "fill_color"], color], 
                  "circle-stroke-width": ["coalesce", ["get", "stroke_width"], 2],
                  "circle-stroke-color": "#fff", "circle-opacity": layer.opacity || 1,
                },
              })
              map.addLayer({
                id: `custom-${layer.id}-label`, type: "symbol", source: sourceId,
                filter: ["==", "$type", "Point"],
                layout: {
                  "text-field": ["get", "name"], "text-size": 11,
                  "text-offset": [0, 1.2], "text-anchor": "top", "text-optional": true,
                },
                paint: { "text-color": "#fff", "text-halo-color": "#000", "text-halo-width": 1.5 },
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

  const handleExportPng = () => {
    const map = mapRef.current?.getMap()
    if (!map) return
    map.once('render', () => {
      const dataUrl = map.getCanvas().toDataURL('image/png')
      const link = document.createElement('a')
      link.download = `map-${Date.now()}.png`
      link.href = dataUrl
      link.click()
    })
    map.triggerRepaint()
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header - 大地坐标风格 */}
      <div className="flex items-center justify-between border-b border-border p-4 bg-card/50">
        <div className="flex items-center gap-3">
          <div className="relative">
            <Layers className="h-5 w-5 text-accent" />
            <div className="absolute -inset-0.5 bg-accent/20 rounded-full blur-sm" />
          </div>
          <h1 className="font-semibold tracking-wide">大地坐标</h1>
          {layers.length > 0 && (
            <span className="text-xs text-muted-foreground ml-2 px-2 py-0.5 bg-muted/50 rounded">📍 {layers.length} 层</span>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowLayerPanel(!showLayerPanel)}
            className={`flex h-9 w-9 items-center justify-center rounded-lg border transition-all ${
              showLayerPanel ? "bg-primary text-primary-foreground border-primary" : "border-border hover:bg-card hover:border-primary/50"
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
          preserveDrawingBuffer={true}
        >
          <MapActionHandler mapInstance={mapRef.current} />
        </Map>

        {/* Legend - Floating Bottom Left */}
        {layers.find(l => l.visible && l.source?.metadata?.thematic_type === 'choropleth') && (
          <div className="absolute bottom-16 left-4 z-10 transition-all duration-500">
            {(() => {
              const tl = layers.find(l => l.visible && l.source?.metadata?.thematic_type === 'choropleth');
              return tl ? <ThematicLegend metadata={tl.source.metadata} /> : null;
            })()}
          </div>
        )}

        {/* Floating Controls - Left Side - 罗盘工具风格 */}
        <div className="absolute top-4 left-4 flex flex-col gap-2">
          <button onClick={handleZoomIn} className="flex h-10 w-10 items-center justify-center rounded-lg bg-card/90 backdrop-blur shadow-lg border border-border hover:bg-card hover:border-primary/50 hover:scale-105 transition-all" title="放大">
            <ZoomIn className="h-4 w-4 text-foreground" />
          </button>
          <button onClick={handleZoomOut} className="flex h-10 w-10 items-center justify-center rounded-lg bg-card/90 backdrop-blur shadow-lg border border-border hover:bg-card hover:border-primary/50 hover:scale-105 transition-all" title="缩小">
            <ZoomOut className="h-4 w-4 text-foreground" />
          </button>
          <button onClick={handleReset} className="flex h-10 w-10 items-center justify-center rounded-lg bg-card/90 backdrop-blur shadow-lg border border-border hover:bg-card hover:border-primary/50 hover:scale-105 transition-all" title="复位">
            <RotateCcw className="h-4 w-4 text-foreground" />
          </button>
          <button onClick={handleLocate} className="flex h-10 w-10 items-center justify-center rounded-lg bg-card/90 backdrop-blur shadow-lg border border-border hover:bg-card hover:border-primary/50 hover:scale-105 transition-all" title="定位">
            <Target className="h-4 w-4 text-foreground" />
          </button>
          <button onClick={handleExportPng} className="flex h-10 w-10 items-center justify-center rounded-lg bg-card/90 backdrop-blur shadow-lg border border-border hover:bg-card hover:border-primary/50 hover:scale-105 transition-all" title="导出PNG">
            <Download className="h-4 w-4 text-foreground" />
          </button>
        </div>

        {/* Floating Control - Right Side (Base Layer Selector) - 地图选择器风格 */}
        <div className="absolute top-4 right-4 z-10">
          <div className="relative">
            <button
              onClick={() => setShowLayerSelector(!showLayerSelector)}
              className="flex h-10 items-center gap-2 rounded-lg bg-card/90 backdrop-blur shadow-lg border border-border px-4 hover:bg-card hover:border-primary/50 transition-all"
            >
              <span className="text-sm font-medium">{MAP_STYLES[selectedBaseLayer].name}</span>
              <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform ${showLayerSelector ? "rotate-180" : ""}`} />
            </button>

            {/* Dropdown */}
            {showLayerSelector && (
              <div className="absolute top-12 right-0 bg-card rounded-lg shadow-xl border border-border py-1 min-w-44 z-20">
                {MAP_STYLES.map((style, index) => (
                  <button
                    key={style.name}
                    onClick={() => handleBaseLayerSelect(index)}
                    className={`w-full text-left px-4 py-2.5 text-sm hover:bg-card hover:text-primary transition-all ${
                      index === selectedBaseLayer ? "text-primary bg-primary/10 font-medium border-l-2 border-primary" : "text-foreground"
                    }`}
                  >
                    {style.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Layer Management Panel - 航海日志风格 */}
        {showLayerPanel && (
          <div className="absolute top-16 right-4 bg-card rounded-lg shadow-xl border border-border p-4 w-80 z-10 max-h-[70vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4 pb-3 border-b border-border">
              <h3 className="font-semibold text-sm tracking-wide">📍 图层概览</h3>
              <button className="flex h-7 w-7 items-center justify-center rounded hover:bg-card hover:border-primary/50 border border-transparent transition-all text-muted-foreground hover:text-primary">
                <Plus className="h-3.5 w-3.5" />
              </button>
            </div>

            {layers.length === 0 ? (
              <p className="text-xs text-muted-foreground text-center py-8 italic">
                暂无记录<br /><span className="text-primary/60">通过AI对话开启探索之旅</span>
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

      {/* Footer - 坐标面板风格 */}
      <div className="border-t border-border p-2.5 text-xs bg-card/80 backdrop-blur">
        <div className="flex justify-between items-center text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="text-primary/60">◈</span> MapLibre GL JS
            <span className="text-border mx-1">|</span>
            {MAP_STYLES[selectedBaseLayer].name}
          </span>
          <span className="font-mono bg-muted/30 px-2 py-0.5 rounded text-foreground">
            {coordinates.lng.toFixed(4)}°E, {coordinates.lat.toFixed(4)}°N
          </span>
          <span className="flex items-center gap-1">
            <span className="text-accent">⬡</span> Z {viewState.zoom.toFixed(1)}
          </span>
        </div>
      </div>
    </div>
  )
}
