'use client';

/**
 * TopologyWorkbenchPanel — 跨图层拓扑工作台（G31，WP08/WP09）。
 *
 * 与 QualityPanel（样例快速审计）互补且边界清楚：本面板在**权威快照**上
 * 运行声明式跨图层规则，问题队列带稳定身份/定位/例外/修复/复验闭环；
 * 样例审计仍走旧入口（保留不动）。
 *
 * 诚实纪律：
 * - 运行状态与质量结论分离渲染（all-skipped/budget-truncated 不得显示绿）；
 * - 问题 total 是全量计数（分页披露）；定位过期（stale）如实标注；
 * - 例外是用户决定（理由必填）；修复候选明确区分建议与可提交计划。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { GitBranch, LocateFixed, Network, RefreshCw } from 'lucide-react';

import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { StatusBadge } from '@/components/shared/status-badge';
import { useToastStore } from '@/components/ui/toast';
import {
  acceptException,
  createRuleset,
  fetchFindings,
  fetchFindingWitness,
  fetchRepairCandidates,
  fetchRun,
  fetchRuns,
  fetchRulesets,
  recheckRun,
  revokeException,
  submitTopologyRun,
  type FindingWitness,
  type RepairCandidate,
  type RoleBindingInput,
  type TopologyFinding,
  type TopologyRule,
  type TopologyRuleset,
  type TopologyRunDetail,
  type TopologyRunSummary,
} from '@/lib/api/topology-workbench';
import { parseApiErrorDetail } from '@/lib/workflow/recovery';
import { useT } from '@/lib/i18n/useT';
import { useHudStore } from '@/lib/store/useHudStore';

const RULE_TYPES = [
  'polygons_no_overlap',
  'area_coverage',
  'objects_covered_by_target',
  'cross_layer_no_overlap',
  'line_endpoints_connected',
] as const;
const SEVERITIES = ['info', 'warn', 'error'] as const;
const FINDING_STATUSES = ['open', 'accepted_exception', 'resolved', 'dismissed'] as const;
const PAGE_SIZE = 20;

export interface TopologyWorkbenchPanelProps {
  projectId: string;
  sessionId?: string | null;
  authed: boolean;
}

interface BindingDraft {
  kind: 'session_ref' | 'bbox';
  ref: string;
  bbox: string;
}

export function TopologyWorkbenchPanel({ projectId, sessionId, authed }: TopologyWorkbenchPanelProps) {
  const t = useT('topology');
  const addToast = useToastStore((s) => s.addToast);

  const [rulesets, setRulesets] = useState<TopologyRuleset[]>([]);
  const [selectedRulesetId, setSelectedRulesetId] = useState('');
  const [runs, setRuns] = useState<TopologyRunSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState('');
  const [runDetail, setRunDetail] = useState<TopologyRunDetail | null>(null);
  const [findings, setFindings] = useState<TopologyFinding[]>([]);
  const [findingsTotal, setFindingsTotal] = useState(0);
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({});
  const [offset, setOffset] = useState(0);
  const [severityFilter, setSeverityFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [selectedFinding, setSelectedFinding] = useState<TopologyFinding | null>(null);
  const [witness, setWitness] = useState<FindingWitness | null>(null);
  const [candidates, setCandidates] = useState<RepairCandidate[] | null>(null);
  const [exceptionReason, setExceptionReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recheckBusy, setRecheckBusy] = useState(false);

  // 规则集创建表单
  const [showCreate, setShowCreate] = useState(false);
  const [draftName, setDraftName] = useState('');
  const [draftRules, setDraftRules] = useState<TopologyRule[]>([
    { rule_id: 'coverage', rule_type: 'area_coverage', severity: 'error', enabled: true, params: { gap_area_threshold_m2: 5 } },
  ]);
  // 角色绑定（简化表单：会话 ref / bbox）
  const [bindingsDraft, setBindingsDraft] = useState<Record<string, BindingDraft>>({});
  const selectedRuleset = rulesets.find((r) => r.id === selectedRulesetId) ?? null;
  const selectedRun = runs.find((r) => r.id === selectedRunId) ?? null;

  const loadRulesets = useCallback(async () => {
    try {
      const { items } = await fetchRulesets({ project_id: projectId, limit: 50 });
      setRulesets(items);
      if (items.length > 0 && !items.some((r) => r.id === selectedRulesetId)) {
        setSelectedRulesetId(items[0].id);
      }
    } catch (err) {
      setError(parseApiErrorDetail(err, String(t("errors.load", { message: "rulesets" }))));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const loadRuns = useCallback(async () => {
    if (!selectedRulesetId) { setRuns([]); return; }
    try {
      const { items } = await fetchRuns({ ruleset_id: selectedRulesetId, limit: 20 });
      setRuns(items);
      if (items.length > 0 && !items.some((r) => r.id === selectedRunId)) {
        setSelectedRunId(items[0].id);
      }
    } catch (err) {
      setError(parseApiErrorDetail(err, 'runs'));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRulesetId]);

  const loadRunDetail = useCallback(async () => {
    if (!selectedRunId) { setRunDetail(null); return; }
    try {
      setRunDetail(await fetchRun(selectedRunId));
    } catch (err) {
      setError(parseApiErrorDetail(err, 'run detail'));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRunId]);

  const loadFindings = useCallback(async () => {
    if (!selectedRunId) { setFindings([]); setFindingsTotal(0); return; }
    try {
      const page = await fetchFindings(selectedRunId, {
        severity: severityFilter || undefined,
        status: statusFilter || undefined,
        limit: PAGE_SIZE, offset,
      });
      setFindings(page.items);
      setFindingsTotal(page.total);
      setStatusCounts(page.status_counts ?? {});
    } catch (err) {
      setError(parseApiErrorDetail(err, 'findings'));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRunId, severityFilter, statusFilter, offset]);

  useEffect(() => { void loadRulesets(); }, [loadRulesets]);
  useEffect(() => { void loadRuns(); }, [loadRuns]);
  useEffect(() => { void loadRunDetail(); }, [loadRunDetail]);
  useEffect(() => { void loadFindings(); }, [loadFindings]);
  // 运行中轮询（10s；完成后停）
  const runningRef = useRef(false);
  runningRef.current = runs.some((r) => r.status === 'pending' || r.status === 'running');
  useEffect(() => {
    if (!runningRef.current) return;
    const h = window.setInterval(() => { void loadRuns(); void loadRunDetail(); }, 10_000);
    return () => window.clearInterval(h);
  }, [runningRef.current, loadRuns, loadRunDetail]);

  const handleCreateRuleset = async () => {
    setBusy(true); setError(null);
    try {
      const created = await createRuleset({
        name: draftName || 'topology-ruleset',
        rules: draftRules, project_id: projectId,
        session_id: sessionId ?? undefined,
      });
      addToast(`ruleset v${created.current_version} created`, 'success');
      setShowCreate(false);
      setSelectedRulesetId(created.id);
      await loadRulesets();
    } catch (err) {
      setError(parseApiErrorDetail(err, 'create ruleset'));
    } finally { setBusy(false); }
  };

  const roleNames = useMemo(() => {
    if (!selectedRuleset) return [];
    const roles = new Set<string>();
    for (const rule of selectedRuleset.rules) {
      if (!rule.enabled) continue;
      for (const [role, req] of Object.entries(ruleRoleSpec(rule.rule_type) as Record<string, string>)) {
        if (req.startsWith('required')) roles.add(role);
      }
    }
    return Array.from(roles);
  }, [selectedRuleset]);

  const handleStartRun = async () => {
    if (!selectedRulesetId) return;
    setBusy(true); setError(null);
    try {
      const bindings: Record<string, RoleBindingInput> = {};
      for (const role of roleNames) {
        const d = bindingsDraft[role];
        if (!d) continue;
        if (d.kind === 'bbox') {
          const parts = d.bbox.split(',').map((v) => Number.parseFloat(v.trim()));
          if (parts.length === 4 && parts.every((v) => Number.isFinite(v)) && role === 'aoi') {
            bindings[role] = { kind: 'bbox', bbox: parts as [number, number, number, number] };
          }
        } else if (d.ref.startsWith('ref:')) {
          bindings[role] = { kind: 'session_ref', ref: d.ref, session_id: sessionId ?? undefined };
        }
      }
      const res = await submitTopologyRun({ ruleset_id: selectedRulesetId, bindings, session_id: sessionId ?? undefined, project_id: projectId });
      addToast(`run ${res.run_id.slice(0, 8)} submitted (job)`, 'success');
      await loadRuns();
      setSelectedRunId(res.run_id);
    } catch (err) {
      setError(translateError(err, t));
    } finally { setBusy(false); }
  };

  const handleRecheck = async () => {
    if (!selectedRunId) return;
    setRecheckBusy(true); setError(null);
    try {
      const res = await recheckRun(selectedRunId);
      addToast(String(t('runs.recheckDone', {
        resolved: res.resolved_count, persisted: res.persisted_count, total: res.new_run_findings_total,
      })), 'success');
      await loadRuns();
      setSelectedRunId(res.recheck_run_id);
    } catch (err) {
      setError(translateError(err, t));
    } finally { setRecheckBusy(false); }
  };

  const openFinding = async (f: TopologyFinding) => {
    setSelectedFinding(f); setWitness(null); setCandidates(null); setExceptionReason('');
    try {
      const [w, c] = await Promise.all([
        fetchFindingWitness(f.id).catch(() => null),
        fetchRepairCandidates(f.id).catch(() => null),
      ]);
      setWitness(w);
      setCandidates(c?.candidates ?? []);
    } catch (err) {
      setError(parseApiErrorDetail(err, 'finding'));
    }
  };

  const locateFinding = (f: TopologyFinding) => {
    // 相机定位：bbox → 视口（witness 证据可在详情中查看 GeoJSON）
    const [w, s, e, n] = f.bbox;
    if (![w, s, e, n].every(Number.isFinite)) return;
    const center: [number, number] = [(w + e) / 2, (s + n) / 2];
    const span = Math.max(e - w, n - s, 1e-4);
    const zoom = Math.max(4, Math.min(18, Math.log2(360 / (span * 4))));
    useHudStore.getState().setViewport(center, zoom, 0, 0, [w, s, e, n]);
  };

  const handleAcceptException = async () => {
    if (!selectedFinding || !exceptionReason.trim()) return;
    setBusy(true);
    try {
      await acceptException(selectedFinding.id, exceptionReason.trim());
      addToast(String(t('exception.accepted')), 'success');
      setSelectedFinding(null);
      await Promise.all([loadFindings(), loadRunDetail(), loadRuns()]);
    } catch (err) {
      setError(translateError(err, t));
    } finally { setBusy(false); }
  };

  const handleRevokeException = async () => {
    if (!selectedFinding?.exception_id) return;
    setBusy(true);
    try {
      await revokeException(selectedFinding.exception_id);
      addToast(String(t('exception.revoked')), 'success');
      setSelectedFinding(null);
      await Promise.all([loadFindings(), loadRunDetail()]);
    } catch (err) {
      setError(translateError(err, t));
    } finally { setBusy(false); }
  };

  const allUnknown = runDetail?.completeness?.all_skipped_or_unknown === true;
  const budgetTruncated = runDetail?.completeness?.budget_truncated === true;
  const failureCode = runDetail?.completeness?.failure_code;

  return (
    <section aria-labelledby="twb-heading" className="space-y-2" data-testid="topology-workbench-panel">
      <h3 id="twb-heading" className="flex items-center gap-1.5 text-meta font-semibold text-ink-secondary">
        <Network size={14} className="text-ink-muted" aria-hidden /> {t('title')}
      </h3>
      <p className="text-micro text-ink-muted">{t('subtitle')}</p>

      {!authed && <InlineNotice variant="warning">{t('noAuth')}</InlineNotice>}
      {error && <InlineNotice variant="error">{error}</InlineNotice>}

      {/* 规则集 */}
      <div className="space-y-1.5 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2">
        <div className="flex items-center justify-between">
          <p className="text-meta font-medium text-ink-secondary">{t('rulesets.section')}</p>
          <div className="flex gap-1">
            <button type="button" onClick={() => void loadRulesets()} className="rounded-sm px-1.5 py-0.5 text-micro text-ink-secondary hover:bg-surface-sunken" aria-label={String(t('refresh'))}>
              <RefreshCw size={12} aria-hidden />
            </button>
            <button type="button" onClick={() => setShowCreate((v) => !v)} disabled={!authed}
              className="rounded-sm bg-status-accent px-2 py-0.5 text-micro font-medium text-ink-on-accent disabled:opacity-60">
              {t('rulesets.create')}
            </button>
          </div>
        </div>
        {rulesets.length === 0 && !showCreate && (
          <p className="text-micro text-ink-muted">{t('rulesets.empty')}</p>
        )}
        <select
          value={selectedRulesetId}
          onChange={(e) => { setSelectedRulesetId(e.target.value); setSelectedRunId(''); setBindingsDraft({}); }}
          aria-label={String(t('runs.selectRuleset'))}
          className="w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2.5 py-1.5 text-meta text-ink"
        >
          <option value="">{t('runs.selectRuleset')}</option>
          {rulesets.map((r) => (
            <option key={r.id} value={r.id}>{r.name} · {t('rulesets.version', { version: r.current_version })}</option>
          ))}
        </select>

        {showCreate && (
          <div className="space-y-1.5 border-t border-edge-subtle pt-2" data-testid="twb-create-form">
            <label className="block space-y-0.5">
              <span className="text-micro text-ink-secondary">{t('rulesets.name')}</span>
              <input value={draftName} onChange={(e) => setDraftName(e.target.value)}
                placeholder={String(t('rulesets.namePlaceholder'))}
                className="w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-micro" />
            </label>
            {draftRules.map((rule, i) => (
              <div key={i} className="space-y-0.5 rounded-sm bg-surface-sunken px-1.5 py-1">
                <div className="flex gap-1">
                  <input value={rule.rule_id} onChange={(e) => setDraftRules((prev) => prev.map((r, j) => j === i ? { ...r, rule_id: e.target.value } : r))}
                    aria-label={String(t('rulesets.ruleId'))}
                    className="w-20 rounded-sm border border-edge-subtle bg-surface px-1.5 py-0.5 text-micro" />
                  <select value={rule.rule_type} onChange={(e) => setDraftRules((prev) => prev.map((r, j) => j === i ? { ...r, rule_type: e.target.value } : r))}
                    aria-label={String(t('rulesets.ruleType'))}
                    className="flex-1 rounded-sm border border-edge-subtle bg-surface px-1.5 py-0.5 text-micro">
                    {RULE_TYPES.map((rt) => <option key={rt} value={rt}>{t(`ruleTypes.${rt}`)}</option>)}
                  </select>
                  <select value={rule.severity} onChange={(e) => setDraftRules((prev) => prev.map((r, j) => j === i ? { ...r, severity: e.target.value as TopologyRule['severity'] } : r))}
                    aria-label={String(t('rulesets.severity'))}
                    className="rounded-sm border border-edge-subtle bg-surface px-1.5 py-0.5 text-micro">
                    {SEVERITIES.map((s) => <option key={s} value={s}>{t(`findings.severity.${s}`)}</option>)}
                  </select>
                  <button type="button" onClick={() => setDraftRules((prev) => prev.filter((_, j) => j !== i))}
                    className="rounded-sm px-1 text-micro text-status-critical hover:bg-surface-raised" aria-label={String(t('rulesets.removeRule'))}>×</button>
                </div>
                <div className="flex items-center gap-1 text-micro text-ink-muted">
                  <span>{t('rulesets.params')}</span>
                  <RuleParamsInput rule={rule} onChange={(params) => setDraftRules((prev) => prev.map((r, j) => j === i ? { ...r, params } : r))} />
                </div>
              </div>
            ))}
            <div className="flex items-center justify-between">
              <button type="button" onClick={() => setDraftRules((prev) => [...prev, { rule_id: `rule${prev.length + 1}`, rule_type: 'polygons_no_overlap', severity: 'warn', enabled: true, params: {} }])}
                className="text-micro text-status-accent hover:underline">{t('rulesets.addRule')}</button>
              <button type="button" onClick={() => void handleCreateRuleset()} disabled={busy || !authed}
                className="rounded-sm bg-status-accent px-2 py-0.5 text-micro font-medium text-ink-on-accent disabled:opacity-60">
                {t('rulesets.create')}
              </button>
            </div>
            <p className="text-micro text-ink-muted">{t('rulesets.tip')}</p>
          </div>
        )}
      </div>

      {/* 运行 */}
      {selectedRuleset && (
        <div className="space-y-1.5 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2">
          <div className="flex items-center justify-between">
            <p className="text-meta font-medium text-ink-secondary">{t('runs.section')}</p>
            <button type="button" onClick={() => void handleStartRun()} disabled={busy || !authed}
              className="rounded-sm bg-status-accent px-2 py-0.5 text-micro font-medium text-ink-on-accent disabled:opacity-60">
              {t('runs.start')}
            </button>
          </div>
          {/* 角色绑定（简化输入） */}
          <div className="space-y-1" data-testid="twb-bindings">
            <p className="text-micro text-ink-muted">{t('runs.bindingHint')}</p>
            {roleNames.map((role) => (
              <div key={role} className="flex items-center gap-1">
                <span className="w-24 shrink-0 font-mono text-micro text-ink-secondary">{role}</span>
                <select
                  value={bindingsDraft[role]?.kind ?? 'session_ref'}
                  onChange={(e) => setBindingsDraft((prev) => ({ ...prev, [role]: { kind: e.target.value as BindingDraft['kind'], ref: prev[role]?.ref ?? '', bbox: prev[role]?.bbox ?? '' } }))}
                  aria-label={`${role} kind`}
                  className="rounded-sm border border-edge-subtle bg-surface-sunken px-1.5 py-0.5 text-micro">
                  <option value="session_ref">session_ref</option>
                  {role === 'aoi' && <option value="bbox">bbox</option>}
                </select>
                {bindingsDraft[role]?.kind === 'bbox' ? (
                  <input value={bindingsDraft[role]?.bbox ?? ''} onChange={(e) => setBindingsDraft((prev) => ({ ...prev, [role]: { kind: 'bbox', ref: '', bbox: e.target.value } }))}
                    placeholder="w,s,e,n" aria-label={`${role} bbox`}
                    className="flex-1 rounded-sm border border-edge-subtle bg-surface-sunken px-1.5 py-0.5 font-mono text-micro" />
                ) : (
                  <input value={bindingsDraft[role]?.ref ?? ''} onChange={(e) => setBindingsDraft((prev) => ({ ...prev, [role]: { kind: 'session_ref', ref: e.target.value, bbox: '' } }))}
                    placeholder="ref:…" aria-label={`${role} ref`}
                    className="flex-1 rounded-sm border border-edge-subtle bg-surface-sunken px-1.5 py-0.5 font-mono text-micro" />
                )}
              </div>
            ))}
          </div>
          {runs.length === 0 && <p className="text-micro text-ink-muted">{t('runs.empty')}</p>}
          <ul className="max-h-32 space-y-0.5 overflow-auto" data-testid="twb-runs">
            {runs.map((r) => (
              <li key={r.id}>
                <button type="button" onClick={() => setSelectedRunId(r.id)}
                  className={`flex w-full items-center justify-between rounded-sm px-1.5 py-1 text-micro ${r.id === selectedRunId ? 'bg-surface-sunken' : 'hover:bg-surface-sunken'}`}>
                  <span className="truncate font-mono text-ink-muted">{r.id.slice(0, 8)}{r.recheck_of_run_id ? ' ↻' : ''}</span>
                  <span className="flex items-center gap-1.5">
                    <StatusBadge status={r.status === 'completed' ? 'passed' : r.status === 'failed' ? 'blocking' : r.status} />
                    <span>{t('runs.findingsTotal', { count: r.findings_total })}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 运行详情 */}
      {runDetail && (
        <div className="space-y-1.5 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2" data-testid="twb-run-detail">
          <div className="flex flex-wrap items-center justify-between gap-1">
            <StatusBadge status={runDetail.status === 'completed' ? 'passed' : runDetail.status === 'failed' ? 'blocking' : runDetail.status} />
            <span className="text-micro text-ink-secondary">
              {t('runs.findingsTotal', { count: runDetail.findings_total })} · {t('runs.openCount', { count: runDetail.findings_open })}
            </span>
            {runDetail.computation_crs && (
              <span className="font-mono text-micro text-ink-muted">{t('runs.computationCrs', { crs: runDetail.computation_crs })}</span>
            )}
            <button type="button" onClick={() => void handleRecheck()} disabled={recheckBusy || !authed || runDetail.status !== 'completed'}
              className="rounded-sm border border-edge-subtle px-2 py-0.5 text-micro text-ink-secondary hover:bg-surface-sunken disabled:opacity-60">
              {recheckBusy ? String(t('runs.rechecking')) : String(t('runs.recheck'))}
            </button>
          </div>
          {failureCode && <InlineNotice variant="error">{String(t('runs.failed', { message: runDetail.completeness?.failure_message ?? failureCode }))}</InlineNotice>}
          {allUnknown && <InlineNotice variant="warning">{t('runs.allUnknown')}</InlineNotice>}
          {budgetTruncated && <InlineNotice variant="warning">{t('runs.budgetTruncated')}</InlineNotice>}
          <ul className="space-y-0.5">
            {(runDetail.rule_results ?? []).map((rr) => (
              <li key={rr.rule_id} className="rounded-sm bg-surface-sunken px-1.5 py-1 text-micro">
                <span className="font-mono text-ink-secondary">{rr.rule_id}</span>
                {' '}
                <span className={rr.outcome === 'no_violations' && rr.computation === 'full' ? 'text-status-success' : rr.outcome === 'unknown' || rr.applicability === 'blocked' ? 'text-status-warning' : 'text-status-critical'}>
                  {rr.applicability === 'blocked' ? String(t('ruleResult.blocked')) : String(t(`ruleResult.${rr.outcome || 'unknown'}`))}
                </span>
                {' · '}
                <span className="text-ink-muted">
                  {t('ruleResult.examined', { count: rr.counts?.examined ?? rr.counts?.coverage_examined ?? 0 })}
                  {rr.computation === 'partial' ? ` · ${String(t('ruleResult.partial'))}` : ''}
                </span>
                {rr.blocked_message && <span className="block text-ink-muted">{rr.blocked_message}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 问题队列 */}
      {selectedRunId && (
        <div className="space-y-1.5 rounded-md border border-edge-subtle bg-surface-raised px-panel py-2" data-testid="twb-findings">
          <div className="flex flex-wrap items-center gap-1.5">
            <p className="text-meta font-medium text-ink-secondary">{t('findings.section')}</p>
            <select value={severityFilter} onChange={(e) => { setSeverityFilter(e.target.value); setOffset(0); }}
              aria-label={String(t('findings.filterSeverity'))}
              className="rounded-sm border border-edge-subtle bg-surface-sunken px-1.5 py-0.5 text-micro">
              <option value="">{t('findings.filterSeverity')} · {t('findings.all')}</option>
              {SEVERITIES.map((s) => <option key={s} value={s}>{t(`findings.severity.${s}`)}</option>)}
            </select>
            <select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setOffset(0); }}
              aria-label={String(t('findings.filterStatus'))}
              className="rounded-sm border border-edge-subtle bg-surface-sunken px-1.5 py-0.5 text-micro">
              <option value="">{t('findings.filterStatus')} · {t('findings.all')}</option>
              {FINDING_STATUSES.map((s) => <option key={s} value={s}>{t(`findings.status.${s}`)}</option>)}
            </select>
          </div>
          {Object.keys(statusCounts).length > 0 && (
            <p className="text-micro text-ink-muted">
              {FINDING_STATUSES.filter((s) => statusCounts[s]).map((s) => `${t(`findings.status.${s}`)}: ${statusCounts[s]}`).join(' · ')}
              {' — '}{t('findings.countNote')}
            </p>
          )}
          {findings.length === 0 && <p className="text-micro text-ink-muted">{t('findings.empty')}</p>}
          <ul className="max-h-48 space-y-1 overflow-auto">
            {findings.map((f) => (
              <li key={f.id} className="rounded-sm bg-surface-sunken px-1.5 py-1 text-micro">
                <div className="flex items-start justify-between gap-1">
                  <div className="min-w-0">
                    <span className={`mr-1 font-mono ${f.severity === 'error' ? 'text-status-critical' : f.severity === 'warn' ? 'text-status-warning' : 'text-ink-muted'}`}>{f.severity}</span>
                    <span className="font-mono text-ink-secondary">{f.rule_id}</span>
                    <span className="ml-1 rounded-sm border border-edge-subtle px-1 text-ink-muted">{t(`findings.status.${f.status}`)}</span>
                    <span className="block truncate text-ink-secondary">{f.message}</span>
                    {f.metric?.value != null && f.metric.unit && (
                      <span className="text-ink-muted">
                        {t('findings.metric', { value: f.metric.value, unit: f.metric.unit, threshold: f.metric.threshold ?? 0, method: f.metric.method ?? '' })}
                      </span>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <button type="button" onClick={() => locateFinding(f)} title={String(t('findings.locate'))}
                      aria-label={String(t('findings.locate'))}
                      className="rounded-sm p-1 text-ink-secondary hover:bg-surface-raised">
                      <LocateFixed size={12} aria-hidden />
                    </button>
                    <button type="button" onClick={() => void openFinding(f)}
                      className="rounded-sm px-1.5 py-0.5 text-ink-secondary hover:bg-surface-raised">
                      {t('findings.detail')}
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
          {findingsTotal > PAGE_SIZE && (
            <div className="flex items-center justify-between text-micro text-ink-muted">
              <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                className="rounded-sm px-1.5 py-0.5 hover:bg-surface-sunken disabled:opacity-50">{t('findings.prev')}</button>
              <span>{t('findings.page', { offset: offset + 1, end: Math.min(offset + findings.length, findingsTotal), total: findingsTotal })}</span>
              <button type="button" disabled={offset + PAGE_SIZE >= findingsTotal} onClick={() => setOffset(offset + PAGE_SIZE)}
                className="rounded-sm px-1.5 py-0.5 hover:bg-surface-sunken disabled:opacity-50">{t('findings.next')}</button>
            </div>
          )}
        </div>
      )}

      {/* 问题详情抽屉 */}
      {selectedFinding && (
        <div className="space-y-1.5 rounded-md border border-status-accent-border bg-surface-raised px-panel py-2" data-testid="twb-finding-detail" role="dialog" aria-label={String(t('findings.detail'))}>
          <div className="flex items-center justify-between">
            <p className="font-mono text-micro text-ink-secondary">{selectedFinding.rule_id} · {selectedFinding.finding_key.slice(0, 12)}</p>
            <button type="button" onClick={() => setSelectedFinding(null)} className="rounded-sm px-1.5 text-micro text-ink-muted hover:bg-surface-sunken" aria-label="close">×</button>
          </div>
          <p className="text-micro text-ink-secondary">{selectedFinding.message}</p>
          {witness?.stale && <InlineNotice variant="warning">{t('findings.stale', { reason: witness.stale_reason ?? '' })}</InlineNotice>}
          {witness && !witness.stale && (
            <details className="text-micro">
              <summary className="cursor-pointer text-ink-secondary">{t('findings.witness')}</summary>
              <pre className="mt-1 max-h-32 overflow-auto rounded-sm bg-surface-sunken p-1.5 font-mono text-micro text-ink-muted">
                {JSON.stringify(witness.witness.geometry, null, 1)}
              </pre>
            </details>
          )}
          {witness?.locators?.length ? (
            <div className="text-micro">
              <p className="text-ink-secondary">{t('findings.locators')}</p>
              <ul className="text-ink-muted">
                {witness.locators.slice(0, 6).map((l, i) => (
                  <li key={i} className="font-mono">{t('findings.locatorRole', { role: l.role, value: l.identity?.value ?? '?' })}{l.content_revision != null ? ` @r${l.content_revision}` : ''}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {/* 例外（用户决定） */}
          {selectedFinding.status === 'open' && (
            <div className="space-y-1 border-t border-edge-subtle pt-1.5">
              <label className="block text-micro">
                <span className="text-ink-secondary">{t('exception.reasonLabel')}</span>
                <input value={exceptionReason} onChange={(e) => setExceptionReason(e.target.value)}
                  placeholder={String(t('exception.reasonPlaceholder'))}
                  className="mt-0.5 w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-micro" />
              </label>
              <div className="flex gap-1">
                <button type="button" onClick={() => void handleAcceptException()} disabled={busy || !exceptionReason.trim() || !authed}
                  className="rounded-sm bg-status-accent px-2 py-0.5 text-micro font-medium text-ink-on-accent disabled:opacity-60">
                  {t('exception.confirm')}
                </button>
              </div>
              <p className="text-micro text-ink-muted">{t('exception.note')}</p>
            </div>
          )}
          {selectedFinding.status === 'accepted_exception' && selectedFinding.exception_id && (
            <button type="button" onClick={() => void handleRevokeException()} disabled={busy || !authed}
              className="rounded-sm border border-edge-subtle px-2 py-0.5 text-micro text-ink-secondary hover:bg-surface-sunken">
              {t('exception.revoke')}
            </button>
          )}
          {/* 修复候选 */}
          {candidates && candidates.length > 0 && (
            <div className="space-y-1 border-t border-edge-subtle pt-1.5">
              <p className="flex items-center gap-1 text-micro font-medium text-ink-secondary">
                <GitBranch size={11} aria-hidden /> {t('repair.candidates')}
              </p>
              {candidates.map((c) => (
                <div key={c.kind} className="rounded-sm bg-surface-sunken px-1.5 py-1 text-micro">
                  <p className="text-ink-secondary">{c.description}</p>
                  {c.ownership_choice_required && <p className="text-status-warning">{t('repair.ownership')}</p>}
                  {c.estimated_change?.expected && <p className="text-ink-muted">{c.estimated_change.expected}</p>}
                </div>
              ))}
              <p className="text-micro text-ink-muted">{t('repair.note')}</p>
            </div>
          )}
          {candidates && candidates.length === 0 && (
            <p className="text-micro text-ink-muted">{t('repair.noCandidates')}</p>
          )}
        </div>
      )}

      {rulesets.length === 0 && !busy && (
        <EmptyState icon={Network} title={String(t('rulesets.section'))} description={String(t('rulesets.empty'))} />
      )}
      {busy && <LoadingState label={String(t('refresh'))} />}
    </section>
  );
}

/** 每类规则的必需角色（与后端 RULE_ROLE_SPECS 同源的最小投影）。 */
function ruleRoleSpec(ruleType: string): Record<string, string> {
  switch (ruleType) {
    case 'area_coverage':
      return { coverage: 'required+complete', aoi: 'required+complete', exclusions: 'optional+complete' };
    case 'objects_covered_by_target':
      return { checked: 'required+scoped', target: 'required+complete' };
    case 'cross_layer_no_overlap':
      return { source_a: 'required+scoped', source_b: 'required+scoped' };
    case 'line_endpoints_connected':
      return { checked: 'required+scoped', connectors: 'required+complete', exit_zones: 'optional+complete' };
    default:
      return { checked: 'required+scoped' };
  }
}

/** 规则参数编辑：按规则类型渲染已知参数键（数值/枚举）。 */
function RuleParamsInput({ rule, onChange }: {
  rule: TopologyRule;
  onChange: (params: Record<string, number | string | boolean>) => void;
}) {
  const params = rule.params ?? {};
  const keys: Record<string, Array<string | { key: string; options: string[] }>> = {
    polygons_no_overlap: ['overlap_area_threshold_m2'],
    area_coverage: ['gap_area_threshold_m2'],
    objects_covered_by_target: ['outside_area_threshold_m2'],
    cross_layer_no_overlap: ['overlap_area_threshold_m2'],
    line_endpoints_connected: ['tolerance_m', { key: 'connector_kind', options: ['points', 'lines', 'points+lines'] }],
  };
  const editors = keys[rule.rule_type] ?? [];
  return (
    <span className="flex flex-wrap gap-1">
      {editors.map((ed) => {
        const key = typeof ed === 'string' ? ed : ed.key;
        if (typeof ed !== 'string') {
          return (
            <select key={key} value={String(params?.[key] ?? ed.options[0])}
              onChange={(e) => onChange({ ...params, [key]: e.target.value })}
              aria-label={key} className="rounded-sm border border-edge-subtle bg-surface px-1 py-0.5 text-micro">
              {ed.options.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          );
        }
        return (
          <label key={key} className="flex items-center gap-0.5">
            <span className="font-mono text-micro">{key}</span>
            <input
              type="number" step="any" min="0"
              value={params?.[key] != null ? Number(params[key]) : ''}
              onChange={(e) => onChange(e.target.value === '' ? { ...params } : { ...params, [key]: Number(e.target.value) })}
              aria-label={key}
              className="w-20 rounded-sm border border-edge-subtle bg-surface px-1 py-0.5 font-mono text-micro" />
          </label>
        );
      })}
    </span>
  );
}

type Translate = (key: string, params?: Record<string, string | number>) => string;

function translateError(err: unknown, t: Translate): string {
  const detail = parseApiErrorDetail(err, 'request failed');
  const code = extractCode(err);
  const map: Record<string, string> = {
    scope_not_allowed: 'errors.scopeNotAllowed',
    source_changed_after_submission: 'errors.sourceChanged',
    budget_exceeded: 'errors.budget',
    region_unsupported: 'errors.region',
    antimeridian: 'errors.region',
    polar: 'errors.region',
    extent: 'errors.region',
  };
  const key = map[code];
  if (!key) return detail;
  try {
    const translated = t(key);
    return translated || detail;
  } catch {
    return detail;
  }
}

function extractCode(err: unknown): string {
  const anyErr = err as { response?: { _data?: { code?: string } }; body?: { code?: string }; code?: string };
  return anyErr?.response?._data?.code ?? anyErr?.body?.code ?? anyErr?.code ?? '';
}

export default TopologyWorkbenchPanel;
