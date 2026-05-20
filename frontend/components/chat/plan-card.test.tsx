import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { PlanCard } from './plan-card';
import type { AgentPlanState } from '@/lib/types/agent-plan';

const buildPlan = (): AgentPlanState => ({
  intent: '成都医疗设施热力分析',
  domains: ['chinese', 'core'],
  steps: [
    { n: 1, goal: '获取成都边界', tool_family: 'chinese', status: 'done' },
    { n: 2, goal: '查询医院 POI', tool_family: 'chinese', status: 'done' },
    { n: 3, goal: '生成热力图', tool_family: 'core', status: 'pending' },
  ],
  finalized: false,
});

describe('PlanCard', () => {
  it('renders intent and step goals', () => {
    render(<PlanCard plan={buildPlan()} />);
    expect(screen.getByText('成都医疗设施热力分析')).toBeInTheDocument();
    expect(screen.getByText('获取成都边界')).toBeInTheDocument();
    expect(screen.getByText('查询医院 POI')).toBeInTheDocument();
    expect(screen.getByText('生成热力图')).toBeInTheDocument();
  });

  it('shows done/total counter', () => {
    render(<PlanCard plan={buildPlan()} />);
    expect(screen.getByText('2 / 3')).toBeInTheDocument();
  });

  it('returns null when steps array is empty', () => {
    const empty: AgentPlanState = { intent: 'x', domains: [], steps: [], finalized: false };
    const { container } = render(<PlanCard plan={empty} />);
    expect(container.firstChild).toBeNull();
  });

  it('applies opacity-50 class to skipped steps', () => {
    const plan = buildPlan();
    plan.steps[2].status = 'skipped';
    plan.finalized = true;
    const { container } = render(<PlanCard plan={plan} />);
    const items = container.querySelectorAll('li');
    expect(items.length).toBe(3);
    // The third item (skipped) should have opacity-50
    expect(items[2].className).toContain('opacity-50');
    // First two (done) should not
    expect(items[0].className).not.toContain('opacity-50');
  });
});
