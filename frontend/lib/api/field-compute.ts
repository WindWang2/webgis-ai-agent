/**
 * Field Compute API — 有类型/单位/时区/作用范围的字段计算工作台客户端（G19）。
 *
 * 链路：describe → createPlan → preview(sample) → execute → getResult。
 * 所有请求走共享 transport（apiFetch）以获得带类型的错误；owner token 经
 * SEC-08 的 X-Session-Token header 承载（匿名会话）。
 *
 * 契约纪律：
 * - preview/execute 是 POST（transport 永不重放非幂等方法，天然单次执行）；
 * - preview 带 generation 查询参数，前端用同一代次丢弃迟到响应；
 * - 编译期错误 detail 含 code/detail/position/hint，执行期含 code/detail；
 *   parseFieldComputeError 把 ApiError.body 归一为可读结构。
 */

import { apiFetch, ApiError, describeApiError } from './transport';

const FC_BASE = '/api/v1/field-compute';
const FC_LABEL = 'Field compute API error';

export interface FieldComputeRequestOptions {
  ownerToken?: string | null;
  signal?: AbortSignal;
}

// ── describe ─────────────────────────────────────────────────────────────

export interface FieldDescriptor {
  name: string;
  dtype: string;
  unit_hint: string;
  unit_confidence: string;
  sample: unknown[];
  [key: string]: unknown;
}

export interface FunctionDescriptor {
  name: string;
  family: string;
  arg_types: string[];
  result_type: string;
  null_behavior: string;
  unit_rule: string;
  help_zh: string;
  min_args: number;
  max_args: number;
  resource?: string;
  [key: string]: unknown;
}

export interface GeometryDescriptor {
  /** geodesic | planar | ""（空串=被阻断）。 */
  mode: string;
  block_reason: string | null;
  detail: string;
  [key: string]: unknown;
}

export interface DescribeResponse {
  input_ref: string;
  data_shape: string;
  row_count: number;
  crs: string;
  crs_source: string;
  fields: FieldDescriptor[];
  sample_rows: Array<Record<string, unknown>>;
  functions: FunctionDescriptor[];
  scope_modes: string[];
  error_policies: string[];
  limits: Record<string, number>;
  geometry: GeometryDescriptor;
  unit_declaration_required: boolean;
  function_set_version: string;
  ir_version: string;
  note?: string;
  [key: string]: unknown;
}

// ── plan ─────────────────────────────────────────────────────────────────

/** 表达式：文本公式（"{人口} / {面积 平方千米}"）或 IR dict。 */
export type ColumnExpression = string | Record<string, unknown>;

export interface ColumnSpec {
  name: string;
  expression: ColumnExpression;
  declared_unit?: string;
  description?: string;
}

export interface PredicateSpec {
  field: string;
  op: string;
  value?: unknown;
  values?: unknown[];
  [key: string]: unknown;
}

export interface ScopeSpec {
  mode: string; // all_rows | predicate | selection
  predicate?: PredicateSpec | null;
  [key: string]: unknown;
}

export interface CreatePlanRequest {
  session_id: string;
  source_ref: string;
  owner?: string;
  columns: ColumnSpec[];
  scope?: ScopeSpec;
  field_units?: Record<string, string>;
  timezone?: string;
  date_format?: string;
  error_policy?: string;
}

export interface ComputePlan {
  plan_id: string;
  compute_fingerprint: string;
  state: string;
  input_ref: string;
  /** 任务契约字段；真实 PlanOut 以 content_hash 承载，故可选。 */
  input_fingerprint?: string;
  columns?: ColumnSpec[];
  mutates_data?: boolean;
  [key: string]: unknown;
}

export interface PlanResponse {
  plan: ComputePlan;
  data_shape: string;
  column_order: string[];
  scope_summary: Record<string, unknown>;
  geometry: GeometryDescriptor;
  estimated_ops: number;
  warnings: string[];
  [key: string]: unknown;
}

// ── preview ──────────────────────────────────────────────────────────────

export interface UnitSummary {
  unit: string;
  unit_status: string;
  dimension: string;
  display: string;
  declared: boolean;
  [key: string]: unknown;
}

export interface PreviewColumn {
  name: string;
  formula: string;
  dtype: string;
  unit: string;
  unit_status: string;
  unit_dimension: string;
  measurement_kind: string;
  derived_from: Record<string, unknown>;
  declared_unit: string;
  computed: number;
  nulls: number;
  errors: number;
  expression?: unknown;
  [key: string]: unknown;
}

export interface BeforeAfterRow {
  row: number;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ErrorSample {
  row: number;
  column: string;
  code: string;
  [key: string]: unknown;
}

export interface PreviewBudget {
  row_count: number;
  estimated_ops: number;
  max_rows: number;
  max_ops_per_row: number;
  within_budget: boolean;
  [key: string]: unknown;
}

export interface PreviewConfirmation {
  params_fingerprint: string;
  note: string;
  [key: string]: unknown;
}

export interface PreviewResponse {
  plan_id: string;
  generation: number;
  compute_fingerprint: string;
  kind: string; // "sample"
  sampled_rows: number;
  total_rows: number;
  truncated: boolean;
  data_shape: string;
  columns: PreviewColumn[];
  before_after: BeforeAfterRow[];
  null_count_sample: number;
  error_count_sample: number;
  error_samples: ErrorSample[];
  sample_error_rate_estimate: number;
  scope: Record<string, unknown>;
  unit_summary: Record<string, UnitSummary>;
  precision_policy: string;
  geometry: Record<string, unknown>;
  budget: PreviewBudget;
  degraded: string[];
  confirmation: PreviewConfirmation;
  next_actions: string[];
  [key: string]: unknown;
}

// ── execute / receipt ────────────────────────────────────────────────────

export interface ExecuteRequest {
  expected_fingerprint?: string;
  project_id?: string;
  owner?: string;
}

export interface ReceiptColumn {
  name: string;
  dtype: string;
  unit: string;
  unit_status: string;
  measurement_kind: string;
  computed: number;
  nulls: number;
  errors: number;
  error_samples: ErrorSample[];
  [key: string]: unknown;
}

export interface ComputeReceipt {
  plan_id: string;
  compute_fingerprint: string;
  input_ref: string;
  output_ref: string;
  row_count: number;
  computed_rows: number;
  selected_rows: number;
  skipped_rows: number;
  null_count: number;
  error_count: number;
  columns: ReceiptColumn[];
  scope: Record<string, unknown>;
  formula_summary: string[];
  unit_summary: Record<string, UnitSummary>;
  precision_policy: string;
  /** session_ref（会话临时）| project_artifact（项目产物）。 */
  persistence: string;
  descriptor_minted: boolean;
  degraded: string[];
  created_at: number;
  [key: string]: unknown;
}

export interface ReceiptResponse {
  receipt: ComputeReceipt;
  [key: string]: unknown;
}

// ── result / catalog / plans / project-results ───────────────────────────

export interface ResultResponse {
  output_ref: string;
  data_shape: string;
  crs: string;
  row_count: number;
  fields: Array<string | FieldDescriptor>;
  sample_rows: Array<Record<string, unknown>>;
  readable: boolean;
  [key: string]: unknown;
}

export interface CatalogResponse {
  function_set_version: string;
  ir_version: string;
  functions: FunctionDescriptor[];
  scope_modes: string[];
  error_policies: string[];
  limits: Record<string, number>;
  [key: string]: unknown;
}

export interface ProjectResultItem {
  project_id: string;
  output_ref: string;
  input_ref: string;
  plan_id: string;
  compute_fingerprint: string;
  formula_summary: string[];
  saved_at: number;
  [key: string]: unknown;
}

export interface ProjectResultsResponse {
  items: ProjectResultItem[];
  [key: string]: unknown;
}

export interface PlansResponse {
  plans: ComputePlan[];
  [key: string]: unknown;
}

export interface DiscardPlanResponse {
  plan_id: string;
  state: string;
  [key: string]: unknown;
}

// ── 错误归一 ─────────────────────────────────────────────────────────────

export interface FieldComputeErrorInfo {
  code?: string;
  detail: string;
  position?: number | string;
  hint?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function errorBody(err: unknown): unknown {
  if (err instanceof ApiError) return err.body;
  if (isRecord(err) && 'body' in err) return (err as Record<string, unknown>).body;
  return undefined;
}

/**
 * 把 transport 抛出的错误归一为可读结构（编译期 code/position/hint + 通用
 * detail）。后端有两种信封：legacy `{"detail": {...}}` 与仓库统一错误信封
 * `{"data": {...}, "message": "..."}`（app/core/exception.py 把结构化 detail
 * 移入 data）；此外还可能是 `{"detail": "..."}`、裸对象或纯文本。
 * 网络层错误回退到 describeApiError 的可读文案。
 */
export function parseFieldComputeError(err: unknown): FieldComputeErrorInfo {
  const body = errorBody(err);
  let node: unknown = body;
  // 裸结构化体（{code, detail, position, hint}）本身就是要解析的对象，
  // 不能因为它含 detail 字段就被误当成信封解包成字符串。
  if (isRecord(node) && !('code' in node) && !('hint' in node)) {
    const detail = node.detail;
    const data = node.data;
    if (isRecord(detail) && ('code' in detail || 'hint' in detail)) {
      node = detail;
    } else if (isRecord(data) && ('code' in data || 'detail' in data || 'hint' in data)) {
      node = data;
    } else if ('detail' in node) {
      node = detail;
    }
  }

  if (isRecord(node)) {
    const position = node.position;
    return {
      code: asString(node.code) || undefined,
      detail: asString(node.detail) || asString(node.message) || '',
      position: typeof position === 'number' || typeof position === 'string'
        ? position
        : undefined,
      hint: asString(node.hint) || undefined,
    };
  }
  if (typeof node === 'string' && node.trim()) return { detail: node };
  return { detail: describeApiError(err, FC_LABEL) };
}

// ── API ──────────────────────────────────────────────────────────────────

export const fieldComputeApi = {
  describe(
    req: { session_id: string; source_ref: string },
    opts?: FieldComputeRequestOptions,
  ): Promise<DescribeResponse> {
    return apiFetch<DescribeResponse>(`${FC_BASE}/describe`, {
      method: 'POST',
      body: req,
      ownerToken: opts?.ownerToken ?? undefined,
      signal: opts?.signal,
      label: FC_LABEL,
    });
  },

  createPlan(
    req: CreatePlanRequest,
    opts?: FieldComputeRequestOptions,
  ): Promise<PlanResponse> {
    return apiFetch<PlanResponse>(`${FC_BASE}/plan`, {
      method: 'POST',
      body: req,
      ownerToken: opts?.ownerToken ?? undefined,
      signal: opts?.signal,
      label: FC_LABEL,
    });
  },

  preview(
    planId: string,
    sessionId: string,
    generation: number,
    opts?: FieldComputeRequestOptions,
  ): Promise<PreviewResponse> {
    return apiFetch<PreviewResponse>(
      `${FC_BASE}/plan/${encodeURIComponent(planId)}/preview?session_id=${encodeURIComponent(sessionId)}&generation=${generation}`,
      {
        method: 'POST',
        ownerToken: opts?.ownerToken ?? undefined,
        signal: opts?.signal,
        label: FC_LABEL,
      },
    );
  },

  execute(
    planId: string,
    sessionId: string,
    body: ExecuteRequest,
    opts?: FieldComputeRequestOptions,
  ): Promise<ReceiptResponse> {
    return apiFetch<ReceiptResponse>(
      `${FC_BASE}/plan/${encodeURIComponent(planId)}/execute?session_id=${encodeURIComponent(sessionId)}`,
      {
        method: 'POST',
        body,
        ownerToken: opts?.ownerToken ?? undefined,
        signal: opts?.signal,
        label: FC_LABEL,
      },
    );
  },

  listPlans(
    sessionId: string,
    opts?: FieldComputeRequestOptions,
  ): Promise<PlansResponse> {
    return apiFetch<PlansResponse>(
      `${FC_BASE}/plans?session_id=${encodeURIComponent(sessionId)}`,
      { ownerToken: opts?.ownerToken ?? undefined, signal: opts?.signal, label: FC_LABEL },
    );
  },

  getResult(
    sessionId: string,
    outputRef: string,
    opts?: FieldComputeRequestOptions,
  ): Promise<ResultResponse> {
    return apiFetch<ResultResponse>(
      `${FC_BASE}/result?session_id=${encodeURIComponent(sessionId)}&output_ref=${encodeURIComponent(outputRef)}`,
      { ownerToken: opts?.ownerToken ?? undefined, signal: opts?.signal, label: FC_LABEL },
    );
  },

  projectResults(
    sessionId: string,
    projectId: string,
    opts?: FieldComputeRequestOptions,
  ): Promise<ProjectResultsResponse> {
    return apiFetch<ProjectResultsResponse>(
      `${FC_BASE}/project-results?session_id=${encodeURIComponent(sessionId)}&project_id=${encodeURIComponent(projectId)}`,
      { ownerToken: opts?.ownerToken ?? undefined, signal: opts?.signal, label: FC_LABEL },
    );
  },

  catalog(opts?: FieldComputeRequestOptions): Promise<CatalogResponse> {
    return apiFetch<CatalogResponse>(`${FC_BASE}/catalog`, {
      ownerToken: opts?.ownerToken ?? undefined,
      signal: opts?.signal,
      label: FC_LABEL,
    });
  },

  /** 请求服务端协作取消（执行循环每批检查；已发布成果不受影响）。 */
  cancelPlan(
    planId: string,
    sessionId: string,
    opts?: FieldComputeRequestOptions,
  ): Promise<{ plan_id: string; cancelled: boolean }> {
    return apiFetch<{ plan_id: string; cancelled: boolean }>(
      `${FC_BASE}/plan/${encodeURIComponent(planId)}/cancel?session_id=${encodeURIComponent(sessionId)}`,
      { method: 'POST', ownerToken: opts?.ownerToken ?? undefined, signal: opts?.signal, label: FC_LABEL },
    );
  },

  discardPlan(
    planId: string,
    sessionId: string,
    opts?: FieldComputeRequestOptions,
  ): Promise<DiscardPlanResponse> {
    return apiFetch<DiscardPlanResponse>(
      `${FC_BASE}/plan/${encodeURIComponent(planId)}?session_id=${encodeURIComponent(sessionId)}`,
      { method: 'DELETE', ownerToken: opts?.ownerToken ?? undefined, signal: opts?.signal, label: FC_LABEL },
    );
  },
};
