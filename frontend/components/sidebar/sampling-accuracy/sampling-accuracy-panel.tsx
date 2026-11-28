'use client';
/**
 * 概率抽样调查与精度核查工作台（GOAL-20261127-03 / WP5）。
 *
 * 五步向导（step 状态机）：建框 → 设计预览 → 创建调查 → 核查标注 → 精度估计。
 *
 * 链路与纪律（对齐 raster-sampling-panel 范式）：
 * - 建框：source_ref + 类别字典（值/名行编辑，≥2 行）→ POST /survey/frame；
 *   层表（层名/单元数/面积/占比）+ frame_digest + excluded 披露；
 * - 设计预览：equal/proportional/neyman + n_total 或 target_se_oa 二选一 →
 *   分配表（层权 W / 建议 n）+ warnings + rationale；
 * - 创建调查：design_digest 不可变快照说明；
 * - 核查：样点上图（legend_spec categorical 按核查状态着色）+ 待核查列表
 *   （筛选 + 行点击 setViewport 定位）+ 逐点标注表单 → POST records（CAS
 *   expected_revision）；盲标开关默认开（blind 下参考类下拉仍可用——
 *   strata 取自 design.allocation keys）；样点级核查状态为会话内本地追踪
 *   （后端 records 无逐点读回端点），权威进度以 /progress 聚合为准并如实披露；
 * - 估计：POST estimate → 状态徽章 + OA 卡 + UA/PA/面积表 + 误差矩阵热力表
 *   （比例/计数切换）；422/409 typed 错误信封如实呈现 code + correction_hint；
 *   历史 estimates 可切换查看。
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useT } from '@/lib/i18n/useT';
import { useToastStore } from '@/components/ui/toast';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  buildFrame,
  createSurvey,
  fetchProgress,
  fetchSurvey,
  listEstimates,
  listFrames,
  normalizeFrameStrata,
  postRecord,
  previewDesign,
  runEstimate,
  surveyErrorOf,
  type DesignPreview,
  type DesignStrategy,
  type EstimatePayload,
  type EvidenceQuality,
  type EvidenceType,
  type FramePayload,
  type FrameStratumRow,
  type MetricValue,
  type RecordParams,
  type RecordStatus,
  type SurveyCreateResponse,
  type SurveyDoc,
  type SurveyProgress,
} from '@/lib/api/sampling-accuracy';

interface PanelProps {
  sessionId?: string | null;
  ownerToken?: string | null | undefined;
}

type StepId = 1 | 2 | 3 | 4 | 5;

type SizeMode = 'n' | 'se';

type SampleFilter = 'all' | 'unlabelled' | 'labelled';

type MatrixMode = 'matrix' | 'counts';

interface ClassRow {
  key: string;
  value: string;
  name: string;
}

/** 核查状态 → i18n key（statusXxx）/地图着色（legend categorical）。 */
const STATUS_KEYS: Record<string, string> = {
  labelled: 'statusLabelled',
  pending: 'statusPending',
  undetermined: 'statusUndetermined',
  refused: 'statusRefused',
  not_visited: 'statusNotVisited',
  disputed: 'statusDisputed',
};

const STATUS_COLORS: Record<string, string> = {
  labelled: '#16a34a', // 绿：已标注
  pending: '#9ca3af', // 灰：待核查
  undetermined: '#f97316', // 橙：不可判
  refused: '#dc2626', // 红：拒绝
  not_visited: '#2563eb', // 蓝：未访问
  disputed: '#7c3aed',
};

const STATUS_ORDER: RecordStatus[] = [
  'labelled', 'pending', 'undetermined', 'refused', 'not_visited', 'disputed',
];

const CHECK_FIELD = 'check_status';
const SAMPLE_LAYER_PREFIX = 'sampling-accuracy:';

function statusKey(status: string): string {
  return STATUS_KEYS[status] ?? 'statusNotVisited';
}

function statusColor(status: string): string {
  return STATUS_COLORS[status] ?? '#2563eb';
}

function fmtNum(v: number | null | undefined, digits = 4): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return String(Number(v.toFixed(digits)));
}

function fmtInt(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toLocaleString('en-US');
}

function fmtArea(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  if (v >= 1e6) return `${fmtNum(v / 1e6, 3)} km2`;
  return `${fmtInt(Math.round(v))} m2`;
}

function estimateStatusLabel(status: string): string {
  if (status === 'supported') return 'statusSupported';
  if (status === 'partial') return 'statusPartial';
  return 'statusNotEvaluated';
}

export function SamplingAccuracyPanel({ sessionId, ownerToken }: PanelProps) {
  const t = useT('samplingAccuracy');
  const addToast = useToastStore((s) => s.addToast);
  const addLayer = useHudStore((s) => s.addLayer);
  const removeLayer = useHudStore((s) => s.removeLayer);
  const setViewport = useHudStore((s) => s.setViewport);

  const [step, setStep] = useState<StepId>(1);
  /** 会话代次：切换 sessionId 后在飞响应到达即丢弃（对齐 R2 #2190 纪律）。 */
  const sessionEpochRef = useRef(0);

  // ── Step 1：建框 ──
  const [sourceRef, setSourceRef] = useState('');
  const [band, setBand] = useState('1');
  const [classRows, setClassRows] = useState<ClassRow[]>([
    { key: 'c0', value: '1', name: '' },
    { key: 'c1', value: '2', name: '' },
  ]);
  const [frame, setFrame] = useState<FramePayload | null>(null);
  const [frames, setFrames] = useState<FramePayload[]>([]);
  const [building, setBuilding] = useState(false);

  // ── Step 2：设计 ──
  const [strategy, setStrategy] = useState<DesignStrategy>('equal');
  const [sizeMode, setSizeMode] = useState<SizeMode>('n');
  const [nTotal, setNTotal] = useState('100');
  const [targetSe, setTargetSe] = useState('0.05');
  const [seed, setSeed] = useState('42');
  const [backupPerStratum, setBackupPerStratum] = useState('2');
  const [maxNonresponse, setMaxNonresponse] = useState('0.2');
  const [blindLabelling, setBlindLabelling] = useState(true);
  const [objective, setObjective] = useState('');
  const [targetTime, setTargetTime] = useState('');
  const [preview, setPreview] = useState<DesignPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);

  // ── Step 3：创建 ──
  const [created, setCreated] = useState<SurveyCreateResponse | null>(null);
  const [creating, setCreating] = useState(false);

  // ── Step 4：核查 ──
  const [survey, setSurvey] = useState<SurveyDoc | null>(null);
  const [blindView, setBlindView] = useState(true);
  const [loadingSurvey, setLoadingSurvey] = useState(false);
  const [filter, setFilter] = useState<SampleFilter>('all');
  /** 样点级核查状态（会话内本地追踪；服务端权威进度用 /progress）。 */
  const [localStatus, setLocalStatus] = useState<Record<string, RecordStatus>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [recordStatus, setRecordStatus] = useState<RecordStatus>('labelled');
  const [referenceClass, setReferenceClass] = useState('');
  const [evidenceType, setEvidenceType] = useState<EvidenceType>('field_visit');
  const [evidenceQuality, setEvidenceQuality] = useState<EvidenceQuality>('medium');
  const [evidenceNote, setEvidenceNote] = useState('');
  const [noteText, setNoteText] = useState('');
  const [recording, setRecording] = useState(false);
  const [recordError, setRecordError] = useState<{ code: string; detail: string } | null>(null);
  const [progress, setProgress] = useState<SurveyProgress | null>(null);

  // ── Step 5：估计 ──
  const [estimate, setEstimate] = useState<EstimatePayload | null>(null);
  const [estimates, setEstimates] = useState<EstimatePayload[]>([]);
  const [estimating, setEstimating] = useState(false);
  const [estimateError, setEstimateError] = useState<{ code: string; detail: string; hint?: string } | null>(null);
  const [matrixMode, setMatrixMode] = useState<MatrixMode>('matrix');

  // 切换会话：清全部向导状态（旧会话响应不泄入）。
  useEffect(() => {
    sessionEpochRef.current += 1;
    setStep(1);
    setFrame(null);
    setFrames([]);
    setPreview(null);
    setCreated(null);
    setSurvey(null);
    setLocalStatus({});
    setSelectedId(null);
    setProgress(null);
    setEstimate(null);
    setEstimates([]);
    setEstimateError(null);
    setRecordError(null);
  }, [sessionId]);

  const opts = useMemo(() => ({ ownerToken }), [ownerToken]);

  const fail = useCallback((err: unknown) => {
    addToast(`${t('errorPrefix')}: ${surveyErrorOf(err).detail.slice(0, 160)}`, 'error');
  }, [addToast, t]);

  // ── Step 1 动作 ──
  const buildClassDictionary = (): Record<string, string> | null => {
    const dict: Record<string, string> = {};
    for (const row of classRows) {
      const v = row.value.trim();
      const name = row.name.trim();
      if (!v && !name) continue; // 全空行跳过
      if (!/^-?\d+$/.test(v) || !name) return null;
      dict[v] = name;
    }
    return Object.keys(dict).length >= 2 ? dict : null;
  };

  const handleBuildFrame = async () => {
    if (!sessionId) {
      addToast(t('sessionMissing'), 'error');
      return;
    }
    const dict = buildClassDictionary();
    if (!dict) {
      addToast(t('needTwoClasses'), 'error');
      return;
    }
    const parsedBand = Number.parseInt(band, 10);
    if (!Number.isFinite(parsedBand) || parsedBand < 1) {
      addToast(`${t('band')}: 1..64`, 'error');
      return;
    }
    const epoch = sessionEpochRef.current;
    setBuilding(true);
    try {
      const res = await buildFrame(sessionId, {
        sourceRef: sourceRef.trim(),
        band: parsedBand,
        classDictionary: dict,
      }, opts);
      if (epoch !== sessionEpochRef.current) return;
      setFrame(res.payload);
      addToast(`${t('frameReady')} · ${res.payload.frame_digest.slice(0, 12)}`, 'success');
      setStep(2);
    } catch (err) {
      if (epoch !== sessionEpochRef.current) return;
      fail(err);
    } finally {
      if (epoch === sessionEpochRef.current) setBuilding(false);
    }
  };

  const handleLoadFrames = async () => {
    if (!sessionId) return;
    const epoch = sessionEpochRef.current;
    try {
      const res = await listFrames(sessionId, opts);
      if (epoch !== sessionEpochRef.current) return;
      setFrames(res.frames || []);
    } catch (err) {
      if (epoch !== sessionEpochRef.current) return;
      fail(err);
    }
  };

  const adoptFrame = (f: FramePayload) => {
    setFrame(f);
    if (f.source_ref) setSourceRef(f.source_ref);
    setPreview(null);
    setStep(2);
  };

  // ── Step 2 动作 ──
  const handlePreview = async () => {
    if (!sessionId || !frame) return;
    const nParsed = Number.parseInt(nTotal, 10);
    const seParsed = Number.parseFloat(targetSe);
    const params: {
      frameDigest: string;
      strategy: DesignStrategy;
      nTotal?: number | null;
      targetSeOa?: number | null;
    } = { frameDigest: frame.frame_digest, strategy };
    if (sizeMode === 'n') {
      if (!Number.isFinite(nParsed) || nParsed < 2) {
        addToast(`${t('nTotal')} >= 2`, 'error');
        return;
      }
      params.nTotal = nParsed;
    } else {
      if (!Number.isFinite(seParsed) || seParsed <= 0 || seParsed >= 1) {
        addToast(`${t('targetSeOa')} (0,1)`, 'error');
        return;
      }
      params.targetSeOa = seParsed;
    }
    const epoch = sessionEpochRef.current;
    setPreviewing(true);
    try {
      const res = await previewDesign(sessionId, params, opts);
      if (epoch !== sessionEpochRef.current) return;
      setPreview(res.preview);
      addToast(t('previewReady'), 'success');
      setStep(3);
    } catch (err) {
      if (epoch !== sessionEpochRef.current) return;
      fail(err);
    } finally {
      if (epoch === sessionEpochRef.current) setPreviewing(false);
    }
  };

  // ── Step 3 动作 ──
  const loadSurveyDetail = useCallback(async (surveyId: string, blind: boolean) => {
    if (!sessionId) return null;
    const epoch = sessionEpochRef.current;
    setLoadingSurvey(true);
    try {
      const res = await fetchSurvey(sessionId, surveyId, { blind }, opts);
      if (epoch !== sessionEpochRef.current) return null;
      setSurvey(res.survey);
      return res.survey;
    } catch (err) {
      if (epoch !== sessionEpochRef.current) return null;
      fail(err);
      return null;
    } finally {
      if (epoch === sessionEpochRef.current) setLoadingSurvey(false);
    }
  }, [sessionId, opts, fail]);

  const loadProgress = useCallback(async (surveyId: string) => {
    if (!sessionId) return null;
    const epoch = sessionEpochRef.current;
    try {
      const res = await fetchProgress(sessionId, surveyId, opts);
      if (epoch !== sessionEpochRef.current) return null;
      setProgress(res.progress);
      return res.progress;
    } catch (err) {
      if (epoch !== sessionEpochRef.current) return null;
      fail(err);
      return null;
    }
  }, [sessionId, opts, fail]);

  const loadHistory = useCallback(async (surveyId: string) => {
    if (!sessionId) return;
    const epoch = sessionEpochRef.current;
    try {
      const res = await listEstimates(sessionId, surveyId, opts);
      if (epoch !== sessionEpochRef.current) return;
      setEstimates(res.estimates || []);
    } catch (err) {
      if (epoch !== sessionEpochRef.current) return;
      fail(err);
    }
  }, [sessionId, opts, fail]);

  const handleCreate = async () => {
    if (!sessionId || !frame || !preview) return;
    if (!objective.trim()) {
      addToast(t('objective'), 'error');
      return;
    }
    const nParsed = Number.parseInt(nTotal, 10);
    const seParsed = Number.parseFloat(targetSe);
    const seedParsed = Number.parseInt(seed, 10);
    const backupParsed = Number.parseInt(backupPerStratum, 10);
    const nonresponseParsed = Number.parseFloat(maxNonresponse);
    const epoch = sessionEpochRef.current;
    setCreating(true);
    try {
      const res = await createSurvey(sessionId, {
        frameDigest: frame.frame_digest,
        objective: objective.trim(),
        targetTime: targetTime.trim(),
        strategy,
        nTotal: sizeMode === 'n' && Number.isFinite(nParsed) ? nParsed : null,
        targetSeOa: sizeMode === 'se' && Number.isFinite(seParsed) ? seParsed : null,
        seed: Number.isFinite(seedParsed) && seedParsed >= 0 ? seedParsed : 42,
        backupPerStratum: Number.isFinite(backupParsed) && backupParsed >= 0 ? backupParsed : 0,
        blindLabelling,
        maxNonresponseRate:
          Number.isFinite(nonresponseParsed) && nonresponseParsed > 0 && nonresponseParsed < 1
            ? nonresponseParsed
            : 0.2,
      }, opts);
      if (epoch !== sessionEpochRef.current) return;
      setCreated(res);
      setLocalStatus({});
      setSelectedId(null);
      setEstimate(null);
      setEstimates([]);
      addToast(`${t('createdToast')} · ${res.survey_id}`, 'success');
      setStep(4);
      void loadSurveyDetail(res.survey_id, blindLabelling);
      void loadProgress(res.survey_id);
    } catch (err) {
      if (epoch !== sessionEpochRef.current) return;
      fail(err);
    } finally {
      if (epoch === sessionEpochRef.current) setCreating(false);
    }
  };

  // ── Step 4 动作 ──
  const handleToggleBlind = (blind: boolean) => {
    setBlindView(blind);
    if (created) void loadSurveyDetail(created.survey_id, blind);
  };

  const effectiveStatus = useCallback((sampleId: string): RecordStatus =>
    localStatus[sampleId] ?? 'not_visited', [localStatus]);

  const visibleSamples = useMemo(() => {
    const samples = survey?.samples ?? [];
    if (filter === 'all') return samples;
    if (filter === 'labelled') return samples.filter((s) => effectiveStatus(s.sample_id) === 'labelled');
    return samples.filter((s) => effectiveStatus(s.sample_id) !== 'labelled');
  }, [survey, filter, effectiveStatus]);

  const handleShowOnMap = () => {
    const samples = survey?.samples ?? [];
    if (!samples.length) {
      addToast(t('mapLayerEmpty'), 'warning');
      return;
    }
    const layerId = `${SAMPLE_LAYER_PREFIX}${created?.survey_id ?? survey?.survey_id ?? 'x'}`;
    removeLayer(layerId);
    const categories = STATUS_ORDER.map((s) => ({
      key: s,
      color: statusColor(s),
      label: t(statusKey(s)),
    }));
    addLayer({
      id: layerId,
      name: `${t('title')}:${(created?.survey_id ?? survey?.survey_id ?? '').slice(0, 12)}`,
      type: 'vector',
      visible: true,
      opacity: 1,
      group: 'reference',
      source: {
        type: 'FeatureCollection',
        features: samples.map((s) => ({
          type: 'Feature' as const,
          id: s.sample_id,
          geometry: { type: 'Point' as const, coordinates: [s.lon, s.lat] },
          properties: { [CHECK_FIELD]: effectiveStatus(s.sample_id), sample_id: s.sample_id },
        })),
      },
      style: { color: STATUS_COLORS.not_visited },
      legend_spec: {
        type: 'categorical',
        field: CHECK_FIELD,
        categories,
        default: { color: STATUS_COLORS.not_visited, label: t('statusNotVisited') },
      },
    });
    addToast(t('mapLayerAdded'), 'success');
  };

  const locateSample = (sampleId: string, lon: number, lat: number) => {
    setSelectedId(sampleId);
    setViewport([lon, lat], 13, 0, 0);
  };

  const handleRecord = async () => {
    if (!sessionId || !created || !selectedId) {
      addToast(t('noSampleSelected'), 'error');
      return;
    }
    const params: RecordParams = {
      sampleId: selectedId,
      status: recordStatus,
      referenceClass: recordStatus === 'labelled' ? referenceClass : null,
      evidenceType,
      evidenceNote: evidenceNote.trim(),
      quality: evidenceQuality,
      note: noteText.trim(),
      expectedRevision: survey?.revision ?? null,
    };
    const epoch = sessionEpochRef.current;
    setRecording(true);
    setRecordError(null);
    try {
      const res = await postRecord(sessionId, created.survey_id, params, opts);
      if (epoch !== sessionEpochRef.current) return;
      setLocalStatus((prev) => ({ ...prev, [selectedId]: recordStatus }));
      setSurvey((prev) => (prev ? { ...prev, revision: res.result.revision } : prev));
      addToast(`${t('recordOk')} · v${res.result.version}`, 'success');
      void loadProgress(created.survey_id);
    } catch (err) {
      if (epoch !== sessionEpochRef.current) return;
      const e = surveyErrorOf(err);
      setRecordError({ code: e.code, detail: e.detail });
      addToast(`${t('errorPrefix')}: ${e.code} ${e.detail.slice(0, 120)}`, 'error');
    } finally {
      if (epoch === sessionEpochRef.current) setRecording(false);
    }
  };

  // ── Step 5 动作 ──
  const handleEstimate = async () => {
    if (!sessionId || !created) return;
    const epoch = sessionEpochRef.current;
    setEstimating(true);
    setEstimateError(null);
    try {
      const res = await runEstimate(sessionId, created.survey_id, opts);
      if (epoch !== sessionEpochRef.current) return;
      setEstimate(res.estimate);
      void loadHistory(created.survey_id);
    } catch (err) {
      if (epoch !== sessionEpochRef.current) return;
      const e = surveyErrorOf(err);
      setEstimateError({ code: e.code, detail: e.detail, hint: e.correction_hint });
    } finally {
      if (epoch === sessionEpochRef.current) setEstimating(false);
    }
  };

  const classOptions = useMemo(() => {
    const doc = survey ?? created;
    if (doc && 'allocation' in doc && doc.allocation) return Object.keys(doc.allocation);
    if (frame) return normalizeFrameStrata(frame).map((s) => s.stratum_id);
    return [];
  }, [survey, created, frame]);

  const stepReached = (s: StepId): boolean => {
    if (s === 1) return true;
    if (s === 2) return frame != null;
    if (s === 3) return preview != null;
    return created != null; // 4/5
  };

  const label = 'text-xs font-medium text-ink-muted';
  const input = 'w-full rounded border border-edge-subtle bg-surface-raised px-2 py-1 text-xs';
  const btn = 'rounded border border-edge-subtle px-2 py-1 text-xs hover:bg-surface-hover disabled:opacity-50';
  const btnPrimary = 'rounded bg-accent text-white px-2 py-1 text-xs hover:opacity-90 disabled:opacity-50';

  const frameStrata: FrameStratumRow[] = frame ? normalizeFrameStrata(frame) : [];
  const frameTotalUnits = frame?.total_units || 0;

  return (
    <section aria-label={t('title')} className="space-y-3 p-2" data-testid="sampling-accuracy-panel">
      <header className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold">{t('title')}</h3>
        <button className={btn} onClick={() => void handleLoadFrames()} disabled={!sessionId}>
          {t('loadFrames')}
        </button>
      </header>
      <p className="text-xs text-ink-muted">{t('subtitle')}</p>

      {/* 步骤指示 */}
      <div className="flex flex-wrap gap-1" role="tablist" aria-label={t('title')}>
        {([1, 2, 3, 4, 5] as StepId[]).map((s) => (
          <button
            key={s}
            role="tab"
            aria-selected={step === s}
            disabled={!stepReached(s)}
            onClick={() => setStep(s)}
            className={step === s
              ? 'rounded bg-accent px-2 py-0.5 text-xs text-white'
              : 'rounded border border-edge-subtle px-2 py-0.5 text-xs hover:bg-surface-hover disabled:opacity-40'}
          >
            {t(s === 1 ? 'stepFrame' : s === 2 ? 'stepDesign' : s === 3 ? 'stepCreate'
              : s === 4 ? 'stepVerify' : 'stepEstimate')}
          </button>
        ))}
      </div>

      {frames.length > 0 && step === 1 && (
        <div className="space-y-1 rounded border border-edge-subtle p-2">
          <div className={label}>{t('framesTitle')}</div>
          {frames.map((f) => (
            <button key={f.frame_digest} className={`${btn} w-full text-left`}
              onClick={() => adoptFrame(f)}>
              <span className="font-mono">{f.frame_digest.slice(0, 16)}</span>
              {' · '}{fmtInt(f.total_units)} · {fmtArea(f.total_area)}
            </button>
          ))}
        </div>
      )}

      {/* ── Step 1：建框 ── */}
      {step === 1 && (
        <div className="space-y-2 rounded border border-edge-subtle p-2" data-testid="frame-step">
          <div className="grid grid-cols-[1fr_4rem] gap-1">
            <div>
              <span className={label}>{t('sourceRef')}</span>
              <input className={input} value={sourceRef} onChange={(e) => setSourceRef(e.target.value)}
                placeholder="ref:data-…" title={t('sourceRefHint')} />
            </div>
            <div>
              <span className={label}>{t('band')}</span>
              <input className={input} value={band} onChange={(e) => setBand(e.target.value)}
                inputMode="numeric" />
            </div>
          </div>
          <div>
            <div className="flex items-center justify-between">
              <span className={label}>{t('classDict')}</span>
              <button className={btn} onClick={() => setClassRows((p) => [
                ...p,
                { key: `c${Date.now() % 100000}`, value: '', name: '' },
              ])}>
                {t('addClassRow')}
              </button>
            </div>
            {classRows.map((row, i) => (
              <div key={row.key} className="mt-1 grid grid-cols-[4rem_1fr_auto] items-center gap-1">
                <input className={input} value={row.value} inputMode="numeric" aria-label={`${t('classValue')} ${i + 1}`}
                  onChange={(e) => setClassRows((p) => p.map((r) => (
                    r.key === row.key ? { ...r, value: e.target.value } : r)))} />
                <input className={input} value={row.name} aria-label={`${t('className')} ${i + 1}`}
                  onChange={(e) => setClassRows((p) => p.map((r) => (
                    r.key === row.key ? { ...r, name: e.target.value } : r)))} />
                <button className={btn} disabled={classRows.length <= 2}
                  onClick={() => setClassRows((p) => p.filter((r) => r.key !== row.key))}>
                  {t('removeClassRow')}
                </button>
              </div>
            ))}
          </div>
          <button className={btnPrimary} onClick={() => void handleBuildFrame()}
            disabled={building || !sessionId || !sourceRef.trim()}>
            {building ? t('building') : t('buildFrame')}
          </button>
        </div>
      )}

      {/* 层表 + digest + excluded（step 1/2 都可见） */}
      {frame && (
        <div className="space-y-1 rounded border border-edge-subtle p-2 text-xs" data-testid="frame-summary">
          <div className="flex flex-wrap gap-3">
            <span>{t('frameDigest')}: <code>{frame.frame_digest.slice(0, 24)}</code></span>
            <span>{t('totalUnits')}: {fmtInt(frame.total_units)}</span>
            <span>{t('totalArea')}: {fmtArea(frame.total_area)}</span>
            {frame.pixel_area_spread != null && (
              <span>{t('pixelAreaSpread')}: {fmtNum(frame.pixel_area_spread * 100, 2)}%</span>
            )}
          </div>
          <div className={label}>{t('strataTitle')}</div>
          <FrameStrataTable rows={frameStrata} totalUnits={frameTotalUnits} t={t} />
          {frame.excluded && (
            <div className="flex flex-wrap gap-3 text-ink-muted">
              <span>{t('excludedTitle')}: {t('excludedNodata')} {fmtInt(frame.excluded.nodata ?? 0)}
                · {t('excludedOutsideDict')} {fmtInt(frame.excluded.outside_dictionary ?? 0)}</span>
            </div>
          )}
        </div>
      )}

      {/* ── Step 2：设计 ── */}
      {step === 2 && frame && (
        <div className="space-y-2 rounded border border-edge-subtle p-2" data-testid="design-step">
          <div className="grid grid-cols-2 gap-1">
            <div>
              <span className={label}>{t('strategy')}</span>
              <select className={input} value={strategy}
                onChange={(e) => setStrategy(e.target.value as DesignStrategy)}>
                <option value="equal">{t('strategyEqual')}</option>
                <option value="proportional">{t('strategyProportional')}</option>
                <option value="neyman">{t('strategyNeyman')}</option>
              </select>
            </div>
            <div>
              <span className={label}>{t('sizeByN')} / {t('sizeBySe')}</span>
              <div className="flex items-center gap-2 text-xs">
                <label className="flex items-center gap-1">
                  <input type="radio" checked={sizeMode === 'n'}
                    onChange={() => setSizeMode('n')} />
                  {t('sizeByN')}
                </label>
                <label className="flex items-center gap-1">
                  <input type="radio" checked={sizeMode === 'se'}
                    onChange={() => setSizeMode('se')} />
                  {t('sizeBySe')}
                </label>
              </div>
            </div>
          </div>
          {sizeMode === 'n' ? (
            <div>
              <span className={label}>{t('nTotal')}</span>
              <input className={input} value={nTotal} onChange={(e) => setNTotal(e.target.value)}
                inputMode="numeric" />
            </div>
          ) : (
            <div>
              <span className={label}>{t('targetSeOa')}</span>
              <input className={input} value={targetSe} onChange={(e) => setTargetSe(e.target.value)}
                inputMode="decimal" />
            </div>
          )}
          <div className="grid grid-cols-2 gap-1">
            <div>
              <span className={label}>{t('seed')}</span>
              <input className={input} value={seed} onChange={(e) => setSeed(e.target.value)}
                inputMode="numeric" />
            </div>
            <div>
              <span className={label}>{t('backupPerStratum')}</span>
              <input className={input} value={backupPerStratum}
                onChange={(e) => setBackupPerStratum(e.target.value)} inputMode="numeric" />
            </div>
            <div>
              <span className={label}>{t('maxNonresponseRate')}</span>
              <input className={input} value={maxNonresponse}
                onChange={(e) => setMaxNonresponse(e.target.value)} inputMode="decimal" />
            </div>
            <label className="mt-4 flex items-center gap-1 text-xs">
              <input type="checkbox" checked={blindLabelling}
                onChange={(e) => setBlindLabelling(e.target.checked)} />
              {t('blindLabelling')}
            </label>
          </div>
          <button className={btnPrimary} onClick={() => void handlePreview()}
            disabled={previewing || !sessionId}>
            {previewing ? t('previewing') : t('runPreview')}
          </button>
        </div>
      )}

      {/* 预览（step 2/3 可见） */}
      {preview && (step === 2 || step === 3) && (
        <div className="space-y-1 rounded border border-edge-subtle p-2" data-testid="preview-block">
          <div className="text-xs font-semibold">
            {t('previewTitle')} · {t('previewNTotal')}: {fmtInt(preview.n_total)}
          </div>
          <PreviewAllocationTable allocation={preview.allocation} weights={preview.weights_W}
            nTotal={preview.n_total} t={t} />
          {(preview.warnings?.length ?? 0) > 0 && (
            <div className="text-xs text-status-warning">
              <div className={label}>{t('warningsTitle')}</div>
              <ul className="list-disc pl-4">
                {preview.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            </div>
          )}
          {preview.rationale && Object.keys(preview.rationale).length > 0 && (
            <div className="text-xs text-ink-muted">
              <div className={label}>{t('rationaleTitle')}</div>
              {Object.entries(preview.rationale).map(([k, v]) => (
                <div key={k} className="truncate font-mono text-[10px]" title={`${k}=${JSON.stringify(v)}`}>
                  {k}={JSON.stringify(v)}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Step 3：创建 ── */}
      {step === 3 && preview && (
        <div className="space-y-2 rounded border border-edge-subtle p-2" data-testid="create-step">
          <div>
            <span className={label}>{t('objective')}</span>
            <input className={input} value={objective} onChange={(e) => setObjective(e.target.value)}
              placeholder={t('objectivePlaceholder')} maxLength={200} />
          </div>
          <div>
            <span className={label}>{t('targetTime')}</span>
            <input className={input} value={targetTime} onChange={(e) => setTargetTime(e.target.value)}
              maxLength={32} />
          </div>
          <button className={btnPrimary} onClick={() => void handleCreate()}
            disabled={creating || !sessionId || !objective.trim()}>
            {creating ? t('creating') : t('createSurvey')}
          </button>
        </div>
      )}

      {/* 创建回执（step 3/4/5 可见） */}
      {created && step >= 3 && (
        <div className="space-y-1 rounded border border-edge-subtle p-2 text-xs" data-testid="created-summary">
          <div className="text-xs font-semibold">{t('createdTitle')}</div>
          <div className="flex flex-wrap gap-3">
            <span>{t('surveyIdLabel')}: <code>{created.survey_id}</code></span>
            <span>{t('designDigestLabel')}: <code>{created.design_digest.slice(0, 24)}</code></span>
            <span>{t('mainCount')}: {fmtInt(created.main_count)}</span>
            <span>{t('sampleCount')}: {fmtInt(created.sample_count)}</span>
            <span>{t('backupCount')}: {fmtInt(created.backup_per_stratum)}</span>
          </div>
          <p className="text-ink-muted">{t('designImmutable')}</p>
        </div>
      )}

      {/* ── Step 4：核查 ── */}
      {step === 4 && created && (
        <div className="space-y-2 rounded border border-edge-subtle p-2" data-testid="verify-step">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label className="flex items-center gap-1">
              <input type="radio" checked={blindView} onChange={() => handleToggleBlind(true)} />
              {t('blindOn')}
            </label>
            <label className="flex items-center gap-1">
              <input type="radio" checked={!blindView} onChange={() => handleToggleBlind(false)} />
              {t('blindOff')}
            </label>
            <button className={btn} onClick={() => void loadSurveyDetail(created.survey_id, blindView)}
              disabled={loadingSurvey}>
              {t('reloadSurvey')}
            </button>
            <button className={btn} onClick={handleShowOnMap}>{t('showOnMap')}</button>
            <span className="text-ink-muted" data-testid="survey-revision">
              {t('revisionLabel')}: {survey?.revision ?? '—'}
            </span>
          </div>
          <p className="text-xs text-ink-muted">{t('blindHint')}</p>

          <div className="flex flex-wrap items-center gap-1 text-xs">
            {(['all', 'unlabelled', 'labelled'] as SampleFilter[]).map((f) => (
              <button key={f} className={filter === f ? btnPrimary : btn}
                onClick={() => setFilter(f)}>
                {t(f === 'all' ? 'filterAll' : f === 'unlabelled' ? 'filterUnlabelled' : 'filterLabelled')}
              </button>
            ))}
          </div>

          <SampleListTable samples={visibleSamples} selectedId={selectedId}
            statusOf={effectiveStatus} t={t}
            onLocate={locateSample} />

          <div className="grid grid-cols-2 gap-1 rounded border border-edge-subtle/60 p-1.5"
            data-testid="record-form">
            <div className="col-span-2 text-xs font-semibold">{t('labellingTitle')}</div>
            <div>
              <span className={label}>{t('statusLabel')}</span>
              <select className={input} value={recordStatus}
                onChange={(e) => setRecordStatus(e.target.value as RecordStatus)}>
                {STATUS_ORDER.map((s) => (
                  <option key={s} value={s}>{t(statusKey(s))}</option>
                ))}
              </select>
            </div>
            <div>
              <span className={label}>{t('referenceClass')}</span>
              <select className={input} value={referenceClass}
                onChange={(e) => setReferenceClass(e.target.value)}
                disabled={recordStatus !== 'labelled'}>
                <option value="">—</option>
                {classOptions.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <span className={label}>{t('evidenceType')}</span>
              <select className={input} value={evidenceType}
                onChange={(e) => setEvidenceType(e.target.value as EvidenceType)}>
                <option value="field_visit">{t('evidenceFieldVisit')}</option>
                <option value="high_res_imagery">{t('evidenceHighResImagery')}</option>
                <option value="existing_survey">{t('evidenceExistingSurvey')}</option>
                <option value="other">{t('evidenceOther')}</option>
              </select>
            </div>
            <div>
              <span className={label}>{t('qualityLabel')}</span>
              <select className={input} value={evidenceQuality}
                onChange={(e) => setEvidenceQuality(e.target.value as EvidenceQuality)}>
                <option value="high">{t('qualityHigh')}</option>
                <option value="medium">{t('qualityMedium')}</option>
                <option value="low">{t('qualityLow')}</option>
              </select>
            </div>
            <div>
              <span className={label}>{t('evidenceNote')}</span>
              <input className={input} value={evidenceNote} maxLength={160}
                onChange={(e) => setEvidenceNote(e.target.value)} />
            </div>
            <div>
              <span className={label}>{t('noteLabel')}</span>
              <input className={input} value={noteText} maxLength={280}
                onChange={(e) => setNoteText(e.target.value)} />
            </div>
            <div className="col-span-2">
              <button className={btnPrimary} onClick={() => void handleRecord()}
                disabled={recording || !selectedId || !sessionId}>
                {recording ? t('recording') : t('submitRecord')}
              </button>
              {recordError && (
                <p className="mt-1 text-status-critical" data-testid="record-error">
                  {recordError.code}: {recordError.detail}
                </p>
              )}
            </div>
          </div>

          {progress && (
            <ProgressTable progress={progress} t={t} onRefresh={() => void loadProgress(created.survey_id)} />
          )}
          <p className="text-[10px] text-ink-muted">{t('localTrackingNote')}</p>
        </div>
      )}

      {/* ── Step 5：估计 ── */}
      {step === 5 && created && (
        <div className="space-y-2 rounded border border-edge-subtle p-2" data-testid="estimate-step">
          <div className="flex flex-wrap items-center gap-2">
            <button className={btnPrimary} onClick={() => void handleEstimate()} disabled={estimating}>
              {estimating ? t('estimating') : t('runEstimate')}
            </button>
            {progress && !progress.can_estimate && (
              <span className="text-xs text-status-warning">{t('estimateNotReady')}</span>
            )}
          </div>
          {estimateError && (
            <div className="rounded border border-edge-subtle p-1.5 text-xs" data-testid="estimate-error">
              <p className="text-status-critical">{t('estimateFailed')}: {estimateError.code}</p>
              <p>{estimateError.detail}</p>
              {estimateError.hint && (
                <p className="text-ink-muted">{t('correctionHint')}: {estimateError.hint}</p>
              )}
            </div>
          )}
          {estimate && (
            <EstimateView estimate={estimate} matrixMode={matrixMode}
              onMatrixMode={setMatrixMode} t={t} />
          )}
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <span className={label}>{t('historyTitle')}</span>
              <button className={btn} onClick={() => void loadHistory(created.survey_id)}>
                {t('loadHistory')}
              </button>
            </div>
            {estimates.length === 0 ? (
              <p className="text-xs text-ink-muted">{t('noEstimates')}</p>
            ) : (
              estimates.map((e, i) => (
                <div key={e.estimate_id ?? i} className="flex items-center justify-between text-xs">
                  <span className="font-mono" title={e.design_digest}>
                    {e.estimate_id?.slice(0, 12) ?? i}
                    {e.created_at != null ? ` · ${new Date(e.created_at * 1000).toISOString().slice(0, 16).replace('T', ' ')}` : ''}
                  </span>
                  <button className={btn} onClick={() => setEstimate(e)}>{t('useEstimate')}</button>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </section>
  );
}

/** 层表：层名 / 类值 / 单元数 / 面积 / 占比。 */
function FrameStrataTable({ rows, totalUnits, t }: {
  rows: FrameStratumRow[];
  totalUnits: number;
  t: (k: string) => string;
}) {
  return (
    <div className="max-h-48 overflow-auto rounded border border-edge-subtle/60"
      data-testid="frame-strata-table">
      <table className="w-full text-left text-[11px]">
        <thead className="sticky top-0 bg-surface-sunken">
          <tr>
            <th className="px-1 py-0.5">{t('colStratum')}</th>
            <th className="px-1 py-0.5">{t('colClassValue')}</th>
            <th className="px-1 py-0.5">{t('colUnits')}</th>
            <th className="px-1 py-0.5">{t('colArea')}</th>
            <th className="px-1 py-0.5">{t('colShare')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.stratum_id}>
              <td className="px-1 py-0.5">{r.stratum_id}</td>
              <td className="px-1 py-0.5 font-mono">{r.class_value ?? '—'}</td>
              <td className="px-1 py-0.5 font-mono">{fmtInt(r.N_units)}</td>
              <td className="px-1 py-0.5 font-mono">{fmtArea(r.area)}</td>
              <td className="px-1 py-0.5 font-mono">
                {totalUnits > 0 ? `${fmtNum((r.N_units / totalUnits) * 100, 2)}%` : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 分配表：每层权重 W / 建议 n / 占比。 */
function PreviewAllocationTable({ allocation, weights, nTotal, t }: {
  allocation: Record<string, number>;
  weights: Record<string, number>;
  nTotal: number;
  t: (k: string) => string;
}) {
  return (
    <div className="max-h-48 overflow-auto rounded border border-edge-subtle/60"
      data-testid="preview-allocation-table">
      <table className="w-full text-left text-[11px]">
        <thead className="sticky top-0 bg-surface-sunken">
          <tr>
            <th className="px-1 py-0.5">{t('colStratum')}</th>
            <th className="px-1 py-0.5">{t('colWeight')}</th>
            <th className="px-1 py-0.5">{t('colAlloc')}</th>
            <th className="px-1 py-0.5">{t('colShare')}</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(allocation).map(([sid, n]) => (
            <tr key={sid}>
              <td className="px-1 py-0.5">{sid}</td>
              <td className="px-1 py-0.5 font-mono">{fmtNum(weights[sid])}</td>
              <td className="px-1 py-0.5 font-mono">{fmtInt(n)}</td>
              <td className="px-1 py-0.5 font-mono">
                {nTotal > 0 ? `${fmtNum((n / nTotal) * 100, 1)}%` : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 待核查列表：筛选后的样点 + 本地核查状态 + 行点击定位。 */
function SampleListTable({ samples, selectedId, statusOf, t, onLocate }: {
  samples: Array<{ sample_id: string; lon: number; lat: number; is_backup: boolean }>;
  selectedId: string | null;
  statusOf: (id: string) => RecordStatus;
  t: (k: string) => string;
  onLocate: (id: string, lon: number, lat: number) => void;
}) {
  return (
    <div className="max-h-56 overflow-auto rounded border border-edge-subtle/60" data-testid="sample-list"
      title={t('locateHint')}>
      <table className="w-full text-left text-[11px]">
        <thead className="sticky top-0 bg-surface-sunken">
          <tr>
            <th className="px-1 py-0.5">{t('colSample')}</th>
            <th className="px-1 py-0.5">{t('colLon')}</th>
            <th className="px-1 py-0.5">{t('colLat')}</th>
            <th className="px-1 py-0.5">{t('colBackup')}</th>
            <th className="px-1 py-0.5">{t('colStatus')}</th>
          </tr>
        </thead>
        <tbody>
          {samples.map((s) => {
            const st = statusOf(s.sample_id);
            return (
              <tr key={s.sample_id}
                className={`cursor-pointer ${selectedId === s.sample_id ? 'bg-accent/10' : ''}`}
                onClick={() => onLocate(s.sample_id, s.lon, s.lat)}>
                <td className="px-1 py-0.5 font-mono" title={s.sample_id}>{s.sample_id.slice(-8)}</td>
                <td className="px-1 py-0.5 font-mono">{fmtNum(s.lon, 5)}</td>
                <td className="px-1 py-0.5 font-mono">{fmtNum(s.lat, 5)}</td>
                <td className="px-1 py-0.5">{s.is_backup ? '✓' : '—'}</td>
                <td className="px-1 py-0.5">
                  <span className="inline-flex items-center gap-1">
                    <span aria-hidden className="inline-block h-2 w-2 rounded-full"
                      style={{ background: statusColor(st) }} />
                    {t(statusKey(st))}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** 进度表：每层 planned/main/labelled/nonusable/unvisited/响应率 + 汇总。 */
function ProgressTable({ progress, t, onRefresh }: {
  progress: SurveyProgress;
  t: (k: string) => string;
  onRefresh: () => void;
}) {
  return (
    <div className="space-y-1" data-testid="progress-table">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold">{t('progressTitle')}</span>
        <button className="rounded border border-edge-subtle px-2 py-0.5 text-xs hover:bg-surface-hover"
          onClick={onRefresh}>
          {t('progressRefresh')}
        </button>
      </div>
      <div className="max-h-40 overflow-auto rounded border border-edge-subtle/60">
        <table className="w-full text-left text-[11px]">
          <thead className="sticky top-0 bg-surface-sunken">
            <tr>
              <th className="px-1 py-0.5">{t('colStratum')}</th>
              <th className="px-1 py-0.5">{t('colPlanned')}</th>
              <th className="px-1 py-0.5">{t('colMain')}</th>
              <th className="px-1 py-0.5">{t('colLabelled')}</th>
              <th className="px-1 py-0.5">{t('colNonusable')}</th>
              <th className="px-1 py-0.5">{t('colUnvisited')}</th>
              <th className="px-1 py-0.5">{t('colResponseRate')}</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(progress.per_stratum).map(([sid, b]) => (
              <tr key={sid}>
                <td className="px-1 py-0.5">{sid}</td>
                <td className="px-1 py-0.5 font-mono">{fmtInt(b.planned)}</td>
                <td className="px-1 py-0.5 font-mono">{fmtInt(b.main)}</td>
                <td className="px-1 py-0.5 font-mono">{fmtInt(b.labelled)}</td>
                <td className="px-1 py-0.5 font-mono">{fmtInt(b.nonusable)}</td>
                <td className="px-1 py-0.5 font-mono">{fmtInt(b.unvisited)}</td>
                <td className="px-1 py-0.5 font-mono">{fmtNum(b.response_rate * 100, 1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-3 text-xs text-ink-muted">
        <span>{t('totalPlanned')}: {fmtInt(progress.total_planned)}</span>
        <span>{t('totalLabelled')}: {fmtInt(progress.total_labelled)}</span>
        <span>{t('overallResponseRate')}: {fmtNum(progress.overall_response_rate * 100, 1)}%</span>
        <span>{progress.can_estimate ? t('canEstimateYes') : t('canEstimateNo')}</span>
      </div>
    </div>
  );
}

/** 指标（UA/PA/面积）表：估计 / SE / CI / 可估。 */
function MetricsTable({ title, metrics, t, isArea }: {
  title: string;
  metrics: Record<string, MetricValue>;
  t: (k: string) => string;
  isArea?: boolean;
}) {
  return (
    <div className="space-y-1">
      <div className="text-xs font-semibold">{title}</div>
      <div className="max-h-48 overflow-auto rounded border border-edge-subtle/60">
        <table className="w-full text-left text-[11px]">
          <thead className="sticky top-0 bg-surface-sunken">
            <tr>
              <th className="px-1 py-0.5">{t('colClass')}</th>
              <th className="px-1 py-0.5">{t('colEstimate')}</th>
              <th className="px-1 py-0.5">{t('colSe')}</th>
              <th className="px-1 py-0.5">{t('colCiLow')}</th>
              <th className="px-1 py-0.5">{t('colCiHigh')}</th>
              <th className="px-1 py-0.5">{t('colEvaluable')}</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(metrics).map(([cls, m]) => (
              <tr key={cls}>
                <td className="px-1 py-0.5">{cls}</td>
                <td className="px-1 py-0.5 font-mono">
                  {isArea ? fmtArea(m.estimate) : fmtNum(m.estimate)}
                </td>
                <td className="px-1 py-0.5 font-mono">{fmtNum(m.se)}</td>
                <td className="px-1 py-0.5 font-mono">
                  {isArea ? fmtArea(m.ci_low) : fmtNum(m.ci_low)}
                </td>
                <td className="px-1 py-0.5 font-mono">
                  {isArea ? fmtArea(m.ci_high) : fmtNum(m.ci_high)}
                </td>
                <td className="px-1 py-0.5">
                  {m.evaluable ? '✓' : <span title={m.reason} className="text-status-warning">✗</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** 矩阵热力表：比例模式 [0,1] 蓝阶；计数模式按行内最大值归一。 */
function MatrixTable({ estimate, mode, t }: {
  estimate: EstimatePayload;
  mode: MatrixMode;
  t: (k: string) => string;
}) {
  const grid = mode === 'matrix' ? estimate.matrix : estimate.sample_counts;
  const max = useMemo(() => {
    let m = 0;
    for (const row of grid) {
      for (const v of row) {
        if (v != null && Number.isFinite(v) && v > m) m = v;
      }
    }
    return m;
  }, [grid]);
  return (
    <div className="overflow-auto rounded border border-edge-subtle/60" data-testid="matrix-table">
      <table className="w-full text-left text-[10px]">
        <thead className="sticky top-0 bg-surface-sunken">
          <tr>
            <th className="px-1 py-0.5">{t('colMapClass')}</th>
            {estimate.ref_classes.map((c) => (
              <th key={c} className="px-1 py-0.5">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {estimate.strata.map((rowCls, i) => (
            <tr key={rowCls}>
              <td className="px-1 py-0.5">{rowCls}</td>
              {(grid[i] ?? []).map((v, j) => {
                const intensity = v == null || max <= 0 ? 0 : mode === 'matrix' ? v : v / max;
                const bg = v == null
                  ? 'transparent'
                  : `rgba(37, 99, 235, ${Math.min(0.85, Math.max(0.04, intensity))})`;
                return (
                  <td key={j} className="px-1 py-0.5 font-mono"
                    style={{ background: bg, color: intensity > 0.5 ? '#ffffff' : undefined }}>
                    {v == null ? '—' : fmtNum(v, 3)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 估计视图：状态徽章 + OA 卡 + UA/PA/面积表 + 矩阵切换。 */
function EstimateView({ estimate, matrixMode, onMatrixMode, t }: {
  estimate: EstimatePayload;
  matrixMode: MatrixMode;
  onMatrixMode: (m: MatrixMode) => void;
  t: (k: string) => string;
}) {
  const oa = estimate.overall_accuracy;
  return (
    <div className="space-y-2" data-testid="estimate-card">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold">{t('estimateTitle')}</span>
        <span data-testid="estimate-status-badge"
          className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
            estimate.status === 'supported'
              ? 'bg-emerald-100 text-emerald-800'
              : estimate.status === 'partial'
                ? 'bg-amber-100 text-amber-800'
                : 'bg-red-100 text-red-800'
          }`}>
          {t(estimateStatusLabel(estimate.status))}
        </span>
      </div>
      {(estimate.limitations?.length ?? 0) > 0 && (
        <div className="text-xs text-status-warning">
          <div className="font-medium">{t('limitationsTitle')}</div>
          <ul className="list-disc pl-4">
            {estimate.limitations.map((l, i) => <li key={i}>{l}</li>)}
          </ul>
        </div>
      )}
      <div className="rounded border border-edge-subtle p-2 text-xs">
        <div className="font-semibold">{t('oaTitle')}</div>
        <div className="flex flex-wrap gap-3 font-mono">
          <span>{fmtNum(oa.estimate)} ± {fmtNum(oa.se)}</span>
          <span>{t('ciLabel')}: [{fmtNum(oa.ci_low)}, {fmtNum(oa.ci_high)}]</span>
          {oa.ci_clipped && <span className="text-status-warning">{t('ciClippedNote')}</span>}
          {!oa.evaluable && (
            <span className="text-status-critical" title={oa.reason}>{t('notEvaluable')}</span>
          )}
        </div>
      </div>
      <MetricsTable title={t('uaTitle')} metrics={estimate.user_accuracy} t={t} />
      <MetricsTable title={t('paTitle')} metrics={estimate.producer_accuracy} t={t} />
      <MetricsTable title={t('areaTitle')} metrics={estimate.area_estimate} t={t} isArea />
      <div className="space-y-1">
        <div className="flex items-center gap-1 text-xs">
          <button className={matrixMode === 'matrix'
            ? 'rounded bg-accent px-2 py-0.5 text-white'
            : 'rounded border border-edge-subtle px-2 py-0.5 hover:bg-surface-hover'}
            onClick={() => onMatrixMode('matrix')}>
            {t('showMatrix')}
          </button>
          <button className={matrixMode === 'counts'
            ? 'rounded bg-accent px-2 py-0.5 text-white'
            : 'rounded border border-edge-subtle px-2 py-0.5 hover:bg-surface-hover'}
            onClick={() => onMatrixMode('counts')}>
            {t('showCounts')}
          </button>
          <span className="text-ink-muted">
            {t(matrixMode === 'matrix' ? 'matrixTitle' : 'countsTitle')}
          </span>
        </div>
        <MatrixTable estimate={estimate} mode={matrixMode} t={t} />
      </div>
      {estimate.excluded_total > 0 && (
        <p className="text-[10px] text-ink-muted">
          excluded: {fmtInt(estimate.excluded_total)}
          {Object.entries(estimate.excluded_reasons ?? {}).map(([k, v]) => ` · ${k} ${fmtInt(v)}`)}
        </p>
      )}
    </div>
  );
}

export default SamplingAccuracyPanel;
