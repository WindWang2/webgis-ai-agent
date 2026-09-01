'use client';

import React, { useId, useState, useEffect, useCallback } from 'react';
import {
  ChevronRight,
  ChevronDown,
  CheckCircle2,
  AlertCircle,
  AlertTriangle,
  Loader2,
  Clock,
  Wrench,
  Copy,
  Check,
} from 'lucide-react';
import { CartographyResultCard } from './cartography-result-card';
import { H3LisaResultCard } from './h3-lisa-result-card';
import { IsochroneResultCard } from './isochrone-result-card';
import { useHudStore } from '@/lib/store/useHudStore';

export interface ToolCallEntry {
  id: string;
  tool: string;
  arguments?: string;
  result?: any;
  status: 'running' | 'completed' | 'failed';
  hasGeojson?: boolean;
  error?: string;
  startedAt?: number;
  completedAt?: number;
  layerId?: string;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatJson(obj: unknown): string {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(obj);
  }
}

function parseArgs(argsStr?: string): Record<string, unknown> | null {
  if (!argsStr) return null;
  try {
    return JSON.parse(argsStr);
  } catch {
    return null;
  }
}

const TOOL_NAMES: Record<string, string> = {
  // Legacy tool names
  query_osm_poi: 'POI 查询',
  query_osm_roads: '路网查询',
  query_osm_buildings: '建筑查询',
  query_osm_boundary: '边界查询',
  search_and_extract_poi: 'POI 搜索',
  buffer_analysis: '缓冲区分析',
  spatial_stats: '空间统计',
  nearest_neighbor: '最近邻分析',
  heatmap_data: '热力图生成',
  overlay_analysis: '叠加分析',
  attribute_filter: '属性筛选',
  spatial_join: '空间连接',
  spatial_cluster: '空间聚类',
  moran_i: '空间自相关',
  hotspot_analysis: '热点分析',
  kde_surface: '核密度估计',
  idw_interpolation: 'IDW 插值',
  kriging_interpolation: '克里金插值',
  service_area: '服务区分析',
  od_matrix: '距离矩阵',
  voronoi_polygons: 'Voronoi 划分',
  convex_hull: '凸包分析',
  multi_ring_buffer: '多环缓冲区',
  create_thematic_map: '专题地图',
  apply_layer_style: '样式应用',
  generate_chart: '统计图表',
  geocode: '地理编码',
  reverse_geocode: '逆地理编码',
  search_poi: 'POI 搜索',
  geocode_cn: '中文编码',
  reverse_geocode_cn: '中文逆编码',
  plan_route: '路径规划',
  get_district: '行政区划',
  fetch_sentinel: 'Sentinel 影像',
  compute_ndvi: 'NDVI 计算',
  fetch_dem: 'DEM 获取',
  compute_terrain: '地形分析',
  compute_vegetation_index: '植被指数',
  generate_analysis_report: '分析报告',
  alias_layer: '图层别名',
  inventory_layers: '图层清单',
  switch_base_layer: '切换底图',
  set_layer_status: '图层状态',
  update_layer_appearance: '图层样式',
  list_uploaded_data: '上传数据',
  get_upload_info: '数据详情',

  // Canonical webgis_* & STAC tool names
  webgis_buffer: '缓冲区分析',
  webgis_clip: '矢量裁剪',
  webgis_overlay: '叠加分析',
  webgis_spatial_join: '空间连接',
  webgis_cluster: '空间聚类',
  webgis_stats: '空间统计',
  webgis_nearest: '最近邻分析',
  webgis_voronoi: 'Voronoi 划分',
  webgis_convex_hull: '凸包分析',
  webgis_multi_ring: '多环缓冲区',
  webgis_kde: '核密度估计',
  webgis_h3_lisa: 'H3 LISA 聚类',
  webgis_isochrones: '等时圈分析',
  stac_search: 'STAC 遥感检索',
  h3_binning: 'H3 网格化',
};

function ToolName({ name }: { name: string }) {
  return <>{TOOL_NAMES[name] || name}</>;
}

/* ── Copy snippet helper ── */
function CopyButton({ text, label = '复制' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  }, [text]);

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-micro font-medium transition-all ${
        copied
          ? 'text-status-success bg-status-success-soft'
          : 'text-ink-muted hover:text-ink hover:bg-surface-hover'
      }`}
      aria-label={copied ? '已复制' : label}
      title={copied ? '已复制到剪贴板' : '复制到剪贴板'}
    >
      {copied ? <Check size={10} className="text-status-success" /> : <Copy size={10} />}
      <span>{copied ? '已复制' : label}</span>
    </button>
  );
}

/* ── Single tool call card (minimal row when collapsed) ── */

export function ToolCallRow({ call, expanded }: { call: ToolCallEntry; expanded: boolean }) {
  const [open, setOpen] = useState(false);
  const duration =
    call.startedAt && call.completedAt
      ? formatDuration(call.completedAt - call.startedAt)
      : null;
  const parsedArgs = parseArgs(call.arguments);

  const CARTO_TOOLS = new Set([
    'create_thematic_map',
    'h3_binning',
    'kde_contours',
    'heatmap_data',
    'webgis_layer_upsert',
  ]);
  const LISA_TOOLS = new Set(['h3_lisa', 'webgis_h3_lisa']);
  const ISOCHRONE_TOOLS = new Set(['isochrones', 'webgis_isochrones', 'service_area']);

  const isCarto = CARTO_TOOLS.has(call.tool);
  const isLisa = LISA_TOOLS.has(call.tool);
  const isIsochrone = ISOCHRONE_TOOLS.has(call.tool);

  const focusLayer = useHudStore((s: { focusLayer: (id: string) => void }) => s.focusLayer);
  const ownLayerId = (call as ToolCallEntry & { layerId?: string }).layerId ?? '';
  const layers = useHudStore((s: { layers: Array<{ id: string; legend_spec?: unknown }> }) => s.layers);
  // Prefer the per-call binding (step_result geojson_ref); fall back to legacy
  // guessing only for stale history entries that predate the binding.
  const fallbackCartoId = layers.find((l) => l.legend_spec)?.id ?? '';
  const fallbackActiveId = layers[0]?.id ?? '';
  const cartoLayerId = ownLayerId || fallbackCartoId;
  const activeLayerId = ownLayerId || fallbackActiveId;
  const hasOwnLayer = !!ownLayerId;

  const statusIcon =
    call.status === 'running' ? (
      <Loader2 size={12} className="animate-spin text-status-info shrink-0" />
    ) : call.status === 'completed' ? (
      <CheckCircle2 size={12} className="text-status-success shrink-0" />
    ) : (
      <AlertCircle size={12} className="text-status-critical shrink-0" />
    );

  const panelId = `tool-row-panel-${call.id}`;

  return (
    <div
      className={`rounded-md border text-body overflow-hidden transition-all ${
        expanded ? 'border-edge-subtle bg-surface-raised shadow-xs' : 'border-transparent'
      }`}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={panelId}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-surface-hover transition-colors cursor-pointer"
      >
        <ChevronRight
          size={11}
          className={`shrink-0 text-ink-disabled transition-transform duration-200 ${
            open ? 'rotate-90 text-ink-secondary' : ''
          }`}
          aria-hidden
        />
        {statusIcon}
        <span className="font-mono text-ink-secondary font-medium text-caption">
          <ToolName name={call.tool} />
        </span>
        {call.hasGeojson && (
          <span className="px-1.5 py-0.2 rounded text-micro bg-status-accent-soft text-status-accent font-medium border border-status-accent-border/40">
            GeoJSON
          </span>
        )}
        <span className="flex-1" />
        {duration && (
          <span className="flex items-center gap-1 text-caption text-ink-disabled font-mono">
            <Clock size={10} aria-hidden />
            {duration}
          </span>
        )}
      </button>

      {isCarto && call.result && (
        <CartographyResultCard
          result={call.result}
          layerId={cartoLayerId}
          onFocus={hasOwnLayer ? (id) => id && focusLayer(id) : undefined}
        />
      )}

      {isLisa && call.result && (
        <H3LisaResultCard
          result={call.result}
          layerId={activeLayerId}
          onFocus={hasOwnLayer ? (id) => id && focusLayer(id) : undefined}
        />
      )}

      {isIsochrone && call.result && (
        <IsochroneResultCard
          result={call.result}
          layerId={activeLayerId}
          onFocus={hasOwnLayer ? (id) => id && focusLayer(id) : undefined}
        />
      )}

      {open && (
        <div
          id={panelId}
          role="region"
          aria-label={`${call.tool} 详细信息`}
          className="border-t border-edge-subtle px-3 py-2 space-y-2 bg-surface-sunken/80"
        >
          {parsedArgs && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <p className="text-micro font-semibold text-ink-muted uppercase tracking-wider">
                  参数
                </p>
                <CopyButton text={formatJson(parsedArgs)} label="复制参数" />
              </div>
              <pre className="p-2 rounded-md bg-surface-raised border border-edge-subtle text-caption leading-relaxed text-ink-secondary font-mono overflow-x-auto max-h-[120px] overflow-y-auto">
                {Object.entries(parsedArgs)
                  .map(([k, v]) => {
                    const val = typeof v === 'string' ? `"${v}"` : JSON.stringify(v);
                    return `${k}: ${val.length > 100 ? val.slice(0, 100) + '...' : val}`;
                  })
                  .join('\n')}
              </pre>
            </div>
          )}
          {call.result && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <p className="text-micro font-semibold text-ink-muted uppercase tracking-wider">
                  结果
                </p>
                <CopyButton text={formatJson(call.result)} label="复制结果" />
              </div>
              <pre className="p-2 rounded-md bg-surface-raised border border-edge-subtle text-caption leading-relaxed text-ink-secondary font-mono overflow-x-auto max-h-[160px] overflow-y-auto">
                {formatJson(call.result).slice(0, 1500)}
                {formatJson(call.result).length > 1500 ? '\n...' : ''}
              </pre>
            </div>
          )}
          {call.error && (
            <div>
              <p className="text-micro font-semibold text-status-critical uppercase tracking-wider mb-1">
                错误
              </p>
              <pre className="p-2 rounded-md bg-status-critical-soft border border-status-critical-border text-caption text-status-critical font-mono whitespace-pre-wrap">
                {call.error}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Collapsible tool chain wrapper ── */

export function ToolCallChain({ calls }: { calls: ToolCallEntry[] }) {
  const [expanded, setExpanded] = useState(false);
  // #1000：失败行抵达时链默认展开——错误详情此前埋在「展开链→展开单行」
  // 两级折叠之下，用户默认看不到失败原因难以调整重试。用户手动开合过
  // 之后不再强推（尊重显式操作，含恢复的历史会话：挂载即已带 failed 态）。
  const [userToggled, setUserToggled] = useState(false);

  const runningCount = calls.filter((c) => c.status === 'running').length;
  const completedCount = calls.filter((c) => c.status === 'completed').length;
  const failedCount = calls.filter((c) => c.status === 'failed').length;

  useEffect(() => {
    if (failedCount > 0 && !userToggled) setExpanded(true);
  }, [failedCount, userToggled]);

  const toggleExpanded = () => {
    setUserToggled(true);
    setExpanded((v) => !v);
  };

  // Summary line when collapsed
  const allDone = runningCount === 0;
  const statusText = allDone
    ? `${completedCount} 个工具调用完成${failedCount > 0 ? `，${failedCount} 个失败` : ''}`
    : `正在执行 ${runningCount} 个工具...`;

  // Fix: a constant id collides when several tool-call chains are on screen,
  // which breaks the aria-controls relationship; useId() keeps it unique per instance.
  const chainListId = useId();

  return (
    <div
      className="my-2 rounded-md border border-edge-subtle bg-surface-raised shadow-raised overflow-hidden transition-all"
      data-testid="tool-call-chain"
    >
      {/* Chain header — click to expand */}
      <button
        type="button"
        onClick={toggleExpanded}
        aria-expanded={expanded}
        aria-controls={chainListId}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-surface-hover transition-colors text-body cursor-pointer select-none"
      >
        {expanded ? (
          <ChevronDown size={13} className="shrink-0 text-ink-secondary" aria-hidden />
        ) : (
          <ChevronRight size={13} className="shrink-0 text-ink-disabled" aria-hidden />
        )}
        <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-status-info-soft text-status-info">
          <Wrench size={12} aria-hidden />
        </div>
        <span className="font-medium text-caption text-ink-secondary">
          {expanded ? '工具调用链' : statusText}
        </span>
        <span className="flex-1" />
        {allDone && !expanded && (
          failedCount > 0 ? (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-micro bg-status-warning-soft text-status-warning font-medium">
              <AlertTriangle size={11} aria-label="部分或全部调用失败" />
              <span>{failedCount} 失败</span>
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-micro bg-status-success-soft text-status-success font-medium">
              <CheckCircle2 size={11} />
              <span>已完成</span>
            </span>
          )
        )}
        {!allDone && (
          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-micro bg-status-info-soft text-status-info font-medium">
            <Loader2 size={11} className="animate-spin text-status-info" />
            <span>执行中</span>
          </span>
        )}
      </button>

      {/* Expanded: individual tool calls */}
      {expanded && (
        <div
          id={chainListId}
          role="region"
          aria-label="工具调用链详情"
          className="border-t border-edge-subtle px-2.5 py-2 space-y-1 bg-surface-sunken/40"
        >
          {calls.map((tc) => (
            <ToolCallRow key={tc.id} call={tc} expanded={expanded} />
          ))}
        </div>
      )}
    </div>
  );
}

/* Keep old export for backward compat */
export function ToolCallCard({ call }: { call: ToolCallEntry }) {
  return <ToolCallRow call={call} expanded={true} />;
}

export default ToolCallCard;
