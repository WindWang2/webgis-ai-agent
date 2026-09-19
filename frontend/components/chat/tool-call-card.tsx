'use client';
import { useT } from '@/lib/i18n/useT';

import React, { useId, useState, useEffect, useCallback, useMemo } from 'react';
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
  Boxes,
} from 'lucide-react';
import { CartographyResultCard } from './cartography-result-card';
import { H3LisaResultCard } from './h3-lisa-result-card';
import { IsochroneResultCard } from './isochrone-result-card';
import { useHudStore } from '@/lib/store/useHudStore';
import { MODELOPS_RUN_TOOLS as MODELOPS_RUN_TOOL_NAMES } from '@/lib/api/modelops';

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

// 工具名词典：tool id → chat ns 键（chat.toolNames.*）；未知工具回退显示原始 id。
const TOOL_NAMES: Record<string, string> = {
  // Legacy tool names
  query_osm_poi: 'toolNames.queryOsmPoi',
  query_osm_roads: 'toolNames.queryOsmRoads',
  query_osm_buildings: 'toolNames.queryOsmBuildings',
  query_osm_boundary: 'toolNames.queryOsmBoundary',
  search_and_extract_poi: 'toolNames.searchAndExtractPoi',
  buffer_analysis: 'toolNames.bufferAnalysis',
  spatial_stats: 'toolNames.spatialStats',
  nearest_neighbor: 'toolNames.nearestNeighbor',
  heatmap_data: 'toolNames.heatmapData',
  overlay_analysis: 'toolNames.overlayAnalysis',
  attribute_filter: 'toolNames.attributeFilter',
  spatial_join: 'toolNames.spatialJoin',
  spatial_cluster: 'toolNames.spatialCluster',
  moran_i: 'toolNames.moranI',
  hotspot_analysis: 'toolNames.hotspotAnalysis',
  kde_surface: 'toolNames.kdeSurface',
  idw_interpolation: 'toolNames.idwInterpolation',
  kriging_interpolation: 'toolNames.krigingInterpolation',
  service_area: 'toolNames.serviceArea',
  od_matrix: 'toolNames.odMatrix',
  voronoi_polygons: 'toolNames.voronoiPolygons',
  convex_hull: 'toolNames.convexHull',
  multi_ring_buffer: 'toolNames.multiRingBuffer',
  create_thematic_map: 'toolNames.createThematicMap',
  apply_layer_style: 'toolNames.applyLayerStyle',
  generate_chart: 'toolNames.generateChart',
  geocode: 'toolNames.geocode',
  reverse_geocode: 'toolNames.reverseGeocode',
  search_poi: 'toolNames.searchPoi',
  geocode_cn: 'toolNames.geocodeCn',
  reverse_geocode_cn: 'toolNames.reverseGeocodeCn',
  plan_route: 'toolNames.planRoute',
  get_district: 'toolNames.getDistrict',
  fetch_sentinel: 'toolNames.fetchSentinel',
  compute_ndvi: 'toolNames.computeNdvi',
  fetch_dem: 'toolNames.fetchDem',
  compute_terrain: 'toolNames.computeTerrain',
  compute_vegetation_index: 'toolNames.computeVegetationIndex',
  generate_analysis_report: 'toolNames.generateAnalysisReport',
  alias_layer: 'toolNames.aliasLayer',
  inventory_layers: 'toolNames.inventoryLayers',
  switch_base_layer: 'toolNames.switchBaseLayer',
  set_layer_status: 'toolNames.setLayerStatus',
  update_layer_appearance: 'toolNames.updateLayerAppearance',
  list_uploaded_data: 'toolNames.listUploadedData',
  get_upload_info: 'toolNames.getUploadInfo',

  // Canonical webgis_* & STAC tool names
  webgis_buffer: 'toolNames.webgisBuffer',
  webgis_clip: 'toolNames.webgisClip',
  webgis_overlay: 'toolNames.webgisOverlay',
  webgis_spatial_join: 'toolNames.webgisSpatialJoin',
  webgis_cluster: 'toolNames.webgisCluster',
  webgis_stats: 'toolNames.webgisStats',
  webgis_nearest: 'toolNames.webgisNearest',
  webgis_voronoi: 'toolNames.webgisVoronoi',
  webgis_convex_hull: 'toolNames.webgisConvexHull',
  webgis_multi_ring: 'toolNames.webgisMultiRing',
  webgis_kde: 'toolNames.webgisKde',
  webgis_h3_lisa: 'toolNames.webgisH3Lisa',
  webgis_isochrones: 'toolNames.webgisIsochrones',
  stac_search: 'toolNames.stacSearch',
  h3_binning: 'toolNames.h3Binning',
  // V9（ADR-0145）：modelops 推理工具（chat 中实际出现卡片的工具族）
  modelops_run_inference: 'toolNames.modelopsRunInference',
  modelops_run_promptable: 'toolNames.modelopsRunPromptable',
};

function ToolName({ name }: { name: string }) {
  const t = useT('chat');
  const key = TOOL_NAMES[name];
  return <>{key ? t(key) : name}</>;
}

/* ── Copy snippet helper ── */
function CopyButton({ text, label }: { text: string; label?: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const copyLabel = label ?? t('common.copy');

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
      aria-label={copied ? t('common.copied') : copyLabel}
      title={copied ? t('chat.toolCall.copiedToClipboard') : t('chat.toolCall.copyToClipboard')}
    >
      {copied ? <Check size={10} className="text-status-success" /> : <Copy size={10} />}
      <span>{copied ? t('common.copied') : copyLabel}</span>
    </button>
  );
}

/* ── Single tool call card (minimal row when collapsed) ── */

// V9（ADR-0145）：modelops 推理 run → ModelOps 面板跳转（契约 ≤30 行）。
// 工具名词表与 use-modelops-runs 共用（lib/api/modelops.ts）。
const MODELOPS_RUN_TOOLS = new Set<string>(MODELOPS_RUN_TOOL_NAMES);

function ModelOpsRunLink({ runId }: { runId: string }) {
  const t = useT();
  const setActiveLeftTab = useHudStore((s: { setActiveLeftTab: (t: 'modelops') => void }) => s.setActiveLeftTab);
  return (
    <button
      type="button"
      onClick={() => setActiveLeftTab('modelops')}
      aria-label={t('chat.toolCall.viewRunAria', { runId })}
      className="inline-flex items-center gap-1 rounded-sm border border-edge-subtle bg-surface-raised px-1.5 py-0.5 text-micro font-medium text-status-accent transition-colors hover:bg-surface-hover"
    >
      <Boxes size={10} aria-hidden />
      ModelOps · run {runId.slice(0, 12)}
    </button>
  );
}

export function ToolCallRow({ call, expanded }: { call: ToolCallEntry; expanded: boolean }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const duration =
    call.startedAt && call.completedAt
      ? formatDuration(call.completedAt - call.startedAt)
      : null;
  const parsedArgs = parseArgs(call.arguments);
  const formattedJson = useMemo(() => formatJson(call.result), [call.result]);

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

      {MODELOPS_RUN_TOOLS.has(call.tool) &&
        typeof (call.result as { run_id?: unknown } | undefined)?.run_id === 'string' && (
          <div className="px-2.5 py-1.5">
            <ModelOpsRunLink runId={(call.result as { run_id: string }).run_id} />
          </div>
        )}

      {open && (
        <div
          id={panelId}
          role="region"
          aria-label={t('chat.toolCall.rowDetailsAria', { tool: call.tool })}
          className="border-t border-edge-subtle px-3 py-2 space-y-2 bg-surface-sunken/80"
        >
          {parsedArgs && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <p className="text-micro font-semibold text-ink-muted uppercase tracking-wider">
                  {t('chat.toolCall.args')}
                </p>
                <CopyButton text={formatJson(parsedArgs)} label={t('chat.toolCall.copyArgs')} />
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
                  {t('chat.toolCall.result')}
                </p>
                <CopyButton text={formattedJson} label={t('chat.toolCall.copyResult')} />
              </div>
              <pre className="p-2 rounded-md bg-surface-raised border border-edge-subtle text-caption leading-relaxed text-ink-secondary font-mono overflow-x-auto max-h-[160px] overflow-y-auto">
                {formattedJson.slice(0, 1500)}
                {formattedJson.length > 1500 ? '\n...' : ''}
              </pre>
            </div>
          )}
          {call.error && (
            <div>
              <p className="text-micro font-semibold text-status-critical uppercase tracking-wider mb-1">
                {t('chat.toolCall.error')}
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
  const t = useT();
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
    ? failedCount > 0
      ? t('chat.toolCall.summaryDoneWithFailed', { completed: completedCount, failed: failedCount })
      : t('chat.toolCall.summaryDone', { completed: completedCount })
    : t('chat.toolCall.summaryRunning', { count: runningCount });

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
          {expanded ? t('chat.toolCall.chainTitle') : statusText}
        </span>
        <span className="flex-1" />
        {allDone && !expanded && (
          failedCount > 0 ? (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-micro bg-status-warning-soft text-status-warning font-medium">
              <AlertTriangle size={11} aria-label={t('chat.toolCall.partialFailAria')} />
              <span>{failedCount} {t('chat.toolCall.failed')}</span>
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-micro bg-status-success-soft text-status-success font-medium">
              <CheckCircle2 size={11} />
              <span>{t('chat.toolCall.done')}</span>
            </span>
          )
        )}
        {!allDone && (
          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-micro bg-status-info-soft text-status-info font-medium">
            <Loader2 size={11} className="animate-spin text-status-info" />
            <span>{t('chat.toolCall.running')}</span>
          </span>
        )}
      </button>

      {/* Expanded: individual tool calls */}
      {expanded && (
        <div
          id={chainListId}
          role="region"
          aria-label={t('chat.toolCall.detailsAria')}
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
