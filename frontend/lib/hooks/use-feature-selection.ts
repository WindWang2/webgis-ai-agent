'use client';
import { useCallback, useEffect, useRef } from 'react';
import { resolveParentLayerId } from '@/lib/map-kit/interactive-ids';
import { ensureLayerData } from '@/lib/store/layer-data';
import { useHudStore } from '@/lib/store/useHudStore';
import { geometryBBox } from '@/lib/utils/geo';
import type { Layer } from '@/lib/types/layer';
import { publishSelection, getSelection } from '@/lib/selection/selection-store';

interface UseFeatureSelectionOptions {
  /** 图层 id 集合 ref（sublayer → 父层解析用）。 */
  layerIdsSetRef: React.RefObject<Set<string>>
  /** 图层 id → Layer 记录 ref（判定 MVT 能力/取图层信息）。 */
  layersMapRef: React.RefObject<Record<string, Layer>>
  /** HUD store 的 setSelectedFeature（点击快照与回填合并都写它）。 */
  setSelectedFeature: (feature: any) => void
}

/**
 * 选中信息入库 + MVT 回填（单职责）：点击链路写 selectedFeature 快照，
 * 随后对 MVT 层发起 single-feature 权威回填。回填竞态由单调 seq +
 * AbortController 守卫 —— 被超越的请求绝不合并进新选中（isStale 五重
 * 判定：abort / AbortError / seq 滚动 / store 快照失配）。返回
 * commitSelection 供点击处理器调用。
 */
export function useFeatureSelection({
  layerIdsSetRef,
  layersMapRef,
  setSelectedFeature,
}: UseFeatureSelectionOptions) {
  // A2: backfill race — AbortController + monotonic seq so superseded fetch never merges
  const selectionBackfillAbortRef = useRef<AbortController | null>(null)
  const selectionSeqRef = useRef(0)
  useEffect(() => {
    return () => {
      selectionBackfillAbortRef.current?.abort()
      selectionBackfillAbortRef.current = null
    }
  }, [])

  /**
   * 选中信息入库（POI 点击重设计 v2）。
   *
   * 点击链路只做两件事：写 selectedFeature 快照（供 AI 会话感知）+
   * 打开纯 DOM 悬浮窗。**不做**：命令式高亮图层、z-order 提升、自动
   * 聚焦相机——这些机制曾在部分会话触发「画布切空白底图」（静默、
   * 无报错，切底图可恢复），重设计后点击不接触任何 GL/样式/相机状态。
   */
  const commitSelection = useCallback((feature: any, point: [number, number], opts?: { additive?: boolean }) => {
    const sublayerId = feature.layer?.id as string | undefined
    const parentId = sublayerId ? resolveParentLayerId(sublayerId, layerIdsSetRef.current) : undefined
    const layerInfo = parentId ? layersMapRef.current[parentId] : undefined
    const rawFeatureId = (feature.id as string | number | undefined) ?? (feature.properties as any)?.id ?? (feature.properties as any)?.OBJECTID
    const targetId = parentId ?? sublayerId
    const layer = targetId ? layersMapRef.current[targetId] : undefined
    const isMvt = !!(layer?._tileUrl && layer?._descriptor?.mvt_capable)
    // `h-` is the synthetic hash-fallback id (e.g. h-1a2b3c4d) assigned by
    // buildSelectedFeatureSnapshot/resolveFeatureId when a feature has no stable
    // `id`/`OBJECTID`/`fid` etc. It is not a real feature id → cannot be used
    // for single-feature backfill; align with layer-data.ts FEATURE_ID_KEYS fallback.
    const hasUsableId = rawFeatureId != null && String(rawFeatureId).trim() !== '' && !String(rawFeatureId).startsWith('h-')
    // initial bbox from tile geometry (approximate)
    let tileBbox: [number, number, number, number] | null = null
    try {
      if (feature.geometry) tileBbox = geometryBBox(feature.geometry as any) as any
    } catch { /* ignore */ }
    const selectedAt = Date.now()
    const seq = ++selectionSeqRef.current
    // Abort any superseded backfill so its promise rejects with AbortError and never merges
    selectionBackfillAbortRef.current?.abort()
    const controller = new AbortController()
    selectionBackfillAbortRef.current = controller
    const layerKey = parentId ?? sublayerId ?? 'unknown';
    setSelectedFeature({
      // 无主图层（process-* 等）时回退到原始 sublayer id。
      layerId: layerKey,
      layerName: layerInfo?.name,
      // 还原回 ref:xxx：sublayerId 形如 'ref:geojson-xxx__point'，父 id 即数据 ref。
      refId: parentId?.startsWith('ref:') ? parentId : undefined,
      point,
      properties: (feature.properties || {}) as Record<string, unknown>,
      selectedAt,
      featureId: rawFeatureId as string | number | undefined,
      bbox: tileBbox,
      ...(isMvt ? { isApproximate: true } : {}),
    })
    // Workspace V2（Goal D4）：map → 共享选择上下文（chart 侧订阅同一份
    // 派生高亮）。选择是 transient UI 状态 —— 不写 MapSpec。
    // Runtime V4：id_field 使表格/框选共享同一稳定要素身份（id 过滤投影）；
    // additive（shift 点选）在同层上追加去重，跨层则替换。
    const prev = opts?.additive ? getSelection() : null
    const sameLayer = prev != null && prev.layer_id === layerKey
    const idField = feature.id != null
      ? '$id'
      : ((feature.properties as any)?.id != null ? 'id' : ((feature.properties as any)?.OBJECTID != null ? 'OBJECTID' : undefined))
    const mergedIds = sameLayer && rawFeatureId != null
      ? Array.from(new Set([...prev!.selected_ids, String(rawFeatureId)]))
      : (rawFeatureId != null ? [String(rawFeatureId)] : [])
    publishSelection('select', {
      source: 'map',
      layer_id: layerKey,
      artifact_ref: parentId?.startsWith('ref:') ? parentId : undefined,
      feature_id: rawFeatureId as string | number | undefined,
      selected_ids: mergedIds,
      id_field: idField != null && mergedIds.length > 0 ? idField : undefined,
      properties: (feature.properties || {}) as Record<string, unknown>,
      bbox: tileBbox ?? undefined,
    })
    // #667/#668: selection truthfulness — backfill authoritative feature for MVT layers
    if (targetId && isMvt) {
      if (!hasUsableId) return
      const isStale = (err?: any): boolean => {
        if (controller.signal.aborted) return true
        if (err?.name === 'AbortError') return true
        if (seq !== selectionSeqRef.current) return true
        const c: any = useHudStore.getState().selectedFeature
        return !c || c.selectedAt !== selectedAt
      }
      void ensureLayerData(targetId, 'selection-detail', { featureId: rawFeatureId as string | number, signal: controller.signal })
        .then((res: any) => {
          if (isStale()) return
          const cur: any = useHudStore.getState().selectedFeature
          if (res?.status === 'single-feature' && res.feature) {
            const af = res.feature as any
            const authProps = (af.properties ?? {}) as Record<string, unknown>
            const geom: any = af.geometry
            let authBbox: [number, number, number, number] | null = cur.bbox ?? null
            try {
              if (geom) {
                const bb = geometryBBox(geom as any)
                if (bb) authBbox = bb as any
              }
              if (Array.isArray(af.bbox) && af.bbox.length === 4) authBbox = af.bbox as any
            } catch { /* ignore */ }
            const authId = (af.id as string | number | undefined) ?? (authProps as any).id ?? cur.featureId
            useHudStore.getState().setSelectedFeature({
              ...cur,
              properties: authProps,
              bbox: authBbox,
              featureId: authId as any,
              isApproximate: false,
            } as any)
          } else if (res?.status === 'fallback') {
            if (isStale()) return
            const cur2: any = useHudStore.getState().selectedFeature
            if (cur2 && cur2.isApproximate !== true) {
              useHudStore.getState().setSelectedFeature({ ...cur2, isApproximate: true } as any)
            }
          }
        })
        .catch((e: any) => {
          if (isStale(e)) return
          const cur: any = useHudStore.getState().selectedFeature
          if (cur && cur.isApproximate !== true) {
            useHudStore.getState().setSelectedFeature({ ...cur, isApproximate: true } as any)
          }
        })
    }
    // 依赖里的 ref 对象身份恒定（useRef 产物），回调身份因此保持稳定——
    // 与原先组件内 deps [setSelectedFeature] 的语义完全一致。
  }, [layerIdsSetRef, layersMapRef, setSelectedFeature])

  return { commitSelection }
}
