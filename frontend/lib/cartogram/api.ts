/** G47 API 客户端（UI 与 Pi 共用后端 `cartogram_authoring` 领域服务）。 */
import { apiFetch } from '@/lib/api/transport';
import type {
  ApplyReceipt,
  CartogramLayout,
  DorlingPlan,
  InspectReceipt,
  JobStatusReceipt,
  PlanReceipt,
  RunReceipt,
} from './contracts';

export interface G47ApiContext {
  sessionId: string;
  ownerToken: string | null;
  signal?: AbortSignal;
}

export interface G47ErrorPayload {
  code: string;
  message: string;
  field?: string;
  correction_hint?: string;
  details?: Record<string, unknown>;
  status_code?: number;
}

export function asG47Error(err: unknown): G47ErrorPayload {
  const body = (err as { body?: unknown } | null)?.body;
  const detail = body && typeof body === 'object'
    ? (body as Record<string, unknown>).detail ?? body
    : null;
  if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>;
    if (typeof d.code === 'string' && typeof d.message === 'string') {
      return d as unknown as G47ErrorPayload;
    }
  }
  return {
    code: 'G47_REQUEST_FAILED',
    message: err instanceof Error ? err.message : String(err),
  };
}

function base(ctx: G47ApiContext, layerId: string): string {
  return `/api/v1/cartogram/sessions/${encodeURIComponent(ctx.sessionId)}`
    + `/layers/${encodeURIComponent(layerId)}`;
}

function sessionBase(ctx: G47ApiContext): string {
  return `/api/v1/cartogram/sessions/${encodeURIComponent(ctx.sessionId)}`;
}

export function inspectDorlingLayer(
  ctx: G47ApiContext, layerId: string,
): Promise<InspectReceipt> {
  return apiFetch<InspectReceipt>(`${base(ctx, layerId)}/inspect`, {
    ownerToken: ctx.ownerToken, signal: ctx.signal, label: 'Dorling cartogram',
  });
}

export function planDorling(
  ctx: G47ApiContext, layerId: string, plan: DorlingPlan,
): Promise<PlanReceipt> {
  return apiFetch<PlanReceipt>(`${base(ctx, layerId)}/plan`, {
    method: 'POST',
    body: { plan: { ...plan, layer_id: layerId } },
    ownerToken: ctx.ownerToken, signal: ctx.signal, label: 'Dorling cartogram',
  });
}

export function runDorling(
  ctx: G47ApiContext, layerId: string, plan: DorlingPlan,
): Promise<RunReceipt> {
  return apiFetch<RunReceipt>(`${base(ctx, layerId)}/run`, {
    method: 'POST',
    body: { plan: { ...plan, layer_id: layerId } },
    ownerToken: ctx.ownerToken, signal: ctx.signal, label: 'Dorling cartogram',
  });
}

export function dorlingJobStatus(
  ctx: G47ApiContext, jobId: number,
): Promise<JobStatusReceipt> {
  return apiFetch<JobStatusReceipt>(`${sessionBase(ctx)}/jobs/${jobId}`, {
    ownerToken: ctx.ownerToken, signal: ctx.signal, label: 'Dorling cartogram',
  });
}

export function fetchDorlingLayout(
  ctx: G47ApiContext, layoutRef: string,
): Promise<CartogramLayout> {
  return apiFetch<CartogramLayout>(
    `${sessionBase(ctx)}/layouts/${encodeURIComponent(layoutRef)}?full=true`, {
      ownerToken: ctx.ownerToken, signal: ctx.signal, label: 'Dorling cartogram',
    },
  );
}

export function applyDorlingLayout(
  ctx: G47ApiContext, layerId: string, args: {
    layoutRef: string;
    expectedRevision: number;
    allowDiagnostic?: boolean;
    acknowledgeStaleSource?: boolean;
    clientMutationId?: string;
  },
): Promise<ApplyReceipt> {
  return apiFetch<ApplyReceipt>(`${base(ctx, layerId)}/apply`, {
    method: 'POST',
    body: {
      layout_ref: args.layoutRef,
      expected_revision: args.expectedRevision,
      allow_diagnostic: Boolean(args.allowDiagnostic),
      acknowledge_stale_source: Boolean(args.acknowledgeStaleSource),
      client_mutation_id: args.clientMutationId,
    },
    ownerToken: ctx.ownerToken, signal: ctx.signal, label: 'Dorling cartogram',
  });
}

export function cancelDorlingJob(
  ctx: G47ApiContext, jobId: number,
): Promise<unknown> {
  return apiFetch(`/api/v1/tasks/jobs/${jobId}`, {
    method: 'DELETE',
    ownerToken: ctx.ownerToken, label: 'Dorling cartogram cancel',
  });
}

/** 导出是受权下载：返回同源 URL（浏览器带 cookie/token 由既有下载路径处理）。 */
export function dorlingExportUrl(
  ctx: G47ApiContext, layoutRef: string, format: 'svg' | 'png' | 'pdf',
  dpi = 96,
): string {
  return `${sessionBase(ctx)}/layouts/${encodeURIComponent(layoutRef)}`
    + `/export?format=${format}&dpi=${dpi}`;
}
