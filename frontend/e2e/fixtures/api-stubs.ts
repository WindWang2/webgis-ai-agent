/**
 * Mock-mode API stubs for journey runs (ADR-0146).
 *
 * One stateful `JourneyWorld` per test backs every product endpoint the six
 * journeys touch; routes are installed with page.route BEFORE navigation so
 * no request ever reaches a backend. Shapes follow the production contracts
 * (lib/api/*.ts) and the seeded SSE replay follows the chat stream contract —
 * the same approach proven by test/visual/capture.mjs, made journey-scoped
 * and mutable here.
 *
 * Determinism rules:
 *  - fixed NOW timestamp, no random ids, no timers;
 *  - state transitions only happen when the journey performs the user action
 *    (cancel click → DELETE handler flips the job), never on a clock.
 */
import type { Page } from 'playwright/test';
import { analysisTurn, completingTurn, failingTurn, hangingTurn } from './sse';
import { EMPTY_MVT_HEADERS, SAMPLE_GEOJSON, TINY_PNG } from './files';

export const NOW = '2026-01-01T08:30:00Z';

export interface JobRecord {
  id: string;
  kind: string;
  name: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress: number | null;
  message: string;
  cancellable: boolean;
  retryable: boolean;
  active: boolean;
  attempt: number;
  session_id: string;
  project_id: string | null;
  agent_task_id: string | null;
  agent_step_id: string | null;
  background_job_ids: string[];
  error: string | null;
  result_ref: string | null;
  step_count: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  cancel_requested_at: string | null;
}

export function makeJob(
  id: string,
  name: string,
  status: JobRecord['status'],
  progress: number | null,
  message: string,
  extra: Partial<JobRecord> = {},
): JobRecord {
  const active = status === 'running' || status === 'queued';
  return {
    id,
    kind: 'analysis',
    name,
    status,
    progress,
    message,
    cancellable: status === 'running',
    retryable: status === 'failed',
    active,
    attempt: 1,
    session_id: 's-j2',
    project_id: 'p-j',
    agent_task_id: null,
    agent_step_id: null,
    background_job_ids: [],
    error: status === 'failed' ? '数据源连接超时（30s）' : null,
    result_ref: status === 'completed' ? 'artifact://result/j2' : null,
    step_count: 4,
    created_at: NOW,
    started_at: active ? NOW : NOW,
    finished_at: active ? null : NOW,
    cancel_requested_at: null,
    ...extra,
  };
}

export interface TemplateRecord {
  id: string;
  kind: string;
  name: string;
  category: string;
  keywords: string[];
  description: string;
  payload: Record<string, unknown>;
  is_builtin: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface FabricSourceRecord {
  id: string;
  name: string;
  source_type: string;
  endpoint_url: string;
  status: string;
  capabilities: string[];
  connection_profile: Record<string, unknown>;
  last_health_check: string;
}

export interface CatalogItemRecord {
  id: string;
  source_id: string;
  name: string;
  title: string;
  description: string;
  geometry_type: string;
  feature_type: string;
  crs: string;
  bbox: number[];
  meta_profile: Record<string, unknown>;
  updated_at: string;
}

/**
 * Mutable world behind the stubs. Journeys construct it with their scenario
 * seed, then user actions mutate it through the route handlers — the UI state
 * the journey asserts on is produced by the same production data flow as in
 * real mode.
 */
export class JourneyWorld {
  jobs: JobRecord[] = [];
  templates: TemplateRecord[] = [];
  sources: FabricSourceRecord[] = [];
  catalog: CatalogItemRecord[] = [];
  /** SSE body per chat POST (popped FIFO), falling back to a default turn. */
  chatScript: string[] = [];
  sessionDetail = {
    session_id: 's-j1',
    title: '旅程会话',
    messages: [] as Array<{ role: string; content: string; created_at: string }>,
  };
  mapState = {
    map_state: {
      viewport: { center: [116.39, 39.95], zoom: 10.4, bearing: 0, pitch: 0 },
      layers: [] as Array<Record<string, unknown>>,
    },
  };
  exports: Array<{ url: string; filename: string }> = [];
  /** Request log for contract assertions (method+path), most recent last. */
  requests: Array<{ method: string; path: string }> = [];

  job(id: string): JobRecord | undefined {
    return this.jobs.find((j) => j.id === id);
  }

  pushChat(body: string): void {
    this.chatScript.push(body);
  }
}

const json = (payload: unknown, origin?: string) => ({
  status: 200,
  contentType: 'application/json',
  headers: corsFor(origin),
  body: JSON.stringify(payload),
});

function template(id: string, kind: string, name: string, category: string, description: string, payload: Record<string, unknown> = {}): TemplateRecord {
  return {
    id, kind, name, category, description,
    keywords: ['城市', '专题'], payload, is_builtin: true, version: 1,
    created_at: NOW, updated_at: NOW,
  };
}

/** Default world seed covering the six journey scenarios. */
export function defaultWorld(): JourneyWorld {
  const world = new JourneyWorld();
  world.jobs = [
    makeJob('job-run', '长时分析（可取消）', 'running', 30, '正在计算…'),
    makeJob('job-fail', 'ST-DBSCAN 时空聚类', 'failed', null, '任务失败'),
  ];
  world.templates = [
    template('tpl-basemap', 'basemap', '深色影像底图', '底图', '低饱和深色底图，突出专题图层。', { providerId: 'carto-dark-vec' }),
    template('tpl-layout', 'layout', 'A3 横向出图版式', '版式', '含图例、比例尺、指北针与元数据栏。', {
      orientation: 'landscape', page: 'A3', showLegend: true, showScaleBar: true, showNorthArrow: true,
    }),
    template('tpl-thematic', 'thematic', '人口密度分级设色', '专题制图', '五级自然断点分级，适用于区县人口密度。'),
  ];
  world.sources = [
    {
      id: 'src-1', name: '城市空间数据底座', source_type: 'postgis',
      endpoint_url: 'postgresql://gis.example.org/gis', status: 'healthy',
      capabilities: ['query', 'preview', 'materialize'], connection_profile: {},
      last_health_check: NOW,
    },
  ];
  world.catalog = [
    {
      id: 'ci-1', source_id: 'src-1', name: 'poi_commercial', title: '商业 POI 点位',
      description: '来自城市空间数据底座的要素集。', geometry_type: 'Point', feature_type: 'vector',
      crs: 'EPSG:4326', bbox: [116.0, 39.6, 116.8, 40.3],
      meta_profile: { feature_count: 4213 }, updated_at: NOW,
    },
  ];
  return world;
}

/** Explicit non-JSON (raw) response marker — JSON payloads may legitimately
 * carry a `status` field, so discrimination must never key off payload shape. */
export interface RawResponse {
  status: number;
  contentType: string;
  body: string | Buffer;
  headers?: Record<string, string>;
}

/** Per-request CORS: credentialed requests (credentials: 'include') forbid
 * wildcard ACAO, so the stub echoes the request origin. */
export function corsFor(origin?: string): Record<string, string> {
  return {
    'access-control-allow-origin': origin ?? '*',
    'access-control-allow-credentials': 'true',
    'access-control-allow-headers': '*',
    'access-control-allow-methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
  };
}

export function raw(status: number, contentType: string, body: string | Buffer, origin?: string, headers?: Record<string, string>): RawResponse {
  return { status, contentType, body, headers: { ...corsFor(origin), ...headers } };
}

/**
 * Install every stub route. Must be awaited before page.goto — Playwright
 * routes only affect requests issued after installation.
 */
export async function installJourneyStubs(page: Page, world: JourneyWorld): Promise<void> {
  // NOTE on ordering: specific patterns first — Playwright routes are
  // last-registered-first-matched, so register the generic catch-all LAST.
  const api = (
    re: RegExp,
    handler: (url: URL, method: string, body: string, origin?: string) => unknown | Promise<unknown>,
  ) =>
    page.route(re, async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      world.requests.push({ method: req.method(), path: url.pathname });
      const origin = req.headers()['origin'];
      if (req.method() === 'OPTIONS') {
        await route.fulfill({ status: 204, headers: corsFor(origin), body: '' });
        return;
      }
      const out = await handler(url, req.method(), req.postData() ?? '', origin);
      if (out !== null && typeof out === 'object' && '__raw' in (out as Record<string, unknown>)) {
        const r = (out as { __raw: RawResponse }).__raw;
        await route.fulfill({ status: r.status, contentType: r.contentType, body: r.body, headers: { ...corsFor(origin), ...r.headers } });
      } else {
        await route.fulfill(json(out, origin));
      }
    });

  // ── chat stream (SSE) ────────────────────────────────────────────────────
  await api(/\/api\/v1\/chat\/stream(\?|$)/, async (_url: URL, _m: string, _b: string, origin?: string) => ({
    __raw: {
      status: 200,
      contentType: 'text/event-stream',
      body: world.chatScript.length > 0 ? (world.chatScript.shift() as string) : analysisTurn({}),
      headers: { 'cache-control': 'no-cache', ...corsFor(origin) },
    },
  }));

  // ── auth ─────────────────────────────────────────────────────────────────
  await api(/\/api\/v1\/auth\/login(\?|$)/, async () => ({
    access_token: 'e2e-mock-access-token',
    refresh_token: null,
    token_type: 'bearer',
    expires_in: 3600,
    user: { id: 'u-e2e', username: 'e2e', display_name: 'E2E 用户', roles: ['user'] },
  }));

  // ── sessions / map state ────────────────────────────────────────────────
  await api(/\/api\/v1\/chat\/sessions\/[^/]+\/map-state(\?|$)/, async () => world.mapState);
  await api(/\/api\/v1\/chat\/sessions\/[^/]+(\?|$)/, async () => world.sessionDetail);
  await api(/\/api\/v1\/chat\/sessions(\?|$)/, () => ({ sessions: [{ session_id: 's-j1', title: '旅程会话', updated_at: NOW, message_count: 1 }] }));
  await api(/\/api\/v1\/chat\/skills(\?|$)/, () => ({ skills: [] }));

  // ── tasks ────────────────────────────────────────────────────────────────
  await api(/\/api\/v1\/tasks\/jobs\/[^/]+\/retry(\?|$)/, async () => {
    const job = world.jobs.find((j) => j.status === 'failed') ?? world.jobs[0];
    if (job) {
      job.attempt += 1;
      job.status = 'running';
      job.progress = 5;
      job.cancellable = true;
      job.retryable = false;
      job.active = true;
      job.error = null;
      job.message = '重试中…';
    }
    return job;
  });
  await api(/\/api\/v1\/tasks\/jobs\/[^/]+(\?|$)/, async (_url, method) => {
    if (method !== 'DELETE') return {};
    const job = world.jobs.find((j) => j.status === 'running');
    if (job) {
      // 真取消语义：请求受理 → 终态由下一次轮询给出（与后端契约一致，
      // UI 显示「已取消」必须等终态，规范 §30）。
      job.cancel_requested_at = NOW;
      job.cancellable = false;
      job.active = false;
      job.status = 'cancelled';
      job.progress = null;
      job.message = '已取消';
    }
    return { job_id: world.jobs[0]?.id ?? '', status: 'cancel_requested' };
  });
  await api(/\/api\/v1\/tasks\/jobs(\?|$)/, async () => ({
    jobs: world.jobs,
    has_active: world.jobs.some((j) => j.active),
    poll_after_ms: null,
  }));

  // ── layers ───────────────────────────────────────────────────────────────
  await api(/\/api\/v1\/layers\/descriptor\/.+(\?|$)/, async () => ({
    ref_id: 'ref:j1-hotspot',
    // > VECTOR_TILE_THRESHOLD (5000, mapspec-runtime/adapter.ts): makes the
    // production MVT path engage so journey 6 can assert the tile contract.
    feature_count: 8421,
    geometry_types: ['Point'],
    bbox: [116.2814, 39.7842, 116.7351, 40.1213],
    mvt_capable: true,
    raster_capable: false,
    estimated_bytes: 482304,
  }));
  await api(/\/api\/v1\/layers\/data\/.+(\?|$)/, async (url) => {
    // MVT tile contract (lib/map-kit/tile-url.ts:21):
    //   /api/v1/layers/data/{ref}/tiles/{z}/{x}/{y}.mvt?session_id=…
    if (/\/tiles\/.+\.mvt$/.test(url.pathname) || url.searchParams.get('format') === 'mvt') {
      return { __raw: raw(200, EMPTY_MVT_HEADERS['content-type'], '', url.origin) };
    }
    return SAMPLE_GEOJSON;
  });
  await api(/\/api\/v1\/layer-types(\?|$)/, () => ({ layer_types: [] }));

  // ── upload ───────────────────────────────────────────────────────────────
  await api(/\/api\/v1\/upload(\?|$)/, async () => ({
    uploads: [
      {
        upload_id: 'up-j1',
        filename: 'j1.shp',
        size_bytes: 942,
        geojson_ref: 'ref:j1-upload',
        layer_name: 'j1',
        feature_count: 3,
        geometry_type: 'Point',
        crs: 'EPSG:4326',
        bbox: [116.2814, 39.7842, 116.7351, 40.1213],
      },
    ],
  }));

  // ── templates ────────────────────────────────────────────────────────────
  await api(/\/api\/v1\/templates(\?|$)/, async () => ({
    items: world.templates,
    total: world.templates.length,
    limit: 50,
    offset: 0,
  }));
  await api(/\/api\/v1\/templates\/[^/]+(\?|$)/, async (url) => {
    const id = url.pathname.split('/').pop() as string;
    return world.templates.find((t) => t.id === id) ?? world.templates[0];
  });

  // ── data fabric ──────────────────────────────────────────────────────────
  await api(/\/api\/v1\/data-fabric\/sources\/[^/]+\/probe(\?|$)/, async () => ({
    status: 'healthy',
    latency_ms: 12,
    capabilities: ['query', 'preview', 'materialize'],
    checked_at: NOW,
  }));
  await api(/\/api\/v1\/data-fabric\/sources(\?|$)/, async (_url: URL, method: string, body: string) => {
    if (method === 'POST') {
      let parsed: { name?: string; source_type?: string; endpoint_url?: string } = {};
      try { parsed = JSON.parse(body); } catch { /* form-encoded not exercised */ }
      const src: FabricSourceRecord = {
        id: `src-${world.sources.length + 1}`,
        name: parsed.name ?? 'e2e 源',
        source_type: parsed.source_type ?? 'postgis',
        endpoint_url: parsed.endpoint_url ?? 'postgresql://gis.example.org/gis',
        status: 'healthy',
        capabilities: ['query', 'preview', 'materialize'],
        connection_profile: {},
        last_health_check: NOW,
      };
      world.sources.push(src);
      return { success: true, data_source: src };
    }
    return { sources: world.sources };
  });
  await api(/\/api\/v1\/data-fabric\/materialize(\?|$)/, async () => ({
    ref_id: 'ref:df-ci-1',
    feature_count: 4213,
    materialized_bytes: 482304,
  }));
  await api(/\/api\/v1\/data-fabric\/catalog(\?|$)/, async () => ({
    total: world.catalog.length,
    limit: 20,
    offset: 0,
    items: world.catalog,
  }));

  // ── projects / datasets ──────────────────────────────────────────────────
  await api(/\/api\/v1\/projects\/[^/]+\/datasets(\?|$)/, () => []);
  await api(/\/api\/v1\/projects\/[^/]+\/workflows(\?|$)/, () => []);
  await api(/\/api\/v1\/projects(\?|$)/, () => ({
    items: [{
      id: 'p-j', name: '旅程项目', description: 'quality-e2e 旅程项目',
      status: 'active', metadata_json: {}, created_at: NOW, updated_at: NOW,
    }],
    total: 1, limit: 20, offset: 0,
  }));

  // ── export ───────────────────────────────────────────────────────────────
  await api(/\/api\/v1\/export\/download\/.+(\?|$)/, () => ({
    __raw: raw(200, 'image/png', TINY_PNG),
  }));
  await api(/\/api\/v1\/export(\?|$)/, async (_url: URL, _method: string, body: string) => {
    const format = /png/i.test(body) ? 'png' : 'pdf';
    const filename = `journey-export.${format}`;
    const record = { url: `/api/v1/export/download/${encodeURIComponent(filename)}`, filename };
    world.exports.push(record);
    return record;
  });

  // ── generic basemap tile / CDN blanking (after all API routes) ───────────
  await page.route(/\.(png|jpe?g|webp)(\?|$)/i, (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: TINY_PNG }));
  await page.route(/\.pbf(\?|$)/i, (route) => route.fulfill({ status: 204, body: '' }));
  await page.route(/^https?:\/\/(?!localhost|127\.0\.0\.1)/, (route) =>
    route.fulfill({ status: 200, contentType: 'text/plain', body: '' }));
}

export { completingTurn, failingTurn, hangingTurn };
