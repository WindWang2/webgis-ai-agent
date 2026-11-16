'use client';

/**
 * GoalRepairPanel —— 任务完成卡下的有界补全面（G42）。
 *
 * 行为：
 *  - 挂载即拉取预览（无计划/已满足/无会话权 → 整面板不渲染，零漂移）；
 *  - 展示每个未兑现需求的补全动作与裁决（可执行/需确认损失/需资料/不支持）；
 *  - 启动（幂等，双击不重复执行）→ 2s 轮询 attempt → 取消（两阶段）→
 *    终态展示逐动作回执、真实产物下载与原终验前后 verdict；
 *  - reobserve 进行中向地图发受控重观察事件（既有 reconcile → POST 观察）。
 * 红线：本面板不判成功 —— final_verdict_after 是原 evaluator 的只读投影。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Download, Loader2, RefreshCw, XCircle } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import { isApiError } from '@/lib/api/transport';
import {
  cancelGoalRepairAttempt,
  fetchGoalRepairAttempt,
  fetchGoalRepairPreview,
  startGoalRepairAttempt,
} from '@/lib/api/goal-repair';
import {
  GOAL_REPAIR_REOBSERVE_EVENT,
  parseGoalRepairAttempt,
  parseGoalRepairPreview,
  type GoalRepairAttemptView,
  type RepairActionBinding,
} from '@/lib/types/goal-repair';

export interface GoalRepairPanelProps {
  sessionId: string;
  /** 仅在完成卡 verdict 非 satisfied 时由父层挂载。 */
  active: boolean;
}

const POLL_INTERVAL_MS = 2000;

function dispositionTone(disposition: string): string {
  switch (disposition) {
    case 'executable':
      return 'text-status-success';
    case 'needs_acknowledgment':
      return 'text-status-warning';
    default:
      return 'text-ink-muted';
  }
}

function actionStatusIcon(status: string) {
  if (status === 'succeeded') return <CheckCircle2 size={12} className="text-status-success" />;
  if (status === 'running' || status === 'pending') return <Loader2 size={12} className="animate-spin text-status-accent" />;
  if (status === 'failed') return <XCircle size={12} className="text-status-critical" />;
  return <AlertTriangle size={12} className="text-status-warning" />;
}

export function GoalRepairPanel({ sessionId, active }: GoalRepairPanelProps) {
  const t = useT();
  const [previewActions, setPreviewActions] = useState<RepairActionBinding[]>([]);
  const [blockers, setBlockers] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [acked, setAcked] = useState<Set<string>>(new Set());
  const [attempt, setAttempt] = useState<GoalRepairAttemptView | null>(null);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  const running = attempt?.active === true;

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (attemptId: string) => {
      stopPolling();
      pollRef.current = setInterval(() => {
        void (async () => {
          try {
            const raw = await fetchGoalRepairAttempt(attemptId);
            const view = parseGoalRepairAttempt(raw);
            if (!mountedRef.current) return;
            if (view) {
              setAttempt(view);
              if (!view.active) {
                stopPolling();
                // 终态后刷新预览（no_progress/新计划状态以服务端为准）。
                void (async () => {
                  try {
                    const fresh = parseGoalRepairPreview(await fetchGoalRepairPreview(sessionId));
                    if (fresh && mountedRef.current) {
                      setPreviewActions(fresh.actions);
                      setBlockers(fresh.blockers);
                    }
                  } catch {
                    /* 预览失败保留旧视图 */
                  }
                })();
              }
            }
          } catch {
            /* 轮询失败保留下一次重试 */
          }
        })();
      }, POLL_INTERVAL_MS);
    },
    [sessionId, stopPolling],
  );

  // 挂载：拉取预览；已有活跃 attempt（刷新/重连恢复）则直接续接轮询。
  useEffect(() => {
    mountedRef.current = true;
    if (!active || !sessionId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const raw = await fetchGoalRepairPreview(sessionId);
        const preview = parseGoalRepairPreview(raw);
        if (cancelled || !mountedRef.current) return;
        if (!preview) {
          setUnavailable(t('chat.goalRepair.unavailable'));
          return;
        }
        setPreviewActions(preview.actions);
        setBlockers(preview.blockers);
        setSelected(
          new Set(
            preview.actions
              .filter(
                (a) =>
                  a.disposition === 'executable' ||
                  a.disposition === 'needs_acknowledgment',
              )
              .map((a) => a.actionId),
          ),
        );
        if (preview.activeAttempt) {
          setAttempt(preview.activeAttempt);
          startPolling(preview.activeAttempt.attemptId);
        }
      } catch (err) {
        if (!cancelled && mountedRef.current) {
          // typed 语义（无计划/已满足/无权限）→ 面板收敛为不可用说明。
          setUnavailable(
            isApiError(err)
              ? t('chat.goalRepair.unavailable')
              : t('chat.goalRepair.error'),
          );
        }
      } finally {
        if (!cancelled && mountedRef.current) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      mountedRef.current = false;
      stopPolling();
    };
  }, [active, sessionId, startPolling, stopPolling, t]);

  // reobserve 动作进行中 → 请求地图受控重观察（每次轮询至多发一次）。
  useEffect(() => {
    if (!running) return;
    const hasReobserve = attempt?.actions.some((a) =>
      ['pending', 'running'].includes(a.status) && a.action === 'reobserve',
    );
    if (hasReobserve && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent(GOAL_REPAIR_REOBSERVE_EVENT));
    }
  }, [attempt, running]);

  const needsAckActions = useMemo(
    () => previewActions.filter((a) => a.disposition === 'needs_acknowledgment'),
    [previewActions],
  );

  const allAckChecked = useMemo(
    () =>
      needsAckActions
        .filter((a) => selected.has(a.actionId))
        .every((a) => (a.ackCodes ?? []).every((code) => acked.has(code))),
    [needsAckActions, selected, acked],
  );

  const handleToggle = useCallback((actionId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(actionId)) next.delete(actionId);
      else next.add(actionId);
      return next;
    });
  }, []);

  const handleAckToggle = useCallback((code: string) => {
    setAcked((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }, []);

  const handleStart = useCallback(() => {
    if (busy || running || selected.size === 0) return;
    // 损失确认是用户决策：任一选中交付动作的确认码未勾全 → 不启动。
    if (!allAckChecked) {
      setError(t('chat.goalRepair.ackRequired'));
      return;
    }
    setBusy(true);
    setError(null);
    void (async () => {
      try {
        const selectedActions = previewActions.filter((a) =>
          selected.has(a.actionId),
        );
        const ackCodes = Array.from(
          new Set(
            selectedActions.flatMap((a) =>
              a.disposition === 'needs_acknowledgment' ? a.ackCodes ?? [] : [],
            ),
          ),
        );
        const raw = await startGoalRepairAttempt(sessionId, {
          action_ids: Array.from(selected),
          ...(ackCodes.length ? { acknowledged_losses: ackCodes } : {}),
        });
        const view = parseGoalRepairAttempt(raw);
        if (view && mountedRef.current) {
          setAttempt(view);
          if (view.active) startPolling(view.attemptId);
        }
      } catch (err) {
        if (mountedRef.current) {
          let code = '';
          if (isApiError(err) && err.body && typeof err.body === 'object') {
            const detail = err.body as { code?: string; error?: string };
            code = detail.code ?? '';
            setError(
              `${detail.error ?? err.message}${code ? ` (${code})` : ''}`,
            );
          } else {
            setError(t('chat.goalRepair.error'));
          }
        }
      } finally {
        if (mountedRef.current) setBusy(false);
      }
    })();
  }, [allAckChecked, busy, previewActions, running, selected, sessionId, startPolling, t]);

  const handleCancel = useCallback(() => {
    if (!attempt?.active) return;
    setBusy(true);
    void (async () => {
      try {
        await cancelGoalRepairAttempt(attempt.attemptId);
      } catch {
        /* 取消幂等；失败保留下次轮询对账 */
      } finally {
        if (mountedRef.current) setBusy(false);
      }
    })();
  }, [attempt]);

  if (!active || !sessionId) return null;
  if (loading) {
    return (
      <div data-testid="goal-repair-panel-loading" className="my-1 flex items-center gap-1 text-meta text-ink-muted">
        <Loader2 size={12} className="animate-spin" />
        {t('chat.goalRepair.loading')}
      </div>
    );
  }
  if (unavailable) {
    return (
      <div data-testid="goal-repair-panel-unavailable" className="my-1 text-meta text-ink-muted">
        {unavailable}
      </div>
    );
  }
  if (previewActions.length === 0 && !attempt) return null;

  const terminal = attempt && !attempt.active ? attempt : null;

  return (
    <div
      data-testid="goal-repair-panel"
      role="region"
      aria-label={t('chat.goalRepair.title')}
      className="my-2 rounded-md border border-edge-subtle bg-surface-sunken p-3 text-body"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-title font-semibold text-ink">
          {t('chat.goalRepair.title')}
        </span>
        {running && (
          <span className="inline-flex items-center gap-1 text-meta text-status-accent">
            <Loader2 size={12} className="animate-spin" />
            {t('chat.goalRepair.running')}
          </span>
        )}
      </div>

      {/* 计划动作清单（启动前可选；运行/终态只读） */}
      {previewActions.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1.5" data-testid="goal-repair-actions">
          {previewActions.map((binding) => {
            const receipt = attempt?.actions.find(
              (r) => r.action_id === binding.actionId,
            );
            const checked = selected.has(binding.actionId);
            const disabled = running || busy || !!terminal;
            return (
              <li
                key={binding.actionId}
                data-testid="goal-repair-action"
                data-disposition={binding.disposition}
                className="rounded-md bg-surface-raised px-2 py-1.5"
              >
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={
                      disabled ||
                      binding.disposition === 'needs_user_input' ||
                      binding.disposition === 'unsupported' ||
                      binding.disposition === 'blocked'
                    }
                    onChange={() => handleToggle(binding.actionId)}
                    aria-label={t('chat.goalRepair.selectAction', {
                      requirement: binding.requirementId,
                    })}
                    className="h-3.5 w-3.5"
                  />
                  <span className="min-w-0 flex-1 truncate text-body text-ink" title={binding.requirementId}>
                    {binding.requirementId}
                  </span>
                  <span className={`text-meta ${dispositionTone(binding.disposition)}`}>
                    {t(`chat.goalRepair.disposition.${binding.disposition}`)}
                  </span>
                  {receipt && (
                    <span className="inline-flex items-center gap-1 text-meta text-ink">
                      {actionStatusIcon(receipt.status)}
                      {receipt.status}
                    </span>
                  )}
                </div>
                {binding.reason && (
                  <div className="mt-0.5 pl-6 text-meta text-ink-muted" title={binding.reason}>
                    {binding.reason}
                  </div>
                )}
                {binding.disposition === 'needs_acknowledgment' &&
                  (binding.ackCodes ?? []).map((code) => (
                    <label
                      key={code}
                      className="mt-1 flex items-center gap-1.5 pl-6 text-meta text-status-warning"
                      data-testid="goal-repair-ack"
                    >
                      <input
                        type="checkbox"
                        checked={acked.has(code)}
                        disabled={disabled}
                        onChange={() => handleAckToggle(code)}
                        className="h-3 w-3"
                      />
                      {t('chat.goalRepair.ackCode', { code })}
                    </label>
                  ))}
                {receipt?.download_url && (
                  <a
                    href={receipt.download_url}
                    className="mt-1 inline-flex items-center gap-1 pl-6 text-meta text-status-accent underline"
                    data-testid="goal-repair-download"
                    download
                  >
                    <Download size={12} />
                    {t('chat.goalRepair.download')}
                  </a>
                )}
                {receipt?.job_id && receipt.status === 'waiting_delivery' && (
                  <div className="mt-0.5 pl-6 text-meta text-ink-muted">
                    {t('chat.goalRepair.waitingDelivery', { jobId: receipt.job_id })}
                  </div>
                )}
                {receipt?.detail && (
                  <div className="mt-0.5 pl-6 text-meta text-ink-muted" title={receipt.detail}>
                    {receipt.detail}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {blockers.length > 0 && (
        <div className="mt-2 text-meta text-status-warning" data-testid="goal-repair-blockers">
          {t('chat.goalRepair.blockers', { codes: blockers.join(', ') })}
        </div>
      )}

      {error && (
        <div
          className="mt-2 text-meta text-status-critical"
          role="alert"
          data-testid="goal-repair-error"
        >
          {error}
        </div>
      )}

      {/* 启动 / 取消 */}
      {!terminal && (
        <div className="mt-2.5 flex items-center gap-2">
          <button
            type="button"
            onClick={handleStart}
            disabled={busy || running || selected.size === 0}
            data-testid="goal-repair-start"
            className="rounded-pill bg-status-accent px-3 py-1 text-meta font-medium text-white disabled:opacity-50"
          >
            {busy || running ? t('chat.goalRepair.busy') : t('chat.goalRepair.start')}
          </button>
          {running && (
            <button
              type="button"
              onClick={handleCancel}
              disabled={busy || !attempt?.cancellable}
              data-testid="goal-repair-cancel"
              className="rounded-pill border border-edge-subtle px-3 py-1 text-meta text-ink disabled:opacity-50"
            >
              {t('chat.goalRepair.cancel')}
            </button>
          )}
        </div>
      )}

      {/* 终态：原终验前后对照（原 evaluator 的只读投影） */}
      {terminal && (
        <div
          className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md bg-surface-raised px-2 py-1.5"
          data-testid="goal-repair-outcome"
          aria-live="polite"
        >
          <span className="inline-flex items-center gap-1 text-meta text-ink">
            <RefreshCw size={12} />
            {t('chat.goalRepair.verdictLine', {
              before: terminal.finalVerdictBefore ?? '—',
              after: terminal.finalVerdictAfter ?? terminal.finalVerdictBefore ?? '—',
            })}
          </span>
          <span
            className={`rounded-pill px-2 py-px text-meta ${
              terminal.finalVerdictAfter === 'satisfied'
                ? 'bg-status-success-soft text-status-success'
                : 'bg-status-warning-soft text-status-warning'
            }`}
            data-testid="goal-repair-final-verdict"
          >
            {terminal.finalVerdictAfter ?? terminal.finalVerdictBefore}
          </span>
          {terminal.outcome === 'no_progress' && (
            <span className="text-meta text-status-warning">
              {t('chat.goalRepair.noProgress')}
            </span>
          )}
          {terminal.outcome === 'outcome_unknown' && (
            <span className="text-meta text-status-critical">
              {t('chat.goalRepair.outcomeUnknown')}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
