/**
 * 系列地图 REST 客户端 —— WP3 并行契约（planning wp3_contract_brief.md §REST）。
 *
 * 端到端联调在 WP3 合并后进行；本 client 只照契约写形状。读走 GET Fast Path，
 * mutation 后显式 invalidateCache。系列只存引用与计划（不建第二地图状态）：
 * 「在地图打开」仅展示 session_id 引用信息，不做跨会话加载。
 */
import { apiFetch } from './transport';
import { fastGet, invalidateCache } from './get-fast-path';
import type {
  ConsistencyResult,
  FreezePlanResult,
  MapSeriesDetail,
  MapSeriesMember,
  MapSeriesPayload,
  MapSeriesSummary,
  PropagationApplyResult,
  PropagationIntents,
  PropagationPreviewResult,
} from '@/lib/design-series/contracts';
import { asDesignSpecError, type DesignSeriesError } from './design-specs';

export { asDesignSpecError };
export type { DesignSeriesError };

const BASE = '/api/v1/map-series';
const LABEL = 'Map series API error';

export interface MapSeriesListResult {
  success: boolean;
  items: MapSeriesSummary[];
}

export async function listMapSeries(
  opts?: { forceRefresh?: boolean; signal?: AbortSignal },
): Promise<MapSeriesListResult> {
  return (await fastGet<MapSeriesListResult>(BASE, {
    forceRefresh: opts?.forceRefresh,
    signal: opts?.signal,
    label: LABEL,
  })).data;
}

export async function getMapSeries(id: string): Promise<MapSeriesDetail> {
  return apiFetch<MapSeriesDetail>(`${BASE}/${encodeURIComponent(id)}`, { label: LABEL });
}

export async function createMapSeries(
  payload: MapSeriesPayload,
): Promise<{ success: boolean; series: MapSeriesSummary }> {
  const out = await apiFetch<{ success: boolean; series: MapSeriesSummary }>(BASE, {
    method: 'POST',
    body: { payload },
    label: LABEL,
  });
  invalidateCache(BASE);
  return out;
}

export async function createMapSeriesVersion(
  id: string,
  payload: MapSeriesPayload,
  parentVersionId?: string,
): Promise<{ success: boolean; version: Record<string, unknown> }> {
  const out = await apiFetch<{ success: boolean; version: Record<string, unknown> }>(
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

/**
 * 单成员增/改（POST {id}/members，SeriesMemberOpRequest：
 * `{op:'upsert'|'remove', member?, member_id?}` —— app/api/routes/map_series.py）。
 */
export async function upsertMapSeriesMember(
  id: string,
  member: MapSeriesMember,
): Promise<{ success: boolean; op: string; member_count?: number }> {
  const out = await apiFetch<{ success: boolean; op: string; member_count?: number }>(
    `${BASE}/${encodeURIComponent(id)}/members`,
    { method: 'POST', body: { op: 'upsert', member }, label: LABEL },
  );
  invalidateCache(BASE);
  return out;
}

/** 单成员删（同端点，`{op:'remove', member_id}`）。 */
export async function removeMapSeriesMember(
  id: string,
  memberId: string,
): Promise<{ success: boolean; op: string; member_id?: string }> {
  const out = await apiFetch<{ success: boolean; op: string; member_id?: string }>(
    `${BASE}/${encodeURIComponent(id)}/members`,
    { method: 'POST', body: { op: 'remove', member_id: memberId }, label: LABEL },
  );
  invalidateCache(BASE);
  return out;
}

export async function getMapSeriesConsistency(id: string): Promise<ConsistencyResult> {
  return (await fastGet<ConsistencyResult>(
    `${BASE}/${encodeURIComponent(id)}/consistency`,
    { forceRefresh: true, label: LABEL },
  )).data;
}

export async function previewPropagation(
  id: string,
  body: { spec_version?: number | null; intents?: PropagationIntents },
): Promise<PropagationPreviewResult> {
  return apiFetch<PropagationPreviewResult>(
    `${BASE}/${encodeURIComponent(id)}/propagation/preview`,
    { method: 'POST', body, label: LABEL },
  );
}

export async function applyPropagation(
  id: string,
  body: { spec_version?: number | null; intents?: PropagationIntents },
): Promise<PropagationApplyResult> {
  return apiFetch<PropagationApplyResult>(
    `${BASE}/${encodeURIComponent(id)}/propagation/apply`,
    { method: 'POST', body, label: LABEL },
  );
}

/** 本期只返回冻结计划 JSON（WP5 才做生产执行）。 */
export async function freezeMapSeriesPlan(
  id: string,
  body: { spec_version?: number | null } = {},
): Promise<FreezePlanResult> {
  return apiFetch<FreezePlanResult>(
    `${BASE}/${encodeURIComponent(id)}/freeze-plan`,
    { method: 'POST', body, label: LABEL },
  );
}
