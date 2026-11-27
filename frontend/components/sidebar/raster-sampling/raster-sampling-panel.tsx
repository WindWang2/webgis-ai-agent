'use client';
/**
 * 栅格采样工作台（G39 / WP11）。
 *
 * 链路：describe（源）→ 计划（点集/线路 + 源/波段/方法）→ 有界预览 →
 * 执行（可后台，任务中心可见）→ 成果（分页长表 + 多单位剖面曲线 +
 * 站位点层 + G18 下载）。
 *
 * 联动纪律：
 * - 曲线缺口（part 断口 / 无效站位）断线显示，不画平滑伪线；
 * - 显示抽稀事实披露（endpoint_gap_extrema_uniform）；
 * - 表格/曲线 hover 同 sample_id；点击表格行定位地图（setViewport）；
 * - 站位点层经既有 layer 通道（fetchRefGeoJSON）加载，不新建渲染器。
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useT } from '@/lib/i18n/useT';
import { useToastStore } from '@/components/ui/toast';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  cancelPlan,
  createPlan,
  describeSources,
  executePlan,
  fetchResults,
  planStatus,
  previewPlan,
  type CurvePoint,
  type PlanPayload,
  type PreviewResponse,
  type RasterRefOption,
  type ResultsResponse,
  type SampleRow,
  type SourceSpec,
  type StatusResponse,
} from '@/lib/api/raster-sampling';
import { dataFabricApi } from '@/lib/api/data-fabric';
import { DataDeliveryDialog } from '@/components/data-delivery/delivery-dialog';
import type { DeliverySourceRef } from '@/lib/api/data-delivery';

interface PanelProps {
  sessionId?: string | null;
  ownerToken?: string | null | undefined;
}

interface SourceRow extends SourceSpec {
  key: string;
}

const MAX_SOURCES = 4;

function parseBands(text: string): number[] {
  return text
    .split(',')
    .map((s) => Number.parseInt(s.trim(), 10))
    .filter((n) => Number.isFinite(n) && n >= 1);
}

function fmtBytes(n: number): string {
  if (n > 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n > 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n > 1e3) return `${(n / 1e3).toFixed(0)} KB`;
  return `${n} B`;
}

function invalidLabel(t: (k: string) => string, reason: string | null): string {
  if (!reason) return '';
  const map: Record<string, string> = {
    outside_extent: 'reasonOutside',
    nodata: 'reasonNodata',
    masked: 'reasonMasked',
    non_finite: 'reasonNonFinite',
    masked_support: 'reasonMaskedSupport',
    transform_failed: 'reasonTransform',
    insufficient_support: 'reasonSupport',
  };
  const key = map[reason];
  return key ? t(key) : reason;
}

export function RasterSamplingPanel({ sessionId, ownerToken }: PanelProps) {
  const t = useT('rasterSampling');
  const addToast = useToastStore((s) => s.addToast);
  const addLayer = useHudStore((s) => s.addLayer);
  const updateLayer = useHudStore((s) => s.updateLayer);
  const removeLayer = useHudStore((s) => s.removeLayer);
  const setViewport = useHudStore((s) => s.setViewport);

  // ── 源与计划表单状态 ──
  const [rasterRefs, setRasterRefs] = useState<RasterRefOption[]>([]);
  const [loadingRefs, setLoadingRefs] = useState(false);
  const [mode, setMode] = useState<'source_points' | 'line_stations'>('source_points');
  const [pointsRef, setPointsRef] = useState('');
  const [linesRef, setLinesRef] = useState('');
  const [lineFeatureId, setLineFeatureId] = useState('');
  const [useSelection, setUseSelection] = useState(false);
  const [selectionRef, setSelectionRef] = useState('');
  const [selectionHandle, setSelectionHandle] = useState('');
  const [stationCrs, setStationCrs] = useState('EPSG:4326');
  const [distanceMode, setDistanceMode] = useState('geodesic');
  const [stationStep, setStationStep] = useState('100');
  const [includeVertices, setIncludeVertices] = useState(true);
  const [method, setMethod] = useState('source_pixel');
  const [planName, setPlanName] = useState('');
  const [sources, setSources] = useState<SourceRow[]>([
    { key: 's0', ref_id: '', bands: '1', band_semantics: {}, band_units: {} } as unknown as SourceRow,
  ]);
  const [bandsText, setBandsText] = useState<Record<string, string>>({ s0: '1' });

  // ── 计划 / 预览 / 执行状态 ──
  const [plan, setPlan] = useState<PlanPayload | null>(null);
  const [stationCount, setStationCount] = useState<number | null>(null);
  const [stationNotes, setStationNotes] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [previewData, setPreviewData] = useState<PreviewResponse | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [statusData, setStatusData] = useState<StatusResponse | null>(null);
  const [results, setResults] = useState<ResultsResponse | null>(null);
  const [resultsPage, setResultsPage] = useState(1);
  const [loadingResults, setLoadingResults] = useState(false);
  const [hoverSampleId, setHoverSampleId] = useState<string | null>(null);
  const [deliverySource, setDeliverySource] = useState<DeliverySourceRef | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  /** 会话代次（R2 #2190）：切换 sessionId 时 +1，在飞响应到达后不匹配即丢弃。 */
  const sessionEpochRef = useRef(0);

  const opts = useMemo(() => ({ ownerToken }), [ownerToken]);

  // R2 #2190：切换会话先停旧轮询、清计划/预览/成果/状态 —— 旧会话的
  // 后台轮询不再打新 sessionId，旧结果也不泄入新会话。
  useEffect(() => {
    sessionEpochRef.current += 1;
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setPlan(null);
    setStationCount(null);
    setStationNotes([]);
    setPreviewData(null);
    setResults(null);
    setStatusData(null);
    setHoverSampleId(null);
    setDeliverySource(null);
    setCreating(false);
    setPreviewing(false);
    setExecuting(false);
  }, [sessionId]);

  const loadRefs = useCallback(async () => {
    if (!sessionId) return;
    const epoch = sessionEpochRef.current;
    setLoadingRefs(true);
    try {
      const res = await describeSources(sessionId, opts);
      if (epoch !== sessionEpochRef.current) return;
      setRasterRefs(res.raster_refs || []);
    } catch (err) {
      if (epoch !== sessionEpochRef.current) return;
      addToast(`${t('errorPrefix')}: ${(err as Error).message.slice(0, 120)}`, 'error');
    } finally {
      if (epoch === sessionEpochRef.current) setLoadingRefs(false);
    }
  }, [sessionId, opts, addToast, t]);

  useEffect(() => {
    void loadRefs();
  }, [loadRefs]);

  useEffect(() => () => {
    if (pollRef.current) clearInterval(pollRef.current);
  }, []);

  const setSourceField = (key: string, field: string, value: unknown) => {
    setSources((prev) => prev.map((s) => (s.key === key ? { ...s, [field]: value } : s)));
  };

  const buildSourceSpecs = (): SourceSpec[] | null => {
    const specs: SourceSpec[] = [];
    for (const s of sources) {
      if (!s.ref_id) continue;
      const bands = parseBands(bandsText[s.key] || '1');
      if (!bands.length) {
        addToast(`${t('bands')}: ${s.ref_id}`, 'error');
        return null;
      }
      const semantics: Record<string, string> = {};
      const units: Record<string, string> = {};
      for (const b of bands) {
        const sem = (s as unknown as Record<string, unknown>)[`sem_${b}`] as string | undefined;
        if (sem === 'categorical' || sem === 'continuous') semantics[String(b)] = sem;
        const unit = (s as unknown as Record<string, unknown>)[`unit_${b}`] as string | undefined;
        if (unit) units[String(b)] = unit;
      }
      specs.push({ ref_id: s.ref_id, bands, band_semantics: semantics, band_units: units });
    }
    return specs.length ? specs : null;
  };

  const handleCreate = async () => {
    if (!sessionId) {
      addToast(t('sessionMissing'), 'error');
      return;
    }
    const specs = buildSourceSpecs();
    if (!specs) {
      addToast(t('noRasterRefs'), 'error');
      return;
    }
    const epoch = sessionEpochRef.current;
    setCreating(true);
    try {
      const res = await createPlan(sessionId, {
        mode,
        points_ref: mode === 'source_points' && !useSelection ? pointsRef : '',
        selection: (mode === 'source_points' && useSelection
          && selectionRef && selectionHandle)
          ? { ref_id: selectionRef, handle: selectionHandle }
          : null,
        lines_ref: mode === 'line_stations' ? linesRef : '',
        line_feature_id: lineFeatureId,
        station_crs: stationCrs,
        distance_mode: mode === 'line_stations' ? distanceMode : 'geodesic',
        station_step: mode === 'line_stations' ? Number.parseFloat(stationStep) || null : null,
        include_vertices: includeVertices,
        method,
        name: planName,
        sources: specs,
      }, opts);
      if (epoch !== sessionEpochRef.current) return;
      setPlan(res.plan);
      setStationCount(res.stations.count);
      setStationNotes(res.stations.notes || []);
      setPreviewData(null);
      setResults(null);
      addToast(`${t('planCreated')} · ${res.stations.count}`, 'success');
    } catch (err) {
      addToast(`${t('errorPrefix')}: ${(err as Error).message.slice(0, 160)}`, 'error');
    } finally {
      setCreating(false);
    }
  };

  const handlePreview = async () => {
    if (!sessionId || !plan) {
      addToast(t('planRequired'), 'error');
      return;
    }
    const epoch = sessionEpochRef.current;
    setPreviewing(true);
    try {
      const res = await previewPlan(sessionId, plan.plan_id, plan.revision, 32, opts);
      if (epoch !== sessionEpochRef.current) return;
      setPreviewData(res);
    } catch (err) {
      if (epoch !== sessionEpochRef.current) return;
      addToast(`${t('errorPrefix')}: ${(err as Error).message.slice(0, 160)}`, 'error');
    } finally {
      if (epoch === sessionEpochRef.current) setPreviewing(false);
    }
  };

  const refreshStatus = useCallback(async (planId: string) => {
    if (!sessionId) return null;
    const epoch = sessionEpochRef.current;
    try {
      const res = await planStatus(sessionId, planId, opts);
      if (epoch !== sessionEpochRef.current) return null;
      setStatusData(res);
      return res;
    } catch (err) {
      // 评审 P2：这里曾裸 catch 吞掉一切错误 —— 轮询腿 st 恒 null，终态永不
      // 识别、interval 不清、executing 永久卡真，且用户看不到任何报错。
      if (epoch !== sessionEpochRef.current) return null;
      addToast(`${t('errorPrefix')}: ${(err as Error).message.slice(0, 160)}`, 'error');
      return null;
    }
  }, [sessionId, opts, addToast, t]);

  const loadResultsPage = useCallback(async (page: number) => {
    if (!sessionId || !plan) return;
    const epoch = sessionEpochRef.current;
    setLoadingResults(true);
    try {
      const res = await fetchResults(sessionId, {
        planId: plan.plan_id, page, perPage: 50, includeCurves: true,
      }, opts);
      if (epoch !== sessionEpochRef.current) return;
      setResults(res);
      setResultsPage(page);
    } catch (err) {
      if (epoch !== sessionEpochRef.current) return;
      addToast(`${t('errorPrefix')}: ${(err as Error).message.slice(0, 160)}`, 'error');
    } finally {
      if (epoch === sessionEpochRef.current) setLoadingResults(false);
    }
  }, [sessionId, plan, opts, addToast, t]);

  const handleExecute = async (background: boolean) => {
    if (!sessionId || !plan) {
      addToast(t('planRequired'), 'error');
      return;
    }
    const epoch = sessionEpochRef.current;
    const planId = plan.plan_id;
    setExecuting(true);
    // R2 #2190：后台任务启动后 executing 必须保持到轮询终点 —— 旧代码
    // finally 立即复位，状态与真实运行不符。
    let backgroundRunning = false;
    try {
      const res = await executePlan(sessionId, planId, plan.revision, background, opts);
      if (epoch !== sessionEpochRef.current) return;
      await refreshStatus(planId);
      if (res.receipt) {
        addToast(`${t('receipt')}: ${res.receipt.execution_id}`, 'success');
        await loadResultsPage(1);
      } else if (res.job_id) {
        addToast(`${t('jobState')}: ${res.status ?? 'running'} (#${res.job_id})`, 'info');
        if (background && pollRef.current === null) {
          backgroundRunning = true;
          pollRef.current = setInterval(async () => {
            const st = await refreshStatus(planId);
            if (epoch !== sessionEpochRef.current) return;
            const jobStatus = st?.job?.status;
            if (jobStatus && ['completed', 'failed', 'cancelled', 'stale'].includes(jobStatus)) {
              if (pollRef.current) clearInterval(pollRef.current);
              pollRef.current = null;
              setExecuting(false);
              if (jobStatus === 'completed') await loadResultsPage(1);
            } else if (!st) {
              // 状态查询失败（refreshStatus 已 toast 披露）—— 停掉轮询并解除
              // executing，否则每 2.5s 无限打后端且按钮永久卡 loading。
              if (pollRef.current) clearInterval(pollRef.current);
              pollRef.current = null;
              setExecuting(false);
            }
          }, 2500);
        }
      } else {
        addToast(`${t('executeFailed')}: ${res.status ?? ''}`, 'warning');
      }
    } catch (err) {
      if (epoch !== sessionEpochRef.current) return;
      addToast(`${t('errorPrefix')}: ${(err as Error).message.slice(0, 160)}`, 'error');
    } finally {
      if (!backgroundRunning) setExecuting(false);
    }
  };

  const handleCancel = async () => {
    if (!sessionId || !plan) return;
    try {
      await cancelPlan(sessionId, plan.plan_id, opts);
      await refreshStatus(plan.plan_id);
    } catch (err) {
      addToast(`${t('errorPrefix')}: ${(err as Error).message.slice(0, 120)}`, 'error');
    }
  };

  const handleLoadStationLayer = async () => {
    if (!sessionId || !results?.station_layer_ref) return;
    const ref = results.station_layer_ref;
    const layerId = `raster-sampling:${results.execution_id}`;
    addLayer({
      id: layerId,
      name: `raster-sampling:${results.execution_id.slice(0, 12)}`,
      type: 'vector',
      visible: true,
      opacity: 1,
      group: 'reference',
      source: { type: 'FeatureCollection', features: [] },
      _refId: ref,
      style: { color: '#0ea5e9' },
    });
    try {
      const geojson = await dataFabricApi.fetchRefGeoJSON(ref, sessionId, { ownerToken });
      if (geojson && Array.isArray(geojson.features) && geojson.features.length > 0) {
        updateLayer(layerId, { source: geojson });
        addToast(t('layerLoaded'), 'success');
      } else {
        removeLayer(layerId);
        addToast(t('layerLoadFailed'), 'warning');
      }
    } catch {
      removeLayer(layerId);
      addToast(t('layerLoadFailed'), 'warning');
    }
  };

  const locateStation = (row: SampleRow) => {
    setViewport([row.station_x, row.station_y], 13, 0, 0);
    setHoverSampleId(row.sample_id);
  };

  const unitOfBand = useCallback((sourceRef: string, band: number): string => {
    const row = (results?.rows || []).find(
      (r) => r.source_ref === sourceRef && r.band === band);
    return row?.unit || t('unknownUnit');
  }, [results, t]);

  const label = 'text-xs font-medium text-ink-muted';
  const input = 'w-full rounded border border-edge-subtle bg-surface-raised px-2 py-1 text-xs';
  const btn = 'rounded border border-edge-subtle px-2 py-1 text-xs hover:bg-surface-hover disabled:opacity-50';
  const btnPrimary = 'rounded bg-accent text-white px-2 py-1 text-xs hover:opacity-90 disabled:opacity-50';

  return (
    <section aria-label={t('title')} className="space-y-3 p-2" data-testid="raster-sampling-panel">
      <header className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold">{t('title')}</h3>
        <button className={btn} onClick={() => void loadRefs()} disabled={loadingRefs}>
          {loadingRefs ? '…' : t('refreshRefs')}
        </button>
      </header>
      <p className="text-xs text-ink-muted">{t('subtitle')}</p>

      {/* ── 站位输入 ── */}
      <div className="space-y-2 rounded border border-edge-subtle p-2">
        <div className="flex gap-2">
          <label className="flex items-center gap-1 text-xs">
            <input type="radio" checked={mode === 'source_points'}
              onChange={() => setMode('source_points')} />
            {t('modePoints')}
          </label>
          <label className="flex items-center gap-1 text-xs">
            <input type="radio" checked={mode === 'line_stations'}
              onChange={() => setMode('line_stations')} />
            {t('modeLines')}
          </label>
        </div>
        {mode === 'source_points' ? (
          <div className="space-y-1">
            <div>
              <span className={label}>{t('pointsRef')}</span>
              <input className={input} value={pointsRef}
                onChange={(e) => setPointsRef(e.target.value)}
                placeholder="ref:data-…" title={t('pointsRefHint')} />
            </div>
            <label className="flex items-center gap-1 text-xs">
              <input type="checkbox" checked={useSelection}
                onChange={(e) => setUseSelection(e.target.checked)} />
              {t('selectionUse')}
            </label>
            {useSelection && (
              <div className="grid grid-cols-2 gap-1">
                <div>
                  <span className={label}>{t('selectionRef')}</span>
                  <input className={input} value={selectionRef}
                    onChange={(e) => setSelectionRef(e.target.value)} />
                </div>
                <div>
                  <span className={label}>{t('selectionHandle')}</span>
                  <input className={input} value={selectionHandle}
                    onChange={(e) => setSelectionHandle(e.target.value)} />
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-1">
            <div>
              <span className={label}>{t('linesRef')}</span>
              <input className={input} value={linesRef}
                onChange={(e) => setLinesRef(e.target.value)}
                placeholder="ref:data-…" title={t('linesRefHint')} />
            </div>
            <div className="grid grid-cols-2 gap-1">
              <div>
                <span className={label}>{t('lineFeatureId')}</span>
                <input className={input} value={lineFeatureId}
                  onChange={(e) => setLineFeatureId(e.target.value)} />
              </div>
              <div>
                <span className={label}>{t('stationStep')}</span>
                <input className={input} value={stationStep}
                  onChange={(e) => setStationStep(e.target.value)} inputMode="decimal" />
              </div>
              <div>
                <span className={label}>{t('distanceMode')}</span>
                <select className={input} value={distanceMode}
                  onChange={(e) => setDistanceMode(e.target.value)}>
                  <option value="geodesic">{t('distanceGeodesic')}</option>
                  <option value="planar_metric">{t('distancePlanar')}</option>
                </select>
              </div>
              <div>
                <span className={label}>{t('stationCrs')}</span>
                <input className={input} value={stationCrs}
                  onChange={(e) => setStationCrs(e.target.value)} />
              </div>
            </div>
            <label className="flex items-center gap-1 text-xs">
              <input type="checkbox" checked={includeVertices}
                onChange={(e) => setIncludeVertices(e.target.checked)} />
              {t('includeVertices')}
            </label>
          </div>
        )}
      </div>

      {/* ── 源与波段 ── */}
      <div className="space-y-2 rounded border border-edge-subtle p-2">
        <div className="flex items-center justify-between">
          <span className={label}>{t('sources')}</span>
          {sources.length < MAX_SOURCES && (
            <button className={btn} onClick={() => {
              const key = `s${Date.now() % 100000}`;
              setSources((p) => [...p, { key, ref_id: '', bands: '1' } as unknown as SourceRow]);
              setBandsText((p) => ({ ...p, [key]: '1' }));
            }}>{t('addSource')}</button>
          )}
        </div>
        {rasterRefs.length === 0 && (
          <p className="text-xs text-ink-muted">{t('noRasterRefs')}</p>
        )}
        {sources.map((s) => {
          const bands = parseBands(bandsText[s.key] || '1');
          return (
            <div key={s.key} className="space-y-1 rounded border border-edge-subtle/60 p-1.5">
              <div className="flex gap-1">
                <select className={input} value={s.ref_id}
                  onChange={(e) => setSourceField(s.key, 'ref_id', e.target.value)}>
                  <option value="">—</option>
                  {rasterRefs.map((r) => (
                    <option key={r.ref_id} value={r.ref_id}>
                      {r.alias || r.ref_id}
                    </option>
                  ))}
                </select>
                <button className={btn} onClick={() =>
                  setSources((p) => p.filter((x) => x.key !== s.key))}>
                  {t('removeSource')}
                </button>
              </div>
              <div>
                <span className={label}>{t('bands')}</span>
                <input className={input} value={bandsText[s.key] || '1'}
                  onChange={(e) => setBandsText((p) => ({ ...p, [s.key]: e.target.value }))} />
              </div>
              {bands.map((b) => (
                <div key={b} className="grid grid-cols-2 gap-1">
                  <div>
                    <span className={label}>{t('bandSemantics')} · {b}</span>
                    <select className={input} value={
                      ((s as unknown as Record<string, unknown>)[`sem_${b}`] as string) || ''}
                      onChange={(e) => setSourceField(s.key, `sem_${b}`, e.target.value)}>
                      <option value="">{t('semanticUndeclared')}</option>
                      <option value="categorical">{t('semanticCategorical')}</option>
                      <option value="continuous">{t('semanticContinuous')}</option>
                    </select>
                  </div>
                  <div>
                    <span className={label}>{t('bandUnit')} · {b}</span>
                    <input className={input} value={
                      ((s as unknown as Record<string, unknown>)[`unit_${b}`] as string) || ''}
                      onChange={(e) => setSourceField(s.key, `unit_${b}`, e.target.value)} />
                  </div>
                </div>
              ))}
            </div>
          );
        })}
        <div className="grid grid-cols-2 gap-1">
          <div>
            <span className={label}>{t('method')}</span>
            <select className={input} value={method} onChange={(e) => setMethod(e.target.value)}>
              <option value="source_pixel">{t('methodSourcePixel')}</option>
              <option value="bilinear">{t('methodBilinear')}</option>
              <option value="bilinear_renormalized">{t('methodRenorm')}</option>
            </select>
          </div>
          <div>
            <span className={label}>{t('planName')}</span>
            <input className={input} value={planName}
              onChange={(e) => setPlanName(e.target.value)} />
          </div>
        </div>
      </div>

      {/* ── 计划操作 ── */}
      <div className="flex flex-wrap items-center gap-2">
        <button className={btnPrimary} onClick={() => void handleCreate()} disabled={creating || !sessionId}>
          {creating ? '…' : t('createPlan')}
        </button>
        <button className={btn} onClick={() => void handlePreview()}
          disabled={previewing || !plan}>
          {previewing ? t('previewing') : t('preview')}
        </button>
        <button className={btnPrimary} onClick={() => void handleExecute(false)}
          disabled={executing || !plan}>
          {executing ? t('executing') : t('execute')}
        </button>
        <button className={btn} onClick={() => void handleExecute(true)}
          disabled={executing || !plan}>
          {t('backgroundRun')}
        </button>
        <button className={btn} onClick={() => void handleCancel()} disabled={!plan}>
          {t('cancel')}
        </button>
        <button className={btn} onClick={() => plan && void refreshStatus(plan.plan_id)} disabled={!plan}>
          {t('status')}
        </button>
        <button className={btn} onClick={() => void loadResultsPage(1)} disabled={!plan}>
          {loadingResults ? '…' : t('loadResults')}
        </button>
      </div>

      {/* ── 计划摘要 ── */}
      {plan && (
        <div className="rounded border border-edge-subtle p-2 text-xs space-y-1">
          <div className="flex flex-wrap gap-3">
            <span>{t('planId')}: <code>{plan.plan_id}</code></span>
            <span>{t('revision')}: {plan.revision}</span>
            {stationCount != null && <span>{t('stationsCount')}: {stationCount}</span>}
            {mode === 'line_stations' && results?.curves && (
              <span>{t('unitsMixed')}</span>
            )}
          </div>
          {stationNotes.length > 0 && (
            <p className="text-ink-muted">{t('stationNotes')}: {stationNotes.join('; ')}</p>
          )}
        </div>
      )}

      {/* ── 预览 ── */}
      {previewData && (
        <div className="space-y-1 rounded border border-edge-subtle p-2">
          <div className="text-xs font-semibold">
            {t('preview')} · {previewData.station_selection.previewed_stations}/
            {previewData.station_selection.requested_stations}
          </div>
          <div className="flex flex-wrap gap-3 text-xs text-ink-muted">
            <span>{t('opens')}: {previewData.read_metrics.opens}</span>
            <span>{t('blocks')}: {previewData.read_metrics.blocks_touched}</span>
            <span>{t('windowReads')}: {previewData.read_metrics.window_reads}</span>
            <span>{t('decodedBytes')}: {fmtBytes(previewData.read_metrics.decoded_bytes)}</span>
            <span>{t('coverage')}: {previewData.coverage_preview.valid_cells}/
              {previewData.coverage_preview.requested_cells}</span>
          </div>
          <SampleTable
            rows={previewData.rows}
            t={t}
            hoverSampleId={hoverSampleId}
            onHover={setHoverSampleId}
            onLocate={locateStation}
          />
        </div>
      )}

      {/* ── 任务状态 ── */}
      {statusData && (
        <div className="rounded border border-edge-subtle p-2 text-xs space-y-1">
          <span className="font-semibold">{t('jobState')}: </span>
          {statusData.job ? (
            <span>#{statusData.job.job_id} = {statusData.job.status}</span>
          ) : (
            <span className="text-ink-muted">{t('noActiveJob')}</span>
          )}
          {statusData.recovery_required && (
            <p className="text-status-critical">{t('recoveryRequired')}</p>
          )}
          {statusData.latest_receipt && (
            <div className="flex flex-wrap gap-3 text-ink-muted">
              <span>{t('receipt')}: {statusData.latest_receipt.execution_id}</span>
              <span>{t('coverage')}: {statusData.latest_receipt.coverage.valid_cells}/
                {statusData.latest_receipt.coverage.requested_cells}</span>
              {statusData.outputs_alive && (
                <span>{t('outputsAlive')}: {statusData.outputs_alive.sample_table ? '✓' : '✗'}</span>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── 成果 ── */}
      {results && (
        <div className="space-y-2 rounded border border-edge-subtle p-2">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-semibold">{t('results')}</span>
            <span>{results.pagination.filtered_rows} / {results.pagination.total_rows}</span>
            <button className={btn} onClick={() => void loadResultsPage(resultsPage - 1)}
              disabled={resultsPage <= 1 || loadingResults}>{t('prevPage')}</button>
            <span>{t('page')} {resultsPage}</span>
            <button className={btn} onClick={() => void loadResultsPage(resultsPage + 1)}
              disabled={resultsPage * 50 >= results.pagination.filtered_rows || loadingResults}>
              {t('nextPage')}
            </button>
            {results.station_layer_ref && (
              <button className={btn} onClick={() => void handleLoadStationLayer()}>
                {t('loadStationLayer')}
              </button>
            )}
            {results.receipt.outputs.sample_table_ref && sessionId && (
              <button className={btn} onClick={() => setDeliverySource({
                kind: 'fabric_parquet',
                session_id: sessionId,
                ref: results.receipt.outputs.sample_table_ref || '',
              })}>
                {t('downloadTable')}
              </button>
            )}
          </div>
          {results.receipt.degraded?.some((d) => d.includes('STATION_LAYER_SKIPPED')) && (
            <p className="text-xs text-status-warning">{t('layerSkipped')}</p>
          )}
          {results.receipt.outputs.sample_table_ref && (
            <p className="truncate font-mono text-[10px] text-ink-muted"
              data-testid="sample-table-ref">
              {results.receipt.outputs.sample_table_ref}
            </p>
          )}
          {results.curves && results.curves.bands.length > 0 && (
            <CurvesBlock
              curves={results.curves}
              thinning={results.curves.thinning}
              t={t}
              unitOfBand={unitOfBand}
              hoverSampleId={hoverSampleId}
              onHover={setHoverSampleId}
            />
          )}
          <SampleTable
            rows={results.rows}
            t={t}
            hoverSampleId={hoverSampleId}
            onHover={setHoverSampleId}
            onLocate={locateStation}
          />
        </div>
      )}

      <DataDeliveryDialog
        open={deliverySource !== null}
        onClose={() => setDeliverySource(null)}
        source={deliverySource}
      />
    </section>
  );
}

function SampleTable({ rows, t, hoverSampleId, onHover, onLocate }: {
  rows: SampleRow[];
  t: (k: string) => string;
  hoverSampleId: string | null;
  onHover: (id: string | null) => void;
  onLocate?: (row: SampleRow) => void;
}) {
  return (
    <div className="max-h-64 overflow-auto rounded border border-edge-subtle/60">
      <table className="w-full text-left text-[11px]">
        <thead className="sticky top-0 bg-surface-sunken">
          <tr>
            <th className="px-1 py-0.5">{t('colStation')}</th>
            <th className="px-1 py-0.5">{t('colParent')}</th>
            <th className="px-1 py-0.5">{t('colPart')}</th>
            <th className="px-1 py-0.5">{t('colChainage')}</th>
            <th className="px-1 py-0.5">{t('colSource')}</th>
            <th className="px-1 py-0.5">{t('colBand')}</th>
            <th className="px-1 py-0.5">{t('colValue')}</th>
            <th className="px-1 py-0.5">{t('colUnit')}</th>
            <th className="px-1 py-0.5">{t('colStatus')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.sample_id}-${r.source_ref}-${r.band}-${i}`}
              className={
                hoverSampleId === r.sample_id
                  ? 'bg-accent/10 cursor-pointer'
                  : 'cursor-pointer'
              }
              onMouseEnter={() => onHover(r.sample_id)}
              onMouseLeave={() => onHover(null)}
              onClick={() => onLocate?.(r)}>
              <td className="px-1 py-0.5 font-mono" title={r.sample_id}>
                {r.sample_id.slice(-8)}
              </td>
              <td className="px-1 py-0.5 max-w-24 truncate" title={r.parent_locator}>
                {r.parent_locator}
              </td>
              <td className="px-1 py-0.5">{r.part_id || '—'}</td>
              <td className="px-1 py-0.5">
                {r.chainage_m != null ? `${r.chainage_m.toFixed(0)}m` : '—'}
              </td>
              <td className="px-1 py-0.5 max-w-24 truncate" title={r.source_ref}>
                {r.source_ref.slice(-10)}
              </td>
              <td className="px-1 py-0.5">{r.band}</td>
              <td className="px-1 py-0.5 font-mono">
                {r.valid
                  ? String(r.raw_exact ?? r.physical_value ?? r.interpolated_value ?? r.raw_value ?? '')
                  : '—'}
              </td>
              <td className="px-1 py-0.5">{r.unit || t('unknownUnit')}</td>
              <td className="px-1 py-0.5">
                {r.valid ? '✓' : (
                  <span title={t('invalidReasonOf')} className="text-status-critical">
                    {invalidLabel(t, r.invalid_reason)}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 多波段剖面曲线：缺口断线；单位进轴标签；hover 同步 sample id。 */
function CurvesBlock({ curves, thinning, t, unitOfBand, hoverSampleId, onHover }: {
  curves: { bands: Array<{ source_ref: string; band: number; points: CurvePoint[] }> };
  thinning: Record<string, Record<string, unknown>>;
  t: (k: string) => string;
  unitOfBand: (sourceRef: string, band: number) => string;
  hoverSampleId: string | null;
  onHover: (id: string | null) => void;
}) {
  return (
    <div className="space-y-2" data-testid="raster-sampling-curves">
      {curves.bands.map((b) => {
        const pts = b.points.filter((p) => p.x != null);
        if (pts.length < 2) return null;
        const xs = pts.map((p) => p.x as number);
        const ys = pts.map((p) => p.y).filter((v): v is number => v != null);
        const xMin = Math.min(...xs);
        const xMax = Math.max(...xs);
        const yMin = Math.min(...ys);
        const yMax = Math.max(...ys);
        const W = 460;
        const H = 120;
        const pad = 6;
        const sx = (x: number) => pad + ((x - xMin) / Math.max(1e-9, xMax - xMin)) * (W - 2 * pad);
        const sy = (y: number) => H - pad - ((y - yMin) / Math.max(1e-9, yMax - yMin)) * (H - 2 * pad);
        // 缺口断线路径：gap_before 或 y=null 都开新子路径
        let path = '';
        let open = false;
        for (const p of pts) {
          if (p.y == null || p.gap) {
            open = false;
            continue;
          }
          const cmd = open ? 'L' : 'M';
          path += `${cmd}${sx(p.x as number).toFixed(1)},${sy(p.y).toFixed(1)} `;
          open = true;
        }
        const thin = thinning[`${b.source_ref}#${b.band}`] || {};
        return (
          <figure key={`${b.source_ref}-${b.band}`} className="rounded border border-edge-subtle/60 p-1">
            <figcaption className="flex justify-between text-[11px] text-ink-muted">
              <span>{b.source_ref.slice(-10)} · band {b.band} · {unitOfBand(b.source_ref, b.band)}</span>
              <span title={t('curveThinning')}>
                {t('curveThinning')}: {String(thin.method ?? 'none')} ({String(thin.kept ?? '?')}/{String(thin.total ?? '?')})
              </span>
            </figcaption>
            <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
              aria-label={`profile ${b.source_ref} band ${b.band}`}>
              <path d={path} fill="none" stroke="rgb(14 165 233)" strokeWidth="1.4" />
              {pts.map((p, i) => p.y == null ? null : (
                <circle
                  key={`${p.sid}-${i}`}
                  cx={sx(p.x as number)}
                  cy={sy(p.y)}
                  r={hoverSampleId === p.sid ? 3.5 : 1.2}
                  fill={hoverSampleId === p.sid ? 'rgb(249 115 22)' : 'rgb(14 165 233)'}
                  onMouseEnter={() => onHover(p.sid)}
                  onMouseLeave={() => onHover(null)}>
                  <title>{`${p.sid} · ${p.x} → ${p.y}`}</title>
                </circle>
              ))}
            </svg>
          </figure>
        );
      })}
    </div>
  );
}

export default RasterSamplingPanel;
