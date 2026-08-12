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
import { computeInteractiveIds, resolveParentLayerId } from "@/lib/map-kit/interactive-ids"
import {
  setSelectionHighlight,
  clearSelectionHighlight,
  SELECTION_HIGHLIGHT_SOURCE_ID,
} from "@/lib/map-kit/selection-highlight"
import { notifyUserGestureStart, notifyUserGestureEnd } from "@/lib/map-commands/camera-arbitration"
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
      // V3 Performance: use pre-computed descriptor.bbox for MVT-backed large layers.
      // Avoids O(n) coordinate scan over 100k features just to fitBounds.
      if (target._descriptor?.bbox) {
        bbox = target._descriptor.bbox as [number, number, number, number]
      } else if (src && Array.isArray(src.bbox) && src.bbox.length === 4) {
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

  // FE-3 (design §7): derive interactiveLayerIds from the runtime's APPLIED
  // spec — the authoritative registry of what the map currently reflects
  // (sublayer ids `${layerId}__${sub}`, plus `process-${stepId}__${sub}`).
  // Fall back to scanning the live style only while the runtime is missing or
  // a patch is in flight (the map may be partially patched during that window,
  // so appliedSpec can't describe it). Recompute happens when a reconcile
  // completes — the styledata listener is gone (findings E3).
  const [interactiveIds, setInteractiveIds] = useState<string[]>([])
  const interactiveIdsRef = useRef<string[]>([])
  // 审计 F32：缓存上次计算的 IDs joined 字符串，相同则跳过 setInteractiveIds
  // -> 防止频繁 recompute 时产生 re-render 风暴。
  const lastInteractiveIdsKeyRef = useRef<string>('')

  const syncInteractiveIds = useCallback(() => {
    const rt = runtimeRef.current
    const applied = rt?.getAppliedSpec() ?? null
    let ids: string[]
    if (rt && applied && !rt.isPatchInFlight()) {
      ids = computeInteractiveIds(applied, [])
    } else {
      const map = mapRef.current?.getMap()
      const styleLayers = ((map?.getStyle()?.layers as Array<{ id: string }>) || [])
      ids = computeInteractiveIds(null, styleLayers)
    }
    const key = ids.join(',')
    if (key !== lastInteractiveIdsKeyRef.current) {
      lastInteractiveIdsKeyRef.current = key
      interactiveIdsRef.current = ids
      setInteractiveIds(ids)
    }
  }, [])

  // Lazily create the runtime once the map instance is available.
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map || !mapReady) return
    if (!runtimeRef.current) {
      runtimeRef.current = new MapSpecRuntime(map)
      syncInteractiveIds()
    }
    return () => {
      runtimeRef.current?.dispose()
      runtimeRef.current = null
    }
  }, [mapReady, syncInteractiveIds])

  // FE-AUDIT-01: Invalidate runtime style cache when basemap style changes so custom layers re-apply
  useEffect(() => {
    runtimeRef.current?.invalidateStyle()
    // appliedSpec is now null → the fallback style scan is authoritative until
    // the re-apply completes (when syncInteractiveIds runs again).
    syncInteractiveIds()
  }, [currentMapStyle, syncInteractiveIds])

  // FIX-3-2: runtime.syncLayerZOrder moves spec sublayers to the TOP of the
  // z-order after every reconcile, burying the ephemeral selection highlight
  // (its layers were added imperatively and never re-positioned). Re-raise the
  // highlight above the top spec layer whenever a reconcile completes. Guarded:
  // no-op when the highlight isn't mounted, and moveLayer is wrapped so a layer
  // that vanished mid-reconcile is skipped silently.
  const raiseSelectionHighlight = useCallback(() => {
    const map = mapRef.current?.getMap()
    if (!map || !mapReady) return
    const highlightIds = [
      `${SELECTION_HIGHLIGHT_SOURCE_ID}-fill`,
      `${SELECTION_HIGHLIGHT_SOURCE_ID}-line`,
      `${SELECTION_HIGHLIGHT_SOURCE_ID}-circle`,
    ]
    if (!highlightIds.some((id) => map.getLayer(id))) return
    for (const id of highlightIds) {
      if (!map.getLayer(id)) continue
      try {
        // moveLayer without beforeId → end of the layer order (top), i.e.
        // directly above every spec layer syncLayerZOrder just stacked.
        map.moveLayer(id)
      } catch {
        // Layer vanished mid-reconcile — nothing to re-raise.
      }
    }
  }, [mapReady])

  // Reconcile whenever the inputs to the derived MapSpec change. The runtime's
  // internal diff is the no-op fast path for unchanged specs, and the async
  // path offloads the diff to a worker and applies the patch through a
  // frame-budgeted RenderDebouncer (ADR-0036 / issue #227).
  useEffect(() => {
    if (!runtimeRef.current) return
    const spec = hudStateToMapSpec({ layers, processLayers, activeFilters, is3D })
    // FE-3: recompute interactive ids once this patch has actually applied
    // (reconcileAsync resolves when its last op ran → appliedSpec advanced).
    void runtimeRef.current.reconcileAsync(spec)
      .then(() => {
        syncInteractiveIds()
        // FIX-3-2: syncLayerZOrder buried the selection highlight under the
        // spec sublayers — put it back on top now that the reconcile settled.
        raiseSelectionHighlight()
      })
      .catch((e) => console.error("[map] reconcile failed", e))
  }, [layers, processLayers, activeFilters, is3D, mapReady, currentMapStyle, syncInteractiveIds, raiseSelectionHighlight])


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

  /**
   * FE-3: commit a clicked/picked feature as the selection.
   *
   * Stores the PARENT layer id in selectedFeature.layerId — the `__sub`
   * suffix is stripped via LONGEST-prefix match against the project layer ids
   * (fixes poi vs poi_schools mis-attribution, findings D). The raw feature
   * geometry is kept aside for the imperative highlight (it is not part of the
   * store snapshot).
   */
  const selectFeature = useCallback((map: any, feature: any, point: [number, number]) => {
    const sublayerId = feature.layer?.id as string | undefined
    const parentId = sublayerId ? resolveParentLayerId(sublayerId, layersRef.current.map((l) => l.id)) : undefined
    const layerInfo = parentId ? layersRef.current.find((l) => l.id === parentId) : undefined
    pendingSelectionGeometryRef.current = feature.geometry ?? null
    setOverlapFeatures(null)
    setSelectedFeature({
      // 无主图层（process-* 等）时回退到原始 sublayer id。
      layerId: parentId ?? sublayerId ?? 'unknown',
      layerName: layerInfo?.name,
      // 还原回 ref:xxx：sublayerId 形如 'ref:geojson-xxx__point'，父 id 即数据 ref。
      refId: parentId?.startsWith('ref:') ? parentId : undefined,
      point,
      properties: (feature.properties || {}) as Record<string, unknown>,
      selectedAt: Date.now(),
    })
  }, [setSelectedFeature])

  // FE-3: overlap 候选列表（同一点 >1 个要素时弹出，用户挑选）。
  const [overlapFeatures, setOverlapFeatures] = useState<{
    point: [number, number]
    features: any[]
  } | null>(null)

  const pickOverlapFeature = useCallback((feature: any, point: [number, number]) => {
    const map = mapRef.current?.getMap()
    if (!map) return
    selectFeature(map, feature, point)
    setOverlapFeatures(null)
  }, [selectFeature])

  const handleMapClick = useCallback((evt: any) => {
    const map = mapRef.current?.getMap()
    if (!map) return
    // FE-3: reuse the registry-derived ids — the duplicate style scan is gone
    // (findings E3). 只查询我们自己添加的 __ 子图层；底图瓦片层不应吃 click。
    const ids = interactiveIdsRef.current
    if (ids.length === 0) {
      setSelectedFeature(null)
      setOverlapFeatures(null)
      return
    }
    const features = map.queryRenderedFeatures(evt.point, { layers: ids })
    if (!features || features.length === 0) {
      setSelectedFeature(null)
      setOverlapFeatures(null)
      return
    }
    const point: [number, number] = [evt.lngLat.lng, evt.lngLat.lat]
    if (features.length > 1) {
      // FE-3 overlap: 同一位置多个要素 —— 弹出候选列表让用户挑选（top ≤3）。
      setOverlapFeatures({ point, features: features.slice(0, 3) })
      return
    }
    selectFeature(map, features[0], point)
  }, [setSelectedFeature, setOverlapFeatures, selectFeature])

  // FE-3: imperative selection highlight — ephemeral, OUTSIDE MapSpecRuntime /
  // MapSpec (ADR-0036: the spec is derived from HUD state; a click is not).
  const pendingSelectionGeometryRef = useRef<unknown>(null)
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map || !mapReady) return
    if (selectedFeature && pendingSelectionGeometryRef.current) {
      setSelectionHighlight(map, {
        geometry: pendingSelectionGeometryRef.current,
        properties: selectedFeature.properties,
      })
    } else {
      pendingSelectionGeometryRef.current = null
      clearSelectionHighlight(map)
    }
  }, [selectedFeature, mapReady])

  // FE-3 hover tooltip: rAF-throttled mousemove over the interactive sublayers,
  // showing the layer name + ≤3 key props. The query only runs once per frame
  // at most, so a ~60fps pointer sweep never floods the pipeline.
  const [hoverInfo, setHoverInfo] = useState<{
    point: [number, number]
    layerName: string
    props: Record<string, unknown>
  } | null>(null)
  const hoverTimerRef = useRef<number | null>(null)
  const hoverPendingRef = useRef<any>(null)

  const flushHover = useCallback(() => {
    hoverTimerRef.current = null
    const evt = hoverPendingRef.current
    hoverPendingRef.current = null
    if (!evt) return
    const map = mapRef.current?.getMap()
    if (!map) return
    const ids = interactiveIdsRef.current
    if (ids.length === 0) {
      setHoverInfo(null)
      return
    }
    const features = map.queryRenderedFeatures(evt.point, { layers: ids })
    if (!features || features.length === 0) {
      setHoverInfo(null)
      return
    }
    const top = features[0]
    const sublayerId = top.layer?.id as string | undefined
    const parentId = sublayerId ? resolveParentLayerId(sublayerId, layersRef.current.map((l) => l.id)) : undefined
    const layerInfo = parentId ? layersRef.current.find((l) => l.id === parentId) : undefined
    const props = (top.properties || {}) as Record<string, unknown>
    setHoverInfo({
      point: [evt.lngLat.lng, evt.lngLat.lat],
      layerName: layerInfo?.name ?? parentId ?? sublayerId ?? '未知图层',
      props: Object.fromEntries(Object.entries(props).slice(0, 3)),
    })
  }, [])

  const handleMapMouseMove = useCallback((evt: any) => {
    // Keep only the latest event; flush at most once per frame (rAF).
    hoverPendingRef.current = evt
    if (hoverTimerRef.current !== null) return
    if (typeof requestAnimationFrame !== 'undefined') {
      hoverTimerRef.current = requestAnimationFrame(() => flushHover()) as unknown as number
    } else {
      // Node / test fallback (mirrors RenderDebouncer's rAF fallback).
      hoverTimerRef.current = setTimeout(() => flushHover(), 0) as unknown as number
    }
  }, [flushHover])

  // FIX-3-3: the hover tooltip must clear when the cursor leaves the map —
  // previously only mousemove updated it, so it lingered after mouseout. Also
  // drop the pending hover event + cancel any scheduled flush so a stale rAF
  // can't re-set hoverInfo after the cursor is gone.
  const handleMapMouseOut = useCallback(() => {
    hoverPendingRef.current = null
    if (hoverTimerRef.current !== null) {
      if (typeof cancelAnimationFrame !== 'undefined') {
        cancelAnimationFrame(hoverTimerRef.current)
      } else {
        clearTimeout(hoverTimerRef.current)
      }
      hoverTimerRef.current = null
    }
    setHoverInfo(null)
  }, [])

  // FIX-3-4: clear the selection (store + highlight + popup) when the layer it
  // belongs to is removed from the HUD layers — otherwise a stale highlight and
  // popup point at a sublayer the map no longer has. `process-*` overlay
  // selections are skipped: they never live in the project `layers` list (no
  // removable panel entry), so "not in layers" is their steady state, not a
  // removal.
  useEffect(() => {
    if (!selectedFeature) return
    const layerId = selectedFeature.layerId
    if (layerId.startsWith('process-')) return
    const stillPresent =
      layers.some((l) => l.id === layerId) ||
      layers.some((l) => layerId.startsWith(l.id + '__'))
    if (stillPresent) return
    pendingSelectionGeometryRef.current = null
    setOverlapFeatures(null)
    setSelectedFeature(null)
    const map = mapRef.current?.getMap()
    if (map) clearSelectionHighlight(map)
  }, [layers, selectedFeature, setSelectedFeature])

  // FE-3: user gesture arbitration — report to camera-arbitration ONLY when the
  // event carries an originalEvent (programmatic camera moves have none).
  // FIX-3-1: ENDS are gated on originalEvent too. An unguarded end let a
  // programmatic zoomend/pitchend (flyTo completion, map.stop) decrement the
  // counter mid-gesture, so arbitration released early and the next AI camera
  // command fought the user. Ends must only decrement gestures that started.
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map || !mapReady) return
    const gestureStart = (evt: any) => {
      if (evt?.originalEvent) notifyUserGestureStart()
    }
    const gestureEnd = (evt: any) => {
      if (evt?.originalEvent) notifyUserGestureEnd()
    }
    map.on('dragstart', gestureStart)
    map.on('zoomstart', gestureStart)
    map.on('rotatestart', gestureStart)
    map.on('pitchstart', gestureStart)
    map.on('dragend', gestureEnd)
    map.on('zoomend', gestureEnd)
    map.on('rotateend', gestureEnd)
    map.on('pitchend', gestureEnd)
    map.on('mousemove', handleMapMouseMove)
    map.on('mouseout', handleMapMouseOut)
    return () => {
      map.off('dragstart', gestureStart)
      map.off('zoomstart', gestureStart)
      map.off('rotatestart', gestureStart)
      map.off('pitchstart', gestureStart)
      map.off('dragend', gestureEnd)
      map.off('zoomend', gestureEnd)
      map.off('rotateend', gestureEnd)
      map.off('pitchend', gestureEnd)
      map.off('mousemove', handleMapMouseMove)
      map.off('mouseout', handleMapMouseOut)
    }
  }, [mapReady, handleMapMouseMove, handleMapMouseOut])

  // FE-3: cancel any pending hover flush on unmount.
  useEffect(() => {
    return () => {
      if (hoverTimerRef.current !== null) {
        if (typeof cancelAnimationFrame !== 'undefined') {
          cancelAnimationFrame(hoverTimerRef.current)
        } else {
          clearTimeout(hoverTimerRef.current)
        }
        hoverTimerRef.current = null
      }
    }
  }, [])

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

  // FE-3 (design §7): memoize the thematic legend derivation + MapDecorations
  // derived props. viewport 走 store（100ms debounce），move 风暴期间稳定 ——
  // memoized MapDecorations / ThematicLegend 不会每帧重渲染（findings E1）。
  const thematicLayers = useMemo(
    () => layers.filter((l) => l.visible && l.legend_spec),
    [layers],
  )
  // ThematicLegend 是 React.memo —— 内联箭头函数每帧都是新引用会击穿 memo，
  // 这里为每个图层固定一个 handler 引用，move 风暴期间 props 稳定。
  // （注意：本文件顶层 import 了 react-map-gl 的 `Map`，不能用全局 Map 构造器。）
  const legendFilterHandlers = useMemo(() => {
    const handlers: Record<string, (ranges: number[][]) => void> = {}
    for (const l of layers) {
      handlers[l.id] = (ranges) => handleFilterChange(l.id, ranges)
    }
    return handlers
  }, [layers, handleFilterChange])
  const decorProps = useMemo(() => ({
    zoom: (viewport as any)?.zoom ?? viewState.zoom ?? 10,
    centerLat: (viewport as any)?.center?.[1] ?? viewState.latitude ?? 30,
    bearing: (viewport as any)?.bearing ?? 0,
  }), [viewport, viewState])

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
        {overlapFeatures && (
          <Popup
            longitude={overlapFeatures.point[0]}
            latitude={overlapFeatures.point[1]}
            anchor="bottom"
            onClose={() => setOverlapFeatures(null)}
            closeOnClick={false}
          >
            <div className="text-xs p-1 font-sans min-w-[160px]">
              <div className="font-semibold border-b pb-1 mb-1 border-white/20 text-primary">选择要素</div>
              {overlapFeatures.features.map((f, i) => {
                const sublayerId = (f.layer?.id as string | undefined)
                const parentId = sublayerId ? resolveParentLayerId(sublayerId, layersRef.current.map((l) => l.id)) : undefined
                const layerInfo = parentId ? layersRef.current.find((l) => l.id === parentId) : undefined
                const name = layerInfo?.name ?? parentId ?? sublayerId ?? `要素 ${i + 1}`
                const firstProp = Object.entries((f.properties || {}) as Record<string, unknown>)[0]
                return (
                  <button
                    key={i}
                    type="button"
                    className="block w-full text-left px-1 py-0.5 hover:bg-white/10 rounded"
                    onClick={() => pickOverlapFeature(f, overlapFeatures.point)}
                  >
                    <span className="text-gray-400 font-mono">{name}</span>
                    {firstProp && <span className="ml-2 font-mono break-all">{String(firstProp[1])}</span>}
                  </button>
                )
              })}
            </div>
          </Popup>
        )}
        {!overlapFeatures && selectedFeature && (
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
        {!overlapFeatures && !selectedFeature && hoverInfo && (
          <Popup
            longitude={hoverInfo.point[0]}
            latitude={hoverInfo.point[1]}
            anchor="bottom"
            closeOnClick={false}
            closeButton={false}
          >
            <div className="text-xs p-1 font-sans">
              <div className="font-semibold border-b pb-1 mb-1 border-white/20">{hoverInfo.layerName}</div>
              <div className="space-y-0.5 min-w-[120px]">
                {Object.entries(hoverInfo.props).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-4">
                    <span className="text-gray-400 font-mono">{k}:</span>
                    <span className="font-mono break-all">{String(v)}</span>
                  </div>
                ))}
              </div>
            </div>
          </Popup>
        )}
      </Map>

      {/* Live cartography overlays — driven by layer.legend_spec */}
      {thematicLayers.length > 0 && (
        <>
          <div className="absolute bottom-4 z-30 space-y-3" style={{ left: 'var(--workspace-offset, 16px)' }}>
            {thematicLayers.map((l) => {
              const flashing = focusLayerId === l.id;
              return (
                <div
                  key={l.id}
                  className={`rounded-xl transition-all ${flashing ? "ring-2 ring-primary/80 ring-offset-2 ring-offset-background animate-pulse" : ""}`}
                >
                  <div className="text-[14px] uppercase tracking-widest text-muted-foreground/60 mb-1 px-1">{l.name}</div>
                  <ThematicLegend spec={l.legend_spec!} onFilterChange={legendFilterHandlers[l.id]} />
                </div>
              );
            })}
          </div>
          <MapDecorations
            show={true}
            title={cartographyTitle ?? thematicLayers[0]?.name ?? null}
            zoom={decorProps.zoom}
            centerLat={decorProps.centerLat}
            bearing={decorProps.bearing}
          />
        </>
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
