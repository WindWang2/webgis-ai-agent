import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { PlanProposalCard } from './plan-proposal-card';

const baseProps = {
  planId: 'ref:plan-abc',
  title: '海淀区医院密度热点',
  summary: '5 步，约 30 秒',
  stepCount: 5,
  status: 'pending' as const,
  stepsPreview: [
    { id: 's1', tool: 'get_local_admin_boundary', purpose: '取海淀边界' },
    { id: 's2', tool: 'search_poi_around', purpose: '医院 POI' },
    { id: 's3', tool: 'hotspot_analysis', purpose: '热点' },
  ],
};

describe('PlanProposalCard', () => {
  it('renders title, step count badge, and step list', () => {
    render(<PlanProposalCard {...baseProps} onApprove={() => {}} onRevise={() => {}} onReject={() => {}} />);
    expect(screen.getByText(/海淀区医院密度热点/)).toBeInTheDocument();
    expect(screen.getByText(/计划 · 5 步/)).toBeInTheDocument();
    expect(screen.getByText('get_local_admin_boundary')).toBeInTheDocument();
    expect(screen.getByText('hotspot_analysis')).toBeInTheDocument();
  });

  it('shows destructive warning banner when destructive_steps non-empty', () => {
    render(
      <PlanProposalCard
        {...baseProps}
        destructiveSteps={['s4']}
        stepsPreview={[
          ...baseProps.stepsPreview,
          { id: 's4', tool: 'create_new_skill', purpose: '注入分析脚本', destructive: true },
        ]}
        onApprove={() => {}}
        onRevise={() => {}}
        onReject={() => {}}
      />
    );
    expect(screen.getByText(/破坏性步骤/)).toBeInTheDocument();
    expect(screen.getByText(/⚠ 破坏性/)).toBeInTheDocument();
  });

  it('fires onApprove with the plan_id when 执行 button clicked', () => {
    const onApprove = vi.fn();
    render(<PlanProposalCard {...baseProps} onApprove={onApprove} onRevise={() => {}} onReject={() => {}} />);
    fireEvent.click(screen.getByText(/执行计划/));
    expect(onApprove).toHaveBeenCalledWith('ref:plan-abc');
  });

  it('fires onRevise / onReject with the plan_id', () => {
    const onRevise = vi.fn();
    const onReject = vi.fn();
    render(<PlanProposalCard {...baseProps} onApprove={() => {}} onRevise={onRevise} onReject={onReject} />);
    fireEvent.click(screen.getByText('修改'));
    fireEvent.click(screen.getByText('取消'));
    expect(onRevise).toHaveBeenCalledWith('ref:plan-abc');
    expect(onReject).toHaveBeenCalledWith('ref:plan-abc');
  });

  it('locks buttons when status is approved', () => {
    const onApprove = vi.fn();
    render(<PlanProposalCard {...baseProps} status="approved" onApprove={onApprove} onRevise={() => {}} onReject={() => {}} />);
    // 一个在 status badge、一个在按钮文字
    expect(screen.getAllByText(/已批准/).length).toBeGreaterThanOrEqual(1);
    // 锁定后按钮 disabled，点击不应触发
    const approveBtn = screen.getAllByText(/已批准/).find(el => el.closest('button'))?.closest('button');
    expect(approveBtn).toBeTruthy();
    expect(approveBtn).toBeDisabled();
    if (approveBtn) fireEvent.click(approveBtn);
    expect(onApprove).not.toHaveBeenCalled();
  });

  it('shows rejected badge when status is rejected', () => {
    render(<PlanProposalCard {...baseProps} status="rejected" onApprove={() => {}} onRevise={() => {}} onReject={() => {}} />);
    expect(screen.getByText(/已取消/)).toBeInTheDocument();
  });
});
