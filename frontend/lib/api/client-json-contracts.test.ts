import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as thematic from '@/lib/thematic-authoring/api';
import * as bivariate from '@/lib/bivariate-reliability/api';
import * as assets from '@/lib/cartographic-assets/api';
import * as raster from './raster-sampling';
import * as proposals from './feature-proposals';
import * as topology from './topology-workbench';
import { createSelectionHandle } from './selection-handles';
import { postContextPreview, putSourcePolicy } from './turnContext';

vi.mock('@/lib/mapspec/session-cursor', () => ({
  getMapSpecSessionCursor: () => ({ sessionId: 'session', ownerToken: 'owner-test' }),
}));
const fetchMock = vi.fn();
const ctx = { sessionId: 'session', ownerToken: 'owner-test' };
const plan = { layer_id: 'old-layer', mode: 'graduated_auto' as const, field: 'population' };
const bivariatePlan = { layer_id: 'old-layer', mode: 'revert' as const };
const boundBivariatePlan = { ...bivariatePlan, layer_id: 'layer' };
const boundPlan = { ...plan, layer_id: 'layer' };
const rasterPlan = { mode: 'source_points' as const, sources: [{ ref_id: 'raster', bands: [1] }] };
const ruleset = { name: 'rules', rules: [] };
const run = { session_id: 'session', ruleset_id: 'rules', bindings: {} } as Parameters<typeof topology.submitTopologyRun>[0];
const scope = { kind: 'ids' as const, ids: ['a', 'b'] };
const input = { ref_id: 'layer', ops: [{ op: 'delete', id: 'a' }], submit: true };
const policyBody = { mode: 'auto', preferred: ['s1'], excluded: [], expected_version: 3, project_id: 'p1' };
const previewBody = { project_id: 'p1', message_draft: 'hi' };

type WriteCase = [string, () => Promise<unknown>, unknown];
const cases: WriteCase[] = [
  ['thematic preview', () => thematic.previewThematicPlan(ctx, 'layer', plan), { plan: boundPlan }],
  ['thematic commit', () => thematic.commitThematicPlan(ctx, 'layer', plan, 3, 'mutation'), { plan: boundPlan, expected_revision: 3, client_mutation_id: 'mutation' }],
  ['bivariate preview', () => bivariate.previewG41Plan(ctx, 'layer', bivariatePlan), { plan: boundBivariatePlan }],
  ['bivariate commit', () => bivariate.commitG41Plan(ctx, 'layer', bivariatePlan, 3, 'digest', 'mutation'), { plan: boundBivariatePlan, expected_revision: 3, preview_digest: 'digest', client_mutation_id: 'mutation' }],
  ['assets preview', () => assets.previewBinding('session', 'layer', plan), { plan }],
  ['assets apply', () => assets.applyBinding('session', 'layer', plan, 3, 'mutation'), { plan, expected_revision: 3, client_mutation_id: 'mutation' }],
  ['assets unbind', () => assets.unbindLayer('session', 'layer', 3), { expected_revision: 3 }],
  ['raster create', () => raster.createPlan('session', rasterPlan), rasterPlan],
  ['raster revise', () => raster.revisePlan('session', 'plan', 3, { name: 'new' }), { expected_revision: 3, name: 'new' }],
  ['raster preview', () => raster.previewPlan('session', 'plan', 3, 25), { expected_revision: 3, limit: 25 }],
  ['raster execute', () => raster.executePlan('session', 'plan', 3, true), { expected_revision: 3, background: true }],
  ['proposal decision', () => proposals.decideFeatureProposal('proposal', 'approve', 'checked'), { action: 'approve', note: 'checked' }],
  ['proposal resolutions', () => proposals.submitFeatureProposalResolutions('proposal', 3, []), { expected_revision: 3, resolutions: [] }],
  ['proposal rebase', () => proposals.rebaseFeatureProposal('proposal', 3, true), { expected_revision: 3, apply: true }],
  ['proposal withdraw', () => proposals.withdrawFeatureProposal('proposal', 'reason'), { action: 'withdraw', note: 'reason' }],
  ['proposal create', () => proposals.createFeatureProposal(input), input],
  ['selection create', () => createSelectionHandle({ sessionId: 'session', refId: 'layer', scope }), scope],
  ['topology create', () => topology.createRuleset(ruleset), ruleset],
  ['topology update', () => topology.updateRuleset('rules', { name: 'new', rules: [] }), { name: 'new', rules: [] }],
  ['topology submit', () => topology.submitTopologyRun(run), run],
  ['topology exception', () => topology.acceptException('finding', 'accepted'), { reason: 'accepted' }],
  ['topology recheck', () => topology.recheckRun('run'), {}],
  ['turn context policy put', () => putSourcePolicy('session', policyBody, 'owner-test'), policyBody],
  ['turn context preview', () => postContextPreview('session', previewBody, 'owner-test'), previewBody],
];

describe('caller JSON contracts (#2175)', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset().mockResolvedValue({ ok: true, status: 200, text: async () => '{}' });
  });
  it.each(cases)('%s sends one JSON object encoding', async (name, invoke, expected) => {
    await invoke();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe(name === 'topology update' || name === 'turn context policy put' ? 'PUT' : 'POST');
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json');
    expect(JSON.parse(init.body as string)).toEqual(expected);
    expect(init.body).toBe(JSON.stringify(expected));
  });
});
