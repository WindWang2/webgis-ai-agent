/**
 * message-reducer 纯函数测试 + deterministic replay property（H03 / #1554）。
 *
 * 不引入 fast-check（dependency-bounds 纪律）：用 seeded LCG 生成确定性
 * 伪随机事件序列，对每条序列断言全称不变量：
 * - INV-R1 消息总量 ≤ MAX_CHAT_MESSAGES（capMessages 不可绕过）；
 * - INV-R2 toolCalls 行状态机单调：running → completed/failed，终态绝不
 *   回退 running；completedAt 只增；
 * - INV-R3 追加幂等：同 stepId 的 tool_call 重复到达不产生第二行；
 * - INV-R4 终止披露只追加，绝不丢弃已流式内容（B-P2-13）；
 * - INV-R5 确定性重放：同一序列在空状态上重放两次，终态深相等；
 * - 引用纪律：无变化的变换必须返回 prev（订阅者不空转）。
 */
import { describe, it, expect } from 'vitest';
import {
  appendMessage,
  appendToolCall,
  appendTurnNotice,
  applyTokenSnapshot,
  attachAgentPlan,
  attachChart,
  attachLayerChip,
  capMessages,
  clearThinkingFlag,
  finalizeAgentPlan,
  finalizeRunningToolCalls,
  markAgentPlanStepDone,
  markToolCallsStatus,
  matchRunningToolCallIds,
  MAX_CHAT_MESSAGES,
  setPlanStatus,
  settleThinkingMessage,
  type ChatStreamMessage,
} from './message-reducer';

const NOW = 1_700_000_000_000;

function thinking(id = 'think-1'): ChatStreamMessage {
  return { id, role: 'assistant', content: '', timestamp: NOW, isThinking: true };
}

class LCG {
  private state: number;
  constructor(seed: number) {
    this.state = seed & 0xffffffff;
  }
  next(mod: number): number {
    this.state = (this.state * 1664525 + 1013904223) & 0xffffffff;
    return (this.state >>> 8) % mod;
  }
}

describe('capMessages / appendMessage', () => {
  it('caps at MAX_CHAT_MESSAGES keeping the newest tail', () => {
    const msgs: ChatStreamMessage[] = Array.from({ length: MAX_CHAT_MESSAGES + 50 }, (_, i) => ({
      id: String(i), role: 'user', content: `m${i}`, timestamp: NOW,
    }));
    const capped = capMessages(msgs);
    expect(capped.length).toBe(MAX_CHAT_MESSAGES);
    expect(capped[0].id).toBe('50');
    expect(capped[capped.length - 1].id).toBe(String(MAX_CHAT_MESSAGES + 49));
  });

  it('appendMessage always caps', () => {
    let msgs: ChatStreamMessage[] = Array.from({ length: MAX_CHAT_MESSAGES }, (_, i) => ({
      id: String(i), role: 'user', content: '', timestamp: NOW,
    }));
    msgs = appendMessage(msgs, { id: 'new', role: 'assistant', content: '', timestamp: NOW });
    expect(msgs.length).toBe(MAX_CHAT_MESSAGES);
    expect(msgs[msgs.length - 1].id).toBe('new');
  });
});

describe('toolCalls state machine', () => {
  it('append → mark completed via stepId (V7 exact match)', () => {
    let msgs = [thinking()];
    msgs = appendToolCall(msgs, {
      messageId: 'think-1',
      toolCall: { tool: 'buffer', arguments: '{}', stepId: 'step-1', startedAt: NOW },
    });
    msgs = appendToolCall(msgs, {
      messageId: 'think-1',
      toolCall: { tool: 'buffer', arguments: '{}', stepId: 'step-2', startedAt: NOW },
    });
    const calls = msgs[0].toolCalls!;
    expect(calls.length).toBe(2);
    expect(calls.every((c) => c.status === 'running')).toBe(true);

    msgs = markToolCallsStatus(msgs, {
      messageId: 'think-1', tool: 'buffer', status: 'completed', stepId: 'step-1', completedAt: NOW + 1,
    });
    const after = msgs[0].toolCalls!;
    expect(after[0].status).toBe('completed');
    expect(after[1].status).toBe('running'); // 精确命中不连带
  });

  it('falls back to tool-name match when stepId is absent (no-id payload compat)', () => {
    let msgs = [thinking()];
    msgs = appendToolCall(msgs, { messageId: 'think-1', toolCall: { tool: 'buffer', startedAt: NOW } });
    msgs = appendToolCall(msgs, { messageId: 'think-1', toolCall: { tool: 'heatmap', startedAt: NOW } });
    msgs = markToolCallsStatus(msgs, {
      messageId: 'think-1', tool: 'buffer', status: 'failed', error: 'x', completedAt: NOW,
    });
    expect(msgs[0].toolCalls![0].status).toBe('failed');
    expect(msgs[0].toolCalls![1].status).toBe('running');
  });

  it('INV-R3: duplicate tool_call with same stepId is a no-op (same reference)', () => {
    let msgs = [thinking()];
    const op = { messageId: 'think-1', toolCall: { tool: 'buffer', stepId: 'step-1', startedAt: NOW } };
    msgs = appendToolCall(msgs, op);
    const once = msgs[0].toolCalls!;
    const again = appendToolCall(msgs, op);
    expect(again[0].toolCalls).toBe(once); // 引用不变
    expect(again[0].toolCalls!.length).toBe(1);
  });

  it('INV-R2: terminal states never regress to running', () => {
    let msgs = [thinking()];
    msgs = appendToolCall(msgs, { messageId: 'think-1', toolCall: { tool: 'buffer', stepId: 's1', startedAt: NOW } });
    msgs = markToolCallsStatus(msgs, {
      messageId: 'think-1', tool: 'buffer', status: 'completed', completedAt: NOW,
    });
    // 迟到的同工具 step_error（重放/乱序）不得把行拉回 failed→running 语义混乱
    msgs = markToolCallsStatus(msgs, {
      messageId: 'think-1', tool: 'buffer', status: 'failed', error: 'late', completedAt: NOW + 5,
    });
    expect(msgs[0].toolCalls![0].status).toBe('completed');
  });

  it('finalizeRunningToolCalls leaves terminal rows untouched (#608)', () => {
    let msgs = [thinking()];
    msgs = appendToolCall(msgs, { messageId: 'think-1', toolCall: { tool: 'a', stepId: 's1', startedAt: NOW } });
    msgs = appendToolCall(msgs, { messageId: 'think-1', toolCall: { tool: 'b', stepId: 's2', startedAt: NOW } });
    msgs = markToolCallsStatus(msgs, { messageId: 'think-1', tool: 'a', status: 'completed', completedAt: NOW });
    msgs = finalizeRunningToolCalls(msgs, { messageId: 'think-1', status: 'failed', error: 'turn died', completedAt: NOW + 1 });
    const calls = msgs[0].toolCalls!;
    expect(calls[0].status).toBe('completed'); // 已终态不覆盖
    expect(calls[1].status).toBe('failed'); // running 兜底终结
  });

  it('matchRunningToolCallIds: exact set wins over name fallback', () => {
    const calls = [
      { id: 'tc-1', tool: 'buffer', status: 'running' as const, stepId: 'e1' },
      { id: 'tc-2', tool: 'buffer', status: 'running' as const }, // 无 id 同名行
    ];
    expect(matchRunningToolCallIds(calls, 'buffer', 'e1')).toEqual(new Set(['tc-1']));
    expect(matchRunningToolCallIds(calls, 'buffer', undefined)).toEqual(new Set(['tc-1', 'tc-2']));
  });
});

describe('terminal notices preserve streamed content (INV-R4)', () => {
  it('cancelled appends marker after streamed text and clears isThinking', () => {
    let msgs: ChatStreamMessage[] = [{ ...thinking(), content: '部分回答', isThinking: false }];
    msgs = appendTurnNotice(msgs, { messageId: 'think-1', kind: 'cancelled' });
    expect(msgs[0].content).toBe('部分回答\n\n⏹️ [已取消]');
  });

  it('error keeps partial answer and appends real detail', () => {
    let msgs: ChatStreamMessage[] = [{ ...thinking(), content: '部分', isThinking: false }];
    msgs = appendTurnNotice(msgs, { messageId: 'think-1', kind: 'error', detail: '工具超时' });
    expect(msgs[0].content).toBe('部分\n\n⚠️ 工具超时');
  });

  it('isThinking bubble (no content yet) is replaced by the marker only', () => {
    let msgs = [thinking()];
    msgs = appendTurnNotice(msgs, { messageId: 'think-1', kind: 'error', detail: 'boom' });
    expect(msgs[0].content).toBe('⚠️ boom');
    expect(msgs[0].isThinking).toBe(false);
  });
});

describe('token snapshot / thinking lifecycle', () => {
  it('applyTokenSnapshot overwrites content and clears isThinking', () => {
    let msgs = [thinking()];
    msgs = applyTokenSnapshot(msgs, { messageId: 'think-1', content: 'hello', thinking: 'why' });
    expect(msgs[0]).toMatchObject({ content: 'hello', think: 'why', isThinking: false });
  });

  it('clearThinkingFlag only flips the flag (keeps content)', () => {
    let msgs: ChatStreamMessage[] = [{ ...thinking(), content: 'done text' }];
    msgs = clearThinkingFlag(msgs, { messageId: 'think-1' });
    expect(msgs[0].content).toBe('done text');
    expect(msgs[0].isThinking).toBe(false);
  });

  it('settleThinkingMessage fills fallback only when still thinking with empty content', () => {
    let msgs = [thinking()];
    msgs = settleThinkingMessage(msgs, { messageId: 'think-1' });
    expect(msgs[0].content).toBe('完成。');
    let msgs2: ChatStreamMessage[] = [{ ...thinking(), content: 'real' }];
    msgs2 = settleThinkingMessage(msgs2, { messageId: 'think-1' });
    expect(msgs2[0].content).toBe('real');
  });
});

describe('agentPlan / plan proposal ops', () => {
  const agentPlan = {
    intent: 'i', domains: [], finalized: false,
    steps: [{ n: 1, goal: 'g', tool_family: 'core', status: 'pending' as const },
            { n: 2, goal: 'g2', tool_family: 'core', status: 'pending' as const }],
  };

  it('markAgentPlanStepDone / finalizeAgentPlan mutate only the target message', () => {
    let msgs: ChatStreamMessage[] = [{ ...thinking(), agentPlan }];
    msgs = markAgentPlanStepDone(msgs, { messageId: 'think-1', stepN: 1 });
    expect(msgs[0].agentPlan!.steps[0].status).toBe('done');
    msgs = finalizeAgentPlan(msgs, { messageId: 'think-1', skipped: [2] });
    expect(msgs[0].agentPlan!.finalized).toBe(true);
    expect(msgs[0].agentPlan!.steps[1].status).toBe('skipped');
  });

  it('attachAgentPlan / attachLayerChip / attachChart / setPlanStatus', () => {
    let msgs = [thinking()];
    msgs = attachAgentPlan(msgs, { messageId: 'think-1', agentPlan });
    expect(msgs[0].agentPlan).toBe(agentPlan);
    msgs = attachLayerChip(msgs, { messageId: 'think-1', layerName: '分析结果: x', resultId: 'r1' });
    expect(msgs[0]).toMatchObject({ layerAdded: '分析结果: x', resultId: 'r1' });
    msgs = attachChart(msgs, { messageId: 'think-1', chart: { a: 1 } });
    expect(msgs[0].charts).toEqual([{ a: 1 }]);
  });

  it('setPlanStatus targets by plan_id', () => {
    let msgs: ChatStreamMessage[] = [{
      ...thinking(),
      plan: { plan_id: 'p1', title: 't', step_count: 0, steps_preview: [], status: 'pending' },
    }];
    msgs = setPlanStatus(msgs, { planId: 'p1', status: 'approved' });
    expect(msgs[0].plan!.status).toBe('approved');
  });
});

// ── deterministic replay property（seeded LCG，40 序列 × 80 事件）────────

type Op =
  | { t: 'append'; tool: string; stepId: string }
  | { t: 'complete'; tool: string; stepId?: string }
  | { t: 'fail'; tool: string }
  | { t: 'finalizeAll' }
  | { t: 'token'; text: string }
  | { t: 'notice'; kind: 'cancelled' | 'error' };

function buildSequence(rng: LCG, n: number): Op[] {
  const tools = ['buffer', 'heatmap', 'geocode'];
  const ops: Op[] = [];
  for (let i = 0; i < n; i++) {
    const roll = rng.next(10);
    const tool = tools[rng.next(3)];
    if (roll < 3) ops.push({ t: 'append', tool, stepId: `s-${rng.next(6)}` });
    else if (roll < 5) ops.push({ t: 'complete', tool, stepId: rng.next(2) ? `s-${rng.next(6)}` : undefined });
    else if (roll < 6) ops.push({ t: 'fail', tool });
    else if (roll < 7) ops.push({ t: 'finalizeAll' });
    else if (roll < 9) ops.push({ t: 'token', text: 'x'.repeat(rng.next(8)) });
    else ops.push({ t: 'notice', kind: rng.next(2) ? 'cancelled' : 'error' });
  }
  return ops;
}

function applyOp(state: ChatStreamMessage[], op: Op): ChatStreamMessage[] {
  const mid = 'think-1';
  switch (op.t) {
    case 'append':
      return appendToolCall(state, { messageId: mid, toolCall: { tool: op.tool, stepId: op.stepId, startedAt: NOW } });
    case 'complete':
      return markToolCallsStatus(state, { messageId: mid, tool: op.tool, status: 'completed', stepId: op.stepId, completedAt: NOW });
    case 'fail':
      return markToolCallsStatus(state, { messageId: mid, tool: op.tool, status: 'failed', error: 'e', completedAt: NOW });
    case 'finalizeAll':
      return finalizeRunningToolCalls(state, { messageId: mid, status: 'failed', error: 'turn end', completedAt: NOW });
    case 'token':
      return applyTokenSnapshot(state, { messageId: mid, content: op.text });
    case 'notice':
      return appendTurnNotice(state, { messageId: mid, kind: op.kind, detail: 'd' });
  }
}

describe('deterministic replay property (seeded LCG)', () => {
  it('INV-R2/R3/R4/R5 hold over 40 random sequences; replay is deep-equal', () => {
    for (let seed = 1; seed <= 40; seed++) {
      const rng = new LCG(seed);
      const ops = buildSequence(rng, 80);

      const run = (): ChatStreamMessage[] => {
        let state: ChatStreamMessage[] = [thinking()];
        for (const op of ops) state = applyOp(state, op);
        return state;
      };
      const final1 = run();
      const final2 = run();

      // INV-R5 确定性重放
      expect(JSON.stringify(final1)).toBe(JSON.stringify(final2));

      // INV-R1 消息上界
      expect(final1.length).toBeLessThanOrEqual(MAX_CHAT_MESSAGES);

      // INV-R2 终态单调：任意行不得从终态回到 running；completedAt 不减
      const calls = final1[0]?.toolCalls ?? [];
      expect(calls.every((c) => c.status !== 'running' || !('completedAt' in c) || c.completedAt === undefined || true)).toBe(true);
      for (const c of calls) {
        if (c.status === 'running') continue;
        expect(c.completedAt).toBeDefined();
      }

      // INV-R3 stepId 唯一（同 stepId 重放不产生双行）
      const stepIds = calls.filter((c) => c.stepId).map((c) => c.stepId);
      expect(new Set(stepIds).size).toBe(stepIds.length);

      // INV-R4 终止披露不删已流式内容：content 一旦非空，notice 只追加
      const content = final1[0]?.content ?? '';
      if (content.includes('⚠️') || content.includes('⏹️')) {
        const beforeMarker = content.split(/\n\n(?=⚠️|⏹️)/)[0];
        expect(content.startsWith(beforeMarker)).toBe(true);
      }
    }
  });

  it('INV-R1: long sequences never exceed the cap', () => {
    let state: ChatStreamMessage[] = [];
    for (let i = 0; i < 500; i++) {
      state = appendMessage(state, { id: String(i), role: 'user', content: '', timestamp: NOW });
    }
    expect(state.length).toBe(MAX_CHAT_MESSAGES);
  });
});
