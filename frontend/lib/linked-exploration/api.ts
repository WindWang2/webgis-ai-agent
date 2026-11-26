/**
 * G46 联合探索 API client —— 与后端 /api/v1/linked-exploration 一一对应。
 *
 * 错误统一携带后端 typed code（LinkedExplorationError payload）。
 */
import { apiFetch } from '../api/transport';
import type {
  ChartSpec,
  DrillResponse,
  ExplorationCondition,
  ExplorationPlanJson,
  ExplorationView,
  InspectResponse,
  MultiSourcePlanJson,
  OpenViewResponse,
  PlanPreviewResponse,
  PreviewResponse,
} from './contracts';

const BASE = '/linked-exploration';

export interface LinkedExplorationApiError {
  code: string;
  message: string;
  hint?: string;
}

export function asLinkedExplorationError(err: unknown): LinkedExplorationApiError {
  const anyErr = err as {
    body?: { detail?: { code?: string; message?: string; correction_hint?: string } };
    message?: string;
  };
  const detail = anyErr?.body?.detail;
  if (detail && typeof detail === 'object' && 'code' in detail) {
    return {
      code: detail.code ?? 'unknown',
      message: detail.message ?? '请求失败',
      hint: detail.correction_hint,
    };
  }
  return { code: 'network_error', message: anyErr?.message ?? String(err) };
}

type Options = { method?: string; body?: unknown; ownerToken?: string | null; signal?: AbortSignal };

function opts(o: Options): Options {
  return o;
}

function chartToPayload(c: ChartSpec): Record<string, unknown> {
  return {
    chart_id: c.chartId,
    kind: c.kind,
    field: c.field,
    aggregation: c.aggregation,
    value_field: c.valueField ?? null,
    title: c.title,
    bins: c.bins,
    layer_id: c.layerId ?? null,
  };
}

function conditionToPayload(c: ExplorationCondition): Record<string, unknown> {
  if (c.kind === 'category') {
    return {
      kind: 'category',
      condition_id: c.conditionId,
      chart_id: c.chartId,
      field: c.field,
      values: c.values,
      include_null: c.includeNull,
      ...(c.labels ? { labels: c.labels } : {}),
    };
  }
  const out: Record<string, unknown> = {
    kind: 'range',
    condition_id: c.conditionId,
    chart_id: c.chartId,
    field: c.field,
  };
  if (c.gte !== undefined) out.gte = c.gte;
  if (c.gt !== undefined) out.gt = c.gt;
  if (c.lte !== undefined) out.lte = c.lte;
  if (c.lt !== undefined) out.lt = c.lt;
  return out;
}

export function createLinkedExplorationClient(ownerToken?: string | null) {
  const q = (sessiondId: string) => `session_id=${encodeURIComponent(sessiondId)}`;
  return {
    inspect: (sessionId: string, ref: string, signal?: AbortSignal) =>
      apiFetch<InspectResponse>(`${BASE}/inspect?${q(sessionId)}`,
        opts({ method: 'POST', body: { ref }, ownerToken, signal })),
    preview: (
      sessionId: string,
      ref: string,
      charts: ChartSpec[],
      conditions: ExplorationCondition[],
      signal?: AbortSignal,
      createHandle = true,
    ) =>
      apiFetch<PreviewResponse>(`${BASE}/preview?${q(sessionId)}`,
        opts({
          method: 'POST',
          body: {
            ref,
            charts: charts.map(chartToPayload),
            conditions: conditions.map(conditionToPayload),
            create_handle: createHandle,
          },
          ownerToken,
          signal,
        })),
    listViews: (sessionId: string, signal?: AbortSignal) =>
      apiFetch<{ views: ExplorationView[] }>(`${BASE}/views?${q(sessionId)}`,
        opts({ ownerToken, signal })),
    saveView: (sessionId: string, title: string, plan: ExplorationPlanJson) =>
      apiFetch<ExplorationView>(`${BASE}/views?${q(sessionId)}`,
        opts({ method: 'POST', body: { title, plan }, ownerToken })),
    getView: (sessionId: string, viewId: string, signal?: AbortSignal) =>
      apiFetch<ExplorationView>(`${BASE}/views/${encodeURIComponent(viewId)}?${q(sessionId)}`,
        opts({ ownerToken, signal })),
    updateView: (
      sessionId: string,
      viewId: string,
      body: { title?: string; plan?: ExplorationPlanJson; status?: string; expected_revision: number },
    ) =>
      apiFetch<ExplorationView>(`${BASE}/views/${encodeURIComponent(viewId)}?${q(sessionId)}`,
        opts({ method: 'PATCH', body, ownerToken })),
    deleteView: (sessionId: string, viewId: string, expectedRevision: number) =>
      apiFetch<{ deleted: boolean }>(
        `${BASE}/views/${encodeURIComponent(viewId)}?${q(sessionId)}&expected_revision=${expectedRevision}`,
        opts({ method: 'DELETE', ownerToken })),
    openView: (sessionId: string, viewId: string, signal?: AbortSignal) =>
      apiFetch<OpenViewResponse>(`${BASE}/views/${encodeURIComponent(viewId)}/open?${q(sessionId)}`,
        opts({ method: 'POST', ownerToken, signal })),
    applyView: (sessionId: string, viewId: string, expectedRevision?: number) =>
      apiFetch<{ applied_components: string[]; count: number; state: string }>(
        `${BASE}/views/${encodeURIComponent(viewId)}/apply?${q(sessionId)}`,
        opts({ method: 'POST', body: { expected_revision: expectedRevision ?? null }, ownerToken })),
    planPreview: (
      sessionId: string,
      plan: MultiSourcePlanJson,
      signal?: AbortSignal,
      createHandle = true,
    ) =>
      apiFetch<PlanPreviewResponse>(`${BASE}/plan-preview?${q(sessionId)}`,
        opts({ method: 'POST', body: { plan, create_handle: createHandle }, ownerToken, signal })),
    drill: (
      sessionId: string,
      args: { plan: MultiSourcePlanJson; source_id: string; direction: 'down' | 'up'; parent_value?: unknown },
      signal?: AbortSignal,
    ) =>
      apiFetch<DrillResponse>(`${BASE}/drill?${q(sessionId)}`,
        opts({ method: 'POST', body: { ...args, create_handle: false }, ownerToken, signal })),
  };
}

export type LinkedExplorationClient = ReturnType<typeof createLinkedExplorationClient>;
