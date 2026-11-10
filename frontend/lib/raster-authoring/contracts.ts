/** G20 工作台契约（与后端 raster_expression.v1 / 生成 TS 类型逐字段对齐）。 */
import type {
  RasterExpressionSpec,
  RasterStretchBounds,
} from '@/lib/mapspec-compiler/types.generated';

export type { RasterExpressionSpec, RasterStretchBounds };

export const RASTER_EXPRESSION_SCHEMA_VERSION = 'raster_expression.v1';
export const RASTER_SESSION_PLACEHOLDER = '__SESSION_ID__';
export const RASTER_RESAMPLINGS = ['nearest', 'bilinear', 'cubic'] as const;
export const RASTER_CLASSIFICATIONS = ['continuous', 'classified'] as const;
export const RASTER_COLORMAPS = [
  'gray', 'viridis', 'magma', 'inferno', 'plasma', 'cividis',
  'Viridis', 'RdYlGn', 'RdYlGn_r', 'Blues', 'Greens', 'YlOrRd', 'turbo', 'coolwarm',
] as const;

export interface RasterBandInfo {
  index: number;
  label: string;
  description: string;
  dtype: string;
  unit: string | null;
  scale: number;
  offset: number;
  scale_declared: boolean;
  offset_declared: boolean;
  role: string | null;
  vmin: number | null;
  vmax: number | null;
}

export interface RasterHistogram {
  band: number;
  buckets: number;
  counts: number[];
  min: number | null;
  max: number | null;
  valid_samples: number;
  total_samples: number;
  approximate: boolean;
  method: string;
  all_nodata: boolean;
}

export interface RasterDescriptor {
  success: boolean;
  ref_id: string;
  data_revision: string;
  content_fingerprint: string | null;
  crs: string | null;
  crs_present: boolean;
  bounds_epsg4326: [number, number, number, number] | null;
  band_count: number;
  bands: RasterBandInfo[];
  statistics: {
    method: string;
    sample_count: number;
    approximate: boolean;
    side: number;
    per_band: [number, number][];
  } | null;
  histograms: RasterHistogram[];
  capabilities: {
    numeric_bands: boolean;
    tiles: boolean;
    probe: boolean;
    classified_semantics_requires_declaration: boolean;
  };
  defaults: RasterExpressionDraft;
  disclosures: string[];
}

/** 面板草稿；提交时由 buildExpressionRequest 转成后端请求体。 */
export interface RasterExpressionDraft {
  schema_version: typeof RASTER_EXPRESSION_SCHEMA_VERSION;
  mode: 'single' | 'rgb';
  bands: number[];
  autoStretch: boolean;
  stretchMin: number;
  stretchMax: number;
  perChannel: [number, number][];
  colormap: string;
  opacity: number;
  resampling: (typeof RASTER_RESAMPLINGS)[number];
  classification: (typeof RASTER_CLASSIFICATIONS)[number];
}

export interface RasterErrorPayload {
  success: false;
  code: string;
  message: string;
  correction_hint?: string;
  field?: string;
  details?: Record<string, unknown>;
}

export interface RasterLegend {
  type: 'continuous' | 'raster_rgb';
  style_id: string;
  data_revision: string;
  min?: number;
  max?: number;
  palette?: string;
  palette_colors?: string[];
  band?: number;
  band_label?: string;
  unit?: string | null;
  unit_status?: string;
  channels?: Array<{
    channel: string; band: number; band_label: string;
    min: number; max: number; unit: string | null; unit_status: string;
  }>;
  constant?: boolean;
  notes?: string[];
  stretch_provenance: {
    source: string; method: string; sample_count: number | null; approximate: boolean;
  };
  invalid_semantics: string;
  out_of_range_semantics: string;
}

export interface RasterReceipt {
  success: boolean;
  committed: boolean;
  state: 'committed' | 'preview';
  layer_id: string;
  source_id: string;
  ref_id: string;
  mapspec_revision: number | null;
  data_revision: string;
  expression_version: string;
  style_id: string;
  statistics_provenance: RasterLegend['stretch_provenance'];
  expression: RasterExpressionSpec;
  legend: RasterLegend;
  expression_statement: Record<string, unknown>;
  mutations?: number;
  /** 提交后回灌的权威 MapSpec（与既有 user-mutation 通道同款）。 */
  mapspec?: Record<string, unknown> | null;
}

export interface RasterProbeValue {
  band: number;
  label: string;
  role: string | null;
  raw: number | null;
  scaled: number | null;
  scale: number;
  offset: number;
  scale_applied: boolean;
  unit: string | null;
  unit_status: string;
  valid: boolean;
  invalid_reason: string | null;
}

export interface RasterProbeResult {
  success: boolean;
  data_revision: string;
  method: string;
  interpolated: boolean;
  classifications?: string;
  points: Array<{
    id: string;
    status: string;
    row: number | null;
    col: number | null;
    requested_coord: [number, number];
    requested_crs: string;
    source_coord: [number, number];
    source_crs: string | null;
    values: RasterProbeValue[];
    valid: boolean;
  }>;
  truncated?: boolean;
}

export interface RasterExportResult {
  success: boolean;
  filename: string;
  download_url: string;
  media_type: string;
  bytes: number;
  frozen_revision: number;
  frozen_style_ids: string[];
  omitted_layers: string[];
  embedded: boolean;
  expression_summary: Record<string, unknown>;
  limits: string[];
}

export function defaultDraft(descriptor: RasterDescriptor): RasterExpressionDraft {
  const d = descriptor.defaults;
  const first = descriptor.bands[0];
  return {
    ...d,
    bands: [...d.bands],
    stretchMin: first?.vmin ?? 0,
    stretchMax: first?.vmax ?? 1,
    perChannel: d.bands.map((b) => {
      const info = descriptor.bands[b - 1];
      return [info?.vmin ?? 0, info?.vmax ?? 1] as [number, number];
    }),
  };
}

export function draftFromCommitted(
  descriptor: RasterDescriptor,
  committed: RasterExpressionSpec,
): RasterExpressionDraft {
  const base = defaultDraft(descriptor);
  const perChannel = committed.bands.map((_, i) => {
    const pair = committed.stretch.per_channel?.[i];
    return [pair?.[0] ?? committed.stretch.min, pair?.[1] ?? committed.stretch.max] as [number, number];
  });
  return {
    ...base,
    mode: committed.mode,
    bands: [...committed.bands],
    autoStretch: committed.stretch.source !== 'manual',
    stretchMin: committed.stretch.min,
    stretchMax: committed.stretch.max,
    perChannel,
    colormap: committed.colormap ?? base.colormap,
    opacity: committed.opacity ?? 1,
    resampling: committed.resampling ?? base.resampling,
    classification: committed.classification ?? base.classification,
  };
}

/** 草稿 → 后端 expression 请求体（显式值，不让服务端猜）。 */
export function buildExpressionRequest(draft: RasterExpressionDraft): Record<string, unknown> {
  const stretch: Record<string, unknown> | 'auto' = draft.autoStretch
    ? 'auto'
    : {
        min: draft.stretchMin,
        max: draft.stretchMax,
        ...(draft.mode === 'rgb' ? { per_channel: draft.perChannel } : {}),
      };
  return {
    schema_version: RASTER_EXPRESSION_SCHEMA_VERSION,
    mode: draft.mode,
    bands: draft.bands,
    stretch,
    ...(draft.mode === 'single' ? { colormap: draft.colormap } : {}),
    opacity: draft.opacity,
    resampling: draft.resampling,
    classification: draft.classification,
  };
}

/** 客户端便宜校验（与后端具体 code 对齐，错误可聚焦到字段）。 */
export function draftFieldErrors(
  draft: RasterExpressionDraft,
  bandCount: number,
): Record<string, string> {
  const out: Record<string, string> = {};
  if (draft.bands.length !== (draft.mode === 'single' ? 1 : 3)) {
    out.bands = 'BAND_COUNT_UNSUPPORTED';
  }
  if (draft.bands.some((b) => !Number.isInteger(b) || b < 1 || b > bandCount)) {
    out.bands = 'BAND_OUT_OF_RANGE';
  }
  if (!draft.autoStretch) {
    if (!Number.isFinite(draft.stretchMin) || !Number.isFinite(draft.stretchMax)) {
      out.stretch = 'STRETCH_NON_FINITE';
    } else if (draft.stretchMax < draft.stretchMin) {
      out.stretch = 'STRETCH_REVERSED';
    }
    for (const pair of draft.perChannel) {
      if (!Number.isFinite(pair[0]) || !Number.isFinite(pair[1]) || pair[1] < pair[0]) {
        out['stretch.per_channel'] = 'STRETCH_REVERSED';
      }
    }
  }
  if (draft.classification === 'classified' && draft.resampling !== 'nearest') {
    out.resampling = 'CLASSIFIED_INTERPOLATION';
  }
  if (!RASTER_COLORMAPS.includes(draft.colormap as (typeof RASTER_COLORMAPS)[number])) {
    out.colormap = 'UNKNOWN_COLORMAP';
  }
  return out;
}

export function isDraftDirty(
  draft: RasterExpressionDraft,
  committed: RasterExpressionSpec | null,
): boolean {
  if (!committed) return true;
  const req = buildExpressionRequest(draft);
  return (
    req.mode !== committed.mode ||
    JSON.stringify(req.bands) !== JSON.stringify(committed.bands) ||
    (committed.colormap ?? null) !== (req.colormap ?? null) ||
    (committed.resampling ?? 'bilinear') !== req.resampling ||
    (committed.classification ?? 'continuous') !== req.classification ||
    (committed.opacity ?? 1) !== req.opacity ||
    (!draft.autoStretch &&
      (draft.stretchMin !== committed.stretch.min || draft.stretchMax !== committed.stretch.max))
  );
}

/** 波段没有可靠角色元数据时只称第 N 波段，不猜红/近红外/地表温度。 */
export function bandLabel(band: RasterBandInfo | undefined, index: number, locale: string): string {
  const n = band?.index ?? index;
  if (band?.role) return `${locale === 'zh' ? '第' : 'Band '}${n}${locale === 'zh' ? ` 波段（${band.role}）` : ` (${band.role})`}`;
  return locale === 'zh' ? `第 ${n} 波段` : `Band ${n}`;
}
