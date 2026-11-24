/**
 * Data Fusion Authoring API —— 多源实体融合工作台 client。
 *
 * 与后端 /api/v1/data-fusion-authoring 端点族一一对应
 * （app/api/routes/data_fusion_authoring.py；词表以
 * app/services/data_fusion_authoring/contracts.py 为准）。诚实纪律：
 * total/has_more 是全量口径（当前页见 cursor）；决定/计划更新一律带
 * expected_revision（CAS）；错误统一携带后端 typed error_code +
 * correction_hint。
 */

import { apiFetch } from './transport';

const BASE = '/api/v1/data-fusion-authoring';

// ── 封闭词表（contracts.py 投影） ──────────────────────────────────

export type EntityKind = 'point_poi' | 'area_link' | 'line_link';
export type CommitPolicy = 'require_resolved' | 'allow_unresolved';
export type GeometryAdoption = 'primary_source' | 'newest_temporal' | 'manual';
export type InvalidGeometryPolicy = 'exclude_count' | 'block';
export type EvidenceKind = 'external_id' | 'name' | 'address' | 'category' | 'distance';
export type FusionPairStatus =
  | 'matched'
  | 'needs_review'
  | 'ambiguous'
  | 'unmatched'
  | 'rejected';
export type FusionRunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
export type DecisionAction = 'accept' | 'reject';
export type AdjudicationRule = 'same_value' | 'trust_priority' | 'newest_temporal' | 'keep_multi' | 'manual';

// ── 计划输入（plan.py FusionPlan 投影；pydantic extra=forbid） ──────

export interface FusionFieldMappingInput {
  source_field: string;
  output_field: string;
  rule?: AdjudicationRule;
}

/** plan.py FusionSourceSpec（锚 + 声明；priority 两源必须不同）。 */
export interface FusionSourceSpecInput {
  kind: 'session_ref' | 'fabric_parquet' | 'project_artifact' | 'table_rows';
  session_id?: string;
  ref?: string;
  project_id?: string;
  artifact_id?: string;
  revision_no?: number;
  table_ref?: string;
  label: string;
  priority: 1 | 2;
  priority_reason: string;
  license?: string | null;
  attribution?: string | null;
  external_id_field?: string | null;
  id_space?: string | null;
  name_field: string;
  address_field?: string | null;
  category_field?: string | null;
  category_map?: Record<string, string>;
  temporal_field?: string | null;
  field_mappings?: FusionFieldMappingInput[];
}

export interface FusionStrategyInput {
  /** 必须恰好覆盖 external_id/name/address/category/distance、非负、Σ=1。 */
  weights: Record<EvidenceKind, number>;
  auto_match_min?: number;
  review_min?: number;
  ambiguous_margin?: number;
  max_match_distance_m?: number;
  close_distance_m?: number;
  overlap_min?: number;
  overlap_full?: number;
  name_normalizers?: string[];
  category_unknown_factor?: number;
  id_mismatch_blocks_match?: boolean;
}

export interface FusionPlanInput {
  schema_version?: number;
  title?: string;
  entity_kind: EntityKind;
  source_a: FusionSourceSpecInput;
  source_b: FusionSourceSpecInput;
  strategy: FusionStrategyInput;
  geometry?: { adoption: GeometryAdoption };
  commit_policy?: CommitPolicy;
  invalid_geometry_policy?: InvalidGeometryPolicy;
  budgets?: Record<string, number>;
  output_name?: string;
  purpose?: string | null;
}

/** describe 端点的逐源探测输入（service._probe_spec 可读键的子集）。 */
export interface FusionDescribeSourceInput {
  side: 'a' | 'b';
  kind?: FusionSourceSpecInput['kind'];
  session_id?: string;
  ref?: string;
  entity_kind?: EntityKind;
  name_field?: string;
  address_field?: string | null;
  category_field?: string | null;
  temporal_field?: string | null;
  external_id_field?: string | null;
  id_space?: string | null;
}

// ── 来源资格卡（service._eligibility_card / describe_sources） ─────

export interface FusionTemporalParse {
  sample_n: number;
  parsed: number;
  note: string;
}

export interface FusionSourceCard {
  side: string;
  state: 'ok' | 'unresolvable';
  /** state=ok */
  adapter_verified?: boolean;
  label?: string;
  row_count?: number;
  crs?: string | null;
  crs_class?: string | null;
  /** 几何形态串（如 "Point"）；"none" = 无几何。 */
  geometry?: string;
  invalid_geometry_excluded?: number;
  /** 声明字段可用性：name_field/external_id_field/address_field/category_field/temporal_field。 */
  fields?: Record<string, { field: string; present: boolean }>;
  declared_field_names?: string[];
  temporal_parse?: FusionTemporalParse | null;
  content_fingerprint?: string;
  content_revision?: string | number | null;
  license_declared?: string | null;
  attribution_declared?: string | null;
  note?: string;
  /** state=unresolvable */
  error_code?: string;
  message?: string;
  correction_hint?: string;
}

export interface FusionDescribeResponse {
  session_id: string;
  sources: FusionSourceCard[];
}

// ── 计划视图 ──────────────────────────────────────────────────────

export interface FusionPlanView {
  plan_id: string;
  session_id: string;
  project_id?: string | null;
  title: string;
  status: 'draft' | 'archived';
  revision: number;
  plan: FusionPlanInput;
  normalized_digest: string;
  fingerprint_a: string | null;
  fingerprint_b: string | null;
  preview: Record<string, unknown> | null;
  preview_digest: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface FusionPlanListResponse {
  plans: FusionPlanView[];
  cursor: number | null;
  has_more: boolean;
}

// ── 预览（service.preview_plan 有界投影） ──────────────────────────

export interface FusionMatchStats {
  rows_a: number;
  rows_b: number;
  candidates: number;
  candidate_overflow_entities_a: number;
  candidate_overflow_entities_b: number;
  pairs_matched: number;
  pairs_needs_review: number;
  pairs_ambiguous: number;
  unmatched_a: number;
  unmatched_b: number;
  hard_blocked: number;
}

export interface FusionEvidenceItem {
  kind: EvidenceKind;
  value: number | string | null;
  detail?: Record<string, unknown>;
}

export interface FusionPreviewSamplePair {
  idx_a: number;
  idx_b: number;
  score: number;
  name_a: string;
  name_b: string;
  status?: FusionPairStatus;
  field_conflicts?: Record<string, string>;
}

export interface FusionPreviewSamples {
  matched: FusionPreviewSamplePair[];
  review: Array<FusionPreviewSamplePair & { status: FusionPairStatus; field_conflicts: Record<string, string> }>;
  unmatched_a_first: number[];
  unmatched_b_first: number[];
  sample_limit: number;
}

export interface FusionPreviewWarning {
  item: string;
  message: string;
}

export interface FusionPreviewResult {
  cached: boolean;
  plan_digest?: string;
  preview_digest?: string;
  sources: FusionSourceCard[];
  match_stats: FusionMatchStats;
  budgets: Record<string, number>;
  samples: FusionPreviewSamples;
  warnings: FusionPreviewWarning[];
  truncated_for_bound?: boolean;
}

// ── 运行视图 ──────────────────────────────────────────────────────

export interface FusionRunReceipt {
  run_id: string;
  match_stats: FusionMatchStats;
  unmatched_a: number;
  unmatched_b: number;
  fingerprint_a: string;
  fingerprint_b: string;
  elapsed_s: number;
  /** "matched_pending_commit" = 匹配完成待 commit。 */
  state: string;
}

export interface FusionCommitReceipt {
  counts: {
    merged: number;
    withheld_review: number;
    rejected: number;
    single_a: number;
    single_b: number;
  };
  total_features: number;
  unresolved_field_count: number;
  decided_pairs: number;
  orphan_decisions: number;
  data_object?: Record<string, unknown> | null;
  quality_summary_bounded?: {
    matched_auto: number;
    needs_review: number;
    ambiguous: number;
    hard_blocked: number;
  };
}

export interface FusionRunView {
  run_id: string;
  plan_id: string;
  session_id: string;
  status: FusionRunStatus;
  job_id: number | null;
  plan_revision?: number;
  plan_digest?: string;
  base_run_id?: string | null;
  receipt: FusionRunReceipt | null;
  error_code: string | null;
  output_ref: string | null;
  output_sha256?: string | null;
  output_features: number | null;
  commit_receipt: FusionCommitReceipt | null;
  created_at: string | null;
  finished_at: string | null;
  poll?: string;
  idempotent_reuse?: boolean;
}

export interface FusionRunListResponse {
  runs: FusionRunView[];
}

/** outputs.result_page 投影：cursor 是本页终点 = 下页起点。 */
export interface FusionResultPageView {
  run_id: string;
  features: Array<{
    type: 'Feature';
    geometry: unknown;
    properties: Record<string, unknown>;
  }>;
  cursor: number;
  total: number;
  has_more: boolean;
}

export interface FusionRunCancelResponse {
  run_id: string;
  status?: string | null;
  cancel_requested?: boolean | null;
  note?: string | null;
  job_id?: number;
}

// ── 审阅队列与决定 ────────────────────────────────────────────────

export interface FusionReviewItem {
  pair_key: string;
  idx_a: number;
  idx_b: number;
  status: FusionPairStatus;
  score: number | null;
  hard_block?: string | null;
  id_pinned?: boolean;
  name_a?: string;
  name_b?: string;
  external_id_a?: string | null;
  external_id_b?: string | null;
  evidence?: FusionEvidenceItem[] | null;
  renormalized?: boolean;
  geometry_evidence?: Record<string, unknown> | null;
  field_conflicts?: Record<string, string> | null;
  decision?: FusionDecisionView | null;
}

export interface FusionReviewQueueResponse {
  run_id: string;
  total: number;
  cursor: number;
  has_more: boolean;
  items: FusionReviewItem[];
}

export interface FusionDecisionView {
  decision_id: string;
  plan_id: string;
  pair_key: string;
  action: DecisionAction;
  field_values?: Record<string, unknown> | null;
  geometry_side?: 'a' | 'b' | null;
  decision_run_id?: string | null;
  idx_a?: number | null;
  idx_b?: number | null;
  reviewer?: string | null;
  note?: string | null;
  locked?: boolean;
  revision: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface FusionDecisionListResponse {
  plan_id: string;
  decisions: FusionDecisionView[];
  cursor: number | null;
  has_more: boolean;
}

/** upsert 决定输入（route DecisionBody；新决定 expected_revision=0）。 */
export interface FusionDecisionInput {
  pair_key: string;
  action: DecisionAction;
  decision_run_id?: string | null;
  idx_a?: number | null;
  idx_b?: number | null;
  field_values?: Record<string, unknown> | null;
  geometry_side?: 'a' | 'b' | null;
  reviewer?: string | null;
  note?: string | null;
  expected_revision: number;
}

// ── 渲染投影 / 增量刷新 ───────────────────────────────────────────

export interface FusionRenderRefResponse {
  render_ref: string;
  /** 要素数（number，非数组）。 */
  features: number;
}

/** incremental.RefreshImpact.to_dict 投影。 */
export interface FusionRefreshImpact {
  side_a: { added: string[]; removed: string[] };
  side_b: { added: string[]; removed: string[] };
  total_new: number;
  affected_ratio: number;
  cap: number;
  safe: boolean;
  decisions_inherited: number;
  decisions_stale: string[];
  decisions_stale_total: number;
  decisions_orphan: number;
  verdict: string;
}

export interface FusionRefreshUnchanged {
  plan_id: string;
  base_run_id: string | null;
  changed: false;
  message?: string;
}

export interface FusionRefreshDryRun extends FusionRefreshImpact {
  plan_id: string;
  base_run_id: string | null;
  changed: true;
  dry_run: true;
}

export interface FusionRefreshApplied {
  plan_id: string;
  base_run_id: string | null;
  changed: true;
  impact: FusionRefreshImpact;
  run: FusionRunView;
}

export type FusionRefreshResponse =
  | FusionRefreshUnchanged
  | FusionRefreshDryRun
  | FusionRefreshApplied;

// ── 错误归一（HTTP detail = {error_code, message, correction_hint?, detail?}） ──

export function asDataFusionError(
  err: unknown,
): { code: string; message: string; hint?: string; detail?: Record<string, unknown> } {
  // ApiError.body = FastAPI detail（typed error_code/message/correction_hint）
  const anyErr = err as {
    body?: { detail?: { error_code?: string; message?: string; correction_hint?: string; detail?: Record<string, unknown> } }
      & { error_code?: string; message?: string; correction_hint?: string; detail?: Record<string, unknown> };
    message?: string;
  };
  const body = anyErr?.body;
  const detail =
    (body as { detail?: unknown } | undefined)?.detail && typeof (body as { detail?: unknown }).detail === 'object'
      ? (body as { detail: Record<string, unknown> }).detail
      : (body as Record<string, unknown> | undefined);
  if (detail && typeof detail === 'object' && 'error_code' in detail) {
    const d = detail as { error_code: string; message?: string; correction_hint?: string; detail?: Record<string, unknown> };
    return {
      code: d.error_code,
      message: d.message ?? '请求失败',
      hint: d.correction_hint,
      detail: d.detail,
    };
  }
  return { code: 'network_error', message: anyErr?.message ?? String(err) };
}

type Options = { method?: string; body?: unknown; ownerToken?: string | null };

function client(sessionId: string, ownerToken?: string | null) {
  const base = `${BASE}/sessions/${encodeURIComponent(sessionId)}`;
  const opts = (o: Options): Options => ({ ...o, ownerToken });
  return {
    /** 来源资格卡（body {sources:[…≤2]}，逐源 side 标记）。 */
    describeSources: (sources: FusionDescribeSourceInput[]) =>
      apiFetch<FusionDescribeResponse>(`${base}/sources/describe`,
        opts({ method: 'POST', body: { sources } })),
    createPlan: (plan: FusionPlanInput) =>
      apiFetch<FusionPlanView>(`${base}/plans`, opts({ method: 'POST', body: { plan } })),
    listPlans: (limit = 50, cursor = 0) =>
      apiFetch<FusionPlanListResponse>(`${base}/plans?limit=${limit}&cursor=${cursor}`, opts({})),
    getPlan: (planId: string) =>
      apiFetch<FusionPlanView>(`${base}/plans/${planId}`, opts({})),
    updatePlan: (planId: string, plan: FusionPlanInput, expectedRevision: number) =>
      apiFetch<FusionPlanView>(`${base}/plans/${planId}`,
        opts({ method: 'PUT', body: { plan, expected_revision: expectedRevision } })),
    /** archive 路由复用 UpdatePlanBody（plan 键必填但 service 不读）。 */
    archivePlan: (planId: string, expectedRevision: number) =>
      apiFetch<FusionPlanView>(`${base}/plans/${planId}/archive`,
        opts({ method: 'POST', body: { plan: {}, expected_revision: expectedRevision } })),
    preview: (planId: string, expectedRevision: number) =>
      apiFetch<FusionPreviewResult>(`${base}/plans/${planId}/preview`,
        opts({ method: 'POST', body: { expected_revision: expectedRevision } })),
    execute: (planId: string, expectedRevision: number, background = true) =>
      apiFetch<FusionRunView>(`${base}/plans/${planId}/execute`,
        opts({ method: 'POST', body: { expected_revision: expectedRevision, background } })),
    listRuns: (planId: string, limit = 50) =>
      apiFetch<FusionRunListResponse>(`${base}/plans/${planId}/runs?limit=${limit}`, opts({})),
    inspectRun: (runId: string) =>
      apiFetch<FusionRunView>(`${base}/runs/${runId}`, opts({})),
    resultPage: (runId: string, cursor = 0, limit = 50) =>
      apiFetch<FusionResultPageView>(`${base}/runs/${runId}/results?cursor=${cursor}&limit=${limit}`, opts({})),
    cancelRun: (runId: string) =>
      apiFetch<FusionRunCancelResponse>(`${base}/runs/${runId}/cancel`,
        opts({ method: 'POST', body: {} })),
    reviewQueue: (runId: string, cursor = 0, limit = 50, status?: string) =>
      apiFetch<FusionReviewQueueResponse>(
        `${base}/runs/${runId}/review-queue?cursor=${cursor}&limit=${limit}${status ? `&status=${encodeURIComponent(status)}` : ''}`,
        opts({})),
    listDecisions: (planId: string, limit = 200, cursor = 0) =>
      apiFetch<FusionDecisionListResponse>(
        `${base}/plans/${planId}/decisions?limit=${limit}&cursor=${cursor}`, opts({})),
    upsertDecision: (planId: string, input: FusionDecisionInput) =>
      apiFetch<FusionDecisionView>(`${base}/plans/${planId}/decisions`,
        opts({ method: 'POST', body: input })),
    deleteDecision: (planId: string, pairKey: string, expectedRevision: number) =>
      apiFetch<FusionDecisionListResponse>(`${base}/plans/${planId}/decisions`,
        opts({ method: 'DELETE', body: { pair_key: pairKey, expected_revision: expectedRevision } })),
    commitRun: (runId: string) =>
      apiFetch<FusionRunView>(`${base}/runs/${runId}/commit`,
        opts({ method: 'POST', body: {} })),
    renderRef: (runId: string) =>
      apiFetch<FusionRenderRefResponse>(`${base}/runs/${runId}/render-ref`,
        opts({ method: 'POST', body: {} })),
    refresh: (planId: string, expectedRevision: number, dryRun: boolean) =>
      apiFetch<FusionRefreshResponse>(`${base}/plans/${planId}/refresh`,
        opts({ method: 'POST', body: { expected_revision: expectedRevision, dry_run: dryRun } })),
  };
}

export const dataFusionApi = { client };
