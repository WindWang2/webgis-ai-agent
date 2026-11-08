'use client';

/**
 * G10 项目数据刷新面板 —— 真实用户旅程入口。
 *
 * 检查更新 → 预览影响（只读）→ 生成隔离候选 → 候选对比 → 采纳 / 放弃 / 回退。
 * 按钮明确区分「只计算」与「改变项目当前版本」；破坏性动作经 ConfirmDialog。
 * 刷新页面后从服务端 proposal/status 恢复候选与进度（localStorage 非权威）。
 *
 * 三态分离：工具执行 ≠ 任务执行 ≠ 地图已显示 ≠ 版本已采纳。
 * 未做真实浏览器渲染观察时显式展示 map_observation=unverified，绝不伪造 ACK。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, RefreshCw, GitCompare, Check, X, Undo2 } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import { describeApiError } from '@/lib/api/transport';
import { ConfirmDialog } from '@/components/shared/confirm-dialog';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { EmptyState } from '@/components/shared/empty-state';
import { StatusBadge } from '@/components/shared/status-badge';
import {
  checkProjectSourceUpdates,
  previewProjectDataRefresh,
  listRefreshProposals,
  getRefreshProposal,
  getRefreshStatus,
  compareRefreshCandidate,
  executeRefreshCandidate,
  adoptRefreshCandidate,
  discardRefreshCandidate,
  rollbackRefreshCandidate,
  type CheckReceipt,
  type RefreshProposal,
  type RefreshCompare,
  type RefreshStatus,
  type AdoptionReceipt,
} from '@/lib/api/project-data-refresh';

export interface DataRefreshPanelProps {
  projectId: string;
  sessionId?: string | null;
  authed: boolean;
}

type Stage = 'check' | 'preview' | 'execute' | 'adopt' | 'discard' | 'rollback' | null;
type PendingAction = 'adopt' | 'discard' | 'rollback' | null;

export function DataRefreshPanel({ projectId, sessionId, authed }: DataRefreshPanelProps) {
  const t = useT('project');
  const [check, setCheck] = useState<CheckReceipt | null>(null);
  const [proposal, setProposal] = useState<RefreshProposal | null>(null);
  const [comparison, setComparison] = useState<RefreshCompare | null>(null);
  const [status, setStatus] = useState<RefreshStatus | null>(null);
  const [receipt, setReceipt] = useState<AdoptionReceipt | null>(null);
  const [busy, setBusy] = useState<Stage>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<PendingAction>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 刷新页面后从服务端恢复最近的提案与候选进度
  useEffect(() => {
    if (!projectId || !authed) return;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    void (async () => {
      try {
        const list = await listRefreshProposals(projectId, ctrl.signal);
        if (ctrl.signal.aborted || !list.items.length) return;
        const latest = list.items[0];
        const prop = await getRefreshProposal(projectId, latest.proposal_id, ctrl.signal);
        const st = await getRefreshStatus(projectId, latest.proposal_id, ctrl.signal);
        if (ctrl.signal.aborted) return;
        setProposal(prop);
        setStatus(st);
        if (st.candidate_status !== 'not_started') {
          const cmp = await compareRefreshCandidate(projectId, latest.proposal_id, ctrl.signal);
          if (!ctrl.signal.aborted) setComparison(cmp);
        }
      } catch (err) {
        if (!ctrl.signal.aborted) setError(describeApiError(err, t('g10LoadFailed')));
      }
    })();
    return () => ctrl.abort();
  }, [projectId, authed, t]);

  const run = useCallback(
    async (stage: Exclude<Stage, null>, fn: () => Promise<void>) => {
      setBusy(stage);
      setError(null);
      setNotice(null);
      try {
        await fn();
      } catch (err) {
        setError(describeApiError(err, t('g10LoadFailed')));
      } finally {
        setBusy(null);
      }
    },
    [t],
  );

  const onCheck = () => void run('check', async () => {
    if (!sessionId) throw new Error(t('g10NeedSession'));
    const result = await checkProjectSourceUpdates(projectId, sessionId, true);
    setCheck(result);
    setNotice(result.inventory.warnings[0] ?? null);
  });

  const onPreview = () => void run('preview', async () => {
    if (!sessionId) throw new Error(t('g10NeedSession'));
    const prop = await previewProjectDataRefresh(projectId, sessionId);
    setProposal(prop);
    setComparison(null);
    setReceipt(null);
    setStatus(await getRefreshStatus(projectId, prop.proposal_id));
  });

  const onExecute = () => void run('execute', async () => {
    if (!proposal) return;
    const result = await executeRefreshCandidate(projectId, proposal.proposal_id);
    setComparison(await compareRefreshCandidate(projectId, proposal.proposal_id));
    setStatus(await getRefreshStatus(projectId, proposal.proposal_id));
    setNotice(result.replayed ? t('g10Replayed') : t('g10CandidateReady'));
  });

  const applyAction = (action: Exclude<PendingAction, null>) =>
    void run(action, async () => {
      if (!proposal) return;
      let r: AdoptionReceipt;
      if (action === 'adopt') {
        r = await adoptRefreshCandidate(projectId, proposal.proposal_id, {
          expectedRevision: proposal.mapspec_revision,
          proposalFingerprint: proposal.proposal_fingerprint,
        });
      } else if (action === 'discard') {
        r = await discardRefreshCandidate(projectId, proposal.proposal_id);
      } else {
        r = await rollbackRefreshCandidate(projectId, proposal.proposal_id);
      }
      setReceipt(r);
      setStatus(await getRefreshStatus(projectId, proposal.proposal_id));
      setNotice(r.warnings[0] ?? null);
      setConfirm(null);
    });

  if (!authed) return <InlineNotice variant="info">{t('g10NeedAuth')}</InlineNotice>;
  if (!sessionId) return <InlineNotice variant="info">{t('g10NeedSession')}</InlineNotice>;

  const diffs = proposal?.diffs ?? [];
  const firstCounts = diffs[0]?.exact_counts ?? null;
  const plan = proposal?.plan ?? null;

  return (
    <div className="space-y-2" data-testid="g10-data-refresh-panel">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <h3 className="text-micro font-semibold text-ink-primary">{t('g10Title')}</h3>
          {check?.stale ? <StatusBadge status="stale" label={t('g10Stale')} /> : null}
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="h-control-sm rounded-sm border border-edge-subtle px-2 text-micro text-ink-secondary hover:bg-surface-raised"
            onClick={onCheck}
            disabled={busy !== null}
            data-testid="g10-check-updates"
          >
            <RefreshCw size={11} aria-hidden className="mr-1 inline" />
            {busy === 'check' ? t('g10Checking') : t('g10Check')}
          </button>
          <button
            type="button"
            className="h-control-sm rounded-sm border border-edge-subtle px-2 text-micro text-ink-secondary hover:bg-surface-raised"
            onClick={onPreview}
            disabled={busy !== null}
            data-testid="g10-preview"
          >
            {busy === 'preview' ? t('g10Previewing') : t('g10Preview')}
          </button>
        </div>
      </div>

      {busy ? <LoadingState label={t('g10Busy')} /> : null}
      {error ? <InlineNotice variant="error">{error}</InlineNotice> : null}
      {notice ? <InlineNotice variant="info">{notice}</InlineNotice> : null}

      {check ? (
        <section className="rounded-sm border border-edge-subtle p-2" aria-label={t('g10Sources')}>
          <p className="text-micro text-ink-secondary" data-testid="g10-check-summary">
            {t('g10ChangedCount', {
              changed: check.changed, unchanged: check.unchanged,
              unverifiable: check.unverifiable,
            })}
          </p>
          {check.truncated ? (
            <p className="text-micro text-ink-muted">{t('g10Truncated')}</p>
          ) : null}
          <ul className="mt-1 max-h-32 space-y-0.5 overflow-auto" data-testid="g10-source-list">
            {check.inventory.items.map((item) => (
              <li key={item.logical_id} className="flex items-center justify-between gap-2 text-micro">
                <span className="truncate text-ink-primary" title={item.logical_id}>
                  {item.display_name || item.logical_id}
                </span>
                <span className="shrink-0 text-ink-muted">
                  {item.verdict} · {item.evidence}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {!proposal ? (
        <EmptyState
          icon={GitCompare}
          title={t('g10NoChanges')}
          description={t('g10NoProposal')}
        />
      ) : (
        <section className="space-y-2 rounded-sm border border-edge-subtle p-2" aria-label={t('g10Preview')}>
          <div className="flex items-center justify-between">
            <h4 className="text-micro font-semibold text-ink-primary">{t('g10Diff')}</h4>
            <span className="text-micro text-ink-muted" title={proposal.proposal_fingerprint}>
              {proposal.proposal_id.slice(0, 16)}
            </span>
          </div>
          {firstCounts ? (
            <p className="text-micro text-ink-secondary" data-testid="g10-diff-counts">
              {t('g10DiffCounts', {
                added: firstCounts.added, removed: firstCounts.removed,
                attr: firstCounts.attribute_changed, geom: firstCounts.geometry_changed,
              })}
            </p>
          ) : (
            <p className="text-micro text-ink-muted">{t('g10Unverifiable')}</p>
          )}
          {diffs.some((d) => d.evidence !== 'exact') ? (
            <InlineNotice variant="warning">{t('g10Unverifiable')}</InlineNotice>
          ) : null}

          {plan ? (
            <div className="space-y-1">
              <p className="text-micro text-ink-secondary" data-testid="g10-recompute-plan">
                {t('g10RecomputeNodes', { count: plan.recompute.length })} ·{' '}
                {t('g10ReuseNodes', { count: plan.reuse.length })}
              </p>
              {plan.conservative ? (
                <InlineNotice variant="warning">{t('g10Conservative')}</InlineNotice>
              ) : null}
              {plan.style_only ? (
                <InlineNotice variant="info">{t('g10StyleOnly')}</InlineNotice>
              ) : null}
              <p className="text-micro text-ink-muted">
                {t('g10Preserved')}: {plan.preserved_user_edits.length}
              </p>
              {plan.conflicts.length ? (
                <InlineNotice variant="warning">
                  {t('g10Conflicts')}: {plan.conflicts.length}
                </InlineNotice>
              ) : null}
            </div>
          ) : null}
          <p className="text-micro text-ink-muted">{t('g10ReadonlyNote')}</p>
        </section>
      )}

      {proposal ? (
        <section className="space-y-1.5 rounded-sm border border-edge-subtle p-2" aria-label={t('g10Candidate')}>
          <p className="text-micro text-ink-secondary" data-testid="g10-candidate-status">
            {t('g10Status')}: {status?.candidate_status ?? 'not_started'} ·{' '}
            {t('g10Attempts', { count: status?.candidate_attempts ?? 0 })}
          </p>
          {comparison ? (
            <div className="space-y-1">
              <p className="text-micro text-ink-secondary">
                {t('g10Compare')}: {comparison.candidate_status}
              </p>
              {comparison.preserved_user_edits.length ? (
                <p className="text-micro text-ink-muted">
                  {t('g10Preserved')}: {comparison.preserved_user_edits.join(', ')}
                </p>
              ) : null}
              {comparison.conflicts.length ? (
                <InlineNotice variant="warning">
                  {t('g10Conflicts')}: {comparison.conflicts.length}
                </InlineNotice>
              ) : null}
              {!comparison.adoptable ? (
                <InlineNotice variant="warning">{t('g10NotAdoptable')}</InlineNotice>
              ) : null}
            </div>
          ) : null}
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              className="h-control-sm rounded-sm border border-edge-subtle px-2 text-micro hover:bg-surface-raised"
              onClick={onExecute}
              disabled={busy !== null}
              data-testid="g10-execute"
            >
              {busy === 'execute' ? t('g10Executing') : t('g10Execute')}
            </button>
            <button
              type="button"
              className="h-control-sm rounded-sm border border-edge-subtle px-2 text-micro hover:bg-surface-raised"
              onClick={() => setConfirm('adopt')}
              disabled={busy !== null || !comparison?.candidate_available}
              data-testid="g10-adopt"
            >
              <Check size={11} aria-hidden className="mr-1 inline" />{t('g10Adopt')}
            </button>
            <button
              type="button"
              className="h-control-sm rounded-sm border border-edge-subtle px-2 text-micro hover:bg-surface-raised"
              onClick={() => setConfirm('discard')}
              disabled={busy !== null}
              data-testid="g10-discard"
            >
              <X size={11} aria-hidden className="mr-1 inline" />{t('g10Discard')}
            </button>
            <button
              type="button"
              className="h-control-sm rounded-sm border border-edge-subtle px-2 text-micro hover:bg-surface-raised"
              onClick={() => setConfirm('rollback')}
              disabled={busy !== null || !receipt || receipt.status !== 'adopted'}
              data-testid="g10-rollback"
            >
              <Undo2 size={11} aria-hidden className="mr-1 inline" />{t('g10Rollback')}
            </button>
          </div>
          <p className="text-micro text-ink-muted">{t('g10AdoptWarning')}</p>
          {receipt ? (
            <div className="space-y-0.5" data-testid="g10-adoption-receipt">
              <p className="text-micro text-ink-secondary">
                {t('g10ReceiptStatus', { status: receipt.status })}
              </p>
              <p className="text-micro text-ink-muted">
                {t('g10ThreeStates', {
                  tool: receipt.tool_execution, task: receipt.task_execution,
                  map: receipt.map_observation, version: receipt.version_adoption,
                })}
              </p>
              {receipt.map_observation === 'unverified' ? (
                <p className="text-micro text-ink-muted">{t('g10MapUnverified')}</p>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      <ConfirmDialog
        open={confirm !== null}
        title={confirm === 'adopt' ? t('g10ConfirmAdopt')
          : confirm === 'discard' ? t('g10ConfirmDiscard') : t('g10ConfirmRollback')}
        description={confirm === 'adopt' ? t('g10AdoptWarning') : undefined}
        confirmLabel={confirm === 'adopt' ? t('g10Adopt')
          : confirm === 'discard' ? t('g10Discard') : t('g10Rollback')}
        onCancel={() => setConfirm(null)}
        onConfirm={() => { if (confirm) applyAction(confirm); }}
      />
      <p className="flex items-center gap-1 text-micro text-ink-muted">
        <AlertTriangle size={10} aria-hidden /> {t('g10MapUnverified')}
      </p>
    </div>
  );
}
