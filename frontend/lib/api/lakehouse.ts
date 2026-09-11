/**
 * Lakehouse typed client — DataObject / Cube / Dataset / Catalog / STAC / GC.
 *
 * 契约来源（以后端代码为准，勿凭记忆改字段；S1 勘察 2026-09-11，基线 8b5b8375）：
 * - app/api/routes/lakehouse.py（对象/cube/发布/catalog/GC 面，17 端点）
 * - app/api/routes/lakehouse_datasets.py（V8 dataset 版本层，12 端点）
 * - app/schemas/lakehouse_schema.py（请求 Pydantic 模型，约束逐一对齐）
 * - 字段级响应形态见 frontend/docs/lakehouse-ui-recon.md §1
 *
 * 语义要点：
 * - session 域端点全部依赖所有权守卫（SEC-08）：匿名会话必须携带 ownerToken
 *   （transport 注入 X-Session-Token）；无身份/非 owner → fail-closed 404。
 * - 错误体只有 `{detail: string}`（FastAPI HTTPException）——typed code 不出后端，
 *   前端按 status 分支：400 契约违例 / 404 不存在或无权 / 409 计划漂移·冲突 /
 *   410 项目 manifest 不可解析 / 422 路由层前置校验 / 403 admin 端点。
 * - 成功响应无统一信封：顶层 `success: true`（或 `status:"success"`）+ 各端点自有键。
 */

import { apiFetch } from './transport';
import { fastGet } from './get-fast-path';

const LABEL = 'Lakehouse API error';

/** 窗口读/扫描/GC 端点服务端做真实 IO，超时比 30s 默认宽。 */
const HEAVY_TIMEOUT_MS = 60_000;

type OwnerCredentials = { ownerToken?: string | null; signal?: AbortSignal };

/* ── 共享形态 ─────────────────────────────────────────────────────────── */

/**
 * DataObject manifest（data_object.build_object_manifest 投影）。
 * kind ∈ vector_parquet | cog_raster | zarr_cube | virtual | modelops_artifact | arrow_ipc。
 */
export interface LakehouseManifest {
  schema_version: number;
  kind: string;
  /** 恰好一键：{session_id} 或 {project_id}。 */
  owner_scope: { session_id?: string; project_id?: string };
  environment_fingerprint: string;
  content_sha256: string;
  byte_size: number;
  content_blobs: Array<{ path: string; sha256: string; byte_size: number }>;
  /** 任意业务载荷（title/times/labeled/virtual 等）。 */
  payload: Record<string, unknown>;
  /** 已脱敏的生产者披露。 */
  producer: Record<string, unknown>;
  source_refs: string[];
  input_fingerprint: string;
}

/** cube build/revise/rs 响应平铺的 durable 披露块（published:false 时其余键缺失）。 */
export interface DurableDisclosure {
  published?: boolean;
  /** published=false 时仅有 reason。 */
  reason?: string;
  durable?: 'published' | 'manifest_only';
  data_object_id?: string;
  /** 服务器位置（不透明 string，UI 不得展示）。 */
  manifest?: string;
  content_sha256?: string;
  byte_size?: number;
  deduped?: boolean;
  entry_count?: number;
}

/** labeled cube 投影（cube_schema.validate_labeled_schema）。 */
export interface LabeledProjection {
  labeled: true;
  /** ⊆ v2 六维且无逐变量 nodata → 2；含 model/scenario 或 nodata_per_variable → 3。 */
  cube_schema_version: 2 | 3;
  /** 尾两轴恒 ["y","x"]。 */
  dims: string[];
  shape: number[];
  variables: Record<string, { dims: string[]; dtype: string }>;
  dtype: string;
  nodata: number | null;
  chunks: number[] | null;
  crs: string;
  crs_checked: 'rasterio' | 'regex';
  coords_summary: Record<
    string,
    | { kind: 'grid'; n: number; start: number; end: number }
    | { kind: 'labels'; n: number; first: string; last: string }
  >;
  /** 仅 v3 携带（键序 sorted）。 */
  nodata_per_variable?: Record<string, number>;
}

/* ── 1. 对象面 ────────────────────────────────────────────────────────── */

export interface ObjectReadResult {
  success: true;
  data_object_id: string;
  manifest: LakehouseManifest;
}

export interface VectorScanRequest {
  session_id: string;
  /** `ref:fabric-parquet/<id>` 形态。 */
  ref: string;
  /** [minx, miny, maxx, maxy]。 */
  bbox: [number, number, number, number];
  columns?: string[];
  /** 后端硬预算 ≤200_000，默认 50_000。 */
  max_rows?: number;
}

/** 扫描结果是 GeoJSON FeatureCollection（row-group 剪枝证据在 properties）。 */
export interface VectorScanResult {
  type: 'FeatureCollection';
  features: Array<Record<string, unknown>>;
  properties: {
    row_groups_total: number;
    row_groups_read: number;
    truncated: boolean;
    window: [number, number, number, number];
  };
}

/* ── 2. Cube 面 ───────────────────────────────────────────────────────── */

export interface CubeTimeSource {
  /** 时间标签（≤64 字符，自由 ISO-8601 / 语义标签）。 */
  time: string;
  /** 栅格切片来源：路径或 `ref:fabric-parquet/*`（≤1024）。 */
  source: string;
}

export interface CubeBuildRequest {
  session_id: string;
  title?: string;
  window_side?: number;
  time_sources: CubeTimeSource[];
}

export interface CubeBuildResult extends DurableDisclosure {
  status: 'success';
  success: true;
  /** `ref:cube/<16hex>`。 */
  ref: string;
  cube_id: string;
  /** 服务器存储路径 —— 不透明 string，UI 不展示。 */
  path: string;
  title: string;
  times: string[];
  steps: number;
  /** 键为源文件路径（不透明）。 */
  content_fingerprints: Record<string, string>;
}

/** 维度索引切片 [start, stop]（Python slice 语义，非负）。 */
export type IndexSlice = [number, number];

export interface CubeWindowRequest {
  session_id: string;
  ref: string;
  time?: IndexSlice;
  y?: IndexSlice;
  x?: IndexSlice;
}

export interface CubeWindowResult {
  /** band 名 → [t][y][x] 嵌套数组（ndarray.tolist()）。 */
  bands: Record<string, number[][][]>;
  /** 跟随切片的时间标签。 */
  times: string[];
  crs: string | null;
  /** affine 6 参。 */
  transform: number[] | null;
  nodata: number | null;
  ref: string;
}

export interface CubeRevisionUpdate {
  band: string;
  time_index: number;
  source: string;
}

export interface CubeRevisionRequest {
  session_id: string;
  ref: string;
  title?: string;
  updates: CubeRevisionUpdate[];
}

export interface CubeRevisionResult extends CubeBuildResult {
  /** 源 cube ref（CoW fork）。 */
  revision_of: string;
  /** 首步预览（band → 嵌套数组）。 */
  first_step_preview: Record<string, number[][][]>;
}

export interface ObjectVerifyRequest {
  session_id?: string;
  project_id?: string;
}

/**
 * 完整性状态（开放联合）：普通 verified|manifest_missing|blob_missing|digest_mismatch；
 * virtual 深度校验产生 virtual_children_missing|virtual_child_corrupt|virtual_owner_mismatch|virtual_child_*。
 */
export type ObjectVerifyState = string;

export interface ObjectVerifyResult {
  success: true;
  data_object_id: string;
  state: ObjectVerifyState;
}

export interface RSCubeSource {
  time: string;
  source: string;
  /** optical | sar | cloud_mask | quality_mask（服务端再验）。 */
  role: string;
  band?: string;
  polarization?: string;
  band_index?: number;
}

export interface RSCubeBuildRequest {
  session_id: string;
  title?: string;
  sources: RSCubeSource[];
}

export interface RSCubeBuildResult extends CubeBuildResult {
  /** sorted 变量名（reflectance / sigma0 / cloud_mask …）。 */
  variables: string[];
  labeled_projection: LabeledProjection;
}

/** labeled 窗口读的标签选择（标签 / bbox / index_slices 至少一种）。 */
export interface LabeledWindowRequest {
  session_id: string;
  ref: string;
  time?: string[];
  band?: string[];
  polarization?: string[];
  vertical?: string[];
  /** V8 model/scenario 维度（v3 cube 契约）。 */
  model?: string[];
  scenario?: string[];
  bbox?: [number, number, number, number];
  /** dim → [start, stop]（优先于标签/bbox）。 */
  index_slices?: Record<string, IndexSlice>;
  max_cells?: number;
}

/** attrs 六键恒在，值可 null。 */
export interface LabeledWindowAttrs {
  crs: string | null;
  transform: number[] | null;
  nodata: number | null;
  nodata_per_variable: Record<string, number> | null;
  cube_schema_version: number | null;
  dims: string[] | null;
}

export interface LabeledWindowSelectionPlan {
  cells: number;
  touched_chunks: number;
  total_chunks: number;
  /** 只含被选择的维度。 */
  slices: Record<string, IndexSlice>;
}

export interface LabeledWindowResult {
  /** 变量名 → 嵌套数组（ndim 随变量维度）。 */
  variables: Record<string, number[] | number[][] | number[][][]>;
  /** dim → 坐标数组（y/x 为 number，标签轴为 string）。 */
  coords: Record<string, Array<number | string>>;
  slices: Record<string, IndexSlice>;
  attrs: LabeledWindowAttrs;
  selection_plan: LabeledWindowSelectionPlan;
  ref: string;
}

/* ── 3. 发布 / 撤销 ───────────────────────────────────────────────────── */

export interface PublishRequest {
  session_id: string;
  project_id: string;
  object_ids: string[];
  tags?: string[];
}

export interface PublishEntry {
  object_id: string;
  /** `art_lh_<16hex>`。 */
  artifact_id: string;
  revision_no: number;
  revision_created: boolean;
  deduped: boolean;
}

export interface PublishResult {
  success: true;
  published: PublishEntry[];
  /** 请求中未知的 object_id（sorted）。 */
  unknown: string[];
  /** 无权发布的 object_id（sorted）。 */
  forbidden: string[];
}

export interface RevokeRequest {
  project_id: string;
  object_ids: string[];
}

export interface RevokeResult {
  success: true;
  /** 已撤销的 object_id（sorted；tombstone）。 */
  revoked: string[];
  unknown: string[];
}

/* ── 4. Catalog / STAC ────────────────────────────────────────────────── */

export type CatalogOwnerType = 'session' | 'project';

export interface CatalogSearchParams {
  owner_type: CatalogOwnerType;
  owner_id: string;
  session_id?: string;
  kind?: string;
  time_from?: string;
  time_to?: string;
  producer?: string;
  include_revoked?: boolean;
  limit?: number;
  offset?: number;
}

/** catalog 条目（lakehouse_catalog 模型 to_dict）。 */
export interface CatalogEntry {
  object_id: string;
  owner_type: CatalogOwnerType;
  owner_id: string;
  kind: string;
  title: string | null;
  producer_capability: string | null;
  producer_tool: string | null;
  workflow_run_id: string | null;
  tags: string[];
  /** 四值全有或全 null。 */
  bbox: [number, number, number, number] | null;
  /** datetime.isoformat()（+00:00 后缀，非 Z）。 */
  time_start: string | null;
  time_end: string | null;
  content_sha256: string;
  byte_size: number;
  status: 'active' | 'revoked';
  created_at: string | null;
}

export interface CatalogPage {
  items: CatalogEntry[];
  count: number;
  /** 诚实形态：字符串数字，或超扫描下界 `">=10000"`。 */
  total: string;
  total_bounded: boolean;
  limit: number;
  offset: number;
  /** null = 没有更多。 */
  next_offset: number | null;
}

/** STAC 1.0.0 Collection 投影。 */
export interface StacCollection {
  type: 'Collection';
  stac_version: string;
  id: string;
  description: string;
  license: string;
  extent?: {
    spatial?: { bbox: number[][] };
    temporal?: { interval: Array<Array<string | null>> };
  };
  links: Array<{ rel: string; href: string; type?: string }>;
}

/** STAC Item（投影前提：bbox 4 元 + time_start 存在，否则进 skipped）。 */
export interface StacItem {
  type: 'Feature';
  stac_version: string;
  id: string;
  geometry: { type: string; coordinates: unknown };
  bbox: [number, number, number, number];
  properties: {
    datetime: string;
    start_datetime?: string;
    end_datetime?: string;
    'webgis:kind': string;
    'webgis:owner_type': string;
    'webgis:owner_id': string;
    'webgis:content_sha256': string;
    'webgis:byte_size': number;
    'webgis:tags'?: string[];
    [key: string]: unknown;
  };
  assets: Record<
    string,
    { href: string; title?: string; roles?: string[]; [key: string]: unknown }
  >;
  links: Array<{ rel: string; href: string; type?: string }>;
}

export interface StacCatalogResult {
  collection: StacCollection;
  items: StacItem[];
  /** 不可投影条目的 object_id（诚实披露，截 64 字符）。 */
  skipped: string[];
}

export interface StacSearchParams {
  owner_type: CatalogOwnerType;
  owner_id: string;
  session_id?: string;
  limit?: number;
  offset?: number;
}

/* ── 5. scrub / GC / 血缘 ─────────────────────────────────────────────── */

export interface ObjectScrubRequest {
  session_id?: string;
  project_id?: string;
  mode?: 'sample' | 'full';
  sample_k?: number;
  etag_check?: boolean;
}

export interface ScrubReport {
  success: true;
  data_object_id: string;
  /** verified|corrupt|etag_mismatch|manifest_only|<virtual 深度态>（开放联合）。 */
  state: string;
  chunks_total: number;
  chunks_checked: number;
  missing: string[];
  corrupt: string[];
  etag_mismatch: string[];
  etag_checked: boolean;
  mode: 'sample' | 'full';
}

export interface GCPlanRequest {
  session_id: string;
  /** 宽限期小时（0..720，默认 72）。 */
  grace_hours?: number;
}

/** GC dry-run 计划（admin 专用；token 供 execute 重验；无字节量字段）。 */
export interface GCPlan {
  success: true;
  /** 64hex manifest id（sorted）。 */
  candidates: string[];
  deletable_blobs: string[];
  protected_count: number;
  scanned_manifests: number;
  /** epoch 秒 float。 */
  watermark: number;
  grace_hours: number;
  requested_grace_hours: number;
  registry_ttl_floor_hours: number;
  token: string;
  /** 内部解析缓存（巨大；前端忽略不展示）。 */
  _scan?: Record<string, unknown>;
}

export interface GCExecuteRequest {
  session_id: string;
  plan: Record<string, unknown>;
}

export interface GCExecuteResult {
  success: true;
  deleted_manifests: string[];
  deleted_blobs: string[];
  skipped_protected_blobs: string[];
  skipped_stale: number;
}

/** 对象血缘祖先视图（root 不在 ancestors 内；深度≤8、节点≤10000）。 */
export interface LineageView {
  success: true;
  root: string;
  ancestors: Array<{ id: string; kind: string; depth: number }>;
  edges: Array<{ child: string; parent: string }>;
  truncated: boolean;
}

/* ── 6. Dataset 版本层（V8） ──────────────────────────────────────────── */

export interface DatasetCreateRequest {
  session_id: string;
  /** [A-Za-z0-9._-]{1,128}。 */
  name: string;
  description?: string;
  default_branch?: string;
  /** v3 cube 契约（dims/variables/nodata/units 等结构化声明）。 */
  cube_contract?: Record<string, unknown>;
}

/** dataset 台账行（dataset_id = 64hex 描述符 manifest id，即 REST 路径用的 id）。 */
export interface LakehouseDataset {
  dataset_id: string;
  owner_type: string;
  owner_id: string;
  name: string;
  description: string | null;
  default_branch: string;
  cube_contract: Record<string, unknown> | null;
  created_at: string | null;
}

export interface DatasetVersion {
  version_id: string;
  parent_version_id: string | null;
  data_object_id: string;
  content_sha256: string;
  byte_size: number;
  branch: string;
  action: 'commit' | 'rollback' | 'delta' | 'import' | 'workflow_publish';
  provenance: Record<string, unknown>;
  workflow_run_id: string | null;
  created_at: string | null;
}

/** branch/tag 指针行。 */
export interface DatasetRef {
  ref_type: 'branch' | 'tag';
  ref_name: string;
  version_id: string;
  generation: number;
  created_at: string | null;
}

/** dataset commit record（内容寻址 manifest 投影）。 */
export interface DatasetCommitRecord {
  schema_version: number;
  kind: 'dataset_commit';
  dataset_id: string;
  parent_version_id: string | null;
  data_object_id: string;
  content_sha256: string;
  action: string;
  provenance: Record<string, unknown>;
}

export interface DatasetDetail {
  success: true;
  dataset: LakehouseDataset;
  refs: DatasetRef[];
  /** default_branch 的 branch 指针；无提交历史 → null。 */
  head: DatasetRef | null;
  /** 描述符 manifest（不可解析 → null）。 */
  descriptor: Record<string, unknown> | null;
}

/** 版本解析：VersionRow 键平铺在顶层 + 合成 manifest 三键。 */
export type DatasetVersionResolveResult = DatasetVersion & {
  success: true;
  manifest: LakehouseManifest | null;
  content_available: boolean;
  commit: DatasetCommitRecord | null;
};

export interface DatasetCommitRequest {
  session_id: string;
  branch: string;
  /** 64 位 hex data object id。 */
  data_object_id: string;
  action?: 'commit' | 'rollback' | 'delta' | 'import' | 'workflow_publish';
  /** 白名单键：action/algorithm/parameters/code_version/model_id/model_version/sources/inputs/coverage/quality_flags/workflow_run_id/workflow_step/rollback_target。 */
  provenance?: Record<string, unknown>;
  parent_version_id?: string;
  workflow_run_id?: string;
}

export interface DatasetCommitResult {
  success: true;
  version_id: string;
  parent_version_id: string | null;
  data_object_id: string;
  branch: string;
  generation: number;
  version_deduped: boolean;
  ref_moved: boolean;
}

export interface DatasetBranchRequest {
  session_id: string;
  name: string;
  from_version_id?: string;
}

export interface DatasetTagRequest {
  session_id: string;
  name: string;
  version_id: string;
}

export interface DatasetRollbackRequest {
  session_id: string;
  branch: string;
  to_version_id: string;
}

export interface DatasetLineageParams {
  session_id?: string;
  version_id: string;
  max_depth?: number;
}

export interface DatasetLineageView {
  success: true;
  version_id: string;
  /** parent 链上溯（新→旧）。 */
  chain: DatasetVersion[];
  depth: number;
  truncated: boolean;
}

export interface RetentionPlanRequest {
  session_id: string;
  max_versions?: number;
  min_age_hours?: number;
}

/** retention dry-run 计划（token 供 execute 重验；无字节量字段）。 */
export interface RetentionPlan {
  success: true;
  dataset_row_id: string;
  dataset_id: string;
  /** version_id（sorted）。 */
  candidates: string[];
  candidate_count: number;
  protected_by_refs: number;
  protected_by_window: number;
  scanned_versions: number;
  max_versions: number;
  min_age_hours: number;
  token: string;
}

export interface RetentionExecuteRequest {
  session_id: string;
  plan: Record<string, unknown>;
}

export interface RetentionExecuteResult {
  success: true;
  dataset_row_id: string;
  dataset_id: string;
  pruned: string[];
  pruned_count: number;
  skipped_protected: string[];
  skipped_overflow: number;
}

/* ── 客户端 ───────────────────────────────────────────────────────────── */

async function get<T>(
  path: string,
  params: Record<string, string | number | boolean | undefined | null>,
  creds: OwnerCredentials,
) {
  const result = await fastGet<T>(path, {
    params,
    ownerToken: creds.ownerToken,
    signal: creds.signal,
    label: LABEL,
  });
  return result.data;
}

function post<T>(
  path: string,
  body: unknown,
  creds: OwnerCredentials,
  options?: { timeoutMs?: number },
) {
  return apiFetch<T>(path, {
    method: 'POST',
    body,
    ownerToken: creds.ownerToken,
    signal: creds.signal,
    label: LABEL,
    timeoutMs: options?.timeoutMs,
  });
}

export const lakehouseApi = {
  /* ── 对象面 ── */

  getObject(objectId: string, sessionId: string, creds: OwnerCredentials = {}) {
    return get<ObjectReadResult>(
      `/api/v1/lakehouse/objects/${encodeURIComponent(objectId)}`,
      { session_id: sessionId },
      creds,
    );
  },

  /** session_id 必须留在 body（Pydantic 模型携带它做所有权守卫）。 */
  scanVector(req: VectorScanRequest, creds: OwnerCredentials = {}) {
    return post<VectorScanResult>(
      '/api/v1/lakehouse/vector/scan', req, creds, { timeoutMs: HEAVY_TIMEOUT_MS },
    );
  },

  /* ── Cube 面 ── */

  buildCube(req: CubeBuildRequest, creds: OwnerCredentials = {}) {
    return post<CubeBuildResult>(
      '/api/v1/lakehouse/cubes', req, creds, { timeoutMs: HEAVY_TIMEOUT_MS },
    );
  },

  /** 窗口读：至少一个有限切片（whole-cube 读取被后端 422 拒绝）。 */
  readCubeWindow(req: CubeWindowRequest, creds: OwnerCredentials = {}) {
    return post<CubeWindowResult>(
      '/api/v1/lakehouse/cubes/window', req, creds, { timeoutMs: HEAVY_TIMEOUT_MS },
    );
  },

  reviseCube(req: CubeRevisionRequest, creds: OwnerCredentials = {}) {
    return post<CubeRevisionResult>(
      '/api/v1/lakehouse/cubes/revise', req, creds, { timeoutMs: HEAVY_TIMEOUT_MS },
    );
  },

  verifyObject(objectId: string, req: ObjectVerifyRequest = {}, creds: OwnerCredentials = {}) {
    return post<ObjectVerifyResult>(
      `/api/v1/lakehouse/objects/${encodeURIComponent(objectId)}/verify`, req, creds,
    );
  },

  buildRsCube(req: RSCubeBuildRequest, creds: OwnerCredentials = {}) {
    return post<RSCubeBuildResult>(
      '/api/v1/lakehouse/cubes/rs', req, creds, { timeoutMs: HEAVY_TIMEOUT_MS },
    );
  },

  /** labeled 窗口读：标签/bbox/index_slices 至少一种（全空 → 422）。 */
  readLabeledWindow(req: LabeledWindowRequest, creds: OwnerCredentials = {}) {
    return post<LabeledWindowResult>(
      '/api/v1/lakehouse/cubes/labeled/window', req, creds, { timeoutMs: HEAVY_TIMEOUT_MS },
    );
  },

  /* ── 发布 / 撤销 ── */

  publish(req: PublishRequest, creds: OwnerCredentials = {}) {
    return post<PublishResult>('/api/v1/lakehouse/publish', req, creds);
  },

  revoke(req: RevokeRequest, creds: OwnerCredentials = {}) {
    return post<RevokeResult>('/api/v1/lakehouse/revoke', req, creds);
  },

  /* ── Catalog / STAC ── */

  searchCatalog(params: CatalogSearchParams, creds: OwnerCredentials = {}) {
    return get<CatalogPage>('/api/v1/lakehouse/catalog', { ...params }, creds);
  },

  searchCatalogStac(params: StacSearchParams, creds: OwnerCredentials = {}) {
    return get<StacCatalogResult>('/api/v1/lakehouse/catalog/stac', { ...params }, creds);
  },

  scrubObject(objectId: string, req: ObjectScrubRequest = {}, creds: OwnerCredentials = {}) {
    return post<ScrubReport>(
      `/api/v1/lakehouse/objects/${encodeURIComponent(objectId)}/scrub`, req, creds,
      { timeoutMs: HEAVY_TIMEOUT_MS },
    );
  },

  /* ── GC（admin 面 —— UI 只读展示计划，执行归 C/F 线） ── */

  planGc(req: GCPlanRequest, creds: OwnerCredentials = {}) {
    return post<GCPlan>(
      '/api/v1/lakehouse/gc/plan', req, creds, { timeoutMs: HEAVY_TIMEOUT_MS },
    );
  },

  executeGc(req: GCExecuteRequest, creds: OwnerCredentials = {}) {
    return post<GCExecuteResult>(
      '/api/v1/lakehouse/gc/execute', req, creds, { timeoutMs: HEAVY_TIMEOUT_MS },
    );
  },

  getObjectLineage(objectId: string, sessionId: string, creds: OwnerCredentials = {}) {
    return get<LineageView>(
      `/api/v1/lakehouse/objects/${encodeURIComponent(objectId)}/lineage`,
      { session_id: sessionId },
      creds,
    );
  },

  getProjectObject(projectId: string, objectId: string, creds: OwnerCredentials = {}) {
    return get<{ success: true; manifest: LakehouseManifest; catalog: CatalogEntry }>(
      `/api/v1/lakehouse/projects/${encodeURIComponent(projectId)}/objects/${encodeURIComponent(objectId)}`,
      {},
      creds,
    );
  },

  /* ── Dataset 版本层 ── */

  createDataset(req: DatasetCreateRequest, creds: OwnerCredentials = {}) {
    return post<{ success: true; created: boolean; dataset: LakehouseDataset }>(
      '/api/v1/lakehouse/datasets', req, creds,
    );
  },

  listDatasets(sessionId: string, limit?: number, creds: OwnerCredentials = {}) {
    return get<{ success: true; datasets: LakehouseDataset[]; count: number }>(
      '/api/v1/lakehouse/datasets',
      { session_id: sessionId, limit },
      creds,
    );
  },

  getDataset(datasetId: string, sessionId: string, creds: OwnerCredentials = {}) {
    return get<DatasetDetail>(
      `/api/v1/lakehouse/datasets/${encodeURIComponent(datasetId)}`,
      { session_id: sessionId },
      creds,
    );
  },

  listDatasetVersions(
    datasetId: string,
    sessionId: string,
    branch?: string,
    limit?: number,
    creds: OwnerCredentials = {},
  ) {
    return get<{ success: true; versions: DatasetVersion[]; count: number }>(
      `/api/v1/lakehouse/datasets/${encodeURIComponent(datasetId)}/versions`,
      { session_id: sessionId, branch, limit },
      creds,
    );
  },

  resolveDatasetVersion(
    datasetId: string,
    versionId: string,
    sessionId: string,
    creds: OwnerCredentials = {},
  ) {
    return get<DatasetVersionResolveResult>(
      `/api/v1/lakehouse/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}`,
      { session_id: sessionId },
      creds,
    );
  },

  commitDatasetVersion(datasetId: string, req: DatasetCommitRequest, creds: OwnerCredentials = {}) {
    return post<DatasetCommitResult>(
      `/api/v1/lakehouse/datasets/${encodeURIComponent(datasetId)}/commit`, req, creds,
    );
  },

  createDatasetBranch(datasetId: string, req: DatasetBranchRequest, creds: OwnerCredentials = {}) {
    return post<{ success: true; created: boolean; ref: DatasetRef }>(
      `/api/v1/lakehouse/datasets/${encodeURIComponent(datasetId)}/branches`, req, creds,
    );
  },

  createDatasetTag(datasetId: string, req: DatasetTagRequest, creds: OwnerCredentials = {}) {
    return post<{ success: true; created: boolean; ref: DatasetRef }>(
      `/api/v1/lakehouse/datasets/${encodeURIComponent(datasetId)}/tags`, req, creds,
    );
  },

  rollbackDataset(datasetId: string, req: DatasetRollbackRequest, creds: OwnerCredentials = {}) {
    return post<DatasetCommitResult>(
      `/api/v1/lakehouse/datasets/${encodeURIComponent(datasetId)}/rollback`, req, creds,
    );
  },

  getDatasetLineage(datasetId: string, params: DatasetLineageParams, creds: OwnerCredentials = {}) {
    return get<DatasetLineageView>(
      `/api/v1/lakehouse/datasets/${encodeURIComponent(datasetId)}/lineage`,
      { ...params },
      creds,
    );
  },

  planRetention(datasetId: string, req: RetentionPlanRequest, creds: OwnerCredentials = {}) {
    return post<RetentionPlan>(
      `/api/v1/lakehouse/datasets/${encodeURIComponent(datasetId)}/retention/plan`, req, creds,
      { timeoutMs: HEAVY_TIMEOUT_MS },
    );
  },

  executeRetention(datasetId: string, req: RetentionExecuteRequest, creds: OwnerCredentials = {}) {
    return post<RetentionExecuteResult>(
      `/api/v1/lakehouse/datasets/${encodeURIComponent(datasetId)}/retention/execute`, req, creds,
    );
  },
} as const;

export type { OwnerCredentials as LakehouseOwnerCredentials };
