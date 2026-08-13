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
}

export interface QueryResult {
  dataset_id: string;
  features: Array<Record<string, unknown>>;
  total_count?: number;
  schema_info?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface DataFabricHealth {
  status: 'healthy' | 'unreachable' | 'degraded' | 'unknown' | string;
  message: string;
  details?: Record<string, unknown>;
  latency_ms?: number;
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

  async syncDataSourceCatalog(sourceId: string): Promise<{ success: boolean; synced_count: number }> {
    const out = await apiFetch<{ success: boolean; synced_count: number }>(
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
    limit?: number;
    offset?: number;
    forceRefresh?: boolean;
    signal?: AbortSignal;
  }): Promise<{ total: number; limit: number; offset: number; items: CatalogItem[] }> {
    const queryParams: Record<string, string | number> = {};
    if (params?.q) queryParams.q = params.q;
    if (params?.source_id) queryParams.source_id = params.source_id;
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
};
