'use client';
/**
 * useSelectionHandleTable — 服务端选择集句柄的分页属性表（G24 / P4）。
 *
 * 数据纪律（对齐 paged-table-data）：
 * - 只按 handle 分页（scope/revision 在服务端），客户端零重排零过滤；
 * - 计数诚实：count/count_exact 如实显示（未知 →「匹配数未知」A13）；
 * - 410 stale/expired → conflict 态（数据已刷新，选择句柄失效 —— 引导重建
 *   选择，A22）；403/404/网络 → typed 错误态，不静默吞；
 * - 作废：handle/身份变化 → AbortController 立即作废 in-flight。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  SelectionHandleStale,
  fetchSelectionFeatures,
} from '@/lib/api/selection-handles';
import { resolveRowId, MAX_TABLE_COLUMNS, type TableRow } from '@/lib/map-components/table-data';
import { getSessionIdentity } from '@/lib/store/session-identity';
import type { Layer } from '@/lib/types/layer';

const PAGE_LIMIT = 200;

export interface SelectionTableState {
  status: 'loading' | 'ready' | 'error' | 'stale';
  rows: TableRow[];
  columns: string[];
  nextCursor: string | null;
  count: number | null;
  countExact: boolean;
  loadingMore: boolean;
  reasonCodes: string[];
}

function extractSessionIdFromTileUrl(tileUrl?: string): string | undefined {
  if (!tileUrl) return undefined;
  try {
    const url = new URL(tileUrl, 'http://dummy');
    return url.searchParams.get('session_id') ?? undefined;
  } catch {
    return undefined;
  }
}

function mergeColumns(prev: string[], features: Array<Record<string, unknown>>): string[] {
  const out = prev.slice();
  for (const f of features.slice(0, 20)) {
    const props = (f.properties ?? {}) as Record<string, unknown>;
    for (const key of Object.keys(props)) {
      if (!out.includes(key) && out.length < MAX_TABLE_COLUMNS) out.push(key);
    }
  }
  return out;
}

export function useSelectionHandleTable({ layer, handle }: { layer: Layer; handle: string }): SelectionTableState & {
  loadMore: () => void;
  reload: () => void;
} {
  const refId = layer._refId ?? '';
  const sessionId = getSessionIdentity().sessionId ?? extractSessionIdFromTileUrl(layer._tileUrl) ?? '';
  const ownerToken = getSessionIdentity().ownerToken ?? null;

  const [state, setState] = useState<SelectionTableState>({
    status: 'loading', rows: [], columns: [], nextCursor: null,
    count: null, countExact: false, loadingMore: false, reasonCodes: [],
  });
  const abortRef = useMemo(() => ({ current: null as AbortController | null }), []);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!refId || !sessionId || !handle) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState((s) => ({ ...s, status: 'loading', rows: [], nextCursor: null, loadingMore: false }));
    let cancelled = false;
    (async () => {
      try {
        const page = await fetchSelectionFeatures({
          sessionId, refId, handle, limit: PAGE_LIMIT, ownerToken, signal: controller.signal,
        });
        if (cancelled || controller.signal.aborted) return;
        setState({
          status: 'ready',
          rows: page.features.map((f) => ({
            rowId: resolveRowId((f.properties ?? {}) as Record<string, unknown>, f.id as string | number | undefined),
            props: (f.properties ?? {}) as Record<string, unknown>,
          })),
          columns: mergeColumns([], page.features),
          nextCursor: page.nextCursor,
          count: page.count,
          countExact: page.countExact,
          loadingMore: false,
          reasonCodes: page.reasonCodes,
        });
      } catch (err) {
        if (cancelled || controller.signal.aborted) return;
        if (err instanceof SelectionHandleStale) {
          setState((s) => ({ ...s, status: 'stale', loadingMore: false }));
          return;
        }
        setState((s) => ({ ...s, status: 'error', loadingMore: false }));
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 身份 = ref/handle/会话/nonce
  }, [refId, sessionId, handle, ownerToken, nonce]);

  const loadMore = useCallback(() => {
    if (state.status !== 'ready' || !state.nextCursor || state.loadingMore) return;
    const controller = abortRef.current;
    setState((s) => ({ ...s, loadingMore: true }));
    (async () => {
      try {
        const page = await fetchSelectionFeatures({
          sessionId, refId, handle, limit: PAGE_LIMIT, cursor: state.nextCursor, ownerToken,
          signal: controller?.signal,
        });
        if (controller?.signal?.aborted) return;
        setState((s) => ({
          ...s,
          loadingMore: false,
          rows: [...s.rows, ...page.features.map((f) => ({
            rowId: resolveRowId((f.properties ?? {}) as Record<string, unknown>, f.id as string | number | undefined),
            props: (f.properties ?? {}) as Record<string, unknown>,
          }))],
          columns: mergeColumns(s.columns, page.features),
          nextCursor: page.nextCursor,
        }));
      } catch (err) {
        if (controller?.signal?.aborted) return;
        if (err instanceof SelectionHandleStale) {
          setState((s) => ({ ...s, status: 'stale', loadingMore: false }));
          return;
        }
        setState((s) => ({ ...s, status: 'error', loadingMore: false }));
      }
    })();
  }, [state.status, state.nextCursor, state.loadingMore, sessionId, refId, handle, ownerToken, abortRef]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { ...state, loadMore, reload };
}
