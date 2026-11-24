'use client';

/**
 * G05 情境化多方案候选面板（WP5）—— 挂 Map Studio「方案」子页签。
 *
 * 单一真相 = 服务端 receipts（scenario_overview / compare / adoption）；
 * 本组件零平行状态：每次动作后重拉 overview（adopt/abandon/release/refresh）。
 * 状态徽标如实映射 candidate.state（stale 带 stale_reasons 披露；
 * adopted 带 adoption_id）；影响分级（current/recompute_required/
 * adopt_blocked/unresolvable）原样透传，不美化。采纳面 = 语义分组多选
 * + 单事务提交；逐项裁决（applied/lock_blocked/agent_conflict/…）在
 * 采纳后完整披露（部分成功不冒充全成功）。
 */
import { useCallback, useEffect, useState } from 'react';
import { FlaskConical, Lock, RefreshCw, Trash2, CheckCircle2, AlertTriangle } from 'lucide-react';

import { useT } from '@/lib/i18n/useT';
import { useToastStore } from '@/components/ui/toast';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import {
  abandonScenarioCandidate,
  adoptScenarioCandidate,
  fetchScenarioCompare,
  fetchScenarioOverview,
  releaseScenarioLock,
  type AdoptionReceiptView,
  type ScenarioCandidateView,
  type ScenarioCompareView,
  type ScenarioOverviewView,
} from '@/lib/api/scenario-harness';

const GROUPS = ['analysis', 'style', 'layout', 'component', 'data'] as const;

type ImpactLevel = 'current' | 'recompute_required' | 'adopt_blocked' | 'unresolvable';

const IMPACT_TONE: Record<ImpactLevel, string> = {
  current: 'var(--agent-accent, #16a34a)',
  recompute_required: '#d97706',
  adopt_blocked: '#dc2626',
  unresolvable: '#dc2626',
};

function stateBadge(state: ScenarioCandidateView['state']): string {
  return state;
}

export function ScenarioPanel() {
  const t = useT('workbench');
  const [overview, setOverview] = useState<ScenarioOverviewView | null>(null);
  const [compare, setCompare] = useState<ScenarioCompareView | null>(null);
  const [receipt, setReceipt] = useState<AdoptionReceiptView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [selectedGroups, setSelectedGroups] = useState<Record<string, string[]>>({});

  const sessionId = getMapSpecSessionCursor().sessionId;

  const refresh = useCallback(async () => {
    const sid = getMapSpecSessionCursor().sessionId;
    if (!sid) {
      setOverview(null);
      return;
    }
    setBusy(true);
    setError('');
    try {
      const next = await fetchScenarioOverview(sid);
      setOverview(next);
    } catch {
      setError(t('scenario.loadFailed'));
    } finally {
      setBusy(false);
    }
  }, [t]);

  useEffect(() => {
    void refresh();
  }, [refresh, sessionId]);

  const runCompare = useCallback(async () => {
    const sid = getMapSpecSessionCursor().sessionId;
    if (!sid) return;
    setBusy(true);
    setError('');
    try {
      const ids = (overview?.candidates ?? [])
        .filter((c) => c.state === 'comparable' || c.state === 'stale')
        .map((c) => c.candidate_id);
      const next = await fetchScenarioCompare(sid, ids);
      setCompare(next);
    } catch {
      setError(t('scenario.loadFailed'));
    } finally {
      setBusy(false);
    }
  }, [overview, t]);

  const runAdopt = useCallback(
    async (candidate: ScenarioCandidateView) => {
      const sid = getMapSpecSessionCursor().sessionId;
      if (!sid) return;
      const groups = (selectedGroups[candidate.candidate_id] ?? []).filter((g) =>
        (GROUPS as readonly string[]).includes(g),
      );
      setBusy(true);
      setError('');
      try {
        const out = await adoptScenarioCandidate(sid, candidate.candidate_id, {
          mode: groups.length ? 'groups' : 'all',
          groups,
        });
        setReceipt(out.receipt);
        useToastStore
          .getState()
          .addToast(
            `${t('scenario.adoptDone')} (${out.receipt.applied_count}/${
              out.receipt.applied_count + out.receipt.skipped_count
            })`,
            'success',
          );
        await refresh();
      } catch (err) {
        // ApiError.body = 解析后的响应体（FastAPI detail 直通或 {detail: {...}} 包装）——
        // 两种形态都探一遍，取不到再用通用文案（诚实降级，不伪造成功）。
        let detail = '';
        if (typeof err === 'object' && err && 'body' in err) {
          const body = (err as { body?: unknown }).body as
            | { detail?: { message?: string }; message?: string }
            | undefined;
          detail = String(body?.detail?.message ?? body?.message ?? '');
        }
        setError(detail || t('scenario.adoptFailed'));
      } finally {
        setBusy(false);
      }
    },
    [refresh, selectedGroups, t],
  );

  const runAbandon = useCallback(
    async (candidateId: string) => {
      const sid = getMapSpecSessionCursor().sessionId;
      if (!sid) return;
      setBusy(true);
      try {
        await abandonScenarioCandidate(sid, candidateId, { hardDelete: true });
        if (receipt?.candidate_id === candidateId) setReceipt(null);
        useToastStore.getState().addToast(t('scenario.abandonDone'), 'success');
        await refresh();
      } catch {
        useToastStore.getState().addToast(t('scenario.abandonFailed'), 'error');
      } finally {
        setBusy(false);
      }
    },
    [receipt, refresh, t],
  );

  const runReleaseLock = useCallback(
    async (lockId: string) => {
      const sid = getMapSpecSessionCursor().sessionId;
      if (!sid) return;
      setBusy(true);
      try {
        await releaseScenarioLock(sid, lockId, 'workbench release');
        await refresh();
      } catch {
        useToastStore.getState().addToast(t('scenario.lockReleaseFailed'), 'error');
      } finally {
        setBusy(false);
      }
    },
    [refresh, t],
  );

  if (!sessionId) {
    return (
      <div className="p-4 text-body text-ink-muted" data-testid="scenario-empty">
        <p className="font-medium text-ink">{t('scenario.emptyTitle')}</p>
        <p className="mt-1">{t('scenario.emptyHint')}</p>
      </div>
    );
  }

  const baseline = overview?.overview?.baseline;

  return (
    <div className="flex flex-col gap-3 p-3" data-testid="scenario-panel">
      {/* 基准摘要 */}
      <div
        className="rounded-md border border-edge-subtle bg-surface-sunken p-2.5 text-meta"
        data-testid="scenario-baseline"
      >
        <div className="flex items-center justify-between gap-2">
          <span className="font-medium text-ink">{t('scenario.baselineTitle')}</span>
          <button
            type="button"
            aria-label={t('scenario.refreshAria')}
            onClick={() => {
              void refresh();
            }}
            disabled={busy}
            className="rounded p-1 hover:bg-surface-raised disabled:opacity-50"
          >
            <RefreshCw size={13} aria-hidden />
          </button>
        </div>
        {baseline ? (
          <div className="mt-1 text-ink-muted">
            rev {baseline.mapspec_revision} · {baseline.dataset_refs.length}{' '}
            {t('scenario.datasetUnit')} · {baseline.captured_at || '—'}
          </div>
        ) : (
          <div className="mt-1 text-ink-muted">{t('scenario.baselineNone')}</div>
        )}
        {(overview?.overview?.active_lock_count ?? 0) > 0 && (
          <div className="mt-1 flex items-center gap-1 text-ink-muted" role="status">
            <Lock size={12} aria-hidden /> {t('scenario.activeLocks')}: {overview?.overview?.active_lock_count}
          </div>
        )}
      </div>

      {error && (
        <div className="rounded-md border border-edge-subtle bg-surface-sunken p-2 text-meta text-[#dc2626]" role="alert">
          {error}
        </div>
      )}

      {/* 候选卡 */}
      {(overview?.candidates ?? []).map((c) => {
        const impact = compare?.result?.impacts.find(
          (i) => i.candidate_id === c.candidate_id,
        );
        return (
          <div
            key={c.candidate_id}
            className="rounded-md border border-edge-subtle bg-surface-overlay p-2.5"
            data-testid={`scenario-card-${c.candidate_id}`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-ink">{c.title || c.candidate_id}</span>
              <span
                className="rounded px-1.5 py-0.5 text-meta"
                style={{
                  backgroundColor:
                    c.state === 'adopted'
                      ? 'var(--agent-accent, #16a34a)'
                      : c.state === 'stale'
                        ? '#d97706'
                        : 'var(--surface-sunken)',
                  color: c.state === 'draft' ? 'var(--text-muted)' : '#fff',
                }}
                data-testid={`scenario-state-${c.candidate_id}`}
              >
                {stateBadge(c.state)}
              </span>
            </div>
            {c.hypothesis && <div className="mt-1 text-meta text-ink-muted">{c.hypothesis}</div>}
            <div className="mt-1 text-meta text-ink-muted">
              {t('scenario.deltaCount')}: {c.delta.length} · {t('scenario.appliedCount')}: {c.applied_seqs.length}
              {' · '}
              {t('scenario.groups')}: {[...new Set(c.delta.map((d) => d.group))].join('/') || '—'}
            </div>
            {c.stale_reasons.length > 0 && (
              <div className="mt-1 flex items-start gap-1 text-meta text-[#d97706]">
                <AlertTriangle size={12} aria-hidden />
                <span>{t('scenario.staleReasons')}: {c.stale_reasons.join('；')}</span>
              </div>
            )}
            {impact && (
              <div className="mt-1 text-meta" style={{ color: IMPACT_TONE[impact.level] }}>
                {t(`scenario.impact_${impact.level}`)}
                {impact.reasons.length > 0 && ` · ${impact.reasons.join('；')}`}
              </div>
            )}

            {c.state !== 'adopted' && (
              <>
                <div className="mt-2 flex flex-wrap gap-1.5" aria-label={t('scenario.groupsAria')}>
                  {GROUPS.map((g) => {
                    const active = (selectedGroups[c.candidate_id] ?? []).includes(g);
                    return (
                      <button
                        key={g}
                        type="button"
                        aria-pressed={active}
                        disabled={busy}
                        onClick={() =>
                          setSelectedGroups((prev) => {
                            const cur = prev[c.candidate_id] ?? [];
                            const next = active ? cur.filter((x) => x !== g) : [...cur, g];
                            return { ...prev, [c.candidate_id]: next };
                          })
                        }
                        className="rounded border border-edge-subtle px-1.5 py-0.5 text-meta disabled:opacity-50"
                        style={{
                          backgroundColor: active ? 'var(--surface-raised)' : 'transparent',
                          color: active ? 'var(--text-primary)' : 'var(--text-muted)',
                        }}
                      >
                        {t(`scenario.group_${g}`)}
                      </button>
                    );
                  })}
                </div>
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    disabled={busy || c.state === 'draft' || c.state === 'running'}
                    onClick={() => {
                      void runAdopt(c);
                    }}
                    className="flex items-center gap-1 rounded-md bg-[var(--agent-accent,#16a34a)] px-2 py-1 text-meta font-medium text-white disabled:opacity-50"
                    data-testid={`scenario-adopt-${c.candidate_id}`}
                  >
                    <CheckCircle2 size={12} aria-hidden />
                    {t('scenario.adopt')}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      void runAbandon(c.candidate_id);
                    }}
                    className="flex items-center gap-1 rounded-md border border-edge-subtle px-2 py-1 text-meta text-ink-muted disabled:opacity-50"
                  >
                    <Trash2 size={12} aria-hidden />
                    {t('scenario.abandon')}
                  </button>
                </div>
              </>
            )}
            {c.state === 'adopted' && (
              <div className="mt-2 text-meta text-ink-muted">
                {t('scenario.adoptedAs')} {c.adoption_id}
              </div>
            )}
          </div>
        );
      })}

      {(overview?.candidates ?? []).length === 0 && !busy && (
        <div className="text-body text-ink-muted" data-testid="scenario-no-candidates">
          <p className="font-medium text-ink">{t('scenario.noCandidatesTitle')}</p>
          <p className="mt-1">{t('scenario.noCandidatesHint')}</p>
        </div>
      )}

      {/* 对比区 */}
      {(overview?.candidates ?? []).length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => {
              void runCompare();
            }}
            disabled={busy}
            className="flex items-center gap-1.5 rounded-md border border-edge-subtle px-2 py-1 text-meta disabled:opacity-50"
            data-testid="scenario-compare-btn"
          >
            <FlaskConical size={12} aria-hidden />
            {t('scenario.compare')}
          </button>
          {compare?.result && (
            <div className="mt-2 space-y-2" data-testid="scenario-compare-result">
              {compare.result.move?.moved && (
                <div className="rounded-md bg-surface-sunken p-2 text-meta text-[#d97706]">
                  {t('scenario.baselineMoved')} rev {compare.result.move.revision_from}→
                  {compare.result.move.revision_to}
                </div>
              )}
              {compare.result.rows.map((row) => (
                <div key={row.candidate_id} className="rounded-md border border-edge-subtle p-2 text-meta">
                  <div className="font-medium text-ink">{row.candidate_id}</div>
                  {!row.comparable && (
                    <div className="mt-1 text-[#dc2626]">
                      {t('scenario.incomparable')}: {row.incomparable_reason}
                    </div>
                  )}
                  {row.comparable && (
                    <div className="mt-1 text-ink-muted">
                      {row.layer_adds.length > 0 && (
                        <div>+{t('scenario.layers')}: {row.layer_adds.join(', ')}</div>
                      )}
                      {row.layer_removes.length > 0 && (
                        <div>−{t('scenario.layers')}: {row.layer_removes.join(', ')}</div>
                      )}
                      {row.style_fields.length > 0 && (
                        <div>{t('scenario.styleFields')}: {row.style_fields.join(', ')}</div>
                      )}
                      {Object.keys(row.metric_deltas).length > 0 && (
                        <div>
                          {t('scenario.metrics')}:{' '}
                          {Object.entries(row.metric_deltas)
                            .slice(0, 6)
                            .map(([k, v]) => `${k}: ${v > 0 ? '+' : ''}${Math.round(v * 100) / 100}`)
                            .join(' · ')}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 采纳回执披露（逐项裁决） */}
      {receipt && (
        <div
          className="rounded-md border border-edge-subtle bg-surface-sunken p-2.5 text-meta"
          data-testid="scenario-adoption-receipt"
          role="status"
        >
          <div className="font-medium text-ink">
            {t('scenario.receiptTitle')} ({receipt.applied_count}/{receipt.items.length})
          </div>
          <ul className="mt-1 space-y-0.5">
            {receipt.items.map((item) => (
              <li key={item.seq} className="text-ink-muted">
                #{item.seq} {item.kind} [{item.verdict}]
                {item.detail ? ` — ${item.detail}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 锁定清单 */}
      {(overview?.active_locks ?? []).length > 0 && (
        <div className="rounded-md border border-edge-subtle p-2.5" data-testid="scenario-locks">
          <div className="flex items-center gap-1 text-meta font-medium text-ink">
            <Lock size={12} aria-hidden /> {t('scenario.locksTitle')}
          </div>
          <ul className="mt-1 space-y-1">
            {overview?.active_locks.map((lock) => (
              <li key={lock.lock_id} className="flex items-center justify-between gap-2 text-meta text-ink-muted">
                <span>
                  {lock.target_id || t('scenario.lockGlobal')} · {lock.field_paths.join(',')}
                  {lock.reason ? ` · ${lock.reason}` : ''}
                </span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    void runReleaseLock(lock.lock_id);
                  }}
                  className="rounded border border-edge-subtle px-1.5 py-0.5 disabled:opacity-50"
                  data-testid={`scenario-unlock-${lock.lock_id}`}
                >
                  {t('scenario.lockRelease')}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
