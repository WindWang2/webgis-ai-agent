'use client';

import { useCallback, useRef, useState } from 'react';
import { Database, Inbox, Layers, SearchX, Sigma, Table2 } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useToastStore } from '@/components/ui/toast';
import { describeApiError, isApiError } from '@/lib/api/transport';
import type { GeoJSONFeatureCollection } from '@/lib/types';
import {
  dataFabricApi,
  type CatalogItem,
  type DatasetDescriptor,
  type ExplainResult,
  type QueryResult,
  type QuerySpec,
  type SyncDiff,
} from '@/lib/api/data-fabric';
import { LoadingState } from '@/components/shared/loading-state';
import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { useSpatialCatalog } from './data-sources/use-spatial-catalog';
import { useDataSources } from './data-sources/use-data-sources';
import { CatalogToolbar } from './data-sources/catalog-toolbar';
import { CatalogItemCard } from './data-sources/catalog-item-card';
import { SourcesToolbar } from './data-sources/sources-toolbar';
import { SourceItemCard } from './data-sources/source-item-card';
import { AddSourceForm } from './data-sources/add-source-form';
import { DatasetDescriptorModal } from './data-sources/dataset-descriptor-modal';
import { PreviewModal } from './data-sources/preview-modal';
import { DatasetInspector } from './data-sources/dataset-inspector';
import { ExplainResultsPanel, type TypedQueryError } from './data-sources/explain-results-panel';

// A-F-08: debounce window re-exported so consumers/tests keep importing it from
// the tab entry point (constant itself lives with the catalog hook).
export { CATALOG_SEARCH_DEBOUNCE_MS } from './data-sources/use-spatial-catalog';

/** 数据工作台子页签（V2 / ADR-0094：目录 → 数据源 → 数据集 → 查询计划）。 */
type DataSubTab = 'catalog' | 'sources' | 'dataset' | 'explain';

/** 子页签顺序即渲染顺序（V4 tablist 键盘导航用）。空间目录保持默认首页签。 */
const SUBTABS: DataSubTab[] = ['catalog', 'sources', 'dataset', 'explain'];

/** sync 后的增量 diff 通知（V2 / ADR-0094 §9：added/updated/removed + warnings）。 */
interface SyncNotice {
  sourceName: string;
  diff: SyncDiff;
  warnings: string[];
}

/**
 * 类型化错误提取（ADR-0094 §13 错误契约）：
 * - DataFabricError 的 JSONResponse 体（{success:false, error_type, error}）；
 * - explain 端点 422 时 FastAPI detail 内嵌的 error outcome 对象；
 * - 其余回落 describeApiError（含 detail 字符串 / 网络错误归一化）。
 */
function extractTypedError(err: unknown, fallback: string): TypedQueryError {
  if (isApiError(err)) {
    const body = (err.body ?? null) as Record<string, unknown> | null;
    const detail = body && typeof body.detail === 'object' ? (body.detail as Record<string, unknown>) : null;
    for (const candidate of [body, detail]) {
      if (!candidate) continue;
      const errorType = typeof candidate.error_type === 'string' ? candidate.error_type : undefined;
      const message = typeof candidate.error === 'string' ? candidate.error : undefined;
      if (errorType || message) {
        return { message: message ?? describeApiError(err, fallback), errorType };
      }
    }
  }
  return { message: describeApiError(err, fallback) };
}

export interface DataSourcesTabProps {
  /**
   * #463: the REAL conversation session id (threaded from ContextPanel).
   * Materialization writes session-store refs scoped to this id — the old
   * `window.__WEBGIS_SESSION_ID__` global had zero writers repo-wide, so every
   * request silently targeted a phantom 'default_session' (401 anonymous,
   * invisible layer + cross-session ref pollution when authed).
   */
  sessionId?: string | null;
  /** Anonymous-session ownership token riding X-Session-Token (SEC-08). */
  ownerToken?: string | null;
}

export function DataSourcesTab({ sessionId, ownerToken }: DataSourcesTabProps) {
  const [activeSubTab, setActiveSubTab] = useState<DataSubTab>('catalog');
  const [showAddForm, setShowAddForm] = useState(false);

  // V4 子页签：补齐 WAI-APG tablist 语义（roving tabindex + 方向键/Home/End
  // 键盘导航，activation-on-focus），与 nav-rail / map-studio 的 tablist 一致。
  const subTabRefs = useRef<Map<string, HTMLButtonElement>>(new Map());

  const onSubTabKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const idx = SUBTABS.indexOf(activeSubTab);
      let next: DataSubTab | null = null;
      if (e.key === 'ArrowRight') next = SUBTABS[(idx + 1) % SUBTABS.length];
      else if (e.key === 'ArrowLeft') next = SUBTABS[(idx - 1 + SUBTABS.length) % SUBTABS.length];
      else if (e.key === 'Home') next = SUBTABS[0];
      else if (e.key === 'End') next = SUBTABS[SUBTABS.length - 1];
      if (!next) return;
      e.preventDefault();
      // activation-on-focus（APG）：移动焦点即激活对应子页签。
      setActiveSubTab(next);
      subTabRefs.current.get(next)?.focus();
    },
    [activeSubTab]
  );

  // Modal / Drawer state
  const [activeDescriptor, setActiveDescriptor] = useState<DatasetDescriptor | null>(null);
  const [previewResult, setPreviewResult] = useState<QueryResult | null>(null);
  const [materializingId, setMaterializingId] = useState<string | null>(null);

  // ── V2 数据工作台状态（数据集检视器 + 查询计划/结果） ─────────────────
  const [inspectedItem, setInspectedItem] = useState<CatalogItem | null>(null);
  const [inspectedDescriptor, setInspectedDescriptor] = useState<DatasetDescriptor | null>(null);
  const [loadingDescriptor, setLoadingDescriptor] = useState(false);
  const [explainResult, setExplainResult] = useState<ExplainResult | null>(null);
  const [explaining, setExplaining] = useState(false);
  const [explainError, setExplainError] = useState<TypedQueryError | null>(null);
  const [queryResult, setQueryResult] = useState<QueryResult | null>(null);
  const [querying, setQuerying] = useState(false);
  const [queryError, setQueryError] = useState<TypedQueryError | null>(null);
  /** 最近一次执行的 spec（不含翻页 extras —— cursor/offset 分页复用它）。 */
  const [lastSpec, setLastSpec] = useState<QuerySpec | null>(null);
  /** 当前 offset 页（0 起；cursor 模式仅作页序显示）。 */
  const [queryPage, setQueryPage] = useState(0);

  // ── V2 sync 增量通知 ───────────────────────────────────────────────────
  const [syncNotice, setSyncNotice] = useState<SyncNotice | null>(null);

  const addToast = useToastStore((s) => s.addToast);
  const addLayer = useHudStore((s) => s.addLayer);
  const updateLayer = useHudStore((s) => s.updateLayer);

  const { sources, loadingSources, refreshSources } = useDataSources();
  const {
    catalogItems,
    catalogTotal,
    loadingCatalog,
    searchQuery,
    setSearchQuery,
    selectedSourceFilter,
    setSelectedSourceFilter,
    availabilityFilter,
    setAvailabilityFilter,
    refreshCatalog,
  } = useSpatialCatalog();

  const closeDescriptor = useCallback(() => setActiveDescriptor(null), []);
  const closePreview = useCallback(() => setPreviewResult(null), []);

  /** 注册成功后：关闭表单 + 同时刷新 sources 与 catalog（保持既有时序）。 */
  const handleSourceCreated = useCallback(() => {
    setShowAddForm(false);
    refreshSources();
    refreshCatalog();
  }, [refreshSources, refreshCatalog]);

  const handleProbe = async (sourceId: string) => {
    try {
      const res = await dataFabricApi.probeDataSource(sourceId);
      addToast(`连通测试结果: ${res.status} (${res.message})`, res.status === 'healthy' ? 'success' : 'warning');
      refreshSources();
    } catch (err) {
      addToast(describeApiError(err, '探测失败'), 'error');
    }
  };

  const handleSync = async (sourceId: string) => {
    try {
      const res = await dataFabricApi.syncDataSourceCatalog(sourceId);
      // V2 (ADR-0094 §9)：增量 diff（added/updated/unchanged/removed） +
      // warnings 以 InlineNotice 呈现，而非只报一个 synced_count。
      const diff = res.diff ?? {};
      const sourceName = sources.find((s) => s.id === sourceId)?.name ?? sourceId;
      setSyncNotice({ sourceName, diff, warnings: res.warnings ?? [] });
      addToast(
        `目录同步完成：新增 ${diff.added ?? 0} · 更新 ${diff.updated ?? 0} · 下线 ${diff.removed ?? 0}`,
        'success'
      );
      refreshCatalog();
    } catch (err) {
      addToast(describeApiError(err, '同步失败'), 'error');
    }
  };

  const handleDeleteSource = async (sourceId: string) => {
    try {
      await dataFabricApi.deleteDataSource(sourceId);
      addToast('数据源已删除', 'success');
      refreshSources();
      refreshCatalog();
    } catch (err) {
      addToast(describeApiError(err, '删除失败'), 'error');
    }
  };

  const handleShowDescriptor = async (itemId: string) => {
    try {
      const desc = await dataFabricApi.getCatalogItemDescriptor(itemId);
      setActiveDescriptor(desc);
    } catch (err) {
      addToast(describeApiError(err, '获取 Descriptor 失败'), 'error');
    }
  };

  const handlePreview = async (itemId: string) => {
    try {
      const prev = await dataFabricApi.previewCatalogItem(itemId, 10);
      setPreviewResult(prev);
    } catch (err) {
      addToast(describeApiError(err, '预览失败'), 'error');
    }
  };

  /** 打开数据集检视器：切换子页签 + 重置上一数据集的计划/结果 + 拉取契约。 */
  const handleInspect = useCallback(
    async (item: CatalogItem) => {
      setInspectedItem(item);
      setActiveSubTab('dataset');
      // 结果/计划归属旧数据集 —— 重置，避免跨数据集串显。
      setInspectedDescriptor(null);
      setExplainResult(null);
      setExplainError(null);
      setQueryResult(null);
      setQueryError(null);
      setLastSpec(null);
      setQueryPage(0);
      setLoadingDescriptor(true);
      try {
        const desc = await dataFabricApi.getCatalogItemDescriptor(item.id);
        setInspectedDescriptor(desc);
      } catch (err) {
        addToast(describeApiError(err, '获取 Descriptor 失败'), 'error');
      } finally {
        setLoadingDescriptor(false);
      }
    },
    [addToast]
  );

  /** 执行查询（extras 携带服务端翻页：cursor / offset）。 */
  const runQuery = useCallback(
    async (spec: QuerySpec, extras?: { cursor?: string; offset?: number }) => {
      if (!inspectedItem) return;
      setQuerying(true);
      setQueryError(null);
      const fullSpec: QuerySpec = { ...spec };
      if (extras?.cursor) fullSpec.cursor = extras.cursor;
      if (extras?.offset !== undefined && extras.offset > 0) fullSpec.offset = extras.offset;
      try {
        const res = await dataFabricApi.queryCatalogItem(inspectedItem.id, fullSpec);
        setQueryResult(res);
        setLastSpec(spec);
        const pageSize = Math.max(1, spec.limit ?? 100);
        if (extras?.offset !== undefined) setQueryPage(Math.floor(extras.offset / pageSize));
        else if (extras?.cursor) setQueryPage((p) => p + 1);
        else setQueryPage(0);
        setActiveSubTab('explain');
      } catch (err) {
        setQueryError(extractTypedError(err, '查询失败'));
        setActiveSubTab('explain');
      } finally {
        setQuerying(false);
      }
    },
    [inspectedItem]
  );

  /** 解释计划（dry-run；错误切到计划面板内联展示，不打断流程）。 */
  const runExplain = useCallback(
    async (spec: QuerySpec) => {
      if (!inspectedItem) return;
      setExplaining(true);
      setExplainError(null);
      try {
        const res = await dataFabricApi.explainCatalogItem(inspectedItem.id, spec);
        setExplainResult(res);
      } catch (err) {
        setExplainError(extractTypedError(err, '解释计划失败'));
      } finally {
        setExplaining(false);
        setActiveSubTab('explain');
      }
    },
    [inspectedItem]
  );

  const handleMaterializeAndLoad = async (item: CatalogItem) => {
    // #463: materialize writes into the CURRENT conversation's session store.
    // Without a live session there is nothing to write into (and no way to
    // fetch the ref back) — fail with actionable guidance instead of posting
    // to a phantom 'default_session'.
    if (!sessionId) {
      addToast('暂无活动会话：请先在对话中发送一条消息创建会话，再实例化至图层', 'error');
      return;
    }
    setMaterializingId(item.id);
    try {
      const res = await dataFabricApi.materializeCatalogItem({
        session_id: sessionId,
        catalog_item_id: item.id,
        ownerToken,
      });

      // Add to frontend HUD layers as a REF-BACKED layer (same shape as the
      // workspace-session restore path): the mapspec adapter skips sourceless
      // layers outright, so carry a placeholder FeatureCollection holding the
      // ref cursor in metadata, then hydrate on demand below. The materialized
      // payload is always a GeoJSON FeatureCollection (see the backend
      // materialize route), hence type 'vector' regardless of feature_type.
      const layerId = `df-${item.id}`;
      const placeholder: GeoJSONFeatureCollection = {
        type: 'FeatureCollection',
        features: [],
        metadata: { ref_id: res.ref_id },
      };
      addLayer({
        id: layerId,
        name: item.title || item.name,
        type: 'vector',
        visible: true,
        opacity: 1,
        group: 'reference',
        source: placeholder,
        _refId: res.ref_id,
        style: { color: '#16a34a' },
      });

      addToast(`成功按需实例化 ${res.feature_count} 个要素至图层`, 'success');

      // Fetch-on-demand (#463): hydrate the layer with the stored payload so
      // it actually renders. The ref lives in the real session, so the fetch
      // carries the same session id / owner token as the materialize call.
      try {
        const geojson = await dataFabricApi.fetchRefGeoJSON(res.ref_id, sessionId, { ownerToken });
        if (geojson && (geojson.type === 'FeatureCollection' || Array.isArray(geojson.features))) {
          updateLayer(layerId, { source: geojson });
        }
      } catch {
        // The layer exists and the ref was stored — only the immediate
        // hydration failed (transient or ownership issue). Say so instead of
        // faking success or silently leaving an invisible layer.
        addToast('图层已创建，但引用数据加载失败，请稍后重试或刷新会话', 'warning');
      }
    } catch (err) {
      if (isApiError(err) && err.status === 401) {
        // The materialize route requires authentication — surface a clear
        // login-required message instead of a raw 401 toast.
        addToast('实例化需要登录：请先在 设置 → 账户 中登录后再试', 'error');
      } else {
        addToast(describeApiError(err, '实例化失败'), 'error');
      }
    } finally {
      setMaterializingId(null);
    }
  };

  /** 子页签按钮的公共配方（WAI-APG tab 语义 + 激活样式）。 */
  const renderSubTabButton = (
    key: DataSubTab,
    label: string,
    Icon: typeof Layers
  ) => (
    <button
      key={key}
      type="button"
      role="tab"
      id={`data-subtab-${key}`}
      aria-selected={activeSubTab === key}
      // 仅当前激活 tab 指向实际渲染的 panel（其余 panel 未挂载）
      aria-controls={activeSubTab === key ? `data-subtab-panel-${key}` : undefined}
      tabIndex={activeSubTab === key ? 0 : -1}
      ref={(el) => {
        if (el) subTabRefs.current.set(key, el);
        else subTabRefs.current.delete(key);
      }}
      onClick={() => setActiveSubTab(key)}
      className={`flex items-center gap-1.5 border-b-2 px-2 pb-2 text-meta font-medium transition-colors ${
        activeSubTab === key
          ? 'border-status-accent-vivid text-status-accent'
          : 'border-transparent text-ink-muted hover:text-ink-secondary'
      }`}
    >
      <Icon size={14} aria-hidden />
      <span>{label}</span>
    </button>
  );

  return (
    <div className="flex h-full flex-col overflow-hidden text-body">
      {/* Subtab navigation —— 完整 WAI-APG tablist（role/aria-selected/
          aria-controls/roving tabindex + 方向键与 Home/End 键盘导航）。
          V4 之前是裸 button，无任何 tab 语义，键盘用户只能 Tab 到按钮后回车。 */}
      <div
        role="tablist"
        aria-label="数据子页签"
        onKeyDown={onSubTabKeyDown}
        className="flex shrink-0 gap-2 border-b border-edge-subtle bg-surface-overlay px-2.5 pt-2"
      >
        {renderSubTabButton('catalog', `空间目录 (${catalogTotal})`, Layers)}
        {renderSubTabButton('sources', `数据源 (${sources.length})`, Database)}
        {renderSubTabButton('dataset', '数据集', Table2)}
        {renderSubTabButton('explain', '查询计划', Sigma)}
      </div>

      {/* Catalog View */}
      {activeSubTab === 'catalog' && (
        <div
          role="tabpanel"
          id="data-subtab-panel-catalog"
          aria-labelledby="data-subtab-catalog"
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <CatalogToolbar
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            sources={sources}
            selectedSourceFilter={selectedSourceFilter}
            onSourceFilterChange={setSelectedSourceFilter}
            availabilityFilter={availabilityFilter}
            onAvailabilityFilterChange={setAvailabilityFilter}
          />

          {/* Catalog items list */}
          <div className="flex-1 space-y-2 overflow-y-auto p-2">
            {loadingCatalog ? (
              <LoadingState label="正在加载空间目录..." />
            ) : catalogItems.length === 0 ? (
              <EmptyState icon={SearchX} title="暂无符合条件的空间数据集" />
            ) : (
              catalogItems.map((item) => (
                <CatalogItemCard
                  key={item.id}
                  item={item}
                  materializing={materializingId === item.id}
                  onShowDescriptor={handleShowDescriptor}
                  onPreview={handlePreview}
                  onMaterialize={handleMaterializeAndLoad}
                  onInspect={handleInspect}
                />
              ))
            )}
          </div>
        </div>
      )}

      {/* Sources View */}
      {activeSubTab === 'sources' && (
        <div
          role="tabpanel"
          id="data-subtab-panel-sources"
          aria-labelledby="data-subtab-sources"
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <SourcesToolbar showAddForm={showAddForm} onToggleAddForm={() => setShowAddForm(!showAddForm)} />

          {showAddForm && <AddSourceForm onCreated={handleSourceCreated} />}

          {/* V2：sync 增量 diff 通知（含 warnings；下线条目>0 或有告警时用警示色）。 */}
          {syncNotice && (
            <div className="px-panel pt-2">
              <InlineNotice
                variant={
                  (syncNotice.diff?.removed ?? 0) > 0 || syncNotice.warnings.length > 0
                    ? 'warning'
                    : 'success'
                }
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p>
                      「{syncNotice.sourceName}」目录同步：新增 {syncNotice.diff?.added ?? 0} · 更新{' '}
                      {syncNotice.diff?.updated ?? 0} · 不变 {syncNotice.diff?.unchanged ?? 0} · 下线{' '}
                      {syncNotice.diff?.removed ?? 0}
                    </p>
                    {syncNotice.warnings.length > 0 && (
                      <ul className="mt-1 list-disc pl-4">
                        {syncNotice.warnings.map((w, i) => (
                          <li key={i}>{w}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => setSyncNotice(null)}
                    aria-label="关闭同步通知"
                    className="shrink-0 text-micro text-ink-muted underline-offset-2 hover:text-ink hover:underline"
                  >
                    关闭
                  </button>
                </div>
              </InlineNotice>
            </div>
          )}

          {/* Sources list */}
          <div className="flex-1 space-y-2 overflow-y-auto p-2">
            {loadingSources ? (
              <LoadingState label="加载数据源..." />
            ) : sources.length === 0 ? (
              <EmptyState icon={Inbox} title="暂无注册的数据源" />
            ) : (
              sources.map((s) => (
                <SourceItemCard
                  key={s.id}
                  source={s}
                  onProbe={handleProbe}
                  onSync={handleSync}
                  onDelete={handleDeleteSource}
                />
              ))
            )}
          </div>
        </div>
      )}

      {/* Dataset Inspector + Query Builder (V2) */}
      {activeSubTab === 'dataset' && (
        <div
          role="tabpanel"
          id="data-subtab-panel-dataset"
          aria-labelledby="data-subtab-dataset"
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <DatasetInspector
            item={inspectedItem}
            descriptor={inspectedDescriptor}
            loadingDescriptor={loadingDescriptor}
            fingerprint={explainResult?.dataset_fingerprint ?? null}
            querying={querying}
            explaining={explaining}
            materializing={!!inspectedItem && materializingId === inspectedItem.id}
            onRunQuery={(spec) => runQuery(spec)}
            onExplain={runExplain}
            onMaterialize={handleMaterializeAndLoad}
          />
        </div>
      )}

      {/* Explain & Results (V2) */}
      {activeSubTab === 'explain' && (
        <div
          role="tabpanel"
          id="data-subtab-panel-explain"
          aria-labelledby="data-subtab-explain"
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <ExplainResultsPanel
            item={inspectedItem}
            explainResult={explainResult}
            explaining={explaining}
            explainError={explainError}
            queryResult={queryResult}
            querying={querying}
            queryError={queryError}
            pageSize={Math.max(1, lastSpec?.limit ?? 100)}
            page={queryPage}
            onCursorNext={(cursor) => lastSpec && runQuery(lastSpec, { cursor })}
            onOffsetPage={(offset) => lastSpec && runQuery(lastSpec, { offset })}
          />
        </div>
      )}

      {/* DatasetDescriptor Modal / Drawer */}
      {activeDescriptor && <DatasetDescriptorModal descriptor={activeDescriptor} onClose={closeDescriptor} />}

      {/* Preview Modal */}
      {previewResult && <PreviewModal result={previewResult} onClose={closePreview} />}
    </div>
  );
}

export default DataSourcesTab;
