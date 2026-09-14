/**
 * Widget two-way binding（ADR-0194 §4.2-5）：用户与生成式微 UI 卡片的
 * 交互 → widget_reply action → 同一画布动作通道回流 Agent + 本地 onReply。
 */
import {
  buildCanvasAction,
  buildEnvelope,
  reportAffordance,
  type WidgetSpec,
} from './affordance';

export interface WidgetReplyResult {
  latencyMs: number;
  dispatched: boolean;
}

/**
 * 构造 widget_reply action（裁决回流契约：meta.widget_reply =
 * {widget_id, kind, value}，spec §1.2 / §3.2）。
 */
export function widgetReplyAction(
  widget: Pick<WidgetSpec, 'widget_id' | 'kind'>,
  value: unknown,
) {
  return buildCanvasAction('widget_reply', {
    meta: {
      widget_reply: { widget_id: widget.widget_id, kind: widget.kind, value },
    },
  });
}

/**
 * 发送一次 widget 裁决：fire-and-forget 上报 + 同步本地回调（双向绑定的
 * 「回传 Agent、更新本地」两半）。
 */
export function sendWidgetReply(
  widget: Pick<WidgetSpec, 'widget_id' | 'kind'>,
  value: unknown,
  onReply?: (value: unknown) => void,
): WidgetReplyResult {
  const envelope = buildEnvelope([widgetReplyAction(widget, value)]);
  const result = reportAffordance(envelope);
  onReply?.(value);
  return result;
}
