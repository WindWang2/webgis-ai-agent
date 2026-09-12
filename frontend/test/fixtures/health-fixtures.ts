/**
 * 系统健康面 + data-fabric 披露 fixtures（三态，ADR-0142 D4/D5/D6）。
 * 形状逐字对照 /api/v1/health*、/version、/status/detailed（recon §4）
 * 与 engine_breaker.disclosure()/result_cache 披露（recon §5）。
 */

export const healthBasicFixture = {
  status: 'healthy',
  timestamp: new Date().toISOString(),
  service: 'WebGIS AI Agent',
  version: '0.1.3',
  agent_runtime: 'pi',
  pi_workers_alive: '4/4',
};

export const readyOkFixture = { ready: true };
export const readyDownFixture = { status: 503, body: { ready: false } };

export const detailedStatusOkFixture = {
  status: 'ok',
  components: {
    db: { status: 'ok', latency_ms: 2.4, detail: null },
    redis: { status: 'ok', latency_ms: 0.6, detail: null },
    llm: { status: 'ok', latency_ms: 412.8, detail: null },
    worker: { status: 'ok', latency_ms: 5.1, detail: null },
    object_store: { status: 'not_configured', latency_ms: null, detail: 'S3 not configured' },
  },
  stuck_jobs: 1,
  refresh_age_s: 0.4,
};

/** 降级态：redis down + llm degraded。 */
export const detailedStatusDegradedFixture = {
  status: 'degraded',
  components: {
    db: { status: 'ok', latency_ms: 2.9, detail: null },
    redis: { status: 'down', latency_ms: null, detail: 'connection refused' },
    llm: { status: 'degraded', latency_ms: 3_812.2, detail: 'p95 above budget' },
    worker: { status: 'ok', latency_ms: 6.3, detail: null },
    object_store: { status: 'ok', latency_ms: 18.4, detail: null },
  },
  stuck_jobs: 4,
  refresh_age_s: 1.2,
};

export const versionInfoFixture = {
  version: '0.1.3',
  commit: '8b5b8375abc',
  python: '3.12.7',
  extensions_api: '2',
  timestamp: new Date().toISOString(),
};

/** 任务中心 owner 域队列深度口径（/tasks/jobs?active_only=true）。 */
export const activeJobsFixture = {
  jobs: [
    { id: 'job-1', kind: 'workflow', name: '流域分析', status: 'running', progress: 42, active: true },
    { id: 'job-2', kind: 'analysis', name: '坡度统计', status: 'queued', progress: null, active: true },
  ],
  has_active: true,
  poll_after_ms: 3000,
};

export const emptyJobsFixture = { jobs: [], has_active: false, poll_after_ms: null };

/* ------------------------------------------------------------------ */
/* data-fabric 披露（engine_breaker.disclosure() / result_cache 内嵌键） */
/* ------------------------------------------------------------------ */

export const breakerClosedFixture = {
  state: 'closed',
  consecutive_failures: 0,
  failure_threshold: 3,
  cool_down_s: 60,
  total_fallbacks: 0,
};

export const breakerOpenFixture = {
  state: 'open',
  consecutive_failures: 3,
  failure_threshold: 3,
  cool_down_s: 60,
  total_fallbacks: 3,
};

export const breakerHalfOpenFixture = {
  state: 'half_open',
  consecutive_failures: 0,
  failure_threshold: 3,
  cool_down_s: 60,
  total_fallbacks: 7,
};

export const cacheLocalHitFixture = {
  hit: true,
  age_s: 12.8,
  ttl_s: 300,
  basis: 'ttl+fingerprint',
  key: '9f2c1e07aa4b13dc',
};

export const cacheDistributedHitFixture = {
  hit: true,
  age_s: null,
  ttl_s: 300,
  basis: 'ttl+fingerprint+distributed',
  key: '01ab77ee5590cc21',
};
