/** G41 API 客户端（UI 与 Pi 共用后端领域服务；同源错误码提取）。 */
import { apiFetch } from '@/lib/api/transport';
import type {
  CommitReceipt,
  G41ErrorPayload,
  InspectReceipt,
  PlanBody,
  PreviewReceipt,
} from './contracts';

export interface G41ApiContext {
  sessionId: string;
  ownerToken: string | null;
  signal?: AbortSignal;
}

/** 从 HTTP 错误体提取同源领域错误码（code/field/index 定位到轴/区间/字段）。 */
export function asG41Error(err: unknown): G41ErrorPayload {
  const body = (err as { body?: unknown } | null)?.body;
  const detail = body && typeof body === 'object'
    ? (body as Record<string, unknown>).detail ?? body
    : null;
  if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>;
    if (typeof d.code === 'string' && typeof d.message === 'string') {
      return d as unknown as G41ErrorPayload;
    }
  }
  return {
    code: 'G41_REQUEST_FAILED',
    message: err instanceof Error ? err.message : String(err),
  };
}

function base(ctx: G41ApiContext, layerId: string): string {
  return `/api/v1/bivariate-reliability/sessions/${encodeURIComponent(ctx.sessionId)}` +
    `/layers/${encodeURIComponent(layerId)}`;
}

export function inspectG41Layer(
  ctx: G41ApiContext, layerId: string,
): Promise<InspectReceipt> {
  return apiFetch<InspectReceipt>(
    `${base(ctx, layerId)}/inspect`,
    { ownerToken: ctx.ownerToken, signal: ctx.signal, label: 'Bivariate / reliability authoring' },
  );
}

export function previewG41Plan(
  ctx: G41ApiContext, layerId: string, plan: PlanBody,
): Promise<PreviewReceipt> {
  return apiFetch<PreviewReceipt>(
    `${base(ctx, layerId)}/preview`,
    {
      method: 'POST',
      body: { plan: { ...plan, layer_id: layerId } },
      ownerToken: ctx.ownerToken,
      signal: ctx.signal,
      label: 'Bivariate / reliability authoring',
    },
  );
}

export function commitG41Plan(
  ctx: G41ApiContext, layerId: string, plan: PlanBody,
  expectedRevision: number, previewDigest: string, clientMutationId: string,
): Promise<CommitReceipt> {
  return apiFetch<CommitReceipt>(
    `${base(ctx, layerId)}/commit`,
    {
      method: 'POST',
      body: {
        plan: { ...plan, layer_id: layerId },
        expected_revision: expectedRevision,
        preview_digest: previewDigest,
        client_mutation_id: clientMutationId,
      },
      ownerToken: ctx.ownerToken,
      signal: ctx.signal,
      label: 'Bivariate / reliability authoring',
    },
  );
}
