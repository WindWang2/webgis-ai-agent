'use client';

/**
 * DatasetPreview — 双模式数据预览（ADR-0143 P2）。
 *
 * 数据来源（前端聚合，勘察报告 §2.9）：数据集 `source_ref` 指向 data-fabric
 * catalog 项时，经 `GET /data-fabric/catalog/{id}/preview`（有界样例，需登录）
 * 取回 features。
 * - 表格模式：复用 explorer 的 TabularDataGrid（虚拟化，支持超大数据集）。
 * - 地图模式：预览要素的 SVG 足迹图（真实几何投影进 bbox，非示意）。
 *   MVT 管线不在此重做——layer 型数据集提供「在主地图打开」（P7 交叉导航）。
 * 后端没有项目数据集的独立预览端点；source_ref 缺失或 catalog 预览失败时
 * 如实降级为提示，不伪造数据（勘察报告 §2.10 协调点）。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Table as TableIcon, Map as MapIcon, ExternalLink } from 'lucide-react';

import { fetchCatalogItemPreview, type CatalogPreviewResponse } from '@/lib/api/project-assets';
import { LoadingState } from '@/components/shared/loading-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { TabularDataGrid } from '@/components/shared/tabular-data-grid';
import { isAbortError, parseApiErrorDetail } from '@/lib/workflow/recovery';

export type PreviewMode = 'table' | 'map';

export interface DatasetPreviewProps {
  datasetId: string;
  sourceRef?: string | null;
  /** layer 型数据集跳主地图（复用 MVT 管线）；缺省隐藏按钮。 */
  onOpenInMap?: (datasetId: string) => void;
}

const PREVIEW_LIMIT = 100;

interface FootprintGeometry {
  points: Array<[number, number]>;
  rings: Array<Array<[number, number]>>;
}

function collectCoord(coord: unknown, out: Array<[number, number]>): void {
  if (!Array.isArray(coord) || coord.length === 0) return;
  // 坐标对 [x, y]：首元素为数字即叶子；否则视为嵌套环/多多边形继续下钻。
  // 不能用 length<2 判容器——单环 Polygon 的 coordinates 外层长度就是 1。
  if (typeof coord[0] === 'number' && typeof coord[1] === 'number') {
    out.push([coord[0] as number, coord[1] as number]);
    return;
  }
  for (const c of coord) collectCoord(c, out);
}

/** Extract drawable geometry from preview features — real coordinates only. */
export function extractFootprint(features: Array<Record<string, unknown>>): FootprintGeometry {
  const points: Array<[number, number]> = [];
  const rings: Array<Array<[number, number]>> = [];
  for (const f of features) {
    const geometry = f.geometry as Record<string, unknown> | null | undefined;
    const coords: Array<[number, number]> = [];
    collectCoord(geometry?.coordinates, coords);
    if (coords.length === 0) continue;
    const type = String(geometry?.type ?? '');
    if (type === 'Polygon' || type === 'MultiPolygon') {
      rings.push(coords);
    } else {
      points.push(...coords);
    }
  }
  return { points, rings };
}

/** SVG footprint plot — equirectangular bbox projection of preview geometry. */
export function FootprintMap({ geometry, className }: { geometry: FootprintGeometry; className?: string }) {
  const all: Array<[number, number]> = [
    ...geometry.points,
    ...geometry.rings.flat(),
  ];
  if (all.length === 0) {
    return <p className="px-2 py-3 text-micro text-ink-muted">预览要素不含几何坐标，无法绘制足迹。</p>;
  }
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const [x, y] of all) {
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  const w = maxX - minX || 1;
  const h = maxY - minY || 1;
  const pad = 0.06;
  const sx = (x: number) => ((x - minX) / w) * (1 - 2 * pad) * 100 + pad * 100;
  const sy = (y: number) => (1 - (((y - minY) / h) * (1 - 2 * pad) + pad)) * 100;
  return (
    <svg
      role="img"
      aria-label={`预览要素足迹图（${all.length} 个坐标点，范围 ${minX.toFixed(3)},${minY.toFixed(3)} 至 ${maxX.toFixed(3)},${maxY.toFixed(3)}）`}
      viewBox="0 0 100 100"
      preserveAspectRatio="xMidYMid meet"
      className={className}
    >
      {geometry.rings.map((ring, i) => (
        <polyline
          key={`ring-${i}`}
          points={ring.map(([x, y]) => `${sx(x).toFixed(2)},${sy(y).toFixed(2)}`).join(' ')}
          fill="none"
          stroke="currentColor"
          strokeWidth={0.6}
          className="text-status-accent"
        />
      ))}
      {geometry.points.map(([x, y], i) => (
        <circle key={`pt-${i}`} cx={sx(x).toFixed(2)} cy={sy(y).toFixed(2)} r={0.9} className="fill-status-accent" />
      ))}
    </svg>
  );
}

export function DatasetPreview({ datasetId, sourceRef, onOpenInMap }: DatasetPreviewProps) {
  const [mode, setMode] = useState<PreviewMode>('table');
  const [preview, setPreview] = useState<CatalogPreviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    if (!sourceRef) return;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);
    setError(null);
    try {
      const result = await fetchCatalogItemPreview(sourceRef, { limit: PREVIEW_LIMIT, signal: ac.signal });
      if (!ac.signal.aborted) setPreview(result);
    } catch (err: unknown) {
      if (ac.signal.aborted || isAbortError(err)) return;
      setError(parseApiErrorDetail(err, '预览加载失败'));
    } finally {
      if (!ac.signal.aborted) setLoading(false);
    }
  }, [sourceRef]);

  useEffect(() => {
    setPreview(null);
    setError(null);
    void load();
    return () => abortRef.current?.abort();
  }, [load]);

  if (!sourceRef) {
    return (
      <div className="space-y-1.5 rounded-md border border-edge-subtle bg-surface-sunken px-2 py-2">
        <p className="text-micro text-ink-muted">
          该数据集没有可预览的来源引用（source_ref 为空）。后端暂无项目数据集预览端点。
        </p>
        {onOpenInMap && (
          <button
            type="button"
            onClick={() => onOpenInMap(datasetId)}
            className="flex items-center gap-1 text-micro text-status-accent hover:underline"
          >
            <ExternalLink size={12} aria-hidden /> 在主地图打开
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-1.5 rounded-md border border-edge-subtle bg-surface-sunken px-2 py-2">
      <div className="flex items-center justify-between">
        <div role="tablist" aria-label="预览模式" className="flex gap-1">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'table'}
            onClick={() => setMode('table')}
            className={`flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-micro ${
              mode === 'table' ? 'bg-status-accent text-ink-on-accent' : 'text-ink-secondary hover:bg-surface-raised'
            }`}
          >
            <TableIcon size={11} aria-hidden /> 表格
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'map'}
            onClick={() => setMode('map')}
            className={`flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-micro ${
              mode === 'map' ? 'bg-status-accent text-ink-on-accent' : 'text-ink-secondary hover:bg-surface-raised'
            }`}
          >
            <MapIcon size={11} aria-hidden /> 地图
          </button>
        </div>
        {onOpenInMap && (
          <button
            type="button"
            onClick={() => onOpenInMap(datasetId)}
            className="flex items-center gap-1 text-micro text-ink-secondary hover:text-ink"
            title="在主地图打开（MVT 管线）"
          >
            <ExternalLink size={11} aria-hidden /> 主地图
          </button>
        )}
      </div>

      {loading && <LoadingState label="加载预览…" />}
      {error && <InlineNotice variant="error">{error}</InlineNotice>}

      {!loading && !error && preview && mode === 'table' && (
        <div className="max-h-64 overflow-auto">
          <TabularDataGrid
            data={preview.features}
            totalCount={preview.total_count}
            defaultPageSize={5}
            pageSizeOptions={[5, 10, 25]}
            enableRowCopy={false}
            emptyTitle="来源无样例数据"
          />
        </div>
      )}

      {!loading && !error && preview && mode === 'map' && (
        <FootprintMap geometry={extractFootprint(preview.features)} className="h-40 w-full text-ink" />
      )}

      {!loading && !error && preview && (
        <p className="text-micro text-ink-muted">
          样例 {preview.features.length} / 共 {preview.total_count} 行（来源目录 {preview.dataset_id}）
        </p>
      )}
    </div>
  );
}
