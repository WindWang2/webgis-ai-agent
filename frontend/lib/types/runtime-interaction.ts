/**
 * G29：Pi 原生运行中交互卡片状态（types； adapters.runtimeInteractionCard 校验入站）。
 *
 * 与收口期 clarification（RequirementIR）不同：这是**运行中**原生 ctx.ui
 * dialog 的投影 —— 当前 agent run 正阻塞等待这一次回答；卡片提交经
 * runtime-interactions REST 面直达原生协议（不经 LLM）。
 */

export type RuntimeInteractionMethod = 'select' | 'confirm' | 'input' | 'editor';

/** 卡片状态机：open → submitting → answered|terminal；error 可重试。 */
export type RuntimeInteractionStatus =
  | 'open'
  | 'submitting'
  | 'answered'
  | 'cancelled'
  | 'timed_out'
  | 'invalidated'
  | 'resolved'
  | 'error';

export interface RuntimeInteractionCardState {
  interactionId: string;
  sessionId: string;
  turnId: string;
  method: RuntimeInteractionMethod;
  title: string;
  message?: string;
  options?: string[];
  placeholder?: string;
  prefill?: string;
  deadlineAt?: string | null;
  sourceTool?: string;
  sourceToolCallId?: string;
  envelopeId?: string;
  businessTarget?: string;
  /** answered 后回显（bounded）。 */
  answerDisplay?: string;
  answerKind?: string;
  resolveEvidence?: string;
  status: RuntimeInteractionStatus;
  /** 提交失败原因码（error 态显示）。 */
  errorCode?: string;
}

/** fire-and-forget 只读投影（pi_ui 事件；bounded）。 */
export interface RuntimeUiFireState {
  status: { key: string; text: string }[];
  widgets: { key: string; lines: string[]; placement: string }[];
  title: string;
  editorSuggestion: string;
  notifies: { message: string; notify_type: string; at: string }[];
}

/** 运行中追加指令的接纳回执（pi_turn_message 事件 / POST 响应）。 */
export interface TurnSteerEntry {
  turnId: string;
  mode: 'adjust' | 'queue';
  command: string;
  messageExcerpt?: string;
  disposition?: unknown;
  error?: string | null;
  accepted?: boolean;
}
