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
import type { BrowserContext, Page } from 'playwright/test';
import { TILE_PROVIDERS } from '../../lib/providers';
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

/** WP4 制图设计规范（/api/v1/design-specs 契约形状；payload 随行）。 */
export interface DesignSpecStub {
  id: string;
  name: string;
  description: string | null;
  is_builtin: boolean;
  status: 'draft' | 'published' | 'retired';
  current_version: number;
  structural_digest: string;
  retired: boolean;
  retirement_note: string | null;
  creator_id: string;
  updated_at: string;
  payload: Record<string, unknown>;
  versions: Array<{
    id: string; spec_id: string; version: number;
    description: string | null; deprecated: boolean; parent_version_id: string | null;
  }>;
}

/** WP4/WP3 系列地图（/api/v1/map-series 契约形状；members 随行于 payload）。 */
export interface MapSeriesStub {
  id: string;
  name: string;
  description: string | null;
  status: 'draft' | 'published' | 'retired';
  current_version: number;
  structural_digest: string;
  design_spec_id: string | null;
  updated_at: string;
  payload: Record<string, unknown>;
}

/**
 * Mutable world behind the stubs. Journeys construct it with their scenario
 * seed, then user actions mutate it through the route handlers — the UI state
 * the journey asserts on is produced by the same production data flow as in
 * real mode.
 */
export class JourneyWorld {
  projects = [{
    id: 'p-j', name: '旅程项目', description: 'quality-e2e 旅程项目',
    status: 'active', metadata_json: {}, created_at: NOW, updated_at: NOW,
  }];
  jobs: JobRecord[] = [];
  templates: TemplateRecord[] = [];
  sources: FabricSourceRecord[] = [];
  catalog: CatalogItemRecord[] = [];
  /** SSE body per chat POST (popped FIFO), falling back to a default turn. */
  chatScript: string[] = [];
  sessionDetail = {
    session_id: 's-j1',
    title: '旅程会话',
    // 会话详情消息：生产恢复路径按 {id, role, content, timestamp} 消费
    // （use-workspace-session selectSession）；created_at 是历史夹具形状，
    // 两者都允许（additive —— 既有 journey 的无 id 消息不受影响）。
    messages: [] as Array<{
      role: string;
      content: string;
      created_at?: string;
      id?: string;
      timestamp?: number;
    }>,
  };
  mapState = {
    map_state: {
      viewport: { center: [116.39, 39.95], zoom: 10.4, bearing: 0, pitch: 0 },
      layers: [] as Array<Record<string, unknown>>,
    },
  };
  exports: Array<{ url: string; filename: string }> = [];
  /** Stateful upload records behind POST /api/v1/upload (#1555 UI upload leg). */
  uploads: Array<Record<string, unknown>> = [];
  /** Stateful project datasets behind attach/list (#1555 UI attach leg). */
  datasets: Array<Record<string, unknown>> = [];
  /** Request log for contract assertions (method+path), most recent last. */
  requests: Array<{ method: string; path: string }> = [];
  /**
   * ADR-0285：IR 事件上行记录（POST /interaction-events 的 body 事件，
   * journeys 断言 kind/payload 契约）；situation/turn-chain 投影面由各
   * journey 可配置（默认空 scope）。
   */
  interactionEvents: Array<{
    kind: string; actor: string; payload: Record<string, unknown>;
    client_instance_id?: string; client_seq?: number;
  }> = [];
  situationView: Record<string, unknown> | null = null;
  turnChainView: Record<string, unknown> | null = null;
  /**
   * G22-P6（恢复可读性）：会话详情 GET 仅在请求方显式带 `?recovery=1` 时
   * 附挂的恢复摘要（recovery-banner 自水合）。默认 null = 不附挂，其它
   * journey 的会话详情响应形状不变。
   */
  recoverySummary: Record<string, unknown> | null = null;
  /**
   * G42（目标补全面）：/goal-repair/* 桩状态。preview 缺席（null）时按
   * 404 PLAN_NOT_FOUND 语义回 —— 与后端 typed 错误一致；attempts 是有界
   * 状态机（running → 第 2 次读 terminal，receipts/verdict 由旅程种子）。
   */
  goalRepair: {
    preview: Record<string, unknown> | null;
    attempts: Array<Record<string, unknown> & { _reads?: number }>;
  } = { preview: null, attempts: [] };
  /** WP4：设计规范 / 系列地图 mock 状态（j26；其他 journey 无请求即无影响）。 */
  designSpecs: DesignSpecStub[] = [];
  mapSeries: MapSeriesStub[] = [];

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
  world.designSpecs = [{
    id: 'dspec-j26', name: '城市专题规范（e2e）', description: 'mock 档种子规范',
    is_builtin: false, status: 'published', current_version: 2,
    structural_digest: 'dspec-sha256:j26seed01', retired: false, retirement_note: null,
    creator_id: 'u-e2e', updated_at: NOW,
    payload: {
      spec_version: 'design_spec.v1', name: '城市专题规范（e2e）',
      description: 'mock 档种子规范',
      color: { theme_id: 'cartographic.light_interactive', palette_sequential: 'Blues' },
      classification: { default_method: 'quantiles', shared_required: true },
      output: { targets: ['interactive', 'png', 'pdf', 'svg'] },
    },
    versions: [
      { id: 'dspec-j26-v1', spec_id: 'dspec-j26', version: 1, description: null, deprecated: false, parent_version_id: null },
      { id: 'dspec-j26-v2', spec_id: 'dspec-j26', version: 2, description: '调色', deprecated: false, parent_version_id: 'dspec-j26-v1' },
    ],
  }];
  world.mapSeries = [{
    id: 'mser-j26', name: '三年对照系列（e2e）', description: null,
    status: 'draft', current_version: 1, structural_digest: 'mser-sha256:j26seed01',
    design_spec_id: 'dspec-j26', updated_at: NOW,
    payload: {
      spec_version: 'map_series.v1', name: '三年对照系列（e2e）',
      design_spec_id: 'dspec-j26',
      members: [
        { member_id: 'm_2020', title: '2020 主图', session_id: 's-j26-2020', role: 'primary',
          dimension_tags: ['2020'], locked_fields: [], enabled: true },
        { member_id: 'm_2024', title: '2024 对照', session_id: 's-j26-2024', role: 'comparison',
          dimension_tags: ['2024'], locked_fields: ['palette'], enabled: true },
      ],
    },
  }];
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
export async function installJourneyStubs(page: Page | BrowserContext, world: JourneyWorld): Promise<void> {
  // Playwright uses last-registered-first-matched precedence. Keep the broad
  // external fallback first so typed fixtures and product APIs win over it.
  await page.route(/^https?:\/\/(?!localhost|127\.0\.0\.1)/, (route) =>
    route.fulfill({ status: 200, contentType: 'text/plain', body: '' }));
  await page.route(/\.(png|jpe?g|webp)(\?|$)/i, (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: TINY_PNG }));
  await page.route(/\.pbf(\?|$)/i, (route) => route.fulfill({ status: 204, body: '' }));

  // A visible canvas is not a loaded map. Style URLs (including extensionless
  // OpenFreeMap URLs) need valid style JSON, otherwise onLoad never fires and
  // neither the MVT runtime nor scene3d assets can mount. Only the basemap is
  // synthetic: application layers, WebGL rendering and GLB loading stay real.
  for (const provider of TILE_PROVIDERS.filter((p) => p.type === 'vector')) {
    await page.route(provider.url, (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        version: 8,
        sources: {},
        layers: [{
          id: 'journey-background', type: 'background',
          paint: { 'background-color': '#e8eef4' },
        }],
      }),
    }));
  }
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

  // ── goal repair（G42 目标补全面）───────────────────────────────────────
  await api(/\/api\/v1\/goal-repair\/sessions\/[^/]+\/preview(\?|$)/, (_url: URL, _method: string, _body: string, origin?: string) => {
    if (!world.goalRepair.preview) {
      return {
        __raw: {
          status: 404, contentType: 'application/json',
          body: JSON.stringify({ detail: { code: 'PLAN_NOT_FOUND', error: 'no plan' } }),
          headers: corsFor(origin),
        },
      };
    }
    return world.goalRepair.preview;
  });
  await api(/\/api\/v1\/goal-repair\/sessions\/[^/]+\/attempts(\?|$)/, (_url: URL, method: string, body: string) => {
    if (method !== 'POST') return { detail: 'method not allowed' };
    let selected: string[] | undefined;
    let acked: string[] | undefined;
    try {
      const parsed = JSON.parse(body) as { action_ids?: string[]; acknowledged_losses?: string[] };
      selected = parsed.action_ids;
      acked = parsed.acknowledged_losses;
    } catch { /* 空 body —— 全默认 */ }
    const id = String(1000 + world.goalRepair.attempts.length + 1);
    const attempt: Record<string, unknown> & { _reads?: number } = {
      attempt_id: id, status: 'running', active: true, cancellable: true,
      session_id: world.sessionDetail.session_id, goal_id: 'g-j42',
      plan_version: 'e2e', outcome: null, final_verdict_before: 'partial',
      final_verdict_after: null, final_changed: false, actions: [],
      _selected: selected, _acked: acked, _reads: 0,
    };
    world.goalRepair.attempts.push(attempt);
    return attempt;
  });
  await api(/\/api\/v1\/goal-repair\/attempts\/[^/]+(\?|$)/, (url: URL, _method: string, _body: string, origin?: string) => {
    const id = url.pathname.split('/').pop() as string;
    const attempt = world.goalRepair.attempts.find((a) => a.attempt_id === id);
    if (!attempt) {
      return {
        __raw: {
          status: 404, contentType: 'application/json',
          body: JSON.stringify({ detail: { code: 'ATTEMPT_NOT_FOUND', error: 'gone' } }),
          headers: corsFor(origin),
        },
      };
    }
    attempt._reads = (attempt._reads ?? 0) + 1;
    // 第 2 次读起终态：回执 + 原终验投影（旅程种子 action 形状）。
    if (attempt.status === 'running' && attempt._reads >= 2) {
      const selected = (attempt._selected as string[] | undefined) ?? [];
      const acked = new Set((attempt._acked as string[] | undefined) ?? []);
      const preview = world.goalRepair.preview as { actions?: Array<{ action_id: string; action: string; disposition: string; ack_codes?: string[] }> } | null;
      const actions = (preview?.actions ?? []).map((b) => {
        const chosen = selected.length === 0 || selected.includes(b.action_id);
        const ackOk = (b.ack_codes ?? []).every((c) => acked.has(c));
        if (!chosen) {
          return { action_id: b.action_id, action: b.action, requirement_id: '', status: 'skipped_cancelled', detail: '', evidence_ref: '', download_url: '', job_id: '', error_code: '' };
        }
        if (!ackOk) {
          return { action_id: b.action_id, action: b.action, requirement_id: '', status: 'needs_acknowledgment', detail: 'format loss unconfirmed', evidence_ref: '', download_url: '', job_id: '', error_code: 'LOSS_ACK_REQUIRED' };
        }
        return { action_id: b.action_id, action: b.action, requirement_id: '', status: 'succeeded', detail: 'e2e receipt', evidence_ref: 'ref:e2e', download_url: '/api/v1/export/download/e2e.bin?exp=1&sig=2', job_id: '9001', error_code: '' };
      });
      attempt.actions = actions;
      // 只按「被选中执行」的动作判定（未选中 = skipped，不阻断终验投影）。
      const chosenActions = actions.filter(
        (a) => (a as { status: string }).status !== 'skipped_cancelled');
      const allSucceeded = chosenActions.length > 0
        && chosenActions.every((a) => (a as { status: string }).status === 'succeeded');
      attempt.status = 'completed';
      attempt.active = false;
      attempt.cancellable = false;
      attempt.outcome = allSucceeded ? 'completed' : 'no_progress';
      attempt.final_verdict_after = allSucceeded ? 'satisfied' : 'partial';
      attempt.final_changed = allSucceeded;
    }
    const { _selected, _acked, _reads, ...view } = attempt as Record<string, unknown>;
    void _selected; void _acked; void _reads;
    return view;
  });
  await api(/\/api\/v1\/goal-repair\/attempts\/[^/]+\/cancel(\?|$)/, (url: URL, method: string) => {
    if (method !== 'POST') return { detail: 'method not allowed' };
    const parts = url.pathname.split('/');
    const id = parts[parts.length - 2];
    const attempt = world.goalRepair.attempts.find((a) => a.attempt_id === id);
    if (attempt && attempt.active) {
      attempt.status = 'cancelled';
      attempt.active = false;
      attempt.cancellable = false;
      attempt.outcome = 'cancelled';
    }
    const { _selected, _acked, _reads, ...view } =
      (attempt ?? {}) as Record<string, unknown>;
    void _selected; void _acked; void _reads;
    return { ...view, cancel_requested: true };
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
  // ── ADR-0285：交互闭环端点（IR 摄入/投影 + situation/turn-chain 只读面）──
  await api(/\/api\/v1\/chat\/sessions\/[^/]+\/interaction-events(\?|$)/, async (_url: URL, method: string, body: string) => {
    if (method !== 'POST') {
      return {
        schema_version: 'interaction_ir.v1', projection: {
          viewport: null, selection: null, active_aoi: null,
          chart_selection: null, temporal_window: null,
          user_overrides: [], attempted_not_applied: [],
          recent_interactions: [], event_count: world.interactionEvents.length,
        },
      };
    }
    let accepted = 0;
    let rejected = 0;
    try {
      const parsed = JSON.parse(body) as {
        events?: Array<{
          kind?: string; actor?: string; payload?: Record<string, unknown>;
          client_instance_id?: string; client_seq?: number;
        }>;
      };
      for (const event of parsed.events ?? []) {
        if (!event.kind) { rejected += 1; continue; }
        world.interactionEvents.push({
          kind: event.kind,
          actor: event.actor ?? 'user',
          payload: event.payload ?? {},
          client_instance_id: event.client_instance_id,
          client_seq: event.client_seq,
        });
        accepted += 1;
      }
    } catch { rejected += 1; }
    return {
      accepted, duplicates: 0, stale: 0, rejected,
      acks: [], dropped: 0, persist_degraded: false,
    };
  });
  await api(/\/api\/v1\/chat\/sessions\/[^/]+\/situation(\?|$)/, async (_url: URL, method: string, body: string) => {
    if (world.situationView) return world.situationView;
    // 默认合成面：claimed_scope=none + 空史（j14+ 可覆盖）。
    let preTurnSelected: Record<string, unknown> | null = null;
    try {
      const parsed = JSON.parse(body) as { map_state?: { selected_feature?: Record<string, unknown> } };
      preTurnSelected = parsed.map_state?.selected_feature ?? null;
    } catch { /* 非 JSON body 不在此端点出现 */ }
    return {
      schema_version: 'situation_view.v1',
      session_id: 's-j1',
      situation: {},
      inconsistencies: [],
      interaction_snapshot: { selection: null, attempted_not_applied: [] },
      selection_history: [],
      claimed_scope: preTurnSelected
        ? {
          kind: 'feature_single',
          layer_id: String(preTurnSelected.layer_id ?? ''),
          dataset_ref: preTurnSelected.dataset_ref ?? null,
          feature_ids: [String(preTurnSelected.feature_id ?? '')].filter(Boolean),
          claimed_at_seq: null, sources: ['pre_turn'],
        }
        : { kind: 'none', sources: [] },
    };
  });
  await api(/\/api\/v1\/chat\/sessions\/[^/]+\/turn-chain(\?|$)/, async () =>
    world.turnChainView ?? {
      schema_version: 'situation_turn_chain.v1', turns: [],
      observed_revision: null, counts: {
        user_explicit: 0, agent_receipts: 0, agent_pending: 0, executed_observed: 0,
      },
    });
  await api(/\/api\/v1\/chat\/sessions\/[^/]+(\?|$)/, async (url: URL) => {
    // G22-P6：?recovery=1 才附挂摘要（默认响应字节不变，与后端路由同语义）。
    if (url.searchParams.get('recovery') === '1' && world.recoverySummary) {
      return { ...world.sessionDetail, recovery: world.recoverySummary };
    }
    return world.sessionDetail;
  });
  await api(/\/api\/v1\/chat\/sessions(\?|$)/, () => ({ sessions: [{ id: world.sessionDetail.session_id, title: world.sessionDetail.title, createdAt: Date.parse(NOW), messages: world.sessionDetail.messages }] }));
  await api(/\/api\/v1\/chat\/skills(\?|$)/, () => ({ skills: [] }));

  // ── tasks ────────────────────────────────────────────────────────────────
  await api(/\/api\/v1\/tasks\/jobs\/[^/]+\/retry(\?|$)/, async (url: URL) => {
    const parts = url.pathname.split('/');
    const id = parts[parts.length - 2];
    const job = world.job(id) ?? world.jobs.find((j) => j.status === 'failed') ?? world.jobs[0];
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
  await api(/\/api\/v1\/tasks\/jobs\/[^/]+(\?|$)/, async (url: URL, method: string) => {
    const id = url.pathname.split('/').pop() as string;
    const addressed = world.job(id);
    if (method !== 'DELETE') return addressed ?? {};
    const job = addressed ?? world.jobs.find((j) => j.status === 'running');
    if (job && job.active) {
      // 真取消语义：请求受理 → 终态由下一次轮询给出（与后端契约一致，
      // UI 显示「已取消」必须等终态，规范 §30）。
      job.cancel_requested_at = NOW;
      job.cancellable = false;
      job.active = false;
      job.status = 'cancelled';
      job.progress = null;
      job.message = '已取消';
    }
    return { job_id: job?.id ?? '', status: 'cancel_requested' };
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

  // ── upload (#1555: POST 是真实 UI 上传腿的传输契约；GET 是 fast-path 列表) ──
  await api(/\/api\/v1\/uploads(\?|$)/, async () => ({
    total: world.uploads.length,
    uploads: world.uploads,
  }));
  await api(/\/api\/v1\/upload(\?|$)/, async (_url: URL, method: string) => {
    if (method !== 'POST') {
      return { total: world.uploads.length, uploads: world.uploads };
    }
    // lib/api/upload.ts uploadFile 的响应合同（UploadResponse）：服务端
    // 裁决 original_name/crs/feature_count —— stub 按合同形状给确定性值。
    const record = {
      id: 101 + world.uploads.length,
      original_name: 'j1-upload-sample.geojson',
      file_type: 'vector',
      format: 'geojson',
      crs: 'EPSG:4326',
      crs_source: 'declared',
      geometry_type: 'Point',
      feature_count: 3,
      bbox: [116.2814, 39.7842, 116.7351, 40.1213],
      file_size: 942,
      deduplicated: false,
      session_ref: `ref:upload-j1-${world.uploads.length + 1}`,
      warnings: [],
      meta: null,
      profile_summary: null,
      quality: null,
      ref_registration_error: null,
    };
    world.uploads.push(record);
    return record;
  });

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

  // ── projects / datasets (#1555: attach 有状态 —— UI 挂载腿的真实数据流) ──
  await api(/\/api\/v1\/projects\/[^/]+\/datasets(\?|$)/, async (url: URL, method: string, body: string) => {
    const projectId = url.pathname.split('/')[4];
    if (method === 'POST') {
      let parsed: { name?: string; source_type?: string; source_ref?: string; crs?: string } = {};
      try { parsed = JSON.parse(body); } catch { /* non-JSON body not exercised */ }
      const record = {
        id: `ds-${world.datasets.length + 1}`,
        project_id: projectId,
        name: parsed.name ?? 'e2e 数据集',
        source_type: parsed.source_type ?? 'layer',
        source_ref: parsed.source_ref ?? null,
        crs: parsed.crs ?? 'EPSG:4326',
        quality_status: 'unchecked',
        version_fingerprint: null,
        created_at: NOW,
      };
      world.datasets.push(record);
      return record;
    }
    const datasets = world.datasets.filter((dataset) => dataset.project_id === projectId);
    return { items: datasets, total: datasets.length, limit: 20, offset: 0 };
  });
  await api(/\/api\/v1\/projects\/[^/]+\/workflows(\?|$)/, () => []);
  await api(/\/api\/v1\/projects(\?|$)/, (_url: URL, method: string, body: string) => {
    if (method === 'POST') {
      let parsed: { name?: string; description?: string } = {};
      try { parsed = JSON.parse(body); } catch { /* non-JSON body not exercised */ }
      const project = {
        id: `p-j-${world.projects.length + 1}`, name: parsed.name ?? 'e2e 项目',
        description: parsed.description ?? '', status: 'active', metadata_json: {},
        created_at: NOW, updated_at: NOW,
      };
      world.projects.push(project);
      return project;
    }
    return { items: world.projects, total: world.projects.length, limit: 20, offset: 0 };
  });

  // ── export ───────────────────────────────────────────────────────────────
  await api(/\/api\/v1\/export\/download\/.+(\?|$)/, (_url: URL, _method: string, _body: string, origin?: string) => ({
    __raw: raw(200, 'image/png', TINY_PNG, origin),
  }));
  await api(/\/api\/v1\/export(\?|$)/, async (_url: URL, _method: string, body: string) => {
    const format = /png/i.test(body) ? 'png' : 'pdf';
    const filename = `journey-export.${format}`;
    const record = { url: `/api/v1/export/download/${encodeURIComponent(filename)}`, filename };
    world.exports.push(record);
    return record;
  });

  // ── design specs / map series（WP4 制图规范与系列；契约形状见
  //    lib/api/design-specs.ts、lib/api/map-series.ts；map-series 为 WP3
  //    并行契约的 mock 面，联调在其合并后核对）────────────────────────────
  const specSummaryView = (spec: DesignSpecStub) => {
    const { payload, versions, ...summary } = spec;
    void payload; void versions;
    return summary;
  };
  const specById = (id: string | null): DesignSpecStub | undefined =>
    world.designSpecs.find((s) => s.id === id);

  await api(/\/api\/v1\/design-specs\/[^/]+\/versions\/[^/]+\/deprecate(\?|$)/, async (url, method) => {
    if (method !== 'POST') return { detail: 'method not allowed' };
    const spec = specById(url.pathname.split('/')[4]);
    const v = Number(url.pathname.split('/')[6]);
    const version = spec?.versions.find((x) => x.version === v);
    if (version) version.deprecated = true;
    return { success: true, deprecated: true };
  });
  await api(/\/api\/v1\/design-specs\/[^/]+\/versions\/[^/]+(\?|$)/, async (url) => {
    const spec = specById(url.pathname.split('/')[4]);
    if (!spec) return { detail: { code: 'DESIGN_SPEC_NOT_FOUND' } };
    const tail = url.pathname.split('/').pop() as string;
    const version = tail === 'latest'
      ? (spec.versions.at(-1) ?? null)
      : (spec.versions.find((x) => x.version === Number(tail)) ?? null);
    return {
      success: true, version, effective_payload: spec.payload,
      inheritance_chain: [spec.id, spec.id],
      reference_validation: { ok: true, violations: [], warnings: [] },
    };
  });
  await api(/\/api\/v1\/design-specs\/[^/]+\/versions(\?|$)/, async (url, method, body) => {
    if (method !== 'POST') return { detail: 'method not allowed' };
    const spec = specById(url.pathname.split('/')[4]);
    if (!spec) return { detail: { code: 'DESIGN_SPEC_NOT_FOUND' } };
    let parsed: { payload?: Record<string, unknown> } = {};
    try { parsed = JSON.parse(body); } catch { /* non-JSON not exercised */ }
    spec.current_version += 1;
    const version = {
      id: `${spec.id}-v${spec.current_version}`, spec_id: spec.id,
      version: spec.current_version, description: null, deprecated: false,
      parent_version_id: spec.versions.at(-1)?.id ?? null,
    };
    spec.versions.push(version);
    if (parsed.payload) spec.payload = parsed.payload;
    spec.updated_at = NOW;
    return { success: true, version };
  });
  await api(/\/api\/v1\/design-specs\/[^/]+\/publish(\?|$)/, async (url, method) => {
    if (method !== 'POST') return { detail: 'method not allowed' };
    const spec = specById(url.pathname.split('/')[4]);
    if (spec) spec.status = 'published';
    return { success: true, spec: spec ? specSummaryView(spec) : null };
  });
  await api(/\/api\/v1\/design-specs\/[^/]+\/retire(\?|$)/, async (url, method, body) => {
    if (method !== 'POST') return { detail: 'method not allowed' };
    const spec = specById(url.pathname.split('/')[4]);
    let parsed: { note?: string } = {};
    try { parsed = JSON.parse(body); } catch { /* non-JSON not exercised */ }
    if (spec) {
      spec.status = 'retired';
      spec.retired = true;
      spec.retirement_note = parsed.note ?? null;
    }
    return { success: true, spec: spec ? specSummaryView(spec) : null };
  });
  await api(/\/api\/v1\/design-specs\/[^/]+\/preview(\?|$)/, async (url) => {
    const spec = specById(url.pathname.split('/')[4]);
    if (!spec) return { detail: { code: 'DESIGN_SPEC_NOT_FOUND' } };
    return {
      success: true, spec: specSummaryView(spec),
      version: spec.versions.at(-1) ?? null,
      effective_payload: spec.payload,
      inheritance_chain: [spec.id, spec.id],
      reference_validation: { ok: true, violations: [], warnings: [] },
      render_targets: {
        supported: ['interactive', 'png', 'pdf', 'svg'],
        unsupported: [],
        font_findings: [],
      },
      conflicts: [],
      resolved_digest: spec.structural_digest,
    };
  });
  await api(/\/api\/v1\/design-specs\/[^/]+(\?|$)/, async (url) => {
    const spec = specById(url.pathname.split('/').pop() as string);
    if (!spec) return { detail: { code: 'DESIGN_SPEC_NOT_FOUND' } };
    return {
      success: true, spec: specSummaryView(spec), payload: spec.payload,
      reference_validation: { ok: true, violations: [], warnings: [] },
    };
  });
  await api(/\/api\/v1\/design-specs(\?|$)/, async (_url: URL, method: string, body: string) => {
    if (method !== 'POST') {
      return { success: true, items: world.designSpecs.map(specSummaryView) };
    }
    let parsed: { payload?: { name?: string } } = {};
    try { parsed = JSON.parse(body); } catch { /* non-JSON not exercised */ }
    const name = parsed.payload?.name ?? 'e2e 规范';
    if (world.designSpecs.some((s) => s.name === name)) {
      return {
        __raw: {
          status: 409, contentType: 'application/json',
          body: JSON.stringify({ data: { success: false, code: 'DESIGN_SPEC_NAME_CONFLICT',
            message: 'name conflict' } }),
          headers: corsFor(),
        },
      };
    }
    const id = `dspec-${world.designSpecs.length + 1}`;
    const spec: DesignSpecStub = {
      id, name, description: null, is_builtin: false, status: 'draft',
      current_version: 1, structural_digest: `dspec-sha256:${id}`, retired: false,
      retirement_note: null, creator_id: 'u-e2e', updated_at: NOW,
      payload: (parsed.payload ?? {}) as Record<string, unknown>,
      versions: [{ id: `${id}-v1`, spec_id: id, version: 1, description: null,
        deprecated: false, parent_version_id: null }],
    };
    world.designSpecs.push(spec);
    return { success: true, spec: specSummaryView(spec) };
  });

  const seriesSummaryView = (series: MapSeriesStub) => {
    const { payload, ...summary } = series;
    const members = (payload as { members?: unknown[] }).members ?? [];
    return { ...summary, member_count: Array.isArray(members) ? members.length : 0 };
  };
  const seriesById = (id: string | null): MapSeriesStub | undefined =>
    world.mapSeries.find((s) => s.id === id);

  await api(/\/api\/v1\/map-series\/[^/]+\/propagation\/preview(\?|$)/, async (url, method, body) => {
    if (method !== 'POST') return { detail: 'method not allowed' };
    const series = seriesById(url.pathname.split('/')[4]);
    void body;
    const members = ((series?.payload as { members?: Array<Record<string, unknown>> }).members ?? []);
    // 种子投影：规范 pin 把 palette intent 投到每个成员；user-wins 锁成员
    // 命中 locked_fields_hit（app/services/map_series/propagation.py 形状）。
    return {
      success: true,
      series_id: series?.id ?? null,
      member_count: members.length,
      unresolvable_member_ids: [],
      members: members.map((m) => {
        const locked = Array.isArray(m.locked_fields) && m.locked_fields.length > 0;
        return locked
          ? { member_id: m.member_id, session_id: m.session_id, unresolvable: false,
            locked_fields_hit: ['palette'], changes: [], skipped:
              [{ intent: 'palette', reason: 'user_locked', field: 'palette' }],
            eligible: false }
          : { member_id: m.member_id, session_id: m.session_id, unresolvable: false,
            locked_fields_hit: [],
            changes: [{ intent: 'palette', action: 'patch_paint_and_ramp',
              component: 'thematic', detail: { ramp_len: 5 } }],
            skipped: [], eligible: true };
      }),
    };
  });
  await api(/\/api\/v1\/map-series\/[^/]+\/propagation\/apply(\?|$)/, async (url, method) => {
    if (method !== 'POST') return { detail: 'method not allowed' };
    const series = seriesById(url.pathname.split('/')[4]);
    const members = ((series?.payload as { members?: Array<Record<string, unknown>> }).members ?? []);
    let revision = 1;
    return {
      success: true,
      receipts: members.map((m) => {
        const locked = Array.isArray(m.locked_fields) && m.locked_fields.length > 0;
        revision += 1;
        return locked
          ? { member_id: m.member_id, status: 'skipped_locked', revision: null,
            error: 'locked fields intersect' }
          : { member_id: m.member_id, status: 'committed', revision, error: null };
      }),
    };
  });
  await api(/\/api\/v1\/map-series\/[^/]+\/freeze-plan(\?|$)/, async (url, method) => {
    if (method !== 'POST') return { detail: 'method not allowed' };
    const series = seriesById(url.pathname.split('/')[4]);
    const members = ((series?.payload as { members?: Array<Record<string, unknown>> }).members ?? []);
    return {
      success: true,
      freeze_plan: {
        series_id: series?.id ?? null,
        members: members.map((m) => ({
          session_id: m.session_id, mapspec_fingerprint: `fp-${String(m.member_id)}`,
          title: m.title, design_spec_id: series?.design_spec_id ?? null,
          output_profile: 'default',
        })),
      },
    };
  });
  await api(/\/api\/v1\/map-series\/[^/]+\/consistency(\?|$)/, async (url) => {
    const series = seriesById(url.pathname.split('/')[4]);
    void series;
    return {
      ok: true,
      findings: [
        { code: 'LEGEND_MISSING', severity: 'warning', member_id: 'm_2024',
          component: 'legend', message: '专题层无 legend 组件',
          fix_hint: '在成员会话添加 legend 组件' },
        { code: 'EXTENT_DIVERGENCE', severity: 'info', member_id: null,
          component: 'extent', message: '成员间 bbox 显著不一致（仅披露）', fix_hint: null },
      ],
    };
  });
  await api(/\/api\/v1\/map-series\/[^/]+\/members(\?|$)/, async (url, method, body) => {
    if (method !== 'POST') return { detail: 'method not allowed' };
    const series = seriesById(url.pathname.split('/')[4]);
    if (!series) return { detail: { code: 'MAP_SERIES_NOT_FOUND' } };
    let parsed: { op?: string; member?: Record<string, unknown>; member_id?: string } = {};
    try { parsed = JSON.parse(body); } catch { /* non-JSON not exercised */ }
    const payload = series.payload as { members: Array<Record<string, unknown>> };
    if (parsed.op === 'remove' && parsed.member_id) {
      payload.members = payload.members.filter((m) => m.member_id !== parsed.member_id);
      series.updated_at = NOW;
      return { success: true, op: 'remove', member_id: parsed.member_id };
    }
    if (parsed.op === 'upsert' && parsed.member?.member_id) {
      const rest = payload.members.filter((m) => m.member_id !== parsed.member?.member_id);
      payload.members = [...rest, parsed.member];
      series.updated_at = NOW;
      return { success: true, op: 'upsert', member_count: payload.members.length };
    }
    return { detail: { code: 'MAP_SERIES_INVALID' } };
  });
  await api(/\/api\/v1\/map-series\/[^/]+\/versions(\?|$)/, async (url, method, body) => {
    if (method !== 'POST') return { detail: 'method not allowed' };
    const series = seriesById(url.pathname.split('/')[4]);
    if (!series) return { detail: { code: 'MAP_SERIES_NOT_FOUND' } };
    let parsed: { payload?: Record<string, unknown> } = {};
    try { parsed = JSON.parse(body); } catch { /* non-JSON not exercised */ }
    if (parsed.payload) series.payload = parsed.payload;
    series.current_version += 1;
    return {
      success: true,
      version: { id: `${series.id}-v${series.current_version}`, series_id: series.id,
        version: series.current_version, deprecated: false },
    };
  });
  await api(/\/api\/v1\/map-series\/[^/]+(\?|$)/, async (url) => {
    const series = seriesById(url.pathname.split('/').pop() as string);
    if (!series) return { detail: { code: 'MAP_SERIES_NOT_FOUND' } };
    return { success: true, series: seriesSummaryView(series), payload: series.payload };
  });
  await api(/\/api\/v1\/map-series(\?|$)/, async (_url: URL, method: string, body: string) => {
    if (method !== 'POST') {
      return { success: true, items: world.mapSeries.map(seriesSummaryView) };
    }
    let parsed: { payload?: { name?: string; design_spec_id?: string; members?: unknown[] } } = {};
    try { parsed = JSON.parse(body); } catch { /* non-JSON not exercised */ }
    const id = `mser-${world.mapSeries.length + 1}`;
    const series: MapSeriesStub = {
      id, name: parsed.payload?.name ?? 'e2e 系列', description: null,
      status: 'draft', current_version: 1, structural_digest: `mser-sha256:${id}`,
      design_spec_id: parsed.payload?.design_spec_id ?? null, updated_at: NOW,
      payload: (parsed.payload ?? {}) as Record<string, unknown>,
    };
    world.mapSeries.push(series);
    return { success: true, series: seriesSummaryView(series) };
  });
}

export { completingTurn, failingTurn, hangingTurn };
