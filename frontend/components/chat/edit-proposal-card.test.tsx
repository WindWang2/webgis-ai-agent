/**
 * EditProposalCard 测试（/goal 03）：状态机显隐、diff 渲染、决策回调。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { EditProposalCard } from '@/components/chat/edit-proposal-card';
import type { FeatureEditProposalState } from '@/lib/types/feature-edit';

function proposal(overrides?: Partial<FeatureEditProposalState>): FeatureEditProposalState {
  return {
    plan_id: 'fep-abc123',
    ref_id: 'ref:data-test0001',
    status: 'pending',
    safe: false,
    n_targets: 3,
    ops: [{ op: 'set_fields', fields: ['type'] }, { op: 'move_by', dx_m: 5, dy_m: 0 }],
    schema_ops: [],
    preview: [
      {
        wfid: 'wf1-1',
        fields: { type: { before: '中学', after: '小学' } },
      },
      {
        wfid: 'wf1-2',
        geometry: { positions_before: 24, positions_after: 24 },
      },
    ],
    validation: [],
    expected_content_revision: 4,
    ...overrides,
  };
}

describe('EditProposalCard', () => {
  it('pending 状态渲染 diff 与双按钮，回调携带 plan_id', () => {
    const onDecide = vi.fn();
    render(<EditProposalCard proposal={proposal()} onDecide={onDecide} />);
    expect(screen.getByTestId('edit-proposal-card-pending')).toBeTruthy();
    // 字段 diff：before 删除线 + after 高亮
    expect(screen.getByText('中学')).toBeTruthy();
    expect(screen.getByText('小学')).toBeTruthy();
    // 几何摘要（顶点数）
    expect(screen.getByText(/24 → 24/)).toBeTruthy();
    fireEvent.click(screen.getByTestId('edit-proposal-approve'));
    expect(onDecide).toHaveBeenCalledWith('fep-abc123', 'approve');
    fireEvent.click(screen.getByTestId('edit-proposal-reject'));
    expect(onDecide).toHaveBeenCalledWith('fep-abc123', 'reject');
  });

  it('approving 禁用按钮；终态隐藏按钮', () => {
    const { rerender } = render(<EditProposalCard proposal={proposal({ status: 'approving' })} />);
    expect((screen.getByTestId('edit-proposal-approve') as HTMLButtonElement).disabled).toBe(true);
    rerender(<EditProposalCard proposal={proposal({ status: 'applied', receipt: { content_revision_after: 7 } })} />);
    expect(screen.queryByTestId('edit-proposal-approve')).toBeNull();
    expect(screen.getByTestId('edit-proposal-applied-receipt').textContent).toContain('7');
  });

  it('stale/conflicted/expired/error 各自的收敛文案', () => {
    for (const status of ['stale', 'conflicted', 'expired', 'error'] as const) {
      const { unmount } = render(<EditProposalCard proposal={proposal({ status })} />);
      expect(screen.getByTestId(`edit-proposal-card-${status}`)).toBeTruthy();
      unmount();
    }
  });

  it('超过 12 个要素的 diff 截断披露', () => {
    const many = Array.from({ length: 15 }, (_, i) => ({
      wfid: `wf1-${i}`,
      fields: { v: { before: i, after: i + 1 } },
    }));
    render(<EditProposalCard proposal={proposal({ preview: many })} />);
    expect(screen.getByText(/其余 3 个要素/)).toBeTruthy();
  });
});
