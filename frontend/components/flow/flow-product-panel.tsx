'use client';

/**
 * G21 Studio OD flow product panel（功能专属面板）。
 *
 * 生命周期：选择 committed MapSpec 的 ref geojson 源 → inspect 真实字段预填 →
 * 显式确认绑定/口径/样式 → Preview（才计算，可取消）→ Apply 一笔 CAS 提交
 * （expected_revision = 预览 revision）。刷新后从 committed `layer.flow` 恢复
 * 已应用状态。hover/焦点不改 revision；预览绝不写入 MapSpec。
 */

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useT } from '@/lib/i18n/useT';
import {
  buildPreviewMapSpec,
  defaultFlowConfig,
  fetchFlowRefGeoJSON,
  findAppliedFlow,
  flowConfigFromApplied,
  flowErrorDetail,
  flowStatsView,
  guessFieldBindings,
  inspectFlowSource,
  listFlowSourceCandidates,
  modeAvailabilityView,
  previewFlowProduct,
  queryFlowProduct,
  type FlowDirectionMode,
  type FlowInspectResponse,
  type FlowNodeDirection,
  type FlowPreviewResponse,
  type FlowProductConfig,
  type FlowQueryEdgeResponse,
  type FlowQueryNodeResponse,
} from '@/lib/flow/flow-product';
import {
  getCommittedMapSpec,
  getMapSpecSessionCursor,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import { commitMapSpecMutation, newMutationId, refreshMapSpecAfterExternalMutation } from '@/lib/mapspec/user-mutation';
import { compileMapSpecToSvg } from '@/lib/mapspec-compiler/mapspec-to-svg';
import { describeApiError } from '@/lib/api/transport';
import { getSelection, subscribeSelection } from '@/lib/selection/selection-store';
import { devOnly } from '@/lib/utils/logger';

const FIELD_LABEL_KEYS = {
  origin_id_field: 'flow.fieldOriginId',
  origin_lng_field: 'flow.fieldOriginLng',
  origin_lat_field: 'flow.fieldOriginLat',
  destination_id_field: 'flow.fieldDestinationId',
  destination_lng_field: 'flow.fieldDestinationLng',
  destination_lat_field: 'flow.fieldDestinationLat',
  weight_field: 'flow.fieldWeight',
} as const;

type BindingKey = keyof typeof FIELD_LABEL_KEYS;

const MODE_LABEL_KEYS: Record<FlowDirectionMode, string> = {
  directed: 'flow.modeDirected',
  'undirected-total': 'flow.modeUndirectedTotal',
  net: 'flow.modeNet',
};

function fmtNum(value: number | undefined | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return value.toLocaleString('en-US');
}

function fmtPct(value: number | null | undefined, unknownLabel: string): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return unknownLabel;
  return `${(value * 100).toFixed(1)}%`;
}

/** 请求代次 + AbortController + 会话守卫：旧响应绝不覆盖新设置。 */
function useGuardedRequest() {
  const seqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const begin = useCallback(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    seqRef.current += 1;
    const seq = seqRef.current;
    const sessionId = getMapSpecSessionCursor().sessionId;
    return {
      signal: controller.signal,
      /** 响应落地前复核：同代次 + 同会话才允许写状态。 */
      isCurrent: () =>
        seq === seqRef.current &&
        !controller.signal.aborted &&
        getMapSpecSessionCursor().sessionId === sessionId,
    };
  }, []);
  useEffect(() => () => abortRef.current?.abort(), []);
  return begin;
}

function useCommittedSpec() {
  return useSyncExternalStore(subscribeMapSpecLive, getCommittedMapSpec, () => null);
}

const inputCls =
  'w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-body text-ink focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)] disabled:opacity-40';
const labelCls = 'mb-1 block text-meta font-medium text-ink-secondary';

export function FlowProductPanel() {
  const t = useT();
  const spec = useCommittedSpec();
  const applied = useMemo(() => findAppliedFlow(spec), [spec]);
  const candidates = useMemo(() => listFlowSourceCandidates(spec), [spec]);

  const [editing, setEditing] = useState(false);
  const [config, setConfig] = useState<FlowProductConfig>(() => defaultFlowConfig());
  const [inspect, setInspect] = useState<
    | { status: 'idle' }
    | { status: 'running' }
    | { status: 'done'; data: FlowInspectResponse }
    | { status: 'error'; message: string }
  >({ status: 'idle' });
  const [preview, setPreview] = useState<
    | { status: 'idle' }
    | { status: 'running' }
    | { status: 'done'; data: FlowPreviewResponse; stale: boolean }
    | { status: 'error'; message: string }
  >({ status: 'idle' });
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [svg, setSvg] = useState<{ status: 'idle' } | { status: 'done'; markup: string } | { status: 'failed' }>(
    { status: 'idle' },
  );

  const beginRequest = useGuardedRequest();

  const patchConfig = useCallback((patch: Partial<FlowProductConfig>) => {
    setConfig((prev) => ({ ...prev, ...patch }));
    setPreview((prev) => (prev.status === 'done' ? { ...prev, stale: true } : prev));
  }, []);

  const runInspect = useCallback(
    async (ref: string) => {
      const { sessionId, ownerToken } = getMapSpecSessionCursor();
      if (!sessionId || !ref) return;
      const guard = beginRequest();
      setInspect({ status: 'running' });
      try {
        const data = await inspectFlowSource(sessionId, { ...defaultFlowConfig(), od_table_ref: ref }, {
          ownerToken,
          signal: guard.signal,
        });
        if (!guard.isCurrent()) return;
        setInspect({ status: 'done', data });
        const guess = guessFieldBindings(data.fields);
        setConfig((prev) => ({ ...prev, od_table_ref: ref, ...guess }));
      } catch (err) {
        if (!guard.isCurrent()) return;
        setInspect({ status: 'error', message: describeApiError(err, String(err)) });
      }
    },
    [beginRequest],
  );

  useEffect(() => {
    if (!applied && !editing) setEditing(true);
    if (!applied && candidates.length > 0 && !config.od_table_ref) {
      const ref = candidates[0].ref;
      setConfig((prev) => ({ ...prev, od_table_ref: ref }));
      void runInspect(ref);
    }
  }, [applied, editing, candidates, config.od_table_ref, runInspect]);

  const runPreview = useCallback(async () => {
    const { sessionId, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId) return;
    const guard = beginRequest();
    setPreview({ status: 'running' });
    setSvg({ status: 'idle' });
    setApplyError(null);
    try {
      const data = await previewFlowProduct(sessionId, config, { ownerToken, signal: guard.signal });
      if (!guard.isCurrent()) return;
      setPreview({ status: 'done', data, stale: false });
      // 缩略图：真实 ref 数据 + 既有编译器（只读，不 commit）。
      try {
        const fragment = data.mapspec_fragment;
        if (fragment) {
          const edgeSourceKey = Object.keys(fragment.sources).find((k) => k.includes('edges'));
          const nodeSourceKey = Object.keys(fragment.sources).find((k) => k.includes('nodes'));
          const edgeRef = (edgeSourceKey ? fragment.sources[edgeSourceKey]?.url : undefined) ?? data.edge_ref;
          const nodeRef = (nodeSourceKey ? fragment.sources[nodeSourceKey]?.url : undefined) ?? data.node_ref;
          const [edgeGeo, nodeGeo] = await Promise.all([
            edgeRef ? fetchFlowRefGeoJSON(sessionId, edgeRef, { ownerToken, signal: guard.signal }) : null,
            nodeRef ? fetchFlowRefGeoJSON(sessionId, nodeRef, { ownerToken, signal: guard.signal }) : null,
          ]);
          if (!guard.isCurrent()) return;
          const previewSpec = buildPreviewMapSpec(fragment, edgeGeo, nodeGeo);
          setSvg({ status: 'done', markup: compileMapSpecToSvg(previewSpec, { width: 560, height: 360, targetDpi: 72 }) });
        }
      } catch (err) {
        if (!guard.isCurrent()) return;
        devOnly.warn('[FlowProductPanel] preview thumbnail failed', err);
        setSvg({ status: 'failed' });
      }
    } catch (err) {
      if (!guard.isCurrent()) return;
      const detail = flowErrorDetail(err);
      setPreview({
        status: 'error',
        message: detail ? `${detail.code}: ${detail.message}` : describeApiError(err, String(err)),
      });
    }
  }, [beginRequest, config]);

  const runApply = useCallback(async () => {
    if (preview.status !== 'done' || preview.stale || !preview.data.can_apply) return;
    const { product_ref: productRef, family_id: familyId, mapspec_revision: previewRevision } = preview.data;
    if (!productRef || !familyId) return;
    setApplying(true);
    setApplyError(null);
    try {
      const data = await commitMapSpecMutation({
        intent: 'apply_flow_product',
        product_ref: productRef,
        family_id: familyId,
        ...(typeof previewRevision === 'number' ? { expected_revision: previewRevision } : {}),
        client_mutation_id: newMutationId(),
      });
      if (data && data.status === 'superseded') {
        setApplyError(t('flow.applySuperseded'));
        return;
      }
      const { sessionId, ownerToken } = getMapSpecSessionCursor();
      if (sessionId) {
        try {
          await refreshMapSpecAfterExternalMutation(sessionId, data?.mutation_revision);
        } catch (err) {
          devOnly.warn('[FlowProductPanel] map spec refresh after apply failed', err);
        }
      }
      if (getMapSpecSessionCursor().sessionId !== sessionId || getMapSpecSessionCursor().ownerToken !== ownerToken) return;
      setEditing(false);
      setPreview({ status: 'idle' });
      setSvg({ status: 'idle' });
    } catch (err) {
      const detail = flowErrorDetail(err);
      setApplyError(detail ? `${detail.code}: ${detail.message}` : t('flow.applyFailed', { reason: describeApiError(err, '') }));
    } finally {
      setApplying(false);
    }
  }, [preview, t]);

  const startEdit = () => {
    if (applied) {
      setConfig(flowConfigFromApplied(applied.flow));
      setInspect({ status: 'idle' });
      setPreview({ status: 'idle' });
      setSvg({ status: 'idle' });
      void runInspect(applied.flow.source_ref);
    }
    setEditing(true);
  };

  const sessionId = getMapSpecSessionCursor().sessionId;
  const previewData = preview.status === 'done' ? preview.data : null;
  const statsView = flowStatsView(previewData?.stats ?? (applied?.flow.stats as never));
  const modes = modeAvailabilityView(
    previewData?.supported_modes ?? (applied?.flow.supported_modes as FlowDirectionMode[] | undefined),
    previewData?.mode_availability,
  );
  const inspectFields = inspect.status === 'done' ? Object.keys(inspect.data.fields) : [];

  return (
    <div className="space-y-3">
      {applied && !editing && (
        <div className="space-y-2 rounded-md border border-edge-subtle bg-surface-sunken p-2.5">
          <div className="flex items-center justify-between gap-2">
            <span className="rounded-sm bg-surface-overlay px-1.5 py-0.5 text-caption font-semibold text-ink-secondary">
              {t('flow.appliedBadge')}
            </span>
            <button
              type="button"
              onClick={startEdit}
              className="rounded-md border border-edge-subtle px-2 py-1 text-meta font-medium text-ink-secondary hover:bg-surface-hover"
            >
              {t('flow.editConfig')}
            </button>
          </div>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-caption text-ink-muted">
            <div className="col-span-2 truncate font-mono" title={applied.flow.product_ref}>
              {applied.flow.product_ref}
            </div>
            <div>{t(MODE_LABEL_KEYS[applied.flow.direction_mode])}</div>
            <div>{applied.flow.weight_unit || 'count'}</div>
            <div>{t('flow.statGrossRetained')}: {fmtPct(statsView.grossRetained, t('flow.statUnknown'))}</div>
            <div>{t('flow.statNetCoverage')}: {fmtPct(statsView.netMagnitudeCoverage, t('flow.statUnknown'))}</div>
          </dl>
          <p className="text-caption text-ink-muted">{t('flow.curveNote')}</p>
        </div>
      )}

      {editing && (
        <div className="space-y-3">
          {!sessionId && <p className="text-caption text-ink-muted">{t('flow.noSession')}</p>}
          {candidates.length === 0 ? (
            <p className="text-caption text-ink-muted">{t('flow.noSources')}</p>
          ) : (
            <>
              <div>
                <label htmlFor="flow-source" className={labelCls}>{t('flow.sourceLabel')}</label>
                <select
                  id="flow-source"
                  className={inputCls}
                  value={config.od_table_ref}
                  onChange={(e) => {
                    const ref = e.target.value;
                    patchConfig({ od_table_ref: ref });
                    void runInspect(ref);
                  }}
                >
                  <option value="">{t('flow.sourcePlaceholder')}</option>
                  {candidates.map((c) => (
                    <option key={c.key} value={c.ref}>{c.key}</option>
                  ))}
                </select>
                {inspect.status === 'running' && <p className="mt-1 text-caption text-ink-muted">{t('flow.inspectRunning')}</p>}
                {inspect.status === 'error' && <p className="mt-1 text-caption text-ink">{t('flow.inspectFailed', { reason: inspect.message })}</p>}
                {inspect.status === 'done' && (
                  <p className="mt-1 text-caption text-ink-muted">
                    {t('flow.inspectMeta', {
                      ref: inspect.data.source_ref,
                      rows: fmtNum(inspect.data.row_count),
                      kind: inspect.data.source_kind ?? 'geojson',
                    })}
                  </p>
                )}
              </div>

              <fieldset className="space-y-2 rounded-md border border-edge-subtle p-2.5">
                <legend className="px-1 text-meta font-semibold text-ink-secondary">{t('flow.fieldsTitle')}</legend>
                {(Object.keys(FIELD_LABEL_KEYS) as BindingKey[]).map((key) => (
                  <div key={key}>
                    <label htmlFor={`flow-field-${key}`} className={labelCls}>
                      {t(FIELD_LABEL_KEYS[key])}
                      <span className="text-ink-disabled"> ({t('flow.fieldRequired')})</span>
                    </label>
                    <select
                      id={`flow-field-${key}`}
                      className={inputCls}
                      aria-label={t(FIELD_LABEL_KEYS[key])}
                      aria-required
                      value={config[key]}
                      disabled={inspectFields.length === 0}
                      onChange={(e) => patchConfig({ [key]: e.target.value } as Partial<FlowProductConfig>)}
                    >
                      <option value="">
                        {inspectFields.length === 0 ? '—' : t('flow.fieldUnrecognized')}
                      </option>
                      {inspectFields.map((f) => (
                        <option key={f} value={f}>{f}</option>
                      ))}
                    </select>
                  </div>
                ))}
                <div>
                  <label htmlFor="flow-unit" className={labelCls}>{t('flow.fieldUnit')}</label>
                  <input
                    id="flow-unit"
                    type="text"
                    className={inputCls}
                    value={config.weight_unit}
                    placeholder={t('flow.unitPlaceholder')}
                    onChange={(e) => patchConfig({ weight_unit: e.target.value })}
                  />
                </div>
              </fieldset>

              <fieldset className="space-y-2 rounded-md border border-edge-subtle p-2.5">
                <legend className="px-1 text-meta font-semibold text-ink-secondary">{t('flow.nodeTableTitle')}</legend>
                <div>
                  <label htmlFor="flow-node-source" className={labelCls}>{t('flow.nodeTableRef')}</label>
                  <select
                    id="flow-node-source"
                    className={inputCls}
                    value={config.node_table_ref ?? ''}
                    onChange={(e) => patchConfig({ node_table_ref: e.target.value || null })}
                  >
                    <option value="">{t('flow.nodeTableNone')}</option>
                    {candidates.map((c) => (
                      <option key={c.key} value={c.ref}>{c.key}</option>
                    ))}
                  </select>
                </div>
                {config.node_table_ref && (
                  <div className="grid grid-cols-3 gap-2">
                    {([['node_id_field', 'flow.nodeIdField'], ['node_lng_field', 'flow.nodeLngField'], ['node_lat_field', 'flow.nodeLatField']] as const).map(
                      ([key, labelKey]) => (
                        <div key={key}>
                          <label htmlFor={`flow-${key}`} className={labelCls}>{t(labelKey)}</label>
                          <select
                            id={`flow-${key}`}
                            className={inputCls}
                            value={config[key]}
                            disabled={inspectFields.length === 0}
                            onChange={(e) => patchConfig({ [key]: e.target.value } as Partial<FlowProductConfig>)}
                          >
                            <option value="">—</option>
                            {inspectFields.map((f) => (
                              <option key={f} value={f}>{f}</option>
                            ))}
                          </select>
                        </div>
                      ),
                    )}
                  </div>
                )}
              </fieldset>

              <fieldset className="space-y-2 rounded-md border border-edge-subtle p-2.5">
                <legend className="px-1 text-meta font-semibold text-ink-secondary">{t('flow.directionModeTitle')}</legend>
                {modes.map((m) => (
                  <label
                    key={m.mode}
                    className={`flex items-center gap-2 text-meta ${m.available ? 'text-ink-secondary' : 'text-ink-disabled'}`}
                    title={m.available ? undefined : t('flow.modeDisabled', { reason: m.reason ?? '' })}
                  >
                    <input
                      type="radio"
                      name="flow-direction-mode"
                      className="h-3.5 w-3.5"
                      style={{ accentColor: 'var(--agent-accent, #16a34a)' }}
                      checked={config.direction_mode === m.mode}
                      disabled={!m.available}
                      onChange={() => patchConfig({ direction_mode: m.mode })}
                    />
                    <span>{t(MODE_LABEL_KEYS[m.mode])}</span>
                    {!m.available && (
                      <span className="text-caption">{t('flow.modeDisabled', { reason: m.reason ?? '' })}</span>
                    )}
                  </label>
                ))}
                <div className="space-y-1">
                  <span className="block text-meta font-medium text-ink-secondary">{t('flow.missingReverseTitle')}</span>
                  {(['unknown', 'zero'] as const).map((v) => (
                    <label key={v} className="flex items-center gap-2 text-meta text-ink-secondary">
                      <input
                        type="radio"
                        name="flow-missing-reverse"
                        className="h-3.5 w-3.5"
                        style={{ accentColor: 'var(--agent-accent, #16a34a)' }}
                        checked={config.missing_reverse === v}
                        onChange={() => patchConfig({ missing_reverse: v })}
                      />
                      <span>{v === 'unknown' ? t('flow.missingReverseUnknown') : t('flow.missingReverseZero')}</span>
                    </label>
                  ))}
                  <p className="text-caption text-ink-muted">{t('flow.missingReverseNote')}</p>
                </div>
              </fieldset>

              <fieldset className="space-y-2 rounded-md border border-edge-subtle p-2.5">
                <legend className="px-1 text-meta font-semibold text-ink-secondary">{t('flow.geometryTitle')}</legend>
                {(['straight', 'curve'] as const).map((g) => (
                  <label key={g} className="flex items-center gap-2 text-meta text-ink-secondary">
                    <input
                      type="radio"
                      name="flow-geometry"
                      className="h-3.5 w-3.5"
                      style={{ accentColor: 'var(--agent-accent, #16a34a)' }}
                      checked={config.geometry === g}
                      onChange={() => patchConfig({ geometry: g })}
                    />
                    <span>{g === 'straight' ? t('flow.geometryStraight') : t('flow.geometryCurve')}</span>
                  </label>
                ))}
                <p className="text-caption text-ink-muted">{t('flow.curveNote')}</p>
                <label className="flex items-center gap-2 text-meta text-ink-secondary">
                  <input
                    type="checkbox"
                    className="h-3.5 w-3.5"
                    style={{ accentColor: 'var(--agent-accent, #16a34a)' }}
                    checked={config.show_arrows}
                    disabled={config.direction_mode === 'undirected-total'}
                    onChange={(e) => patchConfig({ show_arrows: e.target.checked })}
                  />
                  <span>{t('flow.arrowsShow')}</span>
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label htmlFor="flow-arrow-spacing" className={labelCls}>{t('flow.arrowSpacing')}</label>
                    <input
                      id="flow-arrow-spacing"
                      type="number"
                      min={1}
                      className={inputCls}
                      value={config.arrow_spacing_px}
                      onChange={(e) => patchConfig({ arrow_spacing_px: Number(e.target.value) || 1 })}
                    />
                  </div>
                  <div>
                    <label htmlFor="flow-arrow-size" className={labelCls}>{t('flow.arrowSize')}</label>
                    <input
                      id="flow-arrow-size"
                      type="number"
                      min={1}
                      className={inputCls}
                      value={config.arrow_size_px}
                      onChange={(e) => patchConfig({ arrow_size_px: Number(e.target.value) || 1 })}
                    />
                  </div>
                </div>
              </fieldset>

              <fieldset className="space-y-2 rounded-md border border-edge-subtle p-2.5">
                <legend className="px-1 text-meta font-semibold text-ink-secondary">{t('flow.widthTitle')}</legend>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label htmlFor="flow-width-min" className={labelCls}>{t('flow.widthMin')}</label>
                    <input
                      id="flow-width-min"
                      type="number"
                      min={0.1}
                      step={0.1}
                      className={inputCls}
                      value={config.width_min_px}
                      onChange={(e) => patchConfig({ width_min_px: Number(e.target.value) || 0.1 })}
                    />
                  </div>
                  <div>
                    <label htmlFor="flow-width-max" className={labelCls}>{t('flow.widthMax')}</label>
                    <input
                      id="flow-width-max"
                      type="number"
                      min={0.1}
                      step={0.1}
                      className={inputCls}
                      value={config.width_max_px}
                      onChange={(e) => patchConfig({ width_max_px: Number(e.target.value) || 0.1 })}
                    />
                  </div>
                </div>
                <div>
                  <label htmlFor="flow-transform" className={labelCls}>{t('flow.transformTitle')}</label>
                  <select
                    id="flow-transform"
                    className={inputCls}
                    value={config.weight_transform}
                    onChange={(e) => patchConfig({ weight_transform: e.target.value as FlowProductConfig['weight_transform'] })}
                  >
                    <option value="linear">{t('flow.transformLinear')}</option>
                    <option value="sqrt">{t('flow.transformSqrt')}</option>
                    <option value="log1p">{t('flow.transformLog1p')}</option>
                  </select>
                </div>
                <label className="flex items-center gap-2 text-meta text-ink-secondary">
                  <input
                    type="checkbox"
                    className="h-3.5 w-3.5"
                    style={{ accentColor: 'var(--agent-accent, #16a34a)' }}
                    checked={config.weight_domain != null}
                    onChange={(e) =>
                      patchConfig({ weight_domain: e.target.checked ? [0, 100] : null })
                    }
                  />
                  <span>{t('flow.fixedDomain')}</span>
                </label>
                {config.weight_domain && (
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label htmlFor="flow-domain-min" className={labelCls}>{t('flow.domainMin')}</label>
                      <input
                        id="flow-domain-min"
                        type="number"
                        className={inputCls}
                        value={config.weight_domain[0]}
                        onChange={(e) =>
                          patchConfig({ weight_domain: [Number(e.target.value), config.weight_domain?.[1] ?? 100] })
                        }
                      />
                    </div>
                    <div>
                      <label htmlFor="flow-domain-max" className={labelCls}>{t('flow.domainMax')}</label>
                      <input
                        id="flow-domain-max"
                        type="number"
                        className={inputCls}
                        value={config.weight_domain[1]}
                        onChange={(e) =>
                          patchConfig({ weight_domain: [config.weight_domain?.[0] ?? 0, Number(e.target.value)] })
                        }
                      />
                    </div>
                  </div>
                )}
                <p className="text-caption text-ink-muted">{t('flow.paletteNote')}</p>
              </fieldset>

              <fieldset className="space-y-2 rounded-md border border-edge-subtle p-2.5">
                <legend className="px-1 text-meta font-semibold text-ink-secondary">{t('flow.nodesShow')}</legend>
                <label className="flex items-center gap-2 text-meta text-ink-secondary">
                  <input
                    type="checkbox"
                    className="h-3.5 w-3.5"
                    style={{ accentColor: 'var(--agent-accent, #16a34a)' }}
                    checked={config.show_nodes}
                    onChange={(e) => patchConfig({ show_nodes: e.target.checked })}
                  />
                  <span>{t('flow.nodesShow')}</span>
                </label>
                <div>
                  <label htmlFor="flow-node-scope" className={labelCls}>{t('flow.nodeTotalsScopeTitle')}</label>
                  <select
                    id="flow-node-scope"
                    className={inputCls}
                    value={config.node_totals_scope}
                    onChange={(e) => patchConfig({ node_totals_scope: e.target.value as FlowProductConfig['node_totals_scope'] })}
                  >
                    <option value="filtered">{t('flow.scopeFiltered')}</option>
                    <option value="thresholded">{t('flow.scopeThresholded')}</option>
                  </select>
                </div>
              </fieldset>

              <fieldset className="space-y-2 rounded-md border border-edge-subtle p-2.5">
                <legend className="px-1 text-meta font-semibold text-ink-secondary">{t('flow.topNTitle')}</legend>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label htmlFor="flow-top-n" className={labelCls}>{t('flow.topN')}</label>
                    <input
                      id="flow-top-n"
                      type="number"
                      min={1}
                      className={inputCls}
                      value={config.top_n}
                      onChange={(e) => patchConfig({ top_n: Math.max(1, Math.floor(Number(e.target.value) || 1)) })}
                    />
                  </div>
                  <div>
                    <label htmlFor="flow-min-weight" className={labelCls}>{t('flow.minWeight')}</label>
                    <input
                      id="flow-min-weight"
                      type="number"
                      min={0}
                      step="any"
                      className={inputCls}
                      value={config.min_weight}
                      onChange={(e) => patchConfig({ min_weight: Math.max(0, Number(e.target.value) || 0) })}
                    />
                  </div>
                </div>
              </fieldset>

              <fieldset className="space-y-2 rounded-md border border-edge-subtle p-2.5">
                <legend className="px-1 text-meta font-semibold text-ink-secondary">{t('flow.timeTitle')}</legend>
                <div>
                  <label htmlFor="flow-time-field" className={labelCls}>{t('flow.timeField')}</label>
                  <select
                    id="flow-time-field"
                    className={inputCls}
                    value={config.time_field ?? ''}
                    disabled={inspectFields.length === 0}
                    onChange={(e) => patchConfig({ time_field: e.target.value || null })}
                  >
                    <option value="">—</option>
                    {inspectFields.map((f) => (
                      <option key={f} value={f}>{f}</option>
                    ))}
                  </select>
                </div>
                {config.time_field && (
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label htmlFor="flow-time-start" className={labelCls}>{t('flow.timeStart')}</label>
                      <input
                        id="flow-time-start"
                        type="text"
                        className={inputCls}
                        value={config.time_start ?? ''}
                        onChange={(e) => patchConfig({ time_start: e.target.value || null })}
                      />
                    </div>
                    <div>
                      <label htmlFor="flow-time-end" className={labelCls}>{t('flow.timeEnd')}</label>
                      <input
                        id="flow-time-end"
                        type="text"
                        className={inputCls}
                        value={config.time_end ?? ''}
                        onChange={(e) => patchConfig({ time_end: e.target.value || null })}
                      />
                    </div>
                  </div>
                )}
                <div>
                  <label htmlFor="flow-category-field" className={labelCls}>{t('flow.categoryField')}</label>
                  <select
                    id="flow-category-field"
                    className={inputCls}
                    value={config.category_field ?? ''}
                    disabled={inspectFields.length === 0}
                    onChange={(e) => patchConfig({ category_field: e.target.value || null, category_values: null })}
                  >
                    <option value="">—</option>
                    {inspectFields.map((f) => (
                      <option key={f} value={f}>{f}</option>
                    ))}
                  </select>
                </div>
                {config.category_field && (
                  <div>
                    <label htmlFor="flow-category-values" className={labelCls}>{t('flow.categoryValues')}</label>
                    <input
                      id="flow-category-values"
                      type="text"
                      className={inputCls}
                      value={(config.category_values ?? []).join(',')}
                      onChange={(e) =>
                        patchConfig({
                          category_values: e.target.value
                            ? e.target.value.split(',').map((s) => s.trim()).filter(Boolean)
                            : null,
                        })
                      }
                    />
                  </div>
                )}
              </fieldset>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void runPreview()}
                  disabled={!sessionId || !config.od_table_ref || !config.origin_id_field || !config.origin_lng_field || !config.origin_lat_field || !config.destination_id_field || !config.destination_lng_field || !config.destination_lat_field || !config.weight_field || preview.status === 'running'}
                  className="flex-1 rounded-md border border-edge-subtle bg-surface-overlay py-1.5 text-body font-semibold text-ink transition-colors hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {preview.status === 'running' ? t('flow.previewRunning') : t('flow.previewButton')}
                </button>
                {applied && (
                  <button
                    type="button"
                    onClick={() => setEditing(false)}
                    className="rounded-md border border-edge-subtle px-2 py-1.5 text-meta font-medium text-ink-secondary hover:bg-surface-hover"
                  >
                    {t('flow.cancelEdit')}
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {preview.status === 'error' && (
        <p role="alert" className="rounded-md border border-edge-subtle bg-surface-sunken p-2 text-caption text-ink">
          {t('flow.previewFailed', { reason: preview.message })}
        </p>
      )}

      {previewData && (
        <div className="space-y-2 rounded-md border border-edge-subtle bg-surface-sunken p-2.5">
          <div className="flex items-center justify-between gap-2">
            <span className="rounded-sm bg-surface-overlay px-1.5 py-0.5 text-caption font-semibold text-ink-secondary">
              {t('flow.previewBadge')}
            </span>
            {preview.status === 'done' && preview.stale && (
              <span className="text-caption text-ink">{t('flow.previewStale')}</span>
            )}
          </div>

          {svg.status === 'done' && (
            <div
              role="img"
              aria-label={t('flow.svgAlt')}
              className="overflow-hidden rounded-md border border-edge-subtle bg-surface-overlay [&>svg]:h-auto [&>svg]:w-full"
              dangerouslySetInnerHTML={{ __html: svg.markup }}
            />
          )}
          {svg.status === 'failed' && <p className="text-caption text-ink-muted">{t('flow.svgFailed')}</p>}

          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-caption text-ink-muted">
            <div>{t('flow.statRowsRaw')}: {fmtNum(statsView.rowsRaw)}</div>
            <div>{t('flow.statRowsValid')}: {fmtNum(statsView.rowsValid)}</div>
            <div>{t('flow.statRowsFiltered')}: {fmtNum(statsView.rowsFiltered)}</div>
            <div>{t('flow.statEdgesAggregated')}: {fmtNum(statsView.edgesAggregated)}</div>
            <div>{t('flow.statEdgesThresholded')}: {fmtNum(statsView.edgesThresholded)}</div>
            <div>{t('flow.statEdgesDisplayed')}: {fmtNum(statsView.edgesDisplayed)}</div>
            <div>{t('flow.statGrossRetained')}: {fmtPct(statsView.grossRetained, t('flow.statUnknown'))}</div>
            <div>{t('flow.statNetCoverage')}: {fmtPct(statsView.netMagnitudeCoverage, t('flow.statUnknown'))}</div>
            <div>{t('flow.statNodes')}: {fmtNum(statsView.nodesDisplayed)}/{fmtNum(statsView.nodesTotal)}</div>
          </dl>

          {previewData.edge_ref && (
            <p className="truncate font-mono text-caption text-ink-disabled" title={`${previewData.edge_ref} · ${previewData.node_ref ?? ''}`}>
              {t('flow.refNote', { edgeRef: previewData.edge_ref, nodeRef: previewData.node_ref ?? '—' })}
            </p>
          )}
          <p className="text-caption text-ink-muted">{t('flow.curveNote')}</p>

          {(previewData.warnings?.length ?? 0) > 0 && (
            <div>
              <span className="text-caption font-semibold text-ink-secondary">{t('flow.warningsTitle')}</span>
              <ul className="list-inside list-disc text-caption text-ink-muted">
                {previewData.warnings!.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            </div>
          )}
          {(previewData.degradations?.length ?? 0) > 0 && (
            <div>
              <span className="text-caption font-semibold text-ink-secondary">{t('flow.degradationsTitle')}</span>
              <ul className="list-inside list-disc text-caption text-ink-muted">
                {previewData.degradations!.map((d, i) => <li key={i}>{d}</li>)}
              </ul>
            </div>
          )}
          {previewData.can_apply === false && (
            <div>
              <span className="text-caption font-semibold text-ink">{t('flow.blockingTitle')}</span>
              <p className="text-caption text-ink-muted">
                {(previewData.blocking_reasons ?? []).join(', ') || t('flow.emptyResult')}
              </p>
            </div>
          )}

          <button
            type="button"
            onClick={() => void runApply()}
            disabled={applying || preview.status !== 'done' || preview.stale || !previewData.can_apply || !previewData.product_ref || !previewData.family_id}
            className="w-full rounded-md py-1.5 text-body font-semibold text-ink-on-accent transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
            style={{
              background: 'linear-gradient(135deg, var(--agent-accent), color-mix(in srgb, var(--agent-accent) 87%, transparent))',
            }}
          >
            {applying ? t('flow.applying') : t('flow.applyButton')}
          </button>
          {applyError && <p role="alert" className="text-caption text-ink">{applyError}</p>}
        </div>
      )}

      <FlowFocusCard productRef={applied?.flow.product_ref ?? previewData?.product_ref ?? null} />
    </div>
  );
}

/* ── 焦点详情：SelectionContext transient 焦点 + /query 分页 ─────────────── */

function FlowFocusCard({ productRef }: { productRef: string | null }) {
  const t = useT();
  const selection = useSyncExternalStore(subscribeSelection, getSelection, () => null);
  const focus = selection?.properties?.flow_focus;
  const isNode = focus === 'node';
  const isEdge = focus === 'edge';
  const matched = Boolean(
    productRef && selection?.artifact_ref === productRef && (isNode || isEdge) && selection.selected_ids.length > 0,
  );

  const [direction, setDirection] = useState<FlowNodeDirection>('both');
  const [nodeData, setNodeData] = useState<FlowQueryNodeResponse | null>(null);
  const [edgeData, setEdgeData] = useState<FlowQueryEdgeResponse | null>(null);
  const [extraItems, setExtraItems] = useState<Record<string, unknown>[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [status, setStatus] = useState<'idle' | 'loading' | 'error' | 'missing' | 'stale'>('idle');
  const [message, setMessage] = useState('');
  const beginRequest = useGuardedRequest();

  const objectId = matched ? String(selection!.selected_ids[0]) : '';
  const kind = isNode ? 'node' : 'edge';

  useEffect(() => {
    setExtraItems([]);
    setNextOffset(null);
    setNodeData(null);
    setEdgeData(null);
    if (!matched || !objectId) {
      setStatus('idle');
      return;
    }
    const { sessionId, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId || !productRef) return;
    const guard = beginRequest();
    setStatus('loading');
    queryFlowProduct(
      sessionId,
      { product_ref: productRef, kind: kind as 'node' | 'edge', object_id: objectId, direction, offset: 0, limit: 50 },
      { ownerToken, signal: guard.signal },
    )
      .then((data) => {
        if (!guard.isCurrent()) return;
        if (data.kind === 'node') setNodeData(data);
        else setEdgeData(data);
        setNextOffset((data.kind === 'node' ? data.edges : data.members).next_offset);
        setStatus('idle');
      })
      .catch((err) => {
        if (!guard.isCurrent()) return;
        const detail = flowErrorDetail(err);
        if (detail?.code === 'FLOW_NODE_NOT_FOUND' || detail?.code === 'FLOW_EDGE_NOT_FOUND') {
          setStatus('missing');
        } else if (detail?.code === 'FLOW_SOURCE_STALE') {
          setStatus('stale');
        } else {
          setStatus('error');
          setMessage(describeApiError(err, ''));
        }
      });
    // direction 仅对节点焦点有意义；边焦点重查无副作用。
  }, [matched, objectId, kind, direction, productRef, beginRequest]);

  const loadMore = async () => {
    const page = nodeData?.edges ?? edgeData?.members;
    if (!page || nextOffset == null || !productRef) return;
    const { sessionId, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId) return;
    const guard = beginRequest();
    setStatus('loading');
    try {
      const data = await queryFlowProduct(
        sessionId,
        { product_ref: productRef, kind: kind as 'node' | 'edge', object_id: objectId, direction, offset: nextOffset, limit: page.limit },
        { ownerToken, signal: guard.signal },
      );
      if (!guard.isCurrent()) return;
      const items = (data.kind === 'node' ? data.edges : data.members).items;
      setExtraItems((prev) => [...prev, ...items]);
      setNextOffset((data.kind === 'node' ? data.edges : data.members).next_offset);
      setStatus('idle');
    } catch (err) {
      if (!guard.isCurrent()) return;
      setStatus('error');
      setMessage(describeApiError(err, ''));
    }
  };

  if (!matched) {
    return <p className="text-caption text-ink-muted">{t('flow.noSelection')}</p>;
  }

  const page = nodeData?.edges ?? edgeData?.members;
  const items = [...(page?.items ?? []), ...extraItems];

  return (
    <div className="space-y-2 rounded-md border border-edge-subtle bg-surface-sunken p-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-meta font-semibold text-ink-secondary">{t('flow.focusTitle')}</span>
        {isNode && (
          <div role="radiogroup" aria-label={t('flow.focusTitle')} className="flex gap-1">
            {(['inbound', 'outbound', 'both'] as const).map((d) => (
              <label key={d} className="flex items-center gap-1 text-caption text-ink-secondary">
                <input
                  type="radio"
                  name="flow-focus-direction"
                  className="h-3 w-3"
                  style={{ accentColor: 'var(--agent-accent, #16a34a)' }}
                  checked={direction === d}
                  onChange={() => setDirection(d)}
                />
                {d === 'inbound' ? t('flow.focusInbound') : d === 'outbound' ? t('flow.focusOutbound') : t('flow.focusBoth')}
              </label>
            ))}
          </div>
        )}
      </div>

      {isNode && nodeData && (
        <div className="space-y-1 text-caption text-ink-muted">
          <div className="font-semibold text-ink">
            {t('flow.focusNode', { name: String(nodeData.node.name ?? objectId) })}
          </div>
          <div>
            {t('flow.focusNodeTotals', {
              inbound: fmtNum(Number(nodeData.node.inbound ?? 0)),
              outbound: fmtNum(Number(nodeData.node.outbound ?? 0)),
              net: String(nodeData.node.net ?? '—'),
            })}
          </div>
        </div>
      )}

      {isEdge && edgeData && (
        <div className="space-y-1 text-caption text-ink-muted">
          <div className="font-semibold text-ink">{t('flow.focusEdge', { id: objectId })}</div>
          <div>{t('flow.edgeOrigin')}: {String(edgeData.edge.origin_node_id ?? '—')}</div>
          <div>{t('flow.edgeDestination')}: {String(edgeData.edge.destination_node_id ?? '—')}</div>
          <div>
            {t('flow.edgeWeight')}: {String(edgeData.edge.weight ?? '—')} · {t('flow.edgeGross')}: {String(edgeData.edge.gross ?? '—')} · {t('flow.edgeNet')}: {String(edgeData.edge.net ?? '—')}
          </div>
        </div>
      )}

      {status === 'loading' && <p className="text-caption text-ink-muted">{t('flow.focusLoading')}</p>}
      {status === 'missing' && <p className="text-caption text-ink">{t('flow.focusMissing')}</p>}
      {status === 'stale' && <p className="text-caption text-ink">{t('flow.focusStale')}</p>}
      {status === 'error' && <p role="alert" className="text-caption text-ink">{t('flow.focusError', { reason: message })}</p>}

      {items.length > 0 && (
        <div>
          <span className="text-caption font-semibold text-ink-secondary">
            {isNode
              ? t('flow.focusEdgesTitle', { count: page?.matched_count ?? items.length })
              : t('flow.focusMembersTitle', { count: page?.matched_count ?? items.length })}
          </span>
          <ul className="mt-1 space-y-0.5 text-caption text-ink-muted">
            {items.slice(0, 100).map((item, i) => (
              <li key={i} className="truncate font-mono" title={JSON.stringify(item)}>
                {isNode
                  ? `${String(item.origin_node_id ?? '')} → ${String(item.destination_node_id ?? '')} · ${String(item.weight ?? '')}`
                  : `${String(item.origin_node_id ?? item.o ?? '')} → ${String(item.destination_node_id ?? item.d ?? '')} · ${String(item.weight ?? item.value ?? '')}`}
              </li>
            ))}
          </ul>
          {nextOffset != null && (
            <button
              type="button"
              onClick={() => void loadMore()}
              disabled={status === 'loading'}
              className="mt-1 rounded-md border border-edge-subtle px-2 py-1 text-caption font-medium text-ink-secondary hover:bg-surface-hover disabled:opacity-40"
            >
              {t('flow.focusLoadMore')}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default FlowProductPanel;
