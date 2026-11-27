'use client';

/**
 * GOAL-20261127-02 Studio 移动对象轨迹面板（功能专属）。
 *
 * 生命周期：选择 committed MapSpec 的 ref 点表源 → inspect 真实字段预填 →
 * 显式确认绑定/参数 → Build（才计算，可取消；预览永不写 MapSpec）→
 * Apply 一笔 CAS 提交（intent = apply_trajectory_product，与 Pi 工具同链）。
 * 刷新后从 committed `layer.trajectory` 恢复已应用状态。实体钻取经共享
 * selection store 发布（map→list→map 联动的单一事实源）。
 */

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useT } from '@/lib/i18n/useT';
import {
  buildTrajectoryProduct,
  defaultTrajectoryConfig,
  findAppliedTrajectory,
  guessTrajectoryFieldBindings,
  inspectTrajectorySource,
  listTrajectorySourceCandidates,
  trajectoryStatsView,
  type AppliedTrajectory,
  type TrajectoryInspectResponse,
  type TrajectoryPreviewResponse,
  type TrajectoryProductConfig,
  type TrajectoryRegionSummaryRow,
} from '@/lib/moving-object/trajectory-product';
import {
  getCommittedMapSpec,
  getMapSpecSessionCursor,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import { commitMapSpecMutation } from '@/lib/mapspec/user-mutation';
import { describeApiError } from '@/lib/api/transport';
import { publishSelection } from '@/lib/selection/selection-store';

/** 请求代次 + AbortController + 会话守卫：旧响应绝不覆盖新设置。 */
function useGuardedRequest() {
  const seqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const begin = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    seqRef.current += 1;
    return { seq: seqRef.current, signal: abortRef.current.signal };
  }, []);
  const guard = useCallback((seq: number) => {
    const cursor = getMapSpecSessionCursor();
    return seq === seqRef.current && Boolean(cursor.sessionId);
  }, []);
  useEffect(() => () => abortRef.current?.abort(), []);
  return { begin, guard };
}

function fmtNum(value: number | undefined | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return value.toLocaleString('en-US');
}

function useCommittedSpec() {
  return useSyncExternalStore(subscribeMapSpecLive, getCommittedMapSpec, getCommittedMapSpec);
}

export function TrajectoryPanel() {
  const t = useT();
  const spec = useCommittedSpec();
  const cursor = getMapSpecSessionCursor();
  const sessionId = cursor.sessionId;
  const ownerToken = cursor.ownerToken;
  const { begin, guard } = useGuardedRequest();

  const sources = useMemo(() => listTrajectorySourceCandidates(spec), [spec]);
  const applied: AppliedTrajectory | null = useMemo(
    () => findAppliedTrajectory(spec),
    [spec],
  );

  const [config, setConfig] = useState<TrajectoryProductConfig>(() =>
    defaultTrajectoryConfig(),
  );
  const [inspect, setInspect] = useState<TrajectoryInspectResponse | null>(null);
  const [preview, setPreview] = useState<TrajectoryPreviewResponse | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [building, setBuilding] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const patchConfig = useCallback(
    (patch: Partial<TrajectoryProductConfig>) => {
      setConfig((c) => ({ ...c, ...patch }));
    },
    [],
  );

  // 数据源选定后：inspect 真实字段 + 猜测绑定（不伪造）。
  const handleSourceChange = useCallback(
    async (ref: string) => {
      patchConfig({ dataset: ref });
      setInspect(null);
      setPreview(null);
      setError(null);
      if (!ref || !sessionId) return;
      const { seq, signal } = begin();
      setInspecting(true);
      try {
        const result = await inspectTrajectorySource(
          sessionId,
          {
            dataset: ref,
            entity_id_field: config.entity_id_field,
            time_field: config.time_field,
            assumed_timezone: config.assumed_timezone,
          },
          { ownerToken, signal },
        );
        if (!guard(seq)) return;
        setInspect(result);
        const guess = guessTrajectoryFieldBindings(result.fields ?? {});
        patchConfig(guess);
      } catch (err) {
        if (!guard(seq)) return;
        setError(t('movingObject.inspectFailed', { reason: describeApiError(err, String(err)) }));
      } finally {
        if (guard(seq)) setInspecting(false);
      }
    },
    [begin, config.assumed_timezone, config.entity_id_field, config.time_field,
      guard, ownerToken, patchConfig, sessionId, t],
  );

  const handleBuild = useCallback(async () => {
    if (!sessionId || !config.dataset) return;
    const { seq, signal } = begin();
    setBuilding(true);
    setError(null);
    try {
      const result = await buildTrajectoryProduct(sessionId, config, {
        ownerToken,
        signal,
      });
      if (!guard(seq)) return;
      setPreview(result);
    } catch (err) {
      if (!guard(seq)) return;
      setError(t('movingObject.buildFailed', { reason: describeApiError(err, String(err)) }));
    } finally {
      if (guard(seq)) setBuilding(false);
    }
  }, [begin, config, guard, ownerToken, sessionId, t]);

  const handleApply = useCallback(async () => {
    if (!preview || !sessionId) return;
    setApplying(true);
    setError(null);
    try {
      await commitMapSpecMutation({
        intent: 'apply_trajectory_product',
        product_ref: preview.product_ref,
        family_id: preview.family_id,
        expected_revision: preview.mapspec_revision ?? undefined,
      });
      setPreview(null);
    } catch (err) {
      setError(t('movingObject.applyFailed', { reason: describeApiError(err, String(err)) }));
    } finally {
      setApplying(false);
    }
  }, [preview, sessionId, t]);

  // 刷新恢复：committed layer.trajectory 已在 `applied` 中体现。
  const stats = useMemo(() => trajectoryStatsView(preview?.stats), [preview?.stats]);
  const regionRows: TrajectoryRegionSummaryRow[] = preview?.region_summaries ?? [];

  const handleEntityDrill = useCallback(
    (entityId: string) => {
      // 共享 selection store：map→list→map 联动的单一事实源（有界）。
      patchConfig({ entity_filter: entityId ? [entityId] : null });
      publishSelection('select', {
        source: 'legend',
        layer_id: applied?.layerId ?? preview?.family_id ?? '',
        selected_ids: entityId ? [entityId] : [],
      });
    },
    [applied?.layerId, patchConfig, preview?.family_id],
  );

  if (!sessionId) {
    return <p className="text-body text-ink-muted">{t('movingObject.noSession')}</p>;
  }

  const appliedView = applied;
  const effectiveEntities = appliedView
    ? Number((appliedView.trajectory.stats as Record<string, unknown> | undefined)?.entities ?? 0)
    : stats.entities;
  const effectiveSegments = appliedView
    ? Number((appliedView.trajectory.stats as Record<string, unknown> | undefined)?.segments ?? 0)
    : stats.segments;

  return (
    <div className="space-y-3" data-testid="trajectory-panel">
      {appliedView && !preview && (
        <div className="rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-caption text-ink-muted">
          <span className="mr-1 rounded bg-[color:var(--agent-accent,#16a34a)] px-1 text-[10px] text-white">
            {t('movingObject.appliedBadge')}
          </span>
          {t('movingObject.panelSummaryApplied', {
            entities: effectiveEntities,
            segments: effectiveSegments,
          })}
        </div>
      )}

      {sources.length === 0 ? (
        <p className="text-caption text-ink-muted">{t('movingObject.noSources')}</p>
      ) : (
        <>
          <div>
            <label className="mb-1 block text-caption text-ink-muted">
              {t('movingObject.sourceLabel')}
            </label>
            <select
              value={config.dataset}
              onChange={(e) => void handleSourceChange(e.target.value)}
              className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-body text-ink"
            >
              <option value="">{t('movingObject.sourcePlaceholder')}</option>
              {sources.map((s) => (
                <option key={s.key} value={s.ref}>{s.key}</option>
              ))}
            </select>
            {inspecting && (
              <p className="mt-1 text-caption text-ink-muted">{t('movingObject.inspectRunning')}</p>
            )}
            {inspect && (
              <p className="mt-1 text-caption text-ink-muted">
                {t('movingObject.inspectMeta', {
                  ref: inspect.source_ref,
                  rows: inspect.feature_count ?? 0,
                  entities: inspect.ingest_health?.entity_count ?? 0,
                })}
                {' · '}
                {t('movingObject.healthAccepted', {
                  accepted: inspect.ingest_health?.accepted ?? 0,
                  rows: inspect.feature_count ?? 0,
                })}
                {' · '}
                {t('movingObject.healthAnomalies', {
                  missingId: inspect.ingest_health?.missing_entity_id ?? 0,
                  badTime: inspect.ingest_health?.unparseable_time ?? 0,
                  badGeom: inspect.ingest_health?.missing_or_degenerate_geometry ?? 0,
                })}
              </p>
            )}
          </div>

          <fieldset className="space-y-2">
            <legend className="text-caption font-semibold text-ink">
              {t('movingObject.fieldsTitle')}
            </legend>
            <div className="grid grid-cols-2 gap-2">
              <label className="text-caption text-ink-muted">
                {t('movingObject.fieldEntity')}
                <input
                  value={config.entity_id_field}
                  onChange={(e) => patchConfig({ entity_id_field: e.target.value })}
                  className="mt-0.5 w-full rounded border border-edge-subtle bg-surface-sunken px-1.5 py-1 text-body text-ink"
                />
              </label>
              <label className="text-caption text-ink-muted">
                {t('movingObject.fieldTime')}
                <input
                  value={config.time_field}
                  onChange={(e) => patchConfig({ time_field: e.target.value })}
                  className="mt-0.5 w-full rounded border border-edge-subtle bg-surface-sunken px-1.5 py-1 text-body text-ink"
                />
              </label>
            </div>
          </fieldset>

          <fieldset className="space-y-2">
            <legend className="text-caption font-semibold text-ink">
              {t('movingObject.paramsTitle')}
            </legend>
            <div className="grid grid-cols-2 gap-2">
              <NumberField label={t('movingObject.paramMaxGap')} value={config.max_gap_s}
                onChange={(v) => patchConfig({ max_gap_s: v })} />
              <NumberField label={t('movingObject.paramMaxSpeed')} value={config.max_speed_kmh}
                onChange={(v) => patchConfig({ max_speed_kmh: v })} />
              <NumberField label={t('movingObject.paramMaxJump')} value={config.max_jump_m}
                onChange={(v) => patchConfig({ max_jump_m: v })} />
              <NumberField label={t('movingObject.paramDwellRadius')} value={config.dwell_radius_m}
                onChange={(v) => patchConfig({ dwell_radius_m: v })} />
              <NumberField label={t('movingObject.paramDwellMin')} value={config.dwell_min_dwell_s}
                onChange={(v) => patchConfig({ dwell_min_dwell_s: v })} />
              <label className="text-caption text-ink-muted">
                {t('movingObject.paramTimezone')}
                <input
                  value={config.assumed_timezone}
                  onChange={(e) => patchConfig({ assumed_timezone: e.target.value })}
                  className="mt-0.5 w-full rounded border border-edge-subtle bg-surface-sunken px-1.5 py-1 text-body text-ink"
                />
              </label>
            </div>
          </fieldset>

          <fieldset className="space-y-2">
            <legend className="text-caption font-semibold text-ink">
              {t('movingObject.regionsTitle')}
            </legend>
            <select
              value={config.regions_ref ?? ''}
              onChange={(e) => patchConfig({ regions_ref: e.target.value || null })}
              className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-body text-ink"
            >
              <option value="">{t('movingObject.regionsPlaceholder')}</option>
              {sources.map((s) => (
                <option key={`zone-${s.key}`} value={s.ref}>{s.key}</option>
              ))}
            </select>
            {config.regions_ref && (
              <div className="flex items-center gap-3 text-caption text-ink-muted">
                <span>{t('movingObject.boundaryRule')}</span>
                <label className="flex items-center gap-1">
                  <input type="radio" name="boundary_rule" checked={config.boundary_rule === 'closed'}
                    onChange={() => patchConfig({ boundary_rule: 'closed' })} />
                  {t('movingObject.boundaryClosed')}
                </label>
                <label className="flex items-center gap-1">
                  <input type="radio" name="boundary_rule" checked={config.boundary_rule === 'open'}
                    onChange={() => patchConfig({ boundary_rule: 'open' })} />
                  {t('movingObject.boundaryOpen')}
                </label>
              </div>
            )}
          </fieldset>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void handleBuild()}
              disabled={!config.dataset || building}
              className="rounded-md border border-edge-subtle bg-surface-sunken px-3 py-1.5 text-body text-ink hover:bg-surface-raised disabled:opacity-50"
            >
              {building ? t('movingObject.buildRunning') : t('movingObject.previewButton')}
            </button>
            {preview?.can_apply && (
              <button
                type="button"
                onClick={() => void handleApply()}
                disabled={applying}
                className="rounded-md px-3 py-1.5 text-body text-white disabled:opacity-50"
                style={{ background: 'var(--agent-accent, #16a34a)' }}
              >
                {t('movingObject.applyButton')}
              </button>
            )}
            {preview?.cache_hit && preview.cache_hit !== 'miss' && (
              <span className="text-caption text-ink-muted">{t('movingObject.cachedNote')}</span>
            )}
          </div>

          {error && <p className="text-caption text-red-600">{error}</p>}

          {preview && !preview.can_apply && (
            <p className="text-caption text-ink-muted">{t('movingObject.noMotion')}</p>
          )}

          {preview?.can_apply && (
            <>
              <div className="rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5">
                <div className="text-caption font-semibold text-ink">
                  {t('movingObject.statsTitle')}
                  <span className="ml-1 rounded bg-amber-500 px-1 text-[10px] text-white">
                    {t('movingObject.previewBadge')}
                  </span>
                </div>
                <dl className="mt-1 grid grid-cols-3 gap-x-3 gap-y-0.5 text-caption text-ink-muted">
                  <Stat label={t('movingObject.statEntities')} value={fmtNum(stats.entities)} />
                  <Stat label={t('movingObject.statObservations')} value={fmtNum(stats.observations)} />
                  <Stat label={t('movingObject.statSegments')} value={fmtNum(stats.segments)} />
                  <Stat label={t('movingObject.statDwells')} value={fmtNum(stats.dwells)} />
                  <Stat label={t('movingObject.statBreaks')} value={fmtNum(stats.breaks)} />
                  <Stat label={t('movingObject.statTotalKm')} value={stats.totalKm === null ? '—' : String(stats.totalKm)} />
                </dl>
              </div>

              {preview.assumptions?.length ? (
                <details className="text-caption text-ink-muted">
                  <summary className="cursor-pointer">{t('movingObject.assumptionsTitle')}</summary>
                  <ul className="mt-1 list-disc pl-4">
                    {preview.assumptions.map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                </details>
              ) : null}

              {regionRows.length > 0 && (
                <div className="rounded-md border border-edge-subtle px-2 py-1.5">
                  <div className="text-caption font-semibold text-ink">
                    {t('movingObject.eventsTitle')}
                  </div>
                  <table className="mt-1 w-full text-caption">
                    <thead>
                      <tr className="text-ink-muted">
                        <th className="text-left">{t('movingObject.regionCol')}</th>
                        <th className="text-right">{t('movingObject.regionEntitiesCol')}</th>
                        <th className="text-right">{t('movingObject.regionEventsCol')}</th>
                        <th className="text-right">{t('movingObject.regionDwellCol')}</th>
                        <th className="text-right">{t('movingObject.regionZeroHitCol')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {regionRows.map((r) => (
                        <tr key={r.region_id} className="border-t border-edge-subtle">
                          <td className="text-ink">{r.region_id}</td>
                          <td className="text-right">{r.n_entities}</td>
                          <td className="text-right">
                            {r.n_enter}/{r.n_exit}/{r.n_cross}
                          </td>
                          <td className="text-right">{Math.round(r.total_dwell_s)}</td>
                          <td className="text-right">{r.entities_zero_hit}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="mt-1 text-caption text-ink-muted">
                    {t('movingObject.inferredNote')}
                  </p>
                </div>
              )}

              <label className="block text-caption text-ink-muted">
                {t('movingObject.entityDrill')}
                <select
                  value={config.entity_filter?.[0] ?? ''}
                  onChange={(e) => handleEntityDrill(e.target.value)}
                  className="mt-0.5 w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-body text-ink"
                >
                  <option value="">{t('movingObject.drillPlaceholder')}</option>
                  {(preview.quality?.top_entities ?? []).map((row) => {
                    const eid = String(row.entity_id ?? '');
                    return <option key={eid} value={eid}>{eid}</option>;
                  })}
                </select>
              </label>
            </>
          )}
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="inline">{label}: </dt>
      <dd className="inline font-medium text-ink">{value}</dd>
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="text-caption text-ink-muted">
      {label}
      <input
        type="number"
        value={value}
        min={0}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (Number.isFinite(v) && v >= 0) onChange(v);
        }}
        className="mt-0.5 w-full rounded border border-edge-subtle bg-surface-sunken px-1.5 py-1 text-body text-ink"
      />
    </label>
  );
}

export default TrajectoryPanel;
