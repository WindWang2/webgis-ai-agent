'use client';

import { memo, useState, useRef, useEffect, useCallback, useMemo, type KeyboardEvent } from 'react';
import dynamic from 'next/dynamic';
import {
  ArrowUpRight, Blend, CheckCircle2, CircleDot, FolderOpen, Grid2X2, ImagePlus, MapPin,
  RotateCcw, Send, Sparkles, Square,
} from 'lucide-react';
import type { AiStatus } from '@/lib/store/hud-types';
import { useHudStore } from '@/lib/store/useHudStore';
import { ToolCallChain } from '@/components/chat/tool-call-card';
import { CollapsibleThink } from '@/components/chat/collapsible-think';
import { PlanProposalCard } from '@/components/chat/plan-proposal-card';
import { ClarificationCard } from '@/components/chat/clarification-card';
import { RuntimeInteractionCard } from '@/components/chat/runtime-interaction-card';
import { GoalSatisfactionCard } from '@/components/chat/goal-satisfaction-card';
import { GoalRepairPanel } from '@/components/chat/goal-repair-panel';
import { EditProposalCard } from '@/components/chat/edit-proposal-card';
import type { ClarificationCardState } from '@/lib/types/clarification';
import { PlanCard } from '@/components/chat/plan-card';
import { SessionPlanPanel } from '@/components/chat/session-plan-panel';
import { AnalysisGraphPanel } from '@/components/agent/analysis-graph-panel';
import type { SessionPlanViewState } from '@/lib/session/session-plan-delta';
import { InlineNotice } from '@/components/shared/inline-notice';
import { apiFetch } from '@/lib/api/transport';
import { consumePendingLocate } from '@/lib/search/locate';
import {
  agentRuntimeLabel,
  parseAgentRuntime,
  type AgentRuntime,
} from '@/lib/agent-runtime';
import { CitationSourcesProvider, CitationSourceList, splitCitationBlocks, type CitationSource } from '@/components/chat/citation';
import { ChatAnnouncer } from '@/components/chat/chat-announcer';
import { RecoveryBanner } from '@/components/chat/recovery-banner';
import { SessionModelSelector } from '@/components/chat/session-model-selector';
import { TurnContextPanel } from '@/components/chat/turn-context-panel';
import { TurnContextSources } from '@/components/chat/turn-context-sources';
import { adaptChartData } from "@/lib/chart-adapter";
import { VisualAttachBar } from '@/components/sidebar/visual-attach';
import { useLocale, useT } from '@/lib/i18n/useT';

// Bundle-slimming: react-markdown (MiniMd) and recharts (ChartRenderer) load on
// demand instead of riding the / first-load bundle; the tiny pure adapter stays
// static so chart message rendering logic is unchanged.
const MiniMd = dynamic(() => import('@/components/chat/mini-md'), { ssr: false });
const GeneratedFilesPanel = dynamic(() => import('@/components/chat/generated-files-panel').then((m) => m.GeneratedFilesPanel));
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
  { key: 'prompts.poi', icon: MapPin },
  { key: 'prompts.buffer', icon: CircleDot },
  { key: 'prompts.heatmap', icon: Grid2X2 },
  { key: 'prompts.overlay', icon: Blend },
];

function SuggestedPromptButtons({ onSend, isNewSession }: { onSend: (text: string) => void; isNewSession: boolean }) {
  const t = useT();
  return (
    <section className="px-4 py-4" aria-label={t('sidebar.chat.quickCommands')}>
      <h3 className="text-heading font-semibold leading-tight text-ink">
        {t(isNewSession ? 'sidebar.chat.startAnalysis' : 'sidebar.chat.quickCommands')}
      </h3>
      {isNewSession && <p className="mt-1 text-meta leading-normal text-ink-secondary">{t('sidebar.chat.composerPlaceholder')}</p>}
      <div className="mt-3 divide-y divide-edge-subtle rounded-lg border border-edge-subtle bg-surface-raised shadow-raised">
        {SUGGESTED_PROMPTS.map(({ key, icon: Icon }) => {
          // 发送给后端的必须是译后文本（i18n key 只是渲染标识），否则
          // 快捷指令会把 "prompts.poi" 原样当成 prompt 发给 agent。
          const label = t(`sidebar.chat.${key}`);
          return (
            <button
              type="button"
              key={key}
              onClick={() => onSend(label)}
              className="group relative flex min-h-11 w-full cursor-pointer items-center gap-2.5 px-3 py-2 text-left text-body text-ink transition-colors first:rounded-t-lg last:rounded-b-lg hover:bg-surface-hover focus-visible:z-10"
            >
              <span aria-hidden className="grid size-7 shrink-0 place-items-center rounded-md bg-status-accent-soft text-status-accent">
                <Icon size={15} strokeWidth={1.75} />
              </span>
              <span className="min-w-0 flex-1">{label}</span>
              <ArrowUpRight size={14} aria-hidden className="shrink-0 text-ink-muted group-hover:text-status-accent" />
            </button>
          );
        })}
      </div>
    </section>
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
  resultId?: string;
  /** G36（ADR-0291）：用户消息的视觉附件（乐观 chips/回执/恢复三态）。 */
  attachments?: import('@/lib/chat/message-reducer').VisualAttachmentView[];
  /** G43（ADR-0295）：本轮上下文装配回执摘要（SSE 回填 + 恢复）。 */
  turnContextReceipt?: import('@/lib/api/turnContext').TurnContextReceiptSummary;
  /** 方向 3：requirement IR blocking 澄清卡片状态。 */
  clarification?: ClarificationCardState;
  /** G28：任务完成语义卡（task_complete.goal_satisfaction）。 */
  goalSatisfaction?: import('@/lib/types/goal-satisfaction').GoalSatisfactionView;
  /** /goal 03：要素编辑提案卡状态。 */
  featureEditProposal?: import('@/lib/types/feature-edit').FeatureEditProposalState;
  /** G29：Pi 原生运行中交互卡片（多条并存，按 interactionId 去重）。 */
  runtimeInteractions?: import('@/lib/types/runtime-interaction').RuntimeInteractionCardState[];
}

/**
 * #1000：重试入口——最近一条非空 user 消息（失败回合的原始指令），从
 * messages 尾部倒序找。纯派生计算（≤200 条反向扫描，远廉于同帧的
 * markdown 重解析），不 useMemo：React Compiler 无法保留该形状的手工
 * memoization（react-hooks/preserve-manual-memoization）。
 */
function findLastUserMessage(messages: ChatMessage[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === 'user' && m.content.trim()) return m.content.trim();
  }
  return null;
}

interface ChatTabProps {
  messages: ChatMessage[];
  aiStatus: AiStatus;
  onSend: (text: string) => void;
  /** #988：isBusy 期间发送键切换为『停止』形态时点击触发（bridge.cancel）。 */
  onCancel?: () => void;
  /** Plan Mode: 用户在卡片上点按钮时回调，由父组件发送对应 chat 消息并更新 plan.status */
  onPlanAction?: (planId: string, action: 'approve' | 'revise' | 'reject') => void;
  /** /goal 03：EditProposalCard 决策（approve/reject → REST 决策端点）。 */
  onFeatureEditDecision?: (
    proposal: import('@/lib/types/feature-edit').FeatureEditProposalState,
    action: 'approve' | 'reject',
  ) => void;
  /** 方向 3（ADR-0222）：ClarificationCard 提交（REST 应答；waiveKeys 为逐问题豁免）。 */
  onClarificationSubmit?: (
    state: import('@/lib/types/clarification').ClarificationCardState,
    answers: { context_key: string; value: string }[],
    waiveKeys: string[],
  ) => void;
  /** 方向 3：stale/恢复期重取 pending envelope。 */
  onClarificationRefresh?: () => void;
  /** G29：运行中交互提交（REST 直达原生协议；当前 run 自动续行）。 */
  onRuntimeInteractionSubmit?: (
    state: import('@/lib/types/runtime-interaction').RuntimeInteractionCardState,
    answer: { value?: string; confirmed?: boolean; cancelled?: boolean },
  ) => void;
  /** G29：运行中追加指令（adjust=下一可响应位置调整 / queue=完成后处理）。 */
  onTurnMessage?: (text: string, mode: 'adjust' | 'queue') => void;
  /** G29：是否有在飞 turn（追加指令入口的可见性）。 */
  turnActive?: boolean;
  /**
   * #467: 当前会话 id —— 中途切换会话（History 抽屉 / 新会话）会把 aiStatus
   * 强制置 idle，但那是旧会话回合的中止而非完成；播报器据此抑制错误的
   * 「回复已完成」。
   */
  sessionId?: string | null;
  /** Live host for the current turn (SSE task_start). Health is the fallback. */
  agentRuntime?: AgentRuntime | null;
  /** SEC-08：匿名会话的所有权 token —— SessionPlan 面板水合（#1047）用。 */
  ownerToken?: string | null;
  /**
   * #1048：SessionPlan 流式实时状态（page.tsx 里 useSessionPlan 的 view，
   * agentRuntime 同款路径：hook 状态 → props 下行）。未提供时面板自行水合
   * （#1047 行为）。
   */
  sessionPlan?: SessionPlanViewState;
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
  sessionId,
  ownerToken,
  onPlanAction,
  onClarificationSubmit,
  onFeatureEditDecision,
  onClarificationRefresh,
  onRuntimeInteractionSubmit,
}: {
  message: ChatMessage;
  mounted: boolean;
  thinkingText: string;
  sessionId: string | null;
  ownerToken?: string | null;
  onPlanAction?: (planId: string, action: 'approve' | 'revise' | 'reject') => void;
  onClarificationSubmit?: (
    state: import('@/lib/types/clarification').ClarificationCardState,
    answers: { context_key: string; value: string }[],
    waiveKeys: string[],
  ) => void;
  onRuntimeInteractionSubmit?: (
    state: import('@/lib/types/runtime-interaction').RuntimeInteractionCardState,
    answer: { value?: string; confirmed?: boolean; cancelled?: boolean },
  ) => void;
  onFeatureEditDecision?: (
    proposal: import('@/lib/types/feature-edit').FeatureEditProposalState,
    action: 'approve' | 'reject',
  ) => void;
  onClarificationRefresh?: () => void;
}) {
const t = useT();
  const locale = useLocale();
  const isUser = msg.role === 'user';
  const time = (mounted && msg.timestamp)
    ? new Date(msg.timestamp).toLocaleTimeString(locale, {
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
          <span className="text-title font-semibold text-agent-accent">{t('sidebar.chat.you')}</span>
        </div>
        <div
          style={{
            borderTopRightRadius: 4, borderTopLeftRadius: 16,
            borderBottomLeftRadius: 16, borderBottomRightRadius: 16,
            padding: '8px 12px', fontSize: 14.5, lineHeight: 1.6, color: 'var(--text-on-accent)',
            backgroundColor: 'var(--agent-accent)'
          }}
        >
          {/* G36（ADR-0291）：用户消息附件 chips（乐观/回执/恢复三态）。 */}
          {Array.isArray(msg.attachments) && msg.attachments.length > 0 && (
            <div className="mb-1 flex flex-wrap gap-1" data-testid="message-attachments">
              {(msg.attachments as import('@/lib/chat/message-reducer').VisualAttachmentView[]).map((a) => (
                <span
                  key={a.evidence_id}
                  title={`${a.label ?? a.source_type}${a.status === 'refused' ? ` — ${a.reason ?? ''}` : ''}`}
                  className="inline-flex items-center gap-1 rounded-pill bg-white/20 px-1.5 py-0.5 text-[11px]"
                  data-testid={`message-attachment-${a.evidence_id}`}
                >
                  {a.thumbUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={a.thumbUrl} alt="" className="h-5 w-5 rounded-sm object-cover" />
                  ) : (
                    <ImagePlus size={10} aria-hidden />
                  )}
                  {a.source_type === 'map_viewport' && t('sidebar.chat.visualAttach.chipViewport')}
                  {a.source_type === 'map_region' && t('sidebar.chat.visualAttach.chipRegion')}
                  {a.source_type === 'reference_upload' && t('sidebar.chat.visualAttach.chipReference')}
                  {a.mapspec_revision != null && <span>r{a.mapspec_revision}</span>}
                  {a.status === 'sent' && <span aria-label={t('sidebar.chat.visualAttach.receiptSent')}>✓</span>}
                  {a.status === 'refused' && (
                    <span aria-label={t('sidebar.chat.visualAttach.receiptRefused')} title={a.reason}>✕</span>
                  )}
                </span>
              ))}
            </div>
          )}
          <div className="whitespace-pre-wrap">{splitCitationBlocks(msg.content).body}</div>
          <CitationSourceList sources={splitCitationBlocks(msg.content).sources} />
        </div>
        {/* G43（ADR-0295）：本轮上下文回执（可展开；仅有回执的用户消息渲染）。 */}
        {msg.turnContextReceipt && (
          <TurnContextPanel
            sessionId={sessionId}
            ownerToken={ownerToken}
            summary={msg.turnContextReceipt}
          />
        )}
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
            <span>{t('sidebar.chat.perceivedLayers')}{msg.layerAdded}</span>
            {msg.resultId && (
              <button
                type="button"
                onClick={() => {
                  const s = useHudStore.getState();
                  s.selectResult(msg.resultId!);
                  s.setActiveLeftTab('results');
                }}
                className="ml-1 inline-flex shrink-0 items-center gap-0.5 whitespace-nowrap rounded-full bg-white/20 px-1.5 py-0.5 text-[11px] font-medium text-white transition-colors hover:bg-white/30"
                aria-label={t('sidebar.chat.viewResultAria')}
              >
                {t('sidebar.chat.viewResult')}
              </button>
            )}
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
              <CollapsibleThink
                content={msg.think}
                isStreaming={!!msg.isThinking}
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
                onApprove={(pid) => onPlanAction?.(pid, 'approve')}
                onRevise={(pid) => onPlanAction?.(pid, 'revise')}
                onReject={(pid) => onPlanAction?.(pid, 'reject')}
              />
            )}
            {msg.clarification && (
              <ClarificationCard
                state={msg.clarification as ClarificationCardState}
                onSubmit={({ answers, waiveKeys }) =>
                  onClarificationSubmit?.(
                    msg.clarification as ClarificationCardState,
                    answers,
                    waiveKeys,
                  )
                }
                onRefresh={onClarificationRefresh}
              />
            )}
            {(msg.runtimeInteractions ?? []).length > 0 && (
              (msg.runtimeInteractions as import('@/lib/types/runtime-interaction').RuntimeInteractionCardState[]).map(
                (card) => (
                  <RuntimeInteractionCard
                    key={card.interactionId}
                    state={card}
                    onSubmit={({ interactionId, answer }) => {
                      void interactionId;
                      onRuntimeInteractionSubmit?.(card, answer);
                    }}
                    onCancel={() => onRuntimeInteractionSubmit?.(card, { cancelled: true })}
                  />
                ),
              )
            )}
            {msg.goalSatisfaction && (
              <GoalSatisfactionCard
                view={msg.goalSatisfaction as import('@/lib/types/goal-satisfaction').GoalSatisfactionView}
              />
            )}
            {msg.featureEditProposal && (
              <EditProposalCard
                proposal={msg.featureEditProposal}
                onDecide={(planId, action) => {
                  const proposal = msg.featureEditProposal;
                  if (proposal) onFeatureEditDecision?.(proposal, action);
                }}
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

export function ChatTab({ messages, aiStatus, onSend, onCancel, onPlanAction, onClarificationSubmit, onFeatureEditDecision, onClarificationRefresh, onRuntimeInteractionSubmit, onTurnMessage, turnActive, sessionId, agentRuntime, ownerToken, sessionPlan }: ChatTabProps) {
  const t = useT();
  const [filesOpen, setFilesOpen] = useState(false);
  const filesButton = useRef<HTMLButtonElement>(null);
  const closeFiles = useCallback(() => {
    setFilesOpen(false);
    requestAnimationFrame(() => filesButton.current?.focus());
  }, []);
  const [configuredRuntime, setConfiguredRuntime] = useState<AgentRuntime | null>(null);
  useEffect(() => {
    let cancelled = false;
    apiFetch<{ agent_runtime?: string }>('/api/v1/health')
      .then((data) => {
        if (cancelled) return;
        setConfiguredRuntime(parseAgentRuntime(data?.agent_runtime));
      })
      .catch(() => {
        /* badge is additive; a down health endpoint just hides it */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const runtime = agentRuntime ?? configuredRuntime;
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const [input, setInput] = useState('');
  // G29：运行中追加指令模式（turnActive 时可见；normal=停当前发新 turn 的既有语义）。
  const [inRunMode, setInRunMode] = useState<'normal' | 'adjust' | 'queue'>('normal');
  // V9 citation 注入（ADR-0145）：知识库面板把带 [n] 引用块的文本拼入输入框
  // 草稿，由用户确认发送（chat API 的 message 是纯字符串，不静默代发）。
  const pendingInjection = useHudStore((s) => s.pendingChatInjection);
  const clearPendingChatInjection = useHudStore((s) => s.clearPendingChatInjection);
  useEffect(() => {
    if (pendingInjection === null) return;
    setFilesOpen(false);
    setInput((prev) =>
      prev.trim() ? `${prev}\n\n${pendingInjection}` : pendingInjection,
    );
    clearPendingChatInjection();
  }, [pendingInjection, clearPendingChatInjection]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isBusy = aiStatus === 'thinking' || aiStatus === 'acting';

  const scrollToBottom = useCallback(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, []);

  const NEAR_BOTTOM_PX = 80;

  const isNearBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
  }, []);

  useEffect(() => {
    if (messages.length > 0 && isNearBottom()) {
      scrollToBottom();
    }
  }, [messages, aiStatus, scrollToBottom, isNearBottom]);

  // ADR-0147（P4）：跨会话搜索「定位到消息」—— 会话恢复装载完成后，消费
  // pendingLocate 滚动到目标消息并短暂高亮。会话不匹配/超时由 locate 模块
  // 丢弃（append-only 接线，不改既有滚动行为）。
  const [locateIndex, setLocateIndex] = useState<number | null>(null);
  useEffect(() => {
    if (!sessionId || messages.length === 0) return;
    const target = consumePendingLocate(sessionId);
    if (target === null || target >= messages.length) return;
    setFilesOpen(false);
    setLocateIndex(target);
    const t = setTimeout(() => {
      const el = scrollRef.current?.querySelector<HTMLElement>(
        `[data-chat-message-index="${target}"]`,
      );
      el?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
    }, 80);
    const clear = setTimeout(() => setLocateIndex(null), 2600);
    return () => {
      clearTimeout(t);
      clearTimeout(clear);
    };
  }, [sessionId, messages.length]);

  // D-F8：按消息对象身份缓存 citation 解析 —— 流式 token 批次只替换当前
  // 流式消息对象，历史消息命中缓存，不再每批次对全部消息重跑 splitCitationBlocks。
  const citationSourcesCacheRef = useRef(new WeakMap<ChatMessage, CitationSource[]>());
  const citationSourcesByMessage = useMemo(() => {
    const cache = citationSourcesCacheRef.current;
    let latest: CitationSource[] = [];
    return messages.map((message) => {
      if (message.role === 'user') {
        let sources = cache.get(message);
        if (!sources) {
          sources = splitCitationBlocks(message.content).sources;
          cache.set(message, sources);
        }
        latest = sources;
      }
      return latest;
    });
  }, [messages]);

  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 80)}px`;
  }, [input]);

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || isBusy) return;
    if (inRunMode !== 'normal' && turnActive && onTurnMessage) {
      // G29：运行中追加指令 —— 不新开 turn（steer/follow_up 原生队列）。
      onTurnMessage(trimmed, inRunMode);
      setInput('');
      setInRunMode('normal');
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
      return;
    }
    onSend(trimmed);
    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [input, isBusy, onSend, inRunMode, turnActive, onTurnMessage]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // IME 组合中的 Enter 只用于选字（Safari 用 keyCode 229，其余浏览器
      // 依赖 isComposing），直接发送会把未上屏的拼音串当作消息发出。
      if (e.nativeEvent.isComposing || e.keyCode === 229) return;
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  const thinkingText =
    aiStatus === 'thinking'
      ? t('sidebar.chat.thinking')
      : aiStatus === 'acting'
        ? t('sidebar.chat.acting')
        : '';

  // #1000：失败终态时 messages 已是最终形状，直接派生（见函数注释）。
  const lastUserMessage = findLastUserMessage(messages);

  const handleRetry = useCallback(() => {
    if (!lastUserMessage || isBusy) return;
    onSend(lastUserMessage);
  }, [lastUserMessage, isBusy, onSend]);

  const conversation = (
    <div className={filesOpen ? 'hidden' : 'flex h-full flex-col'}>
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-edge-subtle px-panel">
        {runtime && (
          <p className="min-w-0 truncate text-meta tracking-wide text-ink-muted" data-testid="agent-runtime-badge">
            {agentRuntimeLabel(runtime)}
          </p>
        )}
        <button ref={filesButton} type="button" onClick={() => setFilesOpen(true)}
          className="ml-auto inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-md px-2 text-meta font-medium text-ink-secondary hover:bg-surface-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-status-accent">
          <FolderOpen size={15} aria-hidden /> {t('chat.generatedFiles.title')}
        </button>
      </div>
      {/* SessionPlan 面板（Pi 路径，#1047）：会话级只读水合卡。无信封 / 拉取
          失败时整卡隐藏 —— ChatEngine 兜底会话的侧边栏与今天完全一致。
          #1048：sessionPlan（useSessionPlan.view）提供时由流式增量驱动。 */}
      <SessionPlanPanel sessionId={sessionId} ownerToken={ownerToken} live={sessionPlan} />
      {/* 显式分析图（ADR-0097）：与 SessionPlan 同一世界状态的图检视面 ——
          目标/方法论警告/执行 DAG/产品 facets/下一动作。只读投影，端点
          失败时整卡隐藏（与 SessionPlanPanel 同降级约定）。 */}
      <AnalysisGraphPanel
        sessionId={sessionId}
        ownerToken={ownerToken}
        refreshKey={messages.length}
      />
      {/* G22-P6（会话恢复可读性）：恢复摘要横幅挂在 transcript 上方 —— 自水合
          `?recovery=1`，未恢复 / 拉取失败整条隐藏（同 SessionPlanPanel 降级
          约定），技术 id 只进 title 诊断属性。 */}
      <RecoveryBanner sessionId={sessionId} ownerToken={ownerToken} />
      {/* Messages scroll area */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">

        {/*
          a11y：流式回复此前完全没有播报 —— 助手 token、工具调用状态、
          「正在分析指令…」全都只是视觉变化，读屏用户感知不到产品的主反馈回路。
          播报交给 <ChatAnnouncer/>（下方 composer 区）：把 aria-live 直接放在
          消息列表上会在每个 token 批次重读整段回答（MiniMd 每批重新解析、整个
          气泡子树被替换，aria-atomic=false 只有在追加叶子节点时才是增量）。
        */}
        <div className="space-y-2 px-panel py-2 empty:hidden">
          {messages.map((msg, idx) => (
            <div
              key={msg.id ?? `msg-${idx}`}
              data-chat-message-index={idx}
              data-chat-locate-hit={locateIndex === idx ? 'true' : undefined}
              className={locateIndex === idx ? 'rounded-md ring-2 ring-status-accent' : undefined}
            >
              <CitationSourcesProvider sources={citationSourcesByMessage[idx]}>
              <ChatMessageItem
                message={msg}
                mounted={mounted}
                thinkingText={thinkingText}
                onPlanAction={onPlanAction}
                onClarificationSubmit={onClarificationSubmit}
                onRuntimeInteractionSubmit={onRuntimeInteractionSubmit}
      onFeatureEditDecision={onFeatureEditDecision}
                onClarificationRefresh={onClarificationRefresh}
                sessionId={sessionId ?? null}
                ownerToken={ownerToken}
              />
              </CitationSourcesProvider>
              {/* G42：完成卡（非 satisfied）下的有界补全面 —— 预览/启动/取消/
                  回执/重终验对照；attempt 状态经服务端读模型刷新可回看。 */}
              {msg.goalSatisfaction &&
                (msg.goalSatisfaction as import('@/lib/types/goal-satisfaction').GoalSatisfactionView).verdict !== 'satisfied' && (
                  <GoalRepairPanel
                    sessionId={sessionId ?? ''}
                    active
                  />
                )}
            </div>
          ))}


          {/* Thinking indicator at end of messages */}
          {isBusy && messages.length > 0 && !messages[messages.length - 1]?.isThinking && (
            <ThinkingDots text={thinkingText} />
          )}
        </div>

        {/* Suggested prompts when not busy and few messages */}
        {!isBusy && messages.length <= 1 && (
          <SuggestedPromptButtons onSend={onSend} isNewSession={messages.length === 0} />
        )}
      </div>

      {/* Input area */}
      <ChatAnnouncer messages={messages} aiStatus={aiStatus} sessionId={sessionId} />

      {/* 不透明底 + 去掉 blur(12px)：composer 压在地图上，半透明会让输入文字
          与底图细节互相干扰，而 backdrop-filter 又是最贵的那类合成。 */}
      <div className="shrink-0 border-t border-edge-subtle bg-surface-panel">
        {/* G37（会话模型运行配置）：目录/实际生效/用量工作台 —— 展开式
            精简行；服务器回执不可读（新会话/权限）时整条隐藏。 */}
        <SessionModelSelector
          sessionId={sessionId ?? null}
          ownerToken={ownerToken}
          turnActive={Boolean(turnActive)}
        />
        {/* G43（ADR-0295）：会话可选资料工作台（优先/排除 + 预览 + CAS）——
            与模型选择同层的会话级控制面。 */}
        <TurnContextSources
          sessionId={sessionId ?? null}
          ownerToken={ownerToken}
        />
        {/* UI V3：AI 错误在 chat 内可见（之前仅 top-bar/HUD 有指示，
            composer 静默恢复可用，用户无法感知失败） */}
        {aiStatus === 'error' && (
          <div className="px-panel pt-2">
            <InlineNotice variant="error">
              {/* #1000：失败后的恢复入口——一键重发最近一条 user 指令，
                  免去重新手打整条命令。 */}
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1">{t('sidebar.chat.lastFailed')}</span>
                {lastUserMessage && (
                  <button
                    type="button"
                    onClick={handleRetry}
                    disabled={isBusy}
                    aria-label={t('sidebar.chat.retryAria')}
                    title={lastUserMessage}
                    className="inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-sm border border-status-critical-border bg-surface-raised px-1.5 py-0.5 text-micro font-medium text-status-critical transition-colors hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <RotateCcw size={10} aria-hidden />
                    {t('sidebar.chat.retryLast')}
                  </button>
                )}
              </div>
            </InlineNotice>
          </div>
        )}
        <div className="mx-panel my-2 rounded-lg border border-edge bg-surface-raised p-2 shadow-raised focus-within:border-status-accent">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            aria-label={t('sidebar.chat.inputAria')}
            placeholder={t('sidebar.chat.inputPlaceholder')}
            rows={1}
            className="block max-h-20 min-h-9 w-full resize-none rounded-sm bg-transparent px-1.5 py-1 text-body leading-normal text-ink placeholder:text-ink-muted focus-visible:ring-1 focus-visible:ring-status-accent"
          />

          <div className="mt-1 flex flex-wrap items-end gap-1.5">
            <div className="min-w-0 flex-1 self-center">
              <VisualAttachBar sessionId={sessionId} ownerToken={ownerToken} />
            </div>
            {/* Send / Stop button —— #988：isBusy 时发送键切换为『停止』形态，
                中止进行中的 Agent 回合（后端把 SSE 断开归一为 task_cancelled），
                而不是禁用干等或整页刷新丢工作台上下文。 */}
            {turnActive && isBusy && onTurnMessage && (
              <div className="flex shrink-0 items-center gap-1 pr-1" data-testid="turn-message-modes">
                <button
                  type="button"
                  onClick={() => setInRunMode(inRunMode === 'adjust' ? 'normal' : 'adjust')}
                  aria-pressed={inRunMode === 'adjust'}
                  title={t('chat.interaction.adjustHint')}
                  className={`cursor-pointer rounded-pill px-2 py-0.5 text-meta ${
                    inRunMode === 'adjust'
                      ? 'bg-status-accent-soft text-status-accent'
                      : 'bg-surface-sunken text-ink-muted'
                  }`}
                >
                  {t('chat.interaction.adjust')}
                </button>
                <button
                  type="button"
                  onClick={() => setInRunMode(inRunMode === 'queue' ? 'normal' : 'queue')}
                  aria-pressed={inRunMode === 'queue'}
                  title={t('chat.interaction.queueHint')}
                  className={`cursor-pointer rounded-pill px-2 py-0.5 text-meta ${
                    inRunMode === 'queue'
                      ? 'bg-status-accent-soft text-status-accent'
                      : 'bg-surface-sunken text-ink-muted'
                  }`}
                >
                  {t('chat.interaction.queue')}
                </button>
              </div>
            )}
            {isBusy ? (
              <button
                onClick={onCancel}
                disabled={!onCancel}
                aria-label={t('sidebar.chat.stopAria')}
                title={t('sidebar.chat.stopAria')}
                className="touch-target flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-md border border-status-critical-border bg-status-critical-soft text-status-critical transition-colors hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Square size={10} fill="currentColor" aria-hidden />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!input.trim()}
                aria-label={t('sidebar.chat.sendAria')}
                className={`touch-target flex size-8 shrink-0 items-center justify-center rounded-md transition-colors ${
                  input.trim()
                    ? 'cursor-pointer bg-status-accent text-ink-on-accent'
                    : 'cursor-not-allowed bg-surface-sunken text-ink-disabled'
                }`}
              >
                <Send size={15} aria-hidden />
              </button>
            )}
          </div>
        </div>

        {/* Hint */}
        <div className="hidden px-panel pb-2 text-right [@media(pointer:fine)]:block">
          <span className="text-micro text-ink-muted">{t('sidebar.chat.enterHint')}</span>
        </div>
      </div>
    </div>
  );

  return (
    <>
      {filesOpen && (
        <GeneratedFilesPanel
          sessionId={sessionId ?? null}
          ownerToken={ownerToken}
          messages={messages}
          onBack={closeFiles}
        />
      )}
      {conversation}
    </>
  );
}

export default ChatTab;
