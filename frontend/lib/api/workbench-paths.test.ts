import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as topology from './topology-workbench';
import { dataJoinApi } from './data-join-authoring';

const fetchMock = vi.fn();
const join = dataJoinApi.client('session/a b', 'owner-test');
const source = { kind: 'session_ref' as const, ref: 'layer' };
const plan = { title: 'joined', mode: 'attribute' as const, left: source, right: source, keys: [{ left_field: 'id', right_field: 'id' }] };
type PathCase = [string, () => Promise<unknown>, string, string];
const topologyCases: PathCase[] = [
  ['catalog', () => topology.fetchRulesCatalog(), 'GET', '/rules/catalog'],
  ['rulesets', () => topology.fetchRulesets({ project_id: 'project', limit: 5 }), 'GET', '/rulesets?project_id=project&limit=5'],
  ['create', () => topology.createRuleset({ name: 'rules', rules: [] }), 'POST', '/rulesets'],
  ['update', () => topology.updateRuleset('rules/a', { rules: [] }), 'PUT', '/rulesets/rules%2Fa'],
  ['submit', () => topology.submitTopologyRun({ ruleset_id: 'rules', bindings: {} }), 'POST', '/runs'],
  ['runs', () => topology.fetchRuns({ session_id: 'session', limit: 5 }), 'GET', '/runs?session_id=session&limit=5'],
  ['run', () => topology.fetchRun('run/a'), 'GET', '/runs/run%2Fa'],
  ['findings', () => topology.fetchFindings('run/a', { severity: 'error', offset: 2 }), 'GET', '/runs/run%2Fa/findings?severity=error&limit=20&offset=2'],
  ['finding', () => topology.fetchFinding('finding/a'), 'GET', '/findings/finding%2Fa'],
  ['witness', () => topology.fetchFindingWitness('finding/a'), 'GET', '/findings/finding%2Fa/witness'],
  ['accept', () => topology.acceptException('finding/a', 'reason'), 'POST', '/findings/finding%2Fa/exception'],
  ['revoke', () => topology.revokeException('exception/a'), 'DELETE', '/exceptions/exception%2Fa'],
  ['repair', () => topology.fetchRepairCandidates('finding/a'), 'POST', '/findings/finding%2Fa/repair'],
  ['recheck', () => topology.recheckRun('run/a'), 'POST', '/runs/run%2Fa/recheck'],
];
const joinCases: PathCase[] = [
  ['describe', () => join.describe(source), 'POST', '/sources/describe'],
  ['create', () => join.createPlan(plan), 'POST', '/plans'],
  ['update', () => join.updatePlan('plan', plan, 3), 'PATCH', '/plans/plan'],
  ['get', () => join.getPlan('plan'), 'GET', '/plans/plan'],
  ['list', () => join.listPlans(), 'GET', '/plans'],
  ['preview', () => join.preview('plan', 3), 'POST', '/plans/plan/preview'],
  ['execute', () => join.execute('plan', 3, false), 'POST', '/plans/plan/execute'],
  ['inspect', () => join.inspectRun('run'), 'GET', '/runs/run'],
  ['results', () => join.resultPage('run', 12, 25), 'GET', '/runs/run/results?cursor=12&limit=25'],
  ['cancel', () => join.cancelRun('run'), 'POST', '/runs/run/cancel'],
  ['render', () => join.renderRef('run'), 'POST', '/runs/run/render-ref'],
];

describe('versioned workbench routes (#2176)', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset().mockResolvedValue({ ok: true, status: 200, text: async () => '{}' });
  });
  it.each(topologyCases)('topology %s uses the mounted route', async (_name, invoke, method, path) => {
    await invoke();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url);
    expect(parsed.pathname + parsed.search).toBe('/api/v1/topology-workbench' + path);
    expect(init.method).toBe(method);
  });
  it.each(joinCases)('join %s uses the mounted route and owner', async (_name, invoke, method, path) => {
    await invoke();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url);
    expect(parsed.pathname + parsed.search).toBe('/api/v1/data-join-authoring/sessions/session%2Fa%20b' + path);
    expect(init.method).toBe(method);
    expect(new Headers(init.headers).get('X-Session-Token')).toBe('owner-test');
  });
});
