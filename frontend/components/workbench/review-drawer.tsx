'use client';

/**
 * ReviewDrawer（ADR-0203）—— 空间审查/会签面板：proposal 列表 → 详情
 * （锚定评论 / 锚态 / 冲突 / 策略 verdict）→ 批准 / 请求修改 / 拒绝 /
 * 合并 / rebase / 撤回。权威在服务端；本组件只做投影与动作回执展示。
 */
import { useCallback, useEffect, useSyncExternalStore, useRef, useState } from 'react';
import { CheckCircle2, GitBranch, MessageSquarePlus, ShieldCheck, X, XCircle } from 'lucide-react';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import {
  getReviewSnapshot,
  getReviewState,
  reviewSetDetail,
  reviewSetSession,
  reviewSetMergeOutcome,
  reviewSetProposals,
  reviewSetSelected,
  reviewSetStatus,
  subscribeReview,
} from '@/lib/review/store';
import {
  getReviewProposalDetail,
  listReviewProposals,
  reviewAction,
} from '@/lib/review/api';
import type { ReviewMergeOutcome, ReviewProposalDetail } from '@/lib/review/store';
import { ApiError } from '@/lib/api/transport';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { useT } from '@/lib/i18n/useT';
import type { TranslateFn } from '@/lib/i18n/translator';

const STATUS_KEYS = [
  'draft', 'submitted', 'changes_requested', 'approved', 'rejected',
  'merged', 'superseded', 'withdrawn',
] as const;

/**
 * API-08（跨分支）：merge 未成功时服务端改返 409（conflict/interleaved/
 * rolled_back/already_merged）/422（引擎失败），不再以 200 + ok=false 回执。
 * apiFetch 对非 2xx 抛 ApiError，投影保留在 body —— 统一信封的 `data`、
 * legacy `detail`（dict）或顶层。取回投影 + merge_outcome，让既有冲突/
 * 交错文案不降级成通用 HTTP 错误。
 */
function extractMergeFailure(
  err: unknown,
): { detail: ReviewProposalDetail; outcome: ReviewMergeOutcome } | null {
  if (!(err instanceof ApiError) || (err.status !== 409 && err.status !== 422)) return null;
  const body = err.body;
  if (body == null || typeof body !== 'object') return null;
  const record = body as Record<string, unknown>;
  for (const candidate of [record.data, record.detail, record]) {
    if (candidate == null || typeof candidate !== 'object') continue;
    const projection = candidate as Record<string, unknown>;
    const outcome = projection.merge_outcome;
    const proposal = projection.proposal;
    if (outcome == null || typeof outcome !== 'object') continue;
    if (proposal == null || typeof proposal !== 'object') continue;
    return {
      detail: projection as unknown as ReviewProposalDetail,
      outcome: outcome as ReviewMergeOutcome,
    };
  }
  return null;
}

function mergeOutcomeMessage(outcome: ReviewMergeOutcome, t: TranslateFn): string {
  if (outcome.conflict) return t('errorBaseDrifted');
  if (outcome.interleaved) return t('errorInterleaved');
  return outcome.failure ?? t('errorMergeFailed');
}

interface ReviewDrawerProps {
  open: boolean;
  onClose: () => void;
}

export function ReviewDrawer({ open, onClose }: ReviewDrawerProps) {
  const t = useT('review');
  useSyncExternalStore(subscribeReview, getReviewSnapshot, getReviewSnapshot);
  const state = getReviewState();
  const [commentDraft, setCommentDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  useDialogFocus({ open, containerRef: panelRef, onEscape: onClose });

  const refreshList = useCallback(async () => {
    // 以 cursor 为准绑定会话（review R1-P3：切换会话后不残留旧列表/选中）。
    const { sessionId: sid } = getMapSpecSessionCursor();
    if (sid == null) return;
    reviewSetSession(sid);
    reviewSetStatus('loading');
    try {
      const items = await listReviewProposals();
      reviewSetProposals(sid, items);
    } catch {
      reviewSetStatus('error');
    }
  }, []);

  const openDetail = useCallback(async (proposalId: string) => {
    reviewSetSelected(proposalId);
    try {
      const detail = await getReviewProposalDetail(proposalId);
      reviewSetDetail(detail);
    } catch {
      setError(t('errorDetailFailed'));
    }
  }, [t]);

  useEffect(() => {
    if (open) void refreshList();
  }, [open, refreshList]);

  const act = useCallback(
    async (action: 'submit' | 'decisions' | 'merge' | 'rebase' | 'withdraw', body?: Record<string, unknown>) => {
      const id = state.selectedId;
      if (id == null) return;
      setError(null);
      try {
        const detail = await reviewAction(id, action, body);
        reviewSetDetail(detail);
        const outcome = detail.merge_outcome;
        if (outcome != null) {
          reviewSetMergeOutcome(outcome);
          if (!outcome.ok) setError(mergeOutcomeMessage(outcome, t));
        }
        const items = await listReviewProposals();
        reviewSetProposals(state.sessionId ?? '', items);
      } catch (err) {
        const failure = extractMergeFailure(err);
        if (failure) {
          // 409/422：投影随错误体返回 —— 复用成功路径的回执投影与文案。
          reviewSetDetail(failure.detail);
          reviewSetMergeOutcome(failure.outcome);
          if (!failure.outcome.ok) setError(mergeOutcomeMessage(failure.outcome, t));
          try {
            const items = await listReviewProposals();
            reviewSetProposals(state.sessionId ?? '', items);
          } catch {
            // 列表刷新是尽力而为：合并回执已披露，不让列表失败吞掉错误文案。
          }
        } else {
          setError(err instanceof Error ? err.message : t('errorActionFailed'));
        }
      }
    },
    [state.selectedId, state.sessionId, t],
  );

  const addComment = useCallback(async () => {
    const id = state.selectedId;
    const bodyText = commentDraft.trim();
    if (id == null || !bodyText) return;
    setError(null);
    try {
      const detail = await reviewAction(id, 'comments', { body: bodyText });
      reviewSetDetail(detail);
      setCommentDraft('');
    } catch (err) {
      setError(err instanceof Error ? err.message : t('errorCommentFailed'));
    }
  }, [commentDraft, state.selectedId, t]);

  if (!open) return null;
  const detail = state.detail;
  const proposal = detail?.proposal as Record<string, unknown> | undefined;
  const status = String(proposal?.status ?? '');
  const canDecide = status === 'submitted';
  const canMerge = status === 'approved' && (detail?.conflict === false);
  const staleAnchors = detail
    ? Object.values(detail.anchor_states).filter((s) => s === 'stale').length
    : 0;

  return (
    <div className="fixed inset-0 z-50 flex" data-testid="review-drawer">
      <div className="flex-1 bg-surface-scrim" onClick={onClose} aria-hidden="true" />
      <div
        ref={panelRef}
        role="dialog"
        aria-label={t('drawerAria')}
        className="flex w-[420px] flex-col border-l bg-surface-base"
      >
        <div className="flex items-center justify-between border-b px-4 py-3">
          <div className="flex items-center gap-2 font-medium">
            <ShieldCheck className="h-4 w-4" aria-hidden="true" />
            <span>{t('title')}</span>
          </div>
          <button onClick={onClose} aria-label={t('closeAria')} data-testid="review-close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {error != null && (
          <div role="alert" className="bg-danger-surface px-4 py-2 text-sm text-danger-fg" data-testid="review-error">
            {error}
          </div>
        )}

        <div className="flex-1 overflow-auto">
          <ul data-testid="review-list">
            {state.proposals.map((p) => (
              <li key={p.proposal_id}>
                <button
                  className={`w-full px-4 py-2 text-left hover:bg-surface-hover ${state.selectedId === p.proposal_id ? 'bg-surface-active' : ''}`}
                  onClick={() => void openDetail(p.proposal_id)}
                >
                  <div className="flex items-center justify-between">
                    <span className="truncate text-sm">{p.title}</span>
                    <span className="ml-2 shrink-0 text-xs" data-status={p.status}>
                      {(STATUS_KEYS as readonly string[]).includes(p.status) ? t(`status.${p.status}`) : p.status}
                    </span>
                  </div>
                  <div className="text-xs opacity-60">
                    {t('metaLine', { risk: p.risk === 'high' ? t('riskHigh') : '', author: p.author.actor_kind === 'agent' ? t('authorAgent') : t('authorUser'), rev: p.base_revision })}
                  </div>
                </button>
              </li>
            ))}
            {state.proposals.length === 0 && (
              <li className="px-4 py-6 text-center text-sm opacity-60">{t('empty')}</li>
            )}
          </ul>

          {detail != null && proposal != null && (
            <div className="border-t px-4 py-3" data-testid="review-detail">
              <div className="text-sm font-medium">{String(proposal.title)}</div>
              {detail.conflict && (
                <div className="mt-1 text-xs text-danger-fg" data-testid="review-conflict">
                  {t('conflictChanged', { from: detail.base_revision, to: detail.current_revision })}
                </div>
              )}
              {staleAnchors > 0 && (
                <div className="mt-1 text-xs text-danger-fg" data-testid="review-stale">
                  {t('staleCount', { count: staleAnchors })}
                </div>
              )}
              <div className="mt-1 text-xs opacity-60">
                {t('policyLine', { state: detail.policy.satisfied ? t('policySatisfied') : t('policyNotSatisfied'), votes: detail.policy.counted_approvals })}
                {!detail.policy.satisfied && detail.policy.blocking_reasons.length > 0 && (
                  <span> · {detail.policy.blocking_reasons.join('; ')}</span>
                )}
              </div>

              <ul className="mt-2 space-y-1" data-testid="review-comments">
                {(Array.isArray(proposal.comments) ? proposal.comments as Array<Record<string, unknown>> : []).map((c) => {
                  const cid = String(c.comment_id);
                  const anchorState = detail.anchor_states[cid];
                  return (
                    <li key={cid} className="rounded bg-surface-raised px-2 py-1 text-xs">
                      <span className="opacity-60">{String((c.author as Record<string, unknown>)?.actor_id ?? '?')}:</span>
                      {' '}{String(c.body)}
                      {anchorState === 'stale' && <span className="ml-1 text-danger-fg">{t('anchorStale')}</span>}
                      {anchorState === 'unverified' && <span className="ml-1 opacity-50">{t('anchorUnverified')}</span>}
                    </li>
                  );
                })}
              </ul>

              <div className="mt-2 flex gap-1">
                <input
                  className="flex-1 rounded border bg-surface-base px-2 py-1 text-xs"
                  placeholder={t('commentPlaceholder')}
                  value={commentDraft}
                  onChange={(e) => setCommentDraft(e.target.value)}
                  data-testid="review-comment-input"
                />
                <button
                  className="rounded border px-2 py-1 text-xs"
                  onClick={() => void addComment()}
                  aria-label={t('addCommentAria')}
                >
                  <MessageSquarePlus className="h-3 w-3" />
                </button>
              </div>

              <div className="mt-3 flex flex-wrap gap-2" data-testid="review-actions">
                {status === 'draft' && (
                  <button
                    className="rounded border px-2 py-1 text-xs"
                    onClick={() => void act('submit', { base_revision: detail.current_revision })}
                    data-testid="review-submit"
                  >
                    {t('actionSubmit')}
                  </button>
                )}
                {canDecide && (
                  <>
                    <button
                      className="rounded border px-2 py-1 text-xs text-success-fg"
                      onClick={() => void act('decisions', { decision: 'approve' })}
                      data-testid="review-approve"
                    >
                      <CheckCircle2 className="mr-1 inline h-3 w-3" />{t('actionApprove')}
                    </button>
                    <button
                      className="rounded border px-2 py-1 text-xs"
                      onClick={() => void act('decisions', { decision: 'request_changes' })}
                    >
                      {t('actionRequestChanges')}
                    </button>
                    <button
                      className="rounded border px-2 py-1 text-xs text-danger-fg"
                      onClick={() => void act('decisions', { decision: 'reject' })}
                    >
                      <XCircle className="mr-1 inline h-3 w-3" />{t('actionReject')}
                    </button>
                  </>
                )}
                {status === 'approved' && detail.conflict && (
                  <button
                    className="rounded border px-2 py-1 text-xs"
                    onClick={() => void act('rebase')}
                    data-testid="review-rebase"
                  >
                    <GitBranch className="mr-1 inline h-3 w-3" />Rebase
                  </button>
                )}
                {canMerge && (
                  <button
                    className="rounded bg-primary-surface px-2 py-1 text-xs"
                    onClick={() => void act('merge')}
                    data-testid="review-merge"
                  >
                    {t('actionMerge')}
                  </button>
                )}
                {status !== 'merged' && status !== 'withdrawn' && (
                  <button
                    className="rounded border px-2 py-1 text-xs opacity-70"
                    onClick={() => void act('withdraw')}
                  >
                    {t('actionWithdraw')}
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default ReviewDrawer;
