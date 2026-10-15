'use client';

/**
 * ClarificationCard — requirement IR blocking 澄清卡片（方向 3 / ADR-0222）
 *
 * 渲染条件：消息上挂载 clarification 字段（SSE `clarification` 事件或恢复
 * 载入）。行为：
 *  - 每个问题：radio 选项组（≤4）；allow_free_text 时提供文本输入（回车
 *    提交）；原生 radio/input 保证键盘可达与 ARIA 语义；
 *  - 「使用推荐默认值」仅当对应问题 can_waive（存在安全默认）时出现，
 *    逐问题独立豁免，不整卡强推默认；
 *  - 状态机：open → submitting（全禁用）→ submitted（锁定回显）；
 *    stale（revision 前进，提示后由父级重取 pending）/ error（可重试）；
 *  - 提交走 REST answer 端点（结构化，非自由文本 NLP）；成功后父组件
 *    自动发送续行消息让 agent 恢复执行（与 PlanProposalCard 同模式）。
 */

import { useMemo, useState } from 'react';
import { HelpCircle, CheckCircle2, AlertTriangle, RefreshCw } from 'lucide-react';
import { useLocale, useT } from '@/lib/i18n/useT';
import type {
  ClarificationCardState,
  ClarificationQuestion,
} from '@/lib/types/clarification';

export interface ClarificationCardProps {
  state: ClarificationCardState;
  /** 提交应答（answers 非空）或逐问题豁免（waiveKeys 非空）。 */
  onSubmit: (args: {
    requestId: string;
    answers: { context_key: string; value: string }[];
    waiveKeys: string[];
  }) => void;
  /** stale/恢复期重取 pending envelope。 */
  onRefresh?: () => void;
}

function pickText(locale: string, zh: string, en: string): string {
  if (locale.startsWith('en')) return en || zh;
  return zh || en;
}

export function ClarificationCard({ state, onSubmit, onRefresh }: ClarificationCardProps) {
  const t = useT();
  const locale = useLocale();
  const { envelope, status } = state;
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [freeText, setFreeText] = useState<Record<string, string>>({});
  const [waived, setWaived] = useState<Record<string, boolean>>({});

  const disabled = status === 'submitting' || status === 'submitted' || status === 'stale';

  const questions = useMemo(
    () => envelope.questions.slice(0, 2),
    [envelope.questions],
  );

  const answerOf = (q: ClarificationQuestion): string | undefined => {
    if (waived[q.context_key]) return q.default_value;
    const choice = selected[q.context_key];
    if (choice) return choice;
    const text = (freeText[q.context_key] ?? '').trim();
    if (text) return text;
    return undefined;
  };

  const allAnswered = questions.every((q) => answerOf(q) !== undefined);
  const anyWaived = questions.some((q) => waived[q.context_key]);
  const canSubmit = !disabled && (allAnswered || anyWaived);

  const handleSubmit = () => {
    if (!canSubmit) return;
    const answers: { context_key: string; value: string }[] = [];
    const waiveKeys: string[] = [];
    for (const q of questions) {
      const value = answerOf(q);
      if (value === undefined) continue;
      if (waived[q.context_key]) waiveKeys.push(q.context_key);
      else answers.push({ context_key: q.context_key, value });
    }
    if (!answers.length && !waiveKeys.length) return;
    onSubmit({ requestId: envelope.request_id, answers, waiveKeys });
  };

  return (
    <div
      data-testid="clarification-card"
      data-request-id={envelope.request_id}
      data-status={status}
      role="group"
      aria-label={t('chat.clarify.title')}
      className="my-2 rounded-md border border-edge-subtle bg-surface-raised p-3 text-body"
    >
      {/* Header */}
      <div className="flex items-start gap-2">
        <div
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
          style={{ backgroundColor: 'color-mix(in srgb, var(--agent-accent) 12%, transparent)' }}
        >
          <HelpCircle size={14} style={{ color: 'var(--agent-accent)' }} />
        </div>
        <div className="min-w-0 flex-1">
          <span className="text-title font-semibold text-ink">
            {t('chat.clarify.title')}
          </span>
          {status === 'submitted' && (
            <span className="ml-2 inline-flex items-center gap-1 rounded-pill bg-status-success-soft px-2 py-px text-meta text-status-success">
              <CheckCircle2 size={10} /> {t('chat.clarify.submitted')}
            </span>
          )}
          {status === 'stale' && (
            <span className="ml-2 inline-flex items-center gap-1 rounded-pill bg-status-warning-soft px-2 py-px text-meta text-status-warning">
              <AlertTriangle size={10} /> {t('chat.clarify.stale')}
            </span>
          )}
          <div className="mt-1 text-body text-ink-muted">
            {t('chat.clarify.subtitle', { revision: envelope.requirement_revision })}
          </div>
        </div>
      </div>

      {/* Questions */}
      <div className="mt-2.5 flex flex-col gap-3">
        {questions.map((q) => {
          const text = pickText(locale, q.question_zh, q.question_en);
          const submittedValue =
            status === 'submitted' ? state.submittedAnswers?.[q.context_key] : undefined;
          return (
            <fieldset
              key={q.context_key}
              data-context-key={q.context_key}
              className="rounded-md bg-surface-sunken p-2"
              disabled={disabled}
            >
              <legend className="px-1 text-body font-medium text-ink">{text}</legend>
              {status === 'submitted' ? (
                <div className="px-1 py-0.5 text-body text-status-success">
                  {submittedValue
                    ? `✓ ${submittedValue}`
                    : `✓ ${t('chat.clarify.waived')}`}
                </div>
              ) : (
                <>
                  {q.options.length > 0 && (
                    <div role="radiogroup" aria-label={text} className="flex flex-col gap-1">
                      {q.options.map((o) => {
                        const id = `${envelope.request_id}-${q.context_key}-${o.value}`;
                        const label = pickText(locale, o.label_zh, o.label_en);
                        const checked = selected[q.context_key] === o.value && !waived[q.context_key];
                        return (
                          <label
                            key={o.value}
                            htmlFor={id}
                            className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 text-body text-ink hover:bg-surface-raised"
                          >
                            <input
                              id={id}
                              type="radio"
                              name={`${envelope.request_id}-${q.context_key}`}
                              value={o.value}
                              checked={checked}
                              disabled={disabled}
                              onChange={() =>
                                setSelected((prev) => ({
                                  ...prev,
                                  [q.context_key]: o.value,
                                }))
                              }
                            />
                            <span>{label}</span>
                            {o.is_default && (
                              <span className="text-meta text-ink-muted">
                                {t('chat.clarify.recommended')}
                              </span>
                            )}
                          </label>
                        );
                      })}
                    </div>
                  )}
                  {q.allow_free_text && (
                    <input
                      type="text"
                      value={freeText[q.context_key] ?? ''}
                      disabled={disabled}
                      maxLength={128}
                      placeholder={t('chat.clarify.freeTextPlaceholder')}
                      aria-label={t('chat.clarify.freeTextPlaceholder')}
                      className="mt-1.5 w-full rounded-md border border-edge-subtle bg-surface-raised px-2 py-1.5 text-body text-ink"
                      onChange={(e) =>
                        setFreeText((prev) => ({
                          ...prev,
                          [q.context_key]: e.target.value,
                        }))
                      }
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          handleSubmit();
                        }
                      }}
                    />
                  )}
                  {q.can_waive && (
                    <label className="mt-1.5 flex cursor-pointer items-center gap-2 px-1 text-meta text-ink-muted">
                      <input
                        type="checkbox"
                        checked={Boolean(waived[q.context_key])}
                        disabled={disabled}
                        onChange={(e) =>
                          setWaived((prev) => ({
                            ...prev,
                            [q.context_key]: e.target.checked,
                          }))
                        }
                      />
                      {t('chat.clarify.useDefault', { default: q.default_value })}
                    </label>
                  )}
                </>
              )}
            </fieldset>
          );
        })}
      </div>

      {/* Status banners */}
      {status === 'stale' && (
        <div className="mt-2 rounded-md border border-status-warning-border bg-status-warning-soft p-2 text-body text-status-warning">
          {t('chat.clarify.staleHint')}
          {onRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              className="ml-2 inline-flex cursor-pointer items-center gap-1 border-none bg-transparent p-0 text-body underline"
            >
              <RefreshCw size={11} /> {t('chat.clarify.refresh')}
            </button>
          )}
        </div>
      )}
      {status === 'error' && (
        <div className="mt-2 rounded-md border border-status-critical-border bg-status-critical-soft p-2 text-body text-status-critical" role="alert">
          {t('chat.clarify.error')}
        </div>
      )}

      {/* Submit */}
      {status !== 'submitted' && (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            disabled={!canSubmit}
            onClick={handleSubmit}
            className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-2.5 py-1.5 text-body font-medium ${
              canSubmit ? 'cursor-pointer' : 'cursor-not-allowed'
            }`}
            style={{
              backgroundColor: canSubmit
                ? 'var(--agent-accent)'
                : 'color-mix(in srgb, var(--neutral) 30%, transparent)',
              color: canSubmit ? 'var(--text-on-accent)' : 'var(--text-muted)',
            }}
          >
            {status === 'submitting' ? t('chat.clarify.submitting') : t('chat.clarify.submit')}
          </button>
        </div>
      )}
    </div>
  );
}
