"use client"
import { useState, useRef, useCallback, useEffect, useMemo } from "react"
import { MAP_STYLES, MapStyleOption } from "@/lib/constants"
import Map, { MapRef, ViewStateChangeEvent } from "react-map-gl/maplibre"
import maplibregl from "maplibre-gl"
import {
  ZoomIn,
  ZoomOut,
  RotateCcw,
  Target,
  Download,
  ChevronDown,
  Layers,
  Compass,
  Box,
} from "lucide-react"
import type { Layer } from "@/lib/types/layer"
import type { AnalysisResult, GeoJSONFeatureCollection, HeatmapRasterSource } from "@/lib/types"
import { MapActionHandler } from "./map-action-handler"
import { ThematicLegend } from "./thematic-legend"
import { useHudStore } from "@/lib/store/useHudStore"

interface MapPanelProps {
  layers: Layer[]
  onRemoveLayer: (id: string) => void
  onToggleLayer: (id: string) => void
  onEditLayer: (layer: Layer) => void
  analysisResult?: AnalysisResult | null
}

import { useMapAction } from "@/lib/contexts/map-action-context"

function getMapStyle(option: MapStyleOption, index: number): maplibregl.StyleSpecification {
  if (option.type === "raster") {
    const sourceId = `raster-tiles-${index}`;
    const layerId = `raster-tiles-layer-${index}`;
    return {
      version: 8,
      sources: {
        [sourceId]: {
          type: "raster",
          tiles: [option.url],
          tileSize: 256,
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        },
      },
      layers: [
        {
          id: layerId,
          type: "raster",
          source: sourceId,
          minzoom: 0,
          maxzoom: 22,
        },
      ],
    }
  }
  return option.url as unknown as maplibregl.StyleSpecification
}

const DEFAULT_VIEW_STATE = {
  longitude: 116.4074,
  latitude: 39.9042,
  zoom: 4,
}

function isHeatmapRasterSource(source: Layer["source"]): source is HeatmapRasterSource {
  return typeof source === "object" && source !== null && "image" in source && "bbox" in source
}

function isGeoJSONSource(source: Layer["source"]): source is GeoJSONFeatureCollection {
  return typeof source === "object" && source !== null && "type" in source && source.type === "FeatureCollection"
}

export function MapPanel({ layers, onRemoveLayer: _onRemoveLayer, onToggleLayer: _onToggleLayer, onEditLayer: _onEditLayer, analysisResult }: MapPanelProps) {
  void _onRemoveLayer;
  void _onToggleLayer;
  void _onEditLayer;

  const { selectedBaseLayer, setSelectedBaseLayer } = useMapAction()
  const [showLayerSelector, setShowLayerSelector] = useState(false)
  const [coordinates, setCoordinates] = useState({ lng: 0, lat: 0 })
  const [viewState, setViewState] = useState(DEFAULT_VIEW_STATE)
  const [mapReady, setMapReady] = useState(false)
  const [is3D, setIs3D] = useState(false)
  const [activeFilters, setActiveFilters] = useState<Record<string, number[][]>>({})
  const mapRef = useRef<MapRef>(null)
  const lastAnalysisCenter = useRef<string>("")
  const processLayers = useHudStore((s) => s.processLayers)

  const currentMapStyle = useMemo(
    () => getMapStyle(MAP_STYLES[selectedBaseLayer], selectedBaseLayer),
    [selectedBaseLayer]
  )

  const handleFilterChange = useCallback((layerId: string, ranges: number[][]) => {
    setActiveFilters((prev) => ({
      ...prev,
      [layerId]: ranges,
    }))
  }, [])

  // Listen for base layer change events (triggered by AI or other components)
  // Apply analysis results to map
  useEffect(() => {
    if (analysisResult?.center) {
      const centerKey = `${analysisResult.center[0]},${analysisResult.center[1]},${analysisResult.zoom}`
      if (centerKey !== lastAnalysisCenter.current) {
        lastAnalysisCenter.current = centerKey
        setViewState((prev) => ({
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

  // 3D Terrain Toggle Effect
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map || !mapReady) return

    if (is3D) {
      if (!map.getSource("terrain-aws")) {
        map.addSource("terrain-aws", {
          type: "raster-dem",
          tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"],
          tileSize: 256,
          maxzoom: 14,
        })
      }
      map.setTerrain({ source: "terrain-aws", exaggeration: 1.5 })
      map.easeTo({ pitch: 60, bearing: 20, duration: 1000 })
    } else {
      map.setTerrain(null)
      map.easeTo({ pitch: 0, bearing: 0, duration: 1000 })
    }
  }, [is3D, mapReady])

  const renderTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const isUpdatingRef = useRef(false)

  // Dynamic layer rendering with debounce optimization
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map || !mapReady) return

    const renderLayers = () => {
      if (isUpdatingRef.current) return
      if (!map.isStyleLoaded()) {
        map.once("styledata", renderLayers)
        return
      }

      isUpdatingRef.current = true
      try {
        // Step 1: Remove stale layers/sources
        const style = map.getStyle()
        if (style) {
          for (const layer of style.layers || []) {
            if (layer.id.startsWith("custom-")) {
              const withoutPrefix = layer.id.slice(7)
              const baseId = withoutPrefix.replace(/-[^-]*$/, "")
              if (!layers.find((l) => l.id === baseId)) {
                try { map.removeLayer(layer.id) } catch (_e) { /* silent */ }
              }
            }
          }
          for (const sourceId of Object.keys(style.sources || {})) {
            if (sourceId.startsWith("custom-")) {
              const baseId = sourceId.replace("custom-", "")
              if (!layers.find((l) => l.id === baseId)) {
                try { map.removeSource(sourceId) } catch (_e) { /* silent */ }
              }
            }
          }
        }

        // Step 2: Add or Update layers
        for (const layer of layers) {
          if (!layer.visible || !layer.source) {
            if (map.getSource(`custom-${layer.id}`)) {
              const s = map.getStyle()
              s?.layers?.forEach((l) => {
                if (l.id.startsWith(`custom-${layer.id}`)) {
                  map.setLayoutProperty(l.id, "visibility", "none")
                }
              })
            }
            continue
          }

          const sourceId = `custom-${layer.id}`
          const isNewSource = !map.getSource(sourceId)

          try {
            if (isNewSource) {
              if (layer.type === "raster" || layer.type === "tile") {
                map.addSource(sourceId, { type: "raster", tiles: [layer.source as string], tileSize: 256 })
              } else if (layer.type === "heatmap" && isHeatmapRasterSource(layer.source)) {
                const src = layer.source as HeatmapRasterSource
                const [west, south, east, north] = src.bbox
                map.addSource(sourceId, {
                  type: "image",
                  url: src.image,
                  coordinates: [[west, north], [east, north], [east, south], [west, south]],
                })
              } else {
                map.addSource(sourceId, { type: "geojson", data: layer.source as any })
              }
            } else {
              if (layer.type !== "raster" && layer.type !== "tile" && !(layer.type === "heatmap" && isHeatmapRasterSource(layer.source))) {
                const src = map.getSource(sourceId) as any
                if (src && src.setData) src.setData(layer.source)
              }
            }

            const color = layer.style?.color || "#00f2ff"
            const thematicField = layer.source && typeof layer.source === "object" ? (layer.source as any).metadata?.field : null
            const filterRanges = activeFilters[layer.id]

            const getLayerFilter = (baseType: string): unknown[] => {
              const base: unknown[] = ["==", "$type", baseType]
              if (thematicField && filterRanges) {
                const rangeFilters = filterRanges.map((range: number[]) => ["all", [">=", ["get", thematicField], range[0]], ["<", ["get", thematicField], range[1]]])
                return ["all", base, ["any", ...rangeFilters]]
              }
              return base
            }

            const addOrUpdate = (subId: string, layerConfig: Record<string, unknown>) => {
              const fullId = `custom-${layer.id}-${subId}`
              if (!map.getLayer(fullId)) {
                map.addLayer({ ...layerConfig, id: fullId, source: sourceId } as any)
              } else {
                map.setLayoutProperty(fullId, "visibility", "visible")
                if ((layerConfig as any).filter) map.setFilter(fullId, (layerConfig as any).filter as any)
                if ((layerConfig as any).paint) {
                  Object.keys((layerConfig as any).paint).forEach((key) => {
                    map.setPaintProperty(fullId, key, (layerConfig as any).paint[key])
                  })
                }
              }
            }

            if (layer.type === "raster" || layer.type === "tile") {
              addOrUpdate("main", {
                type: "raster",
                paint: { "raster-opacity": layer.opacity || 1 },
              })
            } else if (layer.type === "heatmap" && isHeatmapRasterSource(layer.source)) {
              addOrUpdate("raster", {
                type: "raster",
                paint: { "raster-opacity": layer.opacity ?? 0.85, "raster-resampling": "linear" },
              })
            } else {
              const src = isGeoJSONSource(layer.source) ? layer.source : null
              const features = src?.features || []
              const hasPolygons = features.some((f) => f.geometry?.type?.includes("Polygon"))
              const hasLines = features.some((f) => f.geometry?.type?.includes("Line"))
              const hasPoints = features.some((f) => f.geometry?.type?.includes("Point"))
              const isNativeHeatmap = layer.type === "heatmap" && src && !isHeatmapRasterSource(layer.source)
              const isHeatmapMode = layer.type === "heatmap" || layer.style?.renderType === "heatmap" || layer.style?.renderType === "grid"

              if (isNativeHeatmap) {
                addOrUpdate("native-heat", {
                  type: "heatmap",
                  maxzoom: 19,
                  paint: {
                    "heatmap-weight": ["interpolate", ["linear"], ["get", "weight"], 0, 0, 1, 1],
                    "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 0, 1, 10, 3, 15, 5, 18, 8],
                    "heatmap-color": [
                      "interpolate", ["linear"], ["heatmap-density"],
                      0, "rgba(0,0,0,0)",
                      0.1, "rgba(0,242,255,0.3)",
                      0.3, "rgba(0,255,65,0.5)",
                      0.5, "rgba(255,255,0,0.7)",
                      0.7, "rgba(255,95,0,0.85)",
                      1, "rgba(255,45,85,1)",
                    ],
                    "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 0, 2, 5, 5, 9, 25, 12, 40, 15, 70, 18, 100],
                    "heatmap-opacity": ["interpolate", ["linear"], ["zoom"], 7, 1, 19, 0.85],
                  },
                })
              } else if (hasPolygons) {
                if (isHeatmapMode) {
                  addOrUpdate("heatgrid", {
                    type: "fill",
                    filter: getLayerFilter("Polygon"),
                    paint: {
                      "fill-color": [
                        "interpolate", ["linear"], ["get", "weight"],
                        0.0, "rgba(0,0,0,0)",
                        0.2, "rgba(0,242,255,0.4)",
                        0.4, "rgba(0,255,65,0.6)",
                        0.6, "rgba(255,255,0,0.7)",
                        0.8, "rgba(255,95,0,0.85)",
                        1.0, "rgba(255,45,85,0.95)",
                      ],
                      "fill-outline-color": "rgba(255, 255, 255, 0.05)",
                      "fill-opacity": layer.opacity ?? 1,
                      "fill-antialias": true,
                    },
                  })
                } else {
                  addOrUpdate("fill", {
                    type: "fill",
                    filter: getLayerFilter("Polygon"),
                    paint: {
                      "fill-color": ["coalesce", ["get", "fill_color"], color],
                      "fill-opacity": (layer.opacity || 1) * 0.3,
                    },
                  })
                  // If 3D mode is active, also add an extrusion layer for polygons
                  if (is3D) {
                    addOrUpdate("extrusion", {
                      type: "fill-extrusion",
                      filter: getLayerFilter("Polygon"),
                      paint: {
                        "fill-extrusion-color": color,
                        "fill-extrusion-height": ["coalesce", ["get", "height"], ["*", ["random"], 100], 20],
                        "fill-extrusion-base": 0,
                        "fill-extrusion-opacity": layer.opacity || 0.8,
                      },
                    })
                  }
                  addOrUpdate("outline", {
                    type: "line",
                    filter: getLayerFilter("Polygon"),
                    paint: {
                      "line-color": ["coalesce", ["get", "stroke_color"], ["get", "fill_color"], color],
                      "line-width": 2,
                      "line-opacity": layer.opacity || 1,
                    },
                  })
                }
              }
              if (hasLines && !isNativeHeatmap) {
                addOrUpdate("line", {
                  type: "line",
                  filter: getLayerFilter("LineString"),
                  paint: {
                    "line-color": ["coalesce", ["get", "fill_color"], color],
                    "line-width": 3,
                    "line-opacity": layer.opacity || 1,
                  },
                })
              }
              if (hasPoints && !isNativeHeatmap) {
                addOrUpdate("point", {
                  type: "circle",
                  filter: getLayerFilter("Point"),
                  paint: {
                    "circle-radius": features.some((f) => f.properties?.weight != null) ? ["interpolate", ["linear"], ["get", "weight"], 0, 4, 1, 8] : 6,
                    "circle-color": ["coalesce", ["get", "fill_color"], color],
                    "circle-stroke-width": 1.5,
                    "circle-stroke-color": "rgba(0, 242, 255, 0.3)",
                    "circle-opacity": layer.opacity || 1,
                  },
                })
              }
            }
          } catch (_e) {
            console.error("[MapPanel] incremental update error for:", layer.id, _e)
          }
        }

        // Step 2b: Render process layers (temporary WS layers)
        for (const [stepId, geojson] of Object.entries(processLayers)) {
          const sourceId = `process-${stepId}`
          if (!map.getSource(sourceId)) {
            map.addSource(sourceId, { type: "geojson", data: geojson as any })
            map.addLayer({
              id: `process-${stepId}-fill`,
              type: "fill",
              source: sourceId,
              paint: {
                "fill-color": "rgba(0, 242, 255, 0.08)",
                "fill-outline-color": "rgba(0, 242, 255, 0.3)",
              },
            })
            map.addLayer({
              id: `process-${stepId}-line`,
              type: "line",
              source: sourceId,
              paint: {
                "line-color": "#00f2ff",
                "line-width": 1.5,
                "line-opacity": 0.4,
                "line-dasharray": [3, 3],
              },
            })
            map.addLayer({
              id: `process-${stepId}-point`,
              type: "circle",
              source: sourceId,
              paint: {
                "circle-radius": 4,
                "circle-color": "rgba(0, 242, 255, 0.3)",
                "circle-stroke-width": 1,
                "circle-stroke-color": "#00f2ff",
              },
            })
          }
        }
        // Clean up stale process layers
        const currentProcessIds = new Set(Object.keys(processLayers))
        const liveStyle = map.getStyle()
        for (const sl of liveStyle?.layers || []) {
          if (sl.id.startsWith("process-")) {
            const stepId = sl.id.replace("process-", "").replace(/-[^-]*$/, "")
            if (!currentProcessIds.has(stepId)) {
              try { map.removeLayer(sl.id) } catch (_e) { /* silent */ }
            }
          }
        }
        for (const sid of Object.keys(liveStyle?.sources || {})) {
          if (sid.startsWith("process-")) {
            const stepId = sid.replace("process-", "")
            if (!currentProcessIds.has(stepId)) {
              try { map.removeSource(sid) } catch (_e) { /* silent */ }
            }
          }
        }

        // Step 3: Z-Index Sync — heatmap layers go below point layers
        const reversedLayers = [...layers].reverse()
        const finalStyle = map.getStyle()
        for (const layer of reversedLayers) {
          const subLayers = finalStyle?.layers?.filter((sl) => sl.id.startsWith(`custom-${layer.id}`)) || []
          subLayers.forEach((sl) => {
            try { if (map.getLayer(sl.id)) map.moveLayer(sl.id) } catch (_e) { /* silent */ }
          })
        }
      } catch (err) {
        console.error("[MapPanel] renderLayers error:", err)
      } finally {
        isUpdatingRef.current = false
      }
    }

    // Debounced trigger
    if (renderTimeoutRef.current) clearTimeout(renderTimeoutRef.current)
    renderTimeoutRef.current = setTimeout(() => {
      renderLayers()
      map.on("styledata", renderLayers)
    }, 50)

    return () => {
      if (renderTimeoutRef.current) clearTimeout(renderTimeoutRef.current)
      map.off("styledata", renderLayers)
      isUpdatingRef.current = false
    }
  }, [layers, mapReady, currentMapStyle, activeFilters, processLayers])


  const setViewport = useHudStore((s) => s.setViewport)

  const handleMove = useCallback((evt: ViewStateChangeEvent) => {
    setViewState(evt.viewState)
    setViewport([evt.viewState.longitude, evt.viewState.latitude], evt.viewState.zoom)
  }, [setViewport])

  const handleMouseMove = useCallback((e: { lngLat: { lng: number; lat: number } }) => {
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
    map.once("render", () => {
      const dataUrl = map.getCanvas().toDataURL("image/png")
      const link = document.createElement("a")
      link.download = `map-${Date.now()}.png`
      link.href = dataUrl
      link.click()
    })
    map.triggerRepaint()
  }

  return (
    <div className="absolute inset-0">
      {/* Map Canvas — Full Viewport */}
      <Map
        id="default"
        ref={mapRef}
        {...viewState}
        onMove={handleMove}
        onMouseMove={handleMouseMove}
        onLoad={() => setMapReady(true)}
        style={{ position: "absolute", inset: 0 }}
        mapStyle={currentMapStyle}
        attributionControl={false}
        {...({ preserveDrawingBuffer: true } as any)}
      >
        <MapActionHandler />
      </Map>

      {/* Legend — floating bottom left */}
      {layers.find((l) => l.visible && (l.source as any)?.metadata?.thematic_type === "choropleth") && (
        <div className="absolute bottom-20 left-4 z-10 transition-all duration-500">
          {(() => {
            const tl = layers.find((l) => l.visible && (l.source as any)?.metadata?.thematic_type === "choropleth")
            if (!tl) return null
            return (
              <ThematicLegend
                metadata={(tl.source as any).metadata}
                onFilterChange={(ranges) => handleFilterChange(tl.id, ranges)}
              />
            )
          })()}
        </div>
      )}

      {/* Floating Map Controls — Left Side */}
      <div className="absolute top-4 left-1/2 -translate-x-1/2 z-10 flex gap-1.5">
        {[
          { onClick: handleZoomIn, icon: <ZoomIn className="h-3.5 w-3.5" />, title: "放大" },
          { onClick: handleZoomOut, icon: <ZoomOut className="h-3.5 w-3.5" />, title: "缩小" },
          { onClick: handleReset, icon: <RotateCcw className="h-3.5 w-3.5" />, title: "复位" },
          { onClick: handleLocate, icon: <Target className="h-3.5 w-3.5" />, title: "定位" },
          { onClick: () => setIs3D(!is3D), icon: <Box className={`h-3.5 w-3.5 ${is3D ? "text-hud-cyan" : ""}`} />, title: "3D视界" },
          { onClick: handleExportPng, icon: <Download className="h-3.5 w-3.5" />, title: "导出PNG" },
        ].map((btn, i) => (
          <button
            key={i}
            onClick={btn.onClick}
            className="hud-btn h-9 w-9 rounded-lg glass-panel text-white/50 hover:text-hud-cyan"
            title={btn.title}
          >
            {btn.icon}
          </button>
        ))}
      </div>

      {/* Base Layer Selector — Top Right */}
      <div className="absolute top-16 right-4 z-10">
        <div className="relative">
          <button
            onClick={() => setShowLayerSelector(!showLayerSelector)}
            className="flex h-9 items-center gap-2 rounded-lg glass-panel px-3 text-white/60 hover:text-white/80 transition-all"
          >
            <Layers className="h-3.5 w-3.5 text-hud-cyan/50" />
            <span className="text-[11px] font-medium">{MAP_STYLES[selectedBaseLayer].name}</span>
            <ChevronDown className={`h-3 w-3 transition-transform ${showLayerSelector ? "rotate-180" : ""}`} />
          </button>

          {showLayerSelector && (
            <div className="absolute top-11 right-0 glass-panel-dense rounded-xl py-1 min-w-44 z-20">
              {MAP_STYLES.map((style, index) => (
                <button
                  key={style.name}
                  onClick={() => handleBaseLayerSelect(index)}
                  className={`w-full text-left px-4 py-2.5 text-[11px] transition-all ${
                    index === selectedBaseLayer
                      ? "text-hud-cyan bg-hud-cyan/10 border-l-2 border-hud-cyan font-medium"
                      : "text-white/50 hover:text-white/70 hover:bg-white/[0.03] border-l-2 border-transparent"
                  }`}
                >
                  {style.name}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Coordinate Bar — Bottom Left */}
      <div className="absolute bottom-3 left-4 z-10 flex items-center gap-3 glass-panel rounded-lg px-3 py-1.5">
        <span className="flex items-center gap-1.5 text-[10px] text-white/25">
          <Compass className="h-3 w-3 text-hud-cyan/30" />
          {MAP_STYLES[selectedBaseLayer].name}
        </span>
        <span className="text-white/[0.06]">|</span>
        <span className="font-mono text-[10px] text-white/40">
          {coordinates.lng.toFixed(4)}°E, {coordinates.lat.toFixed(4)}°N
        </span>
        <span className="text-white/[0.06]">|</span>
        <span className="font-mono text-[10px] text-hud-cyan/40">
          Z{viewState.zoom.toFixed(1)}
        </span>
      </div>
    </div>
  )
}
