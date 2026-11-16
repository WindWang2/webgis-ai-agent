'use client';

/**
 * G47 Dorling circular-area cartogram authoring panel.
 *
 * Shares the backend `cartogram_authoring.service` with the Pi tools:
 * inspect -> plan (no mutation) -> run (durable job, cancellable) ->
 * status/layout -> apply (single CAS) -> export.
 *
 * All business decisions stay on the server: this panel only offers input
 * convenience, per-field error targeting, real diagram rendering, and
 * selection linkage with the original map (same `feature_id`, never reverse
 * lookup through a circle centre).
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useToastStore } from '@/components/ui/toast';
import { DorlingDiagram } from '@/components/cartogram/dorling-diagram';
import { useT } from '@/lib/i18n/useT';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import {
  asG47Error, applyDorlingLayout, cancelDorlingJob, dorlingExportUrl,
  dorlingJobStatus, fetchDorlingLayout, inspectDorlingLayer, planDorling, runDorling,
} from '@/lib/cartogram/api';
import type { G47ApiContext } from '@/lib/cartogram/api';
import {
  DEFAULT_PLAN, type CartogramLayout, type DorlingPlan,
  type InspectReceipt, type JobStatusReceipt, type PlanReceipt, type QuantityMeaning,
} from '@/lib/cartogram/contracts';
import {
  getSelection, publishSelection, subscribeSelection,
} from '@/lib/selection/selection-store';

interface Props {
  layerId: string;
}

const inputCls =
  'w-full rounded-md border border-edge-subtle bg-surface px-2 py-1 text-body text-ink '
  + 'focus:outline-none focus:ring-2 focus:ring-status-accent';
const labelCls = 'block text-caption text-ink-muted mb-1';
const btnCls =
  'rounded-md border border-edge-subtle px-2.5 py-1.5 text-body text-ink '
  + 'hover:bg-surface-hover focus:outline-none focus:ring-2 focus:ring-status-accent '
  + 'disabled:opacity-50 disabled:cursor-not-allowed';
const primaryBtnCls =
  'rounded-md border border-status-accent bg-status-accent/10 px-2.5 py-1.5 text-body '
  + 'text-status-accent hover:bg-status-accent/20 focus:outline-none '
  + 'focus:ring-2 focus:ring-status-accent disabled:opacity-50 disabled:cursor-not-allowed';

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'stale']);
const POLL_MS = 1200;

function pct(value: unknown): string {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(2)}%` : '—';
}

export function DorlingCartogramPanel({ layerId }: Props) {
  const t = useT('cartogram.panel');
  const tToast = useT('cartogram.toast');
  const toast = useToastStore((s) => s.addToast);
  const cursor = getMapSpecSessionCursor();
  const ctx = useMemo((): G47ApiContext | null => (
    cursor.sessionId
      ? { sessionId: cursor.sessionId, ownerToken: cursor.ownerToken }
      : null
  ), [cursor.sessionId, cursor.ownerToken]);
  const [inspect, setInspect] = useState<InspectReceipt | null>(null);
  const [field, setField] = useState('');
  const [unit, setUnit] = useState('');
  const [meaning, setMeaning] = useState<QuantityMeaning>('count');
  const [crs, setCrs] = useState('EPSG:3857');
  const [iterations, setIterations] = useState(400);
  const [planReceipt, setPlanReceipt] = useState<PlanReceipt | null>(null);
  const [job, setJob] = useState<JobStatusReceipt | null>(null);
  const [layout, setLayout] = useState<CartogramLayout | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // 诊断草稿必须由用户显式勾选才能应用 —— 不能因为"未达标"就自动放行，
  // 否则诚实门形同虚设。
  const [allowDiagnostic, setAllowDiagnostic] = useState(false);
  const [busy, setBusy] = useState<'inspect' | 'plan' | 'run' | 'apply' | null>(null);
  const [error, setError] = useState<{ code: string; message: string; field?: string; hint?: string } | null>(null);
  const epoch = useRef(0);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const selection = useSyncExternalStore(subscribeSelection, getSelection, () => null);
  const mapSelectedId = useMemo(() => {
    if (!selection || selection.layer_id !== layerId) return null;
    const id = selection.feature_id ?? selection.selected_ids?.[0];
    return id === undefined || id === null ? null : String(id);
  }, [selection, layerId]);

  const activeId = selectedId ?? mapSelectedId;

  useEffect(() => {
    if (!ctx) return;
    const myEpoch = ++epoch.current;
    setBusy('inspect');
    inspectDorlingLayer(ctx, layerId)
      .then((receipt) => {
        if (epoch.current !== myEpoch) return;
        setInspect(receipt);
        const first = receipt.fields.find((f) => f.usable_as_quantity) ?? receipt.fields[0];
        if (first && !field) setField(first.name);
      })
      .catch((err) => {
        if (epoch.current !== myEpoch) return;
        const e = asG47Error(err);
        setError({ code: e.code, message: e.message, field: e.field, hint: e.correction_hint });
      })
      .finally(() => {
        if (epoch.current === myEpoch) setBusy(null);
      });
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx, layerId]);

  const buildPlan = useCallback((): DorlingPlan => ({
    ...DEFAULT_PLAN,
    quantity: { field, unit, meaning, aggregation: 'sum' },
    projection: { crs },
    solver: { max_iterations: iterations },
    source_anchor: inspect?.source_anchor ?? null,
  }), [field, unit, meaning, crs, iterations, inspect]);

  const onPlan = useCallback(async () => {
    if (!ctx) return;
    setError(null);
    setBusy('plan');
    setPlanReceipt(null);
    try {
      const receipt = await planDorling(ctx, layerId, buildPlan());
      setPlanReceipt(receipt);
    } catch (err) {
      const e = asG47Error(err);
      setError({ code: e.code, message: e.message, field: e.field, hint: e.correction_hint });
    } finally {
      setBusy(null);
    }
  }, [buildPlan, ctx, layerId]);

  const loadLayout = useCallback(async (layoutRef: string) => {
    if (!ctx) return;
    try {
      const full = await fetchDorlingLayout(ctx, layoutRef);
      setLayout(full);
      // 每份新布局都要重新征得授权，避免旧勾选顺延到另一份草稿。
      setAllowDiagnostic(false);
    } catch (err) {
      const e = asG47Error(err);
      toast(`${e.code}: ${e.message}`, 'error');
    }
  }, [ctx, toast]);

  const poll = useCallback((jobId: number, myEpoch: number) => {
    const tick = async () => {
      if (!ctx) return;
      try {
        const next = await dorlingJobStatus(ctx, jobId);
        if (epoch.current !== myEpoch) return;
        setJob(next);
        if (TERMINAL.has(next.status)) {
          const ref = next.result_ref
            ?? (next.result_summary?.layout_ref as string | undefined)
            ?? null;
          if (next.status === 'completed' && ref) await loadLayout(ref);
          return;
        }
      } catch {
        // A failed poll does not change the job fact; the next tick retries.
      }
      if (epoch.current === myEpoch) pollTimer.current = setTimeout(tick, POLL_MS);
    };
    pollTimer.current = setTimeout(tick, POLL_MS);
  }, [ctx, loadLayout]);

  const onRun = useCallback(async () => {
    if (!ctx) return;
    setError(null);
    setBusy('run');
    setLayout(null);
    const myEpoch = ++epoch.current;
    try {
      const receipt = await runDorling(ctx, layerId, buildPlan());
      if (epoch.current !== myEpoch) return;
      setJob({ job_id: receipt.job_id, status: receipt.status });
      poll(receipt.job_id, myEpoch);
    } catch (err) {
      const e = asG47Error(err);
      setError({ code: e.code, message: e.message, field: e.field, hint: e.correction_hint });
    } finally {
      setBusy(null);
    }
  }, [buildPlan, ctx, layerId, poll]);

  const onCancel = useCallback(async () => {
    if (!job || !ctx) return;
    try {
      await cancelDorlingJob(ctx, job.job_id);
      toast(tToast('cancelRequested'), 'info');
    } catch (err) {
      const e = asG47Error(err);
      toast(`${e.code}: ${e.message}`, 'error');
    }
  }, [ctx, job, toast, tToast]);

  const onApply = useCallback(async () => {
    if (!layout || !ctx) return;
    setError(null);
    setBusy('apply');
    try {
      const receipt = await applyDorlingLayout(ctx, layerId, {
        layoutRef: layout.layout_ref ?? '',
        expectedRevision: cursor.revision ?? inspect?.revision ?? 0,
        allowDiagnostic,
        clientMutationId: `${layout.candidate_digest}:${cursor.revision ?? 0}`,
      });
      toast(
        receipt.diagnostic ? tToast('appliedDiagnostic') : tToast('applied'),
        receipt.diagnostic ? 'info' : 'success',
      );
      const refreshed = await inspectDorlingLayer(ctx, layerId);
      setInspect(refreshed);
    } catch (err) {
      const e = asG47Error(err);
      setError({ code: e.code, message: e.message, field: e.field, hint: e.correction_hint });
    } finally {
      setBusy(null);
    }
  }, [allowDiagnostic, ctx, cursor.revision, inspect, layerId, layout, toast, tToast]);

  const onSelect = useCallback((featureId: string | null) => {
    setSelectedId(featureId);
    if (featureId) {
      publishSelection('select', {
        source: 'chart', layer_id: layerId, feature_id: featureId,
        selected_ids: [featureId], artifact_ref: layout?.layout_ref,
      });
    } else {
      publishSelection('clear_selection', { source: 'chart', layer_id: layerId });
    }
  }, [layerId, layout?.layout_ref]);

  const metrics = layout?.metrics ?? {};
  const overlap = (metrics.overlap ?? {}) as Record<string, unknown>;
  const area = (metrics.area ?? {}) as Record<string, unknown>;
  const adjacency = (metrics.adjacency ?? {}) as Record<string, unknown>;
  const numericFields = (inspect?.fields ?? []).filter((f) => f.usable_as_quantity);

  return (
    <div className="space-y-3" data-g47-panel="root">
      {error && (
        <div
          role="alert"
          data-g47-error={error.code}
          className="rounded-md border border-red-300 bg-red-50 px-2 py-1.5 text-caption text-red-700"
        >
          <strong>{error.code}</strong>: {error.message}
          {error.field ? ` ${t('errorField', { field: error.field })}` : ''}
          {error.hint ? <div className="text-red-600/80">{error.hint}</div> : null}
        </div>
      )}

      {inspect && (
        <p className="text-caption text-ink-muted" data-g47-source="1">
          {t('source', {
            ref: inspect.source.base_ref,
            revision: inspect.source.content_revision,
          })}
          {' · '}
          {t('sourceFeatures', { count: inspect.source.feature_count })}
          {' · '}
          {t('sourceAlgorithm', { version: inspect.algorithm_version ?? 'dorling.v1' })}
        </p>
      )}

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className={labelCls} htmlFor="g47-field">{t('field')}</label>
          <select
            id="g47-field"
            className={inputCls}
            value={field}
            onChange={(e) => setField(e.target.value)}
          >
            <option value="">{t('fieldPlaceholder')}</option>
            {numericFields.map((f) => (
              <option key={f.name} value={f.name}>
                {t('fieldOption', {
                  name: f.name,
                  positive: f.positive_count ?? 0,
                  zero: f.zero_count ?? 0,
                  missing: f.missing_count ?? 0,
                })}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={labelCls} htmlFor="g47-unit">{t('unit')}</label>
          <input
            id="g47-unit"
            className={inputCls}
            value={unit}
            placeholder={t('unitPlaceholder')}
            onChange={(e) => setUnit(e.target.value)}
          />
        </div>
        <div>
          <label className={labelCls} htmlFor="g47-meaning">{t('meaning')}</label>
          <select
            id="g47-meaning"
            className={inputCls}
            value={meaning}
            onChange={(e) => setMeaning(e.target.value as QuantityMeaning)}
          >
            <option value="count">{t('meaningCount')}</option>
            <option value="amount">{t('meaningAmount')}</option>
          </select>
        </div>
        <div>
          <label className={labelCls} htmlFor="g47-crs">{t('crs')}</label>
          <input
            id="g47-crs"
            className={inputCls}
            value={crs}
            onChange={(e) => setCrs(e.target.value)}
          />
        </div>
        <div>
          <label className={labelCls} htmlFor="g47-iter">{t('iterations')}</label>
          <input
            id="g47-iter"
            type="number"
            min={1}
            max={4000}
            className={inputCls}
            value={iterations}
            onChange={(e) => setIterations(Number(e.target.value) || 400)}
          />
        </div>
      </div>

      <p className="text-caption text-ink-muted">{t('disclosure')}</p>

      <div className="flex flex-wrap gap-2">
        <button type="button" className={btnCls} disabled={!field || !unit || busy !== null}
          onClick={onPlan}>
          {t('plan')}
        </button>
        <button type="button" className={primaryBtnCls} disabled={!field || !unit || busy !== null}
          onClick={onRun}>
          {t('run')}
        </button>
        {job && !TERMINAL.has(job.status) && (
          <button type="button" className={btnCls} onClick={onCancel}>{t('cancel')}</button>
        )}
        {layout && (
          <button type="button" className={primaryBtnCls}
            disabled={busy !== null || (!layout.publication_ready && !allowDiagnostic)}
            onClick={onApply}>
            {layout.publication_ready ? t('apply') : t('applyDiagnostic')}
          </button>
        )}
      </div>

      {planReceipt && (
        <div className="rounded-md border border-edge-subtle bg-surface p-2 text-caption text-ink"
          data-g47-plan="1">
          <div data-g47-plan-summary="1">
            {t('planSummary', {
              circles: planReceipt.units_for_layout.with_positive_value,
              total: planReceipt.units_for_layout.total,
              zero: planReceipt.units_for_layout.zero,
              missing: planReceipt.units_for_layout.missing,
              edges: planReceipt.geometry.adjacency_edges,
              totalValue: planReceipt.quantity.total,
              unit: planReceipt.quantity.unit,
            })}
          </div>
          <div className="text-ink-muted" data-g47-plan-coefficient="1">
            {t('planCoefficient', {
              k: planReceipt.area_scale.coefficient.toExponential(3),
              maxRadius: planReceipt.area_scale.max_radius.toFixed(2),
            })}
          </div>
        </div>
      )}

      {job && (
        <div className="text-caption text-ink" data-g47-job={String(job.job_id)}>
          {t('jobLine', { id: job.job_id, status: job.status })}
          {job.message ? ` · ${job.message}` : ''}
          {job.error ? ` · ${job.error}` : ''}
        </div>
      )}

      {layout && (
        <>
          <div className="grid grid-cols-2 gap-2 text-caption text-ink" data-g47-metrics="1">
            <div>{t('metricArea', { value: pct(area.max_rel_error) })}</div>
            <div>{t('metricOverlap', { value: pct(overlap.max_pair_overlap_ratio) })}</div>
            <div>{t('metricPairs', { value: String(overlap.pairs_over_threshold ?? 0) })}</div>
            <div>
              {t('metricAdjacency', {
                retained: String(adjacency.retained ?? 0),
                total: String(adjacency.original_edges ?? 0),
              })}
            </div>
          </div>
          {!layout.publication_ready && (
            <div className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-caption text-amber-800"
              data-g47-not-ready="1">
              <p>{t('notReady', { status: layout.status })}</p>
              <label className="mt-1 flex items-start gap-1.5">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={allowDiagnostic}
                  onChange={(e) => setAllowDiagnostic(e.target.checked)}
                  data-g47-allow-diagnostic="1"
                />
                <span>{t('diagnosticOptIn')}</span>
              </label>
            </div>
          )}
          <DorlingDiagram
            layout={layout}
            selectedId={activeId}
            onSelect={onSelect}
          />
          <div className="flex flex-wrap gap-2">
            {(['svg', 'png', 'pdf'] as const).map((fmt) => (
              <a
                key={fmt}
                className={btnCls}
                href={ctx && layout.layout_ref
                  ? dorlingExportUrl(ctx, layout.layout_ref, fmt)
                  : undefined}
                target="_blank"
                rel="noreferrer"
                data-g47-export={fmt}
              >
                {t('download', { format: fmt.toUpperCase() })}
              </a>
            ))}
          </div>
          <details className="text-caption text-ink-muted">
            <summary>{t('disclosures', { count: (layout.disclosures ?? []).length })}</summary>
            <ul className="mt-1 list-disc pl-5 space-y-0.5">
              {(layout.disclosures ?? []).map((d) => <li key={d}>{d}</li>)}
            </ul>
          </details>
        </>
      )}
    </div>
  );
}
