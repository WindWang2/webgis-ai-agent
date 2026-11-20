'use client';

/**
 * RuntimeInteractionCard — Pi 原生运行中交互卡片（G29 / WP08）。
 *
 * 渲染条件：消息上挂载 runtimeInteraction 字段（SSE `pi_interaction` 或
 * 恢复水合）。当前 agent run 正阻塞等待这一次回答：
 *  - select：radio 选项组（选项由后端按可读名称+范围/日期区分）；
 *  - confirm：是/否 + 取消；
 *  - input：单行（Enter 提交，输入法组词中不提交）；
 *  - editor：多行 textarea（中文/换行原样保留）。
 * 状态机：open → submitting → answered（等待任务继续）| 终态
 * （resolved/cancelled/timed_out/invalidated）；error 可重试。
 * 提交走 REST answer 端点（不经 LLM；后端白名单/幂等）。
 * 可达性：原生控件 + aria + 键盘全程可操作；不渲染任何 HTML 注入。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { HelpCircle, CheckCircle2, AlertTriangle, XCircle, Clock } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import type { RuntimeInteractionCardState } from '@/lib/types/runtime-interaction';

export interface RuntimeInteractionCardProps {
  state: RuntimeInteractionCardState;
  /** 提交回答（value/confirmed/cancelled 三选一）。 */
  onSubmit: (args: { interactionId: string; answer: { value?: string; confirmed?: boolean; cancelled?: boolean } }) => void;
  /** 用户取消（与提交 cancelled 同端点，语义独立按钮）。 */
  onCancel?: (args: { interactionId: string }) => void;
}

export function RuntimeInteractionCard({ state, onSubmit, onCancel }: RuntimeInteractionCardProps) {
  const t = useT();
  const [selected, setSelected] = useState('');
  const [text, setText] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const busy = state.status === 'submitting';
  const answered = state.status === 'answered';
  const terminal = ['resolved', 'cancelled', 'timed_out', 'invalidated'].includes(state.status);

  // 新问题到达（或封套换代）时清空本地作答并聚焦首个可交互控件。
  useEffect(() => {
    setSelected('');
    setText(state.prefill ?? '');
    if (state.status === 'open') {
      queueMicrotask(() => {
        const first = document.querySelector<HTMLElement>(
          `[data-interaction-id="${state.interactionId}"] input:not([type=hidden]), [data-interaction-id="${state.interactionId}"] textarea`
        );
        first?.focus();
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.interactionId]);

  // 焦点恢复：卡片重挂载（如切标签页回来）时恢复键盘位置。
  useEffect(() => {
    if (state.status === 'open' && state.method === 'editor') {
      textareaRef.current?.focus();
    }
  }, [state.status, state.method]);

  const canSubmit = useMemo(() => {
    if (busy || answered || terminal) return false;
    if (state.method === 'select') return selected !== '';
    if (state.method === 'confirm') return true;
    return text.trim() !== '';
  }, [busy, answered, terminal, state.method, selected, text]);

  const handleSubmit = () => {
    if (!canSubmit) return;
    if (state.method === 'select') {
      onSubmit({ interactionId: state.interactionId, answer: { value: selected } });
    } else if (state.method === 'confirm') {
      onSubmit({ interactionId: state.interactionId, answer: { confirmed: true } });
    } else {
      onSubmit({ interactionId: state.interactionId, answer: { value: text } });
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
      // 输入法组词中的 Enter 不提交（IME 安全）。
      if (state.method === 'input') {
        e.preventDefault();
        handleSubmit();
      } else if (state.method === 'select' && selected) {
        e.preventDefault();
        handleSubmit();
      }
    }
  };

  const statusBadge = () => {
    if (answered) {
      return (
        <span className="ml-2 inline-flex items-center gap-1 rounded-pill bg-status-info-soft px-2 py-px text-meta text-status-info">
          <Clock size={10} /> {t('chat.interaction.answered')}
        </span>
      );
    }
    if (state.status === 'resolved') {
      return (
        <span className="ml-2 inline-flex items-center gap-1 rounded-pill bg-status-success-soft px-2 py-px text-meta text-status-success">
          <CheckCircle2 size={10} /> {t('chat.interaction.resolved')}
        </span>
      );
    }
    if (state.status === 'cancelled') {
      return (
        <span className="ml-2 inline-flex items-center gap-1 rounded-pill bg-status-neutral-soft px-2 py-px text-meta text-ink-muted">
          <XCircle size={10} /> {t('chat.interaction.cancelled')}
        </span>
      );
    }
    if (state.status === 'timed_out' || state.status === 'invalidated') {
      return (
        <span className="ml-2 inline-flex items-center gap-1 rounded-pill bg-status-warning-soft px-2 py-px text-meta text-status-warning">
          <AlertTriangle size={10} /> {t(`chat.interaction.${state.status}`)}
        </span>
      );
    }
    return null;
  };

  return (
    <div
      data-testid="runtime-interaction-card"
      data-interaction-id={state.interactionId}
      data-status={state.status}
      role="group"
      aria-label={state.title || t('chat.interaction.title')}
      className="my-2 rounded-md border border-edge-subtle bg-surface-raised p-3 text-body"
    >
      <div className="flex items-start gap-2">
        <div
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
          style={{ backgroundColor: 'color-mix(in srgb, var(--agent-accent) 12%, transparent)' }}
        >
          <HelpCircle size={14} style={{ color: 'var(--agent-accent)' }} />
        </div>
        <div className="min-w-0 flex-1">
          <span className="text-title font-semibold text-ink">{state.title}</span>
          {statusBadge()}
          {state.message && (
            <div className="mt-1 whitespace-pre-wrap text-body text-ink-muted">{state.message}</div>
          )}
          {state.sourceTool && (
            <div className="mt-0.5 text-meta text-ink-muted">
              {t('chat.interaction.fromTool', { tool: state.sourceTool })}
            </div>
          )}
        </div>
      </div>

      <div className="mt-2.5">
        {state.method === 'select' && (
          <div role="radiogroup" aria-label={state.title} className="flex flex-col gap-1">
            {(state.options ?? []).map((option, index) => {
              const id = `${state.interactionId}-opt-${index}`;
              const checked = selected === option;
              return (
                <label
                  key={id}
                  htmlFor={id}
                  className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 text-body text-ink hover:bg-surface-sunken"
                >
                  <input
                    id={id}
                    type="radio"
                    name={`${state.interactionId}-options`}
                    value={option}
                    checked={checked}
                    disabled={busy || answered || terminal}
                    onChange={() => setSelected(option)}
                    onKeyDown={handleKeyDown}
                  />
                  <span className="whitespace-pre-wrap">{option}</span>
                </label>
              );
            })}
          </div>
        )}

        {state.method === 'input' && (
          <input
            type="text"
            value={text}
            disabled={busy || answered || terminal}
            maxLength={4000}
            placeholder={state.placeholder ?? ''}
            aria-label={state.title}
            className="mt-1 w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-body text-ink"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
          />
        )}

        {state.method === 'editor' && (
          <textarea
            ref={textareaRef}
            value={text}
            disabled={busy || answered || terminal}
            maxLength={8000}
            rows={Math.min(8, Math.max(3, (text.match(/\n/g)?.length ?? 0) + 2))}
            aria-label={state.title}
            className="mt-1 w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-body text-ink"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
          />
        )}
      </div>

      {state.status === 'error' && (
        <div
          className="mt-2 rounded-md border border-status-critical-border bg-status-critical-soft p-2 text-body text-status-critical"
          role="alert"
        >
          {t('chat.interaction.error')}
          {state.errorCode ? ` (${state.errorCode})` : ''}
        </div>
      )}

      {(answered || terminal) && state.answerDisplay && (
        <div className="mt-2 rounded-md bg-surface-sunken p-2 text-body text-ink">
          {t('chat.interaction.answerWas', { answer: state.answerDisplay })}
        </div>
      )}
      {answered && (
        <div className="mt-1 text-meta text-ink-muted">{t('chat.interaction.waitingContinue')}</div>
      )}

      {!answered && !terminal && (
        <div className="mt-3 flex flex-wrap gap-2">
          {state.method === 'confirm' && (
            <button
              type="button"
              disabled={busy}
              onClick={() => onSubmit({ interactionId: state.interactionId, answer: { confirmed: false } })}
              className="cursor-pointer rounded-md border border-edge-subtle bg-surface-sunken px-2.5 py-1.5 text-body text-ink disabled:cursor-not-allowed"
            >
              {t('chat.interaction.confirmNo')}
            </button>
          )}
          <button
            type="button"
            disabled={!canSubmit}
            onClick={handleSubmit}
            className={`flex-1 items-center justify-center rounded-md px-2.5 py-1.5 text-body font-medium ${
              canSubmit ? 'cursor-pointer' : 'cursor-not-allowed'
            }`}
            style={{
              backgroundColor: canSubmit
                ? 'var(--agent-accent)'
                : 'color-mix(in srgb, var(--neutral) 30%, transparent)',
              color: canSubmit ? 'var(--text-on-accent)' : 'var(--text-muted)',
            }}
          >
            {busy ? t('chat.interaction.submitting') : t('chat.interaction.submit')}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onCancel?.({ interactionId: state.interactionId })}
            className="cursor-pointer rounded-md border border-edge-subtle bg-surface-sunken px-2.5 py-1.5 text-body text-ink-muted disabled:cursor-not-allowed"
          >
            {t('chat.interaction.cancel')}
          </button>
        </div>
      )}
    </div>
  );
}
