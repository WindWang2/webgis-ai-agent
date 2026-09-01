'use client';
import React, {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { useHudStore } from '@/lib/store/useHudStore';
import { ensureLayerData, isMvtLayer } from '@/lib/store/layer-data';
import type { Layer } from '@/lib/types/layer';
import {
  buildTableModel,
  getCachedTableArtifact,
  loadTableArtifact,
  normalizeTablePayload,
  subscribeTableArtifacts,
  getTableArtifactsGeneration,
  type TableModel,
} from '@/lib/map-components/table-data';
import {
  clearSelection,
  getSelection,
  getSelectionGeneration,
  publishSelection,
  subscribeSelection,
} from '@/lib/selection/selection-store';
import {
  getViewportContext,
  getViewportGeneration,
  subscribeViewportContext,
} from '@/lib/selection/viewport-context';
import { bboxIntersects, geometryBBox } from '@/lib/utils/geo';
import { registerComponentRenderer } from './registry';
import { resolveVariant } from './helpers';
import { FloatingChrome, usePlacementPatchedComponent } from './floating-chrome';
import type { RendererContext } from './types';

/**
 * table_panel 渲染器（Runtime V4 §10）：artifact/图层双通道交互表格。
 *
 * 大数据纪律（§10 硬约束）：
 * - 虚拟化（固定行高窗口渲染）—— DOM 行数与视口成正比，与总行数无关；
 * - 行数据引用共享（不 clone 100k feature 对象）；行 id 用稳定要素身份
 *   （FEATURE_ID_KEYS 链）—— table↔map 的 id 过滤投影与框选同源；
 * - 排序/过滤作用在行**索引**上（O(rows) 指针操作，无对象复制）。
 *
 * 跨视图联动（§9）：
 * - table→map：行点击发布 select（source='table'，携带 id_field）；
 * - map/brush→table：行高亮 + 首行 scrollIntoView；
 * - chart→table：filter_field 类别选择 → 行过滤（同字段同类别）；
 * - viewport（§12）：可选行过滤（内联层 + 缓存 bbox；MVT 层诚实不支持）。
 */

const ROW_HEIGHT = 28;
const OVERSCAN = 8;
const TABLE_PANEL_VARIANTS = new Set(['default', 'compact']);
/** 视口行过滤只在行数有界时启用（bbox 求值有 WeakMap 记忆，首遍 O(rows)）。 */
const VIEWPORT_FILTER_MAX_ROWS = 50000;

type TableState =
  | { status: 'empty' }
  | { status: 'loading' }
  | { status: 'hydrating' }
  | { status: 'unavailable' }
  | { status: 'ready'; model: TableModel; idField: string | null; features?: Array<{ geometry?: unknown } | null> };

function cellText(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'object') return Array.isArray(value) ? `[${value.length}]` : '{…}';
  const s = String(value);
  return s.length > 64 ? `${s.slice(0, 61)}…` : s;
}

/** 行 id 解析字段（与 buildTableModel 的 resolveRowId 同链）：样例推导。 */
function detectIdField(model: TableModel): string | null {
  for (const row of model.rows.slice(0, 5)) {
    for (const key of ['id', 'OBJECTID', 'fid', 'osm_id']) {
      if (row.props[key] != null && row.props[key] !== '') return key === 'id' && row.rowId.startsWith('h-') ? null : key;
    }
    if (row.rowId.startsWith('h-')) return null; // 内容哈希兜底 —— 地图侧无法编译 id 过滤
  }
  return null;
}

function TablePanelView({ component, ctx }: { component: MapSpecComponent; ctx?: RendererContext }) {
  const patched = usePlacementPatchedComponent(component);
  const variant = TABLE_PANEL_VARIANTS.has(resolveVariant(patched, 'default'))
    ? resolveVariant(patched, 'default')
    : 'default';
  const options = patched.options ?? {};
  const tableRef = typeof options['tableRef'] === 'string' && options['tableRef'].trim()
    ? (options['tableRef'] as string)
    : '';
  const layerId = typeof options['layerId'] === 'string' && options['layerId'].trim()
    ? (options['layerId'] as string)
    : '';
  const preferredColumns = Array.isArray(options['columns'])
    ? (options['columns'] as unknown[]).filter((c): c is string => typeof c === 'string')
    : undefined;

  // ── 数据通道 ─────────────────────────────────────────────────────────
  // ref 通道：模块缓存 + in-flight 去重（chart-artifact 同款）。
  useSyncExternalStore(subscribeTableArtifacts, getTableArtifactsGeneration);
  const [fetchedRef, setFetchedRef] = useState<unknown>(undefined);
  useEffect(() => {
    if (!tableRef) {
      setFetchedRef(undefined);
      return;
    }
    setFetchedRef(undefined);
    let alive = true;
    void loadTableArtifact(tableRef).then((payload) => {
      if (alive) setFetchedRef(payload);
    });
    return () => {
      alive = false;
    };
  }, [tableRef]);

  // layer 通道：HUD store 直读（引用共享）；MVT 层按需水合（attribute-table
  // 预留 reason 的首个消费者）。
  const layersGeneration = useHudStore((s) => s.layers);
  const boundLayer = useMemo(
    () => layersGeneration.find((l: Layer) => l.id === layerId),
    [layersGeneration, layerId],
  );
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    setHydrated(false);
  }, [layerId]);
  useEffect(() => {
    if (!layerId || !boundLayer || !isMvtLayer(boundLayer) || hydrated) return;
    let alive = true;
    void ensureLayerData(layerId, 'attribute-table')
      .then(() => {
        if (alive) setHydrated(true);
      })
      .catch(() => {
        if (alive) setHydrated(true); // 水合失败 → 显示不可用（不阻塞）
      });
    return () => {
      alive = false;
    };
  }, [layerId, boundLayer, hydrated]);

  const state: TableState = useMemo(() => {
    if (tableRef) {
      const payload = fetchedRef !== undefined ? fetchedRef : getCachedTableArtifact(tableRef);
      if (payload === undefined) return { status: 'loading' };
      const model = normalizeTablePayload(payload as never, preferredColumns);
      return model
        ? { status: 'ready', model, idField: detectIdField(model) }
        : { status: 'unavailable' };
    }
    if (layerId) {
      const src = boundLayer?.source as { features?: Array<{ properties?: Record<string, unknown>; id?: string | number; geometry?: unknown }> } | undefined;
      const features = src && Array.isArray(src.features) ? src.features : null;
      if (boundLayer && isMvtLayer(boundLayer) && !features?.length && !hydrated) {
        return { status: 'hydrating' };
      }
      if (features && features.length) {
        const records = features.map((f) => f.properties ?? {});
        const model = buildTableModel(records, preferredColumns);
        // 顶层 feature.id 优先（MVT/瓦片路径的稳定身份）。
        return {
          status: 'ready',
          model,
          idField: detectIdField(model) ?? (features.some((f) => f.id != null) ? '$id' : null),
          features,
        };
      }
      return boundLayer ? { status: 'unavailable' } : { status: 'empty' };
    }
    return { status: 'empty' };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- layersGeneration identity + fetchedRef are the change signals
  }, [tableRef, fetchedRef, layerId, boundLayer, hydrated, preferredColumns]);

  // ── 排序 / 过滤（行索引操作，零复制）─────────────────────────────────
  const [sortKey, setSortKey] = useState<{ column: string; dir: 1 | -1 } | null>(null);
  const [filterText, setFilterText] = useState('');
  const [viewportOnly, setViewportOnly] = useState(false);

  useSyncExternalStore(subscribeViewportContext, getViewportGeneration);
  const viewport = getViewportContext();

  const order = useMemo(() => {
    if (state.status !== 'ready') return [];
    let idx = state.model.rows.map((_, i) => i);
    if (filterText.trim()) {
      const needle = filterText.trim().toLowerCase();
      idx = idx.filter((i) =>
        state.model.columns.some((col) =>
          String(state.model.rows[i].props[col] ?? '').toLowerCase().includes(needle)));
    }
    // 视口行过滤（§12）：只在图层通道（有几何引用）且行数有界时启用；
    // bbox 求值走 geometryBBox 的 WeakMap 记忆（首遍 O(rows) 后 O(1) 查表）。
    if (viewportOnly && viewport && state.features && state.model.rows.length <= VIEWPORT_FILTER_MAX_ROWS) {
      const vb = viewport.bbox;
      idx = idx.filter((i) => {
        const geom = state.features?.[i]?.geometry as { type: string; coordinates: unknown } | undefined;
        if (!geom) return false;
        const bb = geometryBBox(geom);
        return bb != null && bboxIntersects(bb, vb);
      });
    }
    if (sortKey) {
      const col = sortKey.column;
      idx = [...idx].sort((a, b) => {
        const va = state.model.rows[a].props[col];
        const vb = state.model.rows[b].props[col];
        const na = typeof va === 'number' ? va : Number(va);
        const nb = typeof vb === 'number' ? vb : Number(vb);
        const cmp = Number.isFinite(na) && Number.isFinite(nb) && va != null && vb != null && typeof va !== 'boolean'
          ? na - nb
          : cellText(va).localeCompare(cellText(vb), 'zh');
        return cmp * sortKey.dir;
      });
    }
    return idx;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- viewport generation rides the memo key when toggle on
  }, [state, filterText, sortKey, viewportOnly, viewportOnly ? viewport?.generation : 0]);

  // ── 跨视图选择联动 ───────────────────────────────────────────────────
  useSyncExternalStore(subscribeSelection, getSelectionGeneration);
  const selection = getSelection();
  // id 空间桥接（chart-panel 同款）：面板绑定层 id ↔ 选择发布层 id。
  const selectionMatchesLayer = !!selection && !!layerId
    && (selection.layer_id === layerId
      || useHudStore.getState().layers.some(
        (row: Layer) =>
          (row._mapspecLayerId === layerId && row.id === selection.layer_id)
          || (row._mapspecLayerId === selection.layer_id && row.id === layerId)));
  // map/brush/表格来源：行高亮集
  const selectedRowIds = useMemo(() => {
    if (!selection || !selectionMatchesLayer) return null;
    if (selection.source === 'chart') return null; // chart 走行过滤（下方）
    if (!selection.selected_ids.length) return null;
    return new Set(selection.selected_ids);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection, selectionMatchesLayer]);
  // chart 来源（§9.5）：类别选择 → 行过滤（同字段同类别）
  const chartFilter = useMemo(() => {
    if (!selection || !selectionMatchesLayer || selection.source !== 'chart') return null;
    if (!selection.filter_field || !selection.selected_categories.length) return null;
    return { field: selection.filter_field, categories: new Set(selection.selected_categories) };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection, selectionMatchesLayer]);

  // 面板卸载：清掉本面板发布的 table 选择（chart-panel 同款纪律）。
  useEffect(() => {
    return () => {
      const sel = getSelection();
      if (sel && sel.source === 'table' && sel.layer_id === layerId) {
        clearSelection();
      }
    };
  }, [layerId]);

  const visibleOrder = useMemo(() => {
    if (!order.length) return order;
    if (chartFilter) {
      return order.filter((i) => {
        const v = state.status === 'ready' ? state.model.rows[i].props[chartFilter.field] : undefined;
        return v != null && chartFilter.categories.has(String(v));
      });
    }
    return order;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order, chartFilter]);

  // ── 窗口渲染（虚拟化）────────────────────────────────────────────────
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(240);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => setViewH(el.clientHeight || 240));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const total = visibleOrder.length;
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const end = Math.min(total, Math.ceil((scrollTop + viewH) / ROW_HEIGHT) + OVERSCAN);
  const slice = visibleOrder.slice(start, end);

  // map→table：选中行 scrollIntoView（首命中行）。
  const lastSelectedIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedRowIds || state.status !== 'ready' || !scrollRef.current) return;
    const first = selectedRowIds.values().next().value as string | undefined;
    if (!first || lastSelectedIdRef.current === first) return;
    lastSelectedIdRef.current = first;
    const rowIndex = visibleOrder.findIndex((i) => state.model.rows[i].rowId === first);
    if (rowIndex >= 0 && scrollRef.current) {
      scrollRef.current.scrollTop = Math.max(0, rowIndex * ROW_HEIGHT - viewH / 2);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRowIds, visibleOrder, state]);

  const handleRowClick = (rowId: string) => {
    if (!layerId) return;
    const sel = getSelection();
    // 再点同一行 → 清除（与 chart 类别同款 toggle 语义）。
    if (sel && sel.source === 'table' && sel.layer_id === layerId && sel.selected_ids.length === 1 && sel.selected_ids[0] === rowId) {
      publishSelection('clear_selection', { source: 'table', layer_id: layerId });
      return;
    }
    publishSelection('select', {
      source: 'table',
      layer_id: layerId,
      selected_ids: [rowId],
      id_field: state.status === 'ready' && state.idField ? state.idField : undefined,
      artifact_ref: tableRef || undefined,
    });
  };

  const title = typeof options['title'] === 'string' && options['title'].trim()
    ? (options['title'] as string)
    : boundLayer?.name || (state.status === 'ready' ? '属性表' : '表格');

  return (
    <FloatingChrome
      component={patched}
      title={title}
      topSlotIndexes={ctx?.topSlotIndexes}
      testId="spec-chrome-table-panel"
      dataVariant={variant}
      bodyClassName={variant === 'compact' ? 'p-1.5' : 'p-2'}
    >
      {state.status !== 'ready' ? (
        <div
          className="flex h-full min-h-16 items-center justify-center px-2 py-3 text-caption text-map-chrome-ink-muted"
          data-state={state.status}
          role="status"
        >
          {state.status === 'loading' || state.status === 'hydrating'
            ? '表格加载中…'
            : state.status === 'unavailable'
              ? '表格数据不可用'
              : '未绑定数据（tableRef 或 layerId）'}
        </div>
      ) : (
        <div className="flex h-full min-h-40 flex-col gap-1" data-testid="table-panel-body">
          {/* 工具条：过滤框 + 视口过滤开关 + 披露 */}
          <div className="flex items-center gap-1.5">
            <input
              type="text"
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              placeholder="过滤行…"
              aria-label="表格行过滤"
              className="h-6 min-w-0 flex-1 rounded-xs border border-map-chrome-border bg-surface-base px-1.5 text-caption text-map-chrome-ink"
            />
            {layerId && (
              <button
                type="button"
                aria-pressed={viewportOnly}
                title="只显示当前视口范围内的行"
                onClick={() => setViewportOnly((v) => !v)}
                className={`h-6 shrink-0 rounded-xs border border-map-chrome-border px-1.5 text-caption ${
                  viewportOnly ? 'bg-status-accent-soft text-status-accent' : 'text-map-chrome-ink-muted'
                }`}
              >
                视口
              </button>
            )}
            <span className="shrink-0 text-caption tabular-nums text-map-chrome-ink-muted" data-testid="table-panel-count">
              {visibleOrder.length}
              {state.model.truncated ? `/${state.model.totalCount}+` : ''}
            </span>
          </div>
          {/* 表头（可排序） */}
          <div className="flex shrink-0 border-b border-map-chrome-border">
            {state.model.columns.map((col) => (
              <button
                key={col}
                type="button"
                onClick={() => setSortKey((prev) =>
                  prev?.column === col
                    ? (prev.dir === 1 ? { column: col, dir: -1 } : null)
                    : { column: col, dir: 1 })}
                title={`${col} 排序`}
                className="min-w-0 flex-1 truncate px-1.5 py-1 text-left text-caption font-semibold text-map-chrome-ink"
              >
                {col}
                {sortKey?.column === col ? (sortKey.dir === 1 ? ' ▲' : ' ▼') : ''}
              </button>
            ))}
          </div>
          {/* 虚拟化行区 */}
          <div
            ref={scrollRef}
            onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}
            className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
            data-testid="table-panel-scroll"
          >
            <div style={{ height: total * ROW_HEIGHT, position: 'relative' }}>
              <div style={{ transform: `translateY(${start * ROW_HEIGHT}px)` }}>
                {slice.map((rowIdx) => {
                  const row = state.model.rows[rowIdx];
                  const selected = selectedRowIds?.has(row.rowId) ?? false;
                  return (
                    <div
                      key={row.rowId}
                      role="row"
                      aria-selected={selected}
                      data-row-id={row.rowId}
                      data-testid="table-panel-row"
                      onClick={() => handleRowClick(row.rowId)}
                      style={{ height: ROW_HEIGHT }}
                      className={`flex w-full cursor-pointer items-center border-b border-map-chrome-border/40 ${
                        selected ? 'bg-status-accent-soft' : 'hover:bg-surface-hover/60'
                      }`}
                    >
                      {state.model.columns.map((col) => (
                        <span
                          key={col}
                          title={cellText(row.props[col])}
                          className="min-w-0 flex-1 truncate px-1.5 text-caption text-map-chrome-ink"
                        >
                          {cellText(row.props[col])}
                        </span>
                      ))}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
          {state.model.truncated && (
            <div className="shrink-0 text-caption text-map-chrome-ink-muted">
              仅显示前 {state.model.rows.length} 行（共 {state.model.totalCount}）
            </div>
          )}
        </div>
      )}
    </FloatingChrome>
  );
}

function TablePanelRenderer(component: MapSpecComponent, _ctx: RendererContext) {
  return <TablePanelView component={component} ctx={_ctx} />;
}

registerComponentRenderer('table_panel', TablePanelRenderer);
