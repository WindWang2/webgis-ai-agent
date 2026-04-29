"use client"
import { useState, useRef, useCallback, useEffect, useMemo } from "react"
import { MAP_STYLES, MapStyleOption } from "@/lib/constants"
import Map, { MapRef, ViewStateChangeEvent } from "react-map-gl/maplibre"
import maplibregl from "maplibre-gl"
import type { Layer } from "@/lib/types/layer"
import type { AnalysisResult, GeoJSONFeatureCollection, HeatmapRasterSource } from "@/lib/types"
import { MapActionHandler } from "./map-action-handler"
import { ThematicLegend } from "./thematic-legend"
import { useHudStore, type HudState } from "@/lib/store/useHudStore"

interface MapPanelProps {
  layers: Layer[]
  onRemoveLayer: (id: string) => void
  onToggleLayer: (id: string) => void
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

function parseDashArray(dash: string): number[] {
  switch (dash) {
    case 'dashed': return [4, 2]
    case 'dotted': return [1, 2]
    case 'dashdot': return [4, 2, 1, 2]
    default: return []
  }
}

export function MapPanel({ layers, onRemoveLayer: _onRemoveLayer, onToggleLayer: _onToggleLayer, analysisResult }: MapPanelProps) {
  void _onRemoveLayer;
  void _onToggleLayer;

  const { selectedBaseLayer, registerSnapshotFn } = useMapAction()
  const [viewState, setViewState] = useState(DEFAULT_VIEW_STATE)
  const [mapReady, setMapReady] = useState(false)
  const [is3D] = useState(false)
  const [activeFilters, setActiveFilters] = useState<Record<string, number[][]>>({})
  const mapRef = useRef<MapRef>(null)
  const lastAnalysisCenter = useRef<string>("")
  const processLayers = useHudStore((s: HudState) => s.processLayers)

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
      // Style not loaded yet — retry after a short delay instead of silently dropping
      if (!map.isStyleLoaded()) {
        setTimeout(renderLayers, 100)
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
                try { map.removeLayer(layer.id) } catch { /* silent */ }
              }
            }
          }
          for (const sourceId of Object.keys(style.sources || {})) {
            if (sourceId.startsWith("custom-")) {
              const baseId = sourceId.replace("custom-", "")
              if (!layers.find((l) => l.id === baseId)) {
                try { map.removeSource(sourceId) } catch { /* silent */ }
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

            const color = layer.style?.color || "#16a34a"
            const strokeColor = layer.style?.strokeColor || layer.style?.color || "#16a34a"
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
              const rasterPaint: Record<string, any> = { "raster-opacity": layer.opacity || 1 }
              if (layer.style?.brightness != null) rasterPaint["raster-brightness-max"] = layer.style.brightness
              if (layer.style?.contrast != null) rasterPaint["raster-contrast"] = layer.style.contrast
              if (layer.style?.saturation != null) rasterPaint["raster-saturation"] = layer.style.saturation
              addOrUpdate("main", {
                type: "raster",
                paint: rasterPaint,
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
                      "fill-color": layer.style?.fill !== false
                        ? ["coalesce", ["get", "fill_color"], color]
                        : "rgba(0,0,0,0)",
                      "fill-opacity": layer.style?.fill !== false ? (layer.opacity || 1) * 0.3 : 0,
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
                      "line-color": ["coalesce", ["get", "stroke_color"], ["get", "fill_color"], strokeColor],
                      "line-width": layer.style?.strokeWidth ?? 2,
                      "line-opacity": layer.opacity || 1,
                      ...(layer.style?.dashArray && layer.style.dashArray !== 'solid' ? { "line-dasharray": parseDashArray(layer.style.dashArray) } : {}),
                    },
                  })
                }
              }
              if (hasLines && !isNativeHeatmap) {
                addOrUpdate("line", {
                  type: "line",
                  filter: getLayerFilter("LineString"),
                  paint: {
                    "line-color": ["coalesce", ["get", "fill_color"], strokeColor],
                    "line-width": layer.style?.strokeWidth ?? 2,
                    "line-opacity": layer.opacity || 1,
                    ...(layer.style?.dashArray && layer.style.dashArray !== 'solid' ? { "line-dasharray": parseDashArray(layer.style.dashArray) } : {}),
                  },
                })
              }
              if (hasPoints && !isNativeHeatmap) {
                addOrUpdate("point", {
                  type: "circle",
                  filter: getLayerFilter("Point"),
                  paint: {
                    "circle-radius": layer.style?.pointSize != null ? layer.style.pointSize : features.some((f) => f.properties?.weight != null) ? ["interpolate", ["linear"], ["get", "weight"], 0, 4, 1, 8] : 6,
                    "circle-color": ["coalesce", ["get", "fill_color"], color],
                    "circle-stroke-width": 1.5,
                    "circle-stroke-color": "rgba(22, 163, 74, 0.3)",
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
                "fill-color": "rgba(22, 163, 74, 0.08)",
                "fill-outline-color": "rgba(22, 163, 74, 0.3)",
              },
            })
            map.addLayer({
              id: `process-${stepId}-line`,
              type: "line",
              source: sourceId,
              paint: {
                "line-color": "#16a34a",
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
                "circle-color": "rgba(22, 163, 74, 0.3)",
                "circle-stroke-width": 1,
                "circle-stroke-color": "#16a34a",
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
              try { map.removeLayer(sl.id) } catch { /* silent */ }
            }
          }
        }
        for (const sid of Object.keys(liveStyle?.sources || {})) {
          if (sid.startsWith("process-")) {
            const stepId = sid.replace("process-", "")
            if (!currentProcessIds.has(stepId)) {
              try { map.removeSource(sid) } catch { /* silent */ }
            }
          }
        }

        // Step 3: Z-Index Sync — heatmap layers go below point layers
        const reversedLayers = [...layers].reverse()
        const finalStyle = map.getStyle()
        for (const layer of reversedLayers) {
          const subLayers = finalStyle?.layers?.filter((sl) => sl.id.startsWith(`custom-${layer.id}`)) || []
          subLayers.forEach((sl) => {
            try { if (map.getLayer(sl.id)) map.moveLayer(sl.id) } catch { /* silent */ }
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
    renderTimeoutRef.current = setTimeout(renderLayers, 50)

    // Re-apply layers after basemap style changes (MapLibre destroys custom layers on style change)
    const onStyleData = () => {
      if (isUpdatingRef.current) {
        isUpdatingRef.current = false
      }
      if (renderTimeoutRef.current) clearTimeout(renderTimeoutRef.current)
      renderTimeoutRef.current = setTimeout(renderLayers, 100)
    }
    map.on('styledata', onStyleData)

    return () => {
      if (renderTimeoutRef.current) clearTimeout(renderTimeoutRef.current)
      map.off('styledata', onStyleData)
      isUpdatingRef.current = false
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layers, mapReady, currentMapStyle, activeFilters, processLayers])


  const setViewport = useHudStore((s: HudState) => s.setViewport)
  const aiStatus = useHudStore((s: HudState) => s.aiStatus)

  const handleMove = useCallback((evt: ViewStateChangeEvent) => {
    setViewState(evt.viewState)
    const map = mapRef.current?.getMap()
    const b = map?.getBounds()
    const bounds: [number, number, number, number] | undefined = b
      ? [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
      : undefined
    setViewport(
      [evt.viewState.longitude, evt.viewState.latitude],
      evt.viewState.zoom,
      evt.viewState.bearing,
      evt.viewState.pitch,
      bounds
    )
  }, [setViewport])

  // Register snapshot function — reads directly from MapLibre instance (always fresh)
  useEffect(() => {
    registerSnapshotFn(() => {
      const map = mapRef.current?.getMap()
      if (!map) {
        return {
          center: [viewState.longitude, viewState.latitude],
          zoom: viewState.zoom,
          bearing: (viewState as any).bearing ?? 0,
          pitch: (viewState as any).pitch ?? 0,
          bounds: undefined,
        }
      }
      const center = map.getCenter()
      const zoom = map.getZoom()
      const bearing = map.getBearing()
      const pitch = map.getPitch()
      const b = map.getBounds()
      const bounds: [number, number, number, number] | undefined = b
        ? [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
        : undefined
      return {
        center: [center.lng, center.lat] as [number, number],
        zoom,
        bearing,
        pitch,
        bounds,
      }
    })
  }, [registerSnapshotFn])

  const showPerceptionRings = aiStatus === 'thinking' || aiStatus === 'acting'

  return (
    <div className="absolute inset-0">
      {/* Map Canvas — Full Viewport */}
      <Map
        id="default"
        ref={mapRef}
        {...viewState}
        onMove={handleMove}
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

      {/* Perception Rings — AI activity indicator at map center */}
      {showPerceptionRings && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-20">
          <svg width="120" height="120" viewBox="0 0 120 120" className="opacity-60">
            <circle cx="60" cy="60" r="20" fill="none" stroke="#16a34a" strokeWidth="1.5" className="animate-ring-pulse" />
            <circle cx="60" cy="60" r="35" fill="none" stroke="#16a34a" strokeWidth="1" className="animate-ring-pulse-delay" />
            <circle cx="60" cy="60" r="50" fill="none" stroke="#16a34a" strokeWidth="0.75" className="animate-ring-pulse-delay2" />
          </svg>
        </div>
      )}
    </div>
  )
}
