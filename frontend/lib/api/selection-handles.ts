/**
 * selection-handles client（G24 / P3 客户端腿）。
 *
 * 后端契约：POST/GET/DELETE /api/v1/layers/data/{ref}/selection[...]。
 * 语义纪律：
 * - handle 是服务端全集的**寻址**，不是权限 —— 每次调用都带会话身份；
 * - 410 selection_stale/expired → typed 错误（数据已刷新/空闲超时，需重建
 *   选择，绝不静默复用旧 handle —— A22）；
 * - count/count_exact 如实透传：未知总数不得用已返回行数假冒（A13）。
 */
import { apiFetch } from '@/lib/api/transport';

export type SelectionScope =
  | { kind: 'bbox'; bbox: [number, number, number, number] }
  | { kind: 'where'; where: unknown }
  | { kind: 'ids'; ids: string[] };

export interface SelectionHandleMeta {
  handle: string;
  count: number | null;
  count_exact: boolean;
  revision: number | null;
  scope: Record<string, unknown>;
  missing_ids?: string[];
  ttl_seconds: number;
}

export class SelectionHandleStale extends Error {
  readonly currentRevision: number | null;
  readonly code: 'stale' | 'expired';
  constructor(code: 'stale' | 'expired', currentRevision: number | null) {
    super(code === 'stale' ? 'selection handle stale (revision advanced)' : 'selection handle expired');
    this.name = 'SelectionHandleStale';
    this.code = code;
    this.currentRevision = currentRevision;
  }
}

interface HandleErrorBody {
  detail?: { error?: string; current_revision?: number; handle_revision?: number };
}

function _rethrowSelectionError(err: unknown): never {
  if (err && typeof err === 'object' && 'status' in err) {
    const status = (err as { status?: number }).status;
    const body = (err as { body?: unknown }).body as HandleErrorBody | undefined;
    const code = body?.detail?.error;
    if (status === 410) {
      throw new SelectionHandleStale(
        code === 'selection_expired' ? 'expired' : 'stale',
        body?.detail?.current_revision ?? null,
      );
    }
  }
  throw err;
}

export async function createSelectionHandle(params: {
  sessionId: string;
  refId: string;
  scope: SelectionScope;
  revision?: number | null;
  ownerToken?: string | null;
  signal?: AbortSignal;
}): Promise<SelectionHandleMeta> {
  const { sessionId, refId, scope, revision, ownerToken, signal } = params;
  const q = new URLSearchParams({ session_id: sessionId });
  if (typeof revision === 'number') q.set('v', String(revision));
  try {
    return await apiFetch<SelectionHandleMeta>(
      `/api/v1/layers/data/${encodeURIComponent(refId)}/selection?${q}`,
      {
        method: 'POST',
        body: JSON.stringify(scope),
        ownerToken: ownerToken ?? undefined,
        label: 'Selection handle error',
        signal,
      },
    );
  } catch (err) {
    _rethrowSelectionError(err);
  }
}

export interface SelectionHandleFeaturePage {
  features: Record<string, unknown>[];
  nextCursor: string | null;
  hasMore: boolean;
  isComplete: boolean;
  truncated: boolean;
  count: number | null;
  countExact: boolean;
  revision: number | null;
  reasonCodes: string[];
}

export async function fetchSelectionFeatures(params: {
  sessionId: string;
  refId: string;
  handle: string;
  limit?: number;
  cursor?: string | null;
  fields?: string[];
  order?: string;
  ownerToken?: string | null;
  signal?: AbortSignal;
}): Promise<SelectionHandleFeaturePage> {
  const { sessionId, refId, handle, limit = 200, cursor, fields, order, ownerToken, signal } = params;
  const q = new URLSearchParams({ session_id: sessionId, limit: String(limit) });
  if (cursor) q.set('cursor', cursor);
  if (fields?.length) q.set('fields', fields.join(','));
  if (order) q.set('order', order);
  let raw: {
    features?: Record<string, unknown>[];
    pagination?: { next_cursor?: string | null; has_more?: boolean; truncated?: boolean; is_complete?: boolean };
    selection?: { count?: number | null; count_exact?: boolean };
    revision?: number | null;
    reason_codes?: string[];
  };
  try {
    raw = await apiFetch(
      `/api/v1/layers/data/${encodeURIComponent(refId)}/selection/${encodeURIComponent(handle)}/features?${q}`,
      { ownerToken: ownerToken ?? undefined, label: 'Selection page error', signal },
    );
  } catch (err) {
    _rethrowSelectionError(err);
  }
  return {
    features: raw.features ?? [],
    nextCursor: raw.pagination?.next_cursor ?? null,
    hasMore: Boolean(raw.pagination?.has_more),
    isComplete: raw.pagination?.is_complete ?? !raw.pagination?.has_more,
    truncated: Boolean(raw.pagination?.truncated),
    count: raw.selection?.count ?? null,
    countExact: Boolean(raw.selection?.count_exact),
    revision: raw.revision ?? null,
    reasonCodes: raw.reason_codes ?? [],
  };
}

export async function materializeSelectionHandle(params: {
  sessionId: string;
  refId: string;
  handle: string;
  ownerToken?: string | null;
  signal?: AbortSignal;
}): Promise<{ ref_id: string; feature_count: number; coarse_bbox: boolean; revision: number | null }> {
  const { sessionId, refId, handle, ownerToken, signal } = params;
  const q = new URLSearchParams({ session_id: sessionId });
  try {
    return await apiFetch(
      `/api/v1/layers/data/${encodeURIComponent(refId)}/selection/${encodeURIComponent(handle)}/materialize?${q}`,
      { method: 'POST', ownerToken: ownerToken ?? undefined, label: 'Selection materialize error', signal },
    );
  } catch (err) {
    _rethrowSelectionError(err);
  }
}

export function releaseSelectionHandle(params: {
  sessionId: string;
  refId: string;
  handle: string;
  ownerToken?: string | null;
  signal?: AbortSignal;
}): Promise<unknown> {
  const { sessionId, refId, handle, ownerToken, signal } = params;
  const q = new URLSearchParams({ session_id: sessionId });
  return apiFetch(
    `/api/v1/layers/data/${encodeURIComponent(refId)}/selection/${encodeURIComponent(handle)}?${q}`,
    { method: 'DELETE', ownerToken: ownerToken ?? undefined, label: 'Selection release error', signal },
  ).catch(() => undefined); // 释放是收敛动作：失败静默（服务端 TTL 兜底）
}
