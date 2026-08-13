/**
 * Pure normalizer: SSE `step_result` → shared `AnalysisResult`.
 *
 * Deterministic & side-effect-free (safe to memoize). All "live" concerns
 * (layer visibility, descriptor enrichment, job status) are reconciled later by
 * the store/hook layer; the normalizer only reflects what the event itself
 * truthfully carries.
 */
import { classifyFamily, extractMetrics, familyLabel, outputKindFor, toolLabel } from './families';
import { deriveSuggestedActions } from './suggested-actions';
import type {
  AnalysisResult,
  ArgsContext,
  BBox,
  InputRef,
  ResultParam,
  ResultStatus,
  ResultWarning,
  StepResultEvent,
} from './types';
import type { LegendSpec } from '@/lib/map-kit/types';

const REF_KEYS = new Set([
  'geojson',
  'layer',
  'layer_a',
  'layer_b',
  'network_layer',
  'source_points',
  'target_points',
  'raster_path',
  'raster_a',
  'raster_b',
  'h3_geojson',
]);

const FIELD_KEYS = new Set(['value_field', 'stat_field', 'timestamp_field', 'field']);

const PARAM_KEYS = new Set([
  'distance',
  'distances',
  'unit',
  'how',
  'method',
  'n_clusters',
  'eps',
  'eps1_spatial_meters',
  'eps2_temporal_seconds',
  'min_samples',
  'value_field',
  'stat_field',
  'stat_method',
  'timestamp_field',
  'resolution',
  'power',
  'expression',
  'constant',
  'scheme',
  'travel_time',
  'travel_time_min',
  'mode',
  'levels',
  'bandwidth',
  'cell_size',
  'radius',
  'render_type',
  'palette',
  'target_resolution',
  'target_crs',
  'resampling',
  'distance_band',
  'merge_rings',
  'dissolve',
  'date_from',
  'date_to',
  'nodata',
]);

/* ─── helpers ───────────────────────────────────────────────────────────────── */

export function parseBBox(bbox: unknown): BBox | undefined {
  if (Array.isArray(bbox) && bbox.length === 4 && bbox.every((n) => typeof n === 'number' && Number.isFinite(n))) {
    return bbox as BBox;
  }
  return undefined;
}

function extractLegendSpec(result: Record<string, any>): LegendSpec | undefined {
  const ls = result.legend_spec ?? result.data?.legend_spec;
  // Light validation: must look like one of the legend variants.
  if (ls && typeof ls === 'object' && typeof ls.type === 'string') {
    return ls as LegendSpec;
  }
  return undefined;
}

function extractWarnings(result: Record<string, any>, summary: string | undefined): ResultWarning[] {
  const warnings: ResultWarning[] = [];
  const seen = new Set<string>();
  const add = (w: ResultWarning) => {
    if (!seen.has(w.code)) {
      seen.add(w.code);
      warnings.push(w);
    }
  };

  // NOTE: `_streaming_note` is a transport artifact (the server slimmed feature
  // data out of the SSE event but the full layer IS auto-loaded on the map). It
  // is NOT a result-quality warning, so we intentionally do not surface it —
  // otherwise every feature-bearing result would be mislabeled "partial".
  if (result.correction_hint) {
    add({ level: 'warning', code: 'correction_hint', message: String(result.correction_hint) });
  }

  const text = summary ?? '';
  if (/grid too dense/i.test(text)) {
    add({ level: 'warning', code: 'grid_too_dense', message: '网格过密，可能影响渲染性能。' });
  }
  if (/unreachable|disconnected from the road network/i.test(text)) {
    add({ level: 'warning', code: 'unreachable_facilities', message: '存在无法到达的设施（与路网不连通）。' });
  }
  if (/no significant/i.test(text)) {
    add({ level: 'info', code: 'no_significance', message: '未发现显著的热点/冷点。' });
  }
  // Generic "Warning:" sentences in summary prose.
  const m = /Warning:\s*([^\n.]+)/i.exec(text);
  if (m && !seen.has('generic_warning')) {
    add({ level: 'warning', code: 'generic_warning', message: m[1].trim() });
  }

  return warnings;
}

function extractInputs(args: Record<string, any> | undefined): { inputs: InputRef[]; field?: string } {
  if (!args) return { inputs: [] };
  const inputs: InputRef[] = [];
  let field: string | undefined;
  for (const [k, v] of Object.entries(args)) {
    if (FIELD_KEYS.has(k) && typeof v === 'string' && v) {
      field = v;
      continue;
    }
    const isRefVal = typeof v === 'string' && v.startsWith('ref:');
    if (REF_KEYS.has(k) || isRefVal) {
      inputs.push({
        ref: isRefVal ? v : undefined,
        label: labelForInput(k, v),
        inferred: !isRefVal && !REF_KEYS.has(k),
      });
    }
  }
  return { inputs, field };
}

function labelForInput(key: string, value: any): string {
  const map: Record<string, string> = {
    geojson: '输入图层',
    layer: '输入图层',
    layer_a: '图层 A',
    layer_b: '图层 B',
    network_layer: '路网图层',
    source_points: '源点',
    target_points: '目标点',
    raster_path: '输入栅格',
    raster_a: '栅格 A',
    raster_b: '栅格 B',
    h3_geojson: 'H3 图层',
  };
  if (typeof value === 'string' && value.startsWith('ref:')) return map[key] ?? '引用图层';
  if (typeof value === 'string' && value) return value.length > 40 ? `${value.slice(0, 37)}…` : value;
  return map[key] ?? key;
}

function extractParams(args: Record<string, any> | undefined, field?: string): ResultParam[] {
  if (!args) return [];
  const params: ResultParam[] = [];
  for (const [k, v] of Object.entries(args)) {
    if (PARAM_KEYS.has(k) && v !== undefined && v !== null && v !== '') {
      params.push({ label: paramLabel(k), value: formatParam(v), source: k });
    }
  }
  if (field && !params.some((p) => p.source === 'value_field')) {
    params.push({ label: '字段', value: field, source: 'value_field' });
  }
  return params;
}

function paramLabel(key: string): string {
  const map: Record<string, string> = {
    distance: '缓冲距离',
    distances: '缓冲距离',
    unit: '单位',
    how: '叠加方式',
    method: '方法',
    n_clusters: '簇数',
    eps: 'eps',
    min_samples: '最小样本数',
    value_field: '值字段',
    stat_field: '统计字段',
    stat_method: '统计方法',
    timestamp_field: '时间字段',
    resolution: '分辨率',
    power: '幂参数',
    expression: '表达式',
    constant: '常量',
    scheme: '分类方案',
    travel_time: '通行时间',
    travel_time_min: '通行时间',
    mode: '出行方式',
    levels: '层级',
    bandwidth: '带宽',
    cell_size: '像元大小',
    radius: '半径',
    render_type: '渲染类型',
    palette: '配色',
    target_resolution: '目标分辨率',
    target_crs: '目标 CRS',
    resampling: '重采样',
    distance_band: '距离带',
    merge_rings: '合并环',
    dissolve: '融合',
    date_from: '起始日期',
    date_to: '结束日期',
    nodata: 'nodata',
  };
  return map[key] ?? key;
}

function formatParam(v: any): string | number | boolean {
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return v;
  if (Array.isArray(v)) return v.join(', ');
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function deriveStatus(
  result: Record<string, any>,
  warnings: ResultWarning[],
  approximate: boolean,
): ResultStatus {
  if (result.success === false || result.error || result.error_type) return 'failed';
  const hasStrong = warnings.some((w) => w.level === 'warning' || w.level === 'error');
  if (hasStrong) return approximate ? 'partial' : 'warning';
  if (approximate) return 'partial';
  return 'completed';
}

/* ─── entry ─────────────────────────────────────────────────────────────────── */

export function normalizeStepResult(step: StepResultEvent, argsCtx?: ArgsContext): AnalysisResult {
  const tool = step.tool ?? 'unknown';
  const family = classifyFamily(tool);
  const result = step.result ?? {};
  const geojsonRef = step.geojson_ref ?? null;
  const summary = typeof result.summary === 'string' ? result.summary : undefined;

  const legendSpec = extractLegendSpec(result);
  const warnings = extractWarnings(result, summary);
  // "Approximate/partial" means a genuine quality compromise (filtered geometry,
  // over-dense grid) — NOT merely the presence of any warning. A plain warning
  // (e.g. unreachable facilities) stays status `warning`, not `partial`.
  const approximate = warnings.some((w) => w.code === 'grid_too_dense');
  const status = deriveStatus(result, warnings, approximate);

  const { inputs, field } = argsCtx?.captured ? extractInputs(argsCtx.args) : { inputs: [], field: undefined };
  const parameters = argsCtx?.captured ? extractParams(argsCtx.args, field) : [];

  const kind = outputKindFor(family, result, geojsonRef);
  const hasBoundLayer = !!(geojsonRef || result.image);
  // Ref layers mount hidden (visible: !geojson_ref); image-only layers mount visible.
  const hasVisibleLayer = !!(result.image && !geojsonRef);

  const outputs = [
    {
      kind,
      ref: geojsonRef ?? undefined,
      hasLayer: hasBoundLayer,
      bbox: parseBBox(result.bbox),
      note: kind === 'raster' && result.result_path ? '栅格已写入存储' : undefined,
    },
  ];

  const suggestedActions = deriveSuggestedActions(family, outputs, hasVisibleLayer, hasBoundLayer);

  const metrics = extractMetrics(family, step);

  const provenance = [
    { kind: 'operation' as const, label: toolLabel(tool), detail: familyLabel(family) },
    ...inputs.map((inp) => ({ kind: 'input' as const, label: inp.label, detail: inp.ref })),
    { kind: 'output' as const, label: outputLabel(kind), detail: geojsonRef ?? undefined },
    ...(step.background_job_ids?.length ? [{ kind: 'run' as const, label: `后台作业 ×${step.background_job_ids.length}` }] : []),
  ];

  return {
    // Prefer the stable step_id. Avoid falling back to task_id when it equals the
    // session id (Pi-event path stamps task_id = session_id), which would collapse
    // every result in a turn onto one registry entry.
    id: step.step_id
      || (step.task_id && step.task_id !== step.session_id ? step.task_id : `${tool}-${Date.now()}`),
    tool,
    toolLabel: toolLabel(tool),
    family,
    status,
    summary,
    inputs: argsCtx?.captured ? inputs : [],
    parameters,
    metrics,
    warnings,
    outputs,
    bbox: parseBBox(result.bbox),
    legendSpec,
    layerBindings: [],
    provenance,
    suggestedActions,
    backgroundJobIds: step.background_job_ids ?? [],
    raw: result,
    approximate: approximate || undefined,
  };
}

function outputLabel(kind: string): string {
  const map: Record<string, string> = {
    vector: '矢量输出',
    raster: '栅格输出',
    statistic: '统计结果',
    table: '表格输出',
    image: '图像输出',
    none: '无图层输出',
  };
  return map[kind] ?? '输出';
}
