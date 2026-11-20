'use client';

/**
 * G46 联合探索工作台面板（G38 挂载模式）。
 *
 * 闭环：选源（HUD 层 → ref+revision）→ 添加图表（分类/区间）→ 条件 chips
 * （增删/单图清空/全部清空）→ 实时 preview（匹配计数 + facet 统计）→
 * 结果表（服务端分页）→ 命名保存视图 / 重开（stale 处理）/ 应用图表组件。
 *
 * 竞态（E23/E24）：condition generation + AbortController + session epoch
 * 共同拦截旧响应；去抖 250ms 合并快速刷选。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useT } from '@/lib/i18n/useT';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  asLinkedExplorationError,
  createLinkedExplorationClient,
} from '@/lib/linked-exploration/api';
import type {
  ChartPreview,
  ChartSpec,
  ExplorationCondition,
  ExplorationPlanJson,
  ExplorationView,
  InspectResponse,
  PreviewResponse,
} from '@/lib/linked-exploration/contracts';
import {
  bindExploration,
  clearAllConditions,
  clearChartConditions,
  getExploration,
  getExplorationEpoch,
  getExplorationGeneration,
  listCharts,
  listConditions,
  registerChart,
  removeChart,
  removeCondition,
  subscribeExploration,
} from '@/lib/linked-exploration/condition-store';
import { fetchFeaturePage } from '@/lib/data-plane/paged-features';
import { fetchSelectionFeatures } from '@/lib/api/selection-handles';
import { useLinkedExplorationStore } from '@/lib/hooks/use-linked-exploration';
import { setPreviewData } from '@/lib/linked-exploration/preview-store';

interface Props {
  sessionId?: string | null;
  ownerToken?: string | null;
}

/** 内部组件：sessionId 由 Mount 保证非空。 */
interface PanelProps {
  sessionId: string;
  ownerToken?: string | null;
}

export type { Props as ExplorationPanelProps };

type PreviewState =
  | { status: 'idle' }
  | { status: 'loading'; generation: number }
  | { status: 'ready'; data: PreviewResponse }
  | { status: 'error'; code: string; message: string };

type InspectState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; data: InspectResponse }
  | { status: 'error'; code: string; message: string };

let chartSeq = 0;
function nextChartId(): string {
  chartSeq += 1;
  return `chart-${Date.now().toString(36)}-${chartSeq}`;
}

/** 默认导出：读开关 store（命令面板/跳入入口）渲染工作台。 */
export default function LinkedExplorationMount(props: Props) {
  const open = useLinkedExplorationStore((s) => s.open);
  const close = useLinkedExplorationStore((s) => s.close);
  const { sessionId, ownerToken } = props;
  if (!open || !sessionId) return null;
  return (
    <div
      className="fixed inset-y-0 right-0 z-40 w-full max-w-md p-3"
      role="dialog"
      aria-label="linked exploration workbench"
    >
      <div className="relative h-full">
        <button
          type="button"
          onClick={close}
          className="absolute right-2 top-2 z-10 rounded border px-2 py-0.5 text-xs hover:bg-surface"
          aria-label="close linked exploration"
          data-testid="g46-close-panel"
        >✕</button>
        <ExplorationPanel sessionId={sessionId} ownerToken={ownerToken} />
      </div>
    </div>
  );
}

export function ExplorationPanel({ sessionId, ownerToken = null }: PanelProps) {
  const t = useT('linkedExploration');
  const client = useMemo(() => createLinkedExplorationClient(ownerToken), [ownerToken]);
  const layers = useHudStore((s) => s.layers);

  const [sourceLayerId, setSourceLayerId] = useState<string>('');
  const [inspect, setInspect] = useState<InspectState>({ status: 'idle' });
  const [preview, setPreview] = useState<PreviewState>({ status: 'idle' });
  const [views, setViews] = useState<ExplorationView[]>([]);
  const [viewTitle, setViewTitle] = useState('');
  const [notice, setNotice] = useState<{ kind: 'info' | 'error' | 'stale'; text: string } | null>(null);
  const [staleReason, setStaleReason] = useState<{ code: string; message: string } | null>(null);

  // ─── generation 拦截（E23/E24）──────────────────────────────────────────
  const generation = useSyncExternalStoreExploration();
  const previewSeq = useRef(0);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const sourceLayer = useMemo(
    () => layers.find((l) => l.id === sourceLayerId),
    [layers, sourceLayerId],
  );
  const sourceRef = sourceLayer?._refId ?? '';
  const mapspecLayerId = (sourceLayer as { _mapspecLayerId?: string } | undefined)?._mapspecLayerId
    ?? sourceLayerId;

  // ─── 源绑定 → condition-store（地图过滤投影目标） ────────────────────────
  useEffect(() => {
    if (!sourceRef || !sourceLayerId) {
      // 未选源：不动作者 store（E16：关闭面板/切 dock 不清条件；会话切换
      // 由 session-cursor 的 resetExplorationStore 统一处理）。
      return;
    }
    const st = getExploration();
    if (st && st.sourceRef === sourceRef) return; // 已绑定（重开面板不重置）
    const revision = 1;
    bindExploration(sourceRef, revision, mapspecLayerId);
  }, [sourceRef, sourceLayerId, mapspecLayerId]);

  // ─── inspect（源冻结 + 字段候选） ────────────────────────────────────────
  useEffect(() => {
    if (!sourceRef) {
      setInspect({ status: 'idle' });
      return;
    }
    const epoch = getExplorationEpoch();
    const abort = new AbortController();
    setInspect({ status: 'loading' });
    client.inspect(sessionId, sourceRef, abort.signal).then((data) => {
      if (getExplorationEpoch() !== epoch) return;
      setInspect({ status: 'ready', data });
      // 重绑 revision（权威锚）
      const st = getExploration();
      if (st && st.sourceRef === sourceRef) {
        bindExploration(sourceRef, data.anchor.content_revision, mapspecLayerId, { keep: true });
      }
    }).catch((err) => {
      if (abort.signal.aborted) return;
      const e = asLinkedExplorationError(err);
      setInspect({ status: 'error', code: e.code, message: e.message });
    });
    return () => abort.abort();
  }, [client, sessionId, sourceRef, mapspecLayerId]);

  // ─── 视图列表 ────────────────────────────────────────────────────────────
  const refreshViews = useCallback(() => {
    client.listViews(sessionId).then((r) => setViews(r.views ?? [])).catch(() => {});
  }, [client, sessionId]);
  useEffect(() => { refreshViews(); }, [refreshViews]);

  // ─── preview（组合条件求值；去抖 + generation 拦截） ──────────────────────
  const runPreview = useCallback((gen: number) => {
    if (!sourceRef) return;
    const epoch = getExplorationEpoch();
    const seq = ++previewSeq.current;
    const abort = new AbortController();
    setPreview({ status: 'loading', generation: gen });
    const charts = listCharts();
    const conditions = listConditions();
    client.preview(sessionId, sourceRef, charts, conditions, abort.signal).then((data) => {
      if (seq !== previewSeq.current) return;         // 旧响应（已发起新请求）
      if (getExplorationEpoch() !== epoch) return;    // 会话已切换
      if (getExplorationGeneration() !== gen) return; // 条件已再变 → 丢弃
      setPreview({ status: 'ready', data });
      setPreviewData(data); // 图表组件实例共享本次组合求值（单一请求点）
      setStaleReason(null);
    }).catch((err) => {
      if (seq !== previewSeq.current || abort.signal.aborted) return;
      const e = asLinkedExplorationError(err);
      setPreview({ status: 'error', code: e.code, message: e.message });
    });
  }, [client, sessionId, sourceRef]);

  useEffect(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => runPreview(generation), 250);
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
    };
  }, [generation, runPreview]);

  // ─── 图表管理 ────────────────────────────────────────────────────────────
  const [addField, setAddField] = useState('');
  const [addKind, setAddKind] = useState<'category' | 'range'>('category');
  const [addAgg, setAddAgg] = useState<'count' | 'sum' | 'mean' | 'min' | 'max'>('count');
  const [addValueField, setAddValueField] = useState('');

  const candidateFields = useMemo(() => {
    if (inspect.status !== 'ready') return [];
    return inspect.data.fields;
  }, [inspect]);

  const addChart = useCallback(() => {
    if (!addField) return;
    const chartId = nextChartId();
    const chart: ChartSpec = {
      chartId,
      kind: addKind,
      field: addField,
      aggregation: addAgg,
      valueField: addAgg !== 'count' ? (addValueField || undefined) : undefined,
      title: `${addField} · ${addKind === 'category' ? t('panel.kindCategory') : t('panel.kindRange')}`,
      bins: 10,
      layerId: mapspecLayerId,
    };
    registerChart(chart);
    setAddField('');
    setAddValueField('');
  }, [addField, addKind, addAgg, addValueField, mapspecLayerId, t]);

  // eslint-disable-next-line react-hooks/exhaustive-deps -- generation is the change signal
  const charts = useMemo(() => listCharts(), [generation]);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- generation is the change signal
  const conditions = useMemo(() => listConditions(), [generation]);

  // ─── 结果表（服务端分页；组合 where 下推） ────────────────────────────────
  const [tableRows, setTableRows] = useState<Array<Record<string, unknown>>>([]);
  const [tableCursor, setTableCursor] = useState<string | null>(null);
  const [tableTotal, setTableTotal] = useState<number | null>(null);
  const [tableState, setTableState] = useState<'idle' | 'loading' | 'error' | 'ready'>('idle');

  const combinedWhere = useMemo(
    () => (preview.status === 'ready' ? preview.data.combined_where : null),
    [preview],
  );
  const tableFields = useMemo(() => {
    const keys = new Set<string>();
    for (const row of tableRows.slice(0, 20)) {
      for (const k of Object.keys(row)) keys.add(k);
    }
    return Array.from(keys).slice(0, 8);
  }, [tableRows]);

  const activeHandle = useMemo(
    () => (preview.status === 'ready' ? preview.data.selection_handle?.handle ?? null : null),
    [preview],
  );

  const loadTable = useCallback((cursor: string | null) => {
    if (!sourceRef) return;
    setTableState('loading');
    const mergePage = (
      features: Array<Record<string, unknown>>,
      next: string | null,
      total: number | null,
    ) => {
      setTableRows((prev) => (cursor ? [...prev, ...features] : features));
      setTableCursor(next);
      setTableTotal(total);
      setTableState('ready');
    };
    // review P2-3：优先 where-scope 句柄分页（服务端权威完整集合；免
    // /features where 的 2KB JSON 帽 —— 16 条件×64 值的合法组合不再断裂）。
    // 无句柄（无条件）时走 where 下推自然序。
    if (activeHandle) {
      fetchSelectionFeatures({
        sessionId, refId: sourceRef, handle: activeHandle, limit: 50,
        cursor, ownerToken,
      }).then((page) => mergePage(
        // selection 端点返回完整 Feature 对象（与 /features 同形）——
        // 与回退路径同映射 .properties（review NEW-1）
        page.features.map((f) => ((f as { properties?: Record<string, unknown> }).properties ?? {})),
        page.nextCursor, page.count,
      )).catch(() => setTableState('error'));
      return;
    }
    const st = getExploration();
    const revision = st?.sourceRevision;
    fetchFeaturePage({
      sessionId,
      refId: sourceRef,
      revision,
      limit: 50,
      cursor: cursor ?? undefined,
      // fetchFeaturePage 的 where 是 JSON 字符串（服务端 allowlist 校验）
      where: combinedWhere ? JSON.stringify(combinedWhere) : undefined,
      ownerToken,
    }).then((page) => mergePage(
      page.features.map((f) => (f.properties ?? {}) as Record<string, unknown>),
      page.nextCursor ?? null, page.totalMatching ?? null,
    )).catch(() => setTableState('error'));
  }, [sessionId, sourceRef, ownerToken, combinedWhere, activeHandle]);

  useEffect(() => { loadTable(null); }, [loadTable]);

  // ─── 视图操作 ────────────────────────────────────────────────────────────
  const buildPlan = useCallback((): ExplorationPlanJson | null => {
    const st = getExploration();
    if (!st || inspect.status !== 'ready') return null;
    return {
      schema_version: 2,
      anchor: {
        ref_id: st.sourceRef,
        content_revision: inspect.data.anchor.content_revision,
        content_fingerprint: inspect.data.anchor.content_fingerprint ?? null,
      },
      charts: listCharts().map((c) => ({
        chart_id: c.chartId,
        kind: c.kind,
        field: c.field,
        aggregation: c.aggregation,
        value_field: c.valueField ?? null,
        title: c.title,
        bins: c.bins,
        layer_id: c.layerId ?? null,
      })),
      conditions: listConditions().map((c) => {
        if (c.kind === 'category') {
          return {
            kind: 'category', condition_id: c.conditionId, chart_id: c.chartId,
            field: c.field, values: c.values, include_null: c.includeNull,
          };
        }
        const out: Record<string, unknown> = {
          kind: 'range', condition_id: c.conditionId, chart_id: c.chartId, field: c.field,
        };
        for (const k of ['gte', 'gt', 'lte', 'lt'] as const) {
          if (c[k] !== undefined) out[k] = c[k];
        }
        return out;
      }),
      base_scope_note: '',
    };
  }, [inspect]);

  const saveCurrentView = useCallback(() => {
    const plan = buildPlan();
    if (!plan || !viewTitle.trim()) {
      setNotice({ kind: 'error', text: t('views.needTitleAndPlan') });
      return;
    }
    client.saveView(sessionId, viewTitle.trim(), plan).then(() => {
      setNotice({ kind: 'info', text: t('views.saved') });
      setViewTitle('');
      refreshViews();
    }).catch((err) => {
      const e = asLinkedExplorationError(err);
      setNotice({ kind: 'error', text: `${e.code}: ${e.message}` });
    });
  }, [buildPlan, client, sessionId, viewTitle, refreshViews, t]);

  const openSavedView = useCallback((viewId: string) => {
    client.openView(sessionId, viewId).then((res) => {
      if (res.state === 'stale') {
        setStaleReason(res.stale_reason ?? { code: 'STALE', message: '' });
        setNotice({ kind: 'stale', text: t('views.stalePrompt') });
        return;
      }
      // ready：恢复图表 + 条件到 store（同锚同代重放）
      const plan = res.plan;
      const charts: ChartSpec[] = (plan.charts ?? []).map((c) => ({
        chartId: String(c.chart_id),
        kind: (c.kind === 'range' ? 'range' : 'category'),
        field: String(c.field),
        aggregation: (c.aggregation as ChartSpec['aggregation']) ?? 'count',
        valueField: (c.value_field as string) || undefined,
        title: String(c.title ?? ''),
        bins: Number(c.bins ?? 10),
        layerId: (c.layer_id as string) || undefined,
      }));
      const conds: ExplorationCondition[] = (plan.conditions ?? []).map((c) => {
        if (c.kind === 'category') {
          return {
            kind: 'category' as const,
            conditionId: String(c.condition_id),
            chartId: String(c.chart_id),
            field: String(c.field),
            values: (c.values ?? []) as Array<string | number | boolean | null>,
            includeNull: Boolean(c.include_null),
          };
        }
        const rc: ExplorationCondition = {
          kind: 'range' as const,
          conditionId: String(c.condition_id),
          chartId: String(c.chart_id),
          field: String(c.field),
        } as ExplorationCondition;
        for (const k of ['gte', 'gt', 'lte', 'lt'] as const) {
          const v = c[k];
          if (typeof v === 'number') (rc as unknown as Record<string, unknown>)[k] = v;
        }
        return rc;
      });
      bindExploration(
        plan.anchor.ref_id,
        plan.anchor.content_revision,
        mapspecLayerId || plan.anchor.ref_id,
        { charts, conditions: conds },
      );
      // 源层联动：找 HUD 层里 ref 匹配的层
      const layer = useHudStore.getState().layers.find(
        (l) => l._refId === plan.anchor.ref_id,
      );
      if (layer) setSourceLayerId(layer.id);
      setNotice({ kind: 'info', text: t('views.reopened') });
    }).catch((err) => {
      const e = asLinkedExplorationError(err);
      setNotice({ kind: 'error', text: `${e.code}: ${e.message}` });
    });
  }, [client, sessionId, mapspecLayerId, t]);

  const applyViewToMap = useCallback((viewId: string) => {
    client.applyView(sessionId, viewId).then((res) => {
      setNotice({ kind: 'info', text: t('views.applied', { count: String(res.count) }) });
    }).catch((err) => {
      const e = asLinkedExplorationError(err);
      setNotice({ kind: 'error', text: `${e.code}: ${e.message}` });
    });
  }, [client, sessionId, t]);

  // ─── 渲染 ────────────────────────────────────────────────────────────────
  const vectorLayers = layers.filter((l) => l.type === 'vector');

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg border bg-panel text-sm" data-testid="g46-exploration-panel">
      <div className="border-b px-4 py-3">
        <h2 className="font-medium">{t('panel.title')}</h2>
        <p className="text-xs opacity-70">{t('panel.subtitle')}</p>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-3">
        {/* 源选择 */}
        <section>
          <label className="mb-1 block text-xs font-medium" htmlFor="g46-source-select">
            {t('panel.source')}
          </label>
          <select
            id="g46-source-select"
            className="w-full rounded border bg-transparent px-2 py-1"
            value={sourceLayerId}
            onChange={(e) => setSourceLayerId(e.target.value)}
            data-testid="g46-source-select"
          >
            <option value="">{t('panel.selectSource')}</option>
            {vectorLayers.map((l) => (
              <option key={l.id} value={l.id}>{l.name}</option>
            ))}
          </select>
          {inspect.status === 'ready' && (
            <p className="mt-1 text-xs opacity-70" data-testid="g46-source-anchor">
              {t('panel.anchor', {
                rev: String(inspect.data.anchor.content_revision),
                count: String(inspect.data.feature_count),
              })}
            </p>
          )}
          {inspect.status === 'loading' && <p className="mt-1 text-xs opacity-60">{t('panel.inspecting')}</p>}
          {inspect.status === 'error' && (
            <p className="mt-1 text-xs text-error" data-testid="g46-inspect-error">
              {inspect.code}: {inspect.message}
            </p>
          )}
        </section>

        {/* 图表管理 */}
        <section>
          <h3 className="mb-1 text-xs font-medium">{t('charts.section')}</h3>
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label={t('charts.field')}
              className="rounded border bg-transparent px-2 py-1"
              value={addField}
              onChange={(e) => setAddField(e.target.value)}
              data-testid="g46-add-field"
            >
              <option value="">{t('charts.selectField')}</option>
              {candidateFields.map((f) => (
                <option key={f.name} value={f.name}>{t('charts.fieldOption', { name: f.name, type: f.type })}</option>
              ))}
            </select>
            <select
              aria-label={t('charts.kind')}
              className="rounded border bg-transparent px-2 py-1"
              value={addKind}
              onChange={(e) => setAddKind(e.target.value as 'category' | 'range')}
              data-testid="g46-add-kind"
            >
              <option value="category">{t('panel.kindCategory')}</option>
              <option value="range">{t('panel.kindRange')}</option>
            </select>
            <select
              aria-label={t('charts.aggregation')}
              className="rounded border bg-transparent px-2 py-1"
              value={addAgg}
              onChange={(e) => setAddAgg(e.target.value as typeof addAgg)}
              data-testid="g46-add-agg"
            >
              <option value="count">count</option>
              <option value="sum">sum</option>
              <option value="mean">mean</option>
              <option value="min">min</option>
              <option value="max">max</option>
            </select>
            {addAgg !== 'count' && (
              <select
                aria-label={t('charts.valueField')}
                className="rounded border bg-transparent px-2 py-1"
                value={addValueField}
                onChange={(e) => setAddValueField(e.target.value)}
                data-testid="g46-add-value-field"
              >
                <option value="">{t('charts.selectValueField')}</option>
                {candidateFields.map((f) => (
                  <option key={f.name} value={f.name}>{f.name}</option>
                ))}
              </select>
            )}
            <button
              type="button"
              className="rounded border px-2 py-1 hover:bg-surface"
              onClick={addChart}
              disabled={!addField}
              data-testid="g46-add-chart"
            >
              {t('charts.add')}
            </button>
          </div>
          {charts.length > 0 && (
            <ul className="mt-2 space-y-1" data-testid="g46-chart-list">
              {charts.map((c) => (
                <li key={c.chartId} className="flex items-center justify-between rounded border px-2 py-1">
                  <span className="truncate">
                    <span className="mr-2 rounded bg-surface px-1 text-xs">
                      {c.kind === 'category' ? t('panel.kindCategory') : t('panel.kindRange')}
                    </span>
                    {c.field} · {c.aggregation}
                  </span>
                  <span className="flex gap-1">
                    <button
                      type="button" className="text-xs underline"
                      onClick={() => clearChartConditions(c.chartId)}
                      data-testid={`g46-clear-chart-${c.chartId}`}
                    >{t('conditions.clearChart')}</button>
                    <button
                      type="button" className="text-xs underline"
                      onClick={() => removeChart(c.chartId)}
                      data-testid={`g46-remove-chart-${c.chartId}`}
                    >{t('charts.remove')}</button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* 条件 chips */}
        <section>
          <div className="mb-1 flex items-center justify-between">
            <h3 className="text-xs font-medium">{t('conditions.section')}</h3>
            {conditions.length > 0 && (
              <button
                type="button" className="text-xs underline"
                onClick={() => clearAllConditions()}
                data-testid="g46-clear-all"
              >{t('conditions.clearAll')}</button>
            )}
          </div>
          {conditions.length === 0 ? (
            <p className="text-xs opacity-60">{t('conditions.empty')}</p>
          ) : (
            <ul className="flex flex-wrap gap-1" data-testid="g46-condition-chips">
              {conditions.map((cond) => (
                <ConditionChip key={cond.conditionId} cond={cond} t={t} />
              ))}
            </ul>
          )}
        </section>

        {/* 匹配信息 */}
        <section className="rounded border p-2" data-testid="g46-match-info">
          {preview.status === 'loading' && <p className="text-xs opacity-60">{t('preview.loading')}</p>}
          {preview.status === 'idle' && <p className="text-xs opacity-60">{t('preview.idle')}</p>}
          {preview.status === 'error' && (
            <p className="text-xs text-error" data-testid="g46-preview-error">
              {preview.code}: {preview.message}
            </p>
          )}
          {preview.status === 'ready' && (
            <div className="space-y-1">
              <p className="text-xs" data-testid="g46-match-count">
                {t('preview.matchLine', {
                  matched: String(preview.data.match_count),
                  total: String(preview.data.total),
                })}
                {preview.data.match_count_exact ? '' : t('preview.countInexact')}
              </p>
              <p className="text-xs opacity-70">{t('preview.digest', { digest: preview.data.conditions_digest })}</p>
              {preview.data.charts.map((c: ChartPreview) => (
                <p key={c.chart_id} className="text-xs opacity-70" data-testid={`g46-chart-stat-${c.chart_id}`}>
                  {t('preview.chartStatLine', { field: c.field, facet: String(c.facet.statistics.matched), full: String(c.full.statistics.matched) })}
                  {c.facet.statistics.truncated ? ` · ${t('preview.truncated')}` : ''}
                </p>
              ))}
              <p className="text-xs opacity-60">{t('preview.facetNote')}</p>
            </div>
          )}
        </section>

        {/* 结果表 */}
        {sourceRef && (
          <section>
            <h3 className="mb-1 text-xs font-medium">{t('table.section')}</h3>
            <div className="overflow-x-auto rounded border" data-testid="g46-result-table">
              {tableState === 'loading' && <p className="p-2 text-xs opacity-60">{t('table.loading')}</p>}
              {tableState === 'error' && <p className="p-2 text-xs text-error">{t('table.error')}</p>}
              {tableState === 'ready' && tableRows.length === 0 && (
                <p className="p-2 text-xs opacity-60" data-testid="g46-table-empty">{t('table.empty')}</p>
              )}
              {tableState === 'ready' && tableRows.length > 0 && (
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b">
                      {tableFields.map((f) => <th key={f} className="px-2 py-1 text-left font-medium">{f}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {tableRows.slice(0, 50).map((row, i) => (
                      <tr key={i} className="border-b last:border-0">
                        {tableFields.map((f) => (
                          <td key={f} className="px-2 py-1 truncate max-w-40">{String(row[f] ?? '')}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            {tableTotal !== null && (
              <p className="mt-1 text-xs opacity-70" data-testid="g46-table-total">
                {t('table.total', { n: String(tableTotal) })}
              </p>
            )}
            {tableCursor && (
              <button
                type="button" className="mt-1 text-xs underline"
                onClick={() => loadTable(tableCursor)}
                data-testid="g46-table-more"
              >{t('table.more')}</button>
            )}
          </section>
        )}

        {/* 视图管理 */}
        <section>
          <h3 className="mb-1 text-xs font-medium">{t('views.section')}</h3>
          <div className="flex gap-2">
            <input
              type="text"
              className="flex-1 rounded border bg-transparent px-2 py-1"
              placeholder={t('views.titlePlaceholder')}
              value={viewTitle}
              onChange={(e) => setViewTitle(e.target.value)}
              data-testid="g46-view-title"
              aria-label={t('views.titlePlaceholder')}
            />
            <button
              type="button" className="rounded border px-2 py-1 hover:bg-surface"
              onClick={saveCurrentView}
              data-testid="g46-save-view"
            >{t('views.save')}</button>
          </div>
          {views.length > 0 && (
            <ul className="mt-2 space-y-1" data-testid="g46-view-list">
              {views.map((v) => (
                <li key={v.view_id} className="flex items-center justify-between rounded border px-2 py-1">
                  <span className="truncate">
                    {v.title}
                    <span className="ml-2 text-xs opacity-60">r{v.revision} · rev{v.source_revision}</span>
                  </span>
                  <span className="flex gap-1">
                    <button type="button" className="text-xs underline" onClick={() => openSavedView(v.view_id)} data-testid={`g46-open-view-${v.view_id}`}>{t('views.open')}</button>
                    <button type="button" className="text-xs underline" onClick={() => applyViewToMap(v.view_id)} data-testid={`g46-apply-view-${v.view_id}`}>{t('views.apply')}</button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {staleReason && (
          <p className="rounded border border-warning p-2 text-xs" data-testid="g46-stale-reason">
            {t('views.staleReason', { code: staleReason.code, msg: staleReason.message })}
          </p>
        )}
        {notice && (
          <p
            className={`rounded border p-2 text-xs ${notice.kind === 'error' ? 'border-error text-error' : 'border-default'}`}
            data-testid="g46-notice"
            role="status"
          >{notice.text}</p>
        )}
      </div>
    </div>
  );
}

function ConditionChip({
  cond, t,
}: {
  cond: ExplorationCondition;
  t: (k: string, vars?: Record<string, string>) => string;
}) {
  let text: string;
  if (cond.kind === 'category') {
    const parts = cond.values.map((v) => String(v));
    if (cond.includeNull) parts.push('null');
    text = `${cond.field} ∈ {${parts.join(', ')}}`;
  } else {
    const segs: string[] = [];
    if (cond.gte !== undefined) segs.push(`≥ ${cond.gte}`);
    if (cond.gt !== undefined) segs.push(`> ${cond.gt}`);
    if (cond.lte !== undefined) segs.push(`≤ ${cond.lte}`);
    if (cond.lt !== undefined) segs.push(`< ${cond.lt}`);
    text = `${cond.field} ${segs.join(' ')}`;
  }
  return (
    <li className="flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs" data-testid="g46-condition-chip">
      <span className="max-w-56 truncate">{text}</span>
      <button
        type="button"
        aria-label={t('conditions.remove', { id: cond.conditionId })}
        className="opacity-60 hover:opacity-100"
        onClick={() => removeCondition(cond.conditionId)}
        data-testid={`g46-remove-condition-${cond.conditionId}`}
      >✕</button>
    </li>
  );
}

/** useSyncExternalStore hook for exploration generation（react 导入在顶部）。 */
function useSyncExternalStoreExploration(): number {
  return React.useSyncExternalStore(subscribeExploration, getExplorationGeneration);
}
