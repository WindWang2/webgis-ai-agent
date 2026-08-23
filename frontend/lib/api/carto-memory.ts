/**
 * Project cartographic memory API — 项目制图事实账本的治理接口
 * (ADR-0069 / spec 开放问题 2)。
 *
 * 记忆是先验不是证据：这里只提供查看/撤销/显式激活三个人工治理动作，
 * 不存在任何"凭记忆改评审"的入口。GET 走 Fast Path（同项目面板反复
 * 展开不重复请求）。
 */

import { apiFetch } from './transport';
import { fastGet, invalidateCache } from './get-fast-path';

const LABEL = 'Carto memory API error';

export type CartoFactKind =
  | 'preference'
  | 'recipe_outcome'
  | 'data_profile'
  | 'shared_classification';

export type CartoFactStatus = 'active' | 'stale' | 'conflicted' | 'retired';

export interface CartoFact {
  id: string;
  kind: CartoFactKind;
  subject: string;
  payload: Record<string, unknown>;
  fingerprint: string | null;
  validity_tier: string | null;
  status: CartoFactStatus;
  created_at: string | null;
  last_verified_at: string | null;
}

export interface CartoMemoryOverview {
  project_id: string;
  counts: Record<CartoFactStatus, number>;
  facts: CartoFact[];
}

/** GET /api/v1/projects/{id}/carto-memory — 管理视图（含非 active）。 */
export async function getCartoMemory(
  projectId: string,
  opts?: { signal?: AbortSignal },
): Promise<CartoMemoryOverview> {
  const result = await fastGet<CartoMemoryOverview>(`/api/v1/projects/${projectId}/carto-memory`, {
    signal: opts?.signal,
    label: LABEL,
  });
  return result.data;
}

/** DELETE …/carto-memory/{factId} — 撤销（软删 retired）。 */
export async function retireCartoFact(projectId: string, factId: string): Promise<CartoFact> {
  const { fact } = await apiFetch<{ fact: CartoFact }>(
    `/api/v1/projects/${projectId}/carto-memory/${factId}`,
    { method: 'DELETE', label: LABEL },
  );
  invalidateCache(`/api/v1/projects/${projectId}/carto-memory`);
  return fact;
}

/** POST …/carto-memory/{factId}/activate — 显式（重）激活（人工裁决）。 */
export async function activateCartoFact(projectId: string, factId: string): Promise<CartoFact> {
  const { fact } = await apiFetch<{ fact: CartoFact }>(
    `/api/v1/projects/${projectId}/carto-memory/${factId}/activate`,
    { method: 'POST', label: LABEL },
  );
  invalidateCache(`/api/v1/projects/${projectId}/carto-memory`);
  return fact;
}
