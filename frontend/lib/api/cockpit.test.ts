/**
 * Agent Ops Cockpit typed client — 契约测试。
 *
 * 覆盖：envelope 形状（schema/cockpit.v1）、bounded limit 客户端钳制、
 * ownerToken → X-Session-Token（session 端点归属证明链）、operator 动作
 * 走既有 mission-runtime 路由（POST body {worker_id}，非幂等不重试）。
 */
import { describe, expect, it, vi, afterEach } from 'vitest';
import {
  listCockpitMissions,
  getCockpitMission,
  getCockpitTimeline,
  getCockpitSwarm,
  getCockpitSessionSkill,
  getCockpitSessionEvidence,
  getCockpitSessionTrace,
  suspendCockpitMission,
  resumeCockpitMission,
  cancelCockpitMission,
  COCKPIT_SCHEMA,
} from './cockpit';

function stubFetch(routes: Array<{
  method: string; path: string; status?: number; body?: unknown;
}>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = (init?.method ?? 'GET').toUpperCase();
    const route = routes.find(
      (r) => r.method === method && (url.pathname === r.path || url.pathname.startsWith(r.path)),
    );
    if (!route) throw new Error(`no route: ${method} ${url.pathname}`);
    return new Response(JSON.stringify(route.body ?? {}), {
      status: route.status ?? 200,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

const missionSummary = {
  mission_id: 'm-1', org_id: 'org-a', state: 'running', revision: 3,
  goal_revision: 2, root_goal: '成都小学分布分析', project_id: null,
  created_at: 1, updated_at: 2, lease_owner: 'w1', blocked_reason: '',
  frontier_counts: { completed: 1, running: 1, pending: 2, failed: 0, blocked: 0 },
  resource: { quota: { tokens: 100 }, consumed: { tokens: 10 }, reserved: {} },
};

describe('cockpit typed client', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('listCockpitMissions — envelope 透传 + limit 客户端钳制到 200', async () => {
    const fn = stubFetch([
      {
        method: 'GET', path: '/api/v1/cockpit/missions',
        body: {
          schema: COCKPIT_SCHEMA, generated_at: 1,
          missions: [missionSummary], has_active: true, poll_after_ms: 2000,
        },
      },
    ]);
    const res = await listCockpitMissions({ limit: 99999 });
    expect(res.schema).toBe('cockpit.v1');
    expect(res.missions).toHaveLength(1);
    expect(res.missions[0].frontier_counts.failed).toBe(0);
    expect(res.has_active).toBe(true);
    const url = new URL(String(fn.mock.calls[0][0]));
    expect(url.searchParams.get('limit')).toBe('200');
  });

  it('getCockpitMission — mission + diagnostics 组合投影', async () => {
    stubFetch([
      {
        method: 'GET', path: '/api/v1/cockpit/missions/m-1',
        body: {
          schema: COCKPIT_SCHEMA, generated_at: 1,
          mission: { mission_id: 'm-1', state: 'running', revision: 3, refs: {}, frontier: {}, resource_budget: {}, failure: {}, recovery: {} },
          diagnostics: { mission_id: 'm-1', state: 'running', revision: 3, goal_revision: 2, current_frontier: {}, blocked_reason: '', artifact_count: 0, swarm_status: '', resource_use: {}, last_checkpoint: 'cp-1', lease_owner: 'w1', lease_epoch: 1, recovery_count: 0 },
        },
      },
    ]);
    const res = await getCockpitMission('m-1');
    expect(res.mission.revision).toBe(3);
    expect(res.diagnostics.last_checkpoint).toBe('cp-1');
  });

  it('getCockpitTimeline — checkpoints 有界 + limit 钳制到 32', async () => {
    const fn = stubFetch([
      {
        method: 'GET', path: '/api/v1/cockpit/missions/m-1/timeline',
        body: {
          schema: COCKPIT_SCHEMA, mission_id: 'm-1', state: 'running',
          revision: 3, goal_revision: 2, root_goal: 'g', updated_at: 9,
          blocked_reason: 'quota:tokens', refs: { active_session_ids: ['s1'] },
          checkpoints: [
            { checkpoint_id: 'cp-2', mission_revision: 3, goal_revision: 2, state: 'running', created_at: 9 },
            { checkpoint_id: 'cp-1', mission_revision: 1, goal_revision: 1, state: 'planning', created_at: 5 },
          ],
        },
      },
    ]);
    const res = await getCockpitTimeline('m-1', { limit: 500 });
    expect(res.checkpoints).toHaveLength(2);
    expect(res.checkpoints[0].checkpoint_id).toBe('cp-2');
    expect(res.blocked_reason).toBe('quota:tokens');
    const url = new URL(String(fn.mock.calls[0][0]));
    expect(url.searchParams.get('limit')).toBe('32');
  });

  it('getCockpitSwarm — runs + 任务态透传', async () => {
    stubFetch([
      {
        method: 'GET', path: '/api/v1/cockpit/missions/m-1/swarm',
        body: {
          schema: COCKPIT_SCHEMA, mission_id: 'm-1',
          runs: [{
            swarm_run_id: 'sr-1', mission_id: 'm-1', goal_slice: 'slice', state: 'running',
            created_at: 1, updated_at: 2,
            tasks: { t1: { task_id: 't1', assignment_id: '', state: 'SUCCEEDED', operation_class: 'pure', produced_refs: [], summary: '', error_code: '', attempt: 1, idempotency_key: '', settled_at: 2 } },
          }],
        },
      },
    ]);
    const res = await getCockpitSwarm('m-1');
    expect(res.runs[0].tasks.t1.state).toBe('SUCCEEDED');
  });

  it('session 端点带 ownerToken → X-Session-Token 头', async () => {
    const fn = stubFetch([
      { method: 'GET', path: '/api/v1/cockpit/sessions/s1/skill', body: { schema: COCKPIT_SCHEMA, session_id: 's1', present: false, guidance: null, mission_id: '' } },
    ]);
    await getCockpitSessionSkill('s1', { ownerToken: 'tok-123' });
    const init = fn.mock.calls[0][1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get('X-Session-Token')).toBe('tok-123');
  });

  it('getCockpitSessionEvidence — claims/edges/stats/truncated', async () => {
    stubFetch([
      {
        method: 'GET', path: '/api/v1/cockpit/sessions/s1/evidence',
        body: {
          schema: COCKPIT_SCHEMA, session_id: 's1', present: true, truncated: false,
          claims: [{ claim_id: 'c1', claim_type: 'density', status: 'supported', supporting_evidence_refs: ['e1'], contradicting_evidence_refs: [] }],
          edges: [{ edge_id: 'ed1', relation: 'supports', src: 'c1', dst: 'e1' }],
          stats: { claims: 1, evidence: 1, edges: 1 },
        },
      },
    ]);
    const res = await getCockpitSessionEvidence('s1');
    expect(res.present).toBe(true);
    expect(res.claims[0].status).toBe('supported');
    expect(res.edges[0].relation).toBe('supports');
  });

  it('getCockpitSessionTrace — events 有界 + limit 钳制到 64', async () => {
    const fn = stubFetch([
      {
        method: 'GET', path: '/api/v1/cockpit/sessions/s1/trace',
        body: {
          schema: COCKPIT_SCHEMA, session_id: 's1',
          events: [{ ts: 1.5, stage: 'observation', detail: { layer: 'roads' } }],
          summary: { by_stage: { observation: 1 } },
          counters: { observation_rejects: 0 },
        },
      },
    ]);
    const res = await getCockpitSessionTrace('s1', { limit: 9999 });
    expect(res.events).toHaveLength(1);
    expect(res.events[0].stage).toBe('observation');
    const url = new URL(String(fn.mock.calls[0][0]));
    expect(url.searchParams.get('limit')).toBe('64');
  });

  it('operator 动作 — POST mission-runtime 路由 + worker_id body，无 ownerToken 泄漏', async () => {
    const fn = stubFetch([
      { method: 'POST', path: '/api/v1/mission-runtime/missions/m-1/suspend', body: { mission_id: 'm-1', state: 'suspended', revision: 4 } },
    ]);
    const rec = await suspendCockpitMission('m-1');
    const init = fn.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ worker_id: 'ops-console' });
    expect(rec.state).toBe('suspended');
  });

  it('resume 返回恢复编排结果（dict，含 ok）', async () => {
    stubFetch([
      { method: 'POST', path: '/api/v1/mission-runtime/missions/m-1/resume', body: { ok: true } },
    ]);
    const res = await resumeCockpitMission('m-1');
    expect(res.ok).toBe(true);
  });

  it('cancel — 同一 worker 语义', async () => {
    const fn = stubFetch([
      { method: 'POST', path: '/api/v1/mission-runtime/missions/m-1/cancel', body: { mission_id: 'm-1', state: 'cancelled' } },
    ]);
    const rec = await cancelCockpitMission('m-1');
    const init = fn.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ worker_id: 'ops-console' });
    expect(rec.state).toBe('cancelled');
  });
});
