'use client';

/**
 * G41 双变量矩阵 / 估计值+可靠性双通道作者工作台。
 *
 * 与 Pi 工具共用后端 bivariate_reliability_authoring.service：inspect →
 * preview（零 mutation）→ 一次 apply（CAS/checkpoint/undo）。任何设置改变
 * 都会使旧 preview 的 candidate_digest 失效；切层/迟到响应用 epoch + 请求
 * generation 丢弃，绝不会把旧候选提交到错误会话。
 *
 * 业务裁决（分类/矩阵/误差资格/映射）全部在服务端；本面板只做输入便利、
 * 逐字段错误定位与 canonical 图例/统计口径呈现。
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useT } from '@/lib/i18n/useT';
import { useToastStore } from '@/components/ui/toast';
import { getMapSpecSessionCursor, subscribeMapSpecLive } from '@/lib/mapspec/session-cursor';
import {
  asG41Error, commitG41Plan, inspectG41Layer, previewG41Plan,
  type G41ApiContext,
} from '@/lib/bivariate-reliability/api';
import {
  AXIS_METHODS, BOUNDARY_RULES, G41_MODES, MATRIX_IDS, MEASURE_KINDS,
  VISUAL_CHANNELS,
  type G41ErrorPayload, type G41Mode, type InspectReceipt,
  type PlanBody, type PreviewReceipt,
} from '@/lib/bivariate-reliability/contracts';

interface Props { layerId: string }

const inputCls =
  'w-full rounded-md border border-edge-subtle bg-surface px-2 py-1 text-body text-ink ' +
  'focus:outline-none focus:ring-2 focus:ring-status-accent';
const labelCls = 'block text-caption text-ink-muted mb-1';
const btnCls =
  'rounded-md border border-edge-subtle px-2.5 py-1.5 text-body text-ink ' +
  'hover:bg-surface-hover focus:outline-none focus:ring-2 focus:ring-status-accent ' +
  'disabled:opacity-50 disabled:cursor-not-allowed';
const primaryBtnCls =
  'rounded-md border border-status-accent bg-status-accent/10 px-2.5 py-1.5 text-body ' +
  'text-status-accent hover:bg-status-accent/20 focus:outline-none ' +
  'focus:ring-2 focus:ring-status-accent disabled:opacity-50 disabled:cursor-not-allowed';

function parseBreaks(text: string): number[] | undefined {
  const s = text.trim();
  if (!s) return undefined;
  return s.split(',').map((x) => Number(x.trim()));
}

function parseColors(text: string): string[] | undefined {
  const s = text.trim();
  if (!s) return undefined;
  return s.split(/[\s,]+/).filter(Boolean);
}

interface PreviewState { receipt: PreviewReceipt; formKey: string }

export function BivariateReliabilityPanel({ layerId }: Props) {
  const t = useT('hud');
  const toast = useToastStore((s) => s.addToast);
  const liveRevision = useSyncExternalStore(
    subscribeMapSpecLive, () => getMapSpecSessionCursor().revision);

  const epochRef = useRef(0);
  const reqGenRef = useRef(0);
  const [inspect, setInspect] = useState<InspectReceipt | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [previewError, setPreviewError] = useState<G41ErrorPayload | null>(null);

  // ── 双变量草稿 ──
  const [mode, setMode] = useState<G41Mode>('bivariate_matrix');
  const [axAField, setAxAField] = useState('');
  const [axBField, setAxBField] = useState('');
  const [axAUnit, setAxAUnit] = useState('');
  const [axBUnit, setAxBUnit] = useState('');
  const [axAMethod, setAxAMethod] = useState('quantiles');
  const [axBMethod, setAxBMethod] = useState('equal_interval');
  const [axABreaks, setAxABreaks] = useState('');
  const [axBBreaks, setAxBBreaks] = useState('');
  const [n, setN] = useState<2 | 3>(3);
  const [matrixId, setMatrixId] = useState('BiPurpleOrange');
  const [customColors, setCustomColors] = useState('');
  const [boundaryRule, setBoundaryRule] = useState('upper_inclusive');
  const [swapAxes, setSwapAxes] = useState(false);
  const [flipRows, setFlipRows] = useState(false);

  // ── 可靠性草稿 ──
  const [estField, setEstField] = useState('');
  const [estUnit, setEstUnit] = useState('');
  const [estK, setEstK] = useState(5);
  const [measureKind, setMeasureKind] = useState('standard_error');
  const [valueField, setValueField] = useState('');
  const [lowField, setLowField] = useState('');
  const [highField, setHighField] = useState('');
  const [level, setLevel] = useState('');
  const [measureUnit, setMeasureUnit] = useState('');
  const [methodNote, setMethodNote] = useState('');
  const [channel, setChannel] = useState('opacity');
  const [direction] = useState('higher_less_reliable');
  const [vMin, setVMin] = useState('0');
  const [vMax, setVMax] = useState('2');
  const [outMin, setOutMin] = useState('0.25');
  const [outMax, setOutMax] = useState('0.9');
  const [unknownOut, setUnknownOut] = useState('0.5');
  const [buckets, setBuckets] = useState(5);
  const [clip, setClip] = useState('clamp');

  /** 参数改变：保留旧预览但标记失效（Apply 立即禁用），不静默丢上下文。 */
  const invalidate = useCallback(() => {
    setPreviewError(null);
  }, []);

  /** 模式切换：旧预览属于另一种表达，必须整块丢弃。 */
  const invalidateMode = useCallback(() => {
    setPreview(null);
    setPreviewError(null);
  }, []);

  const reload = useCallback(async () => {
    const cursor = getMapSpecSessionCursor();
    if (!cursor.sessionId) return;
    const epoch = ++epochRef.current;
    setLoading(true);
    try {
      const r = await inspectG41Layer(
        { sessionId: cursor.sessionId, ownerToken: cursor.ownerToken }, layerId);
      if (epoch !== epochRef.current) return;
      setInspect(r);
      setLoadError(null);
      if (!axAField && r.fields.length > 0) setAxAField(r.fields[0].name);
      if (!axBField && r.fields.length > 1) setAxBField(r.fields[1].name);
      if (!estField) setEstField(r.fields[0]?.name ?? '');
      if (!valueField) setValueField(r.fields[1]?.name ?? '');
    } catch (err) {
      if (epoch !== epochRef.current) return;
      const e = asG41Error(err);
      setLoadError(`${e.code}: ${e.message}`);
    } finally {
      if (epoch === epochRef.current) setLoading(false);
    }
  }, [layerId, axAField, axBField, estField, valueField]);

  useEffect(() => {
    void reload();
    return () => { epochRef.current += 1; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layerId, liveRevision]);

  const fieldOptions = inspect?.fields ?? [];

  const buildPlan = useMemo((): PlanBody | null => {
    const anchor = inspect?.source_anchor;
    const base: PlanBody = {
      schema_version: 1,
      mode,
      ...(anchor && anchor.ref_id
        ? { source_anchor: {
            ref_id: anchor.ref_id,
            content_revision: anchor.content_revision,
            ...(anchor.content_fingerprint
              ? { content_fingerprint: anchor.content_fingerprint } : {}),
          } }
        : {}),
    };
    if (mode === 'revert') return base;
    if (mode === 'bivariate_matrix') {
      if (!axAField || !axBField) return null;
      const mkAxis = (field: string, unit: string, method: string, br: string) => {
        const axis: { field: string; unit?: string; method: 'quantiles' | 'equal_interval' | 'manual'; breaks?: number[] } = {
          field, method: method as 'quantiles' | 'equal_interval' | 'manual',
        };
        if (unit.trim()) axis.unit = unit.trim();
        if (method === 'manual') {
          const parsed = parseBreaks(br);
          if (!parsed || parsed.length !== n - 1 ||
              parsed.some((x) => !Number.isFinite(x))) return null;
          axis.breaks = parsed;
        }
        return axis;
      };
      const a = mkAxis(axAField, axAUnit, axAMethod, axABreaks);
      const b = mkAxis(axBField, axBUnit, axBMethod, axBBreaks);
      if (!a || !b) return null;
      const colors = parseColors(customColors);
      return {
        ...base,
        bivariate: {
          n, axis_a: a, axis_b: b,
          ...(colors ? { colors } : { matrix_id: matrixId }),
          boundary_rule: boundaryRule as 'upper_inclusive' | 'legacy_lower_inclusive',
          swap_axes: swapAxes,
          flip_rows: flipRows,
        },
      };
    }
    if (!estField) return null;
    const interval = measureKind === 'confidence_interval' || measureKind === 'prediction_interval';
    const lv = Number(level);
    const measure: Record<string, unknown> = {
      kind: measureKind,
      unit: measureUnit.trim(),
      source: 'author_declared',
      method_note: methodNote.trim(),
    };
    if (interval) {
      measure.low_field = lowField;
      measure.high_field = highField;
      if (level.trim() && Number.isFinite(lv)) measure.level = lv;
    } else {
      measure.value_field = valueField;
    }
    return {
      ...base,
      reliability: {
        estimate_field: estField,
        estimate_unit: estUnit.trim(),
        estimate_k: estK,
        measure: measure as never,
        visual: {
          channel: channel as 'opacity' | 'stroke_width',
          direction: direction as 'higher_less_reliable' | 'higher_more_reliable',
          min_value: Number(vMin), max_value: Number(vMax),
          min_output: Number(outMin), max_output: Number(outMax),
          unknown_output: Number(unknownOut),
          buckets, clip: clip as 'clamp' | 'reject',
        },
      },
    };
  }, [inspect, mode, axAField, axBField, axAUnit, axBUnit, axAMethod, axBMethod,
      axABreaks, axBBreaks, n, matrixId, customColors, boundaryRule, swapAxes,
      flipRows, estField, estUnit, estK, measureKind, valueField, lowField,
      highField, level, measureUnit, methodNote, channel, direction, vMin, vMax,
      outMin, outMax, unknownOut, buckets, clip]);

  /** 表单指纹：任何设置改变都会使旧预览不可直接提交（B27/B36）。 */
  const formKey = useMemo(
    () => JSON.stringify(buildPlan), [buildPlan]);

  const ctx = useMemo((): G41ApiContext | null => {
    // liveRevision 是刻意的重算信号：会话游标为外部 store，memo 体内引用
    // 以声明依赖（否则切会话后 ctx 陈旧，规则也会报 unnecessary dependency）。
    void liveRevision;
    const cursor = getMapSpecSessionCursor();
    if (!cursor.sessionId) return null;
    return { sessionId: cursor.sessionId, ownerToken: cursor.ownerToken };
  }, [liveRevision]);

  const runPreview = useCallback(async () => {
    if (!ctx || !buildPlan) return;
    const epoch = epochRef.current;
    const gen = ++reqGenRef.current;
    setBusy(true);
    setPreviewError(null);
    try {
      const r = await previewG41Plan(ctx, layerId, buildPlan);
      if (epoch !== epochRef.current || gen !== reqGenRef.current) return;
      setPreview({ receipt: r, formKey });
    } catch (err) {
      if (epoch !== epochRef.current || gen !== reqGenRef.current) return;
      setPreview(null);
      setPreviewError(asG41Error(err));
    } finally {
      // 预览可能被 Cancel（reqGenRef+1）或切层/liveRevision（epochRef+1）作废，
      // 但作废只意味着丢弃这次结果；busy 必须无条件释放，否则预览/应用按钮
      // 会永久禁用，只能重挂面板恢复（复审 P1）。与 runCommit 同一原则。
      setBusy(false);
    }
  }, [ctx, buildPlan, layerId, formKey]);

  const canApply = Boolean(preview && preview.formKey === formKey &&
    preview.receipt.candidate_digest && !busy);

  const runCommit = useCallback(async () => {
    if (!ctx || !buildPlan || !preview || !canApply) return;
    const cursor = getMapSpecSessionCursor();
    const epoch = epochRef.current;
    const gen = ++reqGenRef.current;
    setBusy(true);
    try {
      await commitG41Plan(
        ctx, layerId, buildPlan, cursor.revision,
        preview.receipt.candidate_digest,
        `g41-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
      if (epoch !== epochRef.current || gen !== reqGenRef.current) return;
      toast(t('bivariateReliability.applied'), 'success');
      setPreview(null);
      setPreviewError(null);
      await reload();
    } catch (err) {
      if (epoch !== epochRef.current || gen !== reqGenRef.current) return;
      const payload = asG41Error(err);
      const where = payload.index !== undefined
        ? ` @${payload.field ?? ''}[${payload.index}]`
        : payload.field ? ` @${payload.field}` : '';
      toast(`${payload.code}${where}: ${payload.message}`, 'error');
      setPreviewError(payload);
    } finally {
      // 提交是独占的（canApply 要求 !busy），因此本请求收尾必须无条件释放 busy。
      // 成功路径里 ``await reload()`` 会推进 epochRef，若这里再按 epoch 相等才
      // 释放，busy 将永久为真 —— 面板的预览/应用按钮此后全部禁用（复审 P1）。
      setBusy(false);
    }
  }, [ctx, buildPlan, preview, canApply, layerId, reload, toast, t]);

  const cancelDraft = useCallback(() => {
    reqGenRef.current += 1;
    setPreview(null);
    setPreviewError(null);
    void reload();
  }, [reload]);

  const fieldSelect = (id: string, label: string, value: string,
                       onChange: (v: string) => void) => (
    <label className="block min-w-0" htmlFor={id}>
      <span className={labelCls}>{label}</span>
      <select id={id} className={inputCls} value={value} title={value}
        onChange={(e) => { onChange(e.target.value); invalidate(); }}>
        <option value="">—</option>
        {fieldOptions.map((f) => (
          <option key={f.name} value={f.name} title={f.name}>{f.name}</option>
        ))}
      </select>
    </label>
  );

  const numInput = (id: string, label: string, value: string,
                    onChange: (v: string) => void, step = 'any') => (
    <label className="block min-w-0" htmlFor={id}>
      <span className={labelCls}>{label}</span>
      <input id={id} className={inputCls} type="number" step={step}
        value={value} onChange={(e) => { onChange(e.target.value); invalidate(); }} />
    </label>
  );

  // ── 渲染 ──
  return (
    <div className="rounded-lg border border-edge-subtle bg-surface-raised p-3 space-y-3"
         data-testid="g41-authoring-panel">
      <div className="flex items-center justify-between gap-2">
        <span className="text-caption font-semibold text-ink">{t('bivariateReliability.title')}</span>
        {inspect && (
          <span className="text-micro text-ink-muted truncate"
                title={inspect.source.snapshot_guarantee}>
            {t('bivariateReliability.sourceVersion')} rev {inspect.source.content_revision}
            {' · '}{inspect.source.feature_count} {t('bivariateReliability.features')}
          </span>
        )}
      </div>

      {loading && <p className="text-caption text-ink-muted">{t('bivariateReliability.loading')}</p>}
      {loadError && (
        <div className="rounded-md border border-status-error p-2 text-caption text-status-error">
          {loadError} <button type="button" className="underline" onClick={() => void reload()}>
            {t('bivariateReliability.retry')}
          </button>
        </div>
      )}
      {inspect?.notes?.map((note, i) => (
        <p key={i} className="text-caption text-status-warning">· {note}</p>
      ))}

      {inspect && (
        <>
          <label className="block" htmlFor="g41-mode">
            <span className={labelCls}>{t('bivariateReliability.mode')}</span>
            <select id="g41-mode" className={inputCls} value={mode}
              onChange={(e) => { setMode(e.target.value as G41Mode); invalidateMode(); }}>
              {G41_MODES.map((m) => (
                <option key={m.id} value={m.id}>{t(m.labelKey)}</option>
              ))}
            </select>
          </label>

          <p className="text-micro text-ink-muted break-words">
            {t('bivariateReliability.sourceNote')} {inspect.source.base_ref}
          </p>
          {inspect.fields_truncated && (
            <p className="text-micro text-status-warning">{t('bivariateReliability.fieldsTruncated')}</p>
          )}

          {mode === 'bivariate_matrix' && (
            <div className="space-y-2" data-testid="g41-bivariate-form">
              <div className="grid grid-cols-2 gap-2">
                {fieldSelect('g41-field-a', t('bivariateReliability.axisA'), axAField, setAxAField)}
                {fieldSelect('g41-field-b', t('bivariateReliability.axisB'), axBField, setAxBField)}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <label className="block" htmlFor="g41-unit-a">
                  <span className={labelCls}>{t('bivariateReliability.unitA')}</span>
                  <input id="g41-unit-a" className={inputCls} value={axAUnit}
                    onChange={(e) => { setAxAUnit(e.target.value); invalidate(); }} />
                </label>
                <label className="block" htmlFor="g41-unit-b">
                  <span className={labelCls}>{t('bivariateReliability.unitB')}</span>
                  <input id="g41-unit-b" className={inputCls} value={axBUnit}
                    onChange={(e) => { setAxBUnit(e.target.value); invalidate(); }} />
                </label>
              </div>
              {(['a', 'b'] as const).map((ax) => (
                <div key={ax} className="grid grid-cols-2 gap-2">
                  <label className="block" htmlFor={`g41-method-${ax}`}>
                    <span className={labelCls}>{t('bivariateReliability.method')} {ax.toUpperCase()}</span>
                    <select id={`g41-method-${ax}`} className={inputCls}
                      value={ax === 'a' ? axAMethod : axBMethod}
                      onChange={(e) => {
                        (ax === 'a' ? setAxAMethod : setAxBMethod)(e.target.value);
                        invalidate();
                      }}>
                      {AXIS_METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                  </label>
                  {(ax === 'a' ? axAMethod : axBMethod) === 'manual' && (
                    <label className="block" htmlFor={`g41-breaks-${ax}`}>
                      <span className={labelCls}>{t('bivariateReliability.breaks')}</span>
                      <input id={`g41-breaks-${ax}`} className={inputCls}
                        value={ax === 'a' ? axABreaks : axBBreaks}
                        placeholder={n === 3 ? '10, 20' : '10'}
                        onChange={(e) => {
                          (ax === 'a' ? setAxABreaks : setAxBBreaks)(e.target.value);
                          invalidate();
                        }} />
                    </label>
                  )}
                </div>
              ))}
              <div className="grid grid-cols-2 gap-2">
                <label className="block" htmlFor="g41-n">
                  <span className={labelCls}>{t('bivariateReliability.matrixSize')}</span>
                  <select id="g41-n" className={inputCls} value={n}
                    onChange={(e) => { setN(Number(e.target.value) as 2 | 3); invalidate(); }}>
                    <option value={2}>2 × 2</option>
                    <option value={3}>3 × 3</option>
                  </select>
                </label>
                <label className="block" htmlFor="g41-boundary">
                  <span className={labelCls}>{t('bivariateReliability.boundaryRule')}</span>
                  <select id="g41-boundary" className={inputCls} value={boundaryRule}
                    onChange={(e) => { setBoundaryRule(e.target.value); invalidate(); }}>
                    {BOUNDARY_RULES.map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                </label>
              </div>
              <label className="block" htmlFor="g41-matrix">
                <span className={labelCls}>{t('bivariateReliability.matrix')}</span>
                <select id="g41-matrix" className={inputCls} value={matrixId}
                  disabled={Boolean(customColors.trim())}
                  onChange={(e) => { setMatrixId(e.target.value); invalidate(); }}>
                  {MATRIX_IDS.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
              <label className="block" htmlFor="g41-colors">
                <span className={labelCls}>{t('bivariateReliability.customColors')}</span>
                <input id="g41-colors" className={inputCls} value={customColors}
                  placeholder={t('bivariateReliability.customColorsHint')}
                  onChange={(e) => { setCustomColors(e.target.value); invalidate(); }} />
              </label>
              <div className="flex gap-4">
                <label className="flex items-center gap-1.5 text-caption text-ink">
                  <input type="checkbox" checked={swapAxes}
                    onChange={(e) => { setSwapAxes(e.target.checked); invalidate(); }} />
                  {t('bivariateReliability.swapAxes')}
                </label>
                <label className="flex items-center gap-1.5 text-caption text-ink">
                  <input type="checkbox" checked={flipRows}
                    onChange={(e) => { setFlipRows(e.target.checked); invalidate(); }} />
                  {t('bivariateReliability.flipRows')}
                </label>
              </div>
            </div>
          )}

          {mode === 'reliability_dual_channel' && (
            <div className="space-y-2" data-testid="g41-reliability-form">
              {fieldSelect('g41-est-field', t('bivariateReliability.estimateField'), estField, setEstField)}
              <div className="grid grid-cols-2 gap-2">
                <label className="block" htmlFor="g41-est-unit">
                  <span className={labelCls}>{t('bivariateReliability.estimateUnit')}</span>
                  <input id="g41-est-unit" className={inputCls} value={estUnit}
                    onChange={(e) => { setEstUnit(e.target.value); invalidate(); }} />
                </label>
                {numInput('g41-est-k', t('bivariateReliability.estimateK'), String(estK),
                  (v) => { setEstK(Number(v) || 2); invalidate(); }, '1')}
              </div>
              <label className="block" htmlFor="g41-measure-kind">
                <span className={labelCls}>{t('bivariateReliability.measureKind')}</span>
                <select id="g41-measure-kind" className={inputCls} value={measureKind}
                  onChange={(e) => { setMeasureKind(e.target.value); invalidate(); }}>
                  {MEASURE_KINDS.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
              {measureKind === 'confidence_interval' || measureKind === 'prediction_interval' ? (
                <div className="grid grid-cols-3 gap-2">
                  {fieldSelect('g41-low', t('bivariateReliability.lowField'), lowField, setLowField)}
                  {fieldSelect('g41-high', t('bivariateReliability.highField'), highField, setHighField)}
                  {numInput('g41-level', t('bivariateReliability.level'), level, setLevel, '0.01')}
                </div>
              ) : (
                fieldSelect('g41-value', t('bivariateReliability.valueField'), valueField, setValueField)
              )}
              <div className="grid grid-cols-2 gap-2">
                <label className="block" htmlFor="g41-measure-unit">
                  <span className={labelCls}>{t('bivariateReliability.measureUnit')}</span>
                  <input id="g41-measure-unit" className={inputCls} value={measureUnit}
                    onChange={(e) => { setMeasureUnit(e.target.value); invalidate(); }} />
                </label>
                <label className="block" htmlFor="g41-method-note">
                  <span className={labelCls}>{t('bivariateReliability.methodNote')}</span>
                  <input id="g41-method-note" className={inputCls} value={methodNote}
                    onChange={(e) => { setMethodNote(e.target.value); invalidate(); }} />
                </label>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <label className="block" htmlFor="g41-channel">
                  <span className={labelCls}>{t('bivariateReliability.channel')}</span>
                  <select id="g41-channel" className={inputCls} value={channel}
                    onChange={(e) => { setChannel(e.target.value); invalidate(); }}>
                    {VISUAL_CHANNELS.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </label>
                <label className="block" htmlFor="g41-clip">
                  <span className={labelCls}>{t('bivariateReliability.clip')}</span>
                  <select id="g41-clip" className={inputCls} value={clip}
                    onChange={(e) => { setClip(e.target.value); invalidate(); }}>
                    <option value="clamp">clamp</option>
                    <option value="reject">reject</option>
                  </select>
                </label>
              </div>
              <div className="grid grid-cols-3 gap-2">
                {numInput('g41-vmin', t('bivariateReliability.domainMin'), vMin, setVMin)}
                {numInput('g41-vmax', t('bivariateReliability.domainMax'), vMax, setVMax)}
                {numInput('g41-buckets', t('bivariateReliability.buckets'), String(buckets),
                  (v) => { setBuckets(Number(v) || 2); invalidate(); }, '1')}
                {numInput('g41-omin', t('bivariateReliability.outputMin'), outMin, setOutMin)}
                {numInput('g41-omax', t('bivariateReliability.outputMax'), outMax, setOutMax)}
                {numInput('g41-unknown', t('bivariateReliability.unknownOutput'), unknownOut, setUnknownOut)}
              </div>
              <p className="text-micro text-ink-muted break-words">
                {inspect.measure_evidence_contract.disclosure}
              </p>
            </div>
          )}

          {mode === 'revert' && (
            <p className="text-caption text-ink-muted">{t('bivariateReliability.revertNote')}</p>
          )}

          <div className="flex flex-wrap gap-2">
            <button type="button" className={btnCls} disabled={!buildPlan || busy}
              onClick={() => void runPreview()}>{t('bivariateReliability.previewBtn')}</button>
            <button type="button" className={primaryBtnCls} disabled={!canApply}
              onClick={() => void runCommit()}>{t('bivariateReliability.applyBtn')}</button>
            <button type="button" className={btnCls} onClick={cancelDraft}>
              {t('bivariateReliability.cancelBtn')}
            </button>
          </div>
          {preview && preview.formKey !== formKey && (
            <p className="text-caption text-status-warning" data-testid="g41-stale-preview">
              {t('bivariateReliability.stalePreview')}
            </p>
          )}

          {previewError && (
            <div className="rounded-md border border-status-error bg-status-error/5 p-2 text-caption text-status-error space-y-1"
                 data-testid="g41-error">
              <p className="font-mono">
                {previewError.code}
                {previewError.index !== undefined ? ` [${previewError.index}]` : ''}
                {previewError.field ? ` @${previewError.field}` : ''}
              </p>
              <p>{previewError.message}</p>
              {previewError.correction_hint && (
                <p className="text-ink-muted">{previewError.correction_hint}</p>
              )}
            </div>
          )}

          {preview && (
            <div className="rounded-md border border-status-accent/60 bg-status-accent/5 p-2 space-y-2"
                 data-testid="g41-preview">
              <div className="flex justify-between text-caption">
                <span className="text-status-accent">{t('bivariateReliability.previewBadge')}</span>
                <span className="text-ink-muted font-mono">
                  {preview.receipt.candidate_digest.slice(0, 16)}
                </span>
              </div>
              <p className="text-micro text-ink-muted">
                {t('bivariateReliability.pairs')} {String(preview.receipt.summary.valid_pairs
                  ?? preview.receipt.stats_basis.valid_estimate ?? '—')}
                {' · '}{t('bivariateReliability.approx')} {String(preview.receipt.legend.approximate)}
              </p>

              {/* canonical 矩阵：屏幕序格子 + 区间 + 精确计数；键盘可到每格 */}
              {preview.receipt.legend.cells && preview.receipt.legend.cells.length > 0 && (
                <div data-testid="g41-matrix" className="space-y-1">
                  <div className="grid gap-px" style={{
                    gridTemplateColumns: `repeat(${preview.receipt.legend.n ?? 3}, minmax(0,1fr))`,
                  }}>
                    {preview.receipt.legend.cells.map((cell) => (
                      <button key={cell.class_index} type="button"
                        data-testid={`g41-cell-${cell.class_index}`}
                        className="h-9 rounded-sm border border-edge-subtle text-micro text-ink focus:outline-none focus:ring-2 focus:ring-status-accent"
                        style={{ backgroundColor: cell.color }}
                        aria-label={`#${cell.class_index}: ${cell.col_interval?.label ?? ''} / ${cell.row_interval?.label ?? ''}, n=${cell.count}${cell.empty ? ', empty' : ''}`}
                        title={`#${cell.class_index} · ${cell.col_interval?.label ?? ''} / ${cell.row_interval?.label ?? ''} · n=${cell.count}${cell.empty ? ' · empty' : ''}`}>
                        {cell.count}
                      </button>
                    ))}
                  </div>
                  <p className="text-micro text-ink-muted break-words">
                    {preview.receipt.legend.axes?.row.arrow === 'down' ? '↓' : '↑'}
                    {preview.receipt.legend.axes?.row.label} ({preview.receipt.legend.axes?.row.unit ?? '—'})
                    {' · →'}{preview.receipt.legend.axes?.col.label}
                    {' ('}{preview.receipt.legend.axes?.col.unit ?? '—'}{')'}
                    {' · '}{preview.receipt.legend.boundary_rule}
                  </p>
                </div>
              )}

              {/* canonical 可靠性：估计 + 误差量名/level/方向/缺失 */}
              {preview.receipt.legend.type === 'reliability_dual_channel' && (
                <div data-testid="g41-reliability-legend" className="space-y-1">
                  <p className="text-caption font-medium break-words">
                    {String(preview.receipt.legend.measure?.unit_note ?? '')}
                  </p>
                  <p className="text-micro text-ink-muted break-words">
                    {String(preview.receipt.legend.measure?.visual_meaning ?? '')}
                  </p>
                  {preview.receipt.legend.buckets?.map((b) => (
                    <div key={b.key} className="flex items-center gap-2 text-caption">
                      <span className="inline-block h-3.5 w-3.5 rounded-sm border border-edge-subtle bg-ink-muted"
                        style={{ opacity: b.output <= 1 ? b.output : 1 }} />
                      <span className="flex-1 truncate" title={b.label}>{b.label}</span>
                      <span className="text-ink-disabled">n={b.count}</span>
                    </div>
                  ))}
                  <p className="text-micro text-ink-muted">
                    {preview.receipt.legend.missing?.label}: {preview.receipt.legend.missing?.count}
                    {' · '}{preview.receipt.legend.missing?.policy}
                  </p>
                </div>
              )}

              {preview.receipt.legend.type === 'bivariate' && (
                <p className="text-micro text-ink-muted">
                  {preview.receipt.legend.missing?.label}: {preview.receipt.legend.missing?.count}
                  {' · '}{preview.receipt.legend.missing?.policy}
                </p>
              )}
              {preview.receipt.disclosures.slice(0, 6).map((d, i) => (
                <p key={i} className="text-micro text-ink-muted break-words">· {d}</p>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
