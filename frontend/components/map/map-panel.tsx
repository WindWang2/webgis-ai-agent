"use client"
import { useState, useRef, useCallback, useEffect, useMemo } from "react"
import { MAP_STYLES, MapStyleOption } from "@/lib/constants"
import Map, { MapRef, ViewStateChangeEvent, Popup } from "react-map-gl/maplibre"
import maplibregl from "maplibre-gl"
import type { Layer } from "@/lib/types/layer"
import type { GeoJSONFeatureCollection, HeatmapRasterSource } from "@/lib/types"
import { MapActionHandler } from "./map-action-handler"
import { ThematicLegend } from "./thematic-legend"
import { MapDecorations } from "./map-decorations"
import { useHudStore, type HudState } from "@/lib/store/useHudStore"
import * as renderer from "@/lib/map-kit/renderer"
import { fitBounds as navFitBounds, calculateBBox } from "@/lib/map-kit/navigation"

interface MapPanelProps {
  layers: Layer[]
  onRemoveLayer: (id: string) => void
  onToggleLayer: (id: string) => void
  onViewportChange?: (center: [number, number], zoom: number, bearing: number, pitch: number) => void
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

export function MapPanel({ layers, onRemoveLayer: _onRemoveLayer, onToggleLayer: _onToggleLayer, onViewportChange }: MapPanelProps) {
  void _onRemoveLayer;
  void _onToggleLayer;

  const { selectedBaseLayer, registerSnapshotFn } = useMapAction()
  const [viewState, setViewState] = useState(DEFAULT_VIEW_STATE)
  const [mapReady, setMapReady] = useState(false)
  // is3D 来自 store，与设置面板 setIs3D 联动。原先 useState 死锁在 false。
  const is3D = useHudStore((s: HudState) => s.is3D)
  const [activeFilters, setActiveFilters] = useState<Record<string, number[][]>>({})
  const mapRef = useRef<MapRef>(null)
  const processLayers = useHudStore((s: HudState) => s.processLayers)
  const cartographyTitle = useHudStore((s: HudState) => s.cartographyTitle)
  const viewport = useHudStore((s: HudState) => s.viewport)
  const focusLayerId = useHudStore((s: HudState) => s.focusLayerId)
  const focusLayerSetter = useHudStore((s: HudState) => s.focusLayer)

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

  // Focus Layer Effect — fit map to layer bbox when focusLayerId is set,
  // then clear it back to null so the same layer can be re-focused later.
  useEffect(() => {
    if (!focusLayerId) return
    const map = mapRef.current?.getMap()
    if (!map || !mapReady) return
    const target = layers.find((l) => l.id === focusLayerId)
    if (!target) {
      focusLayerSetter(null)
      return
    }
    const src = target.source as any
    let bbox: [number, number, number, number] | null = null
    if (src && Array.isArray(src.bbox) && src.bbox.length === 4) {
      bbox = src.bbox as [number, number, number, number]
    } else if (src && (src.type === "FeatureCollection" || src.type === "Feature")) {
      bbox = calculateBBox(src)
    }
    if (bbox) {
      try { navFitBounds(map, bbox, 80) } catch (err) {
        console.warn("[map-panel] focusLayer fitBounds failed:", err)
      }
    }
    // Clear after a short delay so the legend flash animation has time to fire.
    const t = window.setTimeout(() => focusLayerSetter(null), 800)
    return () => window.clearTimeout(t)
  }, [focusLayerId, mapReady, layers, focusLayerSetter])

  // 3D Terrain Toggle Effect — 走 map-kit/renderer 的 enable3DTerrain helper
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map || !mapReady) return

    if (is3D) {
      renderer.enable3DTerrain(map, { exaggeration: 1.5 })
      map.easeTo({ pitch: 60, bearing: 20, duration: 1000 })
    } else {
      renderer.disable3DTerrain(map)
      map.easeTo({ pitch: 0, bearing: 0, duration: 1000 })
    }
  }, [is3D, mapReady])

  const renderTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const isUpdatingRef = useRef(false)
  // 审计 F26：styledata 监听器用此 ref 调最新版 renderLayers，
  // 防止 effect 重跑时旧闭包用 stale layers 触发渲染。
  const renderLayersRef = useRef<() => void>(() => {})

  // Dynamic layer rendering with debounce optimization
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map || !mapReady) return

    const renderLayers = () => {
      // 审计 F26：每次 effect 重跑都把最新版存入 ref，让 styledata 监听
      // 始终调当前闭包（避免 stale layers）。
      renderLayersRef.current = renderLayers
      if (isUpdatingRef.current) return
      // Style not loaded yet — retry after a short delay instead of silently dropping.
      // 必须把 timeout id 存进 renderTimeoutRef，让 effect cleanup 清掉，
      // 否则卸载/重渲染后会调用 stale closure。
      if (!map.isStyleLoaded()) {
        if (renderTimeoutRef.current) clearTimeout(renderTimeoutRef.current)
        renderTimeoutRef.current = setTimeout(renderLayers, 100)
        return
      }

      isUpdatingRef.current = true
      try {
        // Step 1: Remove stale layers/sources — M4 用 renderer.removeOrphanCustomLayers
        const knownIds = new Set(layers.map((l) => l.id))
        renderer.removeOrphanCustomLayers(map, knownIds, "custom-")

        // Step 2: Add or Update layers
        for (const layer of layers) {
          if (!layer.visible || !layer.source) {
            if (map.getSource(`custom-${layer.id}`)) {
              renderer.setLayerStackVisibility(map, `custom-${layer.id}`, false)
            }
            continue
          }

          const sourceId = `custom-${layer.id}`
          const isNewSource = !map.getSource(sourceId)

          try {
            if (layer.type === "raster" || layer.type === "tile") {
              renderer.addRasterTileSource(map, sourceId, layer.source as string)
            } else if (layer.type === "heatmap" && isHeatmapRasterSource(layer.source)) {
              const src = layer.source as HeatmapRasterSource
              const [west, south, east, north] = src.bbox
              renderer.addImageSource(map, sourceId, src.image, [[west, north], [east, north], [east, south], [west, south]])
            } else {
              renderer.addGeoJsonSource(map, sourceId, layer.source as any)
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
              } else {
                // Hide stale point sublayer when layer no longer has point features
                const stalePointId = `custom-${layer.id}-point`
                if (map.getLayer(stalePointId)) {
                  map.setLayoutProperty(stalePointId, "visibility", "none")
                }
              }
            }
          } catch (_e) {
            console.error("[MapPanel] incremental update error for:", layer.id, _e)
          }
        }

        // Step 2b: Render process layers — M4 走 renderer.addProcessLayerStack
        for (const [stepId, geojson] of Object.entries(processLayers)) {
          renderer.addProcessLayerStack(map, stepId, geojson)
        }
        // Clean up stale process layers — 走通用 renderer.removeOrphanCustomLayers
        const currentProcessIds = new Set(Object.keys(processLayers))
        renderer.removeOrphanCustomLayers(map, currentProcessIds, "process-")

        // Step 3: Z-Index Sync — heatmap layers go below point layers
        // 走 renderer.syncLayerZOrder：传 layers 数组顺序，helper 自己反向迭代
        renderer.syncLayerZOrder(map, "custom-", layers.map((l) => l.id))
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
    // 审计 F26：用 renderLayersRef 而非闭包内的 renderLayers，确保调最新版。
    const onStyleData = () => {
      if (isUpdatingRef.current) {
        isUpdatingRef.current = false
      }
      if (renderTimeoutRef.current) clearTimeout(renderTimeoutRef.current)
      renderTimeoutRef.current = setTimeout(() => renderLayersRef.current(), 100)
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

  const setSelectedFeature = useHudStore((s: HudState) => s.setSelectedFeature)
  const selectedFeature = useHudStore((s: HudState) => s.selectedFeature)
  const layersRef = useRef(layers)
  useEffect(() => { layersRef.current = layers }, [layers])

  // /review C8: derive interactiveLayerIds from actual style sublayers.
  // The renderer adds sublayer ids like `custom-${id}-fill` / `-line` / `-circle`,
  // not the bare `custom-${id}` — without enumerating sublayers, MapLibre never
  // toggles pointer-cursor on hover and clickable features have no affordance.
  const [interactiveIds, setInteractiveIds] = useState<string[]>([])
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map) return
    const recompute = () => {
      const all = (map.getStyle()?.layers || []) as Array<{ id: string }>
      setInteractiveIds(all.map((l) => l.id).filter((id) => id.startsWith('custom-')))
    }
    recompute()
    map.on('styledata', recompute)
    return () => { map.off('styledata', recompute) }
  }, [layers])

  const handleMapClick = useCallback((evt: any) => {
    const map = mapRef.current?.getMap()
    if (!map) return
    // 只查询我们自己添加的 custom-* 图层；底图瓦片层不应吃 click
    const styleLayers = map.getStyle()?.layers || []
    const customLayerIds = styleLayers
      .map((l: any) => l.id as string)
      .filter((id) => id.startsWith('custom-'))
    if (customLayerIds.length === 0) {
      setSelectedFeature(null)
      return
    }
    const features = map.queryRenderedFeatures(evt.point, { layers: customLayerIds })
    if (!features || features.length === 0) {
      setSelectedFeature(null)
      return
    }
    const top = features[0]
    const sublayerId = top.layer?.id as string | undefined
    // 还原回 ref:xxx：sublayerId 形如 'custom-ref:geojson-xxx' 或 'custom-ref:geojson-xxx-line'
    let refId: string | undefined
    let layerInfo: any
    if (sublayerId) {
      const stripped = sublayerId.replace(/^custom-/, '')
      // 匹配最长 layer.id 前缀
      layerInfo = layersRef.current.find((l) => stripped.startsWith(l.id))
      if (layerInfo?.id?.startsWith('ref:')) {
        refId = layerInfo.id
      }
    }
    setSelectedFeature({
      layerId: sublayerId || 'unknown',
      layerName: layerInfo?.name,
      refId,
      point: [evt.lngLat.lng, evt.lngLat.lat],
      properties: (top.properties || {}) as Record<string, unknown>,
      selectedAt: Date.now(),
    })
  }, [setSelectedFeature])

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
    onViewportChange?.(
      [evt.viewState.longitude, evt.viewState.latitude],
      evt.viewState.zoom,
      evt.viewState.bearing,
      evt.viewState.pitch,
    )
  }, [setViewport, onViewportChange])

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
        onClick={handleMapClick}
        interactiveLayerIds={interactiveIds}
        onLoad={() => { setMapReady(true); useHudStore.getState().setMapLoaded(true); }}
        style={{ position: "absolute", inset: 0 }}
        mapStyle={currentMapStyle}
        attributionControl={false}
        {...({ preserveDrawingBuffer: true } as any)}
      >
        <MapActionHandler />
        {selectedFeature && (
          <Popup
            longitude={selectedFeature.point[0]}
            latitude={selectedFeature.point[1]}
            anchor="bottom"
            onClose={() => setSelectedFeature(null)}
            closeOnClick={false}
          >
            <div className="text-xs p-1 font-sans">
              <div className="font-semibold border-b pb-1 mb-1 border-white/20 text-primary">
                {selectedFeature.layerName || '未命名图层'}
              </div>
              <div className="max-h-32 overflow-y-auto space-y-0.5 min-w-[150px]">
                {Object.entries(selectedFeature.properties).slice(0, 5).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-4">
                    <span className="text-gray-400 font-mono">{k}:</span>
                    <span className="font-mono break-all">{String(v)}</span>
                  </div>
                ))}
                {Object.keys(selectedFeature.properties).length > 5 && (
                  <div className="text-gray-500 text-[10px] italic">
                    ...以及其他 {Object.keys(selectedFeature.properties).length - 5} 个属性
                  </div>
                )}
              </div>
            </div>
          </Popup>
        )}
      </Map>

      {/* Live cartography overlays — driven by layer.legend_spec */}
      {(() => {
        const thematicLayers = layers.filter((l) => l.visible && l.legend_spec);
        if (thematicLayers.length === 0) return null;
        return (
          <>
            <div className="absolute bottom-4 left-4 z-30 space-y-3">
              {thematicLayers.map((l) => {
                const flashing = focusLayerId === l.id;
                return (
                  <div
                    key={l.id}
                    className={`rounded-xl transition-all ${flashing ? "ring-2 ring-primary/80 ring-offset-2 ring-offset-background animate-pulse" : ""}`}
                  >
                    <div className="text-[14px] uppercase tracking-widest text-muted-foreground/60 mb-1 px-1">{l.name}</div>
                    <ThematicLegend spec={l.legend_spec!} onFilterChange={(ranges) => handleFilterChange(l.id, ranges)} />
                  </div>
                );
              })}
            </div>
            <MapDecorations
              show={true}
              title={cartographyTitle ?? thematicLayers[0]?.name ?? null}
              zoom={(viewport as any)?.zoom ?? viewState.zoom ?? 10}
              centerLat={(viewport as any)?.center?.[1] ?? viewState.latitude ?? 30}
              bearing={(viewport as any)?.bearing ?? 0}
            />
          </>
        );
      })()}

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
