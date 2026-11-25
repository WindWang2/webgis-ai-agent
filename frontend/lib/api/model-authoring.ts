/**
 * Model Authoring typed client（可视化 GIS 模型构建器数据通道）。
 *
 * 端点契约逐字对照 app/api/routes/model_authoring.py（/api/v1/model-authoring
 * 前缀，10 组端点）。错误模型：统一信封 `{code, success:false, message,
 * data:{code, detail, findings?}}` —— typed code 读 `body.data.code`（路由
 * `_http` 以 ``detail={"code": ...}`` 传结构化码，经 unified_http_exception_
 * handler 移入 data），折叠为 ModelAuthoringApiError{code, message, status,
 * findings}。词表（封闭）：
 * - DRAFT_EXISTS / DRAFT_NOT_FOUND / REVISION_CONFLICT / MODEL_SCHEMA_INVALID
 *   / MODEL_INVALID（带 findings）/ MODEL_NOT_PUBLISHED / PACKAGE_CONFLICT
 *   / REQUIRED_PARAM_MISSING / IMPORT_INVALID / INSTANCE_NOT_FOUND
 * - 节点/实例形状直接复用 lib/api/workflow-runtime.ts 投影类型（instantiate
 *   的 instance 与该路由同源投影）。
 */
import { apiFetch, isApiError } from './transport';
import type { WorkflowInstance } from './workflow-runtime';
import type { ModelDocument } from '@/lib/workflow/model-graph';

/* ── 类型（draft 投影 / findings / catalog，字段逐字） ───────────────── */

/** 草稿行（store._row_to_dict 投影）。 */
export interface ModelDraft {
  id: number;
  project_id: string;
  model_slug: string;
  name: string;
  description: string;
  document: ModelDocument;
  revision_no: number;
  document_fingerprint: string;
  source_kind: string;
  source_ref: string;
  template_id: string;
  latest_published_package_id: string;
  latest_published_version: string;
  created_by: string;
  created_at: string;
  updated_at: string;
}

/** typed finding（contracts.ModelFinding.to_bounded 投影；双语 detail）。 */
export interface ModelFinding {
  code: string;
  severity: string;
  node_id: string;
  edge: string;
  param: string;
  detail_zh: string;
  detail_en: string;
  detail: string;
}

export interface ModelCapabilityEntry {
  capability: string;
  input_artifact_types: string[];
  output_artifact_types: string[];
  status: string;
}

export interface ModelCapabilities {
  transform_ops: string[];
  data_roles: string[];
  capabilities: ModelCapabilityEntry[];
}

export interface ModelCatalogEntry {
  model_slug: string;
  name: string;
  published: boolean;
  latest_published_version: string;
  versions: { version: string; status: string; fingerprint: string }[];
  [key: string]: unknown;
}

export interface ModelInstantiateResult {
  model_slug: string;
  package: { package_id: string; version: string; fingerprint: string };
  params_resolved: Record<string, unknown>;
  node_params: Record<string, unknown>;
  /** {data_input 节点: 绑定回执 {ok, code, bound_ref}}（bindings.py 投影）。 */
  bindings: Record<string, { node?: string; ok: boolean; code: string; bound_ref?: string }>;
  instance: WorkflowInstance;
}

export interface ModelPublishResult {
  package_id: string;
  version: string;
  fingerprint: string;
  findings: ModelFinding[];
}

/* ── 错误模型 ───────────────────────────────────────────────────────── */

export class ModelAuthoringApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
    /** MODEL_INVALID（发布 422）随错误体携带的 typed findings。 */
    public readonly findings: ModelFinding[] = [],
  ) {
    super(message);
    this.name = 'ModelAuthoringApiError';
  }
}

/** 信封 data.code 是 typed 码；detail 是有界回显。 */
function parseAuthoringError(err: unknown): never {
  if (isApiError(err)) {
    const body = (err.body ?? {}) as {
      code?: unknown;
      message?: unknown;
      data?: { code?: unknown; detail?: unknown; findings?: unknown } | null;
      detail?: unknown;
    };
    const data = body.data ?? {};
    const findings = Array.isArray(data.findings) ? (data.findings as ModelFinding[]) : [];
    const code =
      (typeof data.code === 'string' && data.code) ||
      (typeof body.code === 'string' && body.code) ||
      'MODEL_AUTHORING_ERROR';
    const message =
      (typeof data.detail === 'string' && data.detail) ||
      (typeof body.message === 'string' && body.message) ||
      `HTTP ${err.status}`;
    throw new ModelAuthoringApiError(code, message, err.status, findings);
  }
  throw err;
}

async function ma<T>(
  path: string,
  opts: { method?: string; body?: unknown; signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<T> {
  try {
    return await apiFetch<T>(`/api/v1/model-authoring${path}`, {
      signal: opts.signal,
      timeoutMs: opts.timeoutMs,
      ...(opts.method ? { method: opts.method } : {}),
      ...(opts.body !== undefined ? { body: opts.body } : {}),
    });
  } catch (err) {
    parseAuthoringError(err);
  }
}

const enc = encodeURIComponent;

/* ── 能力目录 ───────────────────────────────────────────────────────── */

export async function getCapabilities(opts: { signal?: AbortSignal } = {}): Promise<ModelCapabilities> {
  const res = await ma<ModelCapabilities & { success: boolean }>('/capabilities', opts);
  return {
    transform_ops: res.transform_ops ?? [],
    data_roles: res.data_roles ?? [],
    capabilities: res.capabilities ?? [],
  };
}

/* ── 草稿 CRUD（CAS：PUT expected_revision → 409 REVISION_CONFLICT） ── */

export async function createModel(
  projectId: string,
  req: { model_slug: string; document: ModelDocument },
): Promise<ModelDraft> {
  const res = await ma<{ success: boolean; draft: ModelDraft }>(
    `/projects/${enc(projectId)}/models`,
    { method: 'POST', body: req },
  );
  return res.draft;
}

export async function listModels(
  projectId: string,
  opts: { limit?: number; offset?: number; signal?: AbortSignal } = {},
): Promise<ModelDraft[]> {
  const params = new URLSearchParams();
  if (opts.limit != null) params.set('limit', String(opts.limit));
  if (opts.offset != null) params.set('offset', String(opts.offset));
  const qs = params.toString();
  const res = await ma<{ success: boolean; models: ModelDraft[] }>(
    `/projects/${enc(projectId)}/models${qs ? `?${qs}` : ''}`,
    opts,
  );
  return res.models ?? [];
}

export async function getModel(
  projectId: string,
  modelSlug: string,
  opts: { signal?: AbortSignal } = {},
): Promise<ModelDraft> {
  const res = await ma<{ success: boolean; draft: ModelDraft }>(
    `/projects/${enc(projectId)}/models/${enc(modelSlug)}`,
    opts,
  );
  return res.draft;
}

export async function updateModel(
  projectId: string,
  modelSlug: string,
  req: { document: ModelDocument; expected_revision: number; name?: string; description?: string },
): Promise<ModelDraft> {
  const res = await ma<{ success: boolean; draft: ModelDraft }>(
    `/projects/${enc(projectId)}/models/${enc(modelSlug)}`,
    { method: 'PUT', body: req },
  );
  return res.draft;
}

export async function deleteModel(
  projectId: string,
  modelSlug: string,
  expectedRevision: number,
): Promise<void> {
  await ma(`/projects/${enc(projectId)}/models/${enc(modelSlug)}?expected_revision=${expectedRevision}`, {
    method: 'DELETE',
  });
}

/* ── 验证 / 发布 / 实例化 / 运行 ─────────────────────────────────────── */

export async function validateModel(
  projectId: string,
  modelSlug: string,
): Promise<{ valid: boolean; findings: ModelFinding[]; document_fingerprint: string }> {
  const res = await ma<{ success: boolean; valid: boolean; findings: ModelFinding[]; document_fingerprint: string }>(
    `/projects/${enc(projectId)}/models/${enc(modelSlug)}/validate`,
    { method: 'POST' },
  );
  return { valid: res.valid, findings: res.findings ?? [], document_fingerprint: res.document_fingerprint };
}

export async function publishModel(
  projectId: string,
  modelSlug: string,
  change: 'major' | 'minor' | 'patch' = 'minor',
): Promise<ModelPublishResult> {
  const res = await ma<{ success: boolean } & ModelPublishResult>(
    `/projects/${enc(projectId)}/models/${enc(modelSlug)}/publish`,
    { method: 'POST', body: { change } },
  );
  return {
    package_id: res.package_id,
    version: res.version,
    fingerprint: res.fingerprint,
    findings: res.findings ?? [],
  };
}

export async function instantiateModel(
  projectId: string,
  modelSlug: string,
  req: { session_id?: string; version?: string; param_values?: Record<string, unknown>; input_bindings?: Record<string, string> } = {},
): Promise<ModelInstantiateResult> {
  return ma<ModelInstantiateResult>(`/projects/${enc(projectId)}/models/${enc(modelSlug)}/instantiate`, {
    method: 'POST',
    body: {
      session_id: req.session_id ?? '',
      version: req.version ?? '',
      param_values: req.param_values ?? {},
      input_bindings: req.input_bindings ?? {},
    },
  });
}

/** 运行模型实例（project_id 走查询参数 —— 路由 @router.get 风格的显式绑定）。 */
export async function runModelInstance(
  instanceId: string,
  req: { projectId: string; deadlineS?: number; paramValues?: Record<string, unknown> },
): Promise<{ run: Record<string, unknown>; node_params: Record<string, unknown> }> {
  return ma(`/instances/${enc(instanceId)}/run?project_id=${enc(req.projectId)}`, {
    method: 'POST',
    body: { deadline_s: req.deadlineS ?? 60.0, ...(req.paramValues ? { param_values: req.paramValues } : {}) },
    timeoutMs: ((req.deadlineS ?? 60) + 15) * 1000,
  });
}

/* ── 目录 / 导入导出 ─────────────────────────────────────────────────── */

export async function getModelCatalog(
  projectId: string,
  opts: { limit?: number; signal?: AbortSignal } = {},
): Promise<ModelCatalogEntry[]> {
  const qs = opts.limit != null ? `?limit=${opts.limit}` : '';
  const res = await ma<{ success: boolean; models: ModelCatalogEntry[] }>(
    `/projects/${enc(projectId)}/catalog${qs}`,
    opts,
  );
  return res.models ?? [];
}

export async function exportModel(
  projectId: string,
  modelSlug: string,
): Promise<{ format: string; format_version: string; document: ModelDocument }> {
  const res = await ma<{ success: boolean; format: string; format_version: string; document: ModelDocument }>(
    `/projects/${enc(projectId)}/models/${enc(modelSlug)}/export`,
  );
  return { format: res.format, format_version: res.format_version, document: res.document };
}

export async function importModel(
  projectId: string,
  req: { model_slug: string; payload: Record<string, unknown> },
): Promise<ModelDraft> {
  const res = await ma<{ success: boolean; draft: ModelDraft }>(
    `/projects/${enc(projectId)}/models/import`,
    { method: 'POST', body: req },
  );
  return res.draft;
}
