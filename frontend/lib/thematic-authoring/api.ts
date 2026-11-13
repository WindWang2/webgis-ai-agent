/** G33 API 客户端（UI 与 Pi 共用后端领域服务；同源错误码提取）。 */
import { apiFetch } from '@/lib/api/transport';
import type {
  CommitReceipt,
  InspectReceipt,
  PlanBody,
  PreviewReceipt,
  ThematicErrorPayload,
} from './contracts';

export interface ThematicApiContext {
  sessionId: string;
  ownerToken: string | null;
  signal?: AbortSignal;
}

/** 从 HTTP 错误体提取同源领域错误码（code/field/index 定位到具体断点/类别）。 */
export function asThematicError(err: unknown): ThematicErrorPayload {
  const body = (err as { body?: unknown } | null)?.body;
  const detail = body && typeof body === 'object'
    ? (body as Record<string, unknown>).detail ?? body
    : null;
  if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>;
    if (typeof d.code === 'string' && typeof d.message === 'string') {
      return d as unknown as ThematicErrorPayload;
    }
  }
  return {
    code: 'THEMATIC_REQUEST_FAILED',
    message: err instanceof Error ? err.message : String(err),
  };
}

function base(ctx: ThematicApiContext, layerId: string): string {
  return `/api/v1/thematic-authoring/sessions/${encodeURIComponent(ctx.sessionId)}` +
    `/layers/${encodeURIComponent(layerId)}`;
}

export function inspectThematicLayer(
  ctx: ThematicApiContext, layerId: string, field?: string,
): Promise<InspectReceipt> {
  const q = field ? `?field=${encodeURIComponent(field)}` : '';
  return apiFetch<InspectReceipt>(
    `${base(ctx, layerId)}/inspect${q}`,
    { ownerToken: ctx.ownerToken, signal: ctx.signal, label: 'Thematic authoring' },
  );
}

export function previewThematicPlan(
  ctx: ThematicApiContext, layerId: string, plan: PlanBody,
): Promise<PreviewReceipt> {
  return apiFetch<PreviewReceipt>(
    `${base(ctx, layerId)}/preview`,
    {
      method: 'POST',
      body: JSON.stringify({ plan: { ...plan, layer_id: layerId } }),
      ownerToken: ctx.ownerToken,
      signal: ctx.signal,
      label: 'Thematic authoring',
    },
  );
}

export function commitThematicPlan(
  ctx: ThematicApiContext, layerId: string, plan: PlanBody,
  expectedRevision: number, clientMutationId: string,
): Promise<CommitReceipt> {
  return apiFetch<CommitReceipt>(
    `${base(ctx, layerId)}/commit`,
    {
      method: 'POST',
      body: JSON.stringify({
        plan: { ...plan, layer_id: layerId },
        expected_revision: expectedRevision,
        client_mutation_id: clientMutationId,
      }),
      ownerToken: ctx.ownerToken,
      signal: ctx.signal,
      label: 'Thematic authoring',
    },
  );
}
