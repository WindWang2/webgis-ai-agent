'use client';

import { useMemo } from 'react';
import { Map as MapIcon, PlayCircle } from 'lucide-react';
import type {
  CubeWindowResult,
  LabeledWindowResult,
  VectorScanResult,
  CubeBuildResult,
} from '@/lib/api/lakehouse';
import { STitle } from '@/components/shared/section-title';
import { bandStats, bandHistogram } from '@/lib/map-kit/raster-canvas';

export interface QueryResultsProps {
  windowResult?: CubeWindowResult | null;
  labeledResult?: LabeledWindowResult | null;
  scanResult?: VectorScanResult | null;
  buildResult?: CubeBuildResult | null;
  /** 把窗口帧作为 raster 源挂上地图（HeatmapRasterSource 通道）。 */
  onMountToMap: (frame: { grid: number[][]; bbox: [number, number, number, number] | null; title: string; nodata: number | null }) => void;
  /** 打开时序播放器（window 结果 steps>1 时可用）。 */
  onOpenTimeline: (result: CubeWindowResult) => void;
  /** 把矢量扫描结果挂上地图（≤5000 要素 GeoJSON 直挂通道）。 */
  onMountVector?: (fc: VectorScanResult, title: string) => void;
  /** labeled 请求的 bbox（结果上图复用 —— labeled 响应不含地理范围）。 */
  labeledRequestBbox?: [number, number, number, number] | null;
}

/** 标量或嵌套数组的 [y][x] 网格提取（window bands 是 [t][y][x]，首帧）。 */
function pickFrame(data: number[] | number[][] | number[][][]): number[][] {
  if (data.length === 0) return [];
  const first: unknown = data[0];
  if (Array.isArray(first)) {
    const row1: unknown = (first as unknown[])[0];
    if (Array.isArray(row1)) return data[0] as number[][]; // [t][y][x] → 首时间步
    return data as number[][]; // [y][x]
  }
  return [data as number[]]; // [x] 单行
}

/**
 * 查询结果面板：统计摘要（nodata 剔除）+ 首帧直方图（chart-core 主题）+
 * 上图 / 时序播放入口。构建类结果（revise/rs）只显示 durable 披露。
 */
export function QueryResults({
  windowResult,
  labeledResult,
  scanResult,
  buildResult,
  onMountToMap,
  onOpenTimeline,
  onMountVector,
  labeledRequestBbox,
}: QueryResultsProps) {
  // window / labeled 结果的统计与直方图（各取首变量/首帧）。
  const stats = useMemo(() => {
    if (windowResult) {
      const entry = Object.entries(windowResult.bands)[0] as [string, number[][][]] | undefined;
      if (!entry) return null;
      const [name, data] = entry;
      const frame = pickFrame(data);
      return { name, frame, nodata: windowResult.nodata, steps: windowResult.times.length };
    }
    if (labeledResult) {
      const entry = Object.entries(labeledResult.variables)[0];
      if (!entry) return null;
      const [name, data] = entry;
      const frame = pickFrame(data);
      return {
        name,
        frame,
        nodata:
          labeledResult.attrs.nodata_per_variable?.[name] ?? labeledResult.attrs.nodata,
        steps: (labeledResult.coords.time as Array<number | string>).length || 1,
      };
    }
    return null;
  }, [windowResult, labeledResult]);

  const histogram = useMemo(
    () => (stats ? bandHistogram(stats.frame, 12, stats.nodata) : []),
    [stats],
  );

  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-panel py-2" data-testid="lakehouse-query-results">
      {buildResult && (
        <div className="rounded-md border border-edge-subtle bg-surface-overlay px-panel py-2">
          <STitle title="构建回执" sub={buildResult.title} />
          <div className="space-y-1 text-caption">
            <div className="flex justify-between gap-2">
              <span className="text-ink-muted">ref</span>
              <span className="font-mono text-ink">{buildResult.ref}</span>
            </div>
            <div className="flex justify-between gap-2">
              <span className="text-ink-muted">时间步</span>
              <span className="text-ink">{buildResult.steps}</span>
            </div>
            <div className="flex justify-between gap-2">
              <span className="text-ink-muted">durable</span>
              <span className={buildResult.published ? 'text-status-success' : 'text-status-warning'}>
                {buildResult.published
                  ? `${buildResult.durable}${buildResult.deduped ? ' · 去重' : ''}`
                  : buildResult.reason ?? '未发布'}
              </span>
            </div>
          </div>
        </div>
      )}

      {scanResult && (
        <div className="rounded-md border border-edge-subtle bg-surface-overlay px-panel py-2">
          <STitle title="扫描结果" sub={`${scanResult.features.length} 条要素`} />
          <div className="space-y-1 text-caption">
            <div className="flex justify-between gap-2">
              <span className="text-ink-muted">row-group</span>
              <span className="text-ink">
                {scanResult.properties.row_groups_read} / {scanResult.properties.row_groups_total}
              </span>
            </div>
            <div className="flex justify-between gap-2">
              <span className="text-ink-muted">截断</span>
              <span className={scanResult.properties.truncated ? 'text-status-warning' : 'text-ink'}>
                {scanResult.properties.truncated ? '是（预算内截断）' : '否'}
              </span>
            </div>
          </div>
          {onMountVector && (
            <button
              type="button"
              onClick={() => onMountVector(scanResult, '矢量扫描结果')}
              className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-sm bg-status-accent px-2.5 py-1.5 text-caption font-medium text-ink-on-accent transition-opacity hover:opacity-85"
            >
              <MapIcon size={12} aria-hidden />
              加载至地图（GeoJSON 直挂）
            </button>
          )}
        </div>
      )}

      {stats && (
        <>
          <div className="rounded-md border border-edge-subtle bg-surface-overlay px-panel py-2">
            <STitle title={`统计 · ${stats.name}`} sub={stats.nodata != null ? `nodata=${stats.nodata} 已掩膜` : undefined} />
            <StatsGrid grid={stats.frame} nodata={stats.nodata} />
          </div>
          <div className="rounded-md border border-edge-subtle bg-surface-overlay px-panel py-2">
            <STitle title="首帧分布" />
            <Histogram data={histogram} />
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() =>
                onMountToMap({
                  grid: stats.frame,
                  bbox: windowResult ? firstFrameBbox(windowResult) : (labeledRequestBbox ?? null),
                  title: stats.name,
                  nodata: stats.nodata,
                })
              }
              data-testid="lakehouse-mount-frame"
              className="flex flex-1 items-center justify-center gap-1.5 rounded-sm bg-status-accent px-2.5 py-1.5 text-caption font-medium text-ink-on-accent transition-opacity hover:opacity-85"
            >
              <MapIcon size={12} aria-hidden />
              首帧上图
            </button>
            {windowResult && windowResult.times.length > 1 && (
              <button
                type="button"
                onClick={() => onOpenTimeline(windowResult)}
                data-testid="lakehouse-open-timeline"
                className="flex flex-1 items-center justify-center gap-1.5 rounded-sm bg-surface-sunken px-2.5 py-1.5 text-caption font-medium text-ink-secondary transition-colors hover:bg-surface-hover"
              >
                <PlayCircle size={12} aria-hidden />
                时序播放（{windowResult.times.length} 步）
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function firstFrameBbox(windowResult: CubeWindowResult): [number, number, number, number] | null {
  // GDAL 6 参仿射：x = a*col + c，y = e*row + f。y 行序自上而下 → maxy 在 f。
  if (!windowResult.transform) return null;
  const [a, , c, , e, f] = windowResult.transform;
  const firstBand = Object.values(windowResult.bands)[0];
  if (!firstBand) return null;
  const frame = pickFrame(firstBand);
  const rows = frame.length;
  const cols = rows > 0 ? frame[0].length : 0;
  if (rows === 0 || cols === 0) return null;
  const minx = c;
  const maxx = a * cols + c;
  const maxy = f;
  const miny = e * rows + f;
  return [Math.min(minx, maxx), Math.min(miny, maxy), Math.max(minx, maxx), Math.max(miny, maxy)];
}

function StatsGrid({ grid, nodata }: { grid: number[][]; nodata: number | null | undefined }) {
  const s = bandStats(grid, nodata);
  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-caption">
      <span className="text-ink-muted">最小值</span>
      <span className="text-right font-mono text-ink">{fmt(s.min)}</span>
      <span className="text-ink-muted">最大值</span>
      <span className="text-right font-mono text-ink">{fmt(s.max)}</span>
      <span className="text-ink-muted">均值</span>
      <span className="text-right font-mono text-ink">{fmt(s.mean)}</span>
      <span className="text-ink-muted">有效像元</span>
      <span className="text-right font-mono text-ink">
        {s.validCount}
        {s.maskedCount > 0 && <span className="text-ink-muted">（掩膜 {s.maskedCount}）</span>}
      </span>
    </div>
  );
}

function fmt(n: number): string {
  if (!Number.isFinite(n)) return '—';
  return Math.abs(n) >= 1000 ? n.toFixed(0) : n.toPrecision(4);
}

/** 轻量直方图（div 条形 —— 结果面板内不引 recharts，省一档包体与重渲染）。 */
function Histogram({ data }: { data: Array<{ name: string; value: number }> }) {
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <div className="flex h-16 items-end gap-px" role="img" aria-label="首帧数值分布直方图">
      {data.map((d, i) => (
        <div
          key={i}
          title={`${d.name}: ${d.value}`}
          className="min-w-[6px] flex-1 rounded-t-[1px] bg-status-accent/70"
          style={{ height: `${(d.value / max) * 100}%` }}
        />
      ))}
    </div>
  );
}
