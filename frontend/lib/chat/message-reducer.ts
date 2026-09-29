/**
 * Chat 流消息纯 reducer（H03 / #1554）。
 *
 * use-sse-stream 的消息变换此前全部内联在 onEvent 的 if/else 链里的
 * setMessages 闭包中（hook 1362 行的主要体量）。本模块把**消息数组的
 * 全部变换**抽为纯函数：`(prev, args) => next`，无 store/window/随机
 * 访问，时间戳一律由调用方传入 —— 可做 deterministic replay 测试
 * （乱序/重复/终态后迟到事件喂入，断言不变量）。
 *
 * 纪律：
 * - 每个函数在目标消息不存在或无变化时原样返回 `prev`（保持引用恒定，
 *   React/zustand 订阅者不空转）；
 * - 追加类变换内置 capMessages 上界（FE-P3-2，≤200 条）；
 * - 终态不回退：toolCalls 行一旦终态，后续同 id 事件不得改回 running。
 */
import type { ToolCallEntry, PlanProposalPayload } from '@/lib/store/hud-types';
import type { AgentPlanState } from '@/lib/types/agent-plan';

export interface ChatStreamMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date | number | null;
  isThinking?: boolean;
  think?: string;
  charts?: unknown[];
  toolCalls?: ToolCallEntry[];
  plan?: PlanProposalPayload;
  agentPlan?: AgentPlanState;
  layerAdded?: string;
  resultId?: string;
  [key: string]: unknown;
}

export type ToolCallStatus = 'completed' | 'failed';

/** FE-P3-2: 消息总量上界（与 results registry 的有界哲学一致）。 */
export const MAX_CHAT_MESSAGES = 200;

export function capMessages(messages: ChatStreamMessage[]): ChatStreamMessage[] {
  if (messages.length <= MAX_CHAT_MESSAGES) return messages;
  return messages.slice(messages.length - MAX_CHAT_MESSAGES);
}

/** 追加一条消息（唯一入口，强制 capMessages）。 */
export function appendMessage(
  prev: ChatStreamMessage[],
  msg: ChatStreamMessage,
): ChatStreamMessage[] {
  return capMessages([...prev, msg]);
}

function findIndexById(prev: ChatStreamMessage[], messageId: string): number {
  if (!messageId) return -1;
  return prev.findIndex((m) => m.id === messageId);
}

/**
 * TokenBatcher flush 快照应用：覆盖目标消息的 content/think，翻转
 * isThinking。目标不存在（会话已切换等）时原样返回。
 */
export function applyTokenSnapshot(
  prev: ChatStreamMessage[],
  args: { messageId: string; content: string; thinking?: string },
): ChatStreamMessage[] {
  const idx = findIndexById(prev, args.messageId);
  if (idx === -1) return prev;
  const target = prev[idx];
  const updated = [...prev];
  updated[idx] = {
    ...target,
    content: args.content,
    think: args.thinking || target.think,
    isThinking: false,
  };
  return updated;
}

export interface NewToolCall {
  tool: string;
  arguments?: string;
  stepId?: string;
  startedAt: number;
}

/**
 * 追加一条 running 工具行。幂等：载荷带 stepId 且该 stepId 已有行时
 * 跳过 —— 断线重连/重放（seenEventIds 之外的语义重复）不会产生双行。
 */
export function appendToolCall(
  prev: ChatStreamMessage[],
  args: { messageId: string; toolCall: NewToolCall },
): ChatStreamMessage[] {
  const { tool, arguments: toolArgs, stepId, startedAt } = args.toolCall;
  if (!tool) return prev;
  const idx = findIndexById(prev, args.messageId);
  if (idx === -1) return prev;
  const existing = prev[idx].toolCalls ?? [];
  if (stepId && existing.some((c) => c.stepId === stepId)) return prev;
  const next = [...existing, {
    id: `tc-${existing.length + 1}`,
    tool,
    arguments: typeof toolArgs === 'string' ? toolArgs : undefined,
    status: 'running' as const,
    startedAt,
    ...(stepId ? { stepId } : {}),
  }];
  const copy = [...prev];
  copy[idx] = { ...prev[idx], toolCalls: next };
  return copy;
}

/**
 * V7（review MAJOR-3）两段式匹配：stepId 在场且能精确命中时**只用**
 * 精确命中集；无精确命中才整体回落工具名匹配（无 id 载荷兼容）。
 * 只匹配 running 行（终态不回退）。
 */
export function matchRunningToolCallIds(
  calls: ToolCallEntry[],
  tool: string,
  stepId?: string,
): Set<string> {
  const running = calls.filter((c) => c.status === 'running');
  const exact = stepId ? running.filter((c) => c.stepId === stepId) : [];
  const matched = exact.length > 0
    ? exact
    : running.filter((c) => c.tool === tool);
  return new Set(matched.map((c) => c.id));
}

/** 终态迁移（step_result=completed / step_error·step_cancelled=failed）。 */
export function markToolCallsStatus(
  prev: ChatStreamMessage[],
  args: {
    messageId: string;
    tool: string;
    status: ToolCallStatus;
    error?: string;
    stepId?: string;
    extra?: Partial<ToolCallEntry>;
    completedAt: number;
  },
): ChatStreamMessage[] {
  const { messageId, tool, status, error, stepId, extra, completedAt } = args;
  if (!tool) return prev;
  const idx = findIndexById(prev, messageId);
  if (idx === -1) return prev;
  const calls = prev[idx].toolCalls;
  if (!calls || calls.length === 0) return prev;
  const matchedIds = matchRunningToolCallIds(calls, tool, stepId);
  if (matchedIds.size === 0) return prev;
  let changed = false;
  const next = calls.map((c) => {
    if (!matchedIds.has(c.id)) return c;
    changed = true;
    return {
      ...c,
      status,
      ...(status === 'failed' && error ? { error } : {}),
      ...(extra ?? {}),
      completedAt,
    };
  });
  if (!changed) return prev;
  const copy = [...prev];
  copy[idx] = { ...prev[idx], toolCalls: next };
  return copy;
}

/**
 * #608: 流级兜底 —— turn 死亡/收尾时仍未收到终态的 running 行一次性
 * 终结，spinner 不再永久旋转。已终态的行绝不覆盖。
 */
export function finalizeRunningToolCalls(
  prev: ChatStreamMessage[],
  args: { messageId: string; status: ToolCallStatus; error?: string; completedAt: number },
): ChatStreamMessage[] {
  const idx = findIndexById(prev, args.messageId);
  if (idx === -1) return prev;
  const calls = prev[idx].toolCalls;
  if (!calls || calls.length === 0) return prev;
  if (!calls.some((c) => c.status === 'running')) return prev;
  const next = calls.map((c) =>
    c.status === 'running'
      ? {
          ...c,
          status: args.status,
          ...(args.status === 'failed' && args.error ? { error: args.error } : {}),
          completedAt: args.completedAt,
        }
      : c,
  );
  const copy = [...prev];
  copy[idx] = { ...prev[idx], toolCalls: next };
  return copy;
}

/** Plan Mode：propose_plan 结果摘要挂到当前消息（PlanProposalCard 渲染）。 */
export function attachPlanProposal(
  prev: ChatStreamMessage[],
  args: { messageId: string; plan: PlanProposalPayload },
): ChatStreamMessage[] {
  return prev.map((m) => (m.id === args.messageId ? { ...m, plan: args.plan } : m));
}

/** 乐观 plan 状态迁移（handlePlanAction）与失败回滚共用。 */
export function setPlanStatus(
  prev: ChatStreamMessage[],
  args: { planId: string; status: PlanProposalPayload['status'] },
): ChatStreamMessage[] {
  return prev.map((m) =>
    m.plan?.plan_id === args.planId
      ? { ...m, plan: { ...m.plan, status: args.status } }
      : m,
  );
}

/** plan_ready：agentPlan 挂载（#615 恢复计划带 done 状态）。 */
export function attachAgentPlan(
  prev: ChatStreamMessage[],
  args: { messageId: string; agentPlan: AgentPlanState },
): ChatStreamMessage[] {
  return prev.map((m) => (m.id === args.messageId ? { ...m, agentPlan: args.agentPlan } : m));
}

/** plan_step_done：步骤打勾。 */
export function markAgentPlanStepDone(
  prev: ChatStreamMessage[],
  args: { messageId: string; stepN: number },
): ChatStreamMessage[] {
  return prev.map((m) => {
    if (m.id !== args.messageId || !m.agentPlan) return m;
    return {
      ...m,
      agentPlan: {
        ...m.agentPlan,
        steps: m.agentPlan.steps.map((s) =>
          s.n === args.stepN ? { ...s, status: 'done' as const } : s,
        ),
      },
    };
  });
}

/** plan_finalized：finalized 置位 + skipped 步骤标 skipped。 */
export function finalizeAgentPlan(
  prev: ChatStreamMessage[],
  args: { messageId: string; skipped: number[] },
): ChatStreamMessage[] {
  const skipped = new Set(args.skipped);
  return prev.map((m) => {
    if (m.id !== args.messageId || !m.agentPlan) return m;
    return {
      ...m,
      agentPlan: {
        ...m.agentPlan,
        finalized: true,
        steps: m.agentPlan.steps.map((s) =>
          skipped.has(s.n) ? { ...s, status: 'skipped' as const } : s,
        ),
      },
    };
  });
}

/** step_result 挂载的图层 chip + Result Workbench 深链 id。 */
export function attachLayerChip(
  prev: ChatStreamMessage[],
  args: { messageId: string; layerName: string; resultId?: string },
): ChatStreamMessage[] {
  return prev.map((m) =>
    m.id === args.messageId
      ? { ...m, layerAdded: args.layerName, resultId: args.resultId }
      : m,
  );
}

/** FE-P3-2: 有界 per-message chart 历史（≤20）。 */
export function attachChart(
  prev: ChatStreamMessage[],
  args: { messageId: string; chart: unknown; maxCharts?: number },
): ChatStreamMessage[] {
  const max = args.maxCharts ?? 20;
  return prev.map((m) =>
    m.id === args.messageId
      ? { ...m, charts: [...((m.charts as unknown[]) ?? []).slice(-(max - 1)), args.chart] }
      : m,
  );
}

/**
 * turn 级终止披露（task_cancelled / error / task_error）：保留已流式
 * 内容（B-P2-13），追加标记而不是整条替换。
 */
export function appendTurnNotice(
  prev: ChatStreamMessage[],
  args: { messageId: string; kind: 'cancelled' | 'error'; detail?: string },
): ChatStreamMessage[] {
  const marker = args.kind === 'cancelled' ? '⏹️ [已取消]' : `⚠️ ${args.detail ?? '请求失败，请重试。'}`;
  return prev.map((m) => {
    if (m.id !== args.messageId) return m;
    const existing = m.content && !m.isThinking ? m.content : '';
    return {
      ...m,
      content: existing ? `${existing}\n\n${marker}` : marker,
      isThinking: false,
    };
  });
}

/** done/task_complete：翻转 isThinking（#518 终态即翻，不等连接关闭）。 */
export function clearThinkingFlag(
  prev: ChatStreamMessage[],
  args: { messageId: string },
): ChatStreamMessage[] {
  return prev.map((m) =>
    m.id === args.messageId && m.isThinking ? { ...m, isThinking: false } : m,
  );
}

/**
 * handleSend 收尾兜底（连接正常关闭但终态事件未翻转时）：
 * isThinking 仍在才补「完成。」。
 */
export function settleThinkingMessage(
  prev: ChatStreamMessage[],
  args: { messageId: string; fallbackText?: string },
): ChatStreamMessage[] {
  return prev.map((m) =>
    m.id === args.messageId && m.isThinking
      ? { ...m, isThinking: false, content: m.content || args.fallbackText || '完成。' }
      : m,
  );
}
