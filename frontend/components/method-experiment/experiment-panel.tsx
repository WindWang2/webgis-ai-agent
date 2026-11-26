'use client';

/**
 * 方法实验工作台面板 —— 单面板纵向步骤流。
 *
 * 检视（族/问题 → 服务端方法卡与候选空间）→ 计划（候选多选 + folds/预算 →
 * 编译试验清单）→ 执行（有界窗口 ≤6 试验，partial 可继续）→ 比较（可比组
 * 排名 + incomparable 披露 + 服务端推荐或弃权）→ 采纳（记录决定，不改地图）。
 *
 * 诚实纪律：方法卡/试验/推荐全部来自服务端返回，前端不杜撰；比较方向与量纲
 * 用服务端返回的 direction/unit_kind；错误一律携带后端 detail.reason_code +
 * message；采纳未完成试验由服务端 409 MEXP_TRIAL_NOT_COMPLETED 拒绝并如实展示。
 */

import { useCallback, useMemo, useRef, useState } from 'react';
import { FlaskConical, X } from 'lucide-react';

import {
  asMethodExperimentError,
  methodExperimentApi,
} from '@/lib/api/method-experiment';
import type {
  MexpAdoptionView,
  MexpEvaluationView,
  MexpInspectResponse,
  MexpPlanView,
  MexpRunResponse,
} from '@/lib/api/method-experiment';
import { useT } from '@/lib/i18n/useT';
import { useHudStore } from '@/lib/store/useHudStore';

interface MountProps {
  sessionId?: string | null;
  ownerToken?: string | null;
}

interface PanelProps {
  sessionId: string;
  ownerToken?: string | null;
  onClose?: () => void;
}

export type { MountProps as MethodExperimentPanelProps };

type ApiErrorView = { code: string; message: string };
type Translate = (k: string, vars?: Record<string, string>) => string;

type InspectState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; data: MexpInspectResponse }
  | { status: 'error'; code: string; message: string };

const STEPS = ['inspect', 'plan', 'run', 'compare', 'adopt'] as const;

/** 已知族 id → i18n 键；未知 id 原样展示（不杜撰翻译）。 */
const FAMILY_LABEL_KEYS: Record<string, string> = {
  interpolation: 'families.interpolation',
  density: 'families.density',
  clustering: 'families.clustering',
  network_accessibility: 'families.network_accessibility',
  spatial_autocorrelation: 'families.spatial_autocorrelation',
};

function formatParams(params: Record<string, unknown>): string {
  const text = Object.entries(params)
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(', ');
  return text || '—';
}

function formatValue(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(Number(value.toPrecision(6)));
  }
  return String(value);
}

function ErrorBox({
  title, error, testId,
}: {
  title: string;
  error: ApiErrorView;
  testId: string;
}) {
  return (
    <div
      role="alert"
      className="rounded border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-xs"
      data-testid={testId}
    >
      <span className="mr-1 font-medium text-destructive">{title}</span>
      <span className="mr-1 font-mono text-destructive">{error.code}</span>
      <span>{error.message}</span>
    </div>
  );
}

/** 默认导出：自持开关态的挂载面（未开时只渲染左侧启动钮）。 */
export default function MethodExperimentMount({ sessionId, ownerToken }: MountProps) {
  const t = useT('methodExperiment');
  const [open, setOpen] = useState(false);

  if (!sessionId) return null;

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={t('panel.launch')}
        title={t('panel.launch')}
        className="fixed left-14 top-1/2 z-30 flex -translate-y-1/2 flex-col items-center gap-1 rounded-full border border-border bg-background px-1.5 py-2 text-xs shadow-lg hover:bg-accent"
        data-testid="mexp-launch"
      >
        <FlaskConical className="h-4 w-4" aria-hidden />
        <span className="text-[10px] [writing-mode:vertical-rl]">{t('panel.launch')}</span>
      </button>
    );
  }

  return (
    <div
      className="fixed inset-y-0 right-0 z-40 flex w-full max-w-[560px] flex-col border-l border-border bg-background shadow-2xl"
      role="dialog"
      aria-label={t('panel.title')}
      data-testid="mexp-dialog"
    >
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <FlaskConical className="h-4 w-4" aria-hidden />
          {t('panel.title')}
        </div>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label={t('panel.close')}
          className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
          data-testid="mexp-close"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </header>
      <ExperimentPanel sessionId={sessionId} ownerToken={ownerToken} onClose={() => setOpen(false)} />
    </div>
  );
}

export function ExperimentPanel({ sessionId, ownerToken = null }: PanelProps) {
  const t = useT('methodExperiment');
  const api = useMemo(
    () => methodExperimentApi.client(sessionId, ownerToken),
    [sessionId, ownerToken],
  );
  const layers = useHudStore((s) => s.layers);
  const refChoices = useMemo(
    () => layers
      .filter((l) => (l as { type?: string }).type === 'vector')
      .map((l) => ({
        ref: (l as { _refId?: string })._refId ?? '',
        name: (l as { name?: string }).name ?? '',
      }))
      .filter((l) => l.ref !== ''),
    [layers],
  );

  // ── 步骤 1 · 检视 ────────────────────────────────────────────────────────
  const [familyId, setFamilyId] = useState('');
  const [query, setQuery] = useState('');
  const [inspect, setInspect] = useState<InspectState>({ status: 'idle' });
  const inspectSeq = useRef(0);

  // ── 数据输入 ──────────────────────────────────────────────────────────────
  const [inputRef, setInputRef] = useState('');
  const [valueField, setValueField] = useState('');
  const [extraInputs, setExtraInputs] = useState<Record<string, string>>({});

  // ── 步骤 2 · 计划 ────────────────────────────────────────────────────────
  const [selectedCandidates, setSelectedCandidates] = useState<string[]>([]);
  const [folds, setFolds] = useState(5);
  const [maxTrials, setMaxTrials] = useState(12);
  const [plan, setPlan] = useState<MexpPlanView | null>(null);
  const [planning, setPlanning] = useState(false);
  const [planNotice, setPlanNotice] = useState('');
  const [planError, setPlanError] = useState<ApiErrorView | null>(null);
  const planSeq = useRef(0);

  // ── 步骤 3 · 执行 ────────────────────────────────────────────────────────
  const [run, setRun] = useState<MexpRunResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<ApiErrorView | null>(null);
  const runSeq = useRef(0);

  // ── 步骤 4 · 比较 ────────────────────────────────────────────────────────
  const [evaluation, setEvaluation] = useState<MexpEvaluationView | null>(null);
  const [comparing, setComparing] = useState(false);
  const [compareError, setCompareError] = useState<ApiErrorView | null>(null);
  const compareSeq = useRef(0);

  // ── 步骤 5 · 采纳 ────────────────────────────────────────────────────────
  const [selectedTrialId, setSelectedTrialId] = useState('');
  const [adoptReason, setAdoptReason] = useState('');
  const [adoption, setAdoption] = useState<MexpAdoptionView | null>(null);
  const [adopting, setAdopting] = useState(false);
  const [adoptNotice, setAdoptNotice] = useState('');
  const [adoptError, setAdoptError] = useState<ApiErrorView | null>(null);
  const adoptSeq = useRef(0);

  const familyVocab = inspect.status === 'ready' ? inspect.data.family_vocab : [];
  const budgetDefaults = inspect.status === 'ready' ? inspect.data.budget_defaults : null;
  const familyCard = useMemo(
    () => (inspect.status === 'ready'
      ? inspect.data.cards.find((c) => c.family_id === familyId) ?? null
      : null),
    [inspect, familyId],
  );

  const currentStep = !familyCard ? 0
    : !plan ? 1
    : !run || run.status === 'partial' ? 2
    : !evaluation ? 3 : 4;

  const handleInspect = useCallback(async (familyOverride?: string) => {
    const seq = ++inspectSeq.current;
    setInspect({ status: 'loading' });
    try {
      const data = await api.inspect({
        query: query.trim(),
        family_id: familyOverride ?? familyId,
      });
      if (seq !== inspectSeq.current) return;
      setInspect({ status: 'ready', data });
      const resolved = data.resolved_family ?? (familyOverride ?? familyId);
      if (resolved) setFamilyId(resolved);
      const card = data.cards.find((c) => c.family_id === resolved) ?? null;
      if (card) {
        // 默认全选候选（服务端有界：max_candidates 封顶），folds 取方法卡默认。
        setSelectedCandidates(card.candidates.map((c) => c.candidate_id));
        setFolds(card.default_folds);
      }
      setMaxTrials(data.budget_defaults.max_trials);
    } catch (err) {
      if (seq !== inspectSeq.current) return;
      const e = asMethodExperimentError(err);
      setInspect({ status: 'error', code: e.code, message: e.message });
    }
  }, [api, query, familyId]);

  const toggleCandidate = useCallback((candidateId: string) => {
    setSelectedCandidates((prev) => (prev.includes(candidateId)
      ? prev.filter((id) => id !== candidateId)
      : [...prev, candidateId]));
  }, []);

  const handlePlan = useCallback(async () => {
    if (!familyCard) { setPlanNotice(t('plan.needInspect')); return; }
    if (!inputRef.trim()) { setPlanNotice(t('plan.needRef')); return; }
    if (familyCard.requires_value_field && !valueField.trim()) {
      setPlanNotice(t('plan.needValueField'));
      return;
    }
    if (selectedCandidates.length === 0) { setPlanNotice(t('plan.needCandidate')); return; }
    setPlanNotice('');
    setPlanError(null);
    setPlanning(true);
    const seq = ++planSeq.current;
    try {
      const res = await api.plan({
        family_id: familyCard.family_id,
        input_ref: inputRef.trim(),
        value_field: valueField.trim(),
        extra_inputs: Object.fromEntries(
          Object.entries(extraInputs).filter(([, v]) => v.trim() !== ''),
        ),
        candidate_ids: selectedCandidates,
        folds,
        max_trials: maxTrials,
      });
      if (seq !== planSeq.current) return;
      setPlan(res.plan);
      // 新计划使下游证据全部失效（不跨计划复用）。
      setRun(null);
      setEvaluation(null);
      setSelectedTrialId('');
      setAdoption(null);
      setAdoptNotice('');
      setAdoptError(null);
    } catch (err) {
      if (seq !== planSeq.current) return;
      setPlan(null);
      setPlanError(asMethodExperimentError(err));
    } finally {
      if (seq === planSeq.current) setPlanning(false);
    }
  }, [api, familyCard, inputRef, valueField, extraInputs, selectedCandidates, folds, maxTrials, t]);

  const handleRun = useCallback(async () => {
    if (!plan) return;
    setRunning(true);
    setRunError(null);
    const seq = ++runSeq.current;
    try {
      const res = await api.run(plan.experiment_id, 6);
      if (seq !== runSeq.current) return;
      setRun(res);
      setEvaluation(null);
    } catch (err) {
      if (seq !== runSeq.current) return;
      setRunError(asMethodExperimentError(err));
    } finally {
      if (seq === runSeq.current) setRunning(false);
    }
  }, [api, plan]);

  const handleCompare = useCallback(async () => {
    if (!plan) return;
    setComparing(true);
    setCompareError(null);
    const seq = ++compareSeq.current;
    try {
      const res = await api.compare(plan.experiment_id);
      if (seq !== compareSeq.current) return;
      setEvaluation(res.evaluation);
      setSelectedTrialId(res.evaluation.recommendation?.trial_id ?? '');
    } catch (err) {
      if (seq !== compareSeq.current) return;
      setEvaluation(null);
      setCompareError(asMethodExperimentError(err));
    } finally {
      if (seq === compareSeq.current) setComparing(false);
    }
  }, [api, plan]);

  const handleAdopt = useCallback(async () => {
    if (!plan || !selectedTrialId) { setAdoptNotice(t('adopt.needTrial')); return; }
    setAdopting(true);
    setAdoptNotice('');
    setAdoptError(null);
    const seq = ++adoptSeq.current;
    try {
      const res = await api.adopt({
        experiment_id: plan.experiment_id,
        trial_id: selectedTrialId,
        reason: adoptReason.trim(),
      });
      if (seq !== adoptSeq.current) return;
      setAdoption(res.adoption);
      setAdoptNotice(t('adopt.success'));
    } catch (err) {
      if (seq !== adoptSeq.current) return;
      setAdoption(null);
      setAdoptError(asMethodExperimentError(err));
    } finally {
      if (seq === adoptSeq.current) setAdopting(false);
    }
  }, [api, plan, selectedTrialId, adoptReason, t]);

  return (
    <div className="flex h-full flex-col overflow-hidden text-sm" data-testid="mexp-panel">
      <div className="border-b border-border px-4 py-2">
        <p className="text-xs opacity-70">{t('panel.subtitle')}</p>
        <nav className="mt-2 flex flex-wrap gap-1 text-xs" aria-label={t('panel.title')}>
          {STEPS.map((s, i) => (
            <span
              key={s}
              aria-current={i === currentStep ? 'step' : undefined}
              className={`rounded-full px-2 py-0.5 ${
                i <= currentStep ? 'bg-primary/15 text-primary' : 'text-muted-foreground'
              }`}
            >
              {t(`steps.${s}`)}
            </span>
          ))}
        </nav>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto px-4 py-3">
        {/* 1 · 检视 */}
        <section aria-label={t('steps.inspect')} className="space-y-2">
          <label
            className="block text-xs font-medium text-muted-foreground"
            htmlFor="mexp-family"
          >
            {t('families.select')}
          </label>
          <select
            id="mexp-family"
            className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs"
            value={familyId}
            onChange={(e) => {
              setFamilyId(e.target.value);
              // 检视过后换族：直接以新族重检（服务端按族过滤方法卡）。
              if (inspect.status === 'ready' && e.target.value) {
                void handleInspect(e.target.value);
              }
            }}
            data-testid="mexp-family-select"
          >
            <option value="">{t('families.select')}</option>
            {familyVocab.map((id) => (
              <option key={id} value={id}>{familyLabel(t, id)}</option>
            ))}
          </select>
          <label
            className="block text-xs font-medium text-muted-foreground"
            htmlFor="mexp-query"
          >
            {t('families.queryLabel')}
          </label>
          <input
            id="mexp-query"
            type="text"
            className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs"
            placeholder={t('families.queryPlaceholder')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            data-testid="mexp-query-input"
            aria-label={t('families.queryLabel')}
          />
          <button
            type="button"
            onClick={() => void handleInspect()}
            disabled={inspect.status === 'loading'}
            className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground disabled:opacity-50"
            data-testid="mexp-inspect-btn"
          >
            {inspect.status === 'loading' ? t('families.inspecting') : t('families.inspectBtn')}
          </button>
          <p className="text-xs text-muted-foreground">{t('families.hint')}</p>
          {inspect.status === 'idle' && (
            <p className="text-xs text-muted-foreground" data-testid="mexp-inspect-empty">
              {t('families.empty')}
            </p>
          )}
          {inspect.status === 'error' && (
            <ErrorBox title={t('errors.inspectFailed')} error={inspect} testId="mexp-inspect-error" />
          )}
          {inspect.status === 'ready' && (
            <div className="space-y-1 text-xs text-muted-foreground" data-testid="mexp-inspect-meta">
              {inspect.data.resolved_family && (
                <p data-testid="mexp-resolved-family">
                  {t('families.resolved', { family: familyLabel(t, inspect.data.resolved_family) })}
                </p>
              )}
              {inspect.data.query_matched_families.length > 0 && (
                <p data-testid="mexp-matched-families">
                  {t('families.matched', {
                    list: inspect.data.query_matched_families
                      .map((f) => familyLabel(t, f)).join(', '),
                  })}
                </p>
              )}
            </div>
          )}
        </section>

        {/* 方法卡 */}
        {inspect.status === 'ready' && inspect.data.cards.length > 0 && (
          <section aria-label={t('cards.section')} className="space-y-2" data-testid="mexp-cards">
            <h3 className="text-xs font-medium">{t('cards.section')}</h3>
            {inspect.data.cards.map((card) => (
              <div
                key={card.family_id}
                className="rounded border border-border px-3 py-2 text-xs"
                data-testid={`mexp-card-${card.family_id}`}
              >
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="font-medium">{familyLabel(t, card.family_id)}</span>
                  <span className="text-muted-foreground">
                    {t('cards.protocolKind', { kind: card.protocol_kind })}
                  </span>
                  <span className="text-muted-foreground">
                    {t('cards.defaultScheme', { scheme: card.default_scheme })}
                  </span>
                  <span className="text-muted-foreground">
                    {t('cards.defaultFolds', { folds: String(card.default_folds) })}
                  </span>
                  <span className={card.requires_value_field ? 'text-primary' : 'text-muted-foreground'}>
                    {card.requires_value_field
                      ? t('cards.requiresValueField')
                      : t('cards.noValueField')}
                  </span>
                </div>
                <p className="mt-1 text-muted-foreground">
                  {t('cards.metrics', { list: card.default_metrics.join(', ') })}
                </p>
                {card.comparability_notes.length > 0 && (
                  <div className="mt-1 text-muted-foreground">
                    <span>{t('cards.notes')}: </span>
                    <ul className="list-disc pl-4">
                      {card.comparability_notes.map((note, i) => (
                        <li key={i}>{note}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <table className="mt-2 w-full">
                  <thead>
                    <tr className="border-b border-border text-left text-muted-foreground">
                      <th className="px-1 py-1 font-medium">{t('cards.colCandidate')}</th>
                      <th className="px-1 py-1 font-medium">{t('cards.colLabel')}</th>
                      <th className="px-1 py-1 font-medium">{t('cards.colParams')}</th>
                      <th className="px-1 py-1 font-medium">{t('cards.colFloor')}</th>
                      <th className="px-1 py-1 font-medium">{t('cards.colVariants')}</th>
                      <th className="px-1 py-1 font-medium">{t('cards.colLimitations')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {card.candidates.map((cand) => (
                      <tr
                        key={cand.candidate_id}
                        className="border-b border-border align-top last:border-0"
                        data-testid={`mexp-card-candidate-${cand.candidate_id}`}
                      >
                        <td className="px-1 py-1 font-mono">{cand.candidate_id}</td>
                        <td className="px-1 py-1">{cand.label_zh}</td>
                        <td className="px-1 py-1">
                          {Object.entries(cand.param_specs).map(([name, spec]) => (
                            <div key={name}>
                              <span className="rounded bg-muted px-1 font-mono">{name}</span>
                              <span className="text-muted-foreground"> {spec.role}</span>
                              <span className="text-muted-foreground">
                                {' '}· [{spec.grid.map(String).join(', ')}]
                              </span>
                              <span className="text-muted-foreground">
                                {' '}· {t('cards.paramDefault', { value: String(spec.default) })}
                              </span>
                              {spec.minimum !== null && spec.maximum !== null && (
                                <span className="text-muted-foreground">
                                  {' '}· {t('cards.paramRange', {
                                    min: String(spec.minimum),
                                    max: String(spec.maximum),
                                  })}
                                </span>
                              )}
                            </div>
                          ))}
                        </td>
                        <td className="px-1 py-1 tabular-nums">{cand.sample_floor}</td>
                        <td className="px-1 py-1 tabular-nums">{cand.default_variants}</td>
                        <td className="px-1 py-1 text-muted-foreground">
                          {cand.limitations.join('; ') || '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </section>
        )}
        {inspect.status === 'ready' && inspect.data.cards.length === 0 && (
          <p className="text-xs text-muted-foreground" data-testid="mexp-cards-empty">
            {t('cards.empty')}
          </p>
        )}

        {/* 数据输入 */}
        {familyCard && (
          <section aria-label={t('input.section')} className="space-y-2" data-testid="mexp-input">
            <h3 className="text-xs font-medium">{t('input.section')}</h3>
            <label
              className="block text-xs font-medium text-muted-foreground"
              htmlFor="mexp-ref"
            >
              {t('input.refLabel')}
            </label>
            {refChoices.length > 0 ? (
              <select
                id="mexp-ref"
                className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs"
                value={inputRef}
                onChange={(e) => setInputRef(e.target.value)}
                data-testid="mexp-ref-select"
              >
                <option value="">{t('input.refFromLayer')}</option>
                {refChoices.map((l) => (
                  <option key={l.ref} value={l.ref}>{l.name}</option>
                ))}
              </select>
            ) : (
              <input
                id="mexp-ref"
                type="text"
                className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs"
                placeholder={t('input.refPlaceholder')}
                value={inputRef}
                onChange={(e) => setInputRef(e.target.value)}
                data-testid="mexp-ref-input"
                aria-label={t('input.refLabel')}
              />
            )}
            <p className="text-xs text-muted-foreground">{t('input.refHint')}</p>
            {familyCard.requires_value_field && (
              <>
                <label
                  className="block text-xs font-medium text-muted-foreground"
                  htmlFor="mexp-value-field"
                >
                  {t('input.valueFieldLabel')}
                </label>
                <input
                  id="mexp-value-field"
                  type="text"
                  className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs"
                  placeholder={t('input.valueFieldPlaceholder')}
                  value={valueField}
                  onChange={(e) => setValueField(e.target.value)}
                  data-testid="mexp-value-field-input"
                  aria-label={t('input.valueFieldLabel')}
                />
                <p className="text-xs text-muted-foreground">{t('input.valueFieldHint')}</p>
              </>
            )}
            {familyCard.extra_inputs.map((role) => (
              <div key={role}>
                <label
                  className="block text-xs font-medium text-muted-foreground"
                  htmlFor={`mexp-extra-${role}`}
                >
                  {t('input.extraLabel', { role })}
                </label>
                <input
                  id={`mexp-extra-${role}`}
                  type="text"
                  className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs"
                  placeholder={t('input.extraPlaceholder')}
                  value={extraInputs[role] ?? ''}
                  onChange={(e) => setExtraInputs((prev) => ({ ...prev, [role]: e.target.value }))}
                  data-testid={`mexp-extra-${role}`}
                  aria-label={t('input.extraLabel', { role })}
                />
              </div>
            ))}
            {familyCard.extra_inputs.length > 0 && (
              <p className="text-xs text-muted-foreground">{t('input.extraHint')}</p>
            )}
          </section>
        )}

        {/* 2 · 计划预览 */}
        {familyCard && (
          <section aria-label={t('plan.section')} className="space-y-2" data-testid="mexp-plan">
            <h3 className="text-xs font-medium">{t('plan.section')}</h3>
            <p className="text-xs text-muted-foreground">
              {t('plan.candidates', { max: String(budgetDefaults?.max_candidates ?? 6) })}
            </p>
            <ul className="space-y-1">
              {familyCard.candidates.map((cand) => {
                const checked = selectedCandidates.includes(cand.candidate_id);
                return (
                  <li key={cand.candidate_id} className="flex items-center gap-2">
                    <input
                      id={`mexp-candidate-${cand.candidate_id}`}
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleCandidate(cand.candidate_id)}
                      data-testid={`mexp-candidate-check-${cand.candidate_id}`}
                      aria-label={cand.label_zh}
                    />
                    <label htmlFor={`mexp-candidate-${cand.candidate_id}`} className="text-xs">
                      <span className="font-mono">{cand.candidate_id}</span> · {cand.label_zh}
                      <span className="text-muted-foreground"> ({cand.default_variants})</span>
                    </label>
                  </li>
                );
              })}
            </ul>
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label
                  className="block text-xs font-medium text-muted-foreground"
                  htmlFor="mexp-folds"
                >
                  {t('plan.folds')}
                </label>
                <input
                  id="mexp-folds"
                  type="number"
                  min={2}
                  max={10}
                  className="w-20 rounded border border-border bg-background px-2 py-1 text-xs"
                  value={folds}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v) && v > 0) setFolds(Math.min(10, Math.max(2, Math.floor(v))));
                  }}
                  data-testid="mexp-folds-input"
                  aria-label={t('plan.folds')}
                />
              </div>
              <div>
                <label
                  className="block text-xs font-medium text-muted-foreground"
                  htmlFor="mexp-max-trials"
                >
                  {t('plan.maxTrials')}
                </label>
                <input
                  id="mexp-max-trials"
                  type="number"
                  min={1}
                  max={budgetDefaults?.max_trials_hard_cap ?? 24}
                  className="w-24 rounded border border-border bg-background px-2 py-1 text-xs"
                  value={maxTrials}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    const cap = budgetDefaults?.max_trials_hard_cap ?? 24;
                    if (Number.isFinite(v) && v > 0) setMaxTrials(Math.min(cap, Math.floor(v)));
                  }}
                  data-testid="mexp-max-trials-input"
                  aria-label={t('plan.maxTrials')}
                />
              </div>
              <button
                type="button"
                onClick={() => void handlePlan()}
                disabled={planning}
                className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground disabled:opacity-50"
                data-testid="mexp-plan-btn"
              >
                {planning ? t('plan.compiling') : t('plan.compileBtn')}
              </button>
            </div>
            {planNotice && (
              <p role="status" className="text-xs text-destructive" data-testid="mexp-plan-notice">
                {planNotice}
              </p>
            )}
            {planError && (
              <ErrorBox title={t('errors.planFailed')} error={planError} testId="mexp-plan-error" />
            )}
            {plan && (
              <div
                className="space-y-2 rounded border border-border bg-muted/40 px-3 py-2 text-xs"
                data-testid="mexp-plan-summary"
              >
                <p data-testid="mexp-plan-protocol">
                  {t('plan.protocolLine', {
                    kind: plan.protocol.kind,
                    scheme: plan.protocol.scheme,
                    folds: String(plan.protocol.folds),
                    metrics: plan.protocol.metrics.join(', '),
                  })}
                </p>
                <p data-testid="mexp-plan-budget">
                  {t('plan.budgetLine', {
                    max: String(plan.budget.max_trials),
                    sec: String(plan.budget.wall_hint_seconds_per_trial),
                  })}
                </p>
                <p data-testid="mexp-plan-fingerprint">
                  {t('plan.fingerprint', { fp: plan.fingerprint })}
                </p>
                <div data-testid="mexp-plan-preflight">
                  <p className="font-medium">{t('plan.preflight')}</p>
                  {plan.preflight_findings.length === 0 ? (
                    <p className="text-muted-foreground">{t('plan.preflightEmpty')}</p>
                  ) : (
                    <ul className="list-disc pl-4">
                      {plan.preflight_findings.map((f, i) => (
                        <li key={i} data-testid={`mexp-preflight-${i}`}>{f}</li>
                      ))}
                    </ul>
                  )}
                </div>
                <div data-testid="mexp-plan-trials">
                  <p className="font-medium">{t('plan.trialsTitle', { n: String(plan.trials.length) })}</p>
                  <table className="mt-1 w-full">
                    <thead>
                      <tr className="border-b border-border text-left text-muted-foreground">
                        <th className="px-1 py-0.5 font-medium">{t('plan.colTrial')}</th>
                        <th className="px-1 py-0.5 font-medium">{t('plan.colCandidate')}</th>
                        <th className="px-1 py-0.5 font-medium">{t('plan.colVariant')}</th>
                        <th className="px-1 py-0.5 font-medium">{t('plan.colParams')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {plan.trials.map((tr) => (
                        <tr
                          key={tr.trial_id}
                          className="border-b border-border last:border-0"
                          data-testid={`mexp-plan-trial-${tr.trial_id}`}
                        >
                          <td className="px-1 py-0.5 font-mono">{tr.trial_id}</td>
                          <td className="px-1 py-0.5">{tr.candidate_id}</td>
                          <td className="px-1 py-0.5 tabular-nums">{tr.variant_index}</td>
                          <td className="px-1 py-0.5 font-mono">{formatParams(tr.params)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </section>
        )}

        {/* 3 · 执行 */}
        {plan && (
          <section aria-label={t('run.section')} className="space-y-2" data-testid="mexp-run">
            <h3 className="text-xs font-medium">{t('run.section')}</h3>
            <div className="flex flex-wrap gap-2">
              {run?.status === 'partial' ? (
                <button
                  type="button"
                  onClick={() => void handleRun()}
                  disabled={running}
                  className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground disabled:opacity-50"
                  data-testid="mexp-run-continue-btn"
                >
                  {running
                    ? t('run.running')
                    : t('run.continueBtn', { n: String(run?.counts?.pending ?? 0) })}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => void handleRun()}
                  disabled={running}
                  className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground disabled:opacity-50"
                  data-testid="mexp-run-btn"
                >
                  {running ? t('run.running') : t('run.startBtn')}
                </button>
              )}
            </div>
            <p className="text-xs text-muted-foreground">{t('run.windowHint')}</p>
            {!run && (
              <p className="text-xs text-muted-foreground" data-testid="mexp-run-empty">
                {t('run.notStarted')}
              </p>
            )}
            {runError && (
              <ErrorBox title={t('errors.runFailed')} error={runError} testId="mexp-run-error" />
            )}
            {run && (
              <div className="space-y-1 rounded border border-border px-3 py-2 text-xs" data-testid="mexp-run-status">
                <p data-testid="mexp-run-status-line">
                  {t('run.statusLine', { status: run.status })}
                </p>
                <p data-testid="mexp-run-wall">
                  {t('run.wallMs', { ms: String(run.total_wall_ms) })}
                </p>
                <div data-testid="mexp-run-counts">
                  <p className="font-medium">{t('run.countsTitle')}</p>
                  <ul className="flex flex-wrap gap-x-3">
                    {Object.entries(run.counts).map(([k, v]) => (
                      <li key={k} data-testid={`mexp-run-count-${k}`}>
                        {k}: <span className="tabular-nums">{v}</span>
                      </li>
                    ))}
                  </ul>
                </div>
                <div data-testid="mexp-run-trials">
                  <p className="font-medium">{t('run.trialsTitle')}</p>
                  <table className="mt-1 w-full">
                    <thead>
                      <tr className="border-b border-border text-left text-muted-foreground">
                        <th className="px-1 py-0.5 font-medium">{t('run.colTrial')}</th>
                        <th className="px-1 py-0.5 font-medium">{t('run.colCandidate')}</th>
                        <th className="px-1 py-0.5 font-medium">{t('run.colState')}</th>
                        <th className="px-1 py-0.5 font-medium">{t('run.colReason')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {run.trials.map((tr) => (
                        <tr
                          key={tr.trial_id}
                          className="border-b border-border last:border-0"
                          data-testid={`mexp-run-trial-${tr.trial_id}`}
                        >
                          <td className="px-1 py-0.5 font-mono">{tr.trial_id}</td>
                          <td className="px-1 py-0.5">{tr.candidate_id}</td>
                          <td className={`px-1 py-0.5 ${
                            tr.state === 'completed' ? 'text-primary' : tr.state === 'failed' ? 'text-destructive' : ''
                          }`}>{tr.state}</td>
                          <td className="px-1 py-0.5 font-mono">{tr.reason_code || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {run.trials.some((tr) => tr.state === 'failed' || tr.state === 'cancelled') && (
                  <p className="text-muted-foreground">{t('run.failedHint')}</p>
                )}
              </div>
            )}
          </section>
        )}

        {/* 4 · 比较 */}
        {plan && (
          <section aria-label={t('compare.section')} className="space-y-2" data-testid="mexp-compare">
            <h3 className="text-xs font-medium">{t('compare.section')}</h3>
            <button
              type="button"
              onClick={() => void handleCompare()}
              disabled={comparing}
              className="rounded border border-border px-2 py-1 text-xs hover:bg-accent disabled:opacity-50"
              data-testid="mexp-compare-btn"
            >
              {comparing ? t('compare.loading') : t('compare.btn')}
            </button>
            {compareError && (
              <ErrorBox title={t('errors.compareFailed')} error={compareError} testId="mexp-compare-error" />
            )}
            {evaluation && (
              <div className="space-y-3">
                {evaluation.groups.length > 0 && (
                  <div className="space-y-2" data-testid="mexp-compare-groups">
                    <p className="text-xs font-medium">{t('compare.groupsTitle')}</p>
                    {evaluation.groups.map((group) => (
                      <div
                        key={group.metric_id}
                        className="rounded border border-border px-2 py-2 text-xs"
                        data-testid={`mexp-compare-group-${group.metric_id}`}
                      >
                        <div className="flex flex-wrap gap-x-3 gap-y-1">
                          <span className="font-mono font-medium">{group.metric_id}</span>
                          <span data-testid={`mexp-direction-${group.metric_id}`}>
                            {directionLabel(t, group.direction)}
                          </span>
                          <span className="text-muted-foreground">{unitLabel(t, group.unit_kind)}</span>
                          <span className="text-muted-foreground">
                            {t('compare.nEvaluated', {
                              n: String(group.n_evaluated),
                              m: String(group.n_excluded),
                            })}
                          </span>
                        </div>
                        <table className="mt-1 w-full">
                          <thead>
                            <tr className="border-b border-border text-left text-muted-foreground">
                              <th className="px-1 py-0.5">
                                <span className="sr-only">{t('compare.selectHint')}</span>
                              </th>
                              <th className="px-1 py-0.5 font-medium">{t('compare.colTrial')}</th>
                              <th className="px-1 py-0.5 font-medium">{t('compare.colCandidate')}</th>
                              <th className="px-1 py-0.5 font-medium">{t('compare.colParams')}</th>
                              <th className="px-1 py-0.5 font-medium">{t('compare.colValue')}</th>
                            </tr>
                          </thead>
                          <tbody>
                            {group.rows.map((row) => {
                              const selected = selectedTrialId === row.trial_id;
                              return (
                                <tr
                                  key={row.trial_id}
                                  className={`border-b border-border last:border-0 ${selected ? 'bg-primary/10' : ''}`}
                                  data-testid={`mexp-compare-row-${row.trial_id}`}
                                >
                                  <td className="px-1 py-0.5">
                                    <button
                                      type="button"
                                      aria-pressed={selected}
                                      aria-label={t('compare.selectTrial', { trial: row.trial_id })}
                                      onClick={() => setSelectedTrialId(row.trial_id)}
                                      className="rounded border border-border px-1.5 hover:bg-accent"
                                      data-testid={`mexp-trial-select-${row.trial_id}`}
                                    >
                                      {selected ? '✓' : ''}
                                    </button>
                                  </td>
                                  <td className="px-1 py-0.5 font-mono">{row.trial_id}</td>
                                  <td className="px-1 py-0.5">{row.candidate_id}</td>
                                  <td className="px-1 py-0.5 font-mono">{formatParams(row.params)}</td>
                                  <td className="px-1 py-0.5 tabular-nums">{formatValue(row.value)}</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                        {group.ties.length > 0 && (
                          <p className="mt-1 text-muted-foreground" data-testid={`mexp-ties-${group.metric_id}`}>
                            {t('compare.ties', { list: group.ties.join(', ') })}
                          </p>
                        )}
                        {group.exclusion_reasons.length > 0 && (
                          <p className="mt-1 text-muted-foreground">
                            {t('compare.excluded', { list: group.exclusion_reasons.join(', ') })}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                <div className="rounded border border-border px-2 py-1 text-xs" data-testid="mexp-incomparable">
                  <p className="font-medium">{t('compare.incomparableTitle')}</p>
                  {evaluation.incomparable.length === 0 ? (
                    <p className="text-muted-foreground">{t('compare.incomparableEmpty')}</p>
                  ) : (
                    <ul className="list-disc pl-4 text-muted-foreground">
                      {evaluation.incomparable.map((item, i) => (
                        <li key={i}>{item}</li>
                      ))}
                    </ul>
                  )}
                </div>
                {evaluation.recommendation && (
                  <div className="rounded border border-border px-3 py-2 text-xs" data-testid="mexp-recommendation">
                    <p className="font-medium">{t('recommendation.title')}</p>
                    {evaluation.recommendation.status === 'abstained' ? (
                      <div className="mt-1 space-y-1" data-testid="mexp-recommendation-abstain">
                        <p className="font-medium text-destructive">{t('recommendation.abstained')}</p>
                        {evaluation.recommendation.abstain_reason && (
                          <p className="text-muted-foreground" data-testid="mexp-abstain-reason">
                            {t('recommendation.abstainReason', {
                              reason: evaluation.recommendation.abstain_reason,
                            })}
                          </p>
                        )}
                      </div>
                    ) : (
                      <div className="mt-1 space-y-1" data-testid="mexp-recommendation-hit">
                        <p className="font-medium text-primary" data-testid="mexp-recommendation-candidate">
                          {t('recommendation.recommended', { candidate: evaluation.recommendation.candidate_id ?? '' })}
                          {evaluation.recommendation.trial_id
                            ? ` · ${t('recommendation.recommendedTrial', { trial: evaluation.recommendation.trial_id })}`
                            : ''}
                        </p>
                        <p data-testid="mexp-recommendation-evidence">
                          {t('recommendation.evidence')}: {
                            Object.entries(evaluation.recommendation.metric_evidence)
                              .map(([k, v]) => `${k}=${formatValue(v)}`).join(', ') || '—'
                          }
                        </p>
                        <p data-testid="mexp-recommendation-sensitivity">
                          {t('recommendation.sensitivity')}: {
                            Object.entries(evaluation.recommendation.sensitivity)
                              .map(([k, v]) => `${k}=${formatValue(v)}`).join(', ') || '—'
                          }
                        </p>
                        {evaluation.recommendation.alternatives.length > 0 && (
                          <p data-testid="mexp-recommendation-alternatives">
                            {t('recommendation.alternatives')}: {
                              evaluation.recommendation.alternatives
                                .map((a) => JSON.stringify(a)).join(' | ')
                            }
                          </p>
                        )}
                      </div>
                    )}
                    {evaluation.recommendation.caveats.length > 0 && (
                      <div className="mt-1">
                        <p className="text-muted-foreground">{t('recommendation.caveats')}:</p>
                        <ul className="list-disc pl-4 text-muted-foreground">
                          {evaluation.recommendation.caveats.map((c, i) => (
                            <li key={i}>{c}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {evaluation.recommendation.pareto_front.length > 0 && (
                      <p className="mt-1 text-muted-foreground" data-testid="mexp-pareto-front">
                        {t('recommendation.pareto', { list: evaluation.recommendation.pareto_front.join(', ') })}
                      </p>
                    )}
                  </div>
                )}
                {!evaluation.recommendation && (
                  <p className="text-xs text-muted-foreground" data-testid="mexp-recommendation-none">
                    {t('recommendation.none')}
                  </p>
                )}
              </div>
            )}
          </section>
        )}

        {/* 5 · 采纳 */}
        {evaluation && (
          <section aria-label={t('adopt.section')} className="space-y-2" data-testid="mexp-adopt">
            <h3 className="text-xs font-medium">{t('adopt.section')}</h3>
            <p className="text-xs text-muted-foreground" data-testid="mexp-adopt-selection">
              {selectedTrialId
                ? t('compare.selected', { trial: selectedTrialId })
                : t('compare.selectHint')}
            </p>
            <label
              className="block text-xs font-medium text-muted-foreground"
              htmlFor="mexp-adopt-reason"
            >
              {t('adopt.reasonLabel')}
            </label>
            <input
              id="mexp-adopt-reason"
              type="text"
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs"
              placeholder={t('adopt.reasonPlaceholder')}
              value={adoptReason}
              onChange={(e) => setAdoptReason(e.target.value)}
              data-testid="mexp-adopt-reason"
              aria-label={t('adopt.reasonLabel')}
            />
            <button
              type="button"
              onClick={() => void handleAdopt()}
              disabled={adopting || !selectedTrialId}
              className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground disabled:opacity-50"
              data-testid="mexp-adopt-btn"
            >
              {adopting ? t('adopt.adopting') : t('adopt.btn')}
            </button>
            {!selectedTrialId && (
              <p className="text-xs text-muted-foreground">{t('adopt.needTrial')}</p>
            )}
            <p className="text-xs text-muted-foreground">{t('adopt.notice')}</p>
            {adoptNotice && (
              <p
                role="status"
                className="rounded border border-primary/40 bg-primary/5 px-2 py-1 text-xs text-primary"
                data-testid="mexp-adopt-notice"
              >
                {adoptNotice}
              </p>
            )}
            {adoptError && (
              <ErrorBox title={t('errors.adoptFailed')} error={adoptError} testId="mexp-adopt-error" />
            )}
            {adoption && (
              <div
                className="space-y-1 rounded border border-border bg-muted/40 px-3 py-2 text-xs"
                data-testid="mexp-adoption-receipt"
              >
                <p className="font-medium">{t('adopt.receiptTitle')}</p>
                <p data-testid="mexp-adoption-line">
                  {t('adopt.receiptLine', { candidate: adoption.candidate_id, trial: adoption.trial_id })}
                </p>
                <p data-testid="mexp-adoption-fingerprint">
                  {t('adopt.receiptFingerprint', { fp: adoption.plan_fingerprint })}
                </p>
                <p data-testid="mexp-adoption-metrics">
                  {t('adopt.metricSnapshot')}: {
                    Object.entries(adoption.metric_snapshot)
                      .map(([k, v]) => `${k}=${formatValue(v)}`).join(', ') || '—'
                  }
                </p>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

/** 族 id 显示名：已知族走 i18n，未知族原样透传（不杜撰翻译）。 */
function familyLabel(t: Translate, familyId: string): string {
  const key = FAMILY_LABEL_KEYS[familyId];
  return key ? t(key) : familyId;
}

/** 比较方向文案：只用服务端返回的 direction，已知值走 i18n，未知值原样透传。 */
function directionLabel(t: Translate, direction: string): string {
  if (direction === 'lower_better') return t('direction.lower_better');
  if (direction === 'higher_better') return t('direction.higher_better');
  return direction;
}

/** 量纲文案：已知 unit_kind 走 i18n，未知值原样透传。 */
function unitLabel(t: Translate, unitKind: string): string {
  if (unitKind === 'value_unit') return t('unitKind.value_unit');
  if (unitKind === 'ratio') return t('unitKind.ratio');
  if (unitKind === 'minutes') return t('unitKind.minutes');
  return unitKind;
}
