/**
 * ReviewDrawer 交互测试（jsdom + testing-library；API 层 mock）。
 * 锁定行为：列表渲染、详情锚态/冲突投影、批准动作回执、错误披露。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/lib/review/api', () => ({
  listReviewProposals: vi.fn(),
  getReviewProposalDetail: vi.fn(),
  reviewAction: vi.fn(),
}));
vi.mock('@/lib/mapspec/session-cursor', () => ({
  getMapSpecSessionCursor: () => ({ sessionId: 's1', ownerToken: null }),
}));

import { ReviewDrawer } from './review-drawer';
import {
  listReviewProposals,
  getReviewProposalDetail,
  reviewAction,
} from '@/lib/review/api';
import { resetReviewStoreForTests } from '@/lib/review/store';
import { ApiError } from '@/lib/api/transport';

const PROPOSALS = [
  {
    proposal_id: 'rp_1', title: '河流改色', status: 'submitted', risk: 'low',
    base_revision: 7, author: { actor_id: 'u1', actor_kind: 'user', role: 'editor' },
    updated_at: '2026-09-17T00:00:00+00:00',
  },
  {
    proposal_id: 'rp_2', title: '删掉路网层', status: 'approved', risk: 'high',
    base_revision: 7, author: { actor_id: 'u2', actor_kind: 'user', role: 'editor' },
    updated_at: '2026-09-17T00:00:00+00:00',
  },
];

const DETAIL_SUBMITTED = {
  proposal: {
    proposal_id: 'rp_1', title: '河流改色', status: 'submitted',
    comments: [
      { comment_id: 'rc_1', author: { actor_id: 'u2' }, body: '断点对齐 Class 2' },
      { comment_id: 'rc_2', author: { actor_id: 'u3' }, body: '旧图层的评论' },
    ],
  },
  anchor_states: { rc_1: 'ok', rc_2: 'stale' },
  conflict: false,
  current_revision: 7,
  base_revision: 7,
  policy: { snapshot: {}, satisfied: true, counted_approvals: 0, blocking_reasons: [] },
};

const DETAIL_APPROVED_CONFLICT = {
  proposal: { proposal_id: 'rp_2', title: '删掉路网层', status: 'approved', comments: [] },
  anchor_states: {},
  conflict: true,
  current_revision: 11,
  base_revision: 7,
  policy: { snapshot: {}, satisfied: true, counted_approvals: 1, blocking_reasons: [] },
};

beforeEach(() => {
  resetReviewStoreForTests();
  vi.mocked(listReviewProposals).mockResolvedValue(PROPOSALS);
  vi.mocked(getReviewProposalDetail).mockImplementation(async (pid: string) =>
    pid === 'rp_1' ? DETAIL_SUBMITTED : DETAIL_APPROVED_CONFLICT,
  );
  vi.mocked(reviewAction).mockResolvedValue(DETAIL_SUBMITTED);
});

describe('ReviewDrawer', () => {
  it('渲染列表并打开详情（锚态 stale 披露）', async () => {
    const user = userEvent.setup();
    render(<ReviewDrawer open onClose={vi.fn()} />);
    expect(await screen.findByText('河流改色')).toBeTruthy();
    await user.click(screen.getByText('河流改色'));
    await waitFor(() => expect(screen.getByTestId('review-detail')).toBeTruthy());
    // stale 锚点诚实披露
    expect(screen.getByTestId('review-stale').textContent).toContain('1');
    expect(screen.getByTestId('review-comments').textContent).toContain('锚点 stale');
  });

  it('批准动作：决策经 API 提交并刷新列表', async () => {
    const user = userEvent.setup();
    render(<ReviewDrawer open onClose={vi.fn()} />);
    await user.click(await screen.findByText('河流改色'));
    await waitFor(() => expect(screen.getByTestId('review-approve')).toBeTruthy());
    await user.click(screen.getByTestId('review-approve'));
    await waitFor(() =>
      expect(reviewAction).toHaveBeenCalledWith('rp_1', 'decisions', { decision: 'approve' }),
    );
  });

  it('冲突的 approved 提案显示冲突徽标且提供 rebase 而非合并', async () => {
    const user = userEvent.setup();
    render(<ReviewDrawer open onClose={vi.fn()} />);
    await user.click(await screen.findByText('删掉路网层'));
    await waitFor(() => expect(screen.getByTestId('review-conflict')).toBeTruthy());
    expect(screen.queryByTestId('review-merge')).toBeNull();
    expect(screen.getByTestId('review-rebase')).toBeTruthy();
  });

  it('合并失败（并发交错）以错误条披露', async () => {
    const user = userEvent.setup();
    const approvedDetail = {
      ...DETAIL_SUBMITTED,
      proposal: { ...DETAIL_SUBMITTED.proposal, status: 'approved' },
    };
    vi.mocked(getReviewProposalDetail).mockResolvedValue(approvedDetail);
    vi.mocked(reviewAction).mockResolvedValueOnce({
      ...approvedDetail,
      merge_outcome: { ok: false, conflict: false, interleaved: true, rolled_back: false, failure: 'superseded_mid_merge' },
    });
    render(<ReviewDrawer open onClose={vi.fn()} />);
    await user.click(await screen.findByText('河流改色'));
    await waitFor(() => expect(screen.getByTestId('review-merge')).toBeTruthy());
    await user.click(screen.getByTestId('review-merge'));
    await waitFor(() => expect(screen.getByTestId('review-error').textContent).toContain('并发'));
  });

  const MERGE_FAILURE_OUTCOME = {
    ok: false, conflict: false, interleaved: false, rolled_back: false, failure: 'base_revision_drift',
  };

  it('merge 409（legacy detail 信封）保留冲突文案并投影', async () => {
    const user = userEvent.setup();
    const approvedDetail = {
      ...DETAIL_SUBMITTED,
      proposal: { ...DETAIL_SUBMITTED.proposal, status: 'approved' },
    };
    vi.mocked(getReviewProposalDetail).mockResolvedValue(approvedDetail);
    vi.mocked(reviewAction).mockRejectedValueOnce(
      new ApiError(409, 'Conflict', {
        detail: {
          ...approvedDetail,
          merge_outcome: { ...MERGE_FAILURE_OUTCOME, conflict: true },
        },
      }),
    );
    render(<ReviewDrawer open onClose={vi.fn()} />);
    await user.click(await screen.findByText('河流改色'));
    await waitFor(() => expect(screen.getByTestId('review-merge')).toBeTruthy());
    await user.click(screen.getByTestId('review-merge'));
    await waitFor(() => expect(screen.getByTestId('review-error').textContent).toContain('漂移'));
  });

  it('merge 422（统一信封 data）披露引擎失败而非通用 HTTP 错误', async () => {
    const user = userEvent.setup();
    const approvedDetail = {
      ...DETAIL_SUBMITTED,
      proposal: { ...DETAIL_SUBMITTED.proposal, status: 'approved' },
    };
    vi.mocked(getReviewProposalDetail).mockResolvedValue(approvedDetail);
    vi.mocked(reviewAction).mockRejectedValueOnce(
      new ApiError(422, 'Unprocessable Entity', {
        code: 'VALIDATION_ERROR',
        success: false,
        message: '请求参数校验失败',
        data: {
          ...approvedDetail,
          merge_outcome: { ...MERGE_FAILURE_OUTCOME, rolled_back: true, failure: 'checkpoint_failed' },
        },
      }),
    );
    render(<ReviewDrawer open onClose={vi.fn()} />);
    await user.click(await screen.findByText('河流改色'));
    await waitFor(() => expect(screen.getByTestId('review-merge')).toBeTruthy());
    await user.click(screen.getByTestId('review-merge'));
    await waitFor(() => expect(screen.getByTestId('review-error').textContent).toContain('checkpoint_failed'));
  });
});
