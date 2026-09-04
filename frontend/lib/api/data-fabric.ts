/**
 * Data Fabric frontend client (sources, catalog, query, materialize).
 *
 * All requests flow through the shared transport (apiFetch) for typed errors,
 * request correlation, abort, timeout, and `credentials: 'include'` (data-fabric
 * endpoints ride the session cookie). GETs use the Fast Path (in-flight dedup +
 * 5s LRU) so search-as-you-type and tab switches collapse to a single roundtrip.
 *
 * Migrated F-FE-3: previously each function re-implemented fetch + plain
 * `throw new Error(\`...${statusText}\`)`, had no abort/timeout, and lost the
 * FastAPI `detail` body. Now the same 401/403/5xx distinction as chat paths.
 */

import { apiFetch } from './transport';
import { fastGet, invalidateCache } from './get-fast-path';
import type { GeoJSONFeatureCollection } from '../types';

const DF_CREDENTIALS: RequestCredentials = 'include';
const DF_LABEL = 'DataFabric API error';

export interface ConnectionProfile {
  id?: string;
  name?: string;
  source_type: string;
  url?: string;
  options?: Record<string, unknown>;
  allow_private?: boolean;
}

export interface DataSource {
  id: string;
  name: string;
  source_type: string;
  endpoint_url: string;
  status: 'active' | 'healthy' | 'degraded' | 'unreachable' | 'error' | string;
  capabilities: string[];
  connection_profile: Record<string, unknown>;
  last_health_check?: string | null;
}

export interface CreateDataSourceRequest {
  name: string;
  source_type: string;
  endpoint_url: string;
  options?: Record<string, unknown>;
  allow_private?: boolean;
}

export interface CatalogItem {
  id: string;
  source_id: string;
  name: string;
  title: string;
  description?: string;
  geometry_type?: string;
  feature_type?: 'vector' | 'raster' | string;
  crs?: string;
  bbox?: number[];
  /**
   * V2 (ADR-0094 §9)：sync 后条目可用性。'unavailable' 表示数据集已从数据源
   * 消失（stale —— 元数据保留供检索，但查询/物化大概率失败）。缺省视为可用
   * （列表 summary 载荷未携带该字段时的向后兼容）。
   */
  availability?: 'available' | 'unavailable' | string;
  meta_profile?: Record<string, unknown>;
  descriptor?: DatasetDescriptor;
  updated_at?: string;
}

export interface DatasetDescriptor {
  id: string;
  title?: string;
  description?: string;
  source_type: string;
  geometry_type?: string;
  srs?: string;
  bbox?: number[];
  feature_count?: number;
  fields?: Array<{ name: string; type: string; description?: string }>;
  metadata?: Record<string, unknown>;
}

export interface QuerySpec {
  limit?: number;
  offset?: number;
  bbox?: number[];
  where?: string;
  fields?: string[];
  srs?: string;
  extra_params?: Record<string, unknown>;
  // ── V2 (ADR-0094) additive extras：后端 QuerySpec extra="allow"，由
  // normalize_query_spec 归一化为 QuerySpecV2（aggregate/group_by/order_by/
  // result_mode/cursor/sample_size 直接透传）。
  /** 聚合定义；count 可省略 field。设置后语义上应搭配 result_mode='statistics'。 */
  aggregate?: Array<{ func: string; field?: string }>;
  group_by?: string[];
  /** 'field desc' 字符串或 {field, direction} 对象（后端两种都接受）。 */
  order_by?: Array<string | { field: string; direction: 'asc' | 'desc' }>;
  result_mode?: 'features' | 'statistics' | 'sample' | 'descriptor' | 'materialize' | 'vector_tile' | string;
  /** keyset 游标（上一页响应的 next_cursor；首页不传）。 */
  cursor?: string;
  /** SAMPLE 模式采样行数（1..5000）。 */
  sample_size?: number;
}

/**
 * QueryResult.metadata["query_plan"] 的形状（后端 QueryPlan.model_dump()）。
 * metadata 本体是宽松的 Record<string, unknown>，此接口供读取侧窄化。
 */
export interface QueryPlanInfo {
  source_type?: string;
  source_id?: string | null;
  dataset_id?: string;
  dataset_fingerprint?: string | null;
  query_fingerprint?: string | null;
  pushed_filters?: string[];
  local_filters?: string[];
  pushed_projection?: boolean;
  pushed_spatial?: boolean;
  pushed_aggregation?: boolean;
  pushed_sort?: boolean;
  pagination_strategy?: 'cursor' | 'offset' | 'single_page' | 'none' | string;
  pagination_note?: string | null;
  estimated_rows?: number | null;
  estimated_bytes?: number | null;
  execution_mode?: 'pushdown' | 'local_fallback' | 'hybrid' | string;
  result_mode?: string;
  fallback_reason?: string | null;
  warnings?: string[];
  steps?: Array<{ step: string; description: string; pushed?: boolean }>;
}

/**
 * QueryResult.metadata["query_evidence"] 的形状（后端 QueryEvidence.model_dump()）。
 */
export interface QueryEvidenceInfo {
  query_id?: string | null;
  dataset_id?: string | null;
  source_id?: string | null;
  dataset_fingerprint?: string | null;
  query_fingerprint?: string | null;
  pushdowns?: Record<string, boolean>;
  local_operations?: string[];
  result_count?: number | null;
  total_matching?: number | null;
  truncated?: boolean;
  execution_duration_s?: number | null;
  fallbacks?: string[];
  warnings?: string[];
  rows_fetched?: number | null;
  rows_returned?: number | null;
}

export interface QueryResult {
  dataset_id: string;
  features: Array<Record<string, unknown>>;
  /** STATISTICS/DESCRIPTOR 模式的结构化载荷（聚合行 / 描述符），与 features 互斥。 */
  data?: unknown;
  total_count?: number;
  schema_info?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  // ── V2 (ADR-0094) additive fields ─────────────────────────────────────
  /** 命中总数（服务端可估则给，不可估为 null）。 */
  total_matching?: number | null;
  truncated?: boolean;
  has_more?: boolean;
  /** keyset 下一页游标；offset 模式为 null。 */
  next_cursor?: string | null;
  /** 'features' | 'statistics' | 'sample' | 'descriptor' | 'vector_tile' | ... */
  result_mode?: string;
  /** 演示数据（远端不可达时由本地样本充当，诚实标注）。 */
  is_demo?: boolean;
  returned_count?: number;
  payload_type?: string;
  execution_time_seconds?: number;
}

export interface DataFabricHealth {
  status: 'healthy' | 'unreachable' | 'degraded' | 'unknown' | string;
  message: string;
  details?: Record<string, unknown>;
  latency_ms?: number;
}

/** V2 (ADR-0094 §9)：sync 返回的结构化增量 diff。 */
export interface SyncDiff {
  added?: number;
  updated?: number;
  unchanged?: number;
  /** 消失的条目 → 标记 availability='unavailable'（保留元数据，不物理删除）。 */
  removed?: number;
}

export interface SyncCatalogResult {
  success: boolean;
  synced_count: number;
  items?: Array<{ id: string; name: string; title?: string | null }>;
  diff?: SyncDiff;
  warnings?: string[];
}

/** explain（dry-run 计划）的结构化 plan 段（QueryPlan.model_dump()）。 */
export type ExplainPlan = QueryPlanInfo;

export interface ExplainResult {
  status: 'success' | 'error';
  dataset_id?: string;
  dataset_fingerprint?: string;
  /** 人类可读计划行（monospace 块渲染；永不包含 secret/连接 URI）。 */
  explain?: string[];
  plan?: ExplainPlan;
  capabilities?: Record<string, unknown>;
  dataset?: {
    geometry_type?: string | null;
    srs?: string | null;
    feature_count?: number | null;
  };
  // ── error 形态（HTTP 422 detail 内嵌的 outcome） ─────────────────────
  error_type?: string;
  error?: string;
  details?: Record<string, unknown>;
}

export interface MaterializeResult {
  success: boolean;
  ref_id: string;
  feature_count: number;
  total_count?: number;
  dataset_id: string;
  source_id: string;
  title?: string;
}

export const dataFabricApi = {
  async listDataSources(
    sourceType?: string,
    opts?: { forceRefresh?: boolean; signal?: AbortSignal }
  ): Promise<{ sources: DataSource[] }> {
    const path = sourceType
      ? `/api/v1/data-fabric/sources?source_type=${encodeURIComponent(sourceType)}`
      : '/api/v1/data-fabric/sources';
    const result = await fastGet<{ sources: DataSource[] }>(path, {
      forceRefresh: opts?.forceRefresh,
      signal: opts?.signal,
      credentials: DF_CREDENTIALS,
      label: DF_LABEL,
    });
    return result.data;
  },

  async createDataSource(req: CreateDataSourceRequest): Promise<{ success: boolean; data_source: DataSource }> {
    const out = await apiFetch<{ success: boolean; data_source: DataSource }>(
      '/api/v1/data-fabric/sources',
      {
        method: 'POST',
        body: req,
        credentials: DF_CREDENTIALS,
        label: DF_LABEL,
      }
    );
    invalidateCache('/api/v1/data-fabric/sources');
    return out;
  },

  async getDataSource(sourceId: string, opts?: { signal?: AbortSignal }): Promise<DataSource> {
    const result = await fastGet<DataSource>(
      `/api/v1/data-fabric/sources/${encodeURIComponent(sourceId)}`,
      {
        signal: opts?.signal,
        credentials: DF_CREDENTIALS,
        label: DF_LABEL,
      }
    );
    return result.data;
  },

  async deleteDataSource(sourceId: string): Promise<{ success: boolean; message: string }> {
    const out = await apiFetch<{ success: boolean; message: string }>(
      `/api/v1/data-fabric/sources/${encodeURIComponent(sourceId)}`,
      {
        method: 'DELETE',
        credentials: DF_CREDENTIALS,
        label: DF_LABEL,
      }
    );
    invalidateCache('/api/v1/data-fabric/sources');
    return out;
  },

  async probeDataSource(sourceId: string, opts?: { signal?: AbortSignal }): Promise<DataFabricHealth> {
    return apiFetch<DataFabricHealth>(
      `/api/v1/data-fabric/sources/${encodeURIComponent(sourceId)}/probe`,
      {
        method: 'POST',
        credentials: DF_CREDENTIALS,
        signal: opts?.signal,
        label: DF_LABEL,
      }
    );
  },

  async syncDataSourceCatalog(sourceId: string): Promise<SyncCatalogResult> {
    const out = await apiFetch<SyncCatalogResult>(
      `/api/v1/data-fabric/sources/${encodeURIComponent(sourceId)}/sync`,
      {
        method: 'POST',
        credentials: DF_CREDENTIALS,
        label: DF_LABEL,
      }
    );
    // Catalog changed → invalidate any cached list/detail.
    invalidateCache('/api/v1/data-fabric/catalog');
    return out;
  },

  async listSpatialCatalog(params?: {
    q?: string;
    source_id?: string;
    geometry_type?: string;
    feature_type?: string;
    availability?: string;
    limit?: number;
    offset?: number;
    forceRefresh?: boolean;
    signal?: AbortSignal;
  }): Promise<{ total: number; limit: number; offset: number; items: CatalogItem[] }> {
    const queryParams: Record<string, string | number> = {};
    if (params?.q) queryParams.q = params.q;
    if (params?.source_id) queryParams.source_id = params.source_id;
    if (params?.availability) queryParams.availability = params.availability;
    if (params?.geometry_type) queryParams.geometry_type = params.geometry_type;
    if (params?.feature_type) queryParams.feature_type = params.feature_type;
    if (params?.limit !== undefined) queryParams.limit = params.limit;
    if (params?.offset !== undefined) queryParams.offset = params.offset;
    const result = await fastGet<{ total: number; limit: number; offset: number; items: CatalogItem[] }>(
      '/api/v1/data-fabric/catalog',
      {
        params: queryParams,
        forceRefresh: params?.forceRefresh,
        signal: params?.signal,
        credentials: DF_CREDENTIALS,
        label: DF_LABEL,
        // search-as-you-type: keep cached data fresh by default
        ttlMs: 2_000,
      }
    );
    return result.data;
  },

  async getCatalogItem(itemId: string, opts?: { signal?: AbortSignal }): Promise<CatalogItem> {
    const result = await fastGet<CatalogItem>(
      `/api/v1/data-fabric/catalog/${encodeURIComponent(itemId)}`,
      {
        signal: opts?.signal,
        credentials: DF_CREDENTIALS,
        label: DF_LABEL,
      }
    );
    return result.data;
  },

  async getCatalogItemDescriptor(itemId: string, opts?: { signal?: AbortSignal }): Promise<DatasetDescriptor> {
    return apiFetch<DatasetDescriptor>(
      `/api/v1/data-fabric/catalog/${encodeURIComponent(itemId)}/descriptor`,
      {
        credentials: DF_CREDENTIALS,
        signal: opts?.signal,
        label: DF_LABEL,
      }
    );
  },

  async previewCatalogItem(itemId: string, limit = 10): Promise<QueryResult> {
    return apiFetch<QueryResult>(
      `/api/v1/data-fabric/catalog/${encodeURIComponent(itemId)}/preview?limit=${limit}`,
      {
        credentials: DF_CREDENTIALS,
        label: DF_LABEL,
      }
    );
  },

  async queryCatalogItem(itemId: string, spec: QuerySpec): Promise<QueryResult> {
    return apiFetch<QueryResult>(
      `/api/v1/data-fabric/catalog/${encodeURIComponent(itemId)}/query`,
      {
        method: 'POST',
        body: spec,
        credentials: DF_CREDENTIALS,
        label: DF_LABEL,
      }
    );
  },

  /**
   * V2 (ADR-0094 §13)：explain —— dry-run 查询计划，不执行查询。POST 体为
   * {query_spec?}；错误以 HTTP 422 返回（detail 内嵌 error outcome，由
   * describeApiError 之外的 typed-error 提取处理）。不缓存（计划随 spec 变化）。
   */
  async explainCatalogItem(itemId: string, spec?: QuerySpec): Promise<ExplainResult> {
    return apiFetch<ExplainResult>(
      `/api/v1/data-fabric/catalog/${encodeURIComponent(itemId)}/explain`,
      {
        method: 'POST',
        body: spec ? { query_spec: spec } : {},
        credentials: DF_CREDENTIALS,
        label: DF_LABEL,
      }
    );
  },

  async materializeCatalogItem(req: {
    session_id: string;
    catalog_item_id: string;
    query_spec?: QuerySpec;
    ownerToken?: string | null;
  }): Promise<MaterializeResult> {
    return apiFetch<MaterializeResult>('/api/v1/data-fabric/materialize', {
      method: 'POST',
      body: {
        session_id: req.session_id,
        catalog_item_id: req.catalog_item_id,
        query_spec: req.query_spec,
      },
      credentials: DF_CREDENTIALS,
      ownerToken: req.ownerToken,
      // Materialize can do a remote describe + insert; allow extra time.
      timeoutMs: 60_000,
      label: DF_LABEL,
    });
  },

  /**
   * Fetch-on-demand for a materialized ref (#463): hydrate a `df-*` layer with
   * the FeatureCollection stored in the session store under `refId`. Same
   * endpoint/ownership model as the workspace-session layer-restore path
   * (`use-workspace-session.ts`): session-scoped, owner-token protected.
   */
  async fetchRefGeoJSON(
    refId: string,
    sessionId: string,
    opts?: { ownerToken?: string | null; signal?: AbortSignal }
  ): Promise<GeoJSONFeatureCollection> {
    return apiFetch<GeoJSONFeatureCollection>(
      `/api/v1/layers/data/${encodeURIComponent(refId)}?session_id=${encodeURIComponent(sessionId)}`,
      {
        credentials: DF_CREDENTIALS,
        ownerToken: opts?.ownerToken,
        signal: opts?.signal,
        label: DF_LABEL,
      }
    );
  },
};
