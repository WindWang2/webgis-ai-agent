'use client';

/**
 * ModelOpsModelDetail — 模型详情（inspect + lineage，经 executeToolDirect）。
 *
 * 分区与诚实性：
 * - 基本信息 / 任务与能力：descriptor + provider_capabilities 原样；
 * - 地理配准要求：spatial（分辨率区间 m/px、CRS 要求、重投影许可）；
 * - 确定性语义：random_seed_policy 原文（无扁平 verdict 字段，不造「验收通过」）；
 * - 溯源：descriptor.provenance 自由 dict 原文 + #1212 固定说明；
 * - 版本事件：modelops_model_history。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { describeApiError } from '@/lib/api/transport';
import { useT } from '@/lib/i18n/useT';
import type { TranslateFn } from '@/lib/i18n/translator';
import {
  fetchModelopsHistory,
  inspectModelopsModel,
  type ModelOpsDescriptor,
  type ModelOpsHistoryResult,
  type ModelOpsInspectResult,
} from '@/lib/api/modelops';

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-meta font-semibold uppercase tracking-wider text-ink-muted">
        {label}
      </span>
      {children}
    </div>
  );
}

function spatialLines(d: ModelOpsDescriptor, t: TranslateFn): Array<{ k: string; v: string }> {
  const s = d.spatial ?? {};
  const lines: Array<{ k: string; v: string }> = [];
  const rr = s.resolution_range;
  if (rr && (rr.min_m_per_px != null || rr.max_m_per_px != null)) {
    lines.push({
      k: t('spatial.resolutionRange'),
      v: `${rr.min_m_per_px ?? '?'} – ${rr.max_m_per_px ?? '?'} m/px`,
    });
  }
  if (s.crs_requirements != null) {
    lines.push({ k: t('spatial.crs'), v: JSON.stringify(s.crs_requirements) });
  }
  if (s.allow_reproject != null) {
    lines.push({
      k: t('spatial.allowReproject'),
      v: s.allow_reproject ? t('spatial.yes') : t('spatial.no'),
    });
  }
  if (s.chip_size != null) lines.push({ k: t('spatial.chipSize'), v: `${s.chip_size}px` });
  if (s.min_valid_data_ratio != null) {
    lines.push({ k: t('spatial.minValidRatio'), v: String(s.min_valid_data_ratio) });
  }
  return lines;
}

export function ModelOpsModelDetail({
  modelId,
  onBack,
}: {
  modelId: string;
  onBack: () => void;
}) {
  const [inspect, setInspect] = useState<ModelOpsInspectResult | null>(null);
  const [inspectError, setInspectError] = useState<string | null>(null);
  const [history, setHistory] = useState<ModelOpsHistoryResult | null>(null);
  const seqRef = useRef(0);
  const t = useT('modelops');

  const load = useCallback(() => {
    const seq = ++seqRef.current;
    setInspectError(null);
    setInspect(null);
    setHistory(null);
    inspectModelopsModel(modelId)
      .then((res) => {
        if (seq !== seqRef.current) return;
        setInspect(res);
      })
      .catch((err: unknown) => {
        if (seq !== seqRef.current) return;
        setInspectError(describeApiError(err, t('loadFailed')));
      });
    fetchModelopsHistory(modelId)
      .then((res) => {
        if (seq !== seqRef.current) return;
        setHistory(res);
      })
      .catch(() => {
        /* lineage 缺失不阻塞详情 —— 但保留空态文案 */
      });
  }, [modelId, t]);

  useEffect(() => {
    load();
  }, [load]);

  const d: ModelOpsDescriptor | undefined = inspect?.descriptor;
  const events = history
    ? Object.entries(history.versions).flatMap(([v, entry]) =>
        (entry.events ?? []).map((e) => ({ ...e, version: v })),
      )
    : [];

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex w-fit items-center gap-1 rounded-sm px-1 py-0.5 text-meta font-medium text-ink-secondary transition-colors hover:text-ink"
      >
        <ArrowLeft size={12} aria-hidden />
        {t('back')}
      </button>

      {inspectError && (
        <p role="alert" className="text-body font-medium text-status-critical">
          {inspectError}
        </p>
      )}
      {!inspect && !inspectError && (
        <p className="text-body text-ink-muted italic">{t('loading')}</p>
      )}

      {d && (
        <>
          <div>
            <h3 className="min-w-0 truncate text-title font-bold text-ink">
              {d.model_id ?? modelId}
            </h3>
            <p className="mt-0.5 text-meta text-ink-muted">
              v{d.model_version ?? '?'} · {d.provider_type ?? '?'} · {d.license ?? t('licenseUnknown')}
            </p>
            {inspect && (
              <p className="text-meta text-ink-muted">
                owner {inspect.owner_scope} · revision {inspect.revision} · lineage{' '}
                {inspect.lineage.deployment_state}
              </p>
            )}
          </div>

          <Field label={t('fields.taskIo')}>
            <div className="flex flex-wrap gap-1">
              {(d.task_types ?? []).map((taskType) => (
                <span key={taskType} className="rounded-sm bg-surface-sunken px-1.5 py-0.5 text-meta text-ink-secondary">
                  {taskType}
                </span>
              ))}
              {(d.output_types ?? []).map((outType) => (
                <span key={`out-${outType}`} className="rounded-sm bg-surface-sunken px-1.5 py-0.5 text-meta text-ink-muted">
                  → {outType}
                </span>
              ))}
            </div>
            <p className="mt-1 text-meta text-ink-muted">
              {t('taskIo.input', {
                value: (d.input_modalities ?? []).join(', ') || '—',
              })}
              {d.input_bands != null ? t('taskIo.bands', { bands: d.input_bands }) : ''}
            </p>
          </Field>

          <Field label={t('fields.spatial')}>
            {(() => {
              const lines = spatialLines(d, t);
              if (lines.length === 0) {
                return <p className="text-meta text-ink-muted">{t('spatial.empty')}</p>;
              }
              return (
                <ul className="flex flex-col gap-0.5">
                  {lines.map((l) => (
                    <li key={l.k} className="text-meta text-ink-secondary">
                      <span className="font-medium text-ink">{l.k}</span>
                      {t('spatial.sep')}
                      {l.v}
                    </li>
                  ))}
                </ul>
              );
            })()}
          </Field>

          <Field label={t('fields.determinism')}>
            <p className="text-meta text-ink-secondary">
              {t('determinism.value', {
                value: d.random_seed_policy ?? t('determinism.undeclared'),
              })}
            </p>
          </Field>

          <Field label={t('fields.device')}>
            <p className="text-meta text-ink-secondary">
              {d.device_requirements?.required ?? '—'}
              {d.device_requirements?.allow_cpu_fallback != null
                ? d.device_requirements.allow_cpu_fallback
                  ? t('device.cpuAllowed')
                  : t('device.cpuDisallowed')
                : ''}
              {d.device_requirements?.min_vram_mb != null
                ? t('device.vram', { mb: d.device_requirements.min_vram_mb })
                : ''}
            </p>
          </Field>

          {inspect?.provider_capabilities &&
            Object.keys(inspect.provider_capabilities).length > 0 && (
              <Field label={t('fields.provider')}>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-sm bg-surface-sunken px-2 py-1.5 text-meta text-ink-secondary">
                  {JSON.stringify(inspect.provider_capabilities, null, 2)}
                </pre>
              </Field>
            )}

          <Field label={t('fields.provenance')}>
            <div
              data-state="provenance"
              className="rounded-sm border border-edge-subtle bg-surface-sunken/60 px-2 py-1.5"
            >
              {d.provenance && Object.keys(d.provenance).length > 0 ? (
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all text-meta text-ink-secondary">
                  {JSON.stringify(d.provenance, null, 2)}
                </pre>
              ) : (
                <p className="text-meta text-ink-muted">{t('provenance.empty')}</p>
              )}
              <p className="mt-1 text-meta text-ink-muted">{t('provenance.note')}</p>
            </div>
          </Field>

          <Field
            label={
              history
                ? t('fields.versionEventsCount', {
                    count: history.event_count ?? events.length,
                  })
                : t('fields.versionEvents')
            }
          >
            {events.length === 0 ? (
              <p className="text-meta text-ink-muted">{t('versionEvents.empty')}</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {events
                  .sort((a, b) => b.seq - a.seq)
                  .slice(0, 20)
                  .map((e) => (
                    <li
                      key={`${e.version}-${e.seq}`}
                      className="rounded-sm bg-surface-sunken px-2 py-1 text-meta text-ink-secondary"
                    >
                      <span className="font-medium text-ink">#{e.seq}</span> {e.event_type}
                      {e.version ? ` · v${e.version}` : ''} · {e.actor} ·{' '}
                      {new Date(e.ts * 1000).toLocaleString()}
                    </li>
                  ))}
              </ul>
            )}
          </Field>
        </>
      )}
    </div>
  );
}

export default ModelOpsModelDetail;
