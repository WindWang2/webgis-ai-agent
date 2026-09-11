/**
 * Visual baseline harness (UI Visual System V4).
 *
 * Drives the real app in headless Chromium across the responsive matrix
 * (1920/1440/1366/1024 x light/dark) and captures one PNG per surface, so a
 * visual convergence pass can be reviewed as a before/after diff instead of by
 * eye on a single window size.
 *
 * The backend is never contacted: every `API_BASE` call and every basemap tile
 * request is intercepted and answered from the fixtures below, which keeps the
 * shots deterministic and lets the run work offline.
 *
 * Usage (from `frontend/`):
 *   node test/visual/capture.mjs --out .visual/before
 *   node test/visual/capture.mjs --out .visual/after
 *
 * Assumes a dev/prod server is already listening on --base (default :3311).
 */

import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

const args = process.argv.slice(2);
const argOf = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
};

const OUT_ROOT = path.resolve(argOf('out', '.visual/shots'));
const BASE_URL = argOf('base', 'http://localhost:3311');
const ONLY = argOf('only', '');

const VIEWPORTS = [
  { name: '1920x1080', width: 1920, height: 1080 },
  { name: '1440x900', width: 1440, height: 900 },
  { name: '1366x768', width: 1366, height: 768 },
  { name: '1024x768', width: 1024, height: 768 },
];

const THEMES = ['light', 'dark'];

/**
 * Each surface is reached the way a user reaches it — by clicking the nav rail
 * tab or the top-bar button — so the shot proves the surface is production
 * reachable, not merely that a file exists.
 */
const SURFACES = [
  { name: 'chat', tab: '对话' },
  /*
    ContextPanel shell at the 280px resize floor on the most common tab
    (short description + composer) — companion to results-detail-narrow,
    which covers the longest header description + badge. Same real keyboard
    resize path on the drag separator.
  */
  { name: 'chat-narrow', tab: '对话', panelArrows: -6 },
  { name: 'project', tab: '项目' },
  { name: 'data', tab: '数据' },
  { name: 'layers', tab: '图层' },
  { name: 'analysis', tab: '分析' },
  { name: 'tasks', tab: '任务' },
  { name: 'map-studio', tab: '制图' },
  { name: 'settings', button: '设置' },
  { name: 'template-gallery', button: '模板库' },
  { name: 'history', button: '历史会话' },
  { name: 'panel-collapsed', tab: '图层', collapse: true },
  { name: 'hud-expanded', tab: '对话', hud: true },
  /*
    Result Workbench: the results registry is session-scoped and fed only by the
    chat SSE stream, so these seed it through the production path — the stubbed
    `/chat/stream` response below replays tool_call + step_result events through
    the same normalizer that fills the registry on screen. The turn produces
    five results covering the audit matrix: completed vector w/ metrics + bound
    layer + long ref, statistic w/ metrics, warning (unreachable facilities),
    partial (grid too dense) and failed (correction hint).
  */
  { name: 'results-empty', tab: '结果' },
  { name: 'results-list', tab: '结果', seedResults: true },
  { name: 'results-detail', tab: '结果', seedResults: true, openResult: '热点分析' },
  { name: 'results-detail-warning', tab: '结果', seedResults: true, openResult: '等时圈分析' },
  { name: 'results-detail-partial', tab: '结果', seedResults: true, openResult: '核密度表面' },
  { name: 'results-detail-failed', tab: '结果', seedResults: true, openResult: '时空聚类' },
  /*
    Narrowest panel (280px, the resize clamp floor) and widest (420px) via the
    real keyboard resize path on the drag separator — proves the detail survives
    both ends of the ContextPanel width range.
  */
  {
    name: 'results-detail-narrow',
    tab: '结果',
    seedResults: true,
    openResult: '热点分析',
    panelArrows: -6,
  },
  {
    name: 'results-detail-wide',
    tab: '结果',
    seedResults: true,
    openResult: '热点分析',
    panelArrows: 7,
  },
  /*
    Map overlays need layers in the store, and the store is deliberately not on
    `window`. Rather than reach past the app, these two restore a session: the
    real production path (`use-workspace-session`) reads `map-state`, pulls each
    layer's geometry from `/api/v1/layers/data/<ref>` and adds it to the store,
    all of which the fixtures below answer. So the legend stack and the heatmap
    legend are captured through the same code path that puts them on screen in
    production.
  */
  { name: 'map-legends', tab: '图层', restoreSession: true },
  { name: 'map-legends-collapsed', tab: '图层', restoreSession: true, collapse: true },
  /*
    Ops console (ADR-0142): the 运维 rail tab hosts the cluster dashboard /
    plan console / runtime section / breaker panel / system health, plus a
    fullscreen wallboard. Populated fixtures above → information-dense shots.
  */
  { name: 'ops-cluster', tab: '运维' },
  { name: 'ops-runtime', tab: '运维', opsView: 'runtime' },
  { name: 'ops-breaker', tab: '运维', opsView: 'breaker' },
  { name: 'ops-health', tab: '运维', opsView: 'health' },
  { name: 'ops-wallboard', tab: '运维', opsWallboard: true },
];

/** Two features are enough to paint a thematic fill and a heatmap point. */
const FEATURES = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: { name: '朝阳区', pop_density: 8421, value: 0.62 },
      geometry: {
        type: 'Polygon',
        coordinates: [[[116.40, 39.90], [116.52, 39.90], [116.52, 40.00], [116.40, 40.00], [116.40, 39.90]]],
      },
    },
    {
      type: 'Feature',
      properties: { name: '海淀区', pop_density: 12750, value: 0.88 },
      geometry: {
        type: 'Polygon',
        coordinates: [[[116.24, 39.94], [116.38, 39.94], [116.38, 40.05], [116.24, 40.05], [116.24, 39.94]]],
      },
    },
  ],
};

/** Layers as the session-restore path expects them: `_refId` + `legend_spec`. */
const RESTORED_LAYERS = [
  {
    id: 'lyr-graduated',
    name: '区县人口密度（人/km²）',
    type: 'vector',
    visible: true,
    opacity: 0.85,
    group: 'analysis',
    _refId: 'ref:graduated',
    style: { color: '#3182bd' },
    legend_spec: {
      type: 'graduated',
      field: 'pop_density',
      breaks: [0, 4000, 8000, 12000, 16000],
      palette: 'Blues',
      palette_colors: ['#eff3ff', '#bdd7e7', '#6baed6', '#2171b5'],
    },
  },
  {
    id: 'lyr-categorical',
    name: '用地分类',
    type: 'vector',
    visible: true,
    opacity: 0.9,
    group: 'analysis',
    _refId: 'ref:categorical',
    style: { color: '#16a34a' },
    legend_spec: {
      type: 'categorical',
      field: 'landuse',
      categories: [
        { key: 'r', color: '#f4a261', label: '居住用地' },
        { key: 'c', color: '#e76f51', label: '商业用地' },
        { key: 'g', color: '#2a9d8f', label: '绿地与广场' },
      ],
    },
  },
  {
    id: 'lyr-heatmap',
    name: '商业 POI 热力',
    type: 'heatmap',
    visible: true,
    opacity: 0.7,
    group: 'analysis',
    _refId: 'ref:heatmap',
    style: { renderType: 'heatmap', palette: 'inferno' },
  },
];

const NOW = '2026-01-01T08:30:00Z';

/* ── Result Workbench seed turn ───────────────────────────────────────────────
 *
 * One chat turn whose SSE replay fills the results registry through the real
 * normalizer. Refs are deliberately long to exercise truncation; the hotspot
 * ref also gets a descriptor below so the Output section renders enriched
 * metadata (count / geometry / bbox / bytes). */

const HOTSPOT_REF = 'ref:hotspot-8f3a2b7c4d5e6f7a8b9c0d1e2f3a4b5c';
const ISO_REF = 'ref:iso-2c4b6e8a0d1f3a5b7c9e';

function sse(name, payload) {
  return `event: ${name}\ndata: ${JSON.stringify(payload)}\n\n`;
}

const toolCall = (name, args) => sse('tool_call', { name, arguments: JSON.stringify(args) });

const RESULTS_SSE_BODY = [
  sse('session', { session_id: 's-1' }),
  sse('task_start', { task_id: 't-1', session_id: 's-1' }),
  sse('token', { content: '正在对商业 POI 执行空间分析…' }),
  // 1) hotspot — completed vector w/ metrics, legend + visible bound layer (long ref)
  toolCall('hotspot_analysis', {
    geojson: HOTSPOT_REF,
    value_field: 'poi_weight',
    distance_band: 1000,
  }),
  sse('step_result', {
    task_id: 't-1',
    step_id: 'sr-hotspot',
    tool: 'hotspot_analysis',
    geojson_ref: HOTSPOT_REF,
    result: {
      success: true,
      summary: '已完成商业 POI 热点分析：识别出 12 个显著热点与 7 个冷点，结果已挂载为图层。',
      bbox: [116.2814, 39.7842, 116.7351, 40.1213],
      data: { hot_spots_count: 12, cold_spots_count: 7, distance_band_m: 1000 },
      legend_spec: {
        type: 'graduated',
        field: 'gi_bin',
        breaks: [0, 3, 5, 7, 9],
        palette: 'RdYlBu',
        palette_colors: ['#2166ac', '#92c5de', '#f7f7f7', '#f4a582', '#b2182b'],
      },
      runtime_patch: { visible: true },
    },
  }),
  // 2) Moran's I — statistic with metrics, no layer
  toolCall('moran_i', { geojson: 'ref:census-blocks', value_field: 'pop_density' }),
  sse('step_result', {
    task_id: 't-1',
    step_id: 'sr-moran',
    tool: 'moran_i',
    result: {
      success: true,
      summary: '全局空间自相关计算完成，呈现显著聚类模式。',
      data: { moran_i: 0.4231, expected_i: -0.0114, p_value: 0.001, pattern: 'Clustered', n_features: 88 },
    },
  }),
  // 3) isochrone — warning (unreachable facilities), hidden layer, params
  toolCall('isochrone_network', {
    network_layer: 'ref:road-network',
    source_points: 'ref:facilities-clinic',
    travel_time: 15,
    mode: 'walk',
  }),
  sse('step_result', {
    task_id: 't-1',
    step_id: 'sr-iso',
    tool: 'isochrone_network',
    geojson_ref: ISO_REF,
    result: {
      success: true,
      summary: 'Isochrone built. 3 facility(ies) unreachable (disconnected from the road network).',
      bbox: [116.1073, 39.7211, 116.8892, 40.1987],
    },
  }),
  // 4) KDE — partial (grid too dense)
  toolCall('kde_surface', { geojson: 'ref:poi-all', bandwidth: 1200, cell_size: 30 }),
  sse('step_result', {
    task_id: 't-1',
    step_id: 'sr-kde',
    tool: 'kde_surface',
    result: {
      success: true,
      summary: 'Kernel density surface estimated. Warning: grid too dense, output resampled to 2 m cells.',
      data: { count: 40320, bandwidth_m: 1200, grid_size: [512, 384], stats: { min: 0.0004, max: 128.4419, mean_density: 6.2041 } },
    },
  }),
  // 5) ST-DBSCAN — failed with correction hint
  toolCall('st_dbscan', { geojson: 'ref:taxi-trips', eps: 300, min_samples: 8, timestamp_field: 'ts' }),
  sse('step_result', {
    task_id: 't-1',
    step_id: 'sr-stdbscan',
    tool: 'st_dbscan',
    result: {
      success: false,
      error_type: 'VALIDATION_ERROR',
      summary: '时间字段解析失败。',
      correction_hint: '请提供 ISO 8601 格式的时间字段（当前字段 ts 含非数值）。',
    },
  }),
  sse('token', { content: '五个分析步骤已完成，结果已收入结果工作台。' }),
  sse('task_complete', { task_id: 't-1' }),
  'data: [DONE]\n\n',
].join('');

const project = (id, name, description, status = 'active') => ({
  id,
  name,
  description,
  status,
  metadata_json: {},
  created_at: NOW,
  updated_at: NOW,
});

const dataset = (id, name, sourceType, quality) => ({
  id,
  project_id: 'p-1',
  name,
  source_type: sourceType,
  source_ref: `postgis://gis/${id}`,
  schema_profile: { fields: 12 },
  crs: 'EPSG:4326',
  quality_status: quality,
  version_fingerprint: 'a1b2c3d4',
  created_at: NOW,
});

const job = (id, name, status, progress, message, extra = {}) => ({
  id,
  kind: 'analysis',
  name,
  status,
  progress,
  message,
  cancellable: status === 'running',
  retryable: status === 'failed',
  active: status === 'running' || status === 'queued',
  attempt: 1,
  session_id: 'visual-baseline',
  project_id: 'p-1',
  agent_task_id: null,
  agent_step_id: null,
  background_job_ids: [],
  error: status === 'failed' ? '数据源连接超时（30s）' : null,
  result_ref: status === 'completed' ? 'artifact://result/91f3' : null,
  step_count: 4,
  created_at: NOW,
  started_at: NOW,
  finished_at: status === 'running' ? null : NOW,
  cancel_requested_at: null,
  ...extra,
});

const source = (id, name, sourceType, status) => ({
  id,
  name,
  source_type: sourceType,
  endpoint_url: `https://gis.example.org/${id}/wfs`,
  status,
  capabilities: ['query', 'preview', 'materialize'],
  connection_profile: {},
  last_health_check: NOW,
});

const catalogItem = (id, name, title, geometryType, featureType) => ({
  id,
  source_id: 'src-1',
  name,
  title,
  description: '来自城市空间数据底座的要素集，含行政区划与人口统计属性。',
  geometry_type: geometryType,
  feature_type: featureType,
  crs: 'EPSG:4326',
  bbox: [116.0, 39.6, 116.8, 40.3],
  meta_profile: { feature_count: 4213 },
  updated_at: NOW,
});

const template = (id, kind, name, category, description) => ({
  id,
  kind,
  name,
  category,
  keywords: ['城市', '专题'],
  description,
  payload: {},
  is_builtin: true,
  version: 1,
  created_at: NOW,
  updated_at: NOW,
});

/**
 * Shape-correct fixtures for every endpoint the shell touches on mount. They
 * are deliberately populated (and include a failed job / degraded source) so
 * the shots exercise real information density instead of only empty states.
 */
const FIXTURES = [
  // Ops console (ADR-0142): cluster control plane + workflow runtime + health
  // surface — populated states so the dashboard shots carry information
  // density. Registered before the generic fixtures; the ops panels poll
  // these on mount.
  [
    /\/api\/v1\/geocompute\/cluster\/metrics/,
    {
      runs_by_status: { queued: 4, leased: 3, running: 9, completed: 128, failed: 6, cancelled: 2, preempted: 1 },
      queue_depth: 5, inflight: 12, completed: 128, failed: 6, cancelled: 2,
      preempted_total: 3, lease_loss_total: 1,
      cancel_latency: { p50_s: 0.8, p95_s: 2.4, samples: 42 },
      queue_wait: { p50_s: 1.5, p95_s: 9.1, samples: 128 },
      waiting_by_profile: { light_cpu: 2, raster: 2, heavy_cpu: 1 },
      events_counters: { event_rejected_invalid: 0, event_budget_exhausted_total: 1, event_append_failed_total: 0 },
      workers: { live: 6, by_role: { worker: 5, coordinator: 1 }, profile_slots: { light_cpu: 12, heavy_cpu: 6, raster: 8 }, gpu_workers: 1 },
      leader: { count: 1, ids: ['coord-primary'] },
      ledger: [{ scope_key: 'global', rows: 154203, bytes: 48120000000, limit_rows: null, limit_bytes: null }],
      resource_rejections: { rows: 2, bytes: 1, units: 4, mem_mb: 1, gpu: 0 },
      oom_avoided: 3, gpu_fallbacks: 1,
      spill: { count: 18, bytes: 6400000000, rehydrate_hits: 11, rehydrate_misses: 7 },
      transfer: { bytes_total: 27140000000 },
      cache: { worker_cache_hits: 431 },
      lineage: { node_completed: 2140, node_reused: 312, node_lost: 9, partition_planned: 480, speculative_dispatched: 57, poison_quarantined: 3 },
      utilization: { reserved_units: 14, capacity_units: 26, ratio: 0.538 },
      quarantine: [{ owner_scope: 'owner:a1b2c3d4e5f6…', fingerprint: '9f2c1e07aa4b13dc', failure_count: 4, active: true, last_error_code: 'CRS_MISMATCH' }],
    },
  ],
  [
    /\/api\/v1\/geocompute\/cluster\/workers/,
    {
      live: 3,
      workers: [
        { worker_id: 'w-3f9d2a11c4e5', role: 'coordinator', profiles: { light_cpu: 2, heavy_cpu: 1 }, capability: { gpus: [], disk_free_mb: 204800 }, heartbeat_age_s: 2.1, cache_entries: 0, cache_bytes: 0 },
        { worker_id: 'w-88aa00bb21cc', role: 'worker', profiles: { raster: 4, light_cpu: 4 }, capability: { gpus: [{ model: 'A10G', mem_mb: 24576 }] }, heartbeat_age_s: 5.4, cache_entries: 214, cache_bytes: 8410000000 },
        { worker_id: 'w-77ccb43d9012', role: 'worker', profiles: { light_cpu: 2 }, capability: null, heartbeat_age_s: 47.2, cache_entries: 12, cache_bytes: 310000000 },
      ],
    },
  ],
  [
    /\/api\/v1\/geocompute\/cluster\/runs\/stuck/,
    {
      count: 1,
      runs: [
        {
          run_id: 'run-stuck-001', status: 'running', owner_scope: 'owner:test', plan_fingerprint: 'aa77bb00', session_id: 'sess-1',
          priority: 5, attempts: 3, preempts: 0, lease_epoch: 4, cancel_requested_at: null, yield_requested_at: null,
          error_code: null, required_profiles: ['light_cpu'], resource_request: null,
          created_at: new Date(Date.now() - 31 * 60000).toISOString(),
          started_at: new Date(Date.now() - 30 * 60000).toISOString(),
          terminal_at: null, heartbeat_at: new Date(Date.now() - 9 * 60000).toISOString(),
          lease_expires_at: new Date(Date.now() - 6 * 60000).toISOString(),
          id: 1, tenant_key: 't:test', coordinator_id: 'coord-primary', dispatch_seq: 7,
        },
      ],
    },
  ],
  [
    /\/api\/v1\/geocompute\/runs\/run-live-100\/events/,
    { run_id: 'run-live-100', events: [], after_id: 0, count: 0 },
  ],
  [
    /\/api\/v1\/geocompute\/runs\/run-live-100/,
    { run_id: 'run-live-100', plan_fingerprint: 'g1', status: 'running', source: 'cluster', priority: 5, attempts: 1, preempts: 0, error_code: null, required_profiles: ['light_cpu'], progress: { settled: 2, done: 3, failed: 0, total: 4 } },
  ],
  [
    /\/api\/v1\/geocompute\/runs\?/,
    { runs: [], terminal_snapshots: [], limit: 20, offset: 0 },
  ],
  [
    /\/api\/v1\/workflow-runtime\/instances\/wi-ops-1\/recompute-plan/,
    { success: true, instance_id: 'wi-ops-1', stale: 2, counts: {}, decisions: [], recompute: ['spatial_join'], reuse: ['extract_sources'], explanations: ['sources refreshed'], changed_dimensions: ['data'] },
  ],
  [
    /\/api\/v1\/workflow-runtime\/instances\/wi-ops-1/,
    {
      success: true,
      instance: {
        instance_id: 'wi-ops-1', package_id: 'pkg.drainage.analysis', package_version: '1.4.2', package_fingerprint: 'pkgfp88',
        status: 'running', revision: 7, cancel_requested: false,
        nodes: [
          { node_id: 'extract_sources', state: 'SUCCEEDED', attempts: 1, error_code: null, reused: true, binding_violations: [] },
          { node_id: 'spatial_join', state: 'RUNNING', attempts: 1, error_code: null, reused: false, binding_violations: [] },
          { node_id: 'validate_geom', state: 'FAILED', attempts: 3, error_code: 'GEOM_INVALID', reused: false, binding_violations: [] },
        ],
        counts: { SUCCEEDED: 1, RUNNING: 1, FAILED: 1 },
        decisions: [], pending_changes: [], error_code: null, error_detail: null,
        methodology_family: 'drainage-network-analysis',
        explain: { why_recomputed: ['sources refreshed'], why_reused: ['topology unchanged'], blocked: [] },
      },
    },
  ],
  [
    /\/api\/v1\/workflow-runtime\/instances/,
    { success: true, instances: [{ instance_id: 'wi-ops-1', package_id: 'pkg.drainage.analysis', status: 'running', revision: 7 }] },
  ],
  [/\/api\/v1\/health\/live/, { status: 'alive' }],
  [/\/api\/v1\/health/, { status: 'healthy', timestamp: '2026-01-01T08:30:00Z', service: 'WebGIS AI Agent', version: '0.1.3', agent_runtime: 'pi', pi_workers_alive: '4/4' }],
  [/\/api\/v1\/ready/, { ready: true }],
  [
    /\/api\/v1\/status\/detailed/,
    {
      status: 'ok',
      components: {
        db: { status: 'ok', latency_ms: 2.4, detail: null },
        redis: { status: 'ok', latency_ms: 0.6, detail: null },
        llm: { status: 'ok', latency_ms: 412.8, detail: null },
        worker: { status: 'ok', latency_ms: 5.1, detail: null },
        object_store: { status: 'not_configured', latency_ms: null, detail: 'S3 not configured' },
      },
      stuck_jobs: 1, refresh_age_s: 0.4,
    },
  ],
  [/\/api\/v1\/version/, { version: '0.1.3', commit: '8b5b8375abc', python: '3.12.7', extensions_api: '2', timestamp: '2026-01-01T08:30:00Z' }],
  [/\/api\/v1\/tasks\/jobs\?.*active_only=true/, { jobs: [{ id: 'job-1', kind: 'workflow', name: '流域分析', status: 'running', progress: 42, active: true }], has_active: true, poll_after_ms: 3000 }],

  // Result Workbench seeding: the chat turn replay above is answered as an SSE
  // stream (must precede the generic localhost JSON branch in installStubs).
  // Kept here next to the other fixtures for discoverability; matched by URL in
  // installStubs, not by this list.
  // Descriptor for the hotspot ref — the only enriched output (metadata-first).
  [
    /\/api\/v1\/layers\/descriptor\//,
    (url) =>
      decodeURIComponent(url).includes(HOTSPOT_REF)
        ? {
            ref_id: HOTSPOT_REF,
            feature_count: 1284,
            geometry_types: ['Polygon'],
            bbox: [116.2814, 39.7842, 116.7351, 40.1213],
            mvt_capable: true,
            raster_capable: false,
            estimated_bytes: 482304,
          }
        : {},
  ],
  // Session map state drives the layer restore, and therefore the legends.
  // Order matters: these patterns are more specific than the session list below.
  [
    /\/api\/v1\/chat\/sessions\/[^/]+\/map-state/,
    {
      map_state: {
        viewport: { center: [116.39, 39.95], zoom: 10.4, bearing: 0, pitch: 0 },
        layers: RESTORED_LAYERS,
      },
    },
  ],
  [/\/api\/v1\/layers\/data\//, FEATURES],
  [
    /\/api\/v1\/chat\/sessions\/[^/]+$/,
    {
      session_id: 's-1',
      title: '北京商业 POI 热力分析',
      messages: [
        { role: 'user', content: '统计北京各区人口密度并出图', created_at: NOW },
        { role: 'assistant', content: '已生成分级设色图与商业 POI 热力图。', created_at: NOW },
      ],
    },
  ],
  [
    /\/api\/v1\/tasks\/jobs/,
    {
      jobs: [
        job('job-1', '北京市人口密度 H3 聚合', 'running', 62, '正在计算 H3 r8 网格聚合…'),
        job('job-2', '等时圈可达性分析（15 分钟）', 'completed', 100, '已生成 3 个等时圈'),
        job('job-3', 'ST-DBSCAN 时空聚类', 'failed', null, '任务失败'),
        job('job-4', '路网中心性计算', 'queued', null, '排队中'),
      ],
      has_active: true,
      poll_after_ms: null,
    },
  ],
  [
    /\/api\/v1\/data-fabric\/sources/,
    {
      sources: [
        source('src-1', '城市空间数据底座', 'postgis', 'healthy'),
        source('src-2', '国家地理信息公共服务平台', 'wfs', 'active'),
        source('src-3', '气象格网服务', 'wcs', 'degraded'),
      ],
    },
  ],
  [
    /\/api\/v1\/data-fabric\/catalog/,
    {
      total: 3,
      limit: 20,
      offset: 0,
      items: [
        catalogItem('ci-1', 'admin_districts', '行政区划面（区县级）', 'MultiPolygon', 'vector'),
        catalogItem('ci-2', 'poi_commercial', '商业 POI 点位', 'Point', 'vector'),
        catalogItem('ci-3', 'road_network', '城市路网中心线', 'MultiLineString', 'vector'),
      ],
    },
  ],
  [
    /\/api\/v1\/templates/,
    [
      template('t-1', 'thematic', '人口密度分级设色', '专题制图', '五级自然断点分级，适用于区县人口密度。'),
      template('t-2', 'basemap', '深色影像底图', '底图', '低饱和深色底图，突出专题图层。'),
      template('t-3', 'layout', 'A3 横向出图版式', '版式', '含图例、比例尺、指北针与元数据栏。'),
      template('t-4', 'symbology', '路网层级符号化', '符号化', '按道路等级分配线宽与颜色。'),
    ],
  ],
  [
    /\/api\/v1\/chat\/sessions/,
    {
      sessions: [
        { session_id: 's-1', title: '北京商业 POI 热力分析', updated_at: NOW, message_count: 12 },
        { session_id: 's-2', title: '15 分钟生活圈可达性', updated_at: NOW, message_count: 8 },
        { session_id: 's-3', title: '路网中心性与拥堵关联', updated_at: NOW, message_count: 21 },
      ],
    },
  ],
  [/\/api\/v1\/chat\/skills/, { skills: [] }],
  [/\/api\/v1\/layer-types/, { layer_types: [] }],
  [
    /\/projects\/[^/]+\/datasets/,
    [
      dataset('d-1', '区县人口统计 2025', 'postgis', 'passed'),
      dataset('d-2', '商业 POI 点位', 'wfs', 'warning'),
    ],
  ],
  [/\/projects\/[^/]+\/workflows/, []],
  [
    /\/projects\b/,
    [
      project('p-1', '城市空间体检 2026', '区县级人口、用地与可达性综合评估。'),
      project('p-2', '生活圈可达性专题', '15 分钟生活圈等时圈与设施覆盖分析。'),
    ],
  ],
];

/** 1x1 transparent PNG — stands in for every basemap tile. */
const BLANK_TILE = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
);

async function installStubs(page) {
  await page.route('**/*', async (route) => {
    const url = route.request().url();

    // Cover BOTH dev API ports: `next dev` without env vars defaults to
    // :8001 (lib/api/config.ts), while docs/docker setups pass
    // NEXT_PUBLIC_API_URL=http://localhost:8000. Plain `npm run dev` works.
    if (/^https?:\/\/(localhost|127\.0\.0\.1):800[01]\//.test(url)) {
      // The seeded chat turn replays as an SSE stream through the production
      // parser/normalizer (fills the Result Workbench registry).
      if (/\/api\/v1\/chat\/stream/.test(url)) {
        return route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          headers: { 'cache-control': 'no-cache' },
          body: RESULTS_SSE_BODY,
        });
      }
      const hit = FIXTURES.find(([re]) => re.test(url));
      const payload = hit ? (typeof hit[1] === 'function' ? hit[1](url) : hit[1]) : [];
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(payload),
      });
    }

    if (/\.(png|jpe?g|webp)(\?|$)/i.test(url) && !url.startsWith(BASE_URL)) {
      return route.fulfill({ status: 200, contentType: 'image/png', body: BLANK_TILE });
    }

    if (/\.pbf(\?|$)/i.test(url) || /\/tiles?\//i.test(url) && !url.startsWith(BASE_URL)) {
      return route.fulfill({ status: 204, body: '' });
    }

    if (!url.startsWith(BASE_URL) && /^https?:/.test(url)) {
      // Google Fonts, tile JSON, telemetry — answer empty rather than hang.
      return route.fulfill({ status: 200, contentType: 'text/plain', body: '' });
    }

    return route.continue();
  });
}

/** Applies the theme exactly the way `app/page.tsx` does. */
async function applyTheme(page, theme) {
  await page.evaluate((t) => {
    const root = document.documentElement;
    root.classList.toggle('dark', t === 'dark');
    root.setAttribute('data-theme', t);
  }, theme);
}

async function clickRailTab(page, label) {
  const tab = page.locator(`[role="tab"][aria-label*="${label}"]`).first();
  if (await tab.count()) {
    await tab.click({ timeout: 5000 }).catch(() => {});
    return true;
  }
  const byText = page.locator(`[role="tab"]:has-text("${label}")`).first();
  if (await byText.count()) {
    await byText.click({ timeout: 5000 }).catch(() => {});
    return true;
  }
  return false;
}

async function clickTopBarButton(page, label) {
  const btn = page.locator(`button[aria-label*="${label}"], button[title*="${label}"]`).first();
  if (await btn.count()) {
    await btn.click({ timeout: 5000 }).catch(() => {});
    return true;
  }
  return false;
}

/** Send the seed chat turn — the stubbed stream fills the results registry. */
async function seedResults(page) {
  // Chat is the default tab and the panel opens on load; clicking the already
  // active rail tab would TOGGLE the panel closed, so fill directly. Wait for
  // hydration rather than assuming the app mounted within the page-settle wait.
  const input = page.locator('textarea[aria-label="输入空间分析指令"]');
  try {
    await input.waitFor({ state: 'visible', timeout: 10000 });
  } catch {
    return false;
  }
  await input.fill('对商业 POI 做热点、可达性与密度分析');
  const send = page.locator('button[aria-label="发送消息"]');
  // A swallowed click here would screenshot the empty state as "success", so
  // let it throw and surface as a capture failure instead.
  await send.click({ timeout: 5000 });
  // Poll for the seeded turn to land (SSE replay + layer auto-mount) rather
  // than a fixed sleep — deterministic on slow machines. The results LIST only
  // mounts on the results tab, which isn't open yet; the rail badge count is
  // the tab-agnostic signal that the registry filled.
  try {
    await page.waitForFunction(
      () => {
        const tab = document.querySelector('[role="tab"][aria-label*="结果"]');
        return !!tab && /\d/.test(tab.textContent ?? '');
      },
      { timeout: 15000 },
    );
  } catch {
    return false;
  }
  // Descriptor enrichment settles shortly after the rows appear.
  await page.waitForTimeout(1200);
  return true;
}

/** Open a result detail by its tool label in the results list. */
async function openResult(page, label) {
  const row = page
    .locator(`ul[aria-label="分析结果列表"] button:has-text("${label}")`)
    .first();
  try {
    await row.waitFor({ state: 'visible', timeout: 10000 });
  } catch {
    return false;
  }
  await row.click({ timeout: 5000 }).catch(() => {});
  return true;
}

/**
 * Resize the context panel via the keyboard path on the drag separator.
 * Negative arrowPresses shrink (ArrowLeft), positive grow (ArrowRight).
 */
async function resizePanel(page, arrowPresses) {
  const handle = page.locator('div[role="separator"][aria-label="调整面板宽度"]');
  if (!(await handle.count())) return false;
  await handle.focus();
  for (let i = 0; i < Math.abs(arrowPresses); i += 1) {
    await page.keyboard.press(arrowPresses < 0 ? 'ArrowLeft' : 'ArrowRight');
  }
  await page.waitForTimeout(300);
  return true;
}

async function capture() {
  const browser = await chromium.launch({ args: ['--force-color-profile=srgb'] });
  const failures = [];
  let shots = 0;

  for (const vp of VIEWPORTS) {
    for (const theme of THEMES) {
      const context = await browser.newContext({
        viewport: { width: vp.width, height: vp.height },
        deviceScaleFactor: 1,
        reducedMotion: 'reduce',
        locale: 'zh-CN',
        // Pinned timezone + a wall clock anchored to NOW: result timestamps
        // render identically across runs/machines so before/after diffs show
        // real changes, not clock noise. Elapsed time keeps flowing normally.
        timezoneId: 'Asia/Shanghai',
      });
      await context.addInitScript(
        ({ anchor }) => {
          const realNow = Date.now;
          const t0 = realNow();
          Date.now = () => anchor + (realNow() - t0);
        },
        { anchor: Date.parse('2026-01-01T08:30:00Z') },
      );
      const page = await context.newPage();
      await installStubs(page);
      page.on('pageerror', (e) => failures.push(`${vp.name}/${theme}: ${e.message}`));

      for (const surface of SURFACES) {
        if (ONLY && !surface.name.includes(ONLY)) continue;
        const dir = path.join(OUT_ROOT, `${vp.name}-${theme}`);
        await mkdir(dir, { recursive: true });

        try {
          await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
          await page.waitForTimeout(1500);

          if (surface.restoreSession) {
            // 历史会话 → 选第一条：走真实的 selectSession → map-state → 图层恢复。
            await clickTopBarButton(page, '历史会话');
            await page.waitForTimeout(500);
            const entry = page
              .locator('[role="dialog"] button:has-text("北京商业 POI 热力分析")')
              .first();
            if (await entry.count()) await entry.click({ timeout: 5000 }).catch(() => {});
            await page.waitForTimeout(1400);
          }
          if (surface.seedResults && !(await seedResults(page))) {
            throw new Error('seed chat input not found — results registry not populated');
          }
          if (surface.tab) await clickRailTab(page, surface.tab);
          if (surface.button) await clickTopBarButton(page, surface.button);
          if (surface.opsView) {
            const viewBtn = page.locator(`[data-testid="ops-view-${surface.opsView}"]`).first();
            if (await viewBtn.count()) await viewBtn.click({ timeout: 5000 }).catch(() => {});
          }
          if (surface.opsWallboard) {
            const enter = page.locator('[data-testid="ops-wallboard-enter"]').first();
            if (await enter.count()) await enter.click({ timeout: 5000 }).catch(() => {});
          }
          if (surface.openResult && !(await openResult(page, surface.openResult))) {
            throw new Error(`result row "${surface.openResult}" not found`);
          }
          if (surface.panelArrows !== undefined && !(await resizePanel(page, surface.panelArrows))) {
            throw new Error('panel resize separator not found');
          }
          if (surface.collapse) await clickRailTab(page, surface.tab);
          if (surface.hud) {
            const chevron = page
              .locator('button[aria-label*="展开"], button[aria-label*="HUD"]')
              .first();
            if (await chevron.count()) await chevron.click({ timeout: 3000 }).catch(() => {});
          }

          await page.waitForTimeout(900);
          // Applied last: `app/page.tsx` drives the `dark` class from a store effect that
          // runs on hydration and would otherwise strip a theme set before then.
          await applyTheme(page, theme);
          await page.waitForTimeout(400);
          await page.screenshot({ path: path.join(dir, `${surface.name}.png`) });
          shots += 1;
        } catch (err) {
          failures.push(`${vp.name}/${theme}/${surface.name}: ${err.message}`);
        }
      }

      await context.close();
    }
  }

  await browser.close();
  console.log(`[visual] wrote ${shots} screenshots to ${OUT_ROOT}`);
  if (failures.length) {
    console.log(`[visual] ${failures.length} issue(s):`);
    for (const f of failures.slice(0, 40)) console.log(`  - ${f}`);
    // A broken seed/selector must fail the run, not just print — otherwise a
    // wrong-state screenshot ships as a golden.
    process.exitCode = 1;
  }
}

capture().catch((err) => {
  console.error(err);
  process.exit(1);
});
