'use client';

/**
 * G45 坐标操作与重投影工作台面板。
 *
 * 用户闭环：源检查（权威 CRS 证据）→ 目标 CRS 查询 → 候选操作对照（含
 * 不可执行项与缺格网原因）→ 锁定（冻结操作+环境）→ 有界预览（before/after
 * 坐标对 + 发现）→ 全量执行（进度/取消）→ 成果（结果分页 / WGS84 显示副
 * 本 / 交付 ref）。诚实纪律：accuracy_unknown 绝不显示为 0；预览成功 ≠
 * 全量成功；显示副本不可用不伤主成果；刷新后状态来自服务器 plan/run 行。
 * 会话切换 epoch + sessionIdRef 双守卫（旧响应不污染新会话）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Crosshair, Lock, MapPin, PlayCircle, RefreshCw, Search, X } from 'lucide-react';

import {
  AoiInput,
  CoordOpPlanView,
  CoordOpRunView,
  CoordOpSourceRefInput,
  CrsLookupView,
  OperationsListing,
  PreviewView,
  SourceDescribeView,
  asCoordOpError,
  coordOpApi,
} from '@/lib/api/coordinate-operations';
import { useCoordinateOperationsStore } from '@/lib/hooks/use-coordinate-operations';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { useToastStore } from '@/components/ui/toast';
import { useT } from '@/lib/i18n/useT';

interface CoordinateOperationsPanelProps {
  sessionId?: string | null;
  ownerToken?: string | null;
}

const STEPS = ['source', 'candidates', 'preview', 'execute'] as const;
type Step = (typeof STEPS)[number];

export function CoordinateOperationsPanel({
  sessionId,
  ownerToken,
}: CoordinateOperationsPanelProps): React.ReactElement | null {
  const t = useT('coordinateOperations');
  const open = useCoordinateOperationsStore((s) => s.open);
  const presetSourceRef = useCoordinateOperationsStore((s) => s.presetSourceRef);
  const close = useCoordinateOperationsStore((s) => s.close);

  const containerRef = useRef<HTMLDivElement>(null);
  useDialogFocus({ open, containerRef, onEscape: close, initialFocusSelector: '[data-co-focus]' });

  // ── 作者态 ──
  const [refText, setRefText] = useState('');
  const [describe, setDescribe] = useState<SourceDescribeView | null>(null);
  const [describing, setDescribing] = useState(false);
  const [targetCrs, setTargetCrs] = useState('EPSG:32650');
  const [crsLookup, setCrsLookup] = useState<CrsLookupView | null>(null);
  const [aoi, setAoi] = useState<AoiInput | null>(null);
  const [listing, setListing] = useState<OperationsListing | null>(null);
  const [listingLoading, setListingLoading] = useState(false);
  const [selectedCandidate, setSelectedCandidate] = useState<string | null>(null);

  // ── 计划 / 预览 / 执行态（服务器为事实源） ──
  const [plan, setPlan] = useState<CoordOpPlanView | null>(null);
  const [preview, setPreview] = useState<PreviewView | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [run, setRun] = useState<CoordOpRunView | null>(null);
  const [running, setRunning] = useState(false);
  const [resultRows, setResultRows] = useState<Array<{ properties: Record<string, unknown>; first_coordinate: number[] | null }>>([]);
  const [error, setError] = useState<{ code: string; message: string; hint?: string } | null>(null);

  const sessionIdRef = useRef<string | null>(null);
  sessionIdRef.current = sessionId ?? null;
  const epochRef = useRef(0);

  const api = useMemo(
    () => (sessionId ? coordOpApi.client(sessionId, ownerToken) : null),
    [sessionId, ownerToken],
  );

  const refreshPlans = useCallback(async () => {
    if (!api) return;
    try {
      const { plans } = await api.listPlans();
      const latest = plans.find((p) => p.status !== 'archived') ?? null;
      if (latest) {
        setPlan(latest);
        setRefText((r) => r || latest.plan.source?.ref || '');
        setTargetCrs(latest.plan.target_crs);
        // 服务器预览证据水合（评审 P3-13：重开不止步于「预览」步）
        if (latest.preview && !latest.preview_stale) {
          setPreview(latest.preview as unknown as PreviewView);
        }
      }
      const { runs } = await api.listRuns();
      const latestRun = runs[0] ?? null;
      if (latestRun) {
        // list_runs 只带收据摘要 —— 重开/恢复时从 inspectRun 拉全量收据
        //（output_crs/操作 manifest/显示副本/成果 ref 在收据详情里）。
        try {
          setRun(await api.inspectRun(latestRun.run_id));
        } catch {
          setRun(latestRun);
        }
      }
    } catch { /* 列表失败不打断面板 */ }
  }, [api]);

  useEffect(() => {
    if (!open) {
      epochRef.current += 1;
      setDescribe(null); setListing(null); setCrsLookup(null);
      setPlan(null); setPreview(null); setRun(null); setResultRows([]);
      setError(null); setSelectedCandidate(null);
      return;
    }
    if (presetSourceRef) setRefText(presetSourceRef);
    // 刷新后恢复：服务器 plan/run 行是事实源（非 local state）
    void refreshPlans();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, presetSourceRef]);

  const describeSource = useCallback(async () => {
    if (!api || !sessionId) return;
    const epoch = epochRef.current;
    const ref = refText.trim();
    if (!ref) { setDescribe(null); return; }
    setDescribing(true);
    setError(null);
    try {
      const src: CoordOpSourceRefInput = { kind: 'session_ref', session_id: sessionId, ref };
      const view = await api.describeSource(src);
      if (epochRef.current !== epoch) return;
      setDescribe(view);
    } catch (err) {
      if (epochRef.current !== epoch) return;
      setDescribe(null);
      setError(asCoordOpError(err));
    } finally {
      if (epochRef.current === epoch) setDescribing(false);
    }
  }, [api, sessionId, refText]);

  const lookupCrs = useCallback(async () => {
    if (!api || !targetCrs.trim()) return;
    const epoch = epochRef.current;
    setError(null);
    try {
      const view = await api.crsLookup(targetCrs.trim());
      if (epochRef.current !== epoch) return;
      setCrsLookup(view);
    } catch (err) {
      if (epochRef.current !== epoch) return;
      setCrsLookup(null);
      setError(asCoordOpError(err));
    }
  }, [api, targetCrs]);

  const listCandidates = useCallback(async () => {
    if (!api || !sessionId || !refText.trim() || !targetCrs.trim()) return;
    const epoch = epochRef.current;
    setListingLoading(true);
    setError(null);
    try {
      const view = await api.listOperations(
        { kind: 'session_ref', session_id: sessionId, ref: refText.trim() },
        targetCrs.trim(), aoi);
      if (epochRef.current !== epoch) return;
      setListing(view);
      setSelectedCandidate(null);
    } catch (err) {
      if (epochRef.current !== epoch) return;
      setListing(null);
      setError(asCoordOpError(err));
    } finally {
      if (epochRef.current === epoch) setListingLoading(false);
    }
  }, [api, sessionId, refText, targetCrs, aoi]);

  const savePlan = useCallback(async () => {
    if (!api || !sessionId) return;
    const epoch = epochRef.current;
    setError(null);
    const planInput = {
      title: `${describe?.crs ?? ''} → ${targetCrs}`.trim(),
      source: { kind: 'session_ref' as const, session_id: sessionId, ref: refText.trim() },
      target_crs: targetCrs.trim(),
      aoi,
      aoi_derivation: aoi ? ('explicit' as const) : ('none' as const),
    };
    try {
      const view = plan && plan.status !== 'archived'
        ? await api.updatePlan(plan.plan_id, planInput, plan.revision)
        : await api.createPlan(planInput);
      if (epochRef.current !== epoch) return;
      setPlan(view);
    } catch (err) {
      if (epochRef.current !== epoch) return;
      setError(asCoordOpError(err));
    }
  }, [api, sessionId, refText, targetCrs, aoi, describe, plan]);

  const lockCandidate = useCallback(async (candidateId: string) => {
    if (!api || !plan) return;
    const epoch = epochRef.current;
    setError(null);
    try {
      const view = await api.lockPlan(plan.plan_id, candidateId, plan.revision);
      if (epochRef.current !== epoch) return;
      setPlan(view);
    } catch (err) {
      if (epochRef.current !== epoch) return;
      setError(asCoordOpError(err));
    }
  }, [api, plan]);

  const runPreview = useCallback(async () => {
    if (!api || !plan) return;
    const epoch = epochRef.current;
    setPreviewing(true);
    setError(null);
    try {
      const view = await api.preview(plan.plan_id, plan.revision);
      if (epochRef.current !== epoch) return;
      setPreview(view);
    } catch (err) {
      if (epochRef.current !== epoch) return;
      setError(asCoordOpError(err));
    } finally {
      if (epochRef.current === epoch) setPreviewing(false);
    }
  }, [api, plan]);

  const execute = useCallback(async () => {
    if (!api || !plan) return;
    const epoch = epochRef.current;
    setRunning(true);
    setError(null);
    try {
      const started = await api.execute(plan.plan_id, plan.revision, true);
      if (epochRef.current !== epoch) return;
      setRun(started);
      // 轮询直至终态（进度来自服务器 job 行）
      for (let i = 0; i < 600; i += 1) {
        await new Promise((r) => setTimeout(r, 1000));
        if (epochRef.current !== epoch) return;
        const view = await api.inspectRun(started.run_id);
        if (epochRef.current !== epoch) return;
        setRun(view);
        if (['completed', 'failed', 'cancelled'].includes(view.status)) break;
      }
    } catch (err) {
      if (epochRef.current !== epoch) return;
      setError(asCoordOpError(err));
    } finally {
      if (epochRef.current === epoch) setRunning(false);
    }
  }, [api, plan]);

  const cancelRun = useCallback(async () => {
    if (!api || !run) return;
    try { await api.cancelRun(run.run_id); } catch { /* 终态幂等 */ }
  }, [api, run]);

  const loadResults = useCallback(async (runId: string) => {
    if (!api) return;
    const epoch = epochRef.current;
    try {
      const page = await api.resultPage(runId, 0, 20);
      if (epochRef.current !== epoch) return;
      setResultRows(page.rows);
    } catch (err) {
      setError(asCoordOpError(err));
    }
  }, [api]);

  const remintDisplay = useCallback(async () => {
    if (!api || !run) return;
    const epoch = epochRef.current;
    try {
      await api.displayRef(run.run_id);
      if (epochRef.current !== epoch) return;
      useToastStore.getState().addToast(t('execute.display'), 'info');
    } catch (err) {
      setError(asCoordOpError(err));
    }
  }, [api, run, t]);

  if (!open) return null;

  const step: Step = !plan ? 'source'
    : plan.status !== 'locked' ? 'candidates'
    : !preview ? 'preview' : 'execute';
  const candidates = listing?.listing.candidates ?? [];
  const receipt = run?.receipt ?? null;

  return (
    <div
      ref={containerRef}
      data-co-focus
      className="fixed inset-y-0 right-0 z-40 flex w-full max-w-[560px] flex-col border-l border-border bg-background shadow-2xl"
      role="dialog"
      aria-label={t('title')}
    >
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Crosshair className="h-4 w-4" aria-hidden />
          {t('title')}
        </div>
        <button type="button" onClick={close} aria-label={t('close')}
          className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground">
          <X className="h-4 w-4" aria-hidden />
        </button>
      </header>

      <nav className="flex gap-1 border-b border-border px-3 py-2 text-xs">
        {STEPS.map((s, i) => (
          <span key={s}
            className={`rounded-full px-2 py-1 ${i <= STEPS.indexOf(step) ? 'bg-primary/15 text-primary' : 'text-muted-foreground'}`}>
            {t(`steps.${s}`)}
          </span>
        ))}
      </nav>

      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4 text-sm">
        {error && (
          <div role="alert" className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs">
            <div className="font-medium">{error.message}</div>
            {error.hint && <div className="mt-1 text-muted-foreground">{error.hint}</div>}
          </div>
        )}

        {/* 1 · 源与目标 */}
        <section className="space-y-2" aria-label={t('steps.source')}>
          <label className="block text-xs font-medium text-muted-foreground" htmlFor="co-src">
            {t('source.ref')}
          </label>
          <div className="flex gap-2">
            <input id="co-src" value={refText} onChange={(e) => setRefText(e.target.value)}
              placeholder={t('source.refPlaceholder')}
              className="flex-1 rounded border border-border bg-background px-2 py-1.5 text-xs" />
            <button type="button" onClick={describeSource} disabled={describing || !refText.trim()}
              className="rounded border border-border px-2 py-1 text-xs hover:bg-accent disabled:opacity-50">
              <Search className="mr-1 inline h-3 w-3" aria-hidden />{t('source.describe')}
            </button>
          </div>
          {describe && (
            <dl className="space-y-1 rounded border border-border bg-muted/40 px-3 py-2 text-xs">
              <div className="flex justify-between"><dt>{t('source.rowCount')}</dt><dd>{describe.row_count}</dd></div>
              <div className="flex justify-between"><dt>{t('source.crs')}</dt><dd className="font-mono">{describe.crs}</dd></div>
              <div className="text-muted-foreground">{t('source.crsEvidence', { origin: describe.crs_evidence?.origin ?? '' })}</div>
              <div className="flex justify-between"><dt>{t('source.geometryTypes')}</dt>
                <dd>{((describe.geometry as { types?: string[] } | null)?.types ?? []).join(', ') || '—'}</dd></div>
            </dl>
          )}

          <label className="block text-xs font-medium text-muted-foreground" htmlFor="co-tgt">
            {t('source.targetCrs')}
          </label>
          <div className="flex gap-2">
            <input id="co-tgt" value={targetCrs} onChange={(e) => setTargetCrs(e.target.value)}
              placeholder={t('source.targetPlaceholder')}
              className="flex-1 rounded border border-border bg-background px-2 py-1.5 text-xs" />
            <button type="button" onClick={lookupCrs} disabled={!targetCrs.trim()}
              className="rounded border border-border px-2 py-1 text-xs hover:bg-accent disabled:opacity-50">
              {t('source.lookup')}
            </button>
          </div>
          {crsLookup && (
            <div className={`rounded border px-3 py-2 text-xs ${crsLookup.dimension_supported ? 'border-border bg-muted/40' : 'border-destructive/40 bg-destructive/10'}`}>
              <div className="font-medium">{crsLookup.name}</div>
              <div>{t('source.axisOrder')}: {crsLookup.axis_official.map((a) => `${a.abbrev}(${a.direction}, ${a.unit_name})`).join(' · ')}</div>
              {!crsLookup.dimension_supported && <div className="text-destructive">{crsLookup.dimension_unsupported_reason}</div>}
            </div>
          )}

          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground">{t('source.aoi')}</summary>
            <div className="mt-1 grid grid-cols-4 gap-1">
              {(['west', 'south', 'east', 'north'] as const).map((k) => (
                <input key={k} type="number" step="0.0001" placeholder={k}
                  value={aoi?.[k] ?? ''}
                  onChange={(e) => {
                    const v = e.target.value === '' ? null : Number(e.target.value);
                    setAoi((prev) => {
                      const base = prev ?? { west: 0, south: 0, east: 0, north: 0 };
                      return { ...base, [k]: v ?? 0 };
                    });
                  }}
                  className="rounded border border-border px-1 py-1" />
              ))}
            </div>
            <div className="mt-1 text-muted-foreground">{t('source.aoiHint')}</div>
          </details>

          <div className="flex gap-2">
            <button type="button" onClick={listCandidates} disabled={!describe || listingLoading || !targetCrs.trim()}
              className="rounded border border-border px-2 py-1 text-xs hover:bg-accent disabled:opacity-50">
              {t('candidates.list')}
            </button>
            <button type="button" onClick={savePlan} disabled={!describe || !targetCrs.trim()}
              className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground disabled:opacity-50">
              {t('source.savePlan')}
            </button>
          </div>
          {plan && <div className="text-xs text-muted-foreground">{t('source.planSaved', { revision: plan.revision })} · {plan.status}</div>}
        </section>

        {/* 2 · 候选操作 */}
        {candidates.length > 0 && (
          <section className="space-y-2" aria-label={t('steps.candidates')}>
            {listing?.listing.environment && (
              <div className="text-xs text-muted-foreground">
                {t('candidates.envEvidence', { pyproj: listing.listing.environment.pyproj_version, proj: listing.listing.environment.proj_version })}
              </div>
            )}
            <ul className="space-y-2">
              {candidates.map((c) => (
                <li key={c.candidate_id} data-testid="co-candidate"
                    className={`rounded border px-3 py-2 text-xs ${c.availability === 'available' ? 'border-border' : 'border-border bg-muted/30'}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1">
                      <div className="font-medium">{c.name}</div>
                      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-muted-foreground">
                        <span className={c.availability === 'available' ? 'text-primary' : 'text-destructive'}>
                          {c.availability === 'available' ? t('candidates.available') : t('candidates.unavailable')}
                        </span>
                        <span>
                          {t('candidates.accuracy')}: {c.accuracy_unknown ? t('candidates.accuracyUnknown') : `${c.accuracy} m`}
                        </span>
                        {c.ballpark && <span>{t('candidates.ballpark')}</span>}
                        {c.grids.length > 0 && (
                          <span>{t('candidates.grids')}: {c.grids.map((g) => g.short_name).join(', ')}</span>
                        )}
                      </div>
                      {c.availability !== 'available' && c.missing_reason && (
                        <div className="mt-1 text-destructive">{c.missing_reason}</div>
                      )}
                    </div>
                    {c.availability === 'available' && (
                      <button type="button" onClick={() => setSelectedCandidate(c.candidate_id)}
                        className={`shrink-0 rounded border px-2 py-1 ${selectedCandidate === c.candidate_id ? 'border-primary bg-primary/10 text-primary' : 'border-border hover:bg-accent'}`}>
                        {plan?.selected_candidate_id === c.candidate_id ? t('candidates.locked') : t('candidates.lock')}
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
            {(listing?.listing.available_truncated ?? 0) > 0 && (
              <div className="text-xs text-muted-foreground">
                {t('candidates.truncated', { count: (listing?.listing.available_truncated ?? 0) + (listing?.listing.unavailable_truncated ?? 0) })}
              </div>
            )}
            <button type="button" onClick={() => selectedCandidate && lockCandidate(selectedCandidate)}
              disabled={!selectedCandidate || !plan || plan.status === 'locked'}
              className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground disabled:opacity-50">
              <Lock className="mr-1 inline h-3 w-3" aria-hidden />{t('candidates.lock')}
            </button>
            {plan?.status === 'locked' && plan.operation && (
              <div className="rounded border border-primary/40 bg-primary/5 px-3 py-2 text-xs">
                {t('candidates.lockSuccess', { revision: plan.revision })} · {plan.operation.name}
              </div>
            )}
          </section>
        )}

        {/* 3 · 预览 */}
        {plan?.status === 'locked' && (
          <section className="space-y-2" aria-label={t('steps.preview')}>
            <button type="button" onClick={runPreview} disabled={previewing}
              className="rounded border border-border px-2 py-1 text-xs hover:bg-accent disabled:opacity-50">
              {t('preview.run')}
            </button>
            {preview && (
              <div className="space-y-2 rounded border border-border bg-muted/40 px-3 py-2 text-xs">
                <div className="text-muted-foreground">{preview.sample_rule}</div>
                <table className="w-full">
                  <thead>
                    <tr className="text-muted-foreground">
                      <th className="text-left">#</th>
                      <th className="text-left">{t('preview.before')}</th>
                      <th className="text-left">{t('preview.after')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.coordinate_pairs.map((p) => (
                      <tr key={p.row} data-testid="co-preview-pair">
                        <td>{p.row}</td>
                        <td className="font-mono">{p.before[0].toFixed(5)}, {p.before[1].toFixed(5)}</td>
                        <td className="font-mono">{p.after[0].toFixed(2)}, {p.after[1].toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="text-muted-foreground">{t('preview.accuracyNote')}</div>
                <div className="text-muted-foreground">{t('preview.notFullExecution')}</div>
              </div>
            )}
          </section>
        )}

        {/* 4 · 执行与成果 */}
        {plan?.status === 'locked' && (
          <section className="space-y-2" aria-label={t('steps.execute')}>
            <div className="flex gap-2">
              <button type="button" onClick={execute} disabled={running}
                className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground disabled:opacity-50">
                <PlayCircle className="mr-1 inline h-3 w-3" aria-hidden />
                {running ? t('execute.running') : t('execute.run')}
              </button>
              {running && (
                <button type="button" onClick={cancelRun}
                  className="rounded border border-border px-2 py-1 text-xs hover:bg-accent">
                  {t('execute.cancel')}
                </button>
              )}
              <button type="button" onClick={refreshPlans}
                className="rounded border border-border px-2 py-1 text-xs hover:bg-accent">
                <RefreshCw className="mr-1 inline h-3 w-3" aria-hidden />{t('execute.refresh')}
              </button>
            </div>
            {run && (
              <div className="space-y-1 rounded border border-border px-3 py-2 text-xs">
                <div className="flex justify-between"><span>{t('execute.status')}</span>
                  <span className={run.status === 'completed' ? 'text-primary' : run.status === 'failed' ? 'text-destructive' : ''}>
                    {run.status === 'failed' ? t('execute.failed', { code: run.error_code ?? '' }) : run.status}
                  </span></div>
                {receipt && (
                  <>
                    <div className="flex justify-between"><span>{t('execute.rows')}</span>
                      <dd>{receipt.rows_transformed} / {receipt.rows_total}</dd></div>
                    <div className="flex justify-between"><span>{t('execute.vertices')}</span><dd>{receipt.total_vertices}</dd></div>
                    <div className="flex justify-between"><span>{t('execute.outputCrs')}</span><dd className="font-mono">{receipt.output_crs}</dd></div>
                    <div className="flex justify-between"><span>{t('execute.operation')}</span>
                      <dd className="truncate" title={receipt.operation.name}>{receipt.operation.name}</dd></div>
                    <div className="flex justify-between gap-2"><span>{t('execute.outputRef')}</span>
                      <dd className="truncate font-mono" title={receipt.output_ref}>{receipt.output_ref}</dd></div>
                    <div className="flex items-center justify-between">
                      <span>{t('execute.display')}</span>
                      {receipt.display?.state === 'available' ? (
                        <span className="text-primary">{receipt.display.display_ref}</span>
                      ) : (
                        <span className="text-destructive">{t('execute.displayUnavailable')}</span>
                      )}
                    </div>
                    {receipt.display?.state === 'available' && (
                      <button type="button" onClick={remintDisplay}
                        className="rounded border border-border px-2 py-1 hover:bg-accent">
                        <MapPin className="mr-1 inline h-3 w-3" aria-hidden />{t('execute.remintDisplay')}
                      </button>
                    )}
                  </>
                )}
                {run.status === 'completed' && (
                  <button type="button" onClick={() => loadResults(run.run_id)}
                    className="rounded border border-border px-2 py-1 hover:bg-accent">
                    {t('execute.results')}
                  </button>
                )}
              </div>
            )}
            {resultRows.length > 0 && (
              <ul className="max-h-40 space-y-1 overflow-y-auto rounded border border-border px-2 py-1 text-xs">
                {resultRows.map((r, i) => (
                  <li key={i} className="flex justify-between">
                    <span className="truncate">{String((r.properties as { name?: string }).name ?? (r.properties as { _wfid?: string })._wfid ?? i)}</span>
                    <span className="font-mono text-muted-foreground">
                      {r.first_coordinate ? `${r.first_coordinate[0].toFixed(1)}, ${r.first_coordinate[1].toFixed(1)}` : '∅'}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
