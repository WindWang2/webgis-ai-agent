'use client';

/**
 * ReviewDrawer（ADR-0201）—— 空间审查/会签面板：proposal 列表 → 详情
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
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  submitted: '待审',
  changes_requested: '待修改',
  approved: '已批准',
  rejected: '已拒绝',
  merged: '已合并',
  superseded: '已废弃',
  withdrawn: '已撤回',
};

interface ReviewDrawerProps {
  open: boolean;
  onClose: () => void;
}

export function ReviewDrawer({ open, onClose }: ReviewDrawerProps) {
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
      setError('详情读取失败');
    }
  }, []);

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
          if (!outcome.ok && outcome.conflict) setError('base revision 已漂移：请 rebase 后重新会签');
          else if (!outcome.ok && outcome.interleaved) setError('合并期间有并发提交：已保护其工作，请 rebase 重试');
          else if (!outcome.ok) setError(outcome.failure ?? '合并失败');
        }
        const items = await listReviewProposals();
        reviewSetProposals(state.sessionId ?? '', items);
      } catch (err) {
        setError(err instanceof Error ? err.message : '操作失败');
      }
    },
    [state.selectedId, state.sessionId],
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
      setError(err instanceof Error ? err.message : '评论失败');
    }
  }, [commentDraft, state.selectedId]);

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
        aria-label="空间审查"
        className="flex w-[420px] flex-col border-l bg-surface-base"
      >
        <div className="flex items-center justify-between border-b px-4 py-3">
          <div className="flex items-center gap-2 font-medium">
            <ShieldCheck className="h-4 w-4" aria-hidden="true" />
            <span>空间审查 / 会签</span>
          </div>
          <button onClick={onClose} aria-label="关闭审查面板" data-testid="review-close">
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
                      {STATUS_LABEL[p.status] ?? p.status}
                    </span>
                  </div>
                  <div className="text-xs opacity-60">
                    {p.risk === 'high' ? '高风险 · ' : ''}{p.author.actor_kind === 'agent' ? 'Agent' : '用户'} · rev {p.base_revision}
                  </div>
                </button>
              </li>
            ))}
            {state.proposals.length === 0 && (
              <li className="px-4 py-6 text-center text-sm opacity-60">暂无提案</li>
            )}
          </ul>

          {detail != null && proposal != null && (
            <div className="border-t px-4 py-3" data-testid="review-detail">
              <div className="text-sm font-medium">{String(proposal.title)}</div>
              {detail.conflict && (
                <div className="mt-1 text-xs text-danger-fg" data-testid="review-conflict">
                  base revision 已变化（base {detail.base_revision} → 当前 {detail.current_revision}）
                </div>
              )}
              {staleAnchors > 0 && (
                <div className="mt-1 text-xs text-danger-fg" data-testid="review-stale">
                  {staleAnchors} 条评论锚点已失效（stale）
                </div>
              )}
              <div className="mt-1 text-xs opacity-60">
                策略：{detail.policy.satisfied ? '已满足' : '未满足'}
                （{detail.policy.counted_approvals} 票）
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
                      {anchorState === 'stale' && <span className="ml-1 text-danger-fg">（锚点 stale）</span>}
                      {anchorState === 'unverified' && <span className="ml-1 opacity-50">（锚点未验证）</span>}
                    </li>
                  );
                })}
              </ul>

              <div className="mt-2 flex gap-1">
                <input
                  className="flex-1 rounded border bg-surface-base px-2 py-1 text-xs"
                  placeholder="锚定评论…"
                  value={commentDraft}
                  onChange={(e) => setCommentDraft(e.target.value)}
                  data-testid="review-comment-input"
                />
                <button
                  className="rounded border px-2 py-1 text-xs"
                  onClick={() => void addComment()}
                  aria-label="添加评论"
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
                    提交审查
                  </button>
                )}
                {canDecide && (
                  <>
                    <button
                      className="rounded border px-2 py-1 text-xs text-success-fg"
                      onClick={() => void act('decisions', { decision: 'approve' })}
                      data-testid="review-approve"
                    >
                      <CheckCircle2 className="mr-1 inline h-3 w-3" />批准
                    </button>
                    <button
                      className="rounded border px-2 py-1 text-xs"
                      onClick={() => void act('decisions', { decision: 'request_changes' })}
                    >
                      请求修改
                    </button>
                    <button
                      className="rounded border px-2 py-1 text-xs text-danger-fg"
                      onClick={() => void act('decisions', { decision: 'reject' })}
                    >
                      <XCircle className="mr-1 inline h-3 w-3" />拒绝
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
                    合并到地图
                  </button>
                )}
                {status !== 'merged' && status !== 'withdrawn' && (
                  <button
                    className="rounded border px-2 py-1 text-xs opacity-70"
                    onClick={() => void act('withdraw')}
                  >
                    撤回
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
