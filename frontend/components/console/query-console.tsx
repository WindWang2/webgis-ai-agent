'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Database, Play, Map as MapIcon, History, Trash2, BookmarkPlus, X, Zap } from 'lucide-react';
import { dataFabricApi, type CatalogItem, type QueryResult } from '@/lib/api/data-fabric';
import type { QueryPlanInfo } from '@/lib/api/data-fabric';
import { describeApiError } from '@/lib/api/transport';
import { useToastStore } from '@/components/ui/toast';
import { useHudStore } from '@/lib/store/useHudStore';
import { TabularDataGrid } from '@/components/explorer/tabular-data-grid';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { useQueryConsoleStore } from '@/lib/hooks/use-query-console';
import { useT } from '@/lib/i18n/useT';
import { SqlEditor } from './sql-editor';
import { guardFilterText } from '@/lib/console/guard';
import {
  clearHistory,
  appendHistory,
  deleteSample,
  loadHistory,
  loadSamples,
  saveSample,
  toQuerySpec,
  DEFAULT_SPEC,
  type ConsoleSpec,
  type QueryHistoryEntry,
  type SampleEntry,
} from '@/lib/console/samples';

/**
 * 高级查询控制台（ADR-0147，P2）。
 *
 * 数据面：dataFabricApi.query/explain/materialize（P0 勘察 §3.1 契约）。
 * 诚实披露：执行前可 explain（dry-run）；执行后展示 metadata.query_plan
 * （pushdown/本地回退/混合 + 回退原因 + 警告），不美化不吞掉。
 * 危险守卫：lib/console/guard.ts（只读端点，写类语句前置拦截）。
 */

interface QueryConsoleProps {
  sessionId?: string | null;
  ownerToken?: string | null;
}

function readQueryPlan(result: QueryResult | null): QueryPlanInfo | null {
  const meta = result?.metadata as Record<string, unknown> | undefined;
  const plan = meta?.['query_plan'];
  return plan && typeof plan === 'object' ? (plan as QueryPlanInfo) : null;
}

export function QueryConsole({ sessionId, ownerToken }: QueryConsoleProps): React.ReactElement | null {
  const t = useT('console');
  const open = useQueryConsoleStore((s) => s.open);
  const presetTargetId = useQueryConsoleStore((s) => s.targetId);
  const close = useQueryConsoleStore((s) => s.close);

  // ── 目标选择 ──
  const [searchText, setSearchText] = useState('');
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [target, setTarget] = useState<CatalogItem | null>(null);

  // ── 编辑器 ──
  const [spec, setSpec] = useState<ConsoleSpec>(DEFAULT_SPEC);
  const [searchParams, setSearchParams] = useState<{ text: string; nonce: number }>({
    text: '',
    nonce: 0,
  });

  // ── 执行态 ──
  const [explaining, setExplaining] = useState(false);
  const [explainLines, setExplainLines] = useState<string[] | null>(null);
  const [explainError, setExplainError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [toMapLoading, setToMapLoading] = useState(false);

  // ── 历史 / 样例 ──
  const [history, setHistory] = useState<QueryHistoryEntry[]>([]);
  const [samples, setSamples] = useState<SampleEntry[]>([]);
  const [showSamples, setShowSamples] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const guard = useMemo(() => guardFilterText(spec.where), [spec.where]);
  const queryPlan = readQueryPlan(result);

  useDialogFocus({ open, containerRef, onEscape: close, initialFocusSelector: '[data-console-focus]' });

  // 打开时复位执行态、载入历史/样例、应用预选目标
  useEffect(() => {
    if (!open) return;
    setResult(null);
    setRunError(null);
    setExplainLines(null);
    setExplainError(null);
    setHistory(loadHistory());
    setSamples(loadSamples());
    if (presetTargetId) {
      setCatalogLoading(true);
      dataFabricApi
        .getCatalogItem(presetTargetId)
        .then((item) => setTarget(item))
        .catch(() => setTarget(null))
        .finally(() => setCatalogLoading(false));
    }
  }, [open, presetTargetId]);

  // 目录搜索（防抖 + abort）
  useEffect(() => {
    if (!open || target) return;
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setCatalogLoading(true);
    const t = setTimeout(() => {
      dataFabricApi
        .listSpatialCatalog({ q: searchParams.text || undefined, limit: 30, signal: controller.signal })
        .then((res) => setItems(res.items))
        .catch((_err: unknown) => {
          if (!controller.signal.aborted) setItems([]);
        })
        .finally(() => {
          if (!controller.signal.aborted) setCatalogLoading(false);
        });
    }, 300);
    return () => {
      clearTimeout(t);
      controller.abort();
    };
  }, [searchParams, open, target]);

  const handleSelectTarget = useCallback((item: CatalogItem) => {
    setTarget(item);
    setResult(null);
    setRunError(null);
    setExplainLines(null);
    setExplainError(null);
  }, []);

  const handleExplain = useCallback(async () => {
    if (!target) return;
    setExplaining(true);
    setExplainError(null);
    try {
      const res = await dataFabricApi.explainCatalogItem(target.id, toQuerySpec(spec));
      setExplainLines(Array.isArray(res.explain) ? res.explain : [String(res.explain ?? '')]);
    } catch (err) {
      setExplainLines(null);
      setExplainError(describeApiError(err, t('explainFailed')));
    } finally {
      setExplaining(false);
    }
  }, [target, spec, t]);

  const handleRun = useCallback(async () => {
    if (!target) return;
    if (!guard.ok) return;
    setRunning(true);
    setRunError(null);
    const entryId = `qh-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    try {
      const res = await dataFabricApi.queryCatalogItem(target.id, toQuerySpec(spec));
      setResult(res);
      setHistory(
        appendHistory({
          id: entryId,
          ts: Date.now(),
          targetId: target.id,
          targetTitle: target.title || target.name,
          spec,
          status: 'ok',
          summary: res.truncated
            ? t('historySummaryTruncated', { count: res.returned_count ?? res.features?.length ?? 0 })
            : t('historySummary', { count: res.returned_count ?? res.features?.length ?? 0 }),
        }),
      );
    } catch (err) {
      setResult(null);
      const msg = describeApiError(err, t('queryFailed'));
      setRunError(msg);
      setHistory(
        appendHistory({
          id: entryId,
          ts: Date.now(),
          targetId: target.id,
          targetTitle: target.title || target.name,
          spec,
          status: 'error',
          summary: msg.slice(0, 80),
        }),
      );
    } finally {
      setRunning(false);
    }
  }, [target, spec, guard, t]);

  /** 结果上图：以当前 QuerySpec materialize → ref 承载层 → 按需水合。 */
  // F03 同款跨会话守卫：materialize/水合的 await 期间用户切换会话时，
  // 旧会话的图层不得写进新会话（新会话已清空 layers，写入即幽灵图层，
  // 其 _refId 在新会话不可水合）。
  const sessionIdRef = useRef(sessionId);
  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  const handleToMap = useCallback(async () => {
    if (!target || !result) return;
    if (!sessionId) {
      useToastStore.getState().addToast(t('noSessionToast'), 'error');
      return;
    }
    const sid = sessionId;
    setToMapLoading(true);
    try {
      const res = await dataFabricApi.materializeCatalogItem({
        session_id: sid,
        catalog_item_id: target.id,
        query_spec: toQuerySpec(spec),
        ownerToken,
      });
      if (sessionIdRef.current !== sid) return;
      const layerId = `df-${target.id}-${Date.now().toString(36)}`;
      const { addLayer, updateLayer } = useHudStore.getState();
      addLayer({
        id: layerId,
        name: t('layerName', { name: target.title || target.name }),
        type: 'vector',
        visible: true,
        opacity: 1,
        group: 'reference',
        source: { type: 'FeatureCollection', features: [], metadata: { ref_id: res.ref_id } },
        _refId: res.ref_id,
        style: { color: '#16a34a' },
      });
      useToastStore.getState().addToast(t('materializedToast', { count: res.feature_count }), 'success');
      try {
        const geojson = await dataFabricApi.fetchRefGeoJSON(res.ref_id, sid, { ownerToken });
        if (sessionIdRef.current !== sid) return;
        if (geojson && (geojson.type === 'FeatureCollection' || Array.isArray(geojson.features))) {
          updateLayer(layerId, { source: geojson });
        }
      } catch {
        useToastStore.getState().addToast(t('hydrateFailedToast'), 'warning');
      }
    } catch (err) {
      const msg = describeApiError(err, t('materializeFailed'));
      useToastStore.getState().addToast(msg, 'error');
    } finally {
      setToMapLoading(false);
    }
  }, [target, result, sessionId, ownerToken, spec, t]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-start justify-center bg-black/40 p-4 pt-[6vh]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
      data-testid="query-console-overlay"
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label={t('title')}
        data-testid="query-console"
        className="flex max-h-[86vh] w-full max-w-[980px] flex-col overflow-hidden rounded-lg border border-edge-subtle bg-surface-raised shadow-2xl"
      >
        {/* 头部 */}
        <div className="flex shrink-0 items-center justify-between border-b border-edge-subtle px-4 py-2.5">
          <h2 className="flex items-center gap-2 text-body-md font-semibold text-ink">
            <Database size={15} aria-hidden />
            {t('title')}
          </h2>
          <button
            type="button"
            data-console-focus
            onClick={close}
            aria-label={t('closeAria')}
            className="rounded-sm p-1 text-ink-secondary hover:bg-surface-hover hover:text-ink"
          >
            <X size={16} aria-hidden />
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          {/* 左栏：目标选择 + 历史/样例 */}
          <aside className="flex w-[260px] shrink-0 flex-col overflow-y-auto border-r border-edge-subtle p-3">
            {!target ? (
              <>
                <label className="pb-1 text-caption font-medium text-ink-muted" htmlFor="console-catalog-search">
                  {t('selectTarget')}
                </label>
                <input
                  id="console-catalog-search"
                  value={searchText}
                  onChange={(e) => {
                    setSearchText(e.target.value);
                    setSearchParams({ text: e.target.value, nonce: Date.now() });
                  }}
                  placeholder={t('searchPlaceholder')}
                  className="mb-2 w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-body-sm text-ink outline-none placeholder:text-ink-muted focus:border-status-accent"
                />
                {catalogLoading ? (
                  <p className="px-1 py-2 text-caption text-ink-muted">{t('catalogLoading')}</p>
                ) : items.length === 0 ? (
                  <p className="px-1 py-2 text-caption text-ink-muted">{t('noCatalogMatch')}</p>
                ) : (
                  <ul className="space-y-1" data-testid="console-catalog-list">
                    {items.map((item) => (
                      <li key={item.id}>
                        <button
                          type="button"
                          onClick={() => handleSelectTarget(item)}
                          className="w-full rounded-md px-2 py-1.5 text-left text-body-sm text-ink-secondary hover:bg-surface-hover hover:text-ink"
                        >
                          <span className="block truncate font-medium">{item.title || item.name}</span>
                          <span className="block truncate text-caption text-ink-muted">
                            {item.geometry_type ?? item.feature_type ?? ''} {item.availability === 'unavailable' ? t('unavailableBadge') : ''}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            ) : (
              <>
                <div className="rounded-md border border-edge-subtle bg-surface-sunken p-2" data-testid="console-target">
                  <p className="truncate text-body-sm font-medium text-ink">{target.title || target.name}</p>
                  <p className="truncate text-caption text-ink-muted">
                    {target.geometry_type ?? ''} {target.crs ? `· ${target.crs}` : ''}
                  </p>
                  <button
                    type="button"
                    onClick={() => {
                      setTarget(null);
                      setSearchText('');
                      setSearchParams({ text: '', nonce: Date.now() });
                    }}
                    className="mt-1 text-caption text-status-accent hover:underline"
                  >
                    {t('changeTarget')}
                  </button>
                </div>
                <button
                  type="button"
                  onClick={() => setShowSamples((v) => !v)}
                  className="mt-3 rounded-md px-2 py-1.5 text-left text-body-sm text-ink-secondary hover:bg-surface-hover hover:text-ink"
                  aria-expanded={showSamples}
                >
                  {t('samples')}
                </button>
                {showSamples ? (
                  <ul className="mb-2 space-y-1" data-testid="console-samples">
                    {samples.map((s) => (
                      <li key={s.id} className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => setSpec(s.spec)}
                          title={s.description}
                          className="min-w-0 flex-1 truncate rounded-md px-2 py-1 text-left text-body-sm text-ink-secondary hover:bg-surface-hover hover:text-ink"
                        >
                          {s.name}
                          {s.builtin ? '' : ' ·'}
                        </button>
                        {!s.builtin ? (
                          <button
                            type="button"
                            aria-label={t('deleteSampleAria', { name: s.name })}
                            onClick={() => setSamples(deleteSample(s.id))}
                            className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-status-critical"
                          >
                            <Trash2 size={12} aria-hidden />
                          </button>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : null}
                <div className="flex items-center justify-between px-2 py-1.5">
                  <span className="flex items-center gap-1 text-caption font-medium text-ink-muted">
                    <History size={12} aria-hidden /> {t('historyTitle')}
                  </span>
                  {history.length > 0 ? (
                    <button
                      type="button"
                      aria-label={t('clearHistoryAria')}
                      onClick={() => {
                        clearHistory();
                        setHistory([]);
                      }}
                      className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-status-critical"
                    >
                      <Trash2 size={12} aria-hidden />
                    </button>
                  ) : null}
                </div>
                {history.length === 0 ? (
                  <p className="px-2 text-caption text-ink-muted">{t('noHistory')}</p>
                ) : (
                  <ul className="space-y-1" data-testid="console-history">
                    {history.slice(0, 10).map((h) => (
                      <li key={h.id}>
                        <button
                          type="button"
                          onClick={() => {
                            setSpec(h.spec);
                            if (h.targetId !== target.id) {
                              dataFabricApi
                                .getCatalogItem(h.targetId)
                                .then(handleSelectTarget)
                                .catch(() => {});
                            }
                          }}
                          className="w-full rounded-md px-2 py-1 text-left hover:bg-surface-hover"
                        >
                          <span
                            className={`block truncate text-body-sm ${
                              h.status === 'ok' ? 'text-ink-secondary' : 'text-status-critical'
                            }`}
                          >
                            {h.summary ?? h.spec.where ?? t('historyNoFilter')}
                          </span>
                          <span className="block truncate text-caption text-ink-muted">
                            {h.targetTitle} · {new Date(h.ts).toLocaleTimeString('zh-CN')}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </aside>

          {/* 右栏：编辑器 + 结果 */}
          <div className="flex min-w-0 flex-1 flex-col overflow-y-auto p-3">
            {!target ? (
              <div className="flex flex-1 items-center justify-center text-body-sm text-ink-muted">
                {t('selectTargetHint')}
              </div>
            ) : (
              <>
                {/* 编辑器 */}
                <div className="grid grid-cols-2 gap-2">
                  <label className="col-span-2 text-caption font-medium text-ink-muted" htmlFor="console-where">
                    {t('whereLabel')}
                  </label>
                  <div className="col-span-2">
                    <SqlEditor
                      id="console-where"
                      testId="console-where"
                      value={spec.where}
                      onChange={(next) => setSpec((s) => ({ ...s, where: next }))}
                      rows={3}
                      invalid={Boolean(spec.where) && !guard.ok}
                      placeholder={t('wherePlaceholder')}
                    />
                    {spec.where && !guard.ok ? (
                      <p role="alert" className="mt-1 text-caption text-status-critical" data-testid="console-guard">
                        {guard.reason}
                      </p>
                    ) : null}
                  </div>
                  <label className="text-caption font-medium text-ink-muted" htmlFor="console-fields">
                    {t('fieldsLabel')}
                  </label>
                  <label className="text-caption font-medium text-ink-muted" htmlFor="console-limit">
                    {t('limitLabel')}
                  </label>
                  <input
                    id="console-fields"
                    value={spec.fields}
                    onChange={(e) => setSpec((s) => ({ ...s, fields: e.target.value }))}
                    placeholder="name, value"
                    className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 font-mono text-body-sm text-ink outline-none focus:border-status-accent"
                  />
                  <input
                    id="console-limit"
                    type="number"
                    min={1}
                    max={2000}
                    value={spec.limit}
                    onChange={(e) =>
                      setSpec((s) => ({ ...s, limit: Math.max(1, Math.min(2000, Number(e.target.value) || 100)) }))
                    }
                    className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 font-mono text-body-sm text-ink outline-none focus:border-status-accent"
                  />
                  <label className="text-caption font-medium text-ink-muted" htmlFor="console-order">
                    {t('orderLabel')}
                  </label>
                  <label className="text-caption font-medium text-ink-muted" htmlFor="console-mode">
                    {t('modeLabel')}
                  </label>
                  <input
                    id="console-order"
                    value={spec.order}
                    onChange={(e) => setSpec((s) => ({ ...s, order: e.target.value }))}
                    placeholder="value DESC"
                    className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 font-mono text-body-sm text-ink outline-none focus:border-status-accent"
                  />
                  <select
                    id="console-mode"
                    value={spec.resultMode}
                    onChange={(e) => setSpec((s) => ({ ...s, resultMode: e.target.value as ConsoleSpec['resultMode'] }))}
                    className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-body-sm text-ink outline-none focus:border-status-accent"
                  >
                    <option value="features">{t('modeFeatures')}</option>
                    <option value="statistics">{t('modeStatistics')}</option>
                    <option value="sample">{t('modeSample')}</option>
                  </select>
                </div>

                {/* 动作条 */}
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void handleRun()}
                    disabled={running || !guard.ok}
                    className="inline-flex items-center gap-1.5 rounded-md bg-status-accent px-3 py-1.5 text-body-sm font-semibold text-ink-on-accent transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                    data-testid="console-run"
                  >
                    <Play size={13} aria-hidden />
                    {running ? t('running') : t('run')}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleExplain()}
                    disabled={explaining}
                    className="inline-flex items-center gap-1.5 rounded-md border border-edge-subtle px-3 py-1.5 text-body-sm text-ink-secondary hover:bg-surface-hover hover:text-ink disabled:opacity-60"
                    data-testid="console-explain"
                  >
                    <Zap size={13} aria-hidden />
                    {t('explain')}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleToMap()}
                    disabled={!result || toMapLoading}
                    className="inline-flex items-center gap-1.5 rounded-md border border-edge-subtle px-3 py-1.5 text-body-sm text-ink-secondary hover:bg-surface-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-60"
                    data-testid="console-to-map"
                  >
                    <MapIcon size={13} aria-hidden />
                    {toMapLoading ? t('materializing') : t('toMap')}
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      setSamples(
                        saveSample({
                          id: `sample-${Date.now().toString(36)}`,
                          name: spec.where.trim().slice(0, 24) || t('unnamedSample'),
                          spec,
                        }),
                      )
                    }
                    className="inline-flex items-center gap-1.5 rounded-md border border-edge-subtle px-3 py-1.5 text-body-sm text-ink-secondary hover:bg-surface-hover hover:text-ink"
                    title={t('saveSampleTitle')}
                  >
                    <BookmarkPlus size={13} aria-hidden />
                    {t('saveSample')}
                  </button>
                </div>

                {/* explain 披露 */}
                {explainError ? (
                  <p role="alert" className="mt-2 text-body-sm text-status-critical">
                    {explainError}
                  </p>
                ) : null}
                {explainLines ? (
                  <div className="mt-2 rounded-md border border-edge-subtle bg-surface-sunken p-2" data-testid="console-explain-result">
                    <p className="text-caption font-medium text-ink-muted">{t('planTitle')}</p>
                    <ul className="mt-1 space-y-0.5">
                      {explainLines.map((line, i) => (
                        <li key={i} className="whitespace-pre-wrap font-mono text-caption text-ink-secondary">
                          {line}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {/* 执行错误 */}
                {runError ? (
                  <p role="alert" className="mt-2 text-body-sm text-status-critical" data-testid="console-run-error">
                    {runError}
                  </p>
                ) : null}

                {/* pushdown 披露（诚实展示，缺披露就说没披露） */}
                {result ? (
                  <div className="mt-3" data-testid="console-disclosure">
                    <div className="flex flex-wrap items-center gap-2 text-caption text-ink-secondary">
                      <span
                        className={`rounded-pill px-2 py-0.5 font-medium ${
                          queryPlan?.execution_mode === 'pushdown'
                            ? 'bg-status-success-soft text-status-success'
                            : queryPlan?.execution_mode === 'local_fallback'
                              ? 'bg-status-warning-soft text-status-warning'
                              : 'bg-surface-sunken text-ink-muted'
                        }`}
                        data-testid="console-execution-mode"
                      >
                        {queryPlan?.execution_mode === 'pushdown'
                          ? t('modePushdown')
                          : queryPlan?.execution_mode === 'local_fallback'
                            ? t('modeLocalFallback')
                            : queryPlan?.execution_mode === 'hybrid'
                              ? t('modeHybrid')
                              : t('modeUndisclosed')}
                      </span>
                      <span>{t('returnedRows', { count: result.returned_count ?? result.features?.length ?? 0 })}</span>
                      {typeof result.total_matching === 'number' ? <span>{t('matchedRows', { count: result.total_matching })}</span> : null}
                      {typeof result.execution_time_seconds === 'number' ? (
                        <span>{result.execution_time_seconds.toFixed(3)}s</span>
                      ) : null}
                      {result.truncated ? <span className="text-status-warning">{t('truncated')}</span> : null}
                      {result.is_demo ? <span className="text-status-warning">{t('demoData')}</span> : null}
                    </div>
                    {queryPlan ? (
                      <ul className="mt-1 space-y-0.5 text-caption text-ink-muted">
                        {queryPlan.pushed_filters?.length ? <li>{t('pushedFilters', { filters: queryPlan.pushed_filters.join(t('listSeparator')) })}</li> : null}
                        {queryPlan.local_filters?.length ? <li>{t('localFilters', { filters: queryPlan.local_filters.join(t('listSeparator')) })}</li> : null}
                        {queryPlan.fallback_reason ? <li>{t('fallbackReason', { reason: queryPlan.fallback_reason })}</li> : null}
                        {queryPlan.warnings?.map((w, i) => (
                          <li key={i} className="text-status-warning">
                            {t('warning', { message: w })}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="mt-1 text-caption text-ink-muted">{t('noPlanDisclosure')}</p>
                    )}
                  </div>
                ) : null}

                {/* 结果表 */}
                {result ? (
                  <div className="mt-2 min-h-0 flex-1" data-testid="console-result">
                    <TabularDataGrid data={result} enableSearch enableSort enableRowCopy defaultPageSize={10} />
                  </div>
                ) : (
                  <div className="mt-3 flex flex-1 items-center justify-center rounded-md border border-dashed border-edge-subtle py-8 text-body-sm text-ink-muted">
                    {t('emptyResult')}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
