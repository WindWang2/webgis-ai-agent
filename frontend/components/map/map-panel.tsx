"use client"
import { useState, useRef, useCallback, useEffect, useMemo, useSyncExternalStore } from "react"
import { MAP_STYLES, MapStyleOption } from "@/lib/constants"
import Map, { MapRef, ViewStateChangeEvent, Popup } from "react-map-gl/maplibre"
import type { StyleSpecification } from "maplibre-gl"
import type { Layer } from "@/lib/types/layer"

/**
 * #689 图例-过滤对账的纯判定：图例 onFilterChange 载荷 → activeFilters 状态。
 * 恰好覆盖全部类（全可见/规格重置）→ 移除该层过滤键；其余一律保留——含
 * 用户全隐藏的空 ranges（adapter 的 ["any"] 空表达式在 MapLibre 求值为
 * false，正确渲染"该层全部隐藏"）。把空 ranges 当重置删键会让地图全显
 * 而图例全隐（评审修正的反向不一致）。
 */
export function resolveFilterState(
  prev: Record<string, number[][]>,
  layerId: string,
  ranges: number[][],
  layers: Layer[],
): Record<string, number[][]> {
  const spec = layers.find((l) => l.id === layerId)?.legend_spec;
  const breaks = spec && spec.type === "graduated" ? spec.breaks : null;
  const expected = Array.isArray(breaks) ? breaks.length - 1 : null;
  const isFullVisible = expected !== null && ranges.length === expected;
  if (isFullVisible) {
    if (!prev[layerId]) return prev;
    const next = { ...prev };
    delete next[layerId];
    return next;
  }
  return { ...prev, [layerId]: ranges };
}
import { MapActionHandler } from "./map-action-handler"
import { LegendStack } from "./legend-stack"
import { MapDecorations } from "./map-decorations"
import { useHudStore, type HudState } from "@/lib/store/useHudStore"
import * as renderer from "@/lib/map-kit/renderer"
import { remountCustomOverlays } from "@/lib/map-kit/custom-overlay-registry"
import { fitBounds as navFitBounds, calculateBBox, calculateBBoxAsync } from "@/lib/map-kit/navigation"
import { MapSpecRuntime } from "@/lib/mapspec-runtime"
import { composeLiveMapSpec } from "@/lib/mapspec/live-spec"
import {
  injectResolvedRefSources,
  subscribeRefSources,
  getRefSourcesGeneration,
} from "@/lib/mapspec/ref-source-resolver"
import {
  getCommittedMapSpec,
  getMapSpecLiveGeneration,
  getPendingPresentation,
  getPendingRemoved,
  subscribeMapSpecLive,
} from "@/lib/mapspec/session-cursor"
import { computeInteractiveIds } from "@/lib/map-kit/interactive-ids"
import { MapSpecChrome } from "@/components/map/map-spec-chrome"
import { PoiInfoPanel } from "@/components/map/poi-info-panel"
import { raiseAnnotationLayers } from "@/lib/map-commands/annotationHelpers"
import { notifyUserGestureStart, notifyUserGestureEnd } from "@/lib/map-commands/camera-arbitration"
import { devOnly } from "@/lib/utils/logger"
import { buildTileTransformRequest } from "@/lib/map-kit/tile-auth"
import { commitExplicitView } from "@/lib/mapspec/user-mutation"
import { useCartographicObservation } from "@/lib/hooks/use-cartographic-observation"
import { useHoverTooltip } from "@/lib/hooks/use-hover-tooltip"
import { useFeatureSelection } from "@/lib/hooks/use-feature-selection"

interface MapPanelProps {
  layers: Layer[]
  onRemoveLayer: (id: string) => void
  onToggleLayer: (id: string) => void
  onViewportChange?: (center: [number, number], zoom: number, bearing: number, pitch: number) => void
  sessionId?: string | null
  ownerToken?: string | null
  /**
   * SEC-08 anonymous owner_token source (stable ref, read LIVE per request).
   * The token arrives via SSE after the map is constructed and @vis.gl/
   * react-maplibre never re-applies transformRequest on prop change, so the
   * transformRequest closure must read the ref's current value instead of
   * capturing a snapshot.
   */
  sessionTokenRef?: React.MutableRefObject<string | null>
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

export function MapPanel({
  layers,
  onRemoveLayer: _onRemoveLayer,
  onToggleLayer: _onToggleLayer,
  onViewportChange,
  sessionId,
  ownerToken,
  sessionTokenRef,
}: MapPanelProps) {
  void _onRemoveLayer;
  void _onToggleLayer;

  const { selectedBaseLayer, registerSnapshotFn, dispatchAction } = useMapAction()
  // P-9（#882）：非受控相机 —— MapLibre 实例自持真相，onMove 只写
  // viewStateRef（0 re-render 成本）；程序化移动走 mapRef/命令通道。
  // 旧受控回灌（setViewState → {...viewState}）是恒等反馈环，移动期间
  // 每帧触发 MapPanel 整组件重渲染。
  const viewStateRef = useRef(DEFAULT_VIEW_STATE)
  const [decorState, setDecorState] = useState({
    zoom: DEFAULT_VIEW_STATE.zoom,
    centerLat: DEFAULT_VIEW_STATE.latitude,
    bearing: 0,
    // P3：graticule 真实地理 bounds（move 结算时更新；缺席=渲染器自弃）。
    bounds: undefined as { west: number; south: number; east: number; north: number } | undefined,
  })
  const [mapReady, setMapReady] = useState(false)
  const [runtimeRecoveryGeneration, setRuntimeRecoveryGeneration] = useState(0)
  // is3D 来自 store，与设置面板 setIs3D 联动。原先 useState 死锁在 false。
  const is3D = useHudStore((s: HudState) => s.is3D)
  const [activeFilters, setActiveFilters] = useState<Record<string, number[][]>>({})
  const mapRef = useRef<MapRef>(null)
  const processLayers = useHudStore((s: HudState) => s.processLayers)
  const cartographyTitle = useHudStore((s: HudState) => s.cartographyTitle)
  const focusLayerId = useHudStore((s: HudState) => s.focusLayerId)
  const focusLayerSetter = useHudStore((s: HudState) => s.focusLayer)

  const currentMapStyle = useMemo(
    () => getMapStyle(MAP_STYLES[selectedBaseLayer], selectedBaseLayer),
    [selectedBaseLayer]
  )

  // #514: MapLibre tile/image fetches are browser-native and cannot carry
  // headers on their own — inject the same credentials apiFetch sends
  // (Bearer for logged-in, X-Session-Token for anonymous owner_token).
  // The owner_token is read LIVE per request from the stable sessionTokenRef
  // (SSE owner_token arrival + session switch must take effect without a
  // MapLibre transformRequest re-apply).
  const transformRequest = useCallback(
    (url: string, resourceType?: string) =>
      buildTileTransformRequest(() => sessionTokenRef?.current ?? ownerToken ?? null)(url, resourceType),
    [ownerToken, sessionTokenRef],
  )

  const handleFilterChange = useCallback((layerId: string, ranges: number[][]) => {
    setActiveFilters((prev) => resolveFilterState(prev, layerId, ranges, useHudStore.getState().layers))
  }, [])

  // When a layer is removed or its spec fingerprint changes, any
  // activeFilters entry built from the old spec is stale and must be cleared.
  const prevSpecKeysRef = useRef<Record<string, string>>({})
  useEffect(() => {
    const nextKeys: Record<string, string> = {}
    for (const l of layers) {
      const ls: any = l.legend_spec
      nextKeys[l.id] = ls ? `${l._mapspecFingerprint ?? ''}:${ls.breaks?.join(',') ?? ''}:${ls.field ?? ''}` : ''
    }
    const prevKeys = prevSpecKeysRef.current
    let changed = false
    const toDelete: string[] = []
    for (const id of Object.keys(prevKeys)) {
      if (!(id in nextKeys)) {
        // Layer removed
        toDelete.push(id)
        changed = true
      } else if (prevKeys[id] !== nextKeys[id]) {
        // Spec / fingerprint changed -> stale filter
        toDelete.push(id)
        changed = true
      }
    }
    if (changed) {
      setActiveFilters((prev) => {
        let next: Record<string, number[][]> | null = null
        for (const id of toDelete) {
          if (prev[id]) {
            if (!next) next = { ...prev }
            delete next[id]
          }
        }
        return next ?? prev
      })
    }
    prevSpecKeysRef.current = nextKeys
  }, [layers])

  // Focus Layer Effect — fit map to layer bbox when focusLayerId is set,
  // then clear it back to null so the same layer can be re-focused later.
  const lastFittedFocusRef = useRef<string | null>(null)
  useEffect(() => {
    if (!focusLayerId) {
      // #801: 复位时清掉 fitted 标记 —— 同一图层可被再次聚焦（zustand 同值
      // set 不触发订阅，重聚焦依赖这里的清理）。
      lastFittedFocusRef.current = null
      return
    }
    // #739: one fit per focus request — layers identity changes (e.g. a
    // set_view response rewriting layers) used to re-run this effect and
    // refit + re-POST set_view in a self-sustaining cycle.
    if (lastFittedFocusRef.current === focusLayerId) return
    const map = mapRef.current?.getMap()
    // #801: 标记仅在 ready 检查**之后**写入 —— 此前 map 未就绪时先写标记再
    // 早退，pre-ready 的聚焦请求永久丢失。
    if (!map || !mapReady) return
    lastFittedFocusRef.current = focusLayerId
    const target = layers.find((l) => l.id === focusLayerId)
    if (!target) {
      focusLayerSetter(null)
      return
    }
    const src = target.source as any
    let cancelled = false

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

      // audit #843/#852: `cancelled` 只守卫 fit 本身，绝不提前 return 整个
      // continuation —— 复位定时器必须在每条退出路径上调度，否则 bbox await
      // 窗口内的 layers 身份变化会让重跑在 #739 标记处早退、focusLayerId
      // 永久滞留（常亮高亮环 + 每轮携带过期 focus_layer_id）。
      if (!cancelled && bbox) {
        try {
          navFitBounds(map, bbox, 80)
          const center = map.getCenter?.()
          const zoom = map.getZoom?.()
          if (center && typeof zoom === 'number') {
            void commitExplicitView({
              center: [center.lng, center.lat],
              zoom,
              bearing: map.getBearing?.(),
              pitch: map.getPitch?.(),
            })
          }
        } catch (err) {
          devOnly.warn("[map-panel] focusLayer fitBounds failed:", err)
        }
      } else if (!cancelled) {
        // U-8（#890）：无 bbox/无几何的图层聚焦此前完全无反馈（按钮可点但
        // 点击无反应，用户会反复点击认定功能坏了）——诚实告知原因。
        try {
          const { useToastStore } = await import('@/components/ui/toast')
          useToastStore.getState().addToast(
            `图层「${target.name || focusLayerId}」暂无空间范围，无法缩放`,
            'info',
          )
        } catch { /* toast 不可用不影响复位 */ }
      }
      window.setTimeout(() => {
        if (lastFittedFocusRef.current === focusLayerId) {
          focusLayerSetter(null)
        }
      }, 800)
    }

    computeAndFit()

    return () => {
      cancelled = true
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
  // P9：runtime error 观察用的 MapLibre 实例访问器 —— useCallback 固定身份，
  // 否则内联箭头每帧换引用、observation 签发函数（及其依赖的 reconcile
  // effect）随之失效重跑。
  const getMapInstance = useCallback(() => mapRef.current?.getMap() ?? null, [])
  // 制图观测→修复回路整体下沉到 useCartographicObservation（#1009 分解）：
  // generation 门 / AbortController / 修复去重环 / 卸载中止都在 hook 内，
  // 这里只取回 reconcile 落定后要调用的签发函数。
  const issueCartographicObservation = useCartographicObservation({
    runtimeRef,
    sessionId,
    ownerToken,
    dispatchAction,
    // P9：runtime error 观察需要真实 MapLibre 实例（监听生命周期归 hook）。
    getMap: getMapInstance,
  })

  // FE-3 (design §7): derive interactiveLayerIds from the runtime's APPLIED
  // spec — the authoritative registry of what the map currently reflects
  // (sublayer ids `${layerId}__${sub}`, plus `process-${stepId}__${sub}`).
  // Fall back to scanning the live style only while the runtime is missing or
  // a patch is in flight (the map may be partially patched during that window,
  // so appliedSpec can't describe it). Recompute happens when a reconcile
  // completes — the styledata listener is gone (findings E3).
  const [interactiveIds, setInteractiveIds] = useState<string[]>([])
  const interactiveIdsRef = useRef<string[]>([])
  const handleRuntimeStyleRecovery = useCallback(() => {
    setRuntimeRecoveryGeneration((generation) => generation + 1)
  }, [])
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

  // DEBUG(底图消失排查 #2): map error / 全局错误 → window.__mapErrors。
  // #692 生产卫生：dev-only 门（生产零暴露零泄漏）+ 有界 ring（对齐上方
  // appliedRepairIds 的有界惯例；瓦片错误风暴不再无界增长）+ cleanup 卸载
  // window 监听（此前 effect 无 return，监听随组件生命周期泄漏）。
  useEffect(() => {
    if (typeof window === "undefined" || process.env.NODE_ENV !== "development") return;
    const w = window as unknown as { __mapErrors?: unknown[] };
    const RING_LIMIT = 200;
    if (!w.__mapErrors) {
      w.__mapErrors = [];
      const push = (e: unknown) => {
        const err = typeof e === "object" && e !== null && "error" in (e as Record<string, unknown>)
          ? (e as { error?: unknown }).error
          : e;
        const ring = w.__mapErrors!;
        ring.push({ t: Date.now(), msg: String((err as Error)?.message ?? err) });
        if (ring.length > RING_LIMIT) ring.splice(0, ring.length - RING_LIMIT);
      };
      (window as unknown as { __dbgPush?: (e: unknown) => void }).__dbgPush = push;
      window.addEventListener("error", push);
      window.addEventListener("unhandledrejection", push);
      return () => {
        window.removeEventListener("error", push);
        window.removeEventListener("unhandledrejection", push);
        delete (window as unknown as { __dbgPush?: (e: unknown) => void }).__dbgPush;
      };
    }
  }, []);
  useEffect(() => {
    if (process.env.NODE_ENV !== "development") return;
    const map = mapRef.current?.getMap()
    if (!map || !mapReady) return
    const push = (window as unknown as { __dbgPush?: (e: unknown) => void }).__dbgPush
    if (!push) return
    const onErr = (e: { error?: Error }) => push(e)
    map.on("error", onErr as never)
    return () => { map.off("error", onErr as never) }
  }, [mapReady])

  // Lazily create the runtime once the map instance is available.
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map || !mapReady) return
    if (!runtimeRef.current) {
      runtimeRef.current = new MapSpecRuntime(map, {
        onStyleRecovery: handleRuntimeStyleRecovery,
      })
      syncInteractiveIds()
    }
    return () => {
      runtimeRef.current?.dispose()
      runtimeRef.current = null
    }
  }, [mapReady, syncInteractiveIds, handleRuntimeStyleRecovery])

  // FE-AUDIT-01: Invalidate runtime style cache when basemap style changes so custom layers re-apply
  useEffect(() => {
    runtimeRef.current?.invalidateStyle()
    // appliedSpec is now null → the fallback style scan is authoritative until
    // the re-apply completes (when syncInteractiveIds runs again).
    syncInteractiveIds()
  }, [currentMapStyle, syncInteractiveIds])

  // v2 重设计后选中态不再挂高亮图层（曾经的 raise/remount 机制连同
  // 「画布切空白」的触发面一并移除）；保留一个空回调以维持 reconcile
  // 依赖数组的形状稳定。
  const raiseSelectionHighlight = useCallback(() => {}, [])

  const liveGeneration = useSyncExternalStore(
    subscribeMapSpecLive,
    getMapSpecLiveGeneration,
    getMapSpecLiveGeneration,
  )
  // ref 源解析完成（ref-source-resolver 拉回数据）→ 重跑 reconcile，
  // diff 以 source:update + layer:recompile 补挂载 product 等直写图层。
  const refSourcesGeneration = useSyncExternalStore(
    subscribeRefSources,
    getRefSourcesGeneration,
    getRefSourcesGeneration,
  )

  // Reconcile the committed MapSpec plus a pending overlay. HUD is a cache
  // and source payload host, not the Desired author (ADR-0054 / #643).
  useEffect(() => {
    if (!runtimeRef.current) return
    const spec0 = composeLiveMapSpec(
      getCommittedMapSpec(),
      { layers, processLayers, activeFilters, is3D },
      getPendingPresentation(),
      getPendingRemoved(),
    )
    // HUD 已挂靠的 ref 由 live-spec 的 ref_id 合并解析；解析器只兜底
    // 无 HUD 挂靠的（避免对同一份 ref 双下载）。
    const hudOwnedRefs = new Set(
      layers.filter((l) => typeof l._refId === 'string').map((l) => l._refId as string),
    )
    const spec = injectResolvedRefSources(
      spec0,
      sessionId
        ? { sessionId, ownerToken: sessionTokenRef?.current ?? ownerToken ?? null }
        : null,
      hudOwnedRefs,
    )
    // FE-3: recompute interactive ids once this patch has actually applied
    // (reconcileAsync resolves when its last op ran → appliedSpec advanced).
    void runtimeRef.current.reconcileAsync(spec)
      .then(() => {
        syncInteractiveIds()
        const map = mapRef.current?.getMap()
        // #461 (sibling of #401): the imperative `custom-*` overlays
        // (add_layer / heatmap / thematic-map commands) are buried by every
        // layer-changing reconcile — syncLayerZOrder stacks all spec sublayers
        // on top and nothing re-raises the custom band. Restore it FIRST so
        // the ephemeral UX stacks below stay topmost.
        if (map) {
          // v2(#1078 FE1)：basemap setStyle 会抹掉全部命令式 custom-* 覆盖层
          // （旧实现只 z-raise 不重挂 —— 切换即永久丢失）。挂载注册表按
          // 插入序重放后，再由 raise 恢复 custom 带置顶序。reconcile 仅在
          // 新 style 加载完成后 resolve（#605 同款时序约束），此处安全。
          const remounted = remountCustomOverlays(map, {
            onLayerAdded: renderer.noteStyleLayerAdded,
          });
          if (remounted > 0) {
            devOnly.warn('[map] remounted custom overlays after style reload:', remounted);
          }
          renderer.raiseCustomOverlayLayers(map);
        }
        // FIX-3-2: syncLayerZOrder buried the selection highlight under the
        // spec sublayers — put it back on top now that the reconcile settled.
        raiseSelectionHighlight()
        // FIX-3-9 (#401): the imperative annotation stack (markers /
        // measurements / labels) suffers the same burying — syncLayerZOrder
        // stacks every spec sublayer above it on any layer-changing patch.
        // Re-raise it alongside the selection highlight (no-op when the
        // stack isn't mounted, so reconcile-only patches stay cheap).
        if (map) raiseAnnotationLayers(map)
        // #605: a basemap switch (setStyle) sweeps the raster-dem source +
        // setTerrain along with every other imperative layer, but the 3D
        // effect (deps [is3D, mapReady]) never re-runs — mapReady stays true
        // after onLoad. Re-mount terrain at this same style-settled point
        // where the sibling stacks are restored: the reconcile only resolves
        // once the new style is loaded (setTerrain throws mid-load via
        // Style._checkLoaded, so this cannot run earlier). Idempotent — plain
        // reconciles re-assert the same source/terrain.
        if (map) {
          if (is3D) {
            renderer.enable3DTerrain(map, { exaggeration: 1.5 })
          } else {
            renderer.disable3DTerrain(map)
          }
        }
        issueCartographicObservation({ map, spec, layers })
      })
      // #1008：reconcile 失败的裸 console.error 泄漏内部细节 → devOnly。
      .catch((e) => devOnly.error("[map] reconcile failed", e))
  }, [layers, processLayers, activeFilters, is3D, liveGeneration, refSourcesGeneration, mapReady, currentMapStyle, runtimeRecoveryGeneration, syncInteractiveIds, raiseSelectionHighlight, sessionId, ownerToken, sessionTokenRef, issueCartographicObservation])


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

  // 底图自愈看门狗：点击选中要素后检查一次样式健康度。若样式里已无任何
  // 瓦片源（vector/raster 全消失 = 「弹窗还在、画布全空」症状），直接
  // setStyle 重建 + invalidateStyle 让 MapSpecRuntime 重挂业务图层——
  // 与手动切换底图的恢复路径等价，只是自动化。
  useEffect(() => {
    if (!mapReady || !selectedFeature) return
    const timer = setTimeout(() => {
      const map = mapRef.current?.getMap()
      if (!map) return
      try {
        const style = (map as unknown as { getStyle?: () => { sources?: Record<string, unknown>; layers?: unknown[] } }).getStyle?.()
        const sources = Object.values(style?.sources ?? {}) as Array<{ type?: string }>
        const layers = style?.layers ?? []
        const hasTileSource = sources.some((s) => s?.type === "vector" || s?.type === "raster")
        if (layers.length > 0 && !hasTileSource) {
          devOnly.warn("[map] 底图瓦片源丢失，自动重建样式")
          map.setStyle(currentMapStyle)
          runtimeRef.current?.invalidateStyle()
          setRuntimeRecoveryGeneration((g) => g + 1)
        }
      } catch { /* 观测/自愈代码不得反向破坏交互 */ }
    }, 2500)
    return () => clearTimeout(timer)
  }, [selectedFeature, mapReady, currentMapStyle])
  const layersRef = useRef(layers)
  const layersMapRef = useRef<Record<string, Layer>>({})
  const layerIdsSetRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    layersRef.current = layers
    const mapRecord: Record<string, Layer> = {}
    for (const l of layers) mapRecord[l.id] = l
    layersMapRef.current = mapRecord
    layerIdsSetRef.current = new Set(layers.map((l) => l.id))
  }, [layers])

  // 选中入库 + MVT 权威回填（含竞态守卫）下沉到 useFeatureSelection
  // （#1009 分解）；上面的 layers 注册表 ref 仍由本组件维护并传入。
  const { commitSelection } = useFeatureSelection({
    layerIdsSetRef,
    layersMapRef,
    setSelectedFeature,
  })

  // 悬浮窗状态：屏幕坐标 + 命中要素列表（≤5）。纯组件本地状态。
  const [poiPanel, setPoiPanel] = useState<{
    x: number
    y: number
    lngLat: [number, number]
    features: any[]
  } | null>(null)

  const handleMapClick = useCallback((evt: any) => {
    const map = mapRef.current?.getMap()
    if (!map) return
    // 只查询我们自己添加的 __ 子图层；底图瓦片层不应吃 click。
    const ids = interactiveIdsRef.current
    if (ids.length === 0) {
      setSelectedFeature(null)
      setPoiPanel(null)
      return
    }
    let features: any[] = []
    try {
      features = map.queryRenderedFeatures(evt.point, { layers: ids })
    } catch {
      // 会话切换/图层重挂瞬间个别 layer id 可能已失效——查询失败静默收敛，
      // 绝不让异常进入 MapLibre 的事件分发链。
      features = []
    }
    if (!features || features.length === 0) {
      setSelectedFeature(null)
      setPoiPanel(null)
      return
    }
    const point: [number, number] = [evt.lngLat.lng, evt.lngLat.lat]
    // v2 重设计：单/多要素统一进纯 DOM 悬浮窗（候选列表内置），只写快照，
    // 不触发高亮图层、不触发自动聚焦。
    commitSelection(features[0], point)
    setPoiPanel({
      x: evt.point.x,
      y: evt.point.y,
      lngLat: point,
      features: features.slice(0, 5),
    })
  }, [setSelectedFeature, commitSelection])

  // 悬浮提示（rAF 节流 hover 查询 + mouseout/卸载清理）下沉到
  // useHoverTooltip（#1009 分解）；监听仍由下方手势仲裁 effect 挂载。
  const { hoverInfo, handleMapMouseMove, handleMapMouseOut } = useHoverTooltip({
    mapRef,
    interactiveIdsRef,
    layerIdsSetRef,
    layersMapRef,
  })

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
    setPoiPanel(null)
    setSelectedFeature(null)
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

  const handleMove = useCallback((evt: ViewStateChangeEvent) => {
    viewStateRef.current = evt.viewState
    // 悬浮窗锚定在点击时的屏幕像素：地图一动位置就失真，直接关闭。
    setPoiPanel((p) => (p ? null : p))
    const map = mapRef.current?.getMap()
    const b = map?.getBounds()
    const bounds: [number, number, number, number] | undefined = b
      ? [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
      : undefined
    // FE-10：平移/缩放期间本地使用 viewStateRef 跟踪（0 React re-render 成本）；
    // store 与 decorState 写入 debounce 100ms，在手势停止后只触发一次结算更新。
    if (viewportWriteTimerRef.current) clearTimeout(viewportWriteTimerRef.current)
    viewportWriteTimerRef.current = setTimeout(() => {
      setDecorState({
        zoom: evt.viewState.zoom,
        centerLat: evt.viewState.latitude,
        bearing: evt.viewState.bearing ?? 0,
        // P3：graticule live 渲染的真实地理 bounds（同一 debounce 结算，
        // 不新增任何逐帧状态）。
        bounds: bounds
          ? { west: bounds[0], south: bounds[1], east: bounds[2], north: bounds[3] }
          : undefined,
      })
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
        const cur = viewStateRef.current
        return {
          center: [cur.longitude, cur.latitude],
          zoom: cur.zoom,
          bearing: (cur as any).bearing ?? 0,
          pitch: (cur as any).pitch ?? 0,
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

  // FE-3 (design §7): memoize the thematic legend derivation + MapDecorations
  // derived props. decorState 在 move 期间稳定（100ms debounce），结算时更新一次 ——
  // memoized MapDecorations / ThematicLegend 不会每帧重渲染（findings E1）。
  // #679 单图例：热力层由 FloatingLegend 专职渲染（其色带同源自
  // legend_spec.palette_colors），不再进 ThematicLegend 列表 —— 修复同屏
  // 两个互相矛盾的热力图例。
  const thematicLayers = useMemo(
    () => layers.filter((l) => l.visible && l.legend_spec && l.type !== "heatmap"),
    [layers],
  )
  const legendFilterHandlersRef = useRef<Record<string, (ranges: number[][]) => void>>({})
  const legendFilterHandlers = useMemo(() => {
    const handlers: Record<string, (ranges: number[][]) => void> = {}
    // audit #843: 图层 id 每轮分析都换新（哈希后缀）—— 缓存只增不减会让
    // handler 闭包在长会话中无界累积。先按当前图层集修剪，再补新条目
    // （保持 handler 身份稳定的目的不变）。
    const liveIds = new Set(layers.map((l) => l.id))
    for (const id of Object.keys(legendFilterHandlersRef.current)) {
      if (!liveIds.has(id)) delete legendFilterHandlersRef.current[id]
    }
    for (const l of layers) {
      if (!legendFilterHandlersRef.current[l.id]) {
        legendFilterHandlersRef.current[l.id] = (ranges) => handleFilterChange(l.id, ranges)
      }
      handlers[l.id] = legendFilterHandlersRef.current[l.id]
    }
    return handlers
  }, [layers, handleFilterChange])

  const decorProps = useMemo(() => ({
    zoom: decorState.zoom,
    centerLat: decorState.centerLat,
    bearing: decorState.bearing,
    bounds: decorState.bounds,
  }), [decorState])

  // GIS Harness 组件面：committed MapSpec 的 layout.components（Cartography-
  // Component 契约）。有可渲染组件时由 MapSpecChrome 专职渲染 chrome
  // （title/指北针/比例尺/署名/色条/图例），旧 MapDecorations 让位避免双
  // 份；无组件的旧 spec 行为完全不变（HUD chrome 照旧）。
  const committedSpec = useMemo(
    () => getCommittedMapSpec(),
    // liveGeneration 是刻意依赖：spec 提交代数变化时重读 committed doc
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [liveGeneration],
  )
  const specComponents = committedSpec?.layout?.components ?? []
  // 包含清单（只认 MapSpecChrome 实际渲染的类型）：未来未知组件类型不会
  // 静默吞掉既有 chrome。
  const CHROME_RENDERABLE_TYPES = new Set([
    'title', 'subtitle', 'north_arrow', 'scale_bar', 'attribution',
    'continuous_colorbar', 'legend', 'categorical_legend',
    'annotation', 'statistics_panel', 'chart_panel', 'map_border', 'graticule',
    'inset_map',
  ])  // 终审 F4：map_border 有 live 渲染器（P6）—— map_border-only spec
     // 此前不挂 MapSpecChrome，边框导出得出来、live 画不出来。
     // P3：graticule live 渲染器落地（#1089 deferred 补齐）—— graticule-only
     // spec 同理必须挂 chrome（导出画经纬网、live 也画）。
  const enabledSpecComponents = specComponents.filter((c) => c.enabled !== false)
  const hasSpecChrome = enabledSpecComponents.some(
    (c) => CHROME_RENDERABLE_TYPES.has(c.type),
  )
  // spec 图例族组件在场时，HUD 主题图例栈让位（否则同屏两份图例）。
  // 过滤交互仍可用（图层列表/属性面板）；见 PR Known Limitations。
  const hasSpecLegend = enabledSpecComponents.some((c) =>
    c.type === 'continuous_colorbar' || c.type === 'legend' || c.type === 'categorical_legend',
  )

  const showPerceptionRings = aiStatus === 'thinking' || aiStatus === 'acting'

  return (
    /* bg-surface-canvas 作为地图底衬：瓦片未到位（首帧、离线、瓦片报错）时
       暗色主题下会露出一整屏白色画布 —— 地图是主视觉，这里不该闪白。 */
    <div className="absolute inset-0 bg-surface-canvas">
      {/* Map Canvas — Full Viewport */}
      <Map
        id="default"
        ref={mapRef}
        initialViewState={DEFAULT_VIEW_STATE}
        onMove={handleMove}
        onClick={handleMapClick}
        interactiveLayerIds={interactiveIds}
        onLoad={() => { setMapReady(true); useHudStore.getState().setMapLoaded(true); }}
        style={{ position: "absolute", inset: 0 }}
        mapStyle={currentMapStyle}
        attributionControl={false}
        transformRequest={transformRequest}
        {...({ preserveDrawingBuffer: true } as any)}
      >
        <MapActionHandler />
        {poiPanel && (
          <PoiInfoPanel
            x={poiPanel.x}
            y={poiPanel.y}
            features={poiPanel.features}
            layerIds={layerIdsSetRef.current}
            layersMap={layersMapRef.current}
            onClose={() => { setPoiPanel(null); setSelectedFeature(null) }}
          />
        )}
        {!poiPanel && !selectedFeature && hoverInfo && (
          <Popup
            longitude={hoverInfo.point[0]}
            latitude={hoverInfo.point[1]}
            anchor="bottom"
            closeOnClick={false}
            closeButton={false}
          >
            <div className="p-1 font-sans text-meta">
              <div className="mb-1 truncate border-b border-map-chrome-border pb-1 font-semibold text-map-chrome-ink" title={hoverInfo.layerName}>
                {hoverInfo.layerName}
              </div>
              <div className="min-w-[120px] space-y-0.5">
                {Object.entries(hoverInfo.props).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-4">
                    <span className="font-mono text-map-chrome-ink-muted">{k}:</span>
                    <span className="break-all font-mono text-map-chrome-ink">{String(v)}</span>
                  </div>
                ))}
              </div>
            </div>
          </Popup>
        )}
      </Map>

      {/* Live cartography overlays — driven by layer.legend_spec */}
      {thematicLayers.length > 0 && !hasSpecLegend && (
        /* 栈的纵向预算由 LegendStack 自持：top 钉在地图标题之下，多层时
           默认只展开最新一层、其余收折成窄条（2026-08-25 用户反馈图例栈
           整列遮盖地图内容），可滚动。 */
        <LegendStack
          entries={thematicLayers.map((l) => ({
            id: l.id,
            name: l.name,
            legendSpec: l.legend_spec!,
            onFilterChange: legendFilterHandlers[l.id],
            flashing: focusLayerId === l.id,
          }))}
        />
      )}

      {/* #804: 装饰件（指北针/比例尺/标题）不再嵌在「有主题图例」条件内 ——
          纯点/热力会话此前永远没有比例尺；spec chrome 在场时让位
          （MapSpecChrome 自带 north_arrow/scale_bar 缺省回退，与 exporter
          一致），无组件 spec/旧会话行为不变。 */}
      <MapDecorations
        show={!hasSpecChrome}
        title={cartographyTitle ?? thematicLayers[0]?.name ?? null}
        zoom={decorProps.zoom}
        centerLat={decorProps.centerLat}
        bearing={decorProps.bearing}
      />

      {/* GIS Harness 制图组件（MapSpec layout.components 契约渲染面） */}
      {hasSpecChrome && (
        <MapSpecChrome
          components={specComponents}
          zoom={decorProps.zoom}
          centerLat={decorProps.centerLat}
          bearing={decorProps.bearing}
          bounds={decorProps.bounds}
          spec={committedSpec}
        />
      )}

      {/* Perception Rings — AI activity indicator at map center */}
      {showPerceptionRings && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-20">
          <svg width="120" height="120" viewBox="0 0 120 120" className="opacity-60">
            {/* stroke 走 --agent-accent（页面已把 store 的 accentColor 同步到
                该变量），此前硬编码 #16a34a：用户换主题色后感知环还是默认绿。 */}
            <circle cx="60" cy="60" r="20" fill="none" stroke="var(--agent-accent)" strokeWidth="1.5" className="animate-ring-pulse" />
            <circle cx="60" cy="60" r="35" fill="none" stroke="var(--agent-accent)" strokeWidth="1" className="animate-ring-pulse-delay" />
            <circle cx="60" cy="60" r="50" fill="none" stroke="var(--agent-accent)" strokeWidth="0.75" className="animate-ring-pulse-delay2" />
          </svg>
        </div>
      )}
    </div>
  )
}
