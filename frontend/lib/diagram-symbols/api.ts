/** GOAL-20261126-04 API 客户端（UI 与 Pi 共用后端 diagram_symbol_authoring；同源错误码提取）。 */
import { apiFetch } from '@/lib/api/transport';

export interface DiagramApiContext {
  sessionId: string;
  ownerToken: string | null;
  signal?: AbortSignal;
}

export interface DiagramErrorPayload {
  code: string;
  message: string;
  field?: string;
  hint?: string;
}

/** 从 HTTP 错误体提取同源领域错误（diagnostics[0] 携带 code/field/hint）。 */
export function asDiagramError(err: unknown): DiagramErrorPayload {
  const body = (err as { body?: unknown } | null)?.body;
  const detail = body && typeof body === 'object'
    ? (body as Record<string, unknown>).detail ?? body
    : null;
  if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>;
    const diags = d.diagnostics;
    if (Array.isArray(diags) && diags.length > 0) {
      const first = diags[0] as Record<string, unknown>;
      return {
        code: typeof first.code === 'string' ? first.code : 'DIAGRAM_REQUEST_FAILED',
        message: typeof first.detail === 'string' ? first.detail : String(first.detail ?? ''),
        field: typeof first.field === 'string' ? first.field : undefined,
        hint: typeof first.hint === 'string' ? first.hint : undefined,
      };
    }
    if (typeof d.code === 'string') {
      return d as unknown as DiagramErrorPayload;
    }
  }
  return {
    code: 'DIAGRAM_REQUEST_FAILED',
    message: err instanceof Error ? err.message : String(err),
  };
}

function base(ctx: DiagramApiContext, layerId: string): string {
  return `/api/v1/diagram-symbols/sessions/${encodeURIComponent(ctx.sessionId)}` +
    `/layers/${encodeURIComponent(layerId)}`;
}

export interface DiagramInspectReceipt {
  status: string;
  layer_id: string;
  source?: string;
  features_total?: number;
  features_truncated?: boolean;
  geometry_counts?: Record<string, number>;
  available_fields?: string[];
  existing_block?: Record<string, unknown> | null;
  has_existing_block?: boolean;
}

export interface DiagramPlanReceipt {
  status: string;
  layer_id: string;
  block?: Record<string, unknown>;
  plan?: {
    qualification?: { evidence?: Record<string, unknown> };
    scale?: Record<string, number>;
    layout?: {
      schema: string;
      glyphs?: Array<Record<string, unknown>>;
      evidence?: Record<string, unknown>;
    };
    legend?: {
      categories?: Array<{ category: string; color_slot: number }>;
      size_reference?: Record<string, unknown>;
      notes_zh?: string[];
      palette_id?: string;
      unit?: string | null;
    };
    geometry_sample?: Array<Record<string, unknown>>;
    block_fingerprint?: string;
  };
}

export interface DiagramApplyReceipt {
  status: string;
  operation_id?: string;
  mapspec_revision?: number;
  error?: string;
  evidence?: Record<string, unknown> | null;
  block_fingerprint?: string | null;
}

export function inspectDiagramLayer(
  ctx: DiagramApiContext, layerId: string,
): Promise<DiagramInspectReceipt> {
  return apiFetch<DiagramInspectReceipt>(
    `${base(ctx, layerId)}/inspect`,
    { ownerToken: ctx.ownerToken, signal: ctx.signal, label: 'Diagram symbols' },
  );
}

export function planDiagramSymbol(
  ctx: DiagramApiContext, layerId: string, block: Record<string, unknown>,
  layoutPolicy?: Record<string, unknown>,
): Promise<DiagramPlanReceipt> {
  return apiFetch<DiagramPlanReceipt>(
    `${base(ctx, layerId)}/plan`,
    {
      method: 'POST',
      body: { block, layout_policy: layoutPolicy ?? null },
      ownerToken: ctx.ownerToken,
      signal: ctx.signal,
      label: 'Diagram symbols',
    },
  );
}

export function applyDiagramSymbol(
  ctx: DiagramApiContext, layerId: string, block: Record<string, unknown> | null,
  mode: 'preview' | 'commit', expectedRevision: number, operationId: string,
): Promise<DiagramApplyReceipt> {
  return apiFetch<DiagramApplyReceipt>(
    `${base(ctx, layerId)}/apply`,
    {
      method: 'POST',
      body: {
        block,
        mode,
        expected_revision: expectedRevision,
        operation_id: operationId,
      },
      ownerToken: ctx.ownerToken,
      signal: ctx.signal,
      label: 'Diagram symbols',
    },
  );
}
