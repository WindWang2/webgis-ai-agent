/**
 * Lakehouse fixtures — 29 端点 × 正常/空/错误三态。
 *
 * 本仓无 msw 依赖（勘察纪要 §2.3）：fixtures 服务的两个消费面是
 * ① API 层测试的 fetch stub 响应体（jsonOk/jsonErr 工厂）；
 * ② 组件测试的 `vi.mock('@/lib/api/lakehouse')` 返回值。
 * 形态与 lib/api/lakehouse.ts 的类型逐字段对齐（S1 勘察纪要 §1.3）。
 */
import type {
  CatalogEntry,
  CatalogPage,
  CubeBuildResult,
  CubeRevisionResult,
  CubeWindowResult,
  DatasetCommitRecord,
  DatasetDetail,
  DatasetLineageView,
  DatasetRef,
  DatasetVersion,
  GCExecuteResult,
  GCPlan,
  LabeledProjection,
  LabeledWindowResult,
  LakehouseDataset,
  LakehouseManifest,
  LineageView,
  ObjectReadResult,
  ObjectVerifyResult,
  PublishResult,
  RevokeResult,
  RetentionExecuteResult,
  RetentionPlan,
  RSCubeBuildResult,
  ScrubReport,
  StacCatalogResult,
  StacCollection,
  StacItem,
  VectorScanResult,
} from '@/lib/api/lakehouse';

export const SESSION_ID = 'sess-lakehouse-test';
export const OWNER_TOKEN = 'owner-token-test';
export const DATA_OBJECT_ID = 'a'.repeat(64);
export const DATASET_ID = 'b'.repeat(64);
export const VERSION_ID = 'c'.repeat(64);
export const CUBE_REF = 'ref:cube/0123456789abcdef';
export const PROJECT_ID = 'proj-lakehouse-test';

/* ── manifest / 对象 ──────────────────────────────────────────────────── */

export function makeManifest(
  overrides: Partial<LakehouseManifest> = {},
): LakehouseManifest {
  return {
    schema_version: 1,
    kind: 'zarr_cube',
    owner_scope: { session_id: SESSION_ID },
    environment_fingerprint: 'e'.repeat(64),
    content_sha256: 'd'.repeat(64),
    byte_size: 2048,
    content_blobs: [
      { path: 'store.zarr/.zmetadata', sha256: '1'.repeat(64), byte_size: 1024 },
    ],
    payload: { title: '测试 cube', times: ['2026-01-01', '2026-01-02'] },
    producer: { capability: 'geocompute' },
    source_refs: [],
    input_fingerprint: '',
    ...overrides,
  };
}

export function makeObjectRead(
  overrides: Partial<ObjectReadResult> = {},
): ObjectReadResult {
  return {
    success: true,
    data_object_id: DATA_OBJECT_ID,
    manifest: makeManifest(),
    ...overrides,
  };
}

export function makeVerifyResult(
  state = 'verified',
): ObjectVerifyResult {
  return { success: true, data_object_id: DATA_OBJECT_ID, state };
}

export function makeScrubReport(
  overrides: Partial<ScrubReport> = {},
): ScrubReport {
  return {
    success: true,
    data_object_id: DATA_OBJECT_ID,
    state: 'verified',
    chunks_total: 12,
    chunks_checked: 8,
    missing: [],
    corrupt: [],
    etag_mismatch: [],
    etag_checked: true,
    mode: 'sample',
    ...overrides,
  };
}

/* ── vector scan ─────────────────────────────────────────────────────── */

export function makeScanResult(
  featureCount = 2,
  overrides: Partial<VectorScanResult> = {},
): VectorScanResult {
  return {
    type: 'FeatureCollection',
    features: Array.from({ length: featureCount }, (_, i) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [116.4 + i * 0.01, 39.9] },
      properties: { id: i },
    })),
    properties: {
      row_groups_total: 4,
      row_groups_read: 1,
      truncated: false,
      window: [116.0, 39.0, 117.0, 40.0],
    },
    ...overrides,
  };
}

export const emptyScanResult: VectorScanResult = {
  type: 'FeatureCollection',
  features: [],
  properties: {
    row_groups_total: 0,
    row_groups_read: 0,
    truncated: false,
    window: [116.0, 39.0, 117.0, 40.0],
  },
};

/* ── cube ────────────────────────────────────────────────────────────── */

export function makeCubeBuildResult(
  overrides: Partial<CubeBuildResult> = {},
): CubeBuildResult {
  return {
    status: 'success',
    success: true,
    ref: CUBE_REF,
    cube_id: '0123456789abcdef',
    path: '/data/store.zarr',
    title: '测试 cube',
    times: ['2026-01-01', '2026-01-02'],
    steps: 2,
    content_fingerprints: { '/data/src.tif': '2'.repeat(64) },
    published: true,
    durable: 'published',
    data_object_id: DATA_OBJECT_ID,
    manifest: '/data/manifests/aa.json',
    content_sha256: 'd'.repeat(64),
    byte_size: 2048,
    deduped: false,
    entry_count: 2,
    ...overrides,
  };
}

/** published:false 降级：durable 细节键全部缺失（TS Optional 的存在理由）。 */
export function makeCubeBuildUnpublished(): CubeBuildResult {
  return {
    status: 'success',
    success: true,
    ref: CUBE_REF,
    cube_id: '0123456789abcdef',
    path: '/data/store.zarr',
    title: '测试 cube',
    times: ['2026-01-01'],
    steps: 1,
    content_fingerprints: {},
    published: false,
    reason: 'manifest store unavailable',
  };
}

export function makeCubeRevisionResult(
  overrides: Partial<CubeRevisionResult> = {},
): CubeRevisionResult {
  return {
    ...makeCubeBuildResult({ ref: 'ref:cube/fedcba9876543210' }),
    revision_of: CUBE_REF,
    first_step_preview: { band_0: [[[1, 2], [3, 4]]] },
    ...overrides,
  };
}

export function makeCubeWindowResult(
  overrides: Partial<CubeWindowResult> = {},
): CubeWindowResult {
  return {
    bands: {
      band_0: [
        [
          [1, 2],
          [3, 4],
        ],
      ],
    },
    times: ['2026-01-01'],
    crs: 'EPSG:4326',
    transform: [0.01, 0, 116.0, 0, -0.01, 40.0],
    nodata: -9999,
    ref: CUBE_REF,
    ...overrides,
  };
}

export function makeLabeledProjection(
  overrides: Partial<LabeledProjection> = {},
): LabeledProjection {
  return {
    labeled: true,
    cube_schema_version: 3,
    dims: ['time', 'model', 'scenario', 'y', 'x'],
    shape: [2, 2, 2, 4, 4],
    variables: {
      temperature: { dims: ['time', 'model', 'scenario', 'y', 'x'], dtype: 'float32' },
    },
    dtype: 'float32',
    nodata: -9999,
    chunks: [1, 1, 1, 4, 4],
    crs: 'EPSG:4326',
    crs_checked: 'rasterio',
    coords_summary: {
      time: { kind: 'labels', n: 2, first: '2026-01-01', last: '2026-01-02' },
      model: { kind: 'labels', n: 2, first: 'ecmwf', last: 'gfs' },
      scenario: { kind: 'labels', n: 2, first: 'rcp45', last: 'rcp85' },
      y: { kind: 'grid', n: 4, start: 39.0, end: 40.0 },
      x: { kind: 'grid', n: 4, start: 116.0, end: 117.0 },
    },
    nodata_per_variable: { temperature: -9999 },
    ...overrides,
  };
}

export function makeLabeledWindowResult(
  overrides: Partial<LabeledWindowResult> = {},
): LabeledWindowResult {
  return {
    variables: {
      temperature: [
        [
          [1, 2],
          [3, 4],
        ],
      ],
    },
    coords: {
      time: ['2026-01-01'],
      model: ['ecmwf'],
      scenario: ['rcp45'],
      y: [39.5],
      x: [116.5],
    },
    slices: { time: [0, 1], model: [0, 1], scenario: [0, 1], y: [0, 1], x: [0, 1] },
    attrs: {
      crs: 'EPSG:4326',
      transform: [0.01, 0, 116.0, 0, -0.01, 40.0],
      nodata: -9999,
      nodata_per_variable: { temperature: -9999 },
      cube_schema_version: 3,
      dims: ['time', 'model', 'scenario', 'y', 'x'],
    },
    selection_plan: {
      cells: 4,
      touched_chunks: 1,
      total_chunks: 8,
      slices: { time: [0, 1], y: [0, 1], x: [0, 1] },
    },
    ref: CUBE_REF,
    ...overrides,
  };
}

export function makeRsCubeBuildResult(
  overrides: Partial<RSCubeBuildResult> = {},
): RSCubeBuildResult {
  return {
    ...makeCubeBuildResult({ title: 'rs cube' }),
    variables: ['cloud_mask', 'reflectance', 'sigma0'],
    labeled_projection: makeLabeledProjection({
      dims: ['time', 'band', 'polarization', 'y', 'x'],
      variables: {
        reflectance: { dims: ['time', 'band', 'y', 'x'], dtype: 'float32' },
        sigma0: { dims: ['time', 'polarization', 'y', 'x'], dtype: 'float32' },
        cloud_mask: { dims: ['time', 'y', 'x'], dtype: 'uint8' },
      },
      nodata_per_variable: undefined,
      cube_schema_version: 2,
    }),
    ...overrides,
  };
}

/* ── publish / revoke ────────────────────────────────────────────────── */

export function makePublishResult(
  overrides: Partial<PublishResult> = {},
): PublishResult {
  return {
    success: true,
    published: [
      {
        object_id: DATA_OBJECT_ID,
        artifact_id: 'art_lh_0123456789abcdef',
        revision_no: 1,
        revision_created: true,
        deduped: false,
      },
    ],
    unknown: [],
    forbidden: [],
    ...overrides,
  };
}

export function makeRevokeResult(
  overrides: Partial<RevokeResult> = {},
): RevokeResult {
  return {
    success: true,
    revoked: [DATA_OBJECT_ID],
    unknown: [],
    ...overrides,
  };
}

/* ── catalog / STAC ──────────────────────────────────────────────────── */

export function makeCatalogEntry(
  overrides: Partial<CatalogEntry> = {},
): CatalogEntry {
  return {
    object_id: DATA_OBJECT_ID,
    owner_type: 'session',
    owner_id: SESSION_ID,
    kind: 'zarr_cube',
    title: '测试 cube',
    producer_capability: 'geocompute',
    producer_tool: 'build_cube',
    workflow_run_id: null,
    tags: ['demo'],
    bbox: [116.0, 39.0, 117.0, 40.0],
    time_start: '2026-01-01T00:00:00+00:00',
    time_end: '2026-01-02T00:00:00+00:00',
    content_sha256: 'd'.repeat(64),
    byte_size: 2048,
    status: 'active',
    created_at: '2026-09-01T00:00:00+00:00',
    ...overrides,
  };
}

export function makeCatalogPage(
  count = 2,
  overrides: Partial<CatalogPage> = {},
): CatalogPage {
  return {
    items: Array.from({ length: count }, (_, i) =>
      makeCatalogEntry({ object_id: `${i}`.repeat(64), title: `条目 ${i}` }),
    ),
    count,
    total: String(count),
    total_bounded: true,
    limit: 50,
    offset: 0,
    next_offset: null,
    ...overrides,
  };
}

export const emptyCatalogPage: CatalogPage = {
  items: [],
  count: 0,
  total: '0',
  total_bounded: true,
  limit: 50,
  offset: 0,
  next_offset: null,
};

export function makeStacCollection(
  overrides: Partial<StacCollection> = {},
): StacCollection {
  return {
    type: 'Collection',
    stac_version: '1.0.0',
    id: `webgis-lakehouse-session-${SESSION_ID}`,
    description: 'Lakehouse STAC 投影',
    license: 'proprietary',
    extent: {
      spatial: { bbox: [[116.0, 39.0, 117.0, 40.0]] },
      temporal: { interval: [['2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z']] },
    },
    links: [{ rel: 'root', href: './collection.json', type: 'application/json' }],
    ...overrides,
  };
}

export function makeStacItem(
  overrides: Partial<StacItem> = {},
): StacItem {
  return {
    type: 'Feature',
    stac_version: '1.0.0',
    id: DATA_OBJECT_ID,
    geometry: {
      type: 'Polygon',
      coordinates: [
        [
          [116.0, 39.0],
          [117.0, 39.0],
          [117.0, 40.0],
          [116.0, 40.0],
          [116.0, 39.0],
        ],
      ],
    },
    bbox: [116.0, 39.0, 117.0, 40.0],
    properties: {
      datetime: '2026-01-01T00:00:00Z',
      'webgis:kind': 'zarr_cube',
      'webgis:owner_type': 'session',
      'webgis:owner_id': SESSION_ID,
      'webgis:content_sha256': 'd'.repeat(64),
      'webgis:byte_size': 2048,
    },
    assets: {
      data: {
        href: `webgis://data-object/${DATA_OBJECT_ID}`,
        title: '测试 cube',
        roles: ['data'],
        'webgis:data_object_id': DATA_OBJECT_ID,
      },
      metadata: {
        href: `webgis://manifest/${DATA_OBJECT_ID}`,
        title: 'DataObject manifest',
        roles: ['metadata'],
        'webgis:data_object_id': DATA_OBJECT_ID,
      },
    },
    links: [{ rel: 'root', href: './collection.json', type: 'application/json' }],
    ...overrides,
  };
}

export function makeStacResult(
  itemCount = 2,
  overrides: Partial<StacCatalogResult> = {},
): StacCatalogResult {
  return {
    collection: makeStacCollection(),
    items: Array.from({ length: itemCount }, (_, i) =>
      makeStacItem({ id: `${i}`.repeat(64) }),
    ),
    skipped: [],
    ...overrides,
  };
}

export const emptyStacResult: StacCatalogResult = {
  collection: makeStacCollection({
    extent: {
      spatial: { bbox: [] },
      temporal: { interval: [[null, null]] },
    },
  }),
  items: [],
  skipped: ['f'.repeat(64)],
};

/* ── GC / 血缘 ───────────────────────────────────────────────────────── */

export function makeGCPlan(overrides: Partial<GCPlan> = {}): GCPlan {
  return {
    success: true,
    candidates: ['9'.repeat(64)],
    deletable_blobs: ['8'.repeat(64)],
    protected_count: 2,
    scanned_manifests: 10,
    watermark: 1_789_000_000.5,
    grace_hours: 72,
    requested_grace_hours: 72,
    registry_ttl_floor_hours: 24,
    token: '7'.repeat(64),
    ...overrides,
  };
}

export function makeGCExecuteResult(
  overrides: Partial<GCExecuteResult> = {},
): GCExecuteResult {
  return {
    success: true,
    deleted_manifests: ['9'.repeat(64)],
    deleted_blobs: ['8'.repeat(64)],
    skipped_protected_blobs: [],
    skipped_stale: 0,
    ...overrides,
  };
}

export function makeLineageView(
  overrides: Partial<LineageView> = {},
): LineageView {
  return {
    success: true,
    root: DATA_OBJECT_ID,
    ancestors: [
      { id: '6'.repeat(64), kind: 'vector_parquet', depth: 1 },
      { id: '5'.repeat(64), kind: 'cog_raster', depth: 2 },
    ],
    edges: [
      { child: DATA_OBJECT_ID, parent: '6'.repeat(64) },
      { child: '6'.repeat(64), parent: '5'.repeat(64) },
    ],
    truncated: false,
    ...overrides,
  };
}

/* ── dataset 版本层 ──────────────────────────────────────────────────── */

export function makeDataset(
  overrides: Partial<LakehouseDataset> = {},
): LakehouseDataset {
  return {
    dataset_id: DATASET_ID,
    owner_type: 'session',
    owner_id: SESSION_ID,
    name: '气温观测',
    description: '测试数据集',
    default_branch: 'main',
    cube_contract: { dims: ['time', 'y', 'x'], variables: ['temperature'] },
    created_at: '2026-09-01T00:00:00+00:00',
    ...overrides,
  };
}

export function makeDatasetRef(
  overrides: Partial<DatasetRef> = {},
): DatasetRef {
  return {
    ref_type: 'branch',
    ref_name: 'main',
    version_id: VERSION_ID,
    generation: 3,
    created_at: '2026-09-02T00:00:00+00:00',
    ...overrides,
  };
}

export function makeDatasetVersion(
  overrides: Partial<DatasetVersion> = {},
): DatasetVersion {
  return {
    version_id: VERSION_ID,
    parent_version_id: null,
    data_object_id: DATA_OBJECT_ID,
    content_sha256: 'd'.repeat(64),
    byte_size: 4096,
    branch: 'main',
    action: 'commit',
    provenance: { algorithm: 'build_cube' },
    workflow_run_id: null,
    created_at: '2026-09-02T00:00:00+00:00',
    ...overrides,
  };
}

export function makeDatasetDetail(
  overrides: Partial<DatasetDetail> = {},
): DatasetDetail {
  return {
    success: true,
    dataset: makeDataset(),
    refs: [makeDatasetRef(), makeDatasetRef({ ref_type: 'tag', ref_name: 'v1' })],
    head: makeDatasetRef(),
    descriptor: {
      schema_version: 1,
      kind: 'dataset_descriptor',
      owner_scope: { session_id: SESSION_ID },
      name: '气温观测',
      description: '测试数据集',
      default_branch: 'main',
      cube_contract: { dims: ['time', 'y', 'x'] },
    },
    ...overrides,
  };
}

export function makeCommitRecord(
  overrides: Partial<DatasetCommitRecord> = {},
): DatasetCommitRecord {
  return {
    schema_version: 1,
    kind: 'dataset_commit',
    dataset_id: DATASET_ID,
    parent_version_id: null,
    data_object_id: DATA_OBJECT_ID,
    content_sha256: 'd'.repeat(64),
    action: 'commit',
    provenance: {},
    ...overrides,
  };
}

export function makeDatasetLineage(
  overrides: Partial<DatasetLineageView> = {},
): DatasetLineageView {
  return {
    success: true,
    version_id: VERSION_ID,
    chain: [
      makeDatasetVersion(),
      makeDatasetVersion({
        version_id: '4'.repeat(64),
        parent_version_id: null,
        created_at: '2026-09-01T00:00:00+00:00',
      }),
    ],
    depth: 2,
    truncated: false,
    ...overrides,
  };
}

export function makeRetentionPlan(
  overrides: Partial<RetentionPlan> = {},
): RetentionPlan {
  return {
    success: true,
    dataset_row_id: 'row-uuid-1',
    dataset_id: DATASET_ID,
    candidates: ['4'.repeat(64)],
    candidate_count: 1,
    protected_by_refs: 1,
    protected_by_window: 0,
    scanned_versions: 3,
    max_versions: 64,
    min_age_hours: 72,
    token: '0'.repeat(64),
    ...overrides,
  };
}

export function makeRetentionExecuteResult(
  overrides: Partial<RetentionExecuteResult> = {},
): RetentionExecuteResult {
  return {
    success: true,
    dataset_row_id: 'row-uuid-1',
    dataset_id: DATASET_ID,
    pruned: ['4'.repeat(64)],
    pruned_count: 1,
    skipped_protected: [],
    skipped_overflow: 0,
    ...overrides,
  };
}

/* ── 错误态 ──────────────────────────────────────────────────────────── */

/** FastAPI HTTPException 信封：detail 字符串（typed code 不出后端）。 */
export function apiErrorDetail(detail: string): { detail: string } {
  return { detail };
}

export const ERROR_STATES = {
  /** 404：不存在 / 无权（fail-closed，不区分两种语义）。 */
  notFound: { status: 404, body: apiErrorDetail('data object not found') },
  /** 400：契约违例。 */
  badRequest: { status: 400, body: apiErrorDetail('cube window read requires at least one bounded slice') },
  /** 422：路由层前置校验。 */
  unprocessable: { status: 422, body: apiErrorDetail('labeled window read requires at least one label/bbox/index-slice selection') },
  /** 409：计划漂移 / ref 冲突。 */
  conflict: { status: 409, body: apiErrorDetail('plan is stale — candidates changed since plan token was issued') },
  /** 403：admin 端点。 */
  forbidden: { status: 403, body: apiErrorDetail('Admin privileges required') },
  /** 410：项目对象 manifest 不可解析。 */
  gone: { status: 410, body: apiErrorDetail('data object manifest no longer resolvable') },
} as const;
