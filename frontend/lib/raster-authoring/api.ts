/** G20 API 客户端（UI 与 Pi 共用后端领域服务；URL 不含 owner token）。 */
import { apiFetch } from '@/lib/api/transport';
import type {
  RasterDescriptor,
  RasterErrorPayload,
  RasterExportResult,
  RasterExpressionDraft,
  RasterProbeResult,
  RasterReceipt,
} from './contracts';
import { buildExpressionRequest } from './contracts';

export interface RasterApiContext {
  sessionId: string;
  ownerToken: string | null;
  signal?: AbortSignal;
}

function q(ctx: RasterApiContext): string {
  return `?session_id=${encodeURIComponent(ctx.sessionId)}`;
}

function opts(ctx: RasterApiContext) {
  return { ownerToken: ctx.ownerToken, signal: ctx.signal, label: 'Raster authoring' };
}

/** 从 HTTP 错误体提取同源领域错误码；不泄漏服务器路径。 */
export function asRasterError(err: unknown): RasterErrorPayload {
  const body = (err as { body?: unknown } | null)?.body;
  const detail = body && typeof body === 'object'
    ? (body as Record<string, unknown>).detail ?? body
    : null;
  if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>;
    if (typeof d.code === 'string' && typeof d.message === 'string') {
      return d as unknown as RasterErrorPayload;
    }
  }
  return {
    success: false,
    code: 'RENDER_FAILED',
    message: err instanceof Error ? err.message : String(err),
  };
}

export function listRasterRefs(ctx: RasterApiContext) {
  return apiFetch<{
    success: boolean;
    refs: Array<{ ref_id: string; alias: string; kind: string | null; verified: boolean }>;
    truncated: boolean;
  }>(`/api/v1/raster-authoring/refs${q(ctx)}`, opts(ctx));
}

export function fetchDescriptor(ctx: RasterApiContext, refId: string) {
  return apiFetch<RasterDescriptor>(
    `/api/v1/raster-authoring/descriptor/${encodeURIComponent(refId)}${q(ctx)}`,
    opts(ctx),
  );
}

export function previewExpression(
  ctx: RasterApiContext,
  refId: string,
  draft: RasterExpressionDraft,
  layerId: string,
) {
  return apiFetch<RasterReceipt>(
    `/api/v1/raster-authoring/expression/preview${q(ctx)}`,
    {
      ...opts(ctx), method: 'POST',
      body: { ref_id: refId, layer_id: layerId, expression: buildExpressionRequest(draft) },
    },
  );
}

export function commitExpression(
  ctx: RasterApiContext,
  refId: string,
  layerId: string,
  draft: RasterExpressionDraft,
  expectedRevision: number,
) {
  return apiFetch<RasterReceipt>(
    `/api/v1/raster-authoring/expression/commit${q(ctx)}`,
    {
      ...opts(ctx), method: 'POST',
      body: {
        ref_id: refId,
        layer_id: layerId,
        expression: buildExpressionRequest(draft),
        expected_revision: expectedRevision,
        client_mutation_id: `raster-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      },
    },
  );
}

export function probePixels(
  ctx: RasterApiContext,
  refId: string,
  points: Array<[number, number]>,
  crs: string,
  bands: number[],
  dataRevision?: string,
) {
  return apiFetch<RasterProbeResult>(
    `/api/v1/raster-authoring/probe${q(ctx)}`,
    {
      ...opts(ctx), method: 'POST',
      body: {
        ref_id: refId, points, crs, bands,
        ...(dataRevision ? { data_revision: dataRevision } : {}),
      },
    },
  );
}

export function exportExpressionMap(
  ctx: RasterApiContext,
  expectedRevision: number,
  format: 'png' | 'svg' | 'pdf' = 'png',
) {
  return apiFetch<RasterExportResult>(
    `/api/v1/raster-authoring/export${q(ctx)}`,
    {
      ...opts(ctx), method: 'POST',
      body: { expected_revision: expectedRevision, format },
    },
  );
}
