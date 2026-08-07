"use client"
import { useState, useRef, useCallback, useEffect, useMemo } from "react"
import { MAP_STYLES, MapStyleOption } from "@/lib/constants"
import Map, { MapRef, ViewStateChangeEvent, Popup } from "react-map-gl/maplibre"
import type { StyleSpecification } from "maplibre-gl"
import type { Layer } from "@/lib/types/layer"
import { MapActionHandler } from "./map-action-handler"
import { ThematicLegend } from "./thematic-legend"
import { MapDecorations } from "./map-decorations"
import { useHudStore, type HudState } from "@/lib/store/useHudStore"
import * as renderer from "@/lib/map-kit/renderer"
import { fitBounds as navFitBounds, calculateBBox, calculateBBoxAsync } from "@/lib/map-kit/navigation"
import { MapSpecRuntime, hudStateToMapSpec } from "@/lib/mapspec-runtime"
import { devOnly } from "@/lib/utils/logger"

interface MapPanelProps {
  layers: Layer[]
  onRemoveLayer: (id: string) => void
  onToggleLayer: (id: string) => void
  onViewportChange?: (center: [number, number], zoom: number, bearing: number, pitch: number) => void
}

import { useMapAction } from "@/lib/contexts/map-action-context"

function getMapStyle(option: MapStyleOption, index: number): string | StyleSpecification {
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
  return option.url
}

const DEFAULT_VIEW_STATE = {
  longitude: 116.4074,
  latitude: 39.9042,
  zoom: 4,
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
    let cancelled = false
    let timerId: any = null

    const computeAndFit = async () => {
      let bbox: [number, number, number, number] | null = null
      if (src && Array.isArray(src.bbox) && src.bbox.length === 4) {
        bbox = src.bbox as [number, number, number, number]
      } else if (src && (src.type === "FeatureCollection" || src.type === "Feature")) {
        bbox = await calculateBBoxAsync(src)
      } else if (src) {
        bbox = calculateBBox(src)
      }

      if (cancelled) return
      if (bbox) {
        try { navFitBounds(map, bbox, 80) } catch (err) {
          devOnly.warn("[map-panel] focusLayer fitBounds failed:", err)
        }
      }
      if (!cancelled) {
        timerId = setTimeout(() => focusLayerSetter(null), 800)
      }
    }

    computeAndFit()

    return () => {
      cancelled = true
      if (timerId) window.clearTimeout(timerId)
    }
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

  // ADR-0036: layer rendering is delegated to the MapSpecRuntime, which
  // reconciles a derived MapSpec against the live map via minimal diff/patch.
  // This replaces the previous ~225-line render loop + 6 stale-closure refs
  // (renderTimeoutRef/isUpdatingRef/renderLayersRef) + the styledata re-listen
  // machinery. The runtime owns the style-loaded retry internally.
  const runtimeRef = useRef<MapSpecRuntime | null>(null)

  // Lazily create the runtime once the map instance is available.
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map || !mapReady) return
    if (!runtimeRef.current) {
      runtimeRef.current = new MapSpecRuntime(map)
    }
    return () => {
      runtimeRef.current?.dispose()
      runtimeRef.current = null
    }
  }, [mapReady])

  // FE-AUDIT-01: Invalidate runtime style cache when basemap style changes so custom layers re-apply
  useEffect(() => {
    runtimeRef.current?.invalidateStyle()
  }, [currentMapStyle])

  // Reconcile whenever the inputs to the derived MapSpec change. The runtime's
  // internal diff is the no-op fast path for unchanged specs, and the async
  // path offloads the diff to a worker and applies the patch through a
  // frame-budgeted RenderDebouncer (ADR-0036 / issue #227).
  useEffect(() => {
    if (!runtimeRef.current) return
    const spec = hudStateToMapSpec({ layers, processLayers, activeFilters, is3D })
    void runtimeRef.current.reconcileAsync(spec).catch((e) => console.error("[map] reconcile failed", e))
  }, [layers, processLayers, activeFilters, is3D, mapReady, currentMapStyle])


  const setViewport = useHudStore((s: HudState) => s.setViewport)
  const aiStatus = useHudStore((s: HudState) => s.aiStatus)
  // FE-10：handleMove 在每次地图移动时触发（~60fps）。直接 setViewport 会每帧
  // 写 store，导致订阅 viewport 的 SpatialCrosshair 每帧重渲染。
  // 用 100ms debounce 合并连续写入，平移期间不刷 store，停止后写一次最终值。
  const viewportWriteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // FE-AUDIT-02: Add unmount cleanup for viewportWriteTimerRef
  useEffect(() => {
    return () => {
      if (viewportWriteTimerRef.current) {
        clearTimeout(viewportWriteTimerRef.current)
        viewportWriteTimerRef.current = null
      }
    }
  }, [])

  const setSelectedFeature = useHudStore((s: HudState) => s.setSelectedFeature)
  const selectedFeature = useHudStore((s: HudState) => s.selectedFeature)
  const layersRef = useRef(layers)
  useEffect(() => { layersRef.current = layers }, [layers])

  // /review C8 + ADR-0036: derive interactiveLayerIds from actual style sublayers.
  // The MapSpecRuntime emits sublayer ids like `${layerId}__fill` / `__line` /
  // `__point` (and `process-${stepId}__${sub}`) — the `__` separator marks a
  // sublayer we added. Without enumerating these, MapLibre never toggles
  // pointer-cursor on hover and clickable features have no affordance.
  const [interactiveIds, setInteractiveIds] = useState<string[]>([])
  // 审计 F32：缓存上次计算的 IDs joined 字符串，相同则跳过 setInteractiveIds
  // -> 防止 styledata 频繁触发时产生 re-render 风暴。
  const lastInteractiveIdsRef = useRef<string>('')
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map) return
    const recompute = () => {
      const all = (map.getStyle()?.layers || []) as Array<{ id: string }>
      const ids = all.map((l) => l.id).filter((id) => id.includes('__'))
      const joined = ids.join(',')
      if (joined !== lastInteractiveIdsRef.current) {
        lastInteractiveIdsRef.current = joined
        setInteractiveIds(ids)
      }
    }
    recompute()
    map.on('styledata', recompute)
    return () => { map.off('styledata', recompute) }
  }, [layers])

  const handleMapClick = useCallback((evt: any) => {
    const map = mapRef.current?.getMap()
    if (!map) return
    // 只查询我们自己添加的 __ 子图层；底图瓦片层不应吃 click
    const styleLayers = map.getStyle()?.layers || []
    const customLayerIds = styleLayers
      .map((l: any) => l.id as string)
      .filter((id) => id.includes('__'))
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
    // 还原回 ref:xxx：sublayerId 形如 'ref:geojson-xxx__point' 或
    // 'ref:geojson-xxx__fill'。剥掉 '__${sub}' 后缀得到父 layer.id。
    let refId: string | undefined
    let layerInfo: any
    if (sublayerId) {
      const stripped = sublayerId.replace(/__[^_]*$/, '')
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
    // FE-10：本地 viewState 仍每帧更新（廉价）；store 写入 debounce 100ms，
    // 避免订阅 viewport 的组件（如 SpatialCrosshair）每帧重渲染。
    if (viewportWriteTimerRef.current) clearTimeout(viewportWriteTimerRef.current)
    viewportWriteTimerRef.current = setTimeout(() => {
      setViewport(
        [evt.viewState.longitude, evt.viewState.latitude],
        evt.viewState.zoom,
        evt.viewState.bearing,
        evt.viewState.pitch,
        bounds
      )
      // Phase 8: 视口稳定后（100ms debounce）对大型内联 GeoJSON source
      // 重新按视口过滤 + setData —— 只解析屏幕内要素，平移/缩放不掉帧。
      // 小 source 原样穿透（引用缓存跳过），无原始数据的 tile 源跳过。
      if (bounds && map) {
        renderer.refreshGeoJsonSourcesByViewport(map, bounds)
      }
    }, 100)
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
