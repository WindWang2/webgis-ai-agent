/**
 * G10 刷新面板 · 组件正反测试（vitest + testing-library）。
 *
 * 这是 UI 行为证据，**不替代**后端 HTTP 集成与真实浏览器 E2E：
 * API client 在此被替换成确定性返回值，主链证据见
 * tests/unit/test_project_data_refresh_routes.py 与 j13 journey。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { DataRefreshPanel } from './data-refresh-panel';

const api = vi.hoisted(() => ({
  listRefreshProposals: vi.fn(),
  getRefreshProposal: vi.fn(),
  getRefreshStatus: vi.fn(),
  compareRefreshCandidate: vi.fn(),
  checkProjectSourceUpdates: vi.fn(),
  previewProjectDataRefresh: vi.fn(),
  executeRefreshCandidate: vi.fn(),
  adoptRefreshCandidate: vi.fn(),
  discardRefreshCandidate: vi.fn(),
  rollbackRefreshCandidate: vi.fn(),
}));

vi.mock('@/lib/i18n/useT', () => ({ useT: () => (key: string) => key }));
vi.mock('@/lib/api/project-data-refresh', () => api);

const CHECK = {
  project_id: 'proj-test', checked_at: 1, sources_total: 1,
  changed: 1, unchanged: 0, missing: 0, unverifiable: 0,
  inventory: {
    items: [{
      logical_id: 'dataset:ds1', display_name: 'schools', kind: 'vector',
      state: 'refreshable', evidence: 'content_exact',
      baseline_version: 'v1', current_version: 'v2', crs: 'EPSG:4326',
      bbox: null, verdict: 'changed', note: '',
    }],
    truncated: false, warnings: [], fingerprint: 'fp',
  },
  stale: true, warnings: [], truncated: false,
};

const PROPOSAL = {
  proposal_id: 'g10p-test000000000001', project_id: 'proj-test',
  session_id: 'sess-test', status: 'preview', created_at: 1,
  mapspec_revision: 3, proposal_fingerprint: 'g10:abc',
  source_versions: { 'dataset:ds1': 'v2' },
  inventory: CHECK.inventory,
  diffs: [{
    logical_id: 'dataset:ds1', change_class: 'content', evidence: 'exact',
    alignment: 'stable_id',
    exact_counts: { added: 2, removed: 1, attribute_changed: 0, geometry_changed: 0 },
    fields_added: [], fields_removed: [], fields_type_changed: [],
    fields_unit_changed: [], crs_changed: false, bbox_changed: false,
    temporal_changed: false, denominator_changed: false,
    raster_tiles_changed: [], expanded_dependencies: [], warnings: [],
    truncated: false, reason: 'content',
  }],
  plan: {
    recompute: ['data:schools', 'analysis:heat'], reuse: ['data:population'],
    decisions: {}, conservative: false, style_only: false,
    preserved_user_edits: ['layer:L1:paint'], conflicts: [],
    estimated_budget: { recompute_nodes: 2 }, warnings: [],
    proposal_fingerprint: 'g10:abc',
  },
  warnings: [], adopted_revision: null,
};

const STATUS = {
  proposal_id: PROPOSAL.proposal_id, proposal_status: 'preview',
  proposal_fingerprint: 'g10:abc', mapspec_revision: 3,
  candidate_status: 'not_started', candidate_attempts: 0,
  progress: [], adopted_revision: null, recoverable: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  api.listRefreshProposals.mockResolvedValue({ project_id: 'proj-test', count: 0, items: [] });
  api.checkProjectSourceUpdates.mockResolvedValue(CHECK);
  api.previewProjectDataRefresh.mockResolvedValue(PROPOSAL);
  api.getRefreshStatus.mockResolvedValue(STATUS);
  api.compareRefreshCandidate.mockResolvedValue({
    proposal_id: PROPOSAL.proposal_id, candidate_status: 'succeeded',
    candidate_available: true, diff_counts: [], recompute: [], reuse: [],
    preserved_user_edits: ['layer:L1:paint'], conflicts: [],
    hard_semantic_errors: [], adoptable: true, truncated: false, warnings: [],
  });
  api.executeRefreshCandidate.mockResolvedValue({
    candidate: null, replayed: false, conflicts: [], preserved: [],
    receipt: { tool_execution: 'ok', task_execution: 'ok',
               map_observation: 'unverified', version_adoption: 'unchanged' },
  });
  api.adoptRefreshCandidate.mockResolvedValue({
    proposal_id: PROPOSAL.proposal_id, action: 'adopt', status: 'adopted',
    tool_execution: 'ok', task_execution: 'ok',
    map_observation: 'unverified', version_adoption: 'adopted',
    revision_before: 3, revision_after: 4, warnings: [], replayed: false,
  });
});

describe('DataRefreshPanel', () => {
  it('requires authentication before showing any action', () => {
    render(<DataRefreshPanel projectId="proj-test" sessionId="sess-test" authed={false} />);
    expect(screen.getByText('g10NeedAuth')).toBeDefined();
    expect(screen.queryByTestId('g10-check-updates')).toBeNull();
  });

  it('requires a session context', () => {
    render(<DataRefreshPanel projectId="proj-test" sessionId={null} authed />);
    expect(screen.getByText('g10NeedSession')).toBeDefined();
  });

  it('check updates calls the real API client and shows bounded source summary', async () => {
    render(<DataRefreshPanel projectId="proj-test" sessionId="sess-test" authed />);
    fireEvent.click(screen.getByTestId('g10-check-updates'));
    await waitFor(() => expect(api.checkProjectSourceUpdates).toHaveBeenCalledWith(
      'proj-test', 'sess-test', true));
    expect(screen.getByTestId('g10-check-summary')).toBeDefined();
    expect(screen.getByTestId('g10-source-list').textContent).toContain('content_exact');
    expect(screen.getByText('g10Stale')).toBeDefined();
  });

  it('preview shows exact diff counts and the minimal recompute plan', async () => {
    render(<DataRefreshPanel projectId="proj-test" sessionId="sess-test" authed />);
    fireEvent.click(screen.getByTestId('g10-check-updates'));
    await waitFor(() => expect(screen.getByTestId('g10-check-summary')).toBeDefined());
    fireEvent.click(screen.getByTestId('g10-preview'));
    await waitFor(() => expect(screen.getByTestId('g10-diff-counts')).toBeDefined());
    expect(screen.getByTestId('g10-recompute-plan')).toBeDefined();
    expect(screen.getByText('g10ReadonlyNote')).toBeDefined();
  });

  it('adopt requires an explicit confirmation dialog and discloses three states', async () => {
    render(<DataRefreshPanel projectId="proj-test" sessionId="sess-test" authed />);
    fireEvent.click(screen.getByTestId('g10-preview'));
    await waitFor(() => expect(screen.getByTestId('g10-diff-counts')).toBeDefined());
    // no candidate yet → adopt disabled
    const adopt = screen.getByTestId('g10-adopt') as HTMLButtonElement;
    expect(adopt.disabled).toBe(true);
    fireEvent.click(screen.getByTestId('g10-execute'));
    await waitFor(() => expect(api.compareRefreshCandidate).toHaveBeenCalled());
    expect(screen.getByTestId('g10-adopt')).toBeDefined();
    expect(screen.getByText('g10MapUnverified')).toBeDefined();
  });
});
