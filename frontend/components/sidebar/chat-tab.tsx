'use client';

import { memo, useState, useRef, useEffect, useCallback, type KeyboardEvent } from 'react';
import dynamic from 'next/dynamic';
import { Send, Sparkles, CheckCircle2 } from 'lucide-react';
import type { AiStatus } from '@/lib/store/hud-types';
import { ToolCallChain } from '@/components/chat/tool-call-card';
import { CollapsibleThink } from '@/components/chat/collapsible-think';
import { PlanProposalCard } from '@/components/chat/plan-proposal-card';
import { PlanCard } from '@/components/chat/plan-card';
import { InlineNotice } from '@/components/shared/inline-notice';
import { ChatAnnouncer } from '@/components/chat/chat-announcer';
import { adaptChartData } from "@/lib/chart-adapter";

// Bundle-slimming: react-markdown (MiniMd) and recharts (ChartRenderer) load on
// demand instead of riding the / first-load bundle; the tiny pure adapter stays
// static so chart message rendering logic is unchanged.
const MiniMd = dynamic(() => import('@/components/chat/mini-md'), { ssr: false });
const ChartRenderer = dynamic(
  () => import('@/components/chat/chart-renderer').then((m) => ({ default: m.ChartRenderer })),
  { ssr: false }
);

/* ─── Thinking dots animation ─── */
const DOT_ANIMS = ['animate-dot-1', 'animate-dot-2', 'animate-dot-3'];

function ThinkingDots({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 py-1.5 px-1">
      <div className="flex gap-[3px]">
        {DOT_ANIMS.map((anim) => (
          <span
            key={anim}
            style={{
              display: 'block', width: 5, height: 5, borderRadius: '50%',
              backgroundColor: 'var(--agent-accent)'
            }}
            className={anim}
          />
        ))}
      </div>
      <span className="text-body text-ink-muted">{text}</span>
    </div>
  );
}

/* ─── Suggested prompts ─── */
const SUGGESTED_PROMPTS = [
  '分析该区域的 POI 分布',
  '生成缓冲区分析',
  '计算人口密度热力图',
  '叠加分析两个图层',
];

function SuggestedPromptButtons({ onSend }: { onSend: (text: string) => void }) {
  return (
    <div className="px-3 pt-3 pb-2">
      {/* A: 快捷指令头是 14px uppercase 标签 —— 走 V4 的 title 档 + ink-muted，
          不再用裸 text-[14px] 与 --theme-* 双轨。 */}
      <p className="text-title uppercase tracking-wider text-ink-muted mb-2">快捷指令</p>
      <div className="flex flex-wrap gap-1.5">
        {SUGGESTED_PROMPTS.map((prompt) => (
          <button
            key={prompt}
            onClick={() => onSend(prompt)}
            className="cursor-pointer rounded-md border bg-surface-raised px-2.5 py-1.5 text-body text-ink transition-colors"
            style={{ borderColor: 'var(--accent-border)' }}
            onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'var(--surface-hover)'; }}
            onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'var(--surface-raised)'; }}
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ─── Props ─── */
interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  think?: string;
  timestamp: Date | number | null;
  isThinking?: boolean;
  charts?: unknown[];
  toolCalls?: import('@/lib/store/hud-types').ToolCallEntry[];
  plan?: import('@/lib/store/hud-types').PlanProposalPayload;
  agentPlan?: import('@/lib/types/agent-plan').AgentPlanState;
  layerAdded?: string;
}

interface ChatTabProps {
  messages: ChatMessage[];
  aiStatus: AiStatus;
  onSend: (text: string) => void;
  /** Plan Mode: 用户在卡片上点按钮时回调，由父组件发送对应 chat 消息并更新 plan.status */
  onPlanAction?: (planId: string, action: 'approve' | 'revise' | 'reject') => void;
}

/* ─── Memoized message bubble ───
 * D-F8: `messages` is page-level state, so every SSE token batch re-renders
 * the whole app. use-sse-stream replaces ONLY the streaming message object
 * per batch (prior messages keep their identity). Without a memo boundary
 * here, every batch re-rendered ALL messages and re-parsed every MiniMd /
 * react-markdown body: O(messages × batches) parses per turn. React.memo with
 * stable props keeps unchanged messages from re-rendering at all.
 */
const ChatMessageItem = memo(function ChatMessageItem({
  message: msg,
  mounted,
  thinkingText,
  onPlanAction,
}: {
  message: ChatMessage;
  mounted: boolean;
  thinkingText: string;
  onPlanAction?: (planId: string, action: 'approve' | 'revise' | 'reject') => void;
}) {
  const isUser = msg.role === 'user';
  const time = (mounted && msg.timestamp)
    ? new Date(msg.timestamp).toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
      })
    : '';

  return isUser ? (
    /* ── User message: right-aligned bubble ── */
    <div className="flex justify-end">
      <div className="max-w-[85%]">
        <div className="flex items-center justify-end gap-1.5 mb-0.5">
          {/* A: 时间戳是真实正文，--theme-text-subtle（= --text-disabled, 3.5:1）
             只该用于禁用/装饰 —— 改 ink-muted。 */}
          {time && <span className="text-body text-ink-muted">{time}</span>}
          {/* 运行时 accent 直接作文字在暗色下只有 2.96–3.40:1 —— 角色标签改用
              主题校正后的 --agent-accent。 */}
          <span className="text-title font-semibold text-agent-accent">You</span>
        </div>
        <div
          style={{
            borderTopRightRadius: 4, borderTopLeftRadius: 16,
            borderBottomLeftRadius: 16, borderBottomRightRadius: 16,
            padding: '8px 12px', fontSize: 14.5, lineHeight: 1.6, color: 'var(--text-on-accent)',
            backgroundColor: 'var(--agent-accent)'
          }}
        >
          <div className="whitespace-pre-wrap">{msg.content}</div>
        </div>
      </div>
    </div>
  ) : (
    /* ── Assistant message: left-aligned with avatar ── */
    <div className="flex gap-2">
      <div className="shrink-0 mt-0.5">
        <div
          className="w-6 h-6 rounded-full flex items-center justify-center"
          style={{ backgroundColor: 'color-mix(in srgb, var(--agent-accent) 8%, transparent)' }}
        >
          <Sparkles size={11} style={{ color: 'var(--agent-accent)' }} />
        </div>
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-title font-semibold text-agent-accent">GeoAgent</span>
          {time && <span className="text-body text-ink-muted">{time}</span>}
        </div>

        {msg.layerAdded && (
          <div
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 8px',
              borderRadius: 999, fontSize: 12, fontWeight: 500, color: 'var(--text-on-accent)',
              backgroundColor: 'var(--agent-accent)', marginBottom: 6
            }}
          >
            <CheckCircle2 size={10} />
            感知图层已挂载：{msg.layerAdded}
          </div>
        )}

        {msg.isThinking ? (
          <ThinkingDots text={thinkingText} />
        ) : msg.content || msg.think || msg.toolCalls?.length ? (
          <div style={{
            borderTopLeftRadius: 4, borderTopRightRadius: 16,
            borderBottomLeftRadius: 16, borderBottomRightRadius: 16,
            backgroundColor: 'var(--surface-raised)',
            borderWidth: 1, borderStyle: 'solid',
            borderColor: 'var(--border-subtle)',
            padding: '8px 12px'
          }}>
            {msg.think && (
              <CollapsibleThink content={msg.think} />
            )}
            {msg.agentPlan && (
              <PlanCard plan={msg.agentPlan} />
            )}
            {msg.content && <MiniMd text={msg.content} />}
            {msg.toolCalls && msg.toolCalls.length > 0 && (
              <ToolCallChain calls={msg.toolCalls} />
            )}
            {msg.plan && (
              <PlanProposalCard
                planId={msg.plan.plan_id}
                title={msg.plan.title}
                summary={msg.plan.summary}
                stepCount={msg.plan.step_count}
                destructiveSteps={msg.plan.destructive_steps}
                stepsPreview={msg.plan.steps_preview}
                status={msg.plan.status}
                onApprove={(pid) => onPlanAction?.(pid, 'approve')}
                onRevise={(pid) => onPlanAction?.(pid, 'revise')}
                onReject={(pid) => onPlanAction?.(pid, 'reject')}
              />
            )}
            {msg.charts?.map((raw: unknown, idx: number) => {
              const chart = adaptChartData(raw);
              if (!chart) return null;
              return (
                <div key={`chart-${idx}`} className="mt-2 overflow-hidden rounded-md border bg-surface-sunken" style={{ borderColor: 'var(--accent-border)' }}>
                  <ChartRenderer chart={chart} />
                </div>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
});

export function ChatTab({ messages, aiStatus, onSend, onPlanAction }: ChatTabProps) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isBusy = aiStatus === 'thinking' || aiStatus === 'acting';

  const scrollToBottom = useCallback(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, aiStatus, scrollToBottom]);

  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 80)}px`;
  }, [input]);

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || isBusy) return;
    onSend(trimmed);
    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [input, isBusy, onSend]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  const thinkingText =
    aiStatus === 'thinking'
      ? '正在分析指令...'
      : aiStatus === 'acting'
        ? '正在执行空间操作...'
        : '';

  return (
    <div className="flex flex-col h-full">
      {/* Messages scroll area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {messages.length === 0 && !isBusy && (
          <div className="flex h-full flex-col items-center justify-center px-6 text-center">
            <span aria-hidden className="mb-3 flex h-control-lg w-control-lg items-center justify-center rounded-md bg-status-accent-soft">
              <Sparkles size={16} className="text-status-accent" />
            </span>
            <h3 className="mb-1 text-title font-semibold text-ink">GeoAgent</h3>
            <p className="text-meta text-ink-muted">输入空间分析指令，开始智能 GIS 分析</p>
          </div>
        )}

        {/*
          a11y：流式回复此前完全没有播报 —— 助手 token、工具调用状态、
          「正在分析指令…」全都只是视觉变化，读屏用户感知不到产品的主反馈回路。
          播报交给 <ChatAnnouncer/>（下方 composer 区）：把 aria-live 直接放在
          消息列表上会在每个 token 批次重读整段回答（MiniMd 每批重新解析、整个
          气泡子树被替换，aria-atomic=false 只有在追加叶子节点时才是增量）。
        */}
        <div className="space-y-2 px-panel py-2">
          {messages.map((msg, idx) => (
            <ChatMessageItem
              key={msg.id ?? `msg-${idx}`}
              message={msg}
              mounted={mounted}
              thinkingText={thinkingText}
              onPlanAction={onPlanAction}
            />
          ))}


          {/* Thinking indicator at end of messages */}
          {isBusy && messages.length > 0 && !messages[messages.length - 1]?.isThinking && (
            <ThinkingDots text={thinkingText} />
          )}
        </div>

        {/* Suggested prompts when not busy and few messages */}
        {!isBusy && messages.length <= 1 && (
          <SuggestedPromptButtons onSend={onSend} />
        )}
      </div>

      {/* Input area */}
      <ChatAnnouncer messages={messages} aiStatus={aiStatus} />

      {/* 不透明底 + 去掉 blur(12px)：composer 压在地图上，半透明会让输入文字
          与底图细节互相干扰，而 backdrop-filter 又是最贵的那类合成。 */}
      <div className="shrink-0 border-t border-edge-subtle bg-surface-panel">
        {/* UI V3：AI 错误在 chat 内可见（之前仅 top-bar/HUD 有指示，
            composer 静默恢复可用，用户无法感知失败） */}
        {aiStatus === 'error' && (
          <div className="px-panel pt-2">
            <InlineNotice variant="error">上一条指令执行失败，请调整后重试。</InlineNotice>
          </div>
        )}
        <div className="flex items-end gap-2 px-panel pb-1 pt-2">
          {/* Textarea */}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            aria-label="输入空间分析指令"
            placeholder="输入空间分析指令..."
            rows={1}
            /* a11y 修复：这里原本是内联 `outline: 'none'`。内联样式压过
               globals.css 里那条 unlayered 的 *:focus-visible 规则，于是产品最
               核心的输入框是全站唯一没有键盘焦点环的控件。改成 focus-visible
               时用 ring 表达，鼠标点击不显示。 */
            className="max-h-20 flex-1 resize-none bg-transparent py-1 text-body leading-normal text-ink placeholder:text-ink-disabled focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-status-accent-border"
          />

          {/* Send button */}
          <button
            onClick={handleSend}
            disabled={!input.trim() || isBusy}
            aria-label="发送消息"
            className={`flex h-control-md w-control-md shrink-0 items-center justify-center rounded-sm transition-colors ${
              input.trim() && !isBusy
                ? 'bg-status-accent text-ink-on-accent'
                : 'cursor-not-allowed bg-surface-sunken text-ink-disabled'
            }`}
          >
            <Send size={13} aria-hidden />
          </button>
        </div>

        {/* Hint */}
        <div className="px-panel pb-1.5">
          <span className="text-micro text-ink-muted">Enter 发送 · Shift+Enter 换行</span>
        </div>
      </div>
    </div>
  );
}

export default ChatTab;
