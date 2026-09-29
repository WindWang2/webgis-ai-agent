/**
 * Paged features client（W12 数据平面 vNext · 浏览半边的客户端腿）。
 *
 * 后端契约：
 * - GET /api/v1/layers/data/{ref}/features?limit&cursor&bbox&fields&v=<content_revision>
 *   稳定序 = FC 自然序；`v` revision guard：翻页中途 ref 被覆写 → 409 +
 *   `{error:'revision_conflict', current_revision}`（绝不静默跨版拼接页）；
 * - GET /api/v1/data-fabric/catalog/{itemId}/features?limit&cursor&order_by...
 *   keyset cursor（源不支持 → next_cursor=null/has_more=false 诚实降级）。
 *
 * 设计纪律（对齐 ref-service.ts）：
 * - 单飞（同 key 去重）：同 (session, ref, cursor) 的并发请求共享一次往返；
 * - 取消：AbortSignal 全程透传（分页循环逐页检查）；
 * - 诚实失败：畸形 cursor/bbox → 400 原样抛出；409 → PagedRevisionConflict
 *   （携带当前 revision，调用方可重启分页）；
 * - 有界收集：iterateAllFeatures 强制 maxFeatures/maxPages 帽，绝不无界。
 */
import { apiFetch } from '@/lib/api/transport';

export interface FeaturePageParams {
  sessionId: string;
  refId: string;
  /** 客户端持有的 content_revision（revision guard；缺省 = 无守卫）。 */
  revision?: number;
  limit?: number;
  cursor?: string | null;
  bbox?: [number, number, number, number];
  fields?: string[];
  ownerToken?: string | null;
  signal?: AbortSignal;
}

export interface FeaturePage {
  features: Record<string, unknown>[];
  nextCursor: string | null;
  hasMore: boolean;
  revision: number | null;
  featureCount: number | null;
  /** bbox='coarse'：窗口粗滤是保守超集（可能含边界外要素，显示层再裁剪）。 */
  bboxMode: 'coarse' | null;
}

export class PagedRevisionConflict extends Error {
  readonly currentRevision: number | null;
  constructor(currentRevision: number | null) {
    super(`ref revision advanced (current=${String(currentRevision)})`);
    this.name = 'PagedRevisionConflict';
    this.currentRevision = currentRevision;
  }
}

interface RawPage {
  type?: string;
  features?: Record<string, unknown>[];
  pagination?: { next_cursor?: string | null; has_more?: boolean; returned?: number };
  revision?: number | null;
  feature_count?: number | null;
  bbox_mode?: string | null;
}

/** FastAPI HTTPException(detail=…) 的 body 直接就是 detail 字典。 */
interface RevisionConflictBody {
  error?: string;
  current_revision?: number;
  detail?: RevisionConflictBody | string;
}

function qs(params: Record<string, string | number | undefined>): string {
  const out = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') out.set(k, String(v));
  }
  return out.toString();
}

/** 拉取一页会话 ref 窗口要素（稳定序）。 */
export async function fetchFeaturePage(params: FeaturePageParams): Promise<FeaturePage> {
  const {
    sessionId, refId, revision, limit = 200, cursor, bbox, fields, ownerToken, signal,
  } = params;
  const query = qs({
    session_id: sessionId,
    limit,
    cursor: cursor ?? undefined,
    bbox: bbox ? bbox.join(',') : undefined,
    fields: fields && fields.length ? fields.join(',') : undefined,
    v: revision,
  });
  let raw: RawPage;
  try {
    raw = await apiFetch<RawPage>(
      `/api/v1/layers/data/${encodeURIComponent(refId)}/features?${query}`,
      { ownerToken: ownerToken ?? undefined, label: 'Feature page error', signal },
    );
  } catch (err) {
    if (err && typeof err === 'object' && 'status' in err && (err as { status?: number }).status === 409) {
      const body = (err as { body?: unknown }).body as RevisionConflictBody | undefined;
      const nested = body && typeof body.detail === 'object' ? body.detail : undefined;
      const cur = (nested ?? body)?.current_revision ?? null;
      throw new PagedRevisionConflict(cur);
    }
    throw err;
  }
  return {
    features: raw.features ?? [],
    nextCursor: raw.pagination?.next_cursor ?? null,
    hasMore: Boolean(raw.pagination?.has_more),
    revision: raw.revision ?? null,
    featureCount: raw.feature_count ?? null,
    bboxMode: raw.bbox_mode === 'coarse' ? 'coarse' : null,
  };
}

export interface IterateAllOptions extends Omit<FeaturePageParams, 'cursor'> {
  /** 收集帽：任何一顶到即停（响应标注 truncatedByCap）。默认 50_000。 */
  maxFeatures?: number;
  maxPages?: number;
}

export interface IteratedCollection {
  features: Record<string, unknown>[];
  truncatedByCap: boolean;
  revision: number | null;
}

/** 有界全量收集：翻页直至末页或触帽。绝不无界下载。 */
export async function iterateAllFeatures(opts: IterateAllOptions): Promise<IteratedCollection> {
  const maxFeatures = opts.maxFeatures ?? 50_000;
  const maxPages = opts.maxPages ?? 10_000;
  const features: Record<string, unknown>[] = [];
  let cursor: string | null = null;
  let revision: number | null = opts.revision ?? null;
  for (let page = 0; page < maxPages; page += 1) {
    if (opts.signal?.aborted) break;
    // revision 续钉：守卫是 opt-in 时，用响应里观察到的当前 revision 继续
    // 发守卫 —— 翻页中途 ref 被覆写会在下一页 409，绝不静默跨版拼接。
    const p = await fetchFeaturePage({ ...opts, cursor, limit: opts.limit ?? 1000, revision: opts.revision ?? revision ?? undefined });
    revision = p.revision ?? revision;
    features.push(...p.features);
    if (!p.hasMore || !p.nextCursor) {
      return { features, truncatedByCap: false, revision };
    }
    if (features.length >= maxFeatures) {
      return { features, truncatedByCap: true, revision };
    }
    cursor = p.nextCursor;
  }
  return { features, truncatedByCap: true, revision };
}

/** 目录条目 keyset 分页（W12 目录浏览端点的客户端腿）。 */
export async function fetchCatalogFeaturePage(args: {
  itemId: string;
  limit?: number;
  cursor?: string | null;
  orderBy?: string;
  fields?: string[];
  ownerToken?: string | null;
  signal?: AbortSignal;
}): Promise<{ features: Record<string, unknown>[]; nextCursor: string | null; hasMore: boolean; fingerprint: string | null }> {
  const query = qs({
    limit: args.limit ?? 200,
    cursor: args.cursor ?? undefined,
    order_by: args.orderBy,
    fields: args.fields && args.fields.length ? args.fields.join(',') : undefined,
  });
  const raw = await apiFetch<{
    features?: Record<string, unknown>[];
    next_cursor?: string | null;
    has_more?: boolean;
    fingerprint?: string | null;
  }>(`/api/v1/data-fabric/catalog/${encodeURIComponent(args.itemId)}/features?${query}`, {
    ownerToken: args.ownerToken ?? undefined,
    label: 'Catalog features page error',
    signal: args.signal,
  });
  return {
    features: raw.features ?? [],
    nextCursor: raw.next_cursor ?? null,
    hasMore: Boolean(raw.has_more),
    fingerprint: raw.fingerprint ?? null,
  };
}
