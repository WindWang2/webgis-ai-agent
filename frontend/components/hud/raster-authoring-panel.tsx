'use client';

/**
 * G20 局部栅格工作台：真实波段清单、单波段/RGB、拉伸、色带、重采样、
 * 透明度、像元检视与单幅导出。
 *
 * 纪律：所有写操作走后端权威 MapSpec 提交（与 Pi 同一领域服务）；拖动只
 * 是临时预览；失败回滚到最后确认状态；不建第二套图层/样式 store。
 */
import {
  useCallback, useEffect, useMemo, useReducer, useRef, useState,
  useSyncExternalStore,
} from 'react';
import { useT, useLocale } from '@/lib/i18n/useT';
import { useToastStore } from '@/components/ui/toast';
import {
  commitMapSpecDocument, getCommittedMapSpec, getMapSpecSessionCursor,
  setMapSpecRevision, subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import { subscribeMapClick } from '@/lib/raster-authoring/map-click';
import {
  asRasterError, commitExpression, exportExpressionMap, fetchDescriptor,
  listRasterRefs, previewExpression, probePixels,
  type RasterApiContext,
} from '@/lib/raster-authoring/api';
import {
  RASTER_CLASSIFICATIONS, RASTER_COLORMAPS, RASTER_RESAMPLINGS,
  bandLabel, defaultDraft, draftFieldErrors, draftFromCommitted, isDraftDirty,
  type RasterDescriptor, type RasterErrorPayload,
  type RasterExpressionDraft, type RasterExpressionSpec,
  type RasterLegend, type RasterReceipt,
} from '@/lib/raster-authoring/contracts';
import {
  initialState, phaseLabelKey, reduceRasterAuthoring,
} from '@/lib/raster-authoring/state';

interface Props {
  layerId: string;
  /** 已挂载的数值栅格 ref；缺席时工作台先让用户选一份已登记栅格。 */
  refId?: string;
  committedStyle?: RasterExpressionSpec | null;
  /** 权威 MapSpec 的 layer.legend_spec（与样式同一笔提交，刷新后仍可用）。 */
  committedLegend?: RasterLegend | null;
  onCommitted?: (receipt: RasterReceipt) => void;
}

const inputCls =
  'w-full rounded-md border border-edge-subtle bg-surface px-2 py-1 text-body text-ink ' +
  'focus:outline-none focus:ring-2 focus:ring-status-accent';
const labelCls = 'block text-caption text-ink-muted mb-1';
const btnCls =
  'rounded-md border border-edge-subtle px-2.5 py-1.5 text-body text-ink ' +
  'hover:bg-surface-hover focus:outline-none focus:ring-2 focus:ring-status-accent ' +
  'disabled:opacity-50 disabled:cursor-not-allowed';

/**
 * 无图层时的首次挂载入口：只要求会话里已有登记的栅格 ref，不要求
 * HUD 里先有一个 raster/tile 图层。挂载后由回灌的权威 MapSpec 驱动地图。
 */
export function RasterAuthoringLauncher() {
  const t = useT('hud');
  const [open, setOpen] = useState(false);
  // 刷新/撤销/Pi 提交后从权威 MapSpec 读回同一层的表达，保证 launcher
  // 路径与 layer-style-panel 路径的恢复语义一致（复审 P2-2）。
  const liveSpec = useSyncExternalStore(subscribeMapSpecLive, getCommittedMapSpec);
  const liveLayer = (liveSpec as unknown as {
    layers?: Array<Record<string, unknown>>;
  } | null)?.layers?.find((l) => l?.id === 'raster-authoring-main');
  const committedStyle = (liveLayer?.raster_style ?? null) as RasterExpressionSpec | null;
  const committedLegend = (liveLayer?.legend_spec ?? null) as RasterLegend | null;
  return (
    <div className="px-3 pb-2 border-t border-edge-subtle pt-2">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left text-caption text-status-accent hover:underline focus:outline-none focus:ring-2 focus:ring-status-accent rounded"
      >
        {t('rasterAuthoring.title')}
      </button>
      {open && (
        <RasterAuthoringPanel
          layerId="raster-authoring-main"
          committedStyle={committedStyle}
          committedLegend={committedLegend}
        />
      )}
    </div>
  );
}


export function RasterAuthoringPanel({
  layerId, refId, committedStyle, committedLegend, onCommitted,
}: Props) {
  const t = useT('hud');
  const appLocale = useLocale();
  const locale = appLocale.startsWith('zh') ? 'zh' : 'en';
  const [state, dispatch] = useReducer(reduceRasterAuthoring, undefined, initialState);
  const [activeRef, setActiveRef] = useState(refId ?? '');
  const [refOptions, setRefOptions] = useState<string[]>([]);
  const [manualRef, setManualRef] = useState('');
  const [descriptor, setDescriptor] = useState<RasterDescriptor | null>(null);
  const [loadError, setLoadError] = useState<RasterErrorPayload | null>(null);
  const [point, setPoint] = useState<[string, string]>(['', '']);
  const [crs, setCrs] = useState('EPSG:4326');
  const [probeBands, setProbeBands] = useState<number[]>([]);
  const [followClicks, setFollowClicks] = useState(false);
  const [busy, setBusy] = useState(false);
  const probeAbort = useRef<AbortController | null>(null);
  const probeSeqRef = useRef(0);
  const fieldRefs = useRef<Record<string, HTMLElement | null>>({});

  // getCommittedMapSpec 返回稳定对象引用（快照不可每次新建，否则无限重渲染）。
  // 订阅本身触发重渲染；随后每次渲染重新读取游标（revision 用于 CAS）。
  useSyncExternalStore(subscribeMapSpecLive, getCommittedMapSpec);
  const cursor = getMapSpecSessionCursor();
  const ctx: RasterApiContext = useMemo(() => ({
    sessionId: cursor.sessionId ?? '',
    ownerToken: cursor.ownerToken,
  }), [cursor.sessionId, cursor.ownerToken]);

  const errorText = useCallback((code: string): string => {
    switch (code) {
      case 'BAND_COUNT_UNSUPPORTED': return t('rasterAuthoring.errors.BAND_COUNT_UNSUPPORTED');
      case 'BAND_OUT_OF_RANGE': return t('rasterAuthoring.errors.BAND_OUT_OF_RANGE');
      case 'STRETCH_NON_FINITE': return t('rasterAuthoring.errors.STRETCH_NON_FINITE');
      case 'STRETCH_REVERSED': return t('rasterAuthoring.errors.STRETCH_REVERSED');
      case 'UNKNOWN_COLORMAP': return t('rasterAuthoring.errors.UNKNOWN_COLORMAP');
      case 'UNSUPPORTED_RESAMPLING': return t('rasterAuthoring.errors.UNSUPPORTED_RESAMPLING');
      case 'CLASSIFIED_INTERPOLATION': return t('rasterAuthoring.errors.CLASSIFIED_INTERPOLATION');
      case 'COLORMAP_ON_RGB': return t('rasterAuthoring.errors.COLORMAP_ON_RGB');
      case 'OPACITY_OUT_OF_RANGE': return t('rasterAuthoring.errors.OPACITY_OUT_OF_RANGE');
      case 'CAS_SUPERSEDED':
      case 'MUTATION_REJECTED':
      case 'STALE_STYLE': return t('rasterAuthoring.errors.CAS_SUPERSEDED');
      case 'STALE_BINDING': return t('rasterAuthoring.errors.STALE_BINDING');
      case 'SOURCE_UNAVAILABLE':
      case 'SOURCE_NOT_NUMERIC_RASTER': return t('rasterAuthoring.unavailable');
      case 'RENDER_FAILED':
      case 'EXPORT_UNSUPPORTED': return t('rasterAuthoring.errors.RENDER_FAILED');
      default: return t('rasterAuthoring.errors.default');
    }
  }, [t]);

  // 尚未绑定数值源：列出本会话已登记的栅格 ref（有界、owner-scoped）。
  useEffect(() => {
    if (activeRef || !ctx.sessionId) return;
    const controller = new AbortController();
    void listRasterRefs({ ...ctx, signal: controller.signal })
      .then((res) => { if (!controller.signal.aborted) setRefOptions(res.refs.map((r) => r.ref_id)); })
      .catch(() => { /* 面板保留手动输入，不假装成功 */ });
    return () => controller.abort();
  }, [activeRef, ctx]);

  // 元数据/直方图：真实后端读，取消时中止窗口读取请求。
  useEffect(() => {
    if (!activeRef) return;
    const controller = new AbortController();
    let alive = true;
    setDescriptor(null);
    setLoadError(null);
    dispatch({ type: 'reset' });
    void fetchDescriptor({ ...ctx, signal: controller.signal }, activeRef)
      .then((info) => {
        if (!alive) return;
        setDescriptor(info);
        // 像元检视默认读「各波段」而不是只显示波段（Pi 工具同语义）；
        // 后端单次上限 16，超出时由面板显式选择分页。
        setProbeBands(Array.from(
          { length: Math.min(info.band_count, 16) }, (_, i) => i + 1,
        ));
        const committed = committedStyle ?? null;
        const draft = committed ? draftFromCommitted(info, committed) : defaultDraft(info);
        dispatch({ type: 'descriptor-loaded', draft, committed });
      })
      .catch((err: unknown) => {
        if (!alive || controller.signal.aborted) return;
        setLoadError(asRasterError(err));
      });
    return () => { alive = false; controller.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRef, ctx.sessionId, ctx.ownerToken]);

  // 刷新 / 撤销 / 重做 → 权威 style_id 变化：草稿回到最后一次确认状态，
  // 临时预览不污染持久版本。
  const committedId = committedStyle?.style_id ?? null;
  const prevCommittedId = useRef<string | null>(null);
  useEffect(() => {
    if (!descriptor || !committedStyle || committedId === prevCommittedId.current) return;
    prevCommittedId.current = committedId;
    dispatch({
      type: 'external-committed',
      committed: committedStyle,
      draft: draftFromCommitted(descriptor, committedStyle),
    });
  }, [committedId, committedStyle, descriptor]);

  const draft = state.draft;
  const fieldErrors = useMemo(
    () => (draft && descriptor ? draftFieldErrors(draft, descriptor.band_count) : {}),
    [draft, descriptor],
  );
  const hasFieldErrors = Object.keys(fieldErrors).length > 0;
  const dirty = !!(draft && isDraftDirty(draft, state.committed));
  const legend: RasterLegend | null | undefined =
    state.committedReceipt?.legend ?? state.previewReceipt?.legend ?? committedLegend;

  const changeDraft = useCallback((patch: Partial<RasterExpressionDraft>) => {
    if (!draft) return;
    dispatch({ type: 'draft-changed', draft: { ...draft, ...patch } });
  }, [draft]);

  const setMode = useCallback((mode: 'single' | 'rgb') => {
    if (!draft || !descriptor) return;
    const bands = mode === 'single'
      ? [draft.bands[0] ?? 1]
      : [draft.bands[0] ?? 1, draft.bands[1] ?? Math.min(2, descriptor.band_count),
         draft.bands[2] ?? Math.min(3, descriptor.band_count)];
    changeDraft({
      mode, bands,
      perChannel: bands.map((_, i) => draft.perChannel[i] ?? [0, 1] as [number, number]),
    });
  }, [draft, descriptor, changeDraft]);

  const restoreCommitted = useCallback(() => {
    if (!descriptor || !state.committed) return;
    dispatch({
      type: 'restore-committed',
      draft: draftFromCommitted(descriptor, state.committed),
    });
  }, [descriptor, state.committed]);

  const runPreview = useCallback(async () => {
    if (!draft || !ctx.sessionId || hasFieldErrors) return;
    setBusy(true);
    dispatch({ type: 'preview-start' });
    try {
      const receipt = await previewExpression(ctx, activeRef, draft, layerId);
      dispatch({ type: 'preview-ok', receipt });
    } catch (err) {
      const e = asRasterError(err);
      dispatch({ type: 'failed', error: e });
      restoreCommitted();
    } finally { setBusy(false); }
  }, [draft, ctx, activeRef, layerId, hasFieldErrors, restoreCommitted]);

  const runCommit = useCallback(async () => {
    if (!draft || !ctx.sessionId || hasFieldErrors) return;
    setBusy(true);
    dispatch({ type: 'commit-start' });
    try {
      const receipt = await commitExpression(ctx, activeRef, layerId, draft, cursor.revision);
      // 与既有 user-mutation 通道同款：立即回灌权威文档使 live map reconcile，
      // 而不是等下一条 SSE（防止「提交成功但地图仍旧」）。
      if (typeof receipt.mapspec_revision === 'number') {
        setMapSpecRevision(receipt.mapspec_revision);
      }
      if (receipt.mapspec) {
        commitMapSpecDocument(receipt.mapspec as never, receipt.mapspec_revision ?? undefined);
      }
      dispatch({ type: 'commit-ok', receipt });
      onCommitted?.(receipt);
    } catch (err) {
      const e = asRasterError(err);
      dispatch({ type: 'failed', error: e });
      restoreCommitted();
      useToastStore.getState().addToast(errorText(e.code), 'error');
    } finally { setBusy(false); }
  }, [draft, ctx, activeRef, layerId, cursor.revision, hasFieldErrors,
      restoreCommitted, onCommitted, errorText]);

  const runProbe = useCallback(async (coord: [number, number] | null) => {
    if (!ctx.sessionId || !descriptor) return;
    const x = coord ? coord[0] : Number(point[0]);
    const y = coord ? coord[1] : Number(point[1]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    const seq = ++probeSeqRef.current;
    dispatch({ type: 'probe-start', seq });
    probeAbort.current?.abort();
    const controller = new AbortController();
    probeAbort.current = controller;
    try {
      const result = await probePixels(
        { ...ctx, signal: controller.signal }, activeRef, [[x, y]], crs,
        probeBands, descriptor.data_revision,
      );
      dispatch({ type: 'probe-ok', seq, result });
    } catch (err) {
      if (controller.signal.aborted) return;
      dispatch({ type: 'probe-failed', seq, error: asRasterError(err) });
    }
  }, [ctx, descriptor, point, crs, probeBands, activeRef]);

  // 地图点选：快速点击 A、B 时只有最后一次响应能写面板（reducer 用 seq 裁决）。
  useEffect(() => {
    if (!followClicks) return;
    return subscribeMapClick((coord) => {
      setPoint([String(coord[0]), String(coord[1])]);
      void runProbe(coord);
    });
  }, [followClicks, runProbe]);

  const runExport = useCallback(async (format: 'png' | 'svg') => {
    if (!ctx.sessionId) return;
    try {
      const result = await exportExpressionMap(ctx, cursor.revision, format);
      useToastStore.getState().addToast(t('rasterAuthoring.exportDone'), 'success');
      window.open(result.download_url, '_blank', 'noopener,noreferrer');
    } catch (err) {
      useToastStore.getState().addToast(errorText(asRasterError(err).code), 'error');
    }
  }, [ctx, cursor.revision, t, errorText]);

  // 卸载/切换数据源时中止仍在排队的窗口读取，不在后台继续大读取。
  useEffect(() => () => { probeAbort.current?.abort(); }, [activeRef]);

  const focusError = useCallback(() => {
    const key = state.errorField;
    if (!key) return;
    const base = key.split('.')[0];
    fieldRefs.current[base]?.focus();
  }, [state.errorField]);

  useEffect(() => { if (state.phase === 'failed') focusError(); }, [state.phase, focusError]);

  if (!activeRef || !descriptor) {
    return (
      <section
        aria-label={t('rasterAuthoring.title')}
        data-testid="raster-authoring-panel"
        className="rounded-lg border border-edge-subtle bg-surface p-3 space-y-2"
      >
        <h3 className="text-body font-medium text-ink">{t('rasterAuthoring.title')}</h3>
        <p className="text-caption text-ink-muted">{t('rasterAuthoring.subtitle')}</p>
        {!activeRef && (
          <>
            <label className={labelCls} htmlFor={`ra-ref-${layerId}`}>{t('rasterAuthoring.selectSource')}</label>
            <select
              id={`ra-ref-${layerId}`}
              className={inputCls}
              value={manualRef}
              onChange={(e) => setManualRef(e.target.value)}
            >
              <option value="">—</option>
              {refOptions.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <input
              aria-label={t('rasterAuthoring.subtitle')}
              className={inputCls}
              value={manualRef}
              onChange={(e) => setManualRef(e.target.value)}
            />
            <button
              className={btnCls}
              disabled={!manualRef.trim()}
              onClick={() => setActiveRef(manualRef.trim())}
            >
              {t('rasterAuthoring.loadSource')}
            </button>
          </>
        )}
        {activeRef && (
          <p className="text-caption text-ink-muted">
            {loadError ? errorText(loadError.code) : t('rasterAuthoring.loading')}
          </p>
        )}
      </section>
    );
  }
  if (!draft) return null;

  const invalid = (key: string) => Boolean(fieldErrors[key] || state.errorField === key);
  const bandOptions = descriptor.bands.map((b) => (
    <option key={b.index} value={b.index}>{bandLabel(b, b.index, locale)}</option>
  ));

  return (
    <section
      aria-label={t('rasterAuthoring.title')}
      className="rounded-lg border border-edge-subtle bg-surface p-3 space-y-3"
      data-testid="raster-authoring-panel"
    >
      <header>
        <h3 className="text-body font-medium text-ink">{t('rasterAuthoring.title')}</h3>
        <p className="text-caption text-ink-muted">{t('rasterAuthoring.subtitle')}</p>
        <p className="text-caption text-ink-disabled font-mono mt-1">
          {t('rasterAuthoring.dataRevision')}: {descriptor.data_revision}
          {state.committed?.style_id ? ` · ${t('rasterAuthoring.styleId')}: ${state.committed.style_id}` : ''}
        </p>
        <p className="text-caption text-status-ok mt-0.5">{t(phaseLabelKey(state))}</p>
        {descriptor.statistics?.approximate && (
          <p className="text-caption text-ink-muted">{t('rasterAuthoring.approximate')}</p>
        )}
      </header>

      {state.error && (
        <p role="alert" className="text-caption text-status-danger">
          {errorText(state.error.code)} · {state.error.code}
        </p>
      )}

      <div>
        <label className={labelCls} htmlFor={`ra-mode-${layerId}`}>{t('rasterAuthoring.bands')}</label>
        <select
          id={`ra-mode-${layerId}`}
          className={inputCls}
          value={draft.mode}
          ref={(el) => { fieldRefs.current.bands = el; }}
          aria-invalid={invalid('bands')}
          onChange={(e) => setMode(e.target.value as 'single' | 'rgb')}
        >
          <option value="single">{t('rasterAuthoring.modeSingle')}</option>
          <option value="rgb" disabled={descriptor.band_count < 3}>
            {t('rasterAuthoring.modeRgb')}
          </option>
        </select>
        <div className="flex gap-1.5 mt-1.5">
          {draft.bands.map((band, i) => (
            <select
              key={i}
              aria-label={`${t('rasterAuthoring.band')} ${i + 1}`}
              className={inputCls}
              value={band}
              onChange={(e) => {
                const next = [...draft.bands];
                next[i] = Number(e.target.value);
                changeDraft({ bands: next });
              }}
            >
              {bandOptions}
            </select>
          ))}
        </div>
        {descriptor.band_count === 2 && (
          <p className="text-caption text-ink-muted mt-1">
            {descriptor.disclosures.find((d) => d.startsWith('two_band_default')) ?? ''}
          </p>
        )}
      </div>

      <div>
        <label className={labelCls}>
          <input
            type="checkbox"
            className="mr-1.5 align-middle"
            checked={draft.autoStretch}
            onChange={(e) => changeDraft({ autoStretch: e.target.checked })}
          />
          {t('rasterAuthoring.stretchAuto')}
        </label>
        {/* RGB 手动模式只有逐通道端点参与渲染；全局滑块若不隐藏就是改了
            style_id 却不改像素的死控件。 */}
        {!draft.autoStretch && draft.mode === 'single' && (
          <div className="flex gap-1.5">
            <input
              ref={(el) => { fieldRefs.current.stretch = el; }}
              aria-label={t('rasterAuthoring.stretchMin')}
              aria-invalid={invalid('stretch')}
              className={inputCls}
              type="number"
              step="any"
              value={draft.stretchMin}
              onChange={(e) => changeDraft({ stretchMin: Number(e.target.value) })}
            />
            <input
              aria-label={t('rasterAuthoring.stretchMax')}
              aria-invalid={invalid('stretch')}
              className={inputCls}
              type="number"
              step="any"
              value={draft.stretchMax}
              onChange={(e) => changeDraft({ stretchMax: Number(e.target.value) })}
            />
          </div>
        )}
        {!draft.autoStretch && draft.mode === 'rgb' && (
          <div className="mt-1.5 space-y-1">
            <p className="text-caption text-ink-muted">{t('rasterAuthoring.perChannel')}</p>
            {draft.bands.map((band, i) => (
              <div key={i} className="flex gap-1.5 items-center">
                <span className="text-caption w-10">{'RGB'[i]}</span>
                <input
                  aria-label={`${t('rasterAuthoring.perChannel')} ${i + 1} ${t('rasterAuthoring.stretchMin')}`}
                  className={inputCls}
                  type="number"
                  step="any"
                  value={draft.perChannel[i]?.[0] ?? 0}
                  onChange={(e) => {
                    const next = draft.perChannel.map((p) => [...p] as [number, number]);
                    next[i][0] = Number(e.target.value);
                    changeDraft({ perChannel: next });
                  }}
                />
                <input
                  aria-label={`${t('rasterAuthoring.perChannel')} ${i + 1} ${t('rasterAuthoring.stretchMax')}`}
                  className={inputCls}
                  type="number"
                  step="any"
                  value={draft.perChannel[i]?.[1] ?? 1}
                  onChange={(e) => {
                    const next = draft.perChannel.map((p) => [...p] as [number, number]);
                    next[i][1] = Number(e.target.value);
                    changeDraft({ perChannel: next });
                  }}
                />
              </div>
            ))}
          </div>
        )}
      </div>

      {draft.mode === 'single' && (
        <div>
          <label className={labelCls} htmlFor={`ra-cmap-${layerId}`}>{t('rasterAuthoring.colormap')}</label>
          <select
            id={`ra-cmap-${layerId}`}
            ref={(el) => { fieldRefs.current.colormap = el; }}
            aria-invalid={invalid('colormap')}
            className={inputCls}
            value={draft.colormap}
            onChange={(e) => changeDraft({ colormap: e.target.value })}
          >
            {RASTER_COLORMAPS.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          {legend?.palette_colors && (
            <div
              aria-hidden="true"
              className="h-2 rounded mt-1.5"
              style={{ background: `linear-gradient(to right, ${legend.palette_colors.join(',')})` }}
            />
          )}
        </div>
      )}

      <div className="grid grid-cols-2 gap-1.5">
        <div>
          <label className={labelCls} htmlFor={`ra-res-${layerId}`}>{t('rasterAuthoring.resampling')}</label>
          <select
            id={`ra-res-${layerId}`}
            ref={(el) => { fieldRefs.current.resampling = el; }}
            aria-invalid={invalid('resampling')}
            className={inputCls}
            value={draft.resampling}
            onChange={(e) => changeDraft({ resampling: e.target.value as RasterExpressionDraft['resampling'] })}
          >
            {RASTER_RESAMPLINGS.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
        <div>
          <label className={labelCls} htmlFor={`ra-cls-${layerId}`}>{t('rasterAuthoring.classification')}</label>
          <select
            id={`ra-cls-${layerId}`}
            className={inputCls}
            value={draft.classification}
            onChange={(e) => changeDraft({
              classification: e.target.value as RasterExpressionDraft['classification'],
              ...(e.target.value === 'classified' ? { resampling: 'nearest' as const } : {}),
            })}
          >
            {RASTER_CLASSIFICATIONS.map((c) => (
              <option key={c} value={c}>
                {c === 'continuous' ? t('rasterAuthoring.continuous') : t('rasterAuthoring.classified')}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label className={labelCls} htmlFor={`ra-op-${layerId}`}>
          {t('rasterAuthoring.opacity')} {Math.round(draft.opacity * 100)}%
        </label>
        <input
          id={`ra-op-${layerId}`}
          type="range" min={0} max={1} step={0.05}
          value={draft.opacity}
          onChange={(e) => changeDraft({ opacity: Number(e.target.value) })}
          className="w-full accent-status-accent"
        />
      </div>

      <div className="flex flex-wrap gap-1.5">
        <button className={btnCls} onClick={() => void runPreview()} disabled={busy || hasFieldErrors}>
          {t('rasterAuthoring.preview')}
        </button>
        <button className={btnCls} onClick={() => void runCommit()}
                disabled={busy || hasFieldErrors || !dirty || !ctx.sessionId}>
          {t('rasterAuthoring.commit')}
        </button>
        <button className={btnCls} onClick={restoreCommitted} disabled={!state.committed}>
          {t('rasterAuthoring.restore')}
        </button>
      </div>

      {legend && (
        <div className="rounded-md border border-edge-subtle p-2">
          <p className="text-caption text-ink-muted">{t('rasterAuthoring.legend')}</p>
          <p className="text-caption">
            {legend.type === 'continuous'
              ? `${legend.band_label ?? ''} · ${legend.min} – ${legend.max} · ${legend.unit ?? t('rasterAuthoring.probeUnitUnknown')}`
              : t('rasterAuthoring.rgbNoColorbar')}
          </p>
          {legend.channels?.map((ch) => (
            <p key={ch.channel} className="text-caption font-mono">
              {ch.channel} ← {ch.band_label} [{ch.min}, {ch.max}]
            </p>
          ))}
          <p className="text-caption text-ink-disabled">{t('rasterAuthoring.legendInvalid')} · {t('rasterAuthoring.legendClip')}</p>
          <p className="text-caption text-ink-disabled">{legend.stretch_provenance.method}</p>
        </div>
      )}

      <div className="rounded-md border border-edge-subtle p-2 space-y-1.5">
        <p className="text-caption text-ink-muted">{t('rasterAuthoring.probe')}</p>
        <label className="inline-flex items-center gap-1.5 text-caption">
          <input
            type="checkbox"
            checked={followClicks}
            onChange={(e) => setFollowClicks(e.target.checked)}
          />
          {t('rasterAuthoring.probeToggle')}
        </label>
        <div className="flex gap-1.5">
          <input aria-label={t('rasterAuthoring.probeX')} className={inputCls}
                 value={point[0]} onChange={(e) => setPoint([e.target.value, point[1]])} />
          <input aria-label={t('rasterAuthoring.probeY')} className={inputCls}
                 value={point[1]} onChange={(e) => setPoint([point[0], e.target.value])} />
        </div>
        <div className="flex gap-1.5">
          <select aria-label={t('rasterAuthoring.probeCrs')} className={inputCls}
                  value={crs} onChange={(e) => setCrs(e.target.value)}>
            <option value="EPSG:4326">EPSG:4326</option>
            <option value="EPSG:3857">EPSG:3857</option>
            <option value="source">source</option>
          </select>
          <button className={btnCls} onClick={() => void runProbe(null)}>
            {t('rasterAuthoring.probeRun')}
          </button>
        </div>
        <label className={labelCls} htmlFor={`ra-probe-bands-${layerId}`}>
          {t('rasterAuthoring.probeBands')}
        </label>
        <select
          id={`ra-probe-bands-${layerId}`}
          multiple
          size={Math.min(4, Math.max(2, probeBands.length))}
          aria-label={t('rasterAuthoring.probeBands')}
          className={inputCls}
          value={probeBands.map(String)}
          onChange={(e) => setProbeBands(
            Array.from(e.target.selectedOptions, (o) => Number(o.value)),
          )}
        >
          {Array.from({ length: Math.min(descriptor.band_count, 16) }, (_, i) => i + 1)
            .map((b) => (
              <option key={b} value={b}>
                {bandLabel(descriptor.bands[b - 1], b, locale)}
              </option>
            ))}
        </select>
        {state.probe?.points.map((p) => (
          <div key={p.id} className="text-caption">
            <p className="font-mono">{p.status} · {t('rasterAuthoring.probeRowCol')} {p.row},{p.col}</p>
            {p.values.map((v) => (
              <p key={v.band} className="font-mono">
                {v.label}: {t('rasterAuthoring.probeRaw')}={v.raw ?? '—'} ·
                {t('rasterAuthoring.probeScaled')}={v.scaled ?? '—'} ·
                {v.unit ?? t('rasterAuthoring.probeUnitUnknown')}
              </p>
            ))}
            {p.status === 'MASKED_PIXEL' && <p>{t('rasterAuthoring.probeMasked')}</p>}
            {p.status === 'OUTSIDE_EXTENT' && <p>{t('rasterAuthoring.probeOutside')}</p>}
          </div>
        ))}
      </div>

      <div className="flex flex-wrap gap-1.5">
        <button className={btnCls} onClick={() => void runExport('png')}>
          {t('rasterAuthoring.export')} · {t('rasterAuthoring.exportPng')}
        </button>
        <button className={btnCls} onClick={() => void runExport('svg')}>
          {t('rasterAuthoring.export')} · {t('rasterAuthoring.exportSvg')}
        </button>
      </div>
      <p className="text-caption text-ink-disabled">{t('rasterAuthoring.limits')}</p>
    </section>
  );
}
