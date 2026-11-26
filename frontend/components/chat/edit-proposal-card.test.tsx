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

// ── ADR-0304 共享拓扑影响段（additive：仅共享 op 的 plan 带这些字段） ──

describe('EditProposalCard 影响段（ADR-0304）', () => {
  it('impact_summary + topology_context 渲染影响/违规/跨数据集披露', () => {
    render(
      <EditProposalCard
        proposal={proposal({
          ops: [{ op: 'move_shared_vertex' }],
          impact_summary: {
            touched_features: 3,
            affected_neighbors: 2,
            neighbors_truncated: true,
            area_delta_m2: 12.345,
            length_delta_m: -0.5,
            atomicity: { scope: 'ref:data-test0001', cross_dataset: 'blocked', note: 'dataset-scoped' },
          },
          topology_context: { new_violations: 1, existing_violations: 4 },
        })}
      />,
    );
    const section = screen.getByTestId('edit-proposal-impact');
    expect(section.textContent).toContain('影响要素 3');
    expect(section.textContent).toContain('邻接 2');
    expect(section.textContent).toContain('邻接清单已截断');
    expect(section.textContent).toContain('面积变化 +12.35');
    expect(section.textContent).toContain('长度变化 -0.5');
    expect(section.textContent).toContain('新增违规 1（阻断）');
    expect(section.textContent).toContain('基线存量 4（披露）');
    expect(screen.getByText(/跨数据集不支持原子提交/)).toBeTruthy();
  });

  it('无新增违规 → 视觉状态为绿色；共享 op 标签来自新 namespace', () => {
    render(
      <EditProposalCard
        proposal={proposal({
          ops: [{ op: 'move_shared_vertex' }],
          topology_context: { new_violations: 0, existing_violations: 2 },
        })}
      />,
    );
    expect(screen.getByText(/移动共享顶点/)).toBeTruthy();
    expect(screen.getByText(/新增违规 0（阻断）/)).toBeTruthy();
    expect(screen.getByText(/基线存量 2（披露）/)).toBeTruthy();
    expect(screen.queryByText(/跨数据集不支持原子提交/)).toBeNull();
  });

  it('普通单要素 plan 不渲染影响段', () => {
    render(<EditProposalCard proposal={proposal()} />);
    expect(screen.queryByTestId('edit-proposal-impact')).toBeNull();
  });
});
