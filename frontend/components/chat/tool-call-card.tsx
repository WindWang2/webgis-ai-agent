'use client';

import { useState } from 'react';
import { ChevronRight, ChevronDown, CheckCircle2, AlertCircle, Loader2, Clock, Wrench } from 'lucide-react';

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
};

function ToolName({ name }: { name: string }) {
  return <>{TOOL_NAMES[name] || name}</>;
}

/* ── Single tool call card (minimal row when collapsed) ── */

function ToolCallRow({ call, expanded }: { call: ToolCallEntry; expanded: boolean }) {
  const [open, setOpen] = useState(false);
  const duration =
    call.startedAt && call.completedAt
      ? formatDuration(call.completedAt - call.startedAt)
      : null;
  const parsedArgs = parseArgs(call.arguments);

  const statusIcon =
    call.status === 'running' ? (
      <Loader2 size={11} className="animate-spin text-blue-500" />
    ) : call.status === 'completed' ? (
      <CheckCircle2 size={11} className="text-green-500" />
    ) : (
      <AlertCircle size={11} className="text-red-500" />
    );

  return (
    <div className={`rounded-md border text-[11px] overflow-hidden ${expanded ? 'border-slate-200/80 bg-white/50' : 'border-transparent'}`}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-1.5 px-2 py-1 text-left hover:bg-slate-50/60 transition-colors"
      >
        <ChevronRight
          size={10}
          className={`shrink-0 text-slate-400 transition-transform ${open ? 'rotate-90' : ''}`}
        />
        {statusIcon}
        <span className="font-mono text-slate-600">
          <ToolName name={call.tool} />
        </span>
        {call.hasGeojson && (
          <span className="px-1 py-0 rounded text-[8px] bg-green-50 text-green-600 font-medium">
            GeoJSON
          </span>
        )}
        <span className="flex-1" />
        {duration && (
          <span className="flex items-center gap-0.5 text-[9px] text-slate-400">
            <Clock size={9} />
            {duration}
          </span>
        )}
      </button>

      {open && (
        <div className="border-t border-slate-100 px-2.5 py-1.5 space-y-1.5 bg-slate-50/30">
          {parsedArgs && (
            <div>
              <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider mb-0.5">参数</p>
              <pre className="p-1.5 rounded bg-white/80 border border-slate-100 text-[10px] leading-relaxed text-slate-600 font-mono overflow-x-auto max-h-[100px] overflow-y-auto">
                {Object.entries(parsedArgs)
                  .map(([k, v]) => {
                    const val = typeof v === 'string' ? `"${v}"` : JSON.stringify(v);
                    return `${k}: ${val.length > 80 ? val.slice(0, 80) + '...' : val}`;
                  })
                  .join('\n')}
              </pre>
            </div>
          )}
          {call.result && (
            <div>
              <p className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider mb-0.5">结果</p>
              <pre className="p-1.5 rounded bg-white/80 border border-slate-100 text-[10px] leading-relaxed text-slate-600 font-mono overflow-x-auto max-h-[150px] overflow-y-auto">
                {formatJson(call.result).slice(0, 1500)}
                {formatJson(call.result).length > 1500 ? '\n...' : ''}
              </pre>
            </div>
          )}
          {call.error && (
            <div>
              <p className="text-[9px] font-semibold text-red-400 uppercase tracking-wider mb-0.5">错误</p>
              <pre className="p-1.5 rounded bg-red-50/50 border border-red-100 text-[10px] text-red-600 font-mono">{call.error}</pre>
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

  const runningCount = calls.filter((c) => c.status === 'running').length;
  const completedCount = calls.filter((c) => c.status === 'completed').length;
  const failedCount = calls.filter((c) => c.status === 'failed').length;

  // Summary line when collapsed
  const allDone = runningCount === 0;
  const statusText = allDone
    ? `${completedCount} 个工具调用完成${failedCount > 0 ? `，${failedCount} 个失败` : ''}`
    : `正在执行 ${runningCount} 个工具...`;

  return (
    <div className="my-1.5 rounded-lg border border-slate-200/70 bg-white/40 overflow-hidden">
      {/* Chain header — click to expand */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-1.5 px-2.5 py-1.5 text-left hover:bg-slate-50/60 transition-colors text-[11px]"
      >
        {expanded ? (
          <ChevronDown size={12} className="shrink-0 text-slate-400" />
        ) : (
          <ChevronRight size={12} className="shrink-0 text-slate-400" />
        )}
        <Wrench size={11} className="text-slate-400" />
        <span className="text-slate-500">
          {expanded ? '工具调用链' : statusText}
        </span>
        <span className="flex-1" />
        {allDone && !expanded && (
          <CheckCircle2 size={11} className="text-green-500" />
        )}
        {!allDone && (
          <Loader2 size={11} className="animate-spin text-blue-500" />
        )}
      </button>

      {/* Expanded: individual tool calls */}
      {expanded && (
        <div className="border-t border-slate-100 px-2 py-1 space-y-0.5 bg-slate-50/20">
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
