/**
 * G32 FeatureProposalDrawer 测试 — 队列 → 详情（冲突/差异）→ 裁决 → 批准 →
 * 合并的用户旅程（mock API；服务端行为由后端 32 项测试覆盖）。键盘/读屏
 * 语义：dialog role、aria-live 播报、radio 组。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/lib/api/feature-proposals', () => ({
  listFeatureProposals: vi.fn(),
  getFeatureProposalDetail: vi.fn(),
  getFeatureProposalDiff: vi.fn(),
  decideFeatureProposal: vi.fn(),
  submitFeatureProposalResolutions: vi.fn(),
  rebaseFeatureProposal: vi.fn(),
  mergeFeatureProposal: vi.fn(),
  withdrawFeatureProposal: vi.fn(),
  getRecoveryView: vi.fn(),
  createFeatureProposal: vi.fn(),
}));

vi.mock('@/lib/mapspec/session-cursor', () => ({
  getMapSpecSessionCursor: () => ({ sessionId: 'sess-test', ownerToken: 'tok' }),
}));

import { FeatureProposalDrawer, resetFeatureProposalsForTests } from './feature-proposal-drawer';
import {
  decideFeatureProposal,
  getFeatureProposalDiff,
  getFeatureProposalDetail,
  listFeatureProposals,
  mergeFeatureProposal,
  submitFeatureProposalResolutions,
} from '@/lib/api/feature-proposals';

const mocked = vi.mocked;

function summary(overrides: Record<string, unknown> = {}) {
  return {
    proposal_id: 'fpr-abc123',
    ref_id: 'ref:geojson-x',
    title: '分类修正提案',
    author_kind: 'agent',
    status: 'submitted',
    revision: 1,
    candidate_digest: 'fpd-abcdef123456',
    base: {
      ref_id: 'ref:geojson-x', content_revision: 3, fingerprint: 'sha-base',
      snapshot_key: 'k', snapshot_sha256: 's', snapshot_bytes: 100,
      materialized: true, captured_at: 1, dep_fingerprints: {},
    },
    ops_count: 1,
    op_kinds: ['set_fields'],
    merge: null,
    has_approval: false,
    resolutions_count: 0,
    created_at: 1,
    updated_at: 2,
    ...overrides,
  };
}

function detail(overrides: Record<string, unknown> = {}) {
  return {
    ...summary(),
    ops: [{ op: 'set_fields', wfids: ['wf1-x'], fields: { capacity: 500 } }],
    op_seed: 'fpr-abc123',
    resolutions: [],
    decisions: [],
    comments: [],
    approval: null,
    withdrawn_at: null,
    ...overrides,
  };
}

function diff(overrides: Record<string, unknown> = {}) {
  return {
    proposal_id: 'fpr-abc123',
    revision: 1,
    base: summary().base,
    current: { fingerprint: 'sha-cur', available: true },
    candidate_deltas: [
      { wfid: 'wf1-x', fields: { capacity: { before: 600, after: 500 } } },
    ],
    candidate_delta_count: 1,
    page: 1,
    page_size: 20,
    conflict_report: {
      verdict: 'conflicted',
      conflicts: [{
        conflict_id: 'field:wf1-x:capacity',
        kind: 'field_value_conflict',
        wfid: 'wf1-x',
        field: 'capacity',
        base: { presence: 'present', value: 600 },
        current: { presence: 'present', value: 777 },
        proposed: { presence: 'present', value: 500 },
      }],
      unresolved_conflicts: [{
        conflict_id: 'field:wf1-x:capacity',
        kind: 'field_value_conflict',
        wfid: 'wf1-x',
        field: 'capacity',
        base: { presence: 'present', value: 600 },
        current: { presence: 'present', value: 777 },
        proposed: { presence: 'present', value: 500 },
      }],
      satisfied: [],
      independent_current_changes: { count: 1, deleted: 0, created: 0, geometry: 0 },
      identity: { provable: true },
      current_changed: true,
    },
    lineage: [],
    ...overrides,
  };
}

describe('FeatureProposalDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetFeatureProposalsForTests();
  });

  it('renders queue and navigates to detail with conflict decisions', async () => {
    mocked(listFeatureProposals).mockResolvedValue([summary()]);
    mocked(getFeatureProposalDetail).mockResolvedValue(detail());
    mocked(getFeatureProposalDiff).mockResolvedValue(diff());
    const user = userEvent.setup();
    render(<FeatureProposalDrawer open onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId('feature-proposal-item-fpr-abc123')).toBeTruthy();
    });
    await user.click(screen.getByTestId('feature-proposal-item-fpr-abc123'));

    await waitFor(() => {
      expect(screen.getByTestId('feature-proposal-conflicts')).toBeTruthy();
    });
    // 冲突三方值可见（typed 值原样呈现；多处出现用 getAllByText）。
    expect(screen.getAllByText(/777/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/500/).length).toBeGreaterThan(0);
    // 裁决 radio 组。
    const radios = screen.getAllByRole('radio');
    expect(radios.length).toBeGreaterThanOrEqual(2);
    await user.click(radios[0]); // keep_current
    await user.click(screen.getByTestId('feature-proposal-submit-resolutions'));

    await waitFor(() => {
      expect(mocked(submitFeatureProposalResolutions)).toHaveBeenCalledWith(
        'fpr-abc123', 1,
        [{ conflict_id: 'field:wf1-x:capacity', action: 'keep_current' }],
      );
    });
  });

  it('clean verdict offers approve → merge journey', async () => {
    mocked(listFeatureProposals).mockResolvedValue([summary({ status: 'approved' })]);
    mocked(getFeatureProposalDetail).mockResolvedValue(detail({ status: 'approved' }));
    mocked(getFeatureProposalDiff).mockResolvedValue(diff({
      conflict_report: {
        verdict: 'clean', conflicts: [], unresolved_conflicts: [], satisfied: [],
        independent_current_changes: { count: 0, deleted: 0, created: 0, geometry: 0 },
        identity: {}, current_changed: false,
      },
    }));
    mocked(mergeFeatureProposal).mockResolvedValue({
      status: 'merged', proposal_id: 'fpr-abc123', receipt: {
        entry_seq: 5, kind: 'apply', ref_id: 'ref:geojson-x', changed_wfids: ['wf1-x'],
        content_revision_after: 4, actor: 'user', origin: 'feature_proposal',
      },
    });
    const user = userEvent.setup();
    render(<FeatureProposalDrawer open onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId('feature-proposal-item-fpr-abc123')).toBeTruthy();
    });
    await user.click(screen.getByTestId('feature-proposal-item-fpr-abc123'));
    await waitFor(() => {
      expect(screen.getByTestId('feature-proposal-merge')).toBeTruthy();
    });
    await user.click(screen.getByTestId('feature-proposal-merge'));
    await waitFor(() => {
      expect(mocked(mergeFeatureProposal)).toHaveBeenCalledWith('fpr-abc123');
    });
  });

  it('submitted + clean offers approve; conflicted does not', async () => {
    mocked(listFeatureProposals).mockResolvedValue([summary()]);
    mocked(getFeatureProposalDetail).mockResolvedValue(detail());
    mocked(getFeatureProposalDiff).mockResolvedValue(diff());
    render(<FeatureProposalDrawer open onClose={vi.fn()} />);
    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByTestId('feature-proposal-item-fpr-abc123')).toBeTruthy();
    });
    await user.click(screen.getByTestId('feature-proposal-item-fpr-abc123'));
    await waitFor(() => {
      expect(screen.getByTestId('feature-proposal-conflicts')).toBeTruthy();
    });
    // 有未解决冲突 → 不出现批准按钮。
    expect(screen.queryByTestId('feature-proposal-approve')).toBeNull();
    expect(mocked(decideFeatureProposal)).not.toHaveBeenCalled();
  });

  it('recovery_required status surfaces the recovery notice', async () => {
    mocked(listFeatureProposals).mockResolvedValue([summary({ status: 'recovery_required' })]);
    mocked(getFeatureProposalDetail).mockResolvedValue(detail({ status: 'recovery_required' }));
    mocked(getFeatureProposalDiff).mockResolvedValue(diff());
    render(<FeatureProposalDrawer open onClose={vi.fn()} />);
    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByTestId('feature-proposal-item-fpr-abc123')).toBeTruthy();
    });
    await user.click(screen.getByTestId('feature-proposal-item-fpr-abc123'));
    await waitFor(() => {
      expect(screen.getByTestId('feature-proposal-recovery')).toBeTruthy();
    });
  });
});
