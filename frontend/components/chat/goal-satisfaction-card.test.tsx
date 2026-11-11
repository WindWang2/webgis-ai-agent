/**
 * GoalSatisfactionCard（G28 / P4）渲染测试。
 *
 * 契约：裁决徽标 + 逐需求行（id/verdict/缺失码/建议动作）；「计算完成、
 * 地图等待显示」与「任务失败」通过 verdict 徽标与缺失码可区分（A24 UI 面）。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { GoalSatisfactionCard } from './goal-satisfaction-card';
import type { GoalSatisfactionView } from '@/lib/types/goal-satisfaction';

// i18n provider 依赖：chat-tab 外单独挂载时 useT 有默认回落（zh-CN）。
function makeView(overrides: Partial<GoalSatisfactionView> = {}): GoalSatisfactionView {
  return {
    verdict: 'partial',
    signal: 'continue',
    counts: { partial: 1, fulfilled: 1 },
    missing_summary: ['map:frame_product_incomplete'],
    summary_line: '[GIS Goal] goal=partial signal=continue',
    requirements: [
      {
        id: 'map',
        kind: 'map',
        verdict: 'partial',
        missing: ['frame_not_observed:f2'],
        next_action: 'reobserve',
      },
      {
        id: 'statistics',
        kind: 'statistics',
        verdict: 'fulfilled',
        missing: [],
        next_action: '',
      },
    ],
    ...overrides,
  };
}

describe('GoalSatisfactionCard', () => {
  it('渲染裁决徽标与逐需求行', () => {
    render(<GoalSatisfactionCard view={makeView()} />);
    const card = screen.getByTestId('goal-satisfaction-card');
    expect(card).toBeTruthy();
    expect(card.getAttribute('data-verdict')).toBe('partial');
    const rows = screen.getAllByTestId('goal-requirement-row');
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute('data-requirement-id')).toBe('map');
    // 缺失码与建议动作可见（无需打开开发日志）
    expect(screen.getByTitle('frame_not_observed:f2')).toBeTruthy();
    expect(card.textContent).toContain('reobserve');
    expect(card.textContent).toContain('fulfilled');
  });

  it('failed 裁决与 partial 可区分（任务失败 ≠ 等待显示）', () => {
    const { unmount } = render(
      <GoalSatisfactionCard view={makeView({ verdict: 'failed' })} />,
    );
    expect(screen.getByTestId('goal-satisfaction-card').getAttribute('data-verdict'))
      .toBe('failed');
    unmount();
    render(<GoalSatisfactionCard view={makeView({ verdict: 'satisfied' })} />);
    expect(screen.getByTestId('goal-satisfaction-card').getAttribute('data-verdict'))
      .toBe('satisfied');
  });

  it('无需求行时不渲染 ledger（诚实缺席，不造行）', () => {
    render(
      <GoalSatisfactionCard
        view={makeView({ requirements: [], counts: {} })}
      />,
    );
    expect(screen.queryAllByTestId('goal-requirement-row')).toHaveLength(0);
    expect(screen.getByTestId('goal-satisfaction-card')).toBeTruthy();
  });
});
