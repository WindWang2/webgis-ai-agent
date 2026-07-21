'use client';

import { useState, useRef, useEffect, useCallback, type KeyboardEvent } from 'react';
import { Upload, Send, Sparkles, CheckCircle2 } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import type { AiStatus } from '@/lib/store/hud-types';
import MiniMd from '@/components/chat/mini-md';
import { ToolCallChain } from '@/components/chat/tool-call-card';
import { CollapsibleThink } from '@/components/chat/collapsible-think';
import { PlanProposalCard } from '@/components/chat/plan-proposal-card';
import { PlanCard } from '@/components/chat/plan-card';
import { ChartRenderer, adaptChartData } from '@/components/chat/chart-renderer';

/* ─── Thinking dots animation ─── */
const DOT_ANIMS = ['animate-dot-1', 'animate-dot-2', 'animate-dot-3'];

function ThinkingDots({ text, accentColor, isDark }: { text: string; accentColor: string; isDark: boolean }) {
  return (
    <div className="flex items-center gap-2 py-1.5 px-1">
      <div className="flex gap-[3px]">
        {DOT_ANIMS.map((anim) => (
          <span
            key={anim}
            style={{
              display: 'block', width: 5, height: 5, borderRadius: '50%',
              backgroundColor: accentColor
            }}
            className={anim}
          />
        ))}
      </div>
      <span className="text-[15px]" style={{ color: isDark ? '#64748b' : '#94a3b8' }}>{text}</span>
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

function SuggestedPromptButtons({ onSend, accentColor, isDark }: { onSend: (text: string) => void; accentColor: string; isDark: boolean }) {
  return (
    <div className="px-3 pt-3 pb-2">
      <p className="text-[14px] uppercase tracking-wider mb-2" style={{ color: isDark ? '#64748b' : '#94a3b8' }}>快捷指令</p>
      <div className="flex flex-wrap gap-1.5">
        {SUGGESTED_PROMPTS.map((prompt) => (
          <button
            key={prompt}
            onClick={() => onSend(prompt)}
            style={{
              padding: '6px 10px', borderRadius: 8, fontSize: 13,
              color: isDark ? '#e2e8f0' : '#475569',
              borderWidth: 1, borderStyle: 'solid',
              borderColor: `${accentColor}22`,
              backgroundColor: isDark ? 'rgba(30,41,59,0.6)' : 'rgba(255,255,255,0.6)',
              cursor: 'pointer', transition: 'background-color 0.15s'
            }}
            onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = isDark ? 'rgba(30,41,59,0.9)' : 'rgba(255,255,255,0.9)'; }}
            onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = isDark ? 'rgba(30,41,59,0.6)' : 'rgba(255,255,255,0.6)'; }}
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ─── Props ─── */
interface ChatTabProps {
  messages: Array<{
    id: string;
    role: 'user' | 'assistant';
    content: string;
    timestamp: Date | number | null;
    isThinking?: boolean;
    charts?: unknown[];
    toolCalls?: import('@/lib/store/hud-types').ToolCallEntry[];
    plan?: import('@/lib/store/hud-types').PlanProposalPayload;
    agentPlan?: import('@/lib/types/agent-plan').AgentPlanState;
    layerAdded?: string;
  }>;
  aiStatus: AiStatus;
  onSend: (text: string) => void;
  accentColor: string;
  /** Plan Mode: 用户在卡片上点按钮时回调，由父组件发送对应 chat 消息并更新 plan.status */
  onPlanAction?: (planId: string, action: 'approve' | 'revise' | 'reject') => void;
}

export function ChatTab({ messages, aiStatus, onSend, accentColor, onPlanAction }: ChatTabProps) {
  const theme = useHudStore((s) => s.theme);
  const isDark = theme === 'dark';
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
          <div className="flex flex-col items-center justify-center h-full px-6 text-center">
            <div
              className="w-12 h-12 rounded-2xl flex items-center justify-center mb-3"
              style={{ backgroundColor: `${accentColor}15` }}
            >
              <Sparkles size={22} style={{ color: accentColor }} />
            </div>
            <h3 className="text-[15px] font-semibold mb-1" style={{ color: isDark ? '#e2e8f0' : '#1e293b' }}>GeoAgent</h3>
            <p className="text-[13.5px] leading-relaxed" style={{ color: isDark ? '#64748b' : '#94a3b8' }}>
              输入空间分析指令，开始智能 GIS 分析
            </p>
          </div>
        )}

        {/* Message list */}
        <div className="px-3 py-3 space-y-3">
          {messages.map((msg: any, idx: number) => {
            const isUser = msg.role === 'user';
            const time = (mounted && msg.timestamp)
              ? new Date(msg.timestamp).toLocaleTimeString('zh-CN', {
                  hour: '2-digit',
                  minute: '2-digit',
                })
              : '';

            return isUser ? (
              /* ── User message: right-aligned bubble ── */
              <div key={msg.id ?? idx} className="flex justify-end">
                <div className="max-w-[85%]">
                  <div className="flex items-center justify-end gap-1.5 mb-0.5">
                    {time && <span className="text-[15px]" style={{ color: isDark ? '#475569' : '#cbd5e1' }}>{time}</span>}
                    <span className="text-[14px] font-semibold" style={{ color: accentColor }}>You</span>
                  </div>
                  <div
                    style={{
                      borderTopRightRadius: 4, borderTopLeftRadius: 16,
                      borderBottomLeftRadius: 16, borderBottomRightRadius: 16,
                      padding: '8px 12px', fontSize: 14.5, lineHeight: 1.6, color: '#fff',
                      backgroundColor: accentColor
                    }}
                  >
                    <div className="whitespace-pre-wrap">{msg.content}</div>
                  </div>
                </div>
              </div>
            ) : (
              /* ── Assistant message: left-aligned with avatar ── */
              <div key={msg.id ?? idx} className="flex gap-2">
                <div className="shrink-0 mt-0.5">
                  <div
                    className="w-6 h-6 rounded-full flex items-center justify-center"
                    style={{ backgroundColor: `${accentColor}15` }}
                  >
                    <Sparkles size={11} style={{ color: accentColor }} />
                  </div>
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-[14px] font-semibold" style={{ color: accentColor }}>GeoAgent</span>
                    {time && <span className="text-[15px]" style={{ color: isDark ? '#475569' : '#cbd5e1' }}>{time}</span>}
                  </div>

                  {msg.layerAdded && (
                    <div
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 8px',
                        borderRadius: 999, fontSize: 12, fontWeight: 500, color: '#fff',
                        backgroundColor: accentColor, marginBottom: 6
                      }}
                    >
                      <CheckCircle2 size={10} />
                      感知图层已挂载：{msg.layerAdded}
                    </div>
                  )}

                  {msg.isThinking ? (
                    <ThinkingDots text={thinkingText} accentColor={accentColor} isDark={isDark} />
                  ) : msg.content || msg.think || msg.toolCalls?.length ? (
                    <div style={{
                      borderTopLeftRadius: 4, borderTopRightRadius: 16,
                      borderBottomLeftRadius: 16, borderBottomRightRadius: 16,
                      backgroundColor: isDark ? 'rgba(30,41,59,0.8)' : 'rgba(248,250,252,0.8)',
                      borderWidth: 1, borderStyle: 'solid',
                      borderColor: isDark ? 'rgba(148,163,184,0.2)' : 'rgba(226,232,240,0.8)',
                      padding: '8px 12px'
                    }}>
                      {msg.think && (
                        <CollapsibleThink
                          content={msg.think}
                          isDark={isDark}
                          accentColor={accentColor}
                        />
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
                          isDark={isDark}
                          accentColor={accentColor}
                          onApprove={(pid) => onPlanAction?.(pid, 'approve')}
                          onRevise={(pid) => onPlanAction?.(pid, 'revise')}
                          onReject={(pid) => onPlanAction?.(pid, 'reject')}
                        />
                      )}
                      {msg.charts?.map((raw: unknown, idx: number) => {
                        const chart = adaptChartData(raw);
                        if (!chart) return null;
                        return (
                          <div key={`chart-${idx}`} style={{ marginTop: 8, borderRadius: 8, overflow: 'hidden', border: `1px solid ${accentColor}22`, backgroundColor: isDark ? 'rgba(15,23,42,0.6)' : 'rgba(248,250,252,0.8)' }}>
                            <ChartRenderer chart={chart} />
                          </div>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              </div>
            );
          })}

          {/* Thinking indicator at end of messages */}
          {isBusy && messages.length > 0 && !messages[messages.length - 1]?.isThinking && (
            <ThinkingDots text={thinkingText} accentColor={accentColor} isDark={isDark} />
          )}
        </div>

        {/* Suggested prompts when not busy and few messages */}
        {!isBusy && messages.length <= 1 && (
          <SuggestedPromptButtons onSend={onSend} accentColor={accentColor} isDark={isDark} />
        )}
      </div>

      {/* Input area */}
      <div style={{
        borderTopWidth: 1, borderTopStyle: 'solid',
        borderTopColor: isDark ? 'rgba(148,163,184,0.2)' : 'rgba(226,232,240,0.6)',
        backgroundColor: isDark ? 'rgba(15,23,42,0.6)' : 'rgba(255,255,255,0.6)',
        backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)'
      }} className="shrink-0">
        <div className="flex items-end gap-2 px-3 pt-2.5 pb-1.5">
          {/* Upload button */}
          <button
            aria-label="上传文件"
            style={{
              flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
              width: 24, height: 24, borderRadius: 6, color: isDark ? '#64748b' : '#94a3b8',
              backgroundColor: 'transparent', cursor: 'pointer'
            }}
            onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = isDark ? 'rgba(148,163,184,0.15)' : 'rgba(226,232,240,0.6)'; e.currentTarget.style.color = isDark ? '#e2e8f0' : '#475569'; }}
            onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; e.currentTarget.style.color = isDark ? '#64748b' : '#94a3b8'; }}
            title="上传文件"
          >
            <Upload size={14} />
          </button>

          {/* Textarea */}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入空间分析指令..."
            rows={1}
            style={{
              flex: 1, resize: 'none', backgroundColor: 'transparent',
              fontSize: 14.5, color: isDark ? '#e2e8f0' : '#1e293b',
              outline: 'none', lineHeight: 1.5, maxHeight: 80, paddingTop: 4, paddingBottom: 4
            }}
          />

          {/* Send button */}
          <button
            onClick={handleSend}
            disabled={!input.trim() || isBusy}
            aria-label="发送消息"
            style={{
              flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
              width: 26, height: 26, borderRadius: 8, transition: 'opacity 0.15s',
              opacity: !input.trim() || isBusy ? 0.4 : 1,
              backgroundColor: input.trim() ? accentColor : isDark ? '#334155' : '#e2e8f0',
              color: input.trim() ? '#fff' : isDark ? '#64748b' : '#94a3b8',
              cursor: 'pointer'
            }}
          >
            <Send size={13} />
          </button>
        </div>

        {/* Hint */}
        <div className="px-3 pb-2">
          <span className="text-[9.5px]" style={{ color: isDark ? '#475569' : '#cbd5e1' }}>
            Enter 发送 · Shift+Enter 换行
          </span>
        </div>
      </div>
    </div>
  );
}

export default ChatTab;
