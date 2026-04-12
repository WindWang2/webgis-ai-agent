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
  const [activeFilters, setActiveFilters] = useState<Record<string, number[][]>>({})
  const mapRef = useRef<MapRef>(null)
  const lastAnalysisCenter = useRef<string>("")

  const currentMapStyle = useMemo(
    () => getMapStyle(MAP_STYLES[selectedBaseLayer]),
    [selectedBaseLayer]
  )

  const handleFilterChange = useCallback((layerId: string, ranges: number[][]) => {
    setActiveFilters(prev => ({
      ...prev,
      [layerId]: ranges
    }))
  }, [])

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

  // Dynamic layer rendering — 依赖 layers, mapReady, currentMapStyle, activeFilters
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map || !mapReady) return

    const renderLayers = () => {
      if (!map.isStyleLoaded()) {
        map.once('styledata', renderLayers)
        return
      }

      // Step 1: Remove layers/sources that are no longer in the props
      const currentLayers = layers.map(l => `custom-${l.id}`)
      const style = map.getStyle()
      if (style) {
        // Remove layers
        for (const layer of style.layers || []) {
          if (layer.id.startsWith("custom-")) {
            // Extract the layer ID by stripping the "custom-" prefix and then the "-suffix" part
            const withoutPrefix = layer.id.slice(7) // remove "custom-"
            const baseId = withoutPrefix.replace(/-[^-]*$/, '') // remove last "-suffix"
            if (!layers.find(l => l.id === baseId)) {
              try { map.removeLayer(layer.id) } catch (e) { console.warn("[MapPanel] failed to remove layer:", layer.id, e) }
            }
          }
        }
        // Remove sources
        for (const sourceId of Object.keys(style.sources || {})) {
          if (sourceId.startsWith("custom-")) {
            const baseId = sourceId.replace("custom-", "")
            if (!layers.find(l => l.id === baseId)) {
               try { map.removeSource(sourceId) } catch (e) { console.warn("[MapPanel] failed to remove source:", sourceId, e) }
            }
          }
        }
      }

      // Step 2: Add or Update layers
      // Process from bottom to top so that we can use moveLayer correctly
      // Actually, we'll process in order and handle z-index at the end
      for (const layer of layers) {
        if (!layer.visible || !layer.source) {
          // If exists but not visible, hide all associated sub-layers
          if (map.getSource(`custom-${layer.id}`)) {
            const s = map.getStyle()
            s?.layers?.forEach(l => {
              if (l.id.startsWith(`custom-${layer.id}`)) {
                map.setLayoutProperty(l.id, 'visibility', 'none')
              }
            })
          }
          continue
        }

        const sourceId = `custom-${layer.id}`
        const isNewSource = !map.getSource(sourceId)

        try {
          // --- SOURCE HANDLING ---
          if (isNewSource) {
            if (layer.type === "raster" || layer.type === "tile") {
              map.addSource(sourceId, { type: "raster", tiles: [layer.source as string], tileSize: 256 })
            } else if (layer.type === "heatmap" && (layer.source as any)?.image) {
              const src = layer.source as any
              const [west, south, east, north] = src.bbox
              map.addSource(sourceId, {
                type: "image",
                url: src.image,
                coordinates: [[west, north], [east, north], [east, south], [west, south]],
              } as any)
            } else {
              map.addSource(sourceId, { type: "geojson", data: layer.source as any })
            }
          } else {
            // Update existing source data if changed (only for GeoJSON)
            if (layer.type !== "raster" && layer.type !== "tile" && !(layer.type === "heatmap" && (layer.source as any)?.image)) {
              const src = map.getSource(sourceId) as any
              if (src.setData) src.setData(layer.source)
            }
          }

          // --- LAYER HANDLING ---
          const color = layer.style?.color || "#3b82f6"
          const thematicField = layer.source && typeof layer.source === 'object' ? layer.source.metadata?.field : null
          const filterRanges = activeFilters[layer.id]
          
          const getLayerFilter = (baseType: string) => {
            const base: any = ["==", "$type", baseType]
            if (thematicField && filterRanges) {
              const rangeFilters = filterRanges.map((range: number[]) => (
                ["all", [">=", ["get", thematicField], range[0]], ["<", ["get", thematicField], range[1]]]
              ))
              return ["all", base, ["any", ...rangeFilters]]
            }
            return base
          }

          const addOrUpdate = (subId: string, layerConfig: any) => {
            const fullId = `custom-${layer.id}-${subId}`
            if (!map.getLayer(fullId)) {
              map.addLayer({ ...layerConfig, id: fullId, source: sourceId })
            } else {
              // Update visibility
              map.setLayoutProperty(fullId, 'visibility', 'visible')
              // Update filter if applicable
              if (layerConfig.filter) map.setFilter(fullId, layerConfig.filter)
              // Update opacity/paint
              if (layerConfig.paint) {
                Object.keys(layerConfig.paint).forEach(key => {
                  map.setPaintProperty(fullId, key, layerConfig.paint[key])
                })
              }
            }
          }

          if (layer.type === "raster" || layer.type === "tile") {
            addOrUpdate("main", {
              type: "raster",
              paint: { "raster-opacity": layer.opacity || 1 },
            })
          } else if (layer.type === "heatmap" && (layer.source as any)?.image) {
            addOrUpdate("raster", {
              type: "raster",
              paint: { "raster-opacity": layer.opacity ?? 0.85, "raster-resampling": "linear" },
            })
          } else {
            const src = layer.source as any
            const features = src.features || []
            const hasPolygons = features.some((f: any) => f.geometry?.type?.includes("Polygon"))
            const hasLines = features.some((f: any) => f.geometry?.type?.includes("Line"))
            const hasPoints = features.some((f: any) => f.geometry?.type?.includes("Point"))
            const isHeatmapMode = layer.type === "heatmap" || layer.style?.renderType === "heatmap"

            if (hasPolygons) {
              if (isHeatmapMode) {
                addOrUpdate("heatgrid", {
                   type: "fill",
                   filter: getLayerFilter("Polygon"),
                   paint: {
                    // 高对比度格网色带：淡黄 -> 橙色 -> 深红 -> 紫黑
                    "fill-color": [
                      "interpolate",
                      ["linear"],
                      ["get", "weight"],
                      0.0, "rgba(255, 255, 178, 0)",
                      0.2, "rgba(254, 217, 118, 0.7)",
                      0.4, "rgba(253, 141, 60, 0.8)",
                      0.6, "rgba(240, 59, 32, 0.85)",
                      0.8, "rgba(189, 0, 38, 0.9)",
                      1.0, "rgba(71, 14, 0, 0.95)"
                    ],
                    "fill-outline-color": "rgba(255, 255, 255, 0.05)",
                    "fill-opacity": layer.opacity ?? 1,
                    "fill-antialias": true
                  }
                })
              } else {
                addOrUpdate("fill", {
                  type: "fill",
                  filter: getLayerFilter("Polygon"),
                  paint: { "fill-color": ["coalesce", ["get", "fill_color"], color], "fill-opacity": (layer.opacity || 1) * 0.3 }
                })
                addOrUpdate("outline", {
                  type: "line",
                  filter: getLayerFilter("Polygon"),
                  paint: { "line-color": ["coalesce", ["get", "stroke_color"], ["get", "fill_color"], color], "line-width": 2, "line-opacity": layer.opacity || 1 }
                })
              }
            }
            if (hasLines) {
              addOrUpdate("line", {
                type: "line",
                filter: getLayerFilter("LineString"),
                paint: { "line-color": ["coalesce", ["get", "fill_color"], color], "line-width": 3, "line-opacity": layer.opacity || 1 }
              })
            }
            if (hasPoints) {
              addOrUpdate("point", {
                type: "circle",
                filter: getLayerFilter("Point"),
                paint: {
                  "circle-radius": features.some((f: any) => f.properties?.weight) ? ["interpolate", ["linear"], ["get", "weight"], 0, 4, 1, 8] : 7,
                  "circle-color": ["coalesce", ["get", "fill_color"], color],
                  "circle-stroke-width": 2, "circle-stroke-color": "#fff", "circle-opacity": layer.opacity || 1
                }
              })
            }
          }
        } catch (e) {
          console.error("[MapPanel] incremental update error for:", layer.id, e)
        }
      }

      // Step 3: Global Z-Index Sync
      // We process layers from BACK to FRONT (index end to 0) and use moveLayer(id) to move to top
      // Wait, in React state, layers[0] is usually top.
      // So iterate from index length-1 down to 0. Move each to top.
      const reversedLayers = [...layers].reverse()
      for (const layer of reversedLayers) {
        const subLayers = style?.layers?.filter(sl => sl.id.startsWith(`custom-${layer.id}`)) || []
        subLayers.forEach(sl => {
          try { map.moveLayer(sl.id) } catch (e) { console.warn("[MapPanel] failed to move layer:", sl.id, e) }
        })
      }
    }

    renderLayers()
    return () => { map.off('styledata', renderLayers) }
  }, [layers, mapReady, currentMapStyle, activeFilters])

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
              if (!tl) return null;
              return (
                <ThematicLegend 
                  metadata={tl.source.metadata} 
                  onFilterChange={(ranges) => handleFilterChange(tl.id, ranges)}
                />
              );
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
