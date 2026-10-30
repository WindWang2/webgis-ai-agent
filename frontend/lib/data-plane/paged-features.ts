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
  /** 服务端单字段排序：'<field>' / '-<field>'（缺省 = FC 自然序）。 */
  order?: string;
  /** typed 谓词 AST（JSON 字符串；服务端 allowlist 校验，畸形 → 400）。 */
  where?: string;
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
  /** filter/bbox 的精确匹配总数；null = 未知（绝不以已返回数假冒）。 */
  totalMatching: number | null;
  isComplete: boolean;
  /** 本页因扫描预算停止且未集满（页密度被削减的显式信号）。 */
  truncated: boolean;
  reasonCodes: string[];
  /** 稳定 id 披露：native（全链内 id）/ positional（全位置 id）/ mixed。 */
  idMode: 'native' | 'positional' | 'mixed' | null;
  duplicateIdCount: number | null;
}

/** typed 谓词 AST（客户端构造子 —— 与服务端 allowlist 对齐）。 */
export type FilterAst =
  | { field: string; op: 'eq' | 'ne'; value: string | number | boolean | null }
  | { field: string; op: 'in'; values: Array<string | number> }
  | { field: string; op: 'between'; gte: number; lte: number }
  | { field: string; op: 'gte' | 'gt' | 'lte' | 'lt'; value: number }
  | { field: string; op: 'contains' | 'startswith'; value: string }
  | { op: 'and' | 'or'; terms: FilterAst[] }
  | { op: 'not'; term: FilterAst };

/** 搜索框文本 × 列集 → or-contains AST（列数有界，服务端 ≤16 terms 门）。 */
export function buildSearchFilter(text: string, columns: string[]): string | undefined {
  const needle = text.trim();
  if (!needle) return undefined;
  const fields = columns.filter((c) => c && !c.startsWith('_')).slice(0, 12);
  if (!fields.length) return undefined;
  const ast: FilterAst = {
    op: 'or',
    terms: fields.map((field) => ({ field, op: 'contains' as const, value: needle })),
  };
  return JSON.stringify(ast);
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
  pagination?: { next_cursor?: string | null; has_more?: boolean; returned?: number; truncated?: boolean; is_complete?: boolean };
  revision?: number | null;
  feature_count?: number | null;
  bbox_mode?: string | null;
  total_matching?: number | null;
  reason_codes?: string[];
  id_mode?: string | null;
  duplicate_id_count?: number | null;
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

/** 拉取一页会话 ref 窗口要素（稳定序/排序序）。 */
export async function fetchFeaturePage(params: FeaturePageParams): Promise<FeaturePage> {
  const {
    sessionId, refId, revision, limit = 200, cursor, bbox, fields, order, where, ownerToken, signal,
  } = params;
  const query = qs({
    session_id: sessionId,
    limit,
    cursor: cursor ?? undefined,
    bbox: bbox ? bbox.join(',') : undefined,
    fields: fields && fields.length ? fields.join(',') : undefined,
    order,
    where,
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
    totalMatching: raw.total_matching ?? null,
    isComplete: raw.pagination?.is_complete ?? !raw.pagination?.has_more,
    truncated: Boolean(raw.pagination?.truncated),
    reasonCodes: raw.reason_codes ?? [],
    idMode: raw.id_mode === 'native' || raw.id_mode === 'positional' || raw.id_mode === 'mixed'
      ? raw.id_mode
      : null,
    duplicateIdCount: raw.duplicate_id_count ?? null,
  };
}

// ─── 分组聚合（视口/过滤统计的服务端真值） ──────────────────────────────────

export interface LayerAggregateParams {
  sessionId: string;
  refId: string;
  groupBy: string;
  agg?: 'count' | 'sum' | 'mean' | 'min' | 'max';
  aggField?: string;
  bbox?: [number, number, number, number];
  where?: string;
  maxGroups?: number;
  revision?: number;
  ownerToken?: string | null;
  signal?: AbortSignal;
}

export interface AggregateRow {
  key: string | number | null;
  count: number;
  sum?: number | null;
  mean?: number | null;
  min?: number | null;
  max?: number | null;
}

export interface LayerAggregate {
  rows: AggregateRow[];
  groupCount: number;
  isComplete: boolean;
  truncated: boolean;
  scanned: number;
  totalMatching: number;
  nullKeyCount: number;
  nonNumericCount: number;
  revision: number | null;
  reasonCodes: string[];
}

export class AggregateOverLimit extends Error {
  constructor() {
    super('aggregate over scan limit');
    this.name = 'AggregateOverLimit';
  }
}

/** 分组聚合（全扫口径，诚实元数据随行）。 */
export async function fetchLayerAggregate(params: LayerAggregateParams): Promise<LayerAggregate> {
  const {
    sessionId, refId, groupBy, agg = 'count', aggField, bbox, where,
    maxGroups = 64, revision, ownerToken, signal,
  } = params;
  const query = qs({
    session_id: sessionId,
    group_by: groupBy,
    agg,
    agg_field: aggField,
    bbox: bbox ? bbox.join(',') : undefined,
    where,
    max_groups: maxGroups,
    v: revision,
  });
  let raw: Record<string, unknown>;
  try {
    raw = await apiFetch<Record<string, unknown>>(
      `/api/v1/layers/data/${encodeURIComponent(refId)}/aggregate?${query}`,
      { ownerToken: ownerToken ?? undefined, label: 'Layer aggregate error', signal },
    );
  } catch (err) {
    if (err && typeof err === 'object' && 'status' in err && (err as { status?: number }).status === 422) {
      const body = (err as { body?: unknown }).body as { detail?: { error?: string } } | undefined;
      if (body?.detail?.error === 'aggregate_over_limit') throw new AggregateOverLimit();
    }
    throw err;
  }
  return {
    rows: (raw.rows as AggregateRow[] | undefined) ?? [],
    groupCount: (raw.group_count as number | undefined) ?? 0,
    isComplete: (raw.is_complete as boolean | undefined) ?? true,
    truncated: (raw.truncated as boolean | undefined) ?? false,
    scanned: (raw.scanned as number | undefined) ?? 0,
    totalMatching: (raw.total_matching as number | undefined) ?? 0,
    nullKeyCount: (raw.null_key_count as number | undefined) ?? 0,
    nonNumericCount: (raw.non_numeric_count as number | undefined) ?? 0,
    revision: (raw.revision as number | null) ?? null,
    reasonCodes: (raw.reason_codes as string[] | undefined) ?? [],
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
