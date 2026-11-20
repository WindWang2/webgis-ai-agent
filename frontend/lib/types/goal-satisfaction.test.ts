/**
 * GoalSatisfaction 解析器（G28 / P4 A24）测试。
 *
 * 契约：task_complete 载荷的 goal_satisfaction 加性键 → 有界视图；
 * 载荷缺席 / verdict 非法 / 形状非法 → null（不渲染半张卡，零漂移）。
 */
import { describe, it, expect } from 'vitest';
import { parseGoalSatisfaction } from './goal-satisfaction';
import {
  attachGoalSatisfaction,
  type ChatStreamMessage,
} from '@/lib/chat/message-reducer';

function makePayload(overrides: Record<string, unknown> = {}) {
  return {
    goal_satisfaction: {
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
    },
  };
}

describe('parseGoalSatisfaction', () => {
  it('解析合法载荷（逐需求行 + 缺失码 + 动作）', () => {
    const view = parseGoalSatisfaction(makePayload());
    expect(view).not.toBeNull();
    expect(view!.verdict).toBe('partial');
    expect(view!.signal).toBe('continue');
    expect(view!.requirements).toHaveLength(2);
    expect(view!.requirements[0]).toEqual({
      id: 'map',
      kind: 'map',
      verdict: 'partial',
      missing: ['frame_not_observed:f2'],
      next_action: 'reobserve',
    });
    expect(view!.requirements[1].verdict).toBe('fulfilled');
  });

  it('goal_satisfaction 键缺席 → null（旧后端零漂移）', () => {
    expect(parseGoalSatisfaction({ step_count: 1, summary: 'done' })).toBeNull();
  });

  it('verdict 非法 / 载荷非对象 → null（不渲染半张卡）', () => {
    expect(parseGoalSatisfaction({ goal_satisfaction: { verdict: 42 } })).toBeNull();
    expect(parseGoalSatisfaction({ goal_satisfaction: 'partial' })).toBeNull();
    expect(parseGoalSatisfaction(null)).toBeNull();
    expect(parseGoalSatisfaction('x')).toBeNull();
  });

  it('需求行 verdict 非法被丢弃；行数/字段有界（先截断后过滤，与后端一致）', () => {
    const rows = Array.from({ length: 20 }, (_, i) => ({
      id: `r${i}`,
      kind: 'analysis',
      verdict: i === 3 ? 'bogus' : 'fulfilled',
      missing: ['a', 'b', 'c', 'd', 'e'],
      next_action: 'x'.repeat(200),
    }));
    const view = parseGoalSatisfaction(makePayload({ requirements: rows }));
    expect(view!.requirements.length).toBeLessThanOrEqual(12); // MAX_ROWS
    expect(view!.requirements.find((r) => r.id === 'r3')).toBeUndefined();
    expect(view!.requirements[0].missing.length).toBeLessThanOrEqual(3);
    expect(view!.requirements[0].next_action.length).toBeLessThanOrEqual(64);
  });

  it('counts 非数值被丢弃', () => {
    const view = parseGoalSatisfaction(
      makePayload({ counts: { partial: 1, bogus: 'x', neg: -2.5 } }),
    );
    expect(view!.counts).toEqual({ partial: 1, neg: -2.5 });
  });
});

describe('attachGoalSatisfaction（message-reducer）', () => {
  const base: ChatStreamMessage = {
    id: 'm1',
    role: 'assistant',
    content: '',
    timestamp: null,
  };
  const view = parseGoalSatisfaction(makePayload())!;

  it('挂到目标消息；目标不存在时原样返回（引用恒定）', () => {
    const withMsg = [base, { ...base, id: 'm2' }];
    const out = attachGoalSatisfaction(withMsg, { messageId: 'm2', goal: view });
    expect(out[1].goalSatisfaction).toBe(view);
    expect(out[0]).toBe(withMsg[0]);
    // 目标缺席 → prev 原样
    expect(attachGoalSatisfaction(withMsg, { messageId: 'nope', goal: view }))
      .toBe(withMsg);
  });

  it('新 verdict 覆盖旧卡（每轮终态一份）', () => {
    const updated = { ...view, verdict: 'failed' as const };
    const withCard = attachGoalSatisfaction([base], { messageId: 'm1', goal: view });
    const out = attachGoalSatisfaction(withCard, { messageId: 'm1', goal: updated });
    expect(out[0].goalSatisfaction!.verdict).toBe('failed');
  });
});
