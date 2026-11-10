'use client';

/**
 * 字段计算工作台（G19）——「describe → 构造 → 预览(采样) → 确认执行 → 回读/入图」。
 *
 * 纪律：
 * - 预览是 sample/estimate，绝不把采样错误率冒充全量错误率；degraded / 预算 /
 *   作用范围 / 单位量纲如实披露。
 * - generationRef：表单 / scope / 单位任一变更即自增并使旧预览失效；异步响应
 *   回来时若代次与发起时不一致则整体丢弃（不 setState），杜绝迟到响应覆盖新表单。
 * - 确认执行绑定已看到预览的 confirmation.params_fingerprint 作为
 *   expected_fingerprint；persistence 如实区分 session_ref 与 project_artifact。
 * - 所有文案走 useT('fieldCompute')；表达式示例是「可编辑模板」，示例字段名为
 *   占位（不读取 describe 的特定字段），用户按实际列名替换。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { FlaskConical, RefreshCw } from 'lucide-react';

import {
  fieldComputeApi,
  parseFieldComputeError,
  type FieldComputeErrorInfo,
  type DescribeResponse,
  type PlanResponse,
  type PreviewResponse,
  type ComputeReceipt,
  type ResultResponse,
  type CreatePlanRequest,
  type ColumnSpec,
  type ScopeSpec,
  type PredicateSpec,
} from '@/lib/api/field-compute';
import { listUploads } from '@/lib/api/upload';
import { dataFabricApi } from '@/lib/api/data-fabric';
import { useHudStore } from '@/lib/store/useHudStore';
import { useToastStore } from '@/components/ui/toast';
import { useT } from '@/lib/i18n/useT';
import type { GeoJSONFeatureCollection } from '@/lib/types';

// ── 纯格式化/解析助手（模块级；不调用 t，不渲染）────────────────────────

function shortCell(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') {
    try { return JSON.stringify(value); } catch { return String(value); }
  }
  return String(value);
}

function asList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(shortCell);
  if (value === null || value === undefined || value === '') return [];
  return [shortCell(value)];
}

function summaryPairs(value: unknown): Array<[string, string]> {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, shortCell(v)]);
  }
  if (value === null || value === undefined || value === '') return [];
  return [['', shortCell(value)]];
}

/** field_units 文本（每行 字段名=canonical_unit）→ 映射。 */
function parseFieldUnits(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split('\n')) {
    const idx = line.indexOf('=');
    if (idx < 0) continue;
    const k = line.slice(0, idx).trim();
    const v = line.slice(idx + 1).trim();
    if (k && v) out[k] = v;
  }
  return out;
}

function coercePredValue(raw: string): unknown {
  const s = raw.trim();
  if (s === 'true') return true;
  if (s === 'false') return false;
  if (s !== '' && !Number.isNaN(Number(s))) return Number(s);
  return raw;
}

const PREDICATE_OPS = ['eq', 'ne', 'lt', 'le', 'gt', 'ge', 'is_null', 'not_null', 'in'];

interface UploadOption {
  ref: string;
  label: string;
}

interface Example {
  id: string;
  title: string;
  desc: string;
  expression: string;
  targetName: string;
  declaredUnit: string;
  fieldUnitsText: string;
  scope: 'all_rows' | 'predicate';
}

interface FieldComputePanelProps {
  sessionId?: string | null;
  ownerToken?: string | null;
}

export function FieldComputePanel({ sessionId: sessionIdProp, ownerToken }: FieldComputePanelProps) {
  const sessionId = sessionIdProp ?? null;
  const t = useT('fieldCompute');
  const addToast = useToastStore((s) => s.addToast);
  const addLayer = useHudStore((s) => s.addLayer);
  const updateLayer = useHudStore((s) => s.updateLayer);

  // ── state ─────────────────────────────────────────────────────────────
  const [uploads, setUploads] = useState<UploadOption[]>([]);
  const [sourceRef, setSourceRef] = useState('');
  const [describeRes, setDescribeRes] = useState<DescribeResponse | null>(null);
  const [fieldSearch, setFieldSearch] = useState('');

  const [targetName, setTargetName] = useState('');
  const [expression, setExpression] = useState('');
  const [scopeMode, setScopeMode] = useState<'all_rows' | 'predicate'>('all_rows');
  const [predField, setPredField] = useState('');
  const [predOp, setPredOp] = useState('eq');
  const [predValue, setPredValue] = useState('');
  const [declaredUnit, setDeclaredUnit] = useState('');
  const [fieldUnitsText, setFieldUnitsText] = useState('');
  const [timezone, setTimezone] = useState('');
  const [dateFormat, setDateFormat] = useState('');
  const [errorPolicy, setErrorPolicy] = useState('null');
  const [projectId, setProjectId] = useState('');

  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [receipt, setReceipt] = useState<ComputeReceipt | null>(null);
  const [result, setResult] = useState<ResultResponse | null>(null);

  const [describeLoading, setDescribeLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [compileError, setCompileError] = useState<FieldComputeErrorInfo | null>(null);
  const [execError, setExecError] = useState<FieldComputeErrorInfo | null>(null);
  const [staleHint, setStaleHint] = useState(false);
  const [cancelled, setCancelled] = useState(false);

  // ── refs ──────────────────────────────────────────────────────────────
  const generationRef = useRef(0);
  const previewShownRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  const refreshUploads = useCallback(async () => {
    if (!sessionId) return;
    try {
      const res = await listUploads(sessionId, { ownerToken });
      const options = (res.uploads ?? [])
        .filter((u) => u.session_ref)
        .map((u) => ({
          ref: u.session_ref as string,
          label: `${u.original_name} (${u.feature_count ?? '?'})`,
        }));
      setUploads(options);
    } catch {
      setUploads([]);
    }
  }, [sessionId, ownerToken]);

  useEffect(() => {
    // 会话/owner 变化：重置整条链路并让任何在途响应失效。
    generationRef.current += 1;
    previewShownRef.current = false;
    setDescribeRes(null);
    setSourceRef('');
    setFieldSearch('');
    setTargetName('');
    setExpression('');
    setScopeMode('all_rows');
    setPredField('');
    setPredOp('eq');
    setPredValue('');
    setDeclaredUnit('');
    setFieldUnitsText('');
    setTimezone('');
    setDateFormat('');
    setErrorPolicy('null');
    setProjectId('');
    setPlan(null);
    setPreview(null);
    setReceipt(null);
    setResult(null);
    setCompileError(null);
    setExecError(null);
    setStaleHint(false);
    setCancelled(false);
    void refreshUploads();
  }, [refreshUploads]);

  /** 任一参数变更：自增代次 + 使旧预览失效（清空或标记 stale）。 */
  const bumpGeneration = () => {
    generationRef.current += 1;
    if (previewShownRef.current) {
      setStaleHint(true);
      previewShownRef.current = false;
    }
    setPreview(null);
    setPlan(null);
    setCompileError(null);
    setExecError(null);
    setCancelled(false);
  };

  // ── 示例（可编辑模板；表达式/字段名是占位数据，非 UI 文案）───────────
  const examples: Example[] = [
    {
      id: 'density',
      title: t('exDensity'),
      desc: t('exDensityDesc'),
      expression: '{人口} / {面积 平方千米}',
      targetName: '人口密度',
      declaredUnit: '',
      fieldUnitsText: '人口=persons\n面积 平方千米=square_kilometers',
      scope: 'all_rows',
    },
    {
      id: 'rate',
      title: t('exRate'),
      desc: t('exRateDesc'),
      expression: 'if({基期} == 0, null, ({现期} - {基期}) / {基期} * 100)',
      targetName: '变化率',
      declaredUnit: 'percent',
      fieldUnitsText: '',
      scope: 'all_rows',
    },
    {
      id: 'class',
      title: t('exClass'),
      desc: t('exClassDesc'),
      expression: "if({人口} >= 1000000, 'metro', 'town')",
      targetName: '规模分类',
      declaredUnit: '',
      fieldUnitsText: '',
      scope: 'all_rows',
    },
    {
      id: 'pad',
      title: t('exPad'),
      desc: t('exPadDesc'),
      expression: "pad_left(to_string({行政区代码}), 6, '0')",
      targetName: '标准行政区代码',
      declaredUnit: '',
      fieldUnitsText: '',
      scope: 'all_rows',
    },
  ];

  // ── 请求构造 ─────────────────────────────────────────────────────────
  const buildPlanRequest = (sid: string): CreatePlanRequest => {
    const column: ColumnSpec = {
      name: targetName.trim(),
      expression: expression.trim(),
    };
    if (declaredUnit.trim()) column.declared_unit = declaredUnit.trim();

    let scope: ScopeSpec = { mode: 'all_rows' };
    if (scopeMode === 'predicate' && predField.trim()) {
      const predicate: PredicateSpec = { field: predField.trim(), op: predOp };
      if (predOp === 'in') {
        predicate.values = predValue.split(',').map((s) => s.trim()).filter(Boolean);
      } else if (predOp !== 'is_null' && predOp !== 'not_null') {
        predicate.value = coercePredValue(predValue);
      }
      scope = { mode: 'predicate', predicate };
    }

    const req: CreatePlanRequest = {
      session_id: sid,
      source_ref: sourceRef,
      columns: [column],
      scope,
      error_policy: errorPolicy,
    };
    const fus = parseFieldUnits(fieldUnitsText);
    if (Object.keys(fus).length) req.field_units = fus;
    if (timezone.trim()) req.timezone = timezone.trim();
    if (dateFormat.trim()) req.date_format = dateFormat.trim();
    return req;
  };

  // ── describe ─────────────────────────────────────────────────────────
  const handleDescribe = async () => {
    if (!sessionId || !sourceRef) return;
    setDescribeLoading(true);
    setDescribeRes(null);
    setCompileError(null);
    setExecError(null);
    const gen = generationRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const res = await fieldComputeApi.describe(
        { session_id: sessionId, source_ref: sourceRef },
        { ownerToken, signal: controller.signal },
      );
      if (generationRef.current !== gen || controller.signal.aborted) return;
      setDescribeRes(res);
      setFieldSearch('');
    } catch (err) {
      if (controller.signal.aborted) { setCancelled(true); return; }
      if (generationRef.current !== gen) return;
      addToast(t('describeFailed'), 'error');
    } finally {
      setDescribeLoading(false);
    }
  };

  // ── preview（createPlan → preview；迟到响应按 generation 丢弃）───────
  const handlePreview = async () => {
    if (!sessionId || !describeRes || !sourceRef || !targetName.trim() || !expression.trim()) return;
    setPreviewing(true);
    setCancelled(false);
    setStaleHint(false);
    setCompileError(null);
    setExecError(null);
    setReceipt(null);
    const gen = generationRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const planRes = await fieldComputeApi.createPlan(buildPlanRequest(sessionId), {
        ownerToken, signal: controller.signal,
      });
      if (generationRef.current !== gen || controller.signal.aborted) return;
      setPlan(planRes);
      const pv = await fieldComputeApi.preview(planRes.plan.plan_id, sessionId, gen, {
        ownerToken, signal: controller.signal,
      });
      if (generationRef.current !== gen || controller.signal.aborted) return;
      setPreview(pv);
      previewShownRef.current = true;
    } catch (err) {
      if (controller.signal.aborted) { setCancelled(true); return; }
      if (generationRef.current !== gen) return;
      setCompileError(parseFieldComputeError(err));
    } finally {
      setPreviewing(false);
    }
  };

  // ── execute（绑定已看到预览的 params_fingerprint）───────────────────
  const handleExecute = async () => {
    if (!sessionId || !plan || !preview) return;
    setExecuting(true);
    setCancelled(false);
    setExecError(null);
    const gen = generationRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const res = await fieldComputeApi.execute(
        plan.plan.plan_id,
        sessionId,
        {
          expected_fingerprint: preview.confirmation.params_fingerprint,
          project_id: projectId.trim() || undefined,
        },
        { ownerToken, signal: controller.signal },
      );
      if (generationRef.current !== gen || controller.signal.aborted) return;
      setReceipt(res.receipt);
      setResult(null);
    } catch (err) {
      if (controller.signal.aborted) { setCancelled(true); return; }
      if (generationRef.current !== gen) return;
      setExecError(parseFieldComputeError(err));
    } finally {
      setExecuting(false);
    }
  };

  const handleCancel = () => {
    abortRef.current?.abort();
    setPreviewing(false);
    setExecuting(false);
    setCancelled(true);
  };

  const handleOpenResult = async () => {
    if (!sessionId || !receipt) return;
    try {
      const res = await fieldComputeApi.getResult(sessionId, receipt.output_ref, { ownerToken });
      setResult(res);
    } catch {
      addToast(t('openResultFailed'), 'error');
    }
  };

  const handleLoadToMap = async () => {
    if (!sessionId || !receipt) return;
    const layerId = `fc-${receipt.compute_fingerprint}`;
    const placeholder: GeoJSONFeatureCollection = {
      type: 'FeatureCollection',
      features: [],
      metadata: { ref_id: receipt.output_ref },
    };
    addLayer({
      id: layerId,
      name: `field-compute:${receipt.compute_fingerprint}`,
      type: 'vector',
      visible: true,
      opacity: 1,
      group: 'reference',
      source: placeholder,
      _refId: receipt.output_ref,
      style: { color: '#16a34a' },
    });
    try {
      const geojson = await dataFabricApi.fetchRefGeoJSON(receipt.output_ref, sessionId, { ownerToken });
      if (geojson && (geojson.type === 'FeatureCollection' || Array.isArray(geojson.features))) {
        updateLayer(layerId, { source: geojson });
      }
      addToast(t('loadSuccess'), 'success');
    } catch {
      addToast(t('loadFailed'), 'warning');
    }
  };

  const applyExample = (ex: Example) => {
    setTargetName(ex.targetName);
    setExpression(ex.expression);
    setDeclaredUnit(ex.declaredUnit);
    setFieldUnitsText(ex.fieldUnitsText);
    setScopeMode(ex.scope);
    bumpGeneration();
  };

  const insertFieldRef = (name: string) => {
    setExpression((prev) => `${prev}{${name}}`);
    bumpGeneration();
  };

  // ── 派生值 ───────────────────────────────────────────────────────────
  const fields = describeRes?.fields ?? [];
  const filteredFields = fields.filter((f) =>
    f.name.toLowerCase().includes(fieldSearch.toLowerCase()),
  );
  const functions = describeRes?.functions ?? [];
  const sampleRows = describeRes?.sample_rows ?? [];
  const sampleCols = sampleRows.length > 0 ? Object.keys(sampleRows[0]).slice(0, 8) : [];
  const geom = describeRes?.geometry;
  const geometryBlocked = !!geom?.block_reason || !geom?.mode;
  const canPreview = !!sessionId && !!describeRes && targetName.trim().length > 0 && expression.trim().length > 0;
  const busy = previewing || executing;
  const resultColumns = (result?.fields ?? []).map((f) => (typeof f === 'string' ? f : f.name));

  const errorInfoView = (info: FieldComputeErrorInfo) => (
    <div className="flex flex-col gap-0.5 text-meta">
      {info.code && <span data-testid="fc-error-code">{t('errorCode', { code: info.code })}</span>}
      {info.detail && <span data-testid="fc-error-detail">{t('errorDetail', { detail: info.detail })}</span>}
      {info.position !== undefined && (
        <span data-testid="fc-error-position">{t('errorPosition', { position: info.position })}</span>
      )}
      {info.hint && <span data-testid="fc-error-hint">{t('errorHint', { hint: info.hint })}</span>}
    </div>
  );

  if (!sessionId) {
    return (
      <div className="p-3 text-meta text-ink-600" data-testid="fc-no-session">
        {t('noSession')}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-3" data-testid="fc-root">
      {/* ── 数据来源 ─────────────────────────────────────────────── */}
      <section className="rounded border border-line" data-testid="fc-source">
        <header className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
          <span className="flex items-center gap-1.5 text-meta font-semibold text-ink">
            <FlaskConical size={14} />
            {t('title')}
          </span>
          <button
            type="button"
            className="flex items-center gap-1 text-meta text-ink-600 hover:text-ink"
            onClick={() => void refreshUploads()}
            data-testid="fc-refresh"
            aria-label={t('refresh')}
          >
            <RefreshCw size={12} />
            {t('refresh')}
          </button>
        </header>
        <div className="flex flex-col gap-2 p-3">
          {uploads.length === 0 ? (
            <p className="text-meta text-ink-600" data-testid="fc-no-uploads">{t('noUploads')}</p>
          ) : (
            <label className="flex flex-col gap-1 text-meta text-ink-600">
              {t('selectSource')}
              <select
                className="rounded border border-line bg-white px-2 py-1 text-ink"
                value={sourceRef}
                onChange={(e) => { setSourceRef(e.target.value); setDescribeRes(null); bumpGeneration(); }}
                data-testid="fc-source-select"
              >
                <option value="">—</option>
                {uploads.map((u) => (
                  <option key={u.ref} value={u.ref}>{u.label}</option>
                ))}
              </select>
            </label>
          )}
          <button
            type="button"
            className="btn btn-primary text-meta"
            disabled={!sourceRef || describeLoading}
            onClick={() => void handleDescribe()}
            data-testid="fc-describe"
          >
            {describeLoading ? t('describing') : t('describe')}
          </button>
        </div>
      </section>

      {describeRes && (
        <>
          {/* ── 字段 / 函数 / 几何资格 ──────────────────────────────── */}
          <section className="rounded border border-line" data-testid="fc-schema">
            <header className="border-b border-line px-3 py-2 text-meta font-semibold text-ink">
              {t('schemaTitle')}
            </header>
            <div className="flex flex-col gap-2 p-3 text-meta text-ink-700">
              <div className="flex flex-wrap gap-x-3 gap-y-1" data-testid="fc-stats">
                <span>{t('rowCount', { count: describeRes.row_count })}</span>
                <span>{t('dataShape', { shape: describeRes.data_shape })}</span>
                <span>{t('crs', { crs: describeRes.crs, source: describeRes.crs_source })}</span>
              </div>
              <div className="rounded border border-line px-2 py-1" data-testid="fc-geometry">
                <b>{t('geometryTitle')}</b>
                {' · '}
                {t('geometryMode', { mode: geom?.mode || '-' })}
                {' · '}
                {geometryBlocked
                  ? t('geometryBlocked', { reason: geom?.block_reason || 'unknown' })
                  : t('geometryEligible')}
                {geom?.detail && <p className="text-ink-600">{geom.detail}</p>}
              </div>
              {describeRes.unit_declaration_required && (
                <p className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-amber-800" data-testid="fc-unit-required">
                  {t('unitRequired')}
                </p>
              )}

              <div data-testid="fc-fields">
                <div className="flex items-center justify-between gap-2">
                  <b>{t('fieldsTitle', { count: fields.length })}</b>
                  <input
                    className="w-32 rounded border border-line px-2 py-0.5 text-ink"
                    value={fieldSearch}
                    onChange={(e) => setFieldSearch(e.target.value)}
                    placeholder={t('fieldSearch')}
                    aria-label={t('fieldSearch')}
                    data-testid="fc-field-search"
                  />
                </div>
                <ul className="mt-1 flex max-h-40 flex-col gap-1 overflow-y-auto">
                  {filteredFields.map((f) => (
                    <li key={f.name}>
                      <button
                        type="button"
                        className="flex w-full items-center justify-between gap-2 rounded border border-line px-2 py-1 text-left hover:bg-slate-50"
                        onClick={() => insertFieldRef(f.name)}
                        aria-label={t('insertField')}
                        title={t('insertField')}
                        data-testid={`fc-field-${f.name}`}
                      >
                        <b className="text-ink-800">{f.name}</b>
                        <span className="text-ink-500">{t('fieldDtype', { dtype: f.dtype })}</span>
                      </button>
                    </li>
                  ))}
                </ul>
                {filteredFields.length === 0 && (
                  <p className="text-ink-500" data-testid="fc-fields-empty">{t('fieldNoMatch')}</p>
                )}
              </div>

              <details data-testid="fc-functions">
                <summary className="cursor-pointer font-semibold text-ink">
                  {t('functionsTitle', { count: functions.length })}
                </summary>
                <ul className="mt-1 flex flex-col gap-1">
                  {functions.map((fn) => (
                    <li key={fn.name} className="rounded border border-line px-2 py-1" data-testid={`fc-fn-${fn.name}`}>
                      <div className="flex flex-wrap items-center gap-2">
                        <b className="text-ink-800">{fn.name}</b>
                        <span className="text-ink-500">{t('functionMinMax', { min: fn.min_args, max: fn.max_args })}</span>
                        <span className="text-ink-500">{t('functionResult', { type: fn.result_type })}</span>
                      </div>
                      <div className="text-ink-600">{fn.help_zh}</div>
                      <div className="text-ink-500">{fn.null_behavior}</div>
                    </li>
                  ))}
                </ul>
              </details>

              {sampleRows.length > 0 && (
                <div data-testid="fc-sample-rows">
                  <b>{t('sampleRowsTitle', { count: sampleRows.length })}</b>
                  <div className="mt-1 overflow-x-auto">
                    <table className="min-w-full text-meta">
                      <thead>
                        <tr className="text-left text-ink-600">
                          {sampleCols.map((c) => <th key={c} className="px-2 py-1">{c}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {sampleRows.slice(0, 5).map((row, i) => (
                          <tr key={i} className="border-t border-line">
                            {sampleCols.map((c) => (
                              <td key={c} className="px-2 py-1 text-ink-800">{shortCell(row[c]) || t('valueEmpty')}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          </section>

          {/* ── 构造计算列 ─────────────────────────────────────────── */}
          <form
            className="flex flex-col gap-3 rounded border border-line"
            onSubmit={(e) => { e.preventDefault(); void handlePreview(); }}
            data-testid="fc-form"
          >
            <header className="border-b border-line px-3 py-2 text-meta font-semibold text-ink">
              {t('formTitle')}
            </header>

            <div className="flex flex-col gap-1.5 p-3" data-testid="fc-examples">
              <b className="text-meta text-ink-700">{t('examplesTitle')}</b>
              <div className="flex flex-wrap gap-1.5">
                {examples.map((ex) => (
                  <button
                    key={ex.id}
                    type="button"
                    className="rounded border border-line px-2 py-1 text-meta text-ink-700 hover:bg-slate-50"
                    onClick={() => applyExample(ex)}
                    title={ex.desc}
                    data-testid={`fc-example-${ex.id}`}
                  >
                    {ex.title}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex flex-col gap-2 p-3">
              <label className="flex flex-col gap-1 text-meta text-ink-600">
                {t('targetName')}
                <input
                  className="rounded border border-line bg-white px-2 py-1 text-ink"
                  value={targetName}
                  onChange={(e) => { setTargetName(e.target.value); bumpGeneration(); }}
                  placeholder={t('targetNamePlaceholder')}
                  data-testid="fc-target-name"
                />
              </label>
              <label className="flex flex-col gap-1 text-meta text-ink-600">
                {t('expression')}
                <textarea
                  className="min-h-[64px] rounded border border-line bg-white px-2 py-1 font-mono text-ink"
                  value={expression}
                  onChange={(e) => { setExpression(e.target.value); bumpGeneration(); }}
                  placeholder={t('expressionPlaceholder')}
                  data-testid="fc-expression"
                />
              </label>
              <p className="text-meta text-ink-500" data-testid="fc-expression-hint">{t('expressionHint')}</p>

              <label className="flex flex-col gap-1 text-meta text-ink-600">
                {t('scopeMode')}
                <select
                  className="rounded border border-line bg-white px-2 py-1 text-ink"
                  value={scopeMode}
                  onChange={(e) => { setScopeMode(e.target.value as 'all_rows' | 'predicate'); bumpGeneration(); }}
                  data-testid="fc-scope"
                >
                  <option value="all_rows">{t('scopeAllRows')}</option>
                  <option value="predicate">{t('scopePredicate')}</option>
                </select>
              </label>
              {scopeMode === 'predicate' && (
                <div className="grid grid-cols-3 gap-2" data-testid="fc-predicate">
                  <label className="flex flex-col gap-1 text-meta text-ink-600">
                    {t('predField')}
                    <input
                      className="rounded border border-line bg-white px-2 py-1 text-ink"
                      value={predField}
                      onChange={(e) => { setPredField(e.target.value); bumpGeneration(); }}
                      data-testid="fc-pred-field"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-meta text-ink-600">
                    {t('predOp')}
                    <select
                      className="rounded border border-line bg-white px-2 py-1 text-ink"
                      value={predOp}
                      onChange={(e) => { setPredOp(e.target.value); bumpGeneration(); }}
                      data-testid="fc-pred-op"
                    >
                      {PREDICATE_OPS.map((op) => <option key={op} value={op}>{op}</option>)}
                    </select>
                  </label>
                  <label className="flex flex-col gap-1 text-meta text-ink-600">
                    {t('predValue')}
                    <input
                      className="rounded border border-line bg-white px-2 py-1 text-ink"
                      value={predValue}
                      onChange={(e) => { setPredValue(e.target.value); bumpGeneration(); }}
                      data-testid="fc-pred-value"
                    />
                  </label>
                </div>
              )}

              <label className="flex flex-col gap-1 text-meta text-ink-600">
                {t('declaredUnit')}
                <input
                  className="rounded border border-line bg-white px-2 py-1 text-ink"
                  value={declaredUnit}
                  onChange={(e) => { setDeclaredUnit(e.target.value); bumpGeneration(); }}
                  placeholder={t('declaredUnitPlaceholder')}
                  data-testid="fc-declared-unit"
                />
              </label>
              <label className="flex flex-col gap-1 text-meta text-ink-600">
                {t('fieldUnits')}
                <textarea
                  className="min-h-[48px] rounded border border-line bg-white px-2 py-1 font-mono text-ink"
                  value={fieldUnitsText}
                  onChange={(e) => { setFieldUnitsText(e.target.value); bumpGeneration(); }}
                  placeholder={t('fieldUnitsPlaceholder')}
                  data-testid="fc-field-units"
                />
              </label>
              <p className="text-meta text-ink-500">{t('fieldUnitsHint')}</p>

              <div className="grid grid-cols-2 gap-2">
                <label className="flex flex-col gap-1 text-meta text-ink-600">
                  {t('timezone')}
                  <input
                    className="rounded border border-line bg-white px-2 py-1 text-ink"
                    value={timezone}
                    onChange={(e) => { setTimezone(e.target.value); bumpGeneration(); }}
                    placeholder={t('timezonePlaceholder')}
                    data-testid="fc-timezone"
                  />
                </label>
                <label className="flex flex-col gap-1 text-meta text-ink-600">
                  {t('dateFormat')}
                  <input
                    className="rounded border border-line bg-white px-2 py-1 text-ink"
                    value={dateFormat}
                    onChange={(e) => { setDateFormat(e.target.value); bumpGeneration(); }}
                    placeholder={t('dateFormatPlaceholder')}
                    data-testid="fc-date-format"
                  />
                </label>
              </div>

              <label className="flex flex-col gap-1 text-meta text-ink-600">
                {t('errorPolicy')}
                <select
                  className="rounded border border-line bg-white px-2 py-1 text-ink"
                  value={errorPolicy}
                  onChange={(e) => { setErrorPolicy(e.target.value); bumpGeneration(); }}
                  data-testid="fc-error-policy"
                >
                  <option value="null">{t('errorPolicyNull')}</option>
                  <option value="abort">{t('errorPolicyAbort')}</option>
                </select>
              </label>

              <label className="flex flex-col gap-1 text-meta text-ink-600">
                {t('projectId')}
                <input
                  className="rounded border border-line bg-white px-2 py-1 text-ink"
                  value={projectId}
                  onChange={(e) => { setProjectId(e.target.value); bumpGeneration(); }}
                  placeholder={t('projectIdPlaceholder')}
                  data-testid="fc-project-id"
                />
              </label>
            </div>

            <div className="flex flex-col gap-2 border-t border-line p-3">
              {staleHint && (
                <p className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-meta text-amber-800" data-testid="fc-stale">
                  {t('staleHint')}
                </p>
              )}
              {!preview && !staleHint && (
                <p className="text-meta text-ink-500" data-testid="fc-no-preview-hint">{t('noPreviewHint')}</p>
              )}
              {cancelled && (
                <p className="text-meta text-ink-500" data-testid="fc-cancelled">{t('cancelled')}</p>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="submit"
                  className="btn btn-primary text-meta"
                  disabled={!canPreview || busy}
                  data-testid="fc-preview"
                >
                  {previewing ? t('previewing') : t('preview')}
                </button>
                <button
                  type="button"
                  className="btn btn-secondary text-meta"
                  disabled={!preview || busy}
                  onClick={() => void handleExecute()}
                  data-testid="fc-execute"
                >
                  {executing ? t('executing') : t('execute')}
                </button>
                {busy && (
                  <button
                    type="button"
                    className="btn text-meta"
                    onClick={handleCancel}
                    data-testid="fc-cancel"
                  >
                    {t('cancel')}
                  </button>
                )}
              </div>
              {preview && (
                <p className="text-meta text-ink-500" data-testid="fc-confirmation-note">{t('confirmationNote')}</p>
              )}

              {compileError && (
                <div className="rounded border border-red-300 bg-red-50 px-2 py-1 text-red-800" data-testid="fc-compile-error">
                  <b>{t('compileErrorTitle')}</b>
                  {errorInfoView(compileError)}
                </div>
              )}
              {execError && (
                <div className="rounded border border-red-300 bg-red-50 px-2 py-1 text-red-800" data-testid="fc-exec-error">
                  <b>{t('execErrorTitle')}</b>
                  {errorInfoView(execError)}
                </div>
              )}
            </div>
          </form>

          {/* ── 采样预览 ───────────────────────────────────────────── */}
          {preview && (
            <section className="rounded border border-line" data-testid="fc-preview-view">
              <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
                <span className="text-meta font-semibold text-ink">
                  {t('previewTitle', { rows: preview.sampled_rows, total: preview.total_rows })}
                  {preview.truncated ? ` ${t('truncated')}` : ''}
                </span>
                <span className="rounded bg-amber-100 px-1.5 py-0.5 text-meta text-amber-800" data-testid="fc-sample-badge">
                  {t('sampleBadge')}
                </span>
              </header>
              <div className="flex flex-col gap-2 p-3 text-meta text-ink-700">
                <p className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-amber-800" data-testid="fc-sample-note">
                  {t('sampleOnlyNote')}
                </p>

                <div data-testid="fc-preview-columns">
                  <b>{t('columnsTitle')}</b>
                  <div className="mt-1 overflow-x-auto">
                    <table className="min-w-full text-meta">
                      <thead>
                        <tr className="text-left text-ink-600">
                          <th className="px-2 py-1">{t('colName')}</th>
                          <th className="px-2 py-1">{t('colFormula')}</th>
                          <th className="px-2 py-1">{t('colDtype')}</th>
                          <th className="px-2 py-1">{t('colUnit')}</th>
                          <th className="px-2 py-1">{t('colUnitStatus')}</th>
                          <th className="px-2 py-1">{t('colDimension')}</th>
                          <th className="px-2 py-1">{t('colMeasurement')}</th>
                          <th className="px-2 py-1">{t('colDeclaredUnit')}</th>
                          <th className="px-2 py-1">{t('colComputed')}</th>
                          <th className="px-2 py-1">{t('colNulls')}</th>
                          <th className="px-2 py-1">{t('colErrors')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.columns.map((c) => (
                          <tr key={c.name} className="border-t border-line" data-testid={`fc-preview-col-${c.name}`}>
                            <td className="px-2 py-1">{c.name}</td>
                            <td className="px-2 py-1 font-mono">{c.formula}</td>
                            <td className="px-2 py-1">{c.dtype}</td>
                            <td className="px-2 py-1">{c.unit || t('valueEmpty')}</td>
                            <td className="px-2 py-1">{c.unit_status}</td>
                            <td className="px-2 py-1">{c.unit_dimension || t('valueEmpty')}</td>
                            <td className="px-2 py-1">{c.measurement_kind || t('valueEmpty')}</td>
                            <td className="px-2 py-1">{c.declared_unit || t('valueEmpty')}</td>
                            <td className="px-2 py-1">{c.computed}</td>
                            <td className="px-2 py-1">{c.nulls}</td>
                            <td className="px-2 py-1">{c.errors}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div data-testid="fc-before-after">
                  <b>{t('beforeAfterTitle')}</b>
                  <div className="mt-1 overflow-x-auto">
                    <table className="min-w-full text-meta">
                      <thead>
                        <tr className="text-left text-ink-600">
                          <th className="px-2 py-1">{t('colRow')}</th>
                          <th className="px-2 py-1">{t('colBefore')}</th>
                          <th className="px-2 py-1">{t('colAfter')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.before_after.map((ba) => (
                          <tr key={ba.row} className="border-t border-line">
                            <td className="px-2 py-1">{ba.row}</td>
                            <td className="px-2 py-1 font-mono">{shortCell(ba.before) || t('valueEmpty')}</td>
                            <td className="px-2 py-1 font-mono">{shortCell(ba.after) || t('valueEmpty')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="flex flex-wrap gap-x-4 gap-y-1" data-testid="fc-counts">
                  <span>{t('nullCountSample', { count: preview.null_count_sample })}</span>
                  <span>{t('errorCountSample', { count: preview.error_count_sample })}</span>
                  <span>{t('sampleErrorRate', { rate: preview.sample_error_rate_estimate })}</span>
                </div>
                <p className="text-ink-500" data-testid="fc-rate-note">{t('sampleErrorRateNote')}</p>

                {preview.error_samples.length > 0 && (
                  <div data-testid="fc-error-samples">
                    <b>{t('errorSamplesTitle')}</b>
                    <ul className="mt-1 flex flex-col gap-0.5">
                      {preview.error_samples.map((e, i) => (
                        <li key={i} data-testid="fc-error-sample">
                          {t('errorSampleItem', { row: e.row, column: e.column, code: e.code })}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div data-testid="fc-budget">
                  <b>{t('budgetTitle')}</b>
                  <div className="flex flex-wrap gap-x-4 gap-y-1">
                    <span>{t('budgetRows', { rows: preview.budget.row_count, max: preview.budget.max_rows })}</span>
                    <span>{t('budgetOps', { ops: preview.budget.estimated_ops, max: preview.budget.max_ops_per_row })}</span>
                    <span data-testid="fc-budget-flag">
                      {preview.budget.within_budget ? t('budgetWithin') : t('budgetExceeded')}
                    </span>
                  </div>
                </div>

                <div data-testid="fc-scope-summary">
                  <b>{t('scopeSummaryTitle')}</b>
                  <ul className="mt-1 flex flex-col gap-0.5 font-mono">
                    {summaryPairs(preview.scope).map(([k, v], i) => (
                      <li key={i}>{k ? `${k}: ${v || t('valueEmpty')}` : v}</li>
                    ))}
                  </ul>
                  <span className="text-ink-600">{t('precisionPolicy', { policy: shortCell(preview.precision_policy) || '-' })}</span>
                </div>

                <div data-testid="fc-unit-summary">
                  <b>{t('unitSummaryTitle')}</b>
                  <ul className="mt-1 flex flex-col gap-0.5 font-mono">
                    {summaryPairs(preview.unit_summary).map(([k, v], i) => (
                      <li key={i}>{k ? `${k}: ${v || t('valueEmpty')}` : v}</li>
                    ))}
                  </ul>
                </div>

                {preview.degraded.length > 0 && (
                  <div data-testid="fc-degraded">
                    <b>{t('degradedTitle')}</b>
                    <ul className="mt-1 flex flex-col gap-0.5">
                      {preview.degraded.map((d, i) => <li key={i}>{d}</li>)}
                    </ul>
                  </div>
                )}

                <div data-testid="fc-next-actions">
                  <b>{t('nextActionsTitle')}</b>
                  <ul className="mt-1 flex flex-wrap gap-2">
                    {asList(preview.next_actions).map((a, i) => <li key={i}>{a}</li>)}
                  </ul>
                </div>
              </div>
            </section>
          )}

          {/* ── 执行收据 ───────────────────────────────────────────── */}
          {receipt && (
            <section className="rounded border border-line" data-testid="fc-receipt">
              <header className="border-b border-line px-3 py-2 text-meta font-semibold text-ink">
                {t('receiptTitle')}
              </header>
              <div className="flex flex-col gap-2 p-3 text-meta text-ink-700">
                <p data-testid="fc-output-ref">{t('outputRef', { ref: receipt.output_ref })}</p>
                <p data-testid="fc-rows-summary">
                  {t('rowsSummary', {
                    rowCount: receipt.row_count,
                    computed: receipt.computed_rows,
                    selected: receipt.selected_rows,
                    skipped: receipt.skipped_rows,
                  })}
                </p>
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  <span>{t('nullCount', { count: receipt.null_count })}</span>
                  <span>{t('errorCount', { count: receipt.error_count })}</span>
                </div>

                <div data-testid="fc-formula-summary">
                  <b>{t('formulaSummaryTitle')}</b>
                  <ul className="mt-1 flex flex-col gap-0.5 font-mono">
                    {asList(receipt.formula_summary).map((f, i) => <li key={i}>{f}</li>)}
                  </ul>
                </div>

                <div data-testid="fc-receipt-unit-summary">
                  <b>{t('unitSummaryTitle')}</b>
                  <ul className="mt-1 flex flex-col gap-0.5 font-mono">
                    {summaryPairs(receipt.unit_summary).map(([k, v], i) => (
                      <li key={i}>{k ? `${k}: ${v || t('valueEmpty')}` : v}</li>
                    ))}
                  </ul>
                </div>

                <div data-testid="fc-receipt-scope">
                  <b>{t('scopeSummaryTitle')}</b>
                  <ul className="mt-1 flex flex-col gap-0.5 font-mono">
                    {summaryPairs(receipt.scope).map(([k, v], i) => (
                      <li key={i}>{k ? `${k}: ${v || t('valueEmpty')}` : v}</li>
                    ))}
                  </ul>
                  <span className="text-ink-600">{t('precisionPolicy', { policy: shortCell(receipt.precision_policy) || '-' })}</span>
                </div>

                <div data-testid="fc-persistence">
                  <b>{t('persistenceTitle')}</b>
                  {' · '}
                  {receipt.persistence === 'project_artifact'
                    ? t('persistenceProject')
                    : receipt.persistence === 'session_ref'
                      ? t('persistenceSession')
                      : t('persistenceUnknown', { value: receipt.persistence })}
                  {' · '}
                  {receipt.descriptor_minted ? t('descriptorMinted') : t('descriptorNotMinted')}
                </div>

                {receipt.degraded.length > 0 && (
                  <div data-testid="fc-receipt-degraded">
                    <b>{t('degradedTitle')}</b>
                    <ul className="mt-1 flex flex-col gap-0.5">
                      {receipt.degraded.map((d, i) => <li key={i}>{d}</li>)}
                    </ul>
                  </div>
                )}

                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="btn btn-secondary text-meta"
                    onClick={() => void handleOpenResult()}
                    data-testid="fc-open-result"
                  >
                    {t('openResult')}
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary text-meta"
                    onClick={() => void handleLoadToMap()}
                    data-testid="fc-load-map"
                  >
                    {t('loadToMap')}
                  </button>
                </div>
              </div>
            </section>
          )}

          {/* ── 结果表 ─────────────────────────────────────────────── */}
          {result && (
            <section className="rounded border border-line" data-testid="fc-result">
              <header className="border-b border-line px-3 py-2 text-meta font-semibold text-ink">
                {t('resultTitle', { ref: result.output_ref })}
              </header>
              <div className="flex flex-col gap-2 p-3 text-meta text-ink-700" data-testid="fc-result-table">
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  <span>{t('resultRows', { count: result.row_count })}</span>
                  <span>{t('resultFields', { count: resultColumns.length })}</span>
                  <span data-testid="fc-result-readable">
                    {result.readable ? t('resultReadable') : t('resultNotReadable')}
                  </span>
                </div>
                <b>{t('resultSample')}</b>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-meta">
                    <thead>
                      <tr className="text-left text-ink-600">
                        {resultColumns.map((c) => <th key={c} className="px-2 py-1">{c}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {(result.sample_rows ?? []).slice(0, 5).map((row, i) => (
                        <tr key={i} className="border-t border-line">
                          {resultColumns.map((c) => (
                            <td key={c} className="px-2 py-1 text-ink-800">{shortCell(row[c]) || t('valueEmpty')}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
