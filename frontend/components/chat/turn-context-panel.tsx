'use client';

/**
 * G43（ADR-0295）：用户消息下方的「本轮提交资料」可展开回执面板（WP09）。
 *
 * 事实纪律：
 * - 摘要来自 SSE turn_context_receipt / 消息恢复；完整源清单展开时按
 *   session+turn 拉取真实持久回执（不事后重算）；
 * - 状态语义诚实：not_sent=装配后未发出；submitted+queued≠已消费；
 *   handled≠已运行；unknown=未确证；legacy/缺失 = coverage limited；
 * - tokens 标注为估算（不是供应商计费）；「排除」不声称历史已遗忘。
 */
import React, { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronUp, FileClock, RefreshCw } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import {
  fetchTurnReceipt,
  type ReceiptSourceLine,
  type TurnContextReceiptSummary,
  type TurnReceiptDetail,
} from '@/lib/api/turnContext';

const DECISION_KEYS: Record<string, string> = {
  included: 'chat.turnReceipt.decisionIncluded',
  preferred: 'chat.turnReceipt.decisionPreferred',
  excluded_by_policy: 'chat.turnReceipt.decisionExcluded',
  omitted_budget: 'chat.turnReceipt.decisionOmitted',
  omitted_card_cap: 'chat.turnReceipt.decisionOmitted',
  not_live: 'chat.turnReceipt.decisionNotLive',
};

function statusKey(status: string): string {
  switch (status) {
    case 'submitted': return 'chat.turnReceipt.statusSubmitted';
    case 'not_sent': return 'chat.turnReceipt.statusNotSent';
    case 'unknown': return 'chat.turnReceipt.statusUnknown';
    default: return 'chat.turnReceipt.statusAssembled';
  }
}

export function TurnContextPanel({
  sessionId,
  ownerToken,
  summary,
}: {
  sessionId: string | null;
  ownerToken?: string | null;
  summary: TurnContextReceiptSummary;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<TurnReceiptDetail | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!sessionId || !summary.turn_id) return;
    setLoading(true);
    try {
      const d = await fetchTurnReceipt(sessionId, summary.turn_id, ownerToken);
      setDetail(d);
    } finally {
      setLoading(false);
    }
  }, [sessionId, summary.turn_id, ownerToken]);

  useEffect(() => {
    if (open && !detail && !loading) void load();
  }, [open, detail, loading, load]);

  const counts = summary.counts ?? {};
  const sources: ReceiptSourceLine[] = detail?.payload?.sources ?? [];
  const mode = summary.mode || 'typed';
  const coverageLimited = mode === 'legacy' || summary.persisted === false;

  return (
    <div
      className="mt-1 flex flex-col items-end gap-0.5"
      data-testid={`turn-context-receipt-${summary.turn_id}`}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="inline-flex items-center gap-1 rounded-pill border border-edge-subtle px-2 py-0.5 text-[11px] text-ink-muted hover:bg-surface-raised"
        data-testid="turn-context-receipt-toggle"
      >
        <FileClock size={11} aria-hidden />
        <span>{t('chat.turnReceipt.toggleLabel')}</span>
        <span
          className="font-mono"
          data-testid="turn-context-receipt-status"
          data-status={summary.status}
          data-disposition={summary.disposition ?? ''}
          title={t(statusKey(summary.status))}
        >
          {t(statusKey(summary.status))}
          {summary.disposition ? `·${summary.disposition}` : ''}
        </span>
        {typeof counts.sources_total === 'number' && (
          <span className="text-ink-faint">{counts.sources_total}</span>
        )}
        {open ? <ChevronUp size={11} aria-hidden /> : <ChevronDown size={11} aria-hidden />}
      </button>
      {open && (
        <div
          className="w-full max-w-[92%] rounded-md border border-edge-subtle bg-surface-raised px-2.5 py-2 text-[11.5px] leading-5 text-ink-standard"
          data-testid="turn-context-receipt-detail"
        >
          <div className="mb-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-ink-muted">
            <span>
              {t('chat.turnReceipt.turn')}
              <span className="font-mono"> {summary.turn_id.slice(0, 12)}</span>
            </span>
            <span>
              {t('chat.turnReceipt.policy')}
              <span className="font-mono"> v{summary.policy_version}</span>
            </span>
            {summary.digest && (
              <span className="font-mono" title={summary.digest}>
                #{summary.digest.slice(0, 8)}
              </span>
            )}
            <span className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => void load()}
                className="inline-flex items-center gap-0.5 hover:text-ink-standard"
                aria-label={t('chat.turnReceipt.refresh')}
                data-testid="turn-context-receipt-refresh"
              >
                <RefreshCw size={10} aria-hidden />
              </button>
            </span>
          </div>

          <div className="mb-1 flex flex-wrap gap-x-3 text-ink-muted">
            <span>
              {t('chat.turnReceipt.countIncluded')}: <b className="text-ink-standard">{counts.included ?? '—'}</b>
            </span>
            <span>
              {t('chat.turnReceipt.countOmitted')}: <b className="text-ink-standard">{counts.omitted ?? '—'}</b>
            </span>
            <span>
              {t('chat.turnReceipt.countTruncated')}: <b className="text-ink-standard">{counts.truncated ?? '—'}</b>
            </span>
          </div>

          {summary.status === 'not_sent' && (
            <p className="mb-1 text-warning" data-testid="turn-context-receipt-notsent">
              {t('chat.turnReceipt.notSentNote')}
              {summary.not_sent_reason ? ` (${summary.not_sent_reason})` : ''}
            </p>
          )}
          {summary.status === 'submitted' && summary.disposition === 'queued' && (
            <p className="mb-1 text-ink-muted">{t('chat.turnReceipt.queuedNote')}</p>
          )}
          {summary.status === 'submitted' && summary.disposition === 'handled' && (
            <p className="mb-1 text-warning">{t('chat.turnReceipt.handledNote')}</p>
          )}
          {summary.status === 'unknown' && (
            <p className="mb-1 text-warning">{t('chat.turnReceipt.unknownNote')}</p>
          )}
          {coverageLimited && (
            <p className="mb-1 text-ink-muted">{t('chat.turnReceipt.coverageLimited')}</p>
          )}

          {loading && !detail && (
            <p className="text-ink-muted">{t('chat.turnReceipt.loading')}</p>
          )}
          {!loading && !detail && (
            <p className="text-ink-muted" data-testid="turn-context-receipt-missing">
              {t('chat.turnReceipt.missingDetail')}
            </p>
          )}
          {detail && (
            <div>
              <p className="mb-0.5 font-medium text-ink-standard">
                {t('chat.turnReceipt.sourcesTitle')}
              </p>
              {sources.length === 0 && (
                <p className="text-ink-muted">{t('chat.turnReceipt.noSources')}</p>
              )}
              <ul className="max-h-44 overflow-y-auto" data-testid="turn-context-receipt-sources">
                {sources.map((s) => (
                  <li key={s.source_id} className="flex flex-wrap items-baseline gap-1">
                    <span className="font-mono text-ink-muted">{s.source_id.slice(0, 40)}</span>
                    {s.version_token && (
                      <span className="font-mono text-ink-faint" title={s.version_token}>
                        @{s.version_token.slice(0, 16)}
                      </span>
                    )}
                    <span className={s.decision === 'excluded_by_policy' ? 'text-ink-faint' : ''}>
                      {t(DECISION_KEYS[s.decision] ?? 'chat.turnReceipt.decisionIncluded')}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-ink-faint">{t('chat.turnReceipt.estimateNote')}</p>
              <p className="text-ink-faint">{t('chat.turnReceipt.historyNote')}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
