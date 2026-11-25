/**
 * 制图设计规范 REST 客户端（后端 /api/v1/design-specs 已实现，行为真相
 * tests/unit/design_specs/test_design_spec_api.py）。
 *
 * 读走 GET Fast Path（面板双 tab 反复激活不重复请求）；一切 mutation 后显式
 * invalidateCache —— 面板内部状态不进全局 store，靠 invalidate 保证跨 tab 不 stale。
 */
import { apiFetch } from './transport';
import { fastGet, invalidateCache } from './get-fast-path';
import type {
  DesignSpecDetail,
  DesignSpecPayload,
  DesignSpecPreview,
  DesignSpecSummary,
  DesignSpecVersion,
  DesignSpecVersionResult,
  ReferenceValidation,
} from '@/lib/design-series/contracts';

const BASE = '/api/v1/design-specs';
const LABEL = 'Design spec API error';

export interface DesignSpecListResult {
  success: boolean;
  items: DesignSpecSummary[];
}

export interface DesignSpecMutationResult {
  success: boolean;
  spec: DesignSpecSummary;
}

export interface DesignSpecVersionCreatedResult {
  success: boolean;
  version: DesignSpecVersion;
}

export interface DesignSpecDeprecateResult {
  success: boolean;
  deprecated: boolean;
}

export async function listDesignSpecs(
  opts?: { forceRefresh?: boolean; signal?: AbortSignal },
): Promise<DesignSpecListResult> {
  return (await fastGet<DesignSpecListResult>(BASE, {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    label: LABEL,
  })).data;
}

export async function getDesignSpec(id: string): Promise<DesignSpecDetail> {
  return apiFetch<DesignSpecDetail>(`${BASE}/${encodeURIComponent(id)}`, { label: LABEL });
}

export async function createDesignSpec(
  payload: DesignSpecPayload,
): Promise<DesignSpecMutationResult> {
  const out = await apiFetch<DesignSpecMutationResult>(BASE, {
    method: 'POST',
    body: { payload },
    label: LABEL,
  });
  invalidateCache(BASE);
  return out;
}

export async function createDesignSpecVersion(
  id: string,
  payload: DesignSpecPayload,
  parentVersionId?: string,
): Promise<DesignSpecVersionCreatedResult> {
  const out = await apiFetch<DesignSpecVersionCreatedResult>(
    `${BASE}/${encodeURIComponent(id)}/versions`,
    {
      method: 'POST',
      body: { payload, parent_version_id: parentVersionId ?? undefined },
      label: LABEL,
    },
  );
  invalidateCache(BASE);
  return out;
}

export async function getDesignSpecVersion(
  id: string,
  version: number | 'latest' = 'latest',
): Promise<DesignSpecVersionResult> {
  const v = version === 'latest' ? 'latest' : String(version);
  return (await fastGet<DesignSpecVersionResult>(
    `${BASE}/${encodeURIComponent(id)}/versions/${encodeURIComponent(v)}`,
    { label: LABEL },
  )).data;
}

export async function publishDesignSpec(id: string): Promise<DesignSpecMutationResult> {
  const out = await apiFetch<DesignSpecMutationResult>(
    `${BASE}/${encodeURIComponent(id)}/publish`,
    { method: 'POST', body: {}, label: LABEL },
  );
  invalidateCache(BASE);
  return out;
}

export async function retireDesignSpec(id: string, note: string): Promise<DesignSpecMutationResult> {
  const out = await apiFetch<DesignSpecMutationResult>(
    `${BASE}/${encodeURIComponent(id)}/retire`,
    { method: 'POST', body: { note }, label: LABEL },
  );
  invalidateCache(BASE);
  return out;
}

export async function deprecateDesignSpecVersion(
  id: string,
  version: number,
  note: string,
): Promise<DesignSpecDeprecateResult> {
  const out = await apiFetch<DesignSpecDeprecateResult>(
    `${BASE}/${encodeURIComponent(id)}/versions/${encodeURIComponent(String(version))}/deprecate`,
    { method: 'POST', body: { note }, label: LABEL },
  );
  invalidateCache(BASE);
  return out;
}

export async function getDesignSpecPreview(
  id: string,
  version?: number | 'latest',
): Promise<DesignSpecPreview> {
  const params: Record<string, string | number> = {};
  if (version !== undefined && version !== 'latest') params.version = version;
  return (await fastGet<DesignSpecPreview>(
    `${BASE}/${encodeURIComponent(id)}/preview`,
    { params, label: LABEL },
  )).data;
}

export interface DesignSeriesError {
  code: string;
  message: string;
  violations: ReferenceValidation['violations'];
}

/**
 * 错误信封解码：HTTPException detail 被全局处理器放进响应体 `data`
 * （409 DESIGN_SPEC_NAME_CONFLICT / 400 DESIGN_SPEC_INVALID + violations[]）。
 */
export function asDesignSpecError(err: unknown): DesignSeriesError {
  const body = (err as { body?: unknown } | null)?.body;
  const rec = body && typeof body === 'object' ? (body as Record<string, unknown>) : null;
  const src = rec && typeof rec.data === 'object' && rec.data !== null
    ? (rec.data as Record<string, unknown>)
    : rec && typeof rec.detail === 'object' && rec.detail !== null
      ? (rec.detail as Record<string, unknown>)
      : null;
  if (src && typeof src.code === 'string') {
    return {
      code: src.code,
      message: String(src.message ?? src.code),
      violations: Array.isArray(src.violations)
        ? (src.violations as ReferenceValidation['violations'])
        : [],
    };
  }
  return {
    code: 'DESIGN_SERIES_REQUEST_FAILED',
    message: err instanceof Error ? err.message : String(err),
    violations: [],
  };
}
