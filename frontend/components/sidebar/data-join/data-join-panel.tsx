'use client';

/**
 * G38 数据关联与空间富集工作台面板。
 *
 * 用户闭环：两源绑定 → 键/空间关系与基数策略 → 全量诊断 → 执行 → 结果
 * （分页表 / 加入地图 / 交付说明）。诚实纪律：诊断计数来自服务端全量扫描
 * （blocked 状态如实展示 typed 码与 correction_hint）；total_rows 是全量
 * 行数；>渲染上限时禁用「加入地图」并披露（数据本体经产物中心/G18 交付）。
 * 会话切换 epoch + sessionIdRef 双守卫（旧响应不污染新会话）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Layers, Link2, MapPin, PlayCircle, RefreshCw, Table2, X } from 'lucide-react';

import {
  AggregateInput,
  FieldMapEntry,
  JoinMode,
  JoinPlanInput,
  JoinRunView,
  PreviewResult,
  ResultPageView,
  SourceDescribeView,
  dataJoinApi,
  asDataJoinError,
} from '@/lib/api/data-join-authoring';
import { useDataJoinStore } from '@/lib/hooks/use-data-join';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { useHudStore } from '@/lib/store/useHudStore';
import { useToastStore } from '@/components/ui/toast';
import { useT } from '@/lib/i18n/useT';

interface DataJoinPanelProps {
  sessionId?: string | null;
  ownerToken?: string | null;
}

interface SideState {
  refText: string;
  describe: SourceDescribeView | null;
  loading: boolean;
}

const MODES: JoinMode[] = ['attribute', 'spatial_point_in_polygon'];

export function DataJoinPanel({ sessionId, ownerToken }: DataJoinPanelProps): React.ReactElement | null {
  const t = useT('dataJoin');
  const open = useDataJoinStore((s) => s.open);
  const presetLeftRef = useDataJoinStore((s) => s.presetLeftRef);
  const close = useDataJoinStore((s) => s.close);

  const containerRef = useRef<HTMLDivElement>(null);
  useDialogFocus({ open, containerRef, onEscape: close, initialFocusSelector: '[data-dj-focus]' });

  // ── 作者态 ──
  const [left, setLeft] = useState<SideState>({ refText: '', describe: null, loading: false });
  const [right, setRight] = useState<SideState>({ refText: '', describe: null, loading: false });
  const [mode, setMode] = useState<JoinMode>('attribute');
  const [keepSide, setKeepSide] = useState<'left' | 'inner'>('left');
  const [cardinality, setCardinality] = useState<'one_to_one' | 'many_to_one' | 'one_to_many'>('many_to_one');
  const [keyLeft, setKeyLeft] = useState('');
  const [keyRight, setKeyRight] = useState('');
  const [keyTransform, setKeyTransform] = useState<'none' | 'trim' | 'casefold'>('none');
  const [predicate, setPredicate] = useState<'within' | 'within_or_boundary'>('within');
  const [multiMatch, setMultiMatch] = useState<'require_unique' | 'expand_all' | 'pick_by_field' | 'aggregate'>('require_unique');
  const [fieldMap, setFieldMap] = useState<FieldMapEntry[]>([]);
  const [aggregates, setAggregates] = useState<AggregateInput[]>([]);
  const [outputName, setOutputName] = useState('');

  // ── 计划 / 预览 / 执行态 ──
  const [planId, setPlanId] = useState<string | null>(null);
  const [planRevision, setPlanRevision] = useState(0);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState<JoinRunView | null>(null);
  const [resultPage, setResultPage] = useState<ResultPageView | null>(null);
  const [resultCursor, setResultCursor] = useState(0);
  const [error, setError] = useState<{ code: string; message: string; hint?: string } | null>(null);
  const [toMapLoading, setToMapLoading] = useState(false);

  const sessionIdRef = useRef<string | null>(null);
  sessionIdRef.current = sessionId ?? null;
  const epochRef = useRef(0);

  useEffect(() => {
    if (!open) {
      epochRef.current += 1;
      setPreview(null); setRun(null); setResultPage(null); setError(null);
      setPlanId(null); setPlanRevision(0); setFieldMap([]); setAggregates([]);
      return;
    }
    if (presetLeftRef) {
      setLeft({ refText: presetLeftRef, describe: null, loading: false });
    }
  }, [open, presetLeftRef]);

  const api = useMemo(
    () => (sessionId ? dataJoinApi.client(sessionId, ownerToken) : null),
    [sessionId, ownerToken],
  );

  /** F1（评审）：空间模式不依赖属性键 —— 就绪判据按模式分派。
   * attribute：双键字段；spatial：双源已绑定（pick_by_field 另需择一字段）。 */
  const rulesReady =
    mode === 'attribute'
      ? Boolean(keyLeft.trim() && keyRight.trim())
      : Boolean(left.refText.trim() && right.refText.trim())
        && (multiMatch !== 'pick_by_field' || Boolean(keyRight.trim()));

  const describeSide = useCallback(async (side: 'left' | 'right') => {
    if (!api || !sessionId) return;
    const epoch = epochRef.current;
    const refText = (side === 'left' ? left : right).refText.trim();
    const setSide = side === 'left' ? setLeft : setRight;
    if (!refText) { setSide((s) => ({ ...s, describe: null })); return; }
    setSide((s) => ({ ...s, loading: true }));
    try {
      const view = await api.describe({
        kind: 'session_ref',
        session_id: sessionId,
        ref: refText,
      });
      if (epoch !== epochRef.current || sessionIdRef.current !== sessionId) return;
      setSide((s) => ({ ...s, describe: view, loading: false }));
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setSide((s) => ({ ...s, describe: null, loading: false }));
      setError(asDataJoinError(err));
    }
  }, [api, sessionId, left, right]);

  const buildPlan = useCallback((): JoinPlanInput => {
    const src = (side: SideState) => ({
      kind: 'session_ref' as const,
      session_id: sessionId ?? undefined,
      ref: side.refText.trim(),
    });
    const plan: JoinPlanInput = {
      mode,
      left: src(left),
      right: src(right),
      keep_side: keepSide,
      output_cardinality: cardinality,
      output_name: outputName.trim() || t('defaultOutputName'),
    };
    if (mode === 'attribute') {
      plan.keys = [{
        left_field: keyLeft.trim(),
        right_field: keyRight.trim(),
        transform: keyTransform === 'none' ? null : keyTransform,
      }];
    } else {
      const sp: JoinPlanInput['spatial'] = {
        predicate,
        multi_match: multiMatch,
        invalid_geometry_policy: 'exclude_count',
      };
      if (multiMatch === 'pick_by_field') {
        sp.pick_field = keyRight.trim() || null;
        sp.pick_direction = 'min';
      }
      plan.spatial = sp;
      plan.output_cardinality = multiMatch === 'expand_all' ? 'one_to_many' : 'many_to_one';
    }
    if (fieldMap.length) plan.field_map = fieldMap;
    if (aggregates.length && cardinality === 'many_to_one') {
      plan.aggregates = aggregates;
    }
    return plan;
  }, [mode, left, right, keepSide, cardinality, keyLeft, keyRight, keyTransform,
      predicate, multiMatch, fieldMap, aggregates, outputName, sessionId, t]);

  const saveOrSyncPlan = useCallback(async (): Promise<{ planId: string; revision: number } | null> => {
    if (!api) return null;
    const plan = buildPlan();
    try {
      setError(null);
      if (planId) {
        const view = await api.updatePlan(planId, plan, planRevision);
        setPlanRevision(view.revision);
        return { planId: view.plan_id, revision: view.revision };
      }
      const view = await api.createPlan(plan);
      setPlanId(view.plan_id);
      setPlanRevision(view.revision);
      return { planId: view.plan_id, revision: view.revision };
    } catch (err) {
      setError(asDataJoinError(err));
      return null;
    }
  }, [api, buildPlan, planId, planRevision]);

  const doPreview = useCallback(async () => {
    if (!api) return;
    const saved = await saveOrSyncPlan();
    if (!saved) return;
    const epoch = epochRef.current;
    setPreviewing(true); setPreview(null);
    try {
      const res = await api.preview(saved.planId, saved.revision);
      if (epoch !== epochRef.current) return;
      setPreview(res);
      // 从样本恢复列序（字段映射默认投影）
      if (res.sample_rows?.length && !fieldMap.length) {
        const cols = Object.keys(res.sample_rows[0]).filter(
          (k) => !k.startsWith('_join') && k !== '_wfid' && k !== 'match_count');
        const lcols = new Set(left.describe?.sample_fields.map((f) => f.name) ?? []);
        const rcols = new Set(right.describe?.sample_fields.map((f) => f.name) ?? []);
        setFieldMap(cols
          .filter((c) => lcols.has(c) || rcols.has(c))
          .map((c) => ({ side: (lcols.has(c) ? 'left' : 'right') as 'left' | 'right', field: c })));
      }
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError(asDataJoinError(err));
    } finally {
      if (epoch === epochRef.current) setPreviewing(false);
    }
  }, [api, saveOrSyncPlan, fieldMap.length, left.describe, right.describe]);

  const loadResultPage = useCallback(async (runId: string, cursor: number) => {
    if (!api) return;
    const epoch = epochRef.current;
    try {
      const page = await api.resultPage(runId, cursor, 50);
      if (epoch !== epochRef.current) return;
      setResultPage(page);
      setResultCursor(cursor);
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError(asDataJoinError(err));
    }
  }, [api]);

  const pollRun = useCallback(async (runId: string) => {
    if (!api) return;
    for (let i = 0; i < 240; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      if (sessionIdRef.current !== sessionId || !open) return;
      try {
        const view = await api.inspectRun(runId);
        if (sessionIdRef.current !== sessionId) return;
        setRun(view);
        if (view.status === 'completed') { await loadResultPage(runId, 0); return; }
        if (view.status === 'failed' || view.status === 'cancelled') return;
      } catch { /* 轮询失败继续 */ }
    }
  }, [api, sessionId, open, loadResultPage]);

  const doExecute = useCallback(async () => {
    if (!api) return;
    const saved = await saveOrSyncPlan();
    if (!saved) return;
    const epoch = epochRef.current;
    setRunning(true); setRun(null); setResultPage(null);
    try {
      const view = await api.execute(saved.planId, saved.revision, true);
      if (epoch !== epochRef.current) return;
      setRun(view);
      if (view.status === 'completed') {
        await loadResultPage(view.run_id, 0);
      } else if (view.status !== 'failed' && view.status !== 'cancelled') {
        void pollRun(view.run_id);
      }
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setError(asDataJoinError(err));
    } finally {
      if (epoch === epochRef.current) setRunning(false);
    }
  }, [api, saveOrSyncPlan, pollRun, loadResultPage]);

  const doCancel = useCallback(async () => {
    if (!api || !run) return;
    try {
      await api.cancelRun(run.run_id);
      useToastStore.getState().addToast(t('cancelRequested'), 'info');
    } catch (err) {
      setError(asDataJoinError(err));
    }
  }, [api, run, t]);

  const addToMap = useCallback(async () => {
    if (!api || !run || run.status !== 'completed') return;
    const sid = sessionId;
    if (!sid) return;
    setToMapLoading(true);
    try {
      const res = await api.renderRef(run.run_id);
      if (sessionIdRef.current !== sid) return;
      const layerId = `dj-${run.run_id}-${Date.now().toString(36)}`;
      const { addLayer } = useHudStore.getState();
      addLayer({
        id: layerId,
        name: outputName.trim() || t('defaultOutputName'),
        type: 'vector',
        visible: true,
        opacity: 1,
        group: 'reference',
        source: { type: 'FeatureCollection', features: [], metadata: { ref_id: res.render_ref } },
        _refId: res.render_ref,
        style: { color: '#7c3aed' },
      });
      useToastStore.getState().addToast(t('addedToMapToast', { count: res.rows }), 'success');
    } catch (err) {
      const e = asDataJoinError(err);
      setError(e);
      useToastStore.getState().addToast(e.hint ?? e.message, 'error');
    } finally {
      setToMapLoading(false);
    }
  }, [api, run, sessionId, outputName, t]);

  const swapSides = useCallback(() => {
    setLeft(right); setRight(left);
    setKeyLeft(keyRight); setKeyRight(keyLeft);
    setFieldMap((m) => m.map((e) => ({ ...e, side: e.side === 'left' ? 'right' : 'left' })));
    setPreview(null);
  }, [left, right, keyLeft, keyRight]);

  if (!open) return null;

  const renderSource = (side: 'left' | 'right') => {
    const s = side === 'left' ? left : right;
    const setS = side === 'left' ? setLeft : setRight;
    const label = side === 'left' ? t('leftSource') : t('rightSource');
    return (
      <div className="rounded-md border border-edge-subtle p-2.5" data-testid={`dj-src-${side}`}>
        <div className="flex items-center justify-between pb-1.5">
          <span className="text-caption font-medium text-ink-muted">{label}</span>
          {s.describe && (
            <span className="text-caption text-ink-muted">
              {s.describe.row_count} {t('rows')} {s.describe.source.crs ? `· ${s.describe.source.crs}` : ''}
              {s.describe.has_geometry ? ` · ${t('geometry')}` : ` · ${t('tableOnly')}`}
            </span>
          )}
        </div>
        <div className="flex gap-1.5">
          <input
            value={s.refText}
            onChange={(e) => setS((prev) => ({ ...prev, refText: e.target.value, describe: null }))}
            placeholder="ref:…"
            aria-label={label}
            className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-body-sm text-ink outline-none focus:border-status-accent"
          />
          <button
            type="button"
            onClick={() => void describeSide(side)}
            disabled={!s.refText.trim() || s.loading}
            className="shrink-0 rounded-md border border-edge-subtle px-2 py-1.5 text-body-sm text-ink-secondary hover:bg-surface-hover disabled:opacity-50"
          >
            {s.loading ? <RefreshCw size={14} className="animate-spin" aria-hidden /> : t('inspect')}
          </button>
        </div>
        {s.describe && (
          <div className="pt-1.5 text-caption text-ink-muted">
            {s.describe.sample_fields.slice(0, 8).map((f) => f.name).join(', ')}
            {s.describe.sample_fields.length > 8 ? ' …' : ''}
          </div>
        )}
      </div>
    );
  };

  const diag = preview?.diagnostics;

  return (
    <div
      className="fixed inset-0 z-40 flex items-start justify-center bg-ink/40 pt-[8vh]"
      data-testid="data-join-overlay"
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label={t('title')}
        data-testid="data-join-panel"
        className="flex max-h-[86vh] w-full max-w-[900px] flex-col overflow-hidden rounded-lg border border-edge-subtle bg-surface-raised shadow-2xl"
      >
        <div className="flex shrink-0 items-center justify-between border-b border-edge-subtle px-4 py-2.5">
          <h2 className="flex items-center gap-2 text-body-md font-semibold text-ink">
            <Link2 size={15} aria-hidden /> {t('title')}
          </h2>
          <button
            type="button" data-dj-focus onClick={close} aria-label={t('closeAria')}
            className="rounded-sm p-1 text-ink-secondary hover:bg-surface-hover hover:text-ink"
          >
            <X size={16} aria-hidden />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
          {/* 1 两源 */}
          <section className="space-y-2" data-testid="dj-step-sources">
            <div className="flex items-center justify-between">
              <h3 className="text-body-sm font-medium text-ink">{t('stepSources')}</h3>
              <button type="button" onClick={swapSides} className="text-caption text-ink-secondary underline hover:text-ink">
                {t('swapSides')}
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {renderSource('left')}
              {renderSource('right')}
            </div>
            {!sessionId && <p className="text-caption text-status-error">{t('noSession')}</p>}
          </section>

          {/* 2 规则 */}
          <section className="space-y-2 rounded-md border border-edge-subtle p-2.5" data-testid="dj-step-rules">
            <h3 className="text-body-sm font-medium text-ink">{t('stepRules')}</h3>
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={mode} onChange={(e) => { setMode(e.target.value as JoinMode); setPreview(null); }}
                aria-label={t('mode')} className="rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
              >
                {MODES.map((m) => <option key={m} value={m}>{m === 'attribute' ? t('modeAttribute') : t('modeSpatial')}</option>)}
              </select>
              <select
                value={keepSide} onChange={(e) => { setKeepSide(e.target.value as 'left' | 'inner'); setPreview(null); }}
                aria-label={t('keepSide')} className="rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
              >
                <option value="left">{t('keepLeft')}</option>
                <option value="inner">{t('keepInner')}</option>
              </select>
              {mode === 'attribute' ? (
                <select
                  value={cardinality} onChange={(e) => { setCardinality(e.target.value as typeof cardinality); setPreview(null); }}
                  aria-label={t('cardinality')} className="rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
                >
                  <option value="one_to_one">1:1</option>
                  <option value="many_to_one">N:1</option>
                  <option value="one_to_many">1:N</option>
                </select>
              ) : (
                <select
                  value={multiMatch} onChange={(e) => { setMultiMatch(e.target.value as typeof multiMatch); setPreview(null); }}
                  aria-label={t('multiMatch')} className="rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
                >
                  <option value="require_unique">{t('mmRequireUnique')}</option>
                  <option value="expand_all">{t('mmExpandAll')}</option>
                  <option value="pick_by_field">{t('mmPickByField')}</option>
                  <option value="aggregate">{t('mmAggregate')}</option>
                </select>
              )}
            </div>
            {mode === 'attribute' ? (
              <div className="flex flex-wrap items-center gap-2">
                <input
                  value={keyLeft} onChange={(e) => { setKeyLeft(e.target.value); setPreview(null); }}
                  placeholder={t('leftKeyField')} aria-label={t('leftKeyField')}
                  className="w-36 rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
                />
                <span className="text-ink-muted">=</span>
                <input
                  value={keyRight} onChange={(e) => { setKeyRight(e.target.value); setPreview(null); }}
                  placeholder={t('rightKeyField')} aria-label={t('rightKeyField')}
                  className="w-36 rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
                />
                <select
                  value={keyTransform} onChange={(e) => { setKeyTransform(e.target.value as typeof keyTransform); setPreview(null); }}
                  aria-label={t('keyTransform')} className="rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
                >
                  <option value="none">{t('transformNone')}</option>
                  <option value="trim">{t('transformTrim')}</option>
                  <option value="casefold">{t('transformCasefold')}</option>
                </select>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={predicate} onChange={(e) => { setPredicate(e.target.value as typeof predicate); setPreview(null); }}
                  aria-label={t('predicate')} className="rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
                >
                  <option value="within">{t('predWithin')}</option>
                  <option value="within_or_boundary">{t('predWithinOrBoundary')}</option>
                </select>
                {multiMatch === 'pick_by_field' && (
                  <input
                    value={keyRight} onChange={(e) => { setKeyRight(e.target.value); setPreview(null); }}
                    placeholder={t('pickField')} aria-label={t('pickField')}
                    className="w-36 rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
                  />
                )}
              </div>
            )}
            <p className="text-caption text-ink-muted">{t('nullPolicyNote')}</p>
            <input
              value={outputName} onChange={(e) => setOutputName(e.target.value)}
              placeholder={t('outputName')} aria-label={t('outputName')}
              className="w-64 rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1 text-body-sm"
            />
          </section>

          {/* 3 诊断 */}
          <section className="space-y-2 rounded-md border border-edge-subtle p-2.5" data-testid="dj-step-diagnostics">
            <div className="flex items-center justify-between">
              <h3 className="text-body-sm font-medium text-ink">{t('stepDiagnostics')}</h3>
              <div className="flex gap-1.5">
                <button
                  type="button" onClick={() => void doPreview()} disabled={previewing || !rulesReady}
                  className="inline-flex items-center gap-1.5 rounded-md border border-edge-subtle px-3 py-1.5 text-body-sm text-ink-secondary hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <RefreshCw size={14} className={previewing ? 'animate-spin' : ''} aria-hidden />
                  {t('preview')}
                </button>
                <button
                  type="button" onClick={() => void doExecute()} disabled={running || !rulesReady}
                  className="inline-flex items-center gap-1.5 rounded-md bg-status-accent px-3 py-1.5 text-body-sm font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <PlayCircle size={14} aria-hidden /> {t('execute')}
                </button>
              </div>
            </div>
            {error && (
              <div className="rounded-md border border-status-error/40 bg-status-error/10 p-2 text-body-sm text-status-error" data-testid="dj-error">
                <span className="font-mono text-caption">{error.code}</span> — {error.message}
                {error.hint && <p className="pt-0.5 text-caption">{error.hint}</p>}
              </div>
            )}
            {preview && (
              preview.status === 'blocked' ? (
                <div className="rounded-md border border-status-error/40 bg-status-error/10 p-2 text-body-sm" data-testid="dj-blocked">
                  <p className="font-medium text-status-error">
                    {t('blockedTitle', { code: preview.blocked_code ?? '' })}
                  </p>
                  <p>{preview.blocked?.message}</p>
                  {preview.blocked?.correction_hint && (
                    <p className="pt-0.5 text-caption text-ink-secondary">{preview.blocked.correction_hint}</p>
                  )}
                </div>
              ) : diag ? (
                <div className="grid grid-cols-3 gap-1.5 text-caption sm:grid-cols-4" data-testid="dj-diag">
                  {[
                    ['leftRows', diag.left_rows], ['rightRows', diag.right_rows],
                    ['matchedPairs', diag.matched_pairs], ['unmatchedLeft', diag.unmatched_left_rows],
                    ['unmatchedRight', diag.unmatched_right_rows],
                    ['leftNullKeys', diag.left_null_keys], ['rightNullKeys', diag.right_null_keys],
                    ['dupRightKeys', diag.right_duplicate_keys],
                    ['outputRows', diag.output_rows],
                  ].map(([k, v]) => (
                    <div key={String(k)} className="rounded border border-edge-subtle bg-surface-sunken px-2 py-1">
                      <span className="text-ink-muted">{t(String(k))}</span>
                      <span className="pl-1 font-mono text-ink">{String(v)}</span>
                    </div>
                  ))}
                  {diag.basis !== 'full' && (
                    <div className="col-span-full text-status-warning">{t('sampledBasis')}</div>
                  )}
                  {diag.working_crs && (
                    <div className="col-span-full text-caption text-ink-muted">
                      {t('workingCrs')}: {diag.working_crs}
                      {diag.transform_note ? ` · ${diag.transform_note}` : ''}
                    </div>
                  )}
                </div>
              ) : null
            )}
          </section>

          {/* 4 执行结果 */}
          {run && (
            <section className="space-y-2 rounded-md border border-edge-subtle p-2.5" data-testid="dj-step-result">
              <div className="flex items-center justify-between">
                <h3 className="flex items-center gap-1.5 text-body-sm font-medium text-ink">
                  {run.receipt?.output_kind === 'table' ? <Table2 size={14} aria-hidden /> : <Layers size={14} aria-hidden />}
                  {t('stepResult')} — <span className="font-mono text-caption">{run.status}</span>
                  {run.output_rows != null && (
                    <span className="text-caption text-ink-muted">{t('totalRows', { n: run.output_rows })}</span>
                  )}
                </h3>
                {(run.status === 'pending' || run.status === 'running') && (
                  <button type="button" onClick={() => void doCancel()} className="rounded-md border border-edge-subtle px-2 py-1 text-caption text-ink-secondary hover:bg-surface-hover">
                    {t('cancel')}
                  </button>
                )}
              </div>
              {run.status === 'failed' && run.error_code && (
                <p className="text-body-sm text-status-error">
                  <span className="font-mono text-caption">{run.error_code}</span>
                  {run.content_invalidated ? ` · ${t('contentInvalidated')}` : ''}
                </p>
              )}
              {resultPage && (
                <>
                  <div className="max-h-56 overflow-auto rounded border border-edge-subtle" data-testid="dj-result-table">
                    <table className="w-full text-caption">
                      <thead className="sticky top-0 bg-surface-sunken text-ink-muted">
                        <tr>
                          {Object.keys(resultPage.rows[0]?.properties ?? {}).slice(0, 12).map((c) => (
                            <th key={c} className="px-2 py-1 text-left font-medium">{c}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {resultPage.rows.map((r, i) => (
                          <tr key={i} className="border-t border-edge-subtle/60">
                            {Object.keys(resultPage.rows[0]?.properties ?? {}).slice(0, 12).map((c) => (
                              <td key={c} className="px-2 py-1 text-ink">{String(r.properties[c] ?? '')}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-caption text-ink-muted">
                      {t('pageOf', { cursor: resultCursor + 1, total: resultPage.total_rows })}
                    </span>
                    <div className="flex gap-1.5">
                      <button
                        type="button" disabled={resultPage.cursor === 0}
                        onClick={() => void loadResultPage(run.run_id, Math.max(0, resultPage.cursor - 50))}
                        className="rounded border border-edge-subtle px-2 py-1 text-caption disabled:opacity-50"
                      >{t('prevPage')}</button>
                      <button
                        type="button" disabled={resultPage.next_cursor == null}
                        onClick={() => resultPage.next_cursor != null && void loadResultPage(run.run_id, resultPage.next_cursor)}
                        className="rounded border border-edge-subtle px-2 py-1 text-caption disabled:opacity-50"
                      >{t('nextPage')}</button>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 pt-1">
                    <button
                      type="button" onClick={() => void addToMap()}
                      disabled={toMapLoading || resultPage.output_kind !== 'enriched_layer'
                        || (resultPage.total_rows ?? 0) > 10000}
                      className="inline-flex items-center gap-1.5 rounded-md border border-edge-subtle px-3 py-1.5 text-body-sm text-ink-secondary hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <MapPin size={14} aria-hidden /> {t('addToMap')}
                    </button>
                    <span className="text-caption text-ink-muted">
                      {(resultPage.total_rows ?? 0) > 10000 ? t('renderOverBudgetNote') : t('deliveryNote')}
                    </span>
                  </div>
                </>
              )}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
