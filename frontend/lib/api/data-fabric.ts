import { API_BASE } from './config';

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
  async listDataSources(sourceType?: string): Promise<{ sources: DataSource[] }> {
    const url = new URL(`${API_BASE}/api/v1/data-fabric/sources`);
    if (sourceType) url.searchParams.append('source_type', sourceType);

    const res = await fetch(url.toString(), { credentials: 'include' });
    if (!res.ok) throw new Error(`获取数据源列表失败: ${res.statusText}`);
    return res.json();
  },

  async createDataSource(req: CreateDataSourceRequest): Promise<{ success: boolean; data_source: DataSource }> {
    const res = await fetch(`${API_BASE}/api/v1/data-fabric/sources`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `注册数据源失败: ${res.statusText}`);
    }
    return res.json();
  },

  async getDataSource(sourceId: string): Promise<DataSource> {
    const res = await fetch(`${API_BASE}/api/v1/data-fabric/sources/${encodeURIComponent(sourceId)}`, {
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`获取数据源失败: ${res.statusText}`);
    return res.json();
  },

  async deleteDataSource(sourceId: string): Promise<{ success: boolean; message: string }> {
    const res = await fetch(`${API_BASE}/api/v1/data-fabric/sources/${encodeURIComponent(sourceId)}`, {
      method: 'DELETE',
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`删除数据源失败: ${res.statusText}`);
    return res.json();
  },

  async probeDataSource(sourceId: string): Promise<DataFabricHealth> {
    const res = await fetch(`${API_BASE}/api/v1/data-fabric/sources/${encodeURIComponent(sourceId)}/probe`, {
      method: 'POST',
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`数据源连通性探查失败: ${res.statusText}`);
    return res.json();
  },

  async syncDataSourceCatalog(sourceId: string): Promise<{ success: boolean; synced_count: number }> {
    const res = await fetch(`${API_BASE}/api/v1/data-fabric/sources/${encodeURIComponent(sourceId)}/sync`, {
      method: 'POST',
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`同步数据源目录失败: ${res.statusText}`);
    return res.json();
  },

  async listSpatialCatalog(params?: {
    q?: string;
    source_id?: string;
    geometry_type?: string;
    feature_type?: string;
    limit?: number;
    offset?: number;
    signal?: AbortSignal;
  }): Promise<{ total: number; limit: number; offset: number; items: CatalogItem[] }> {
    const url = new URL(`${API_BASE}/api/v1/data-fabric/catalog`);
    if (params?.q) url.searchParams.append('q', params.q);
    if (params?.source_id) url.searchParams.append('source_id', params.source_id);
    if (params?.geometry_type) url.searchParams.append('geometry_type', params.geometry_type);
    if (params?.feature_type) url.searchParams.append('feature_type', params.feature_type);
    if (params?.limit) url.searchParams.append('limit', String(params.limit));
    if (params?.offset) url.searchParams.append('offset', String(params.offset));

    const res = await fetch(url.toString(), { credentials: 'include', signal: params?.signal });
    if (!res.ok) throw new Error(`获取空间目录失败: ${res.statusText}`);
    return res.json();
  },

  async getCatalogItem(itemId: string): Promise<CatalogItem> {
    const res = await fetch(`${API_BASE}/api/v1/data-fabric/catalog/${encodeURIComponent(itemId)}`, {
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`获取目录项失败: ${res.statusText}`);
    return res.json();
  },

  async getCatalogItemDescriptor(itemId: string): Promise<DatasetDescriptor> {
    const res = await fetch(`${API_BASE}/api/v1/data-fabric/catalog/${encodeURIComponent(itemId)}/descriptor`, {
      credentials: 'include',
    });
    if (!res.ok) throw new Error(`获取数据集 Descriptor 失败: ${res.statusText}`);
    return res.json();
  },

  async previewCatalogItem(itemId: string, limit = 10): Promise<QueryResult> {
    const url = new URL(`${API_BASE}/api/v1/data-fabric/catalog/${encodeURIComponent(itemId)}/preview`);
    url.searchParams.append('limit', String(limit));
    const res = await fetch(url.toString(), { credentials: 'include' });
    if (!res.ok) throw new Error(`数据预览失败: ${res.statusText}`);
    return res.json();
  },

  async queryCatalogItem(itemId: string, spec: QuerySpec): Promise<QueryResult> {
    const res = await fetch(`${API_BASE}/api/v1/data-fabric/catalog/${encodeURIComponent(itemId)}/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(spec),
    });
    if (!res.ok) throw new Error(`推导查询失败: ${res.statusText}`);
    return res.json();
  },

  async materializeCatalogItem(req: {
    session_id: string;
    catalog_item_id: string;
    query_spec?: QuerySpec;
  }): Promise<MaterializeResult> {
    const res = await fetch(`${API_BASE}/api/v1/data-fabric/materialize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `实例化数据失败: ${res.statusText}`);
    }
    return res.json();
  },
};
