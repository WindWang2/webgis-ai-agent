'use client';

/**
 * 只读地图发布读者页（04 线 WP3/WP4）—— /p/[code]。
 *
 * 读者产品中心：无编辑工作台、无 Agent 历史、无写工具、无会话凭据。
 * 数据来源 = 发布快照的授权投影（reader doc + reader map spec + 白名单
 * 分页/统计端点）。无 WebGL 时提供等价数据表视图；键盘/读屏语义见
 * aria 属性与 announce 通道。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { Map as RNMap, NavigationControl, useMap } from 'react-map-gl/maplibre';
import type { MapRef } from 'react-map-gl/maplibre';
import type { StyleSpecification } from 'maplibre-gl';

import { compileMapSpec } from '@/lib/mapspec-compiler';
import { useT } from '@/lib/i18n/useT';
import { describeApiError, isApiError } from '@/lib/api/transport';
import {
  fetchReaderDoc,
  fetchReaderFeatures,
  fetchReaderMap,
  fetchReaderSummary,
} from '@/lib/publication/reader-api';
import {
  describePredicates,
  predicatesToMapLibreFilter,
  predicatesToWhere,
  predicatesWithinWhitelist,
} from '@/lib/publication/reader-filter';
import { useReaderStore } from '@/lib/publication/reader-store';
import type {
  ReaderDoc,
  ReaderMapPayload,
  ReaderPredicate,
} from '@/lib/publication/reader-types';

const MapErrorBoundary = dynamic(
  () => import('@/components/map/map-error-boundary').then((m) => m.MapErrorBoundary),
  { ssr: false },
);

function detectWebGl(): boolean {
  if (typeof document === 'undefined') return false;
  try {
    const canvas = document.createElement('canvas');
    return Boolean(
      canvas.getContext('webgl2') ||
      canvas.getContext('webgl') ||
      canvas.getContext('experimental-webgl'),
    );
  } catch {
    return false;
  }
}

/** 地图内子组件：应用筛选（MapLibre filter）与显隐。 */
function ReaderFilterApplier({
  compiledLayerIds,
}: {
  compiledLayerIds: string[];
}) {
  const { current: mapRef } = useMap();
  const visibleLayers = useReaderStore((s) => s.visibleLayers);
  const predicates = useReaderStore((s) => s.predicates);
  const filterExpr = useMemo(() => predicatesToMapLibreFilter(predicates), [predicates]);

  useEffect(() => {
    const map = mapRef?.getMap();
    if (!map) return;
    for (const layerId of compiledLayerIds) {
      const visible = visibleLayers[layerId] ?? true;
      try {
        if (map.getLayoutProperty(layerId, 'visibility') !== (visible ? 'visible' : 'none')) {
          map.setLayoutProperty(layerId, 'visibility', visible ? 'visible' : 'none');
        }
        if (filterExpr) {
          map.setFilter(layerId, filterExpr as never);
        } else {
          map.setFilter(layerId, null);
        }
      } catch {
        // 图层可能不存在于编译产物（如 background）；显隐/筛选跳过。
      }
    }
  }, [mapRef, compiledLayerIds, visibleLayers, filterExpr]);
  return null;
}

export function PublicationReaderView({ code }: { code: string }) {
  const t = useT('publication');
  const [doc, setDoc] = useState<ReaderDoc | null>(null);
  const [mapPayload, setMapPayload] = useState<ReaderMapPayload | null>(null);
  const [errorText, setErrorText] = useState('');
  const [activeTab, setActiveTab] = useState<'legend' | 'table' | 'stats' | 'about'>('legend');
  const [tablePage, setTablePage] = useState<{ cursor: string | null; rows: { properties: Record<string, unknown>; id?: string | number }[]; pageNo: number }>({ cursor: null, rows: [], pageNo: 1 });
  const [tableLoading, setTableLoading] = useState(false);
  const [summary, setSummary] = useState<Awaited<ReturnType<typeof fetchReaderSummary>> | null>(null);
  const [summaryField, setSummaryField] = useState('');
  const [useExtent, setUseExtent] = useState(false);
  const [extentBbox, setExtentBbox] = useState<[number, number, number, number] | null>(null);
  const mapRef = useRef<MapRef>(null);
  const [mapStyle, setMapStyle] = useState<StyleSpecification | null>(null);
  const [compiledLayerIds, setCompiledLayerIds] = useState<string[]>([]);
  const [legendDefs, setLegendDefs] = useState<{ layerId: string; title: string; items: unknown[] }[]>([]);

  const status = useReaderStore((s) => s.status);
  const statusDetail = useReaderStore((s) => s.statusDetail);
  const setStatus = useReaderStore((s) => s.setStatus);
  const visibleLayers = useReaderStore((s) => s.visibleLayers);
  const setVisibleLayers = useReaderStore((s) => s.setVisibleLayers);
  const toggleLayer = useReaderStore((s) => s.toggleLayer);
  const predicates = useReaderStore((s) => s.predicates);
  const setPredicates = useReaderStore((s) => s.setPredicates);
  const selection = useReaderStore((s) => s.selection);
  const setSelection = useReaderStore((s) => s.setSelection);
  const announce = useReaderStore((s) => s.announce);
  const webglUnavailable = useReaderStore((s) => s.webglUnavailable);
  const setWebglUnavailable = useReaderStore((s) => s.setWebglUnavailable);

  // 初始加载：reader doc + map spec。
  useEffect(() => {
    const controller = new AbortController();
    setStatus('loading');
    (async () => {
      try {
        const readerDoc = await fetchReaderDoc(code, controller.signal);
        setDoc(readerDoc);
        setStatus('ready');
        try {
          const payload = await fetchReaderMap(code, controller.signal);
          setMapPayload(payload);
          const result = compileMapSpec(payload.spec as never);
          if (result.report.success && result.style) {
            setMapStyle(result.style as unknown as StyleSpecification);
            setCompiledLayerIds(result.legend.map((l) => l.layerId));
            setLegendDefs(result.legend);
          }
        } catch (mapErr) {
          // map spec 失败不阻断等价信息视图（表/统计/说明仍可用）。
          if (!isApiError(mapErr) || mapErr.status !== 404) {
            setStatus('degraded', describeApiError(mapErr, 'map spec unavailable'));
          } else {
            setStatus('unavailable');
          }
        }
      } catch (err) {
        if (isApiError(err) && err.status === 404) {
          setStatus('unavailable');
        } else if (isApiError(err) && err.status === 503) {
          setStatus('error', t('notReady'));
          setErrorText(t('notReady'));
        } else if (!controller.signal.aborted) {
          setStatus('error', describeApiError(err, t('error')));
          setErrorText(describeApiError(err, t('error')));
        }
      }
    })();
    return () => controller.abort();
  }, [code, setStatus, t]);

  useEffect(() => {
    if (!detectWebGl()) setWebglUnavailable(true);
  }, [setWebglUnavailable]);

  const visibleLayerInfos = useMemo(
    () => (doc ? doc.layers.filter((l) => l.reader_visibility_allowed) : []),
    [doc],
  );

  useEffect(() => {
    if (doc && Object.keys(visibleLayers).length === 0 && doc.layers.length > 0) {
      const initial: Record<string, boolean> = {};
      for (const layer of doc.layers) initial[layer.layer_id] = true;
      setVisibleLayers(initial);
    }
  }, [doc, visibleLayers, setVisibleLayers]);

  // 筛选语义校验（白名单内才发请求）。
  const whereAst = useMemo(() => predicatesToWhere(predicates), [predicates]);

  // 数据表分页（服务端 where + 白名单投影）。
  const loadTable = useCallback(
    async (cursor?: string | null, pageNo: number = 1) => {
      if (!doc || doc.layers.length === 0) return;
      const layer = doc.layers[0];
      setTableLoading(true);
      try {
        const page = await fetchReaderFeatures(code, layer.layer_id, {
          limit: 200,
          cursor: cursor ?? undefined,
          where: whereAst ?? undefined,
          fields: layer.field_whitelist,
        });
        setTablePage((prev) => ({
          cursor: page.cursor ?? null,
          pageNo,
          rows: pageNo === 1
            ? page.features.map((f) => ({ properties: f.properties, id: f.id }))
            : [...prev.rows, ...page.features.map((f) => ({ properties: f.properties, id: f.id }))],
        }));
      } catch (err) {
        setErrorText(describeApiError(err, t('error')));
      } finally {
        setTableLoading(false);
      }
    },
    [code, doc, whereAst, t],
  );

  useEffect(() => {
    if (activeTab === 'table' && doc) void loadTable(null, 1);
  }, [activeTab, doc, loadTable]);

  // 统计（显式分母；服务端同谓词）。
  useEffect(() => {
    if (activeTab !== 'stats' || !doc || doc.layers.length === 0) return;
    const layer = doc.layers[0];
    const numericFields = layer.field_whitelist.filter((f) => {
      // 从表样例推断数值性（有界：只看已加载行）。
      return tablePage.rows.some((row) => typeof row.properties[f] === 'number');
    });
    const field = summaryField && layer.field_whitelist.includes(summaryField)
      ? summaryField
      : numericFields[0] ?? layer.field_whitelist[0];
    if (!field) return;
    setSummaryField(field);
    (async () => {
      try {
        const result = await fetchReaderSummary(code, layer.layer_id, field, {
          where: whereAst ?? undefined,
          bbox: useExtent ? extentBbox : null,
        });
        setSummary(result);
      } catch (err) {
        setErrorText(describeApiError(err, t('error')));
      }
    })();
  }, [activeTab, doc, code, whereAst, useExtent, extentBbox, summaryField, tablePage.rows, t]);

  const applyPredicates = useCallback(
    (next: ReaderPredicate[]) => {
      if (!predicatesWithinWhitelist(next, doc?.layers.flatMap((l) => l.field_whitelist) ?? [])) {
        setErrorText(t('filterInvalid'));
        return;
      }
      setErrorText('');
      setTablePage({ cursor: null, rows: [], pageNo: 1 });
      setPredicates(next, `${t('filterApplied')}：${describePredicates(next)}`);
    },
    [doc, setPredicates, t],
  );

  const initialView = useMemo(() => {
    const view = (mapPayload?.spec as { view?: { center?: number[]; zoom?: number } } | undefined)?.view;
    return {
      longitude: view?.center?.[0] ?? 116.4,
      latitude: view?.center?.[1] ?? 39.9,
      zoom: view?.zoom ?? 8,
    };
  }, [mapPayload]);

  const handleMapClick = useCallback(
    (event: unknown) => {
      const map = mapRef.current;
      if (!map || !compiledLayerIds.length) return;
      const ev = event as { point: { x: number; y: number } };
      const features = map.queryRenderedFeatures([ev.point.x, ev.point.y]) as unknown as {
        layer: { id: string };
        properties: Record<string, unknown>;
        id?: string | number;
      }[];
      if (features.length === 0) {
        setSelection(null);
        return;
      }
      const hit = features[0];
      setSelection(
        { layerId: hit.layer.id, featureId: hit.id, properties: hit.properties },
        t('selectionAnnounce'),
      );
    },
    [compiledLayerIds.length, setSelection, t],
  );

  const handleExtentChange = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;
    const bounds = map.getBounds();
    setExtentBbox([
      bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth(),
    ]);
  }, []);

  if (status === 'loading') {
    return <div role="status" className="flex items-center justify-center h-screen text-[var(--ink-muted)]">{t('loading')}</div>;
  }
  if (status === 'unavailable') {
    return (
      <div role="alert" className="flex flex-col items-center justify-center h-screen gap-2 text-center px-6">
        <div className="text-lg font-semibold">{t('unavailable')}</div>
        <div className="text-sm opacity-70">{t('unavailableDetail')}</div>
      </div>
    );
  }
  if (status === 'error' && !doc) {
    return (
      <div role="alert" className="flex flex-col items-center justify-center h-screen gap-2 text-center px-6">
        <div className="text-sm">{statusDetail || t('error')}</div>
      </div>
    );
  }
  if (!doc) return null;

  const accessibility = doc.accessibility;
  const degradations = mapPayload?.degradations ?? [];

  return (
    <div className="flex flex-col h-screen bg-[var(--surface-base)] text-[var(--ink-primary)]">
      {/* 顶部：标题与等价信息 */}
      <header className="px-4 py-3 border-b border-[var(--edge-subtle)] flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-base font-semibold">{doc.meta.title}</h1>
        <span className="text-xs px-1.5 py-0.5 rounded border border-[var(--edge-subtle)]">
          {t('versionBadge', { version: doc.version })}
        </span>
        <span className="text-xs opacity-70 truncate max-w-[40ch]" title={doc.meta.purpose}>
          {doc.meta.purpose}
        </span>
        <span className="text-xs opacity-70">
          {doc.meta.attribution} · {doc.meta.license} · {doc.scope.crs}
        </span>
        <a href="#publication-about" className="text-xs underline sr-only focus:not-sr-only">{t('skipToAbout')}</a>
        <a href="#publication-table" className="text-xs underline sr-only focus:not-sr-only">{t('skipToTable')}</a>
        <span className="text-xs opacity-60 ml-auto">{t('readonlyNotice')}</span>
      </header>

      {status === 'degraded' && (
        <div role="status" className="px-4 py-1.5 text-xs bg-[var(--status-warning-bg, #fef3c7)]">
          {t('degradedNotice')} {statusDetail}
        </div>
      )}
      {webglUnavailable && (
        <div role="status" className="px-4 py-1.5 text-xs bg-[var(--status-info-bg, #dbeafe)]">
          {t('webglUnavailable')}
        </div>
      )}

      <div className="flex flex-1 min-h-0 flex-col lg:flex-row">
        {/* 地图 / 等价视图 */}
        <div
          className="relative flex-1 min-h-[50vh]"
          role="application"
          aria-label={`${doc.meta.title} — ${t('title')}`}
        >
          {mapStyle && !webglUnavailable ? (
            <MapErrorBoundary>
              <RNMap
                ref={mapRef}
                id="publication-reader"
                initialViewState={initialView}
                mapStyle={mapStyle as never}
                style={{ position: 'absolute', inset: 0 }}
                onClick={handleMapClick}
                onMoveEnd={handleExtentChange}
                onLoad={handleExtentChange}
                attributionControl={false}
              >
                <NavigationControl showCompass={false} visualizePitch={false} />
                <ReaderFilterApplier compiledLayerIds={compiledLayerIds} />
              </RNMap>
            </MapErrorBoundary>
          ) : (
            <div className="absolute inset-0 flex items-center justify-center text-sm opacity-70 px-8 text-center">
              {webglUnavailable ? t('webglUnavailable') : t('loading')}
            </div>
          )}
          {/* 图层显隐（仅获准层） */}
          <div className="absolute left-2 top-2 z-10 flex flex-col gap-1 rounded-lg bg-[var(--surface-raised, white)]/90 p-2 shadow-sm max-w-[220px]">
            <div className="text-xs font-medium">{t('layers')}</div>
            {visibleLayerInfos.map((layer) => (
              <label key={layer.layer_id} className="flex items-center gap-1.5 text-xs">
                <input
                  type="checkbox"
                  checked={visibleLayers[layer.layer_id] ?? true}
                  onChange={() => toggleLayer(layer.layer_id)}
                  aria-label={t('showLayer', { name: layer.title || layer.layer_id })}
                />
                <span className="truncate">{layer.title || layer.layer_id}</span>
              </label>
            ))}
          </div>
        </div>

        {/* 信息面板 */}
        <aside className="flex flex-col w-full lg:w-[380px] border-t lg:border-t-0 lg:border-l border-[var(--edge-subtle)] min-h-0">
          <div role="tablist" aria-label={t('title')} className="flex border-b border-[var(--edge-subtle)] text-sm">
            {(['legend', 'table', 'stats', 'about'] as const).map((tab) => (
              <button
                key={tab}
                role="tab"
                aria-selected={activeTab === tab}
                onClick={() => setActiveTab(tab)}
                className={`flex-1 px-2 py-2 ${activeTab === tab ? 'font-semibold border-b-2 border-[var(--accent, #2563eb)]' : 'opacity-70'}`}
              >
                {t(tab === 'legend' ? 'tabLegend' : tab === 'table' ? 'tabTable' : tab === 'stats' ? 'tabStats' : 'tabAbout')}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto p-3 text-sm min-h-0">
            {/* 图例 + 筛选 */}
            {activeTab === 'legend' && (
              <div className="flex flex-col gap-4">
                <section aria-label={t('tabLegend')}>
                  {legendDefs.length === 0 && <div className="opacity-60 text-xs">—</div>}
                  {legendDefs.map((def) => (
                    <div key={def.layerId} className="mb-3">
                      <div className="text-xs font-medium mb-1">{def.title}</div>
                      {/* 编译图例 items 形状开放 —— 以色块/标签最小渲染（只读语义）。 */}
                      <ul className="flex flex-col gap-1">
                        {(def.items as { label?: string; color?: string }[]).map((item, idx) => (
                          <li key={idx} className="flex items-center gap-2 text-xs">
                            {item.color ? (
                              <span className="inline-block w-3 h-3 rounded-sm border border-[var(--edge-subtle)]" style={{ background: item.color }} />
                            ) : null}
                            <span>{item.label ?? '—'}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </section>

                <section aria-label={t('filterTitle')} className="border-t border-[var(--edge-subtle)] pt-3">
                  <div className="flex items-center justify-between mb-2">
                    <div className="text-xs font-medium">{t('filterTitle')}</div>
                    {predicates.length > 0 && (
                      <button
                        className="text-xs underline"
                        onClick={() => applyPredicates([])}
                      >
                        {t('filterClear')}
                      </button>
                    )}
                  </div>
                  <PredicateEditor
                    fields={doc.layers.flatMap((l) => l.field_whitelist)}
                    predicates={predicates}
                    onChange={applyPredicates}
                    t={t}
                  />
                </section>

                {/* 选择详情 */}
                <section aria-label={t('selectionTitle')} className="border-t border-[var(--edge-subtle)] pt-3">
                  <div className="text-xs font-medium mb-1">{t('selectionTitle')}</div>
                  {selection ? (
                    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                      {Object.entries(selection.properties).map(([key, value]) => (
                        <React.Fragment key={key}>
                          <dt className="opacity-70">{key}</dt>
                          <dd className="break-all">{String(value ?? '—')}</dd>
                        </React.Fragment>
                      ))}
                    </dl>
                  ) : (
                    <div className="text-xs opacity-60">{t('selectionEmpty')}</div>
                  )}
                </section>

                {degradations.length > 0 && (
                  <section aria-label={t('degradationsTitle')} className="border-t border-[var(--edge-subtle)] pt-3">
                    <div className="text-xs font-medium mb-1">{t('degradationsTitle')}</div>
                    <ul className="list-disc pl-4 text-xs opacity-80">
                      {degradations.map((d, idx) => (
                        <li key={idx}>{d.message}</li>
                      ))}
                    </ul>
                  </section>
                )}
              </div>
            )}

            {/* 数据表 */}
            {activeTab === 'table' && (
              <div id="publication-table" aria-label={t('tableTitle')}>
                <div className="flex items-center gap-2 mb-2">
                  <button
                    className="text-xs underline disabled:opacity-40"
                    disabled={tableLoading || tablePage.pageNo <= 1}
                    onClick={() => void loadTable(null, 1)}
                  >
                    {t('tablePrev')}
                  </button>
                  <span className="text-xs opacity-70">{t('tablePage', { page: tablePage.pageNo })}</span>
                  <button
                    className="text-xs underline disabled:opacity-40"
                    disabled={tableLoading || !tablePage.cursor}
                    onClick={() => void loadTable(tablePage.cursor, tablePage.pageNo + 1)}
                  >
                    {t('tableNext')}
                  </button>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs border-collapse">
                    <thead>
                      <tr>
                        {doc.layers[0]?.field_whitelist.map((field) => (
                          <th key={field} scope="col" className="text-left border-b border-[var(--edge-subtle)] py-1 px-1 font-medium">
                            {field}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {tablePage.rows.map((row, idx) => (
                        <tr key={idx} className="border-b border-[var(--edge-subtle)]/50">
                          {doc.layers[0]?.field_whitelist.map((field) => (
                            <td key={field} className="py-1 px-1 align-top break-all">
                              {String(row.properties[field] ?? '—')}
                            </td>
                          ))}
                        </tr>
                      ))}
                      {tablePage.rows.length === 0 && !tableLoading && (
                        <tr>
                          <td colSpan={Math.max(doc.layers[0]?.field_whitelist.length ?? 1, 1)} className="py-3 text-center opacity-60">
                            {t('tableEmpty')}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                {tableLoading && <div role="status" className="text-xs opacity-60 mt-2">{t('loading')}</div>}
              </div>
            )}

            {/* 统计 */}
            {activeTab === 'stats' && (
              <div aria-label={t('statsTitle')}>
                <div className="text-xs font-medium mb-2">{t('statsTitle')}</div>
                <label className="flex items-center gap-2 text-xs mb-2">
                  <input
                    type="checkbox"
                    checked={useExtent}
                    onChange={(e) => setUseExtent(e.target.checked)}
                  />
                  {t('useCurrentExtent')}
                </label>
                <label className="flex items-center gap-2 text-xs mb-2">
                  <span className="opacity-70">{t('statsField')}</span>
                  <select
                    className="text-xs border border-[var(--edge-subtle)] rounded px-1 py-0.5"
                    value={summaryField}
                    onChange={(e) => setSummaryField(e.target.value)}
                  >
                    {doc.layers[0]?.field_whitelist.map((field) => (
                      <option key={field} value={field}>{field}</option>
                    ))}
                  </select>
                </label>
                {summary ? (
                  <div className="flex flex-col gap-1 text-xs">
                    <div className="grid grid-cols-2 gap-x-3 gap-y-1">
                      <span className="opacity-70">count</span><span>{summary.stats.count}</span>
                      <span className="opacity-70">sum</span><span>{summary.stats.sum ?? '—'}</span>
                      <span className="opacity-70">min</span><span>{summary.stats.min ?? '—'}</span>
                      <span className="opacity-70">max</span><span>{summary.stats.max ?? '—'}</span>
                    </div>
                    <div className="opacity-70 mt-2">
                      {t('statsDenominator', {
                        total: summary.denominator.total_features,
                        matching: summary.denominator.matching_features ?? '?',
                        numeric: summary.denominator.numeric_count ?? summary.stats.count,
                        nulls: summary.denominator.null_or_non_numeric ?? summary.stats.null_count,
                      })}
                    </div>
                    <div className="opacity-50 text-[11px]">{t('statsNote')}</div>
                  </div>
                ) : (
                  <div className="text-xs opacity-60">{t('loading')}</div>
                )}
              </div>
            )}

            {/* 说明（WP4 等价信息） */}
            {activeTab === 'about' && (
              <div id="publication-about" aria-label={t('aboutTitle')} className="flex flex-col gap-3 text-xs">
                <div className="text-sm font-medium">{t('aboutTitle')}</div>
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                  <dt className="opacity-70">{t('aboutPurpose')}</dt>
                  <dd>{doc.meta.purpose || '—'}</dd>
                  <dt className="opacity-70">{t('aboutAttribution')}</dt>
                  <dd>{doc.meta.attribution || '—'}</dd>
                  <dt className="opacity-70">{t('aboutLicense')}</dt>
                  <dd>{doc.meta.license || '—'}</dd>
                  <dt className="opacity-70">{t('aboutProjection')}</dt>
                  <dd>{accessibility.projection || doc.scope.crs}</dd>
                  <dt className="opacity-70">{t('aboutTimeRange')}</dt>
                  <dd>{doc.scope.time_start ?? '—'} ~ {doc.scope.time_end ?? '—'}</dd>
                  <dt className="opacity-70">{t('aboutMissing')}</dt>
                  <dd>{doc.meta.missing_values_note || '—'}</dd>
                </dl>
                <div>
                  <div className="font-medium mb-1">{t('aboutSources')}</div>
                  <ul className="list-disc pl-4 opacity-80">
                    {(accessibility.dataSources ?? []).map((s, idx) => (
                      <li key={idx}>{s.name}{s.attribution ? ` — ${s.attribution}` : ''}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <div className="font-medium mb-1">{t('aboutColorblind')}</div>
                  <div className="opacity-80">
                    {accessibility.colorblindSafety?.checked
                      ? accessibility.colorblindSafety.separable
                        ? t('colorblindOk')
                        : t('colorblindFail')
                      : t('colorblindUnchecked')}
                  </div>
                </div>
                <div>
                  <div className="font-medium mb-1">{t('altTextTitle')}</div>
                  <p className="opacity-80">{accessibility.altText}</p>
                </div>
                {doc.blockers.length > 0 && (
                  <div>
                    <div className="font-medium mb-1">{t('blockersTitle')}</div>
                    <ul className="list-disc pl-4 opacity-80">
                      {doc.blockers.map((b, idx) => (
                        <li key={idx}>{b.message}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <div>
                  <div className="font-medium mb-1">{t('qaTitle')}</div>
                  <ul className="list-disc pl-4 opacity-80">
                    <li>{t('qaSpecValidity')}: {doc.qa.spec_validity}</li>
                    {accessibility.complete
                      ? <li>{t('accessibilityComplete')}</li>
                      : <li>{t('accessibilityIncomplete', { missing: (accessibility.missing ?? []).join(', ') })}</li>}
                  </ul>
                </div>
              </div>
            )}
          </div>
        </aside>
      </div>

      {/* 筛选/选择的读屏播报 */}
      <div aria-live="polite" role="status" className="sr-only">{announce}</div>
      {errorText && (
        <div role="alert" className="px-4 py-1.5 text-xs bg-[var(--status-danger-bg, #fee2e2)]">
          {errorText}
        </div>
      )}
    </div>
  );
}

const FILTER_OPS: { value: ReaderPredicate['op']; label: string }[] = [
  { value: 'eq', label: '=' },
  { value: 'ne', label: '≠' },
  { value: 'gte', label: '≥' },
  { value: 'lte', label: '≤' },
  { value: 'contains', label: '∋' },
];

function PredicateEditor({
  fields,
  predicates,
  onChange,
  t,
}: {
  fields: string[];
  predicates: ReaderPredicate[];
  onChange: (next: ReaderPredicate[]) => void;
  t: ReturnType<typeof useT>;
}) {
  return (
    <div className="flex flex-col gap-2">
      {predicates.map((p, idx) => (
        <div key={idx} className="flex items-center gap-1.5 text-xs">
          <select
            aria-label={t('filterField')}
            className="border border-[var(--edge-subtle)] rounded px-1 py-0.5"
            value={p.field}
            onChange={(e) => {
              const next = [...predicates];
              next[idx] = { ...p, field: e.target.value };
              onChange(next);
            }}
          >
            {fields.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          <select
            aria-label={t('filterOp')}
            className="border border-[var(--edge-subtle)] rounded px-1 py-0.5"
            value={p.op}
            onChange={(e) => {
              const next = [...predicates];
              next[idx] = { ...p, op: e.target.value as ReaderPredicate['op'] };
              onChange(next);
            }}
          >
            {FILTER_OPS.map((op) => <option key={op.value} value={op.value}>{op.label}</option>)}
          </select>
          <input
            aria-label={t('filterValue')}
            className="border border-[var(--edge-subtle)] rounded px-1 py-0.5 w-24"
            value={String(p.value ?? '')}
            onChange={(e) => {
              const raw = e.target.value;
              const asNumber = Number(raw);
              const next = [...predicates];
              next[idx] = { ...p, value: raw !== '' && !Number.isNaN(asNumber) ? asNumber : raw };
              onChange(next);
            }}
          />
          <button
            className="underline"
            aria-label={`remove ${idx}`}
            onClick={() => onChange(predicates.filter((_, i) => i !== idx))}
          >
            ×
          </button>
        </div>
      ))}
      <button
        className="text-xs underline self-start"
        onClick={() => onChange([...predicates, { field: fields[0] ?? '', op: 'eq', value: '' }])}
        disabled={fields.length === 0}
      >
        + {t('filterAdd')}
      </button>
    </div>
  );
}
