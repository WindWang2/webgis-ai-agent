'use client';
/**
 * MVT 图层的视口分组聚合 hook（Track B · M7/M8）。
 *
 * inline 层（≤5000 采样）继续走 chart-panel 的 visibleCategoryValues 本地
 * 过滤；MVT 大图层没有内联要素 —— 视口统计改走服务端 /aggregate（bbox
 * 粗滤 + 分组计数，全扫口径），拿到的窗口统计**可证**而非采样。
 *
 * 纪律：
 * - 视口 settle（viewport-context 已 debounce+去重+generation）才发请求；
 *   generation 变化立即作废 in-flight（连续手势不堆积）；
 * - 诚实元数据（scanned/matched/groups/truncated/null 键/非数值/revision）
 *   随行返回，消费端必须展示证据行，不得把窗口统计表述为全量；
 * - 重型分析不在此 hook 的职责内（那是 #1869 分析流程）—— 这里只有
 *   分组计数这一只读投影。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AggregateOverLimit,
  fetchLayerAggregate,
  type AggregateRow,
} from '@/lib/data-plane/paged-features';
import { getSessionIdentity } from '@/lib/store/session-identity';
import type { Layer } from '@/lib/types/layer';

export interface ViewportAggregateMeta {
  scanned: number;
  totalMatching: number | null;
  groupCount: number;
  truncated: boolean;
  nullKeyCount: number;
  nonNumericCount: number;
  revision: number | null;
}

export type ViewportAggregateState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; rows: AggregateRow[]; meta: ViewportAggregateMeta }
  | { status: 'overlimit' }
  | { status: 'error' };

function extractSessionIdFromTileUrl(tileUrl?: string): string | undefined {
  if (!tileUrl) return undefined;
  try {
    const url = new URL(tileUrl, 'http://dummy');
    return url.searchParams.get('session_id') ?? undefined;
  } catch {
    return undefined;
  }
}

export interface UseViewportAggregateArgs {
  /** 仅 extentLinked && MVT 层 && 有分组字段时启用。 */
  enabled: boolean;
  layer: Layer | null | undefined;
  groupBy: string;
  bbox: [number, number, number, number] | null;
  /** viewport generation —— 变化即作废旧请求。 */
  generation: number;
}

export function useViewportAggregate({
  enabled, layer, groupBy, bbox, generation,
}: UseViewportAggregateArgs): ViewportAggregateState {
  const [state, setState] = useState<ViewportAggregateState>({ status: 'idle' });
  const abortRef = useRef<AbortController | null>(null);

  const refId = layer?._refId ?? '';
  const revision = layer?._descriptor?.content_revision ?? null;
  const bboxKey = useMemo(() => (bbox ? bbox.join(',') : ''), [bbox]);

  useEffect(() => {
    if (!enabled || !refId || !groupBy || !bbox) {
      setState({ status: 'idle' });
      return;
    }
    const sessionId = getSessionIdentity().sessionId
      ?? extractSessionIdFromTileUrl(layer?._tileUrl) ?? '';
    if (!sessionId) {
      setState({ status: 'idle' });
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ status: 'loading' });
    (async () => {
      try {
        const agg = await fetchLayerAggregate({
          sessionId,
          refId,
          groupBy,
          bbox: bbox!.slice() as [number, number, number, number],
          revision: revision ?? undefined,
          maxGroups: 64,
          ownerToken: getSessionIdentity().ownerToken ?? null,
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setState({
          status: 'ready',
          rows: agg.rows,
          meta: {
            scanned: agg.scanned,
            totalMatching: agg.totalMatching,
            groupCount: agg.groupCount,
            truncated: agg.truncated,
            nullKeyCount: agg.nullKeyCount,
            nonNumericCount: agg.nonNumericCount,
            revision: agg.revision,
          },
        });
      } catch (err) {
        if (controller.signal.aborted) return;
        if (err instanceof AggregateOverLimit) {
          setState({ status: 'overlimit' });
          return;
        }
        setState({ status: 'error' });
      }
    })();
    return () => {
      controller.abort();
    };
    // bbox 以值键参与（视口坐标连续变化，值不变不重查）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, refId, groupBy, revision, bboxKey, generation]);

  return state;
}
