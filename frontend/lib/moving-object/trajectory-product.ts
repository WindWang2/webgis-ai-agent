/**
 * GOAL-20261127-02 moving-object trajectory product — Studio panel types,
 * the ONLY network boundary, and pure projections.
 *
 * 纪律（与 flow-product.ts 同款）：
 * - 本文件是唯一网络边界（inspect/build/apply/query），组件测试只 mock
 *   本模块，不模拟领域逻辑。
 * - 投影函数全部纯函数：committed MapSpec → 候选点表源 / 已应用产品 /
 *   字段绑定猜测，便于 TDD。
 * - 预览永不写 MapSpec；apply 走既有 commitMapSpecMutation（CAS，
 *   intent = apply_trajectory_product，与 Pi 工具同一后端服务）。
 */

import { ApiError, apiFetch } from '@/lib/api/transport';
import type { MapSpec, MapSpecLayerTrajectory } from '@/lib/mapspec-compiler/types';

/* ── 类型（与后端 TrajectoryProductConfig / 响应契约对齐） ───────────────── */

export interface TrajectoryProductConfig {
  dataset: string;
  entity_id_field: string;
  time_field: string;
  source_row_field?: string | null;
  assumed_timezone: string;
  duplicate_time_policy: 'keep_all' | 'keep_first' | 'keep_last';
  sort_policy: 'time_asc' | 'keep_input';
  max_gap_s: number;
  max_speed_kmh: number;
  max_jump_m: number;
  min_points_per_segment: number;
  detect_dwells: boolean;
  dwell_radius_m: number;
  dwell_min_dwell_s: number;
  dwell_min_points: number;
  regions_ref?: string | null;
  region_id_field: string;
  boundary_rule: 'closed' | 'open';
  detect_cross: boolean;
  region_dwell_min_s: number;
  detect_region_dwell: boolean;
  time_start?: string | null;
  time_end?: string | null;
  entity_filter?: string[] | null;
  palette?: string[] | null;
  width_min_px: number;
  width_max_px: number;
  show_direction: boolean;
  product_id?: string | null;
}

export function defaultTrajectoryConfig(
  overrides: Partial<TrajectoryProductConfig> = {},
): TrajectoryProductConfig {
  return {
    dataset: '',
    entity_id_field: 'entity_id',
    time_field: 'timestamp',
    source_row_field: null,
    assumed_timezone: 'UTC',
    duplicate_time_policy: 'keep_all',
    sort_policy: 'time_asc',
    max_gap_s: 3600,
    max_speed_kmh: 300,
    max_jump_m: 50000,
    min_points_per_segment: 2,
    detect_dwells: true,
    dwell_radius_m: 50,
    dwell_min_dwell_s: 120,
    dwell_min_points: 3,
    regions_ref: null,
    region_id_field: 'region_id',
    boundary_rule: 'closed',
    detect_cross: true,
    region_dwell_min_s: 60,
    detect_region_dwell: true,
    time_start: null,
    time_end: null,
    entity_filter: null,
    palette: null,
    width_min_px: 1.5,
    width_max_px: 7,
    show_direction: true,
    product_id: null,
    ...overrides,
  };
}

export interface TrajectoryIngestHealth {
  input_rows?: number;
  accepted?: number;
  missing_entity_id?: number;
  unparseable_time?: number;
  missing_or_degenerate_geometry?: number;
  duplicate_time_pairs?: number;
  out_of_order_rows?: number;
  naive_time_rows?: number;
  entity_count?: number;
  time_min?: string | null;
  time_max?: string | null;
  empty_after_ingest?: boolean;
  notes?: string[];
}

export interface TrajectoryInspectResponse {
  success: boolean;
  operation: string;
  source_ref: string;
  source_revision: number;
  feature_count?: number;
  fields: Record<string, string>;
  sample: Record<string, unknown>[];
  sample_truncated?: boolean;
  ingest_health?: TrajectoryIngestHealth;
  entity_preview?: unknown[];
}

export interface TrajectoryStats {
  observations?: number;
  input_rows?: number;
  entities?: number;
  segments?: number;
  segments_zero_extent?: number;
  breaks_total?: number;
  break_reasons?: Record<string, number>;
  dropped_runs?: number;
  degenerate_intervals?: number;
  dwells?: number;
  dwell_near_miss_runs?: number;
  total_path_length_m?: number;
}

export interface TrajectoryEventSummary {
  n_entities?: number;
  n_regions?: number;
  n_events?: number;
  by_type?: Record<string, number>;
  unknown_intervals?: number;
  inferred_events?: number;
  notes?: string[];
}

export interface TrajectoryRegionSummaryRow {
  region_id: string;
  n_entities: number;
  n_enter: number;
  n_exit: number;
  n_cross: number;
  n_dwell_spans: number;
  total_dwell_s: number;
  total_inside_fixes: number;
  entities_zero_hit: number;
  unknown_intervals: number;
}

export interface TrajectoryQuality {
  ingest?: TrajectoryIngestHealth;
  notes?: string[];
  break_reason_counts?: Record<string, number>;
  overall_sampling?: {
    n_intervals?: number;
    median_gap_s?: number;
    p90_gap_s?: number;
    max_gap_s?: number;
    min_gap_s?: number;
    zero_gap_intervals?: number;
    degenerate_intervals?: number;
  } | null;
  top_entities?: Array<Record<string, unknown>>;
}

export interface TrajectoryMapSpecFragment {
  sources: Record<string, { type: string; url?: string; ref_id?: string; ref?: string }>;
  layers: Array<Record<string, unknown>>;
}

export interface TrajectoryPreviewResponse {
  success: boolean;
  operation: string;
  product_ref: string;
  family_id: string;
  source_ref: string;
  source_revision: number;
  source_fingerprint?: string;
  product_fingerprint?: string;
  cache_hit?: string;
  line_ref?: string;
  dwell_ref?: string;
  event_ref?: string;
  stats?: TrajectoryStats;
  quality?: TrajectoryQuality;
  coverage?: Record<string, unknown>;
  assumptions?: string[];
  warnings?: string[];
  events_summary?: TrajectoryEventSummary | null;
  region_summaries?: TrajectoryRegionSummaryRow[];
  can_apply?: boolean;
  blocking_reasons?: string[];
  mapspec_fragment?: TrajectoryMapSpecFragment;
  mapspec_revision?: number;
  scientific_evidence?: Record<string, unknown>;
}

export interface TrajectoryPage<T = Record<string, unknown>> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}

export interface TrajectoryQueryResponse {
  success: boolean;
  kind: 'product' | 'entity' | 'dwell' | 'event';
  entity_id?: string;
  product?: Record<string, unknown>;
  page?: TrajectoryPage;
}

/* ── API 边界（组件测试 mock 的唯一网络面） ──────────────────────────────── */

interface TrajectoryRequestOptions {
  ownerToken?: string | null;
  signal?: AbortSignal;
}

export async function inspectTrajectorySource(
  sessionId: string,
  req: { dataset: string; entity_id_field: string; time_field: string; assumed_timezone: string },
  opts: TrajectoryRequestOptions = {},
): Promise<TrajectoryInspectResponse> {
  return apiFetch<TrajectoryInspectResponse>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/trajectory/inspect`,
    {
      method: 'POST',
      body: req,
      ownerToken: opts.ownerToken,
      signal: opts.signal,
      timeoutMs: 60_000,
      label: 'Trajectory inspect',
    },
  );
}

export async function buildTrajectoryProduct(
  sessionId: string,
  config: TrajectoryProductConfig,
  opts: TrajectoryRequestOptions = {},
): Promise<TrajectoryPreviewResponse> {
  return apiFetch<TrajectoryPreviewResponse>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/trajectory/build`,
    {
      method: 'POST',
      body: config,
      ownerToken: opts.ownerToken,
      signal: opts.signal,
      timeoutMs: 120_000,
      label: 'Trajectory build',
    },
  );
}

export async function queryTrajectoryProduct(
  sessionId: string,
  req: {
    product_ref: string;
    kind: 'product' | 'entity' | 'dwell' | 'event';
    entity_id?: string;
    offset?: number;
    limit?: number;
  },
  opts: TrajectoryRequestOptions = {},
): Promise<TrajectoryQueryResponse> {
  return apiFetch<TrajectoryQueryResponse>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/trajectory/query`,
    {
      method: 'POST',
      body: {
        product_ref: req.product_ref,
        kind: req.kind,
        entity_id: req.entity_id ?? '',
        offset: req.offset ?? 0,
        limit: req.limit ?? 50,
      },
      ownerToken: opts.ownerToken,
      signal: opts.signal,
      timeoutMs: 60_000,
      label: 'Trajectory query',
    },
  );
}

/* ── 纯投影 ─────────────────────────────────────────────────────────────── */

export interface TrajectorySourceCandidate {
  /** committed MapSpec sources 的键（面板展示名）。 */
  key: string;
  /** 可 inspect/build 的 ref。 */
  ref: string;
}

/**
 * 候选点表：committed MapSpec 中 type=geojson 且带 ref 的源；
 * 已被轨迹产品消费的派生产物源（trajectory 契约块所在图层族）排除。
 */
export function listTrajectorySourceCandidates(
  spec: MapSpec | null | undefined,
): TrajectorySourceCandidate[] {
  if (!spec?.sources) return [];
  const consumed = new Set(
    (spec.layers ?? [])
      .filter((l) => (l as { trajectory?: unknown }).trajectory)
      .map((l) => l.source as string),
  );
  const out: TrajectorySourceCandidate[] = [];
  for (const [key, src] of Object.entries(spec.sources)) {
    if (!src || src.type !== 'geojson') continue;
    if (consumed.has(key)) continue;
    const candidate =
      (src as { url?: unknown }).url ??
      (src as { ref_id?: unknown }).ref_id ??
      (src as { ref?: unknown }).ref;
    const ref = typeof candidate === 'string' && candidate.startsWith('ref:') ? candidate : null;
    if (!ref) continue;
    out.push({ key, ref });
  }
  return out;
}

export interface AppliedTrajectory {
  layerId: string;
  trajectory: MapSpecLayerTrajectory;
}

/** 刷新恢复：committed MapSpec 中第一个带 layer.trajectory 的层。 */
export function findAppliedTrajectory(spec: MapSpec | null | undefined): AppliedTrajectory | null {
  for (const layer of spec?.layers ?? []) {
    const trajectory = (layer as { trajectory?: MapSpecLayerTrajectory }).trajectory;
    if (trajectory) return { layerId: layer.id, trajectory };
  }
  return null;
}

/**
 * 由已应用 layer.trajectory 恢复**表达层**可编辑项（样式宽度/方向/色板/
 * 数据源/时区）。分析阈值（分段/驻留参数）不在 trajectory 契约块内——
 * 后端不持久化 config 全量（R1 审查：读取不存在的键会把阈值静默重置为
 * 缺省）；用户改阈值需重新 build，不假装从图层恢复。
 */
export function trajectoryConfigFromApplied(
  trajectory: MapSpecLayerTrajectory,
): TrajectoryProductConfig {
  const encoding = (trajectory.encoding ?? {}) as Record<string, unknown>;
  const num = (v: unknown): number | undefined =>
    typeof v === 'number' && Number.isFinite(v) ? v : undefined;
  return defaultTrajectoryConfig({
    dataset: trajectory.source_ref,
    assumed_timezone:
      ((trajectory.coverage ?? {}) as Record<string, unknown>).assumed_timezone as string
      ?? 'UTC',
    width_min_px: num(encoding['width_min_px']) ?? 1.5,
    width_max_px: num(encoding['width_max_px']) ?? 7,
    show_direction: encoding['show_direction'] !== false,
    palette: Array.isArray(encoding['palette'])
      ? (encoding['palette'] as string[])
      : null,
    product_id: null,
  });
}

/**
 * 由 inspect 真实返回的字段表猜测实体/时间绑定（用户确认后才用于 build）。
 * 精确名（大小写不敏感）优先，其次子串包含；无命中给空串（UI 显示未识别，
 * 不伪造）。
 */
export function guessTrajectoryFieldBindings(
  fields: Record<string, string>,
): { entity_id_field: string; time_field: string } {
  const names = Object.keys(fields);
  const pick = (exact: string[], contains: string[]): string => {
    const lower = new Map(names.map((n) => [n.toLowerCase(), n]));
    for (const candidate of exact) {
      const hit = lower.get(candidate);
      if (hit) return hit;
    }
    const hit = names.find((n) => {
      const ln = n.toLowerCase();
      return contains.some((c) => ln.includes(c));
    });
    return hit ?? '';
  };
  return {
    entity_id_field: pick(
      ['entity_id', 'vehicle_id', 'obj_id', 'objectid', 'track_id', 'id'],
      ['entity', 'vehicle', 'track_id', 'obj'],
    ),
    time_field: pick(
      ['timestamp', 'time', 'datetime', 'fix_time'],
      ['time', 'timestamp', 'datetime'],
    ),
  };
}

/** 统计视图：有界、面向 UI 的摘要（数值全部来自真实产物）。 */
export interface TrajectoryStatsView {
  entities: number;
  observations: number;
  segments: number;
  dwells: number;
  breaks: number;
  totalKm: number | null;
  warningCount: number;
}

export function trajectoryStatsView(stats: TrajectoryStats | undefined): TrajectoryStatsView {
  const s = stats ?? {};
  return {
    entities: s.entities ?? 0,
    observations: s.observations ?? 0,
    segments: s.segments ?? 0,
    dwells: s.dwells ?? 0,
    breaks: s.breaks_total ?? 0,
    totalKm:
      typeof s.total_path_length_m === 'number'
        ? Math.round((s.total_path_length_m / 1000) * 10) / 10
        : null,
    warningCount: 0,
  };
}

export { ApiError };
