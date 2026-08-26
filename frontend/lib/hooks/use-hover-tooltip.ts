'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { MapRef } from 'react-map-gl/maplibre';
import { resolveParentLayerId } from '@/lib/map-kit/interactive-ids';
import type { Layer } from '@/lib/types/layer';

export interface HoverInfo {
  point: [number, number]
  layerName: string
  props: Record<string, unknown>
}

interface UseHoverTooltipOptions {
  /** 地图容器 ref（flushHover 时读取 live map 做 queryRenderedFeatures）。 */
  mapRef: React.RefObject<MapRef | null>
  /** 当前可交互 sublayer id 列表 ref（与点击命中共享的注册表）。 */
  interactiveIdsRef: React.RefObject<string[]>
  /** 图层 id 集合 ref（sublayer → 父层解析用）。 */
  layerIdsSetRef: React.RefObject<Set<string>>
  /** 图层 id → Layer 记录 ref（取图层显示名）。 */
  layersMapRef: React.RefObject<Record<string, Layer>>
}

/**
 * 悬浮提示（hover tooltip）单职责 hook：rAF 节流的 mousemove 查询
 * （每帧至多一次 queryRenderedFeatures）、mouseout/卸载清理，以及
 * 悬浮窗状态（图层名 + ≤3 个关键属性）。返回 hoverInfo 供 JSX 渲染
 * Popup，handleMapMouseMove / handleMapMouseOut 由调用方挂到 map 实例。
 */
export function useHoverTooltip({
  mapRef,
  interactiveIdsRef,
  layerIdsSetRef,
  layersMapRef,
}: UseHoverTooltipOptions) {
  // FE-3 hover tooltip: rAF-throttled mousemove over the interactive sublayers,
  // showing the layer name + ≤3 key props. The query only runs once per frame
  // at most, so a ~60fps pointer sweep never floods the pipeline.
  const [hoverInfo, setHoverInfo] = useState<HoverInfo | null>(null)
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
    const parentId = sublayerId ? resolveParentLayerId(sublayerId, layerIdsSetRef.current) : undefined
    const layerInfo = parentId ? layersMapRef.current[parentId] : undefined
    const props = (top.properties || {}) as Record<string, unknown>
    setHoverInfo({
      point: [evt.lngLat.lng, evt.lngLat.lat],
      layerName: layerInfo?.name ?? parentId ?? sublayerId ?? '未知图层',
      props: Object.fromEntries(Object.entries(props).slice(0, 3)),
    })
    // 依赖里的 ref 对象身份恒定（useRef 产物），回调身份因此保持稳定——
    // 与原先组件内 deps [] 的语义完全一致。
  }, [mapRef, interactiveIdsRef, layerIdsSetRef, layersMapRef])

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

  return { hoverInfo, handleMapMouseMove, handleMapMouseOut }
}
