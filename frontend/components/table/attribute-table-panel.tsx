'use client';

/**
 * AttributeTablePanel — V7 图层属性表（停靠底部区静态面板）。
 *
 * 性能契约（审计 §8-C 的正面回答）：
 * - DOM 虚拟化：use-virtual-rows 行窗口（自研，与图层树同源），10 万行
 *   恒定渲染 ~可见窗 + overscan；不用 <table>（行窗口用 translateY）；
 * - 过滤只字符串化标量值（此前全值 JSON.stringify 是 10 万行的主线程杀手）；
 * - 排序/过滤 memo 化，一次派生数组，不逐渲染重建。
 *
 * 联动契约：行选择经 selection-store 发布（source 'table' + id_field），
 * 订阅同一 store 高亮来自 map/chart 的选择 —— map↔table 双向单真相。
 * 无内联要素的 MVT/ref 大层：显式披露「瓦片通道无内联属性」，不静默空表。
 */
import React, { useMemo, useState, useSyncExternalStore } from 'react';
import { X } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useVirtualRows } from '@/lib/hooks/use-virtual-rows';
import {
  getSelection,
  getSelectionGeneration,
  publishSelection,
  subscribeSelection,
} from '@/lib/selection/selection-store';
import { resolveFeatureId, resolveFeatureIdField } from '@/lib/layers/feature-id';

const ROW_HEIGHT = 28;
const MAX_COLUMNS = 40;
/** 过滤扫描上限：超过此行数只前缀扫描（10 万行的全量 substring 每键入仍是 O(n)，可接受；
 *  列值字符串化有界 —— 不做逐值 JSON.stringify）。 */
const MAX_CELL_CHARS = 120;

interface Column {
  key: string;
}

function cellText(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'object') return Array.isArray(value) ? '[…]' : '{…}';
  const s = String(value);
  return s.length > MAX_CELL_CHARS ? `${s.slice(0, MAX_CELL_CHARS)}…` : s;
}

export function AttributeTablePanel() {
  const layers = useHudStore((s) => s.layers);
  const selectedLayerIds = useHudStore((s) => s.selectedLayerIds);
  const attributeTableLayerId = useHudStore((s) => s.attributeTableLayerId);
  const setAttributeTableLayerId = useHudStore((s) => s.setAttributeTableLayerId);

  // 目标层：显式绑定 > 图层树多选首行 > 首个有内联要素的层。目标消失自动回退。
  const layer = useMemo(() => {
    const byId = (id: string | null | undefined) => layers.find((l) => l.id === id);
    return (
      byId(attributeTableLayerId)
      || byId(selectedLayerIds[0])
      || layers.find((l) => getInlineFeatureCount(l) > 0)
      || null
    );
  }, [layers, attributeTableLayerId, selectedLayerIds]);

  const features = useMemo<Record<string, unknown>[]>(() => {
    const src = layer?.source;
    if (!src || typeof src !== 'object' || !('features' in src)) return [];
    const arr = (src as { features?: unknown[] }).features;
    return Array.isArray(arr) ? (arr as Record<string, unknown>[]) : [];
  }, [layer]);

  const columns = useMemo<Column[]>(() => {
    const keys = new Set<string>();
    // 采样推导 schema（与 FR-06 修复同思路：前 50 行足够稳定）
    for (const f of features.slice(0, 50)) {
      const props = (f.properties ?? f) as Record<string, unknown>;
      if (props && typeof props === 'object') {
        for (const k of Object.keys(props)) {
          if (k !== 'meta' && k !== 'fid') keys.add(k);
        }
      }
      if (keys.size >= MAX_COLUMNS) break;
    }
    return Array.from(keys).slice(0, MAX_COLUMNS).map((key) => ({ key }));
  }, [features]);

  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(null);
  const [search, setSearch] = useState('');

  const rows = useMemo(() => {
    type Row = { fid: string | null; topId: unknown; cells: string[]; raw: Record<string, unknown> };
    const q = search.trim().toLowerCase();
    const out: Row[] = [];
    for (let i = 0; i < features.length; i += 1) {
      const f = features[i];
      const raw = (f.properties ?? f) as Record<string, unknown>;
      const topId = (f as { id?: unknown }).id;
      const fid = resolveFeatureId(raw, topId);
      const cells = columns.map((c) => cellText(raw[c.key]));
      if (q) {
        const hay = `${fid ?? ''}\u0000${cells.join('\u0000')}`.toLowerCase();
        if (!hay.includes(q)) continue;
      }
      out.push({ fid: fid != null ? String(fid) : null, topId, cells, raw });
    }
    if (sort) {
      const idx = columns.findIndex((c) => c.key === sort.key);
      if (idx >= 0) {
        const dir = sort.dir === 'asc' ? 1 : -1;
        out.sort((a, b) => {
          const av = a.cells[idx];
          const bv = b.cells[idx];
          const an = Number(av);
          const bn = Number(bv);
          if (Number.isFinite(an) && Number.isFinite(bn)) return (an - bn) * dir;
          return av.localeCompare(bv) * dir;
        });
      }
    }
    return out;
  }, [features, columns, search, sort]);

  // map ↔ table 单真相：订阅 selection-store；表格高亮 = current 选中 ids。
  const selectionGeneration = useSyncExternalStore(subscribeSelection, getSelectionGeneration);
  const selectedIds = useMemo(() => {
    void selectionGeneration;
    const sel = getSelection();
    if (!sel || (layer && sel.layer_id !== layer.id)) return new Set<string>();
    return new Set(sel.selected_ids);
  }, [selectionGeneration, layer]);

  const handleRowSelect = (fid: string | null, topLevelId: unknown, raw: Record<string, unknown>) => {
    if (!layer || fid == null) return;
    const idField = resolveFeatureIdField(raw, topLevelId);
    if (!idField) return;
    publishSelection('select', {
      source: 'table',
      layer_id: layer.id,
      feature_id: fid,
      selected_ids: [fid],
      id_field: idField === '$id' ? 'id' : idField,
    });
  };

  const clearSelection = () => {
    publishSelection('clear_selection', { source: 'table', layer_id: layer?.id ?? '' });
  };

  const virtual = useVirtualRows(rows.length, ROW_HEIGHT);

  if (!layer) {
    return (
      <div className="flex h-full items-center justify-center text-micro text-ink-muted">
        暂无含内联属性的图层 —— 分析结果落地后可在此查看属性表
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-1" data-testid="attribute-table-panel">
      {/* 工具行：图层选择 + 搜索 + 计数 + 清除高亮 */}
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <select
          aria-label="选择属性表图层"
          value={layer.id}
          onChange={(e) => setAttributeTableLayerId(e.target.value)}
          className="h-control-sm max-w-56 rounded-xs border border-edge-subtle bg-surface-panel px-1 text-micro text-ink"
        >
          {layers.map((l) => (
            <option key={l.id} value={l.id}>{l.name || l.id}</option>
          ))}
        </select>
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="过滤行（属性子串）"
          aria-label="过滤属性行"
          className="h-control-sm w-40 rounded-xs border border-edge-subtle bg-surface-sunken px-1.5 text-micro text-ink focus:outline-none"
        />
        <span className="text-micro tabular-nums text-ink-muted" data-testid="attribute-row-count">
          {rows.length.toLocaleString()} 行
        </span>
        {selectedIds.size > 0 && (
          <button
            type="button"
            className="flex items-center gap-0.5 rounded-xs px-1.5 py-0.5 text-micro text-ink-secondary hover:bg-surface-hover hover:text-ink"
            onClick={clearSelection}
          >
            <X aria-hidden size={11} />
            清除高亮（{selectedIds.size}）
          </button>
        )}
      </div>

      {/* 虚拟化网格（role=grid；固定行高窗口渲染） */}
      <div
        role="grid"
        aria-label={`${layer.name || layer.id} 属性表`}
        className="min-h-0 flex-1 overflow-y-auto rounded-xs border border-edge-subtle"
      >
        <div
          role="row"
          className="sticky top-0 z-10 flex items-center gap-0 border-b border-edge-subtle bg-surface-subtle px-2 text-micro font-medium text-ink-secondary"
          style={{ height: ROW_HEIGHT }}
        >
          <span className="w-14 shrink-0 tabular-nums" aria-hidden>#</span>
          {columns.map((c) => (
            <button
              key={c.key}
              type="button"
              role="columnheader"
              aria-sort={sort?.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
              className="min-w-0 flex-1 truncate text-left hover:text-ink"
              title={`按 ${c.key} 排序`}
              onClick={() =>
                setSort((prev) =>
                  prev?.key === c.key
                    ? (prev.dir === 'asc' ? { key: c.key, dir: 'desc' } : null)
                    : { key: c.key, dir: 'asc' },
                )
              }
            >
              {c.key}
              {sort?.key === c.key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}
            </button>
          ))}
        </div>
        <div style={{ height: virtual.totalHeight, position: 'relative' }}>
          <div style={{ transform: `translateY(${virtual.offsetY}px)` }}>
            {rows.slice(virtual.start, virtual.end).map((row, i) => {
              const rowIndex = virtual.start + i;
              const highlighted = row.fid != null && selectedIds.has(row.fid);
              return (
                <div
                  key={row.fid ?? `row-${rowIndex}`}
                  role="row"
                  tabIndex={0}
                  aria-selected={highlighted || undefined}
                  data-testid={`attribute-row-${rowIndex}`}
                  onClick={() => handleRowSelect(row.fid, row.topId, row.raw)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      handleRowSelect(row.fid, row.topId, row.raw);
                    }
                  }}
                  className={`flex items-center gap-0 px-2 text-micro tabular-nums text-ink-secondary ${
                    highlighted ? 'bg-status-accent-soft text-ink' : 'odd:bg-surface-subtle/40 hover:bg-surface-hover'
                  }`}
                  style={{ height: ROW_HEIGHT }}
                >
                  <span className="w-14 shrink-0 text-ink-disabled">{rowIndex + 1}</span>
                  {row.cells.map((cell, ci) => (
                    <span key={columns[ci].key} role="gridcell" className="min-w-0 flex-1 truncate" title={cell}>
                      {cell}
                    </span>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      </div>
      {features.length === 0 && (
        <div className="shrink-0 pb-1 text-micro text-ink-muted" role="note">
          该图层走矢量瓦片（MVT）通道，无内联属性 —— 属性表仅在数据以 GeoJSON
          内联挂载时可用。
        </div>
      )}
    </div>
  );
}

function getInlineFeatureCount(layer: { source?: unknown; _descriptor?: { feature_count?: number } }): number {
  const src = layer.source;
  if (src && typeof src === 'object' && 'features' in (src as Record<string, unknown>)) {
    const arr = (src as { features?: unknown[] }).features;
    return Array.isArray(arr) ? arr.length : 0;
  }
  return 0;
}

export default AttributeTablePanel;
