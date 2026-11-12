/**
 * Skill Jobs API client（G30）—— 技能作业工作台与聊天共享同一后端契约。
 *
 * 端点：/api/v1/skill-jobs/*（catalog/prepare/start/runs/presets）。
 * typed 错误（HTTP detail.code）保持原样上抛，UI 按 code 定位字段/数据角色。
 */
import { apiFetch, ApiError } from './transport';

export interface SkillParamSpec {
  name: string;
  label: string;
  type: 'number' | 'string' | 'enum' | 'boolean' | 'field_ref';
  required: boolean;
  unit?: string;
  enum_values?: string[];
  default?: unknown;
  role?: string;
  expects?: string;
  required_if_param?: string;
  required_if_in?: string[];
  description?: string;
  max_length?: number;
  min_value?: number | null;
  max_value?: number | null;
}

export interface SkillRoleBindingSpec {
  role: string;
  required: boolean;
  geometry_kinds: string[];
  min_features: number;
  description?: string;
}

export interface SkillCatalogEntry {
  asset_type: 'gis_skill' | 'md_guide' | 'extension_candidate';
  id: string;
  name: string;
  description: string;
  domain?: string;
  version?: string;
  digest?: string;
  deprecated?: boolean;
  deprecated_by?: string;
  when_to_use?: string;
  runnability: 'runnable' | 'guidance_only' | 'deprecated' | 'unavailable';
  runnability_reasons?: string[];
  result_kind?: string;
  parameters?: SkillParamSpec[];
  role_bindings?: SkillRoleBindingSpec[];
  steps_summary?: Array<{ step_id: string; title: string; kind: string }>;
  tools?: string[];
}

export interface CatalogResponse {
  skills: SkillCatalogEntry[];
  md_guides: SkillCatalogEntry[];
  extension_candidates: SkillCatalogEntry[];
  counts: Record<string, number>;
}

export interface PreparedPreview {
  skill: { id: string; name: string; version: string; digest: string; domain: string };
  result_kind: string;
  steps: Array<{
    step_id: string; tool: string; capability: string; side_effect: string;
    reads_roles: string[]; produces_evidence: string[]; required: boolean;
  }>;
  bindings: Array<{
    role: string; ref: string; inline: boolean; geometry_kind: string;
    feature_count: number; fields: string[]; content_digest: string;
  }>;
  params: Array<{ name: string; source: string }>;
  obligations: string[];
  evidence_covered: string[];
  evidence_not_covered: string[];
}

export interface PrepareResponse {
  prepared_id: string;
  skill: { id: string; version: string; digest: string };
  idempotency_key: string;
  preview: PreparedPreview;
}

export interface SkillJobRun {
  job_id: string;
  status: string;
  progress?: number | null;
  message?: string | null;
  skill_id?: string;
  skill_version?: string;
  result_ref?: string | null;
  error?: string | null;
  cancellable?: boolean;
  active?: boolean;
  already_running?: boolean;
  idempotent_reuse?: boolean;
  steps?: Array<Record<string, unknown>>;
  evidence_covered?: string[];
  evidence_not_covered?: string[];
  artifact_refs?: Record<string, string>;
  created_at?: string | null;
}

export interface SkillJobPreset {
  id: string;
  name: string;
  description: string;
  skill_id: string;
  skill_version: string;
  params: Record<string, unknown>;
  bindings_template: Record<string, unknown>;
  updated_at: string | null;
}

export class SkillJobApiError extends Error {
  code: string;
  detail: Record<string, unknown>;
  constructor(code: string, message: string, detail: Record<string, unknown>) {
    super(message);
    this.code = code;
    this.detail = detail;
  }
}

async function request<T>(path: string, options?: {
  method?: string;
  body?: unknown;
}): Promise<T> {
  try {
    return await apiFetch<T>(path, {
      method: options?.method,
      body: options?.body,
    });
  } catch (err) {
    if (err instanceof ApiError) {
      // ApiError.body = 解析后的响应体（FastAPI detail 形状）
      const detail = (err.body as {
        detail?: { code?: string; message?: string; detail?: Record<string, unknown> };
      } | null | undefined)?.detail;
      throw new SkillJobApiError(
        detail?.code || `HTTP_${err.status}`,
        detail?.message || err.message.slice(0, 160),
        detail?.detail || {},
      );
    }
    throw err;
  }
}

export function getCatalog(): Promise<CatalogResponse> {
  return request<CatalogResponse>('/api/v1/skill-jobs/catalog');
}

export function prepareSkillJob(payload: {
  session_id: string;
  skill_id: string;
  skill_version?: string;
  params: Record<string, unknown>;
  bindings: Array<{ role: string; ref?: string; inline?: unknown }>;
}): Promise<PrepareResponse> {
    return request<PrepareResponse>('/api/v1/skill-jobs/prepare', {
    method: 'POST',
    body: payload,
  });
}

export function startSkillJob(payload: { prepared_id: string; session_id: string }): Promise<SkillJobRun> {
    return request<SkillJobRun>('/api/v1/skill-jobs/start', {
    method: 'POST',
    body: payload,
  });
}

export function listSkillJobRuns(sessionId: string): Promise<{ runs: SkillJobRun[] }> {
  return request<{ runs: SkillJobRun[] }>(`/api/v1/skill-jobs/runs?session_id=${encodeURIComponent(sessionId)}`);
}

export function getSkillJobRun(jobId: string | number): Promise<SkillJobRun> {
  return request<SkillJobRun>(`/api/v1/skill-jobs/runs/${jobId}`);
}

export function cancelSkillJobRun(jobId: string | number, sessionId: string): Promise<{ cancel_requested: boolean }> {
  return request<{ cancel_requested: boolean }>(
    `/api/v1/skill-jobs/runs/${jobId}/cancel?session_id=${encodeURIComponent(sessionId)}`,
    { method: 'POST' },
  );
}

export function listPresets(): Promise<{ presets: SkillJobPreset[] }> {
  return request<{ presets: SkillJobPreset[] }>('/api/v1/skill-jobs/presets');
}

export function createPreset(payload: {
  name: string; description?: string; skill_id: string; skill_version?: string;
  params: Record<string, unknown>; bindings_template: Record<string, unknown>;
}): Promise<SkillJobPreset> {
    return request<SkillJobPreset>('/api/v1/skill-jobs/presets', {
    method: 'POST',
    body: payload,
  });
}

export function presetPrepare(
  presetId: string,
  payload: { session_id: string; bindings: Array<{ role: string; ref?: string; inline?: unknown }>; params_override?: Record<string, unknown> },
): Promise<PrepareResponse & { preset: { name: string }; param_sources: Array<{ name: string; source: string; value: unknown }> }> {
  return request('/api/v1/skill-jobs/presets/' + encodeURIComponent(presetId) + '/prepare', { method: 'POST', body: payload });
}
