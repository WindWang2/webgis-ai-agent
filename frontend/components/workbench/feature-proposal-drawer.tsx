'use client';

/**
 * FeatureProposalDrawer（G32）—— 持久要素变更提案审阅面：队列 → 详情
 * （三方差异分页 / 冲突逐项裁决 / base 证据）→ 批准 → 合并（真实回执）→
 * 撤回 / 恢复视图。权威在服务端；本组件只做投影与动作回执展示。
 *
 * 键盘/读屏：dialog focus 管理（Escape 关闭、焦点圈定）；冲突裁决用
 * fieldset+radio（组语义）；状态变化经 aria-live 播报。
 */
import { useCallback, useEffect, useMemo, useSyncExternalStore, useRef, useState } from 'react';
import { CheckCircle2, ClipboardList, GitBranch, ShieldAlert, X } from 'lucide-react';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import {
  getFeatureProposalsSnapshot,
  getFeatureProposalsState,
  proposalsSetList,
  proposalsSetSelected,
  proposalsSetSession,
  proposalsSetStatus,
  subscribeFeatureProposals,
  resetFeatureProposalsForTests,
} from '@/lib/proposals/store';
import {
  decideFeatureProposal,
  getFeatureProposalDetail,
  getFeatureProposalDiff,
  listFeatureProposals,
  mergeFeatureProposal,
  rebaseFeatureProposal,
  submitFeatureProposalResolutions,
  withdrawFeatureProposal,
  resolveRecovery,
} from '@/lib/api/feature-proposals';
import type {
  ProposalConflict,
  ProposalDetail,
  ProposalDiff,
  ProposalSummary,
} from '@/lib/api/feature-proposals';
import { ApiError } from '@/lib/api/transport';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { useT } from '@/lib/i18n/useT';

/** 只支持 keep_current 的冲突类型（与 rebase.apply_resolutions_to_ops 同步）。 */
const KEEP_ONLY_KINDS = new Set(['target_deleted', 'parent_retired', 'dependency_changed']);

const STATUS_ORDER: ProposalSummary['status'][] = [
  'recovery_required', 'merging', 'approved', 'changes_requested',
  'submitted', 'draft', 'merged', 'rejected', 'withdrawn',
];

function valueCell(v: { presence: string; value?: unknown } | undefined): string {
  if (v == null) return '—';
  if (v.presence === 'missing') return '(missing)';
  const raw = v.value;
  if (raw === null) return 'null';
  if (typeof raw === 'string') return raw;
  try {
    return JSON.stringify(raw);
  } catch {
    return String(raw);
  }
}

export function FeatureProposalDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT('featureProposals');
  useSyncExternalStore(subscribeFeatureProposals, getFeatureProposalsSnapshot, getFeatureProposalsSnapshot);
  const state = getFeatureProposalsState();
  const [detail, setDetail] = useState<ProposalDetail | null>(null);
  const [diff, setDiff] = useState<ProposalDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resolutions, setResolutions] = useState<Record<string, 'keep_current' | 'take_proposed'>>({});
  const panelRef = useRef<HTMLDivElement>(null);
  useDialogFocus({ open, containerRef: panelRef, onEscape: onClose });

  const refreshList = useCallback(async () => {
    const { sessionId: sid } = getMapSpecSessionCursor();
    if (sid == null) return;
    proposalsSetSession(sid);
    proposalsSetStatus('loading');
    try {
      const items = await listFeatureProposals();
      proposalsSetList(sid, items);
    } catch {
      proposalsSetStatus('error');
    }
  }, []);

  const loadDetail = useCallback(async (proposalId: string) => {
    setError(null);
    try {
      const [d, df] = await Promise.all([
        getFeatureProposalDetail(proposalId),
        getFeatureProposalDiff(proposalId),
      ]);
      setDetail(d);
      setDiff(df);
    } catch {
      setError(t('errorDetailFailed'));
    }
  }, [t]);

  useEffect(() => {
    if (open) void refreshList();
  }, [open, refreshList]);

  useEffect(() => {
    if (state.selectedId) void loadDetail(state.selectedId);
  }, [state.selectedId, loadDetail]);

  const act = useCallback(async (fn: () => Promise<unknown>, successNote?: string) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
      if (successNote) setNotice(successNote);
      if (state.selectedId) await loadDetail(state.selectedId);
      await refreshList();
    } catch (err) {
      if (err instanceof ApiError) {
        const bodyText = JSON.stringify(err.body ?? '').slice(0, 200);
        setError(`${err.status}: ${t('errorActionFailed')} (${bodyText})`);
      } else {
        setError(t('errorActionFailed'));
      }
    } finally {
      setBusy(false);
    }
  }, [busy, loadDetail, refreshList, state.selectedId, t]);

  const unresolved = useMemo(
    () => diff?.conflict_report?.unresolved_conflicts ?? [],
    [diff],
  );
  const verdict = diff?.conflict_report?.verdict;

  const submitResolutions = useCallback(() => {
    if (!detail || Object.keys(resolutions).length === 0) return;
    const payload = unresolved
      .filter((c) => resolutions[c.conflict_id])
      .map((c) => ({ conflict_id: c.conflict_id, action: resolutions[c.conflict_id] }));
    void act(
      () => submitFeatureProposalResolutions(detail.proposal_id, detail.revision, payload),
      t('noticeResolutionsApplied'),
    );
  }, [act, detail, resolutions, t, unresolved]);

  if (!open) return null;

  const selected = state.selectedId;

  return (
    <div
      className="fixed inset-y-0 right-0 z-40 flex w-[30rem] max-w-[92vw] flex-col border-l border-edge-subtle bg-surface-panel shadow-lg"
      data-testid="feature-proposal-drawer"
      role="dialog"
      aria-modal="true"
      aria-label={t('drawerTitle')}
      ref={panelRef}
    >
      <header className="flex items-center justify-between border-b border-edge-subtle px-3 py-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <ClipboardList size={16} aria-hidden />
          {selected ? (
            <button
              type="button"
              className="rounded-xs px-1 hover:bg-surface-hover"
              onClick={() => { proposalsSetSelected(null); setDetail(null); setDiff(null); setResolutions({}); }}
            >
              ← {t('backToList')}
            </button>
          ) : (
            t('drawerTitle')
          )}
        </div>
        <button type="button" onClick={onClose} aria-label={t('close')} className="rounded-xs p-1 hover:bg-surface-hover">
          <X size={16} aria-hidden />
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-3 py-2 text-sm" aria-live="polite">
        {error && (
          <div className="mb-2 rounded-xs border border-status-error/40 bg-status-error/10 px-2 py-1 text-micro" role="alert">
            {error}
          </div>
        )}
        {notice && (
          <div className="mb-2 rounded-xs border border-status-ok/40 bg-status-ok/10 px-2 py-1 text-micro" role="status">
            {notice}
          </div>
        )}

        {!selected && (
          <ul className="flex flex-col gap-1.5" data-testid="feature-proposal-list">
            {state.proposals.length === 0 && state.status === 'ready' && (
              <li className="text-micro text-text-subtle">{t('emptyList')}</li>
            )}
            {[...state.proposals]
              .sort((a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status) || b.updated_at - a.updated_at)
              .map((p) => (
                <li key={p.proposal_id}>
                  <button
                    type="button"
                    className="w-full rounded-xs border border-edge-subtle px-2 py-1.5 text-left hover:bg-surface-hover"
                    onClick={() => { proposalsSetSelected(p.proposal_id); setResolutions({}); }}
                    data-testid={`feature-proposal-item-${p.proposal_id}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium">{p.title}</span>
                      <span className={`shrink-0 rounded-xs px-1.5 py-0.5 text-micro ${
                        p.status === 'recovery_required' ? 'bg-status-error/15 text-status-error'
                          : p.status === 'merged' ? 'bg-status-ok/15 text-status-ok'
                            : 'bg-surface text-text-subtle'
                      }`}>
                        {t(`status.${p.status}`)}
                      </span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-2 text-micro text-text-subtle">
                      <span>rev {p.revision}</span>
                      <span>·</span>
                      <span>{p.op_kinds.join(', ')}</span>
                      <span>·</span>
                      <span>{p.author_kind === 'agent' ? t('authorAgent') : t('authorUser')}</span>
                      {p.status === 'recovery_required' && (
                        <span className="inline-flex items-center gap-0.5 text-status-error"><ShieldAlert size={12} aria-hidden />{t('needsRecovery')}</span>
                      )}
                    </div>
                  </button>
                </li>
              ))}
          </ul>
        )}

        {selected && detail && diff && (
          <div className="flex flex-col gap-3" data-testid="feature-proposal-detail">
            <section>
              <h3 className="text-sm font-medium">{detail.title}</h3>
              <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-micro text-text-subtle">
                <span>{t('colStatus')}: {t(`status.${detail.status}`)}</span>
                <span>{t('colRevision')}: {detail.revision}</span>
                <span className="truncate">{t('colBaseRev')}: {detail.base.content_revision}</span>
                <span>{t('colDigest')}: {detail.candidate_digest.slice(0, 12)}…</span>
                <span className="col-span-2 truncate" title={detail.base.fingerprint}>
                  {t('colBaseFp')}: {detail.base.fingerprint.slice(0, 20)}…
                </span>
              </div>
            </section>

            {(detail.status === 'recovery_required' || detail.status === 'merging') && (
              <section className="rounded-xs border border-status-error/40 bg-status-error/10 px-2 py-1.5 text-micro" data-testid="feature-proposal-recovery">
                <div className="flex items-center gap-1 font-medium text-status-error">
                  <ShieldAlert size={14} aria-hidden /> {t('recoveryTitle')}
                </div>
                <p className="mt-1">{t('recoveryBody')}</p>
                <button
                  type="button"
                  disabled={busy}
                  className="mt-1.5 rounded-xs border border-edge-subtle px-2 py-1 hover:bg-surface-hover disabled:opacity-50"
                  onClick={() => { void act(() => resolveRecovery(detail.proposal_id), t('noticeRecoveryResolved')); }}
                  data-testid="feature-proposal-resolve-recovery"
                >
                  {t('actionResolveRecovery')}
                </button>
              </section>
            )}

            <section data-testid="feature-proposal-verdict">
              <div className="flex items-center gap-2">
                {verdict === 'clean' && <CheckCircle2 size={14} className="text-status-ok" aria-hidden />}
                {verdict === 'conflicted' && <GitBranch size={14} className="text-status-warning" aria-hidden />}
                {verdict === 'blocked' && <ShieldAlert size={14} className="text-status-error" aria-hidden />}
                <span className="font-medium">
                  {verdict === 'clean' ? t('verdictClean')
                    : verdict === 'conflicted' ? t('verdictConflicted', { count: unresolved.length })
                      : t('verdictBlocked')}
                </span>
              </div>
              {diff.conflict_report.current_changed && (
                <p className="mt-0.5 text-micro text-text-subtle">{t('currentChanged')}</p>
              )}
            </section>

            {unresolved.length > 0 && (
              <section data-testid="feature-proposal-conflicts">
                <h4 className="text-xs font-medium uppercase tracking-wide text-text-subtle">{t('conflictsTitle')}</h4>
                <ul className="mt-1 flex flex-col gap-1.5">
                  {unresolved.map((c: ProposalConflict) => (
                    <li key={c.conflict_id} className="rounded-xs border border-edge-subtle px-2 py-1.5">
                      <div className="text-micro font-medium">{t(`conflictKind.${c.kind}`, { fallback: c.kind })}</div>
                      {c.field != null && (
                        <div className="mt-1 grid grid-cols-3 gap-1 text-micro">
                          <span className="text-text-subtle">{t('valBase')}: <span className="text-text">{valueCell(c.base)}</span></span>
                          <span className="text-text-subtle">{t('valCurrent')}: <span className="text-text">{valueCell(c.current)}</span></span>
                          <span className="text-text-subtle">{t('valProposed')}: <span className="text-text">{valueCell(c.proposed)}</span></span>
                        </div>
                      )}
                      {c.note && <p className="mt-0.5 text-micro text-text-subtle">{c.note}</p>}
                      {c.wfid && <p className="text-micro text-text-subtle">wfid: {c.wfid}</p>}
                      {c.kind !== 'schema_incompatible' && c.kind !== 'identity_unprovable' && c.kind !== 'id_ambiguous' && (
                        <fieldset className="mt-1.5">
                          <legend className="text-micro text-text-subtle">{t('resolutionLegend')}</legend>
                          <div className="mt-0.5 flex flex-wrap gap-2 text-micro">
                            {(KEEP_ONLY_KINDS.has(c.kind)
                              ? (['keep_current'] as const)
                              : (['keep_current', 'take_proposed'] as const)
                            ).map((action) => (
                              <label key={action} className="inline-flex items-center gap-1">
                                <input
                                  type="radio"
                                  name={`res-${c.conflict_id}`}
                                  checked={resolutions[c.conflict_id] === action}
                                  onChange={() => setResolutions((r) => ({ ...r, [c.conflict_id]: action }))}
                                />
                                {t(`resolution.${action}`)}
                              </label>
                            ))}
                          </div>
                        </fieldset>
                      )}
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  className="mt-2 rounded-xs border border-edge-subtle px-2 py-1 text-micro hover:bg-surface-hover disabled:opacity-50"
                  disabled={busy || Object.keys(resolutions).length !== unresolved.filter(
                    (c) => !['schema_incompatible', 'identity_unprovable', 'id_ambiguous'].includes(c.kind),
                  ).length}
                  data-pending={unresolved.filter(
                    (c) => !['schema_incompatible', 'identity_unprovable', 'id_ambiguous'].includes(c.kind),
                  ).length}
                  onClick={submitResolutions}
                  data-testid="feature-proposal-submit-resolutions"
                >
                  {t('submitResolutions')}
                </button>
              </section>
            )}

            <section data-testid="feature-proposal-deltas">
              <h4 className="text-xs font-medium uppercase tracking-wide text-text-subtle">
                {t('deltasTitle')} ({diff.candidate_delta_count})
              </h4>
              <ul className="mt-1 flex flex-col gap-1">
                {diff.candidate_deltas.map((d) => (
                  <li key={d.wfid} className="rounded-xs border border-edge-subtle px-2 py-1 text-micro">
                    <div className="flex items-center gap-1">
                      <span className="font-mono">{d.wfid}</span>
                      {d.presence && (
                        <span className="rounded-xs bg-surface px-1 py-0.5">{t(`presence.${d.presence}`)}</span>
                      )}
                    </div>
                    {Object.entries(d.fields).map(([fname, change]) => (
                      <div key={fname} className="mt-0.5">
                        <span className="text-text-subtle">{fname}:</span>{' '}
                        {String(change.before)} → <span className="font-medium">{String(change.after)}</span>
                      </div>
                    ))}
                    {d.geometry_after_digest && (
                      <div className="mt-0.5 text-text-subtle">
                        geometry: {d.geometry_before_digest ?? '∅'} → {d.geometry_after_digest}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
              {diff.candidate_delta_count > diff.candidate_deltas.length && (
                <button
                  type="button"
                  className="mt-1 text-micro underline"
                  onClick={() => {
                    if (state.selectedId) {
                      void getFeatureProposalDiff(state.selectedId, diff.page + 1).then((next) => {
                        setDiff({ ...next, candidate_deltas: [...diff.candidate_deltas, ...next.candidate_deltas] });
                      });
                    }
                  }}
                >
                  {t('loadMoreDeltas')}
                </button>
              )}
            </section>
          </div>
        )}
      </div>

      {selected && detail && (
        <footer className="flex flex-wrap items-center gap-1.5 border-t border-edge-subtle px-3 py-2" data-testid="feature-proposal-actions">
          {detail.status === 'draft' && (
            <button
              type="button" disabled={busy}
              className="rounded-xs border border-edge-subtle px-2 py-1 text-micro hover:bg-surface-hover disabled:opacity-50"
              onClick={() => void act(() => decideFeatureProposal(detail.proposal_id, 'submit'), t('noticeSubmitted'))}
            >
              {t('actionSubmit')}
            </button>
          )}
          {(detail.status === 'submitted' || detail.status === 'changes_requested') && verdict === 'clean' && (
            <button
              type="button" disabled={busy}
              className="rounded-xs border border-status-ok/50 bg-status-ok/10 px-2 py-1 text-micro hover:bg-status-ok/20 disabled:opacity-50"
              onClick={() => void act(() => decideFeatureProposal(detail.proposal_id, 'approve'), t('noticeApproved'))}
              data-testid="feature-proposal-approve"
            >
              {t('actionApprove')}
            </button>
          )}
          {detail.status === 'approved' && (
            <button
              type="button" disabled={busy}
              className="rounded-xs border border-status-ok/50 bg-status-ok/10 px-2 py-1 text-micro hover:bg-status-ok/20 disabled:opacity-50"
              onClick={() => void act(() => mergeFeatureProposal(detail.proposal_id), t('noticeMerged'))}
              data-testid="feature-proposal-merge"
            >
              {t('actionMerge')}
            </button>
          )}
          {(detail.status === 'submitted' || detail.status === 'changes_requested') && diff?.conflict_report.current_changed && (
            <button
              type="button" disabled={busy || verdict !== 'clean'}
              className="rounded-xs border border-edge-subtle px-2 py-1 text-micro hover:bg-surface-hover disabled:opacity-50"
              onClick={() => void act(() => rebaseFeatureProposal(detail.proposal_id, detail.revision, true), t('noticeRebased'))}
            >
              {t('actionRebase')}
            </button>
          )}
          {detail.status === 'recovery_required' && (
            <button
              type="button" disabled={busy}
              className="rounded-xs border border-edge-subtle px-2 py-1 text-micro hover:bg-surface-hover disabled:opacity-50"
              onClick={() => void act(() => getFeatureProposalDetail(detail.proposal_id).then(() => undefined))}
            >
              {t('actionRefresh')}
            </button>
          )}
          {['draft', 'submitted', 'changes_requested', 'approved'].includes(detail.status) && (
            <button
              type="button" disabled={busy}
              className="ml-auto rounded-xs border border-edge-subtle px-2 py-1 text-micro hover:bg-surface-hover disabled:opacity-50"
              onClick={() => void act(() => withdrawFeatureProposal(detail.proposal_id), t('noticeWithdrawn'))}
            >
              {t('actionWithdraw')}
            </button>
          )}
        </footer>
      )}
    </div>
  );
}

export { resetFeatureProposalsForTests };
