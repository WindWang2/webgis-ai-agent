'use client';
/**
 * MVT 大图层的服务端分页属性表数据 hook（Track B · M4）。
 *
 * 数据纪律（对齐 table-data.ts / INTERFACE_CONTRACTS §4）：
 * - 绝不整包水合：每页 ≤200 行游标翻页，行引用共享（不克隆）；
 * - 排序/过滤下推服务端（order/where typed AST），客户端零重排；
 * - 诚实计数：totalMatching 只在服务端可证时显示，未知显示「匹配数未知」；
 * - 作废：查询身份（session/ref/revision/order/where/bbox）变化 →
 *   AbortController 立即作废 in-flight；会话切换经 resolved sessionId 变化
 *   自然进入查询身份；
 * - 错误恢复：409 → conflict（带当前 revision，UI 提供重载）；429 → 单次
 *   退避重试；403/404/网络 → typed 错误态，不静默吞。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  PagedRevisionConflict,
  buildSearchFilter,
  fetchFeaturePage,
} from '@/lib/data-plane/paged-features';
import { resolveRowId, MAX_TABLE_COLUMNS, type TableRow } from '@/lib/map-components/table-data';
import { getSessionIdentity } from '@/lib/store/session-identity';
import type { Layer } from '@/lib/types/layer';

export interface PagedTableSort {
  column: string;
  dir: 1 | -1;
}

export type PagedTableErrorKind =
  | 'conflict' | 'rate' | 'forbidden' | 'notfound' | 'network' | 'badQuery';

export interface PagedTableError {
  kind: PagedTableErrorKind;
  /** conflict 时的服务端当前 revision（重载锚点）。 */
  currentRevision: number | null;
}

export interface PagedTableState {
  status: 'loading' | 'ready' | 'error';
  rows: TableRow[];
  columns: string[];
  nextCursor: string | null;
  totalMatching: number | null;
  featureCount: number | null;
  isComplete: boolean;
  truncated: boolean;
  idMode: 'native' | 'positional' | 'mixed' | null;
  duplicateIdCount: number | null;
  revision: number | null;
  reasonCodes: string[];
  error: PagedTableError | null;
  loadingMore: boolean;
}

const PAGE_LIMIT = 200;
const SEARCH_DEBOUNCE_MS = 300;
const RATE_RETRY_MS = 900;

function extractSessionIdFromTileUrl(tileUrl?: string): string | undefined {
  if (!tileUrl) return undefined;
  try {
    const url = new URL(tileUrl, 'http://dummy');
    return url.searchParams.get('session_id') ?? undefined;
  } catch {
    return undefined;
  }
}

function classifyError(err: unknown): PagedTableError {
  if (err instanceof PagedRevisionConflict) {
    return { kind: 'conflict', currentRevision: err.currentRevision };
  }
  const status = (err as { status?: number } | undefined)?.status;
  if (status === 429) return { kind: 'rate', currentRevision: null };
  if (status === 403) return { kind: 'forbidden', currentRevision: null };
  if (status === 404) return { kind: 'notfound', currentRevision: null };
  if (status === 400) return { kind: 'badQuery', currentRevision: null };
  return { kind: 'network', currentRevision: null };
}

function mergeColumns(prev: string[], features: Array<Record<string, unknown>>): string[] {
  const out = prev.slice();
  const sample = features.slice(0, 20);
  for (const f of sample) {
    const props = (f.properties ?? {}) as Record<string, unknown>;
    for (const key of Object.keys(props)) {
      if (!out.includes(key) && out.length < MAX_TABLE_COLUMNS) out.push(key);
    }
  }
  return out;
}

function rowsFromPage(features: Array<Record<string, unknown>>): TableRow[] {
  return features.map((f) => {
    const props = (f.properties ?? {}) as Record<string, unknown>;
    return { rowId: resolveRowId(props, f.id as string | number | undefined), props };
  });
}

export interface UsePagedTableArgs {
  layer: Layer;
  /** 排序（服务端下推；null = FC 自然序）。 */
  sort: PagedTableSort | null;
  /** 搜索文本（或-contains 下推；空串 = 无过滤）。 */
  searchText: string;
  /** 视口窗口过滤（null = 关闭）。 */
  bbox: [number, number, number, number] | null;
}

export function usePagedLayerTable({ layer, sort, searchText, bbox }: UsePagedTableArgs): PagedTableState & {
  loadMore: () => void;
  reload: () => void;
} {
  const refId = layer._refId ?? '';
  const descriptorRevision = layer._descriptor?.content_revision ?? null;
  const sessionId = getSessionIdentity().sessionId ?? extractSessionIdFromTileUrl(layer._tileUrl) ?? '';
  const ownerToken = getSessionIdentity().ownerToken ?? null;

  // 搜索去抖：输入停顿后再下推，避免逐键打服务端。
  const [debouncedSearch, setDebouncedSearch] = useState(searchText);
  useEffect(() => {
    const h = setTimeout(() => setDebouncedSearch(searchText), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(h);
  }, [searchText]);

  const orderParam = sort ? (sort.dir === -1 ? `-${sort.column}` : sort.column) : undefined;
  // where 依赖列（首屏后才知道列）——先用空列（无过滤），列就绪后查询身份
  // 变化自动重取。列快照进 state，避免渲染期派生身份抖动。
  const [columnsState, setColumnsState] = useState<string[]>([]);
  const where = useMemo(
    () => (columnsState.length ? buildSearchFilter(debouncedSearch, columnsState) : undefined),
    [debouncedSearch, columnsState],
  );

  const [state, setState] = useState<PagedTableState>({
    status: 'loading', rows: [], columns: [], nextCursor: null,
    totalMatching: null, featureCount: null, isComplete: false, truncated: false,
    idMode: null, duplicateIdCount: null, revision: descriptorRevision,
    reasonCodes: [], error: null, loadingMore: false,
  });
  const [reloadNonce, setReloadNonce] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  // 首页（查询身份变化即重取并作废旧请求）。
  useEffect(() => {
    if (!refId || !sessionId) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState((s) => ({ ...s, status: 'loading', error: null, rows: [], nextCursor: null, loadingMore: false }));
    let cancelled = false;
    let retried429 = false;

    const fetchFirstPage = async (): Promise<void> => {
      try {
        const page = await fetchFeaturePage({
          sessionId, refId, revision: descriptorRevision ?? undefined,
          limit: PAGE_LIMIT, order: orderParam, where, bbox: bbox ?? undefined,
          ownerToken, signal: controller.signal,
        });
        if (cancelled || controller.signal.aborted) return;
        const rows = rowsFromPage(page.features);
        const columns = mergeColumns([], page.features);
        setColumnsState(columns);
        setState({
          status: 'ready', rows, columns, nextCursor: page.nextCursor,
          totalMatching: page.totalMatching, featureCount: page.featureCount,
          isComplete: page.isComplete, truncated: page.truncated,
          idMode: page.idMode, duplicateIdCount: page.duplicateIdCount,
          revision: page.revision ?? descriptorRevision,
          reasonCodes: page.reasonCodes, error: null, loadingMore: false,
        });
      } catch (err) {
        if (cancelled || controller.signal.aborted) return;
        const e = classifyError(err);
        if (e.kind === 'rate' && !retried429) {
          retried429 = true;
          await new Promise((r) => setTimeout(r, RATE_RETRY_MS));
          if (cancelled || controller.signal.aborted) return;
          return fetchFirstPage();
        }
        setState((s) => ({ ...s, status: 'error', error: e, loadingMore: false }));
      }
    };
    void fetchFirstPage();
    return () => {
      cancelled = true;
      controller.abort();
    };
    // bbox 以值键参与身份（视口 bbox 每帧新数组身份，值不变不该重取）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refId, sessionId, descriptorRevision, orderParam, where, bbox?.join(','), reloadNonce]);

  // 续页（游标追加；不重置已累积行）。沿用首页请求的 controller ——
  // 查询身份变化/卸载时统一作废。
  const loadMore = useCallback(() => {
    if (state.status !== 'ready' || !state.nextCursor || state.loadingMore) return;
    const controller = abortRef.current;
    setState((s) => ({ ...s, loadingMore: true }));
    (async () => {
      try {
        const page = await fetchFeaturePage({
          sessionId, refId, revision: state.revision ?? undefined,
          limit: PAGE_LIMIT, cursor: state.nextCursor,
          order: orderParam, where, bbox: bbox ?? undefined,
          ownerToken, signal: controller?.signal,
        });
        if (controller?.signal?.aborted) return;
        setState((s) => ({
          ...s,
          loadingMore: false,
          rows: [...s.rows, ...rowsFromPage(page.features)],
          columns: mergeColumns(s.columns, page.features),
          nextCursor: page.nextCursor,
          isComplete: page.isComplete,
          truncated: page.truncated,
          totalMatching: page.totalMatching ?? s.totalMatching,
          reasonCodes: page.reasonCodes,
        }));
      } catch (err) {
        if (controller?.signal?.aborted) return;
        setState((s) => ({ ...s, loadingMore: false, error: classifyError(err) }));
      }
    })();
  }, [state.status, state.nextCursor, state.loadingMore, state.revision, sessionId, refId, orderParam, where, bbox, ownerToken]);

  const reload = useCallback(() => {
    setReloadNonce((n) => n + 1);
  }, []);

  return { ...state, loadMore, reload };
}
