'use client';

import { useCallback, useRef, useState } from 'react';
import { Database, Inbox, Layers, SearchX } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useToastStore } from '@/components/ui/toast';
import { dataFabricApi, type CatalogItem, type DatasetDescriptor, type QueryResult } from '@/lib/api/data-fabric';
import { LoadingState } from '@/components/shared/loading-state';
import { EmptyState } from '@/components/shared/empty-state';
import { useSpatialCatalog } from './data-sources/use-spatial-catalog';
import { useDataSources } from './data-sources/use-data-sources';
import { CatalogToolbar } from './data-sources/catalog-toolbar';
import { CatalogItemCard } from './data-sources/catalog-item-card';
import { SourcesToolbar } from './data-sources/sources-toolbar';
import { SourceItemCard } from './data-sources/source-item-card';
import { AddSourceForm } from './data-sources/add-source-form';
import { DatasetDescriptorModal } from './data-sources/dataset-descriptor-modal';
import { PreviewModal } from './data-sources/preview-modal';

// A-F-08: debounce window re-exported so consumers/tests keep importing it from
// the tab entry point (constant itself lives with the catalog hook).
export { CATALOG_SEARCH_DEBOUNCE_MS } from './data-sources/use-spatial-catalog';

/** 子页签顺序即渲染顺序（V4 tablist 键盘导航用）。 */
const SUBTABS: Array<'catalog' | 'sources'> = ['catalog', 'sources'];

export function DataSourcesTab() {
  const [activeSubTab, setActiveSubTab] = useState<'catalog' | 'sources'>('catalog');
  const [showAddForm, setShowAddForm] = useState(false);

  // V4 子页签：补齐 WAI-APG tablist 语义（roving tabindex + 方向键/Home/End
  // 键盘导航，activation-on-focus），与 nav-rail / map-studio 的 tablist 一致。
  const subTabRefs = useRef<Map<string, HTMLButtonElement>>(new Map());

  const onSubTabKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const idx = SUBTABS.indexOf(activeSubTab);
      let next: 'catalog' | 'sources' | null = null;
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

  const addToast = useToastStore((s) => s.addToast);
  const addLayer = useHudStore((s) => s.addLayer);

  const { sources, loadingSources, refreshSources } = useDataSources();
  const {
    catalogItems,
    catalogTotal,
    loadingCatalog,
    searchQuery,
    setSearchQuery,
    selectedSourceFilter,
    setSelectedSourceFilter,
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
      addToast(err instanceof Error ? err.message : '探测失败', 'error');
    }
  };

  const handleSync = async (sourceId: string) => {
    try {
      const res = await dataFabricApi.syncDataSourceCatalog(sourceId);
      addToast(`目录同步成功，共同步 ${res.synced_count} 个数据集`, 'success');
      refreshCatalog();
    } catch (err) {
      addToast(err instanceof Error ? err.message : '同步失败', 'error');
    }
  };

  const handleDeleteSource = async (sourceId: string) => {
    try {
      await dataFabricApi.deleteDataSource(sourceId);
      addToast('数据源已删除', 'success');
      refreshSources();
      refreshCatalog();
    } catch (err) {
      addToast(err instanceof Error ? err.message : '删除失败', 'error');
    }
  };

  const handleShowDescriptor = async (itemId: string) => {
    try {
      const desc = await dataFabricApi.getCatalogItemDescriptor(itemId);
      setActiveDescriptor(desc);
    } catch (err) {
      addToast(err instanceof Error ? err.message : '获取 Descriptor 失败', 'error');
    }
  };

  const handlePreview = async (itemId: string) => {
    try {
      const prev = await dataFabricApi.previewCatalogItem(itemId, 10);
      setPreviewResult(prev);
    } catch (err) {
      addToast(err instanceof Error ? err.message : '预览失败', 'error');
    }
  };

  const handleMaterializeAndLoad = async (item: CatalogItem) => {
    setMaterializingId(item.id);
    try {
      const activeSessionId = (window as unknown as { __WEBGIS_SESSION_ID__?: string }).__WEBGIS_SESSION_ID__ || 'default_session';
      const res = await dataFabricApi.materializeCatalogItem({
        session_id: activeSessionId,
        catalog_item_id: item.id,
      });

      // Add to frontend HUD layers
      addLayer({
        id: `df-${item.id}`,
        name: item.title || item.name,
        type: item.feature_type === 'raster' ? 'raster' : 'vector',
        visible: true,
        opacity: 1,
        group: 'reference',
        _refId: res.ref_id,
        style: { color: '#16a34a' },
      });

      addToast(`成功按需实例化 ${res.feature_count} 个要素至图层`, 'success');
    } catch (err) {
      addToast(err instanceof Error ? err.message : '实例化失败', 'error');
    } finally {
      setMaterializingId(null);
    }
  };

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
        <button
          type="button"
          role="tab"
          id="data-subtab-catalog"
          aria-selected={activeSubTab === 'catalog'}
          // 仅当前激活 tab 指向实际渲染的 panel（另一 panel 未挂载）
          aria-controls={activeSubTab === 'catalog' ? 'data-subtab-panel-catalog' : undefined}
          tabIndex={activeSubTab === 'catalog' ? 0 : -1}
          ref={(el) => {
            if (el) subTabRefs.current.set('catalog', el);
            else subTabRefs.current.delete('catalog');
          }}
          onClick={() => setActiveSubTab('catalog')}
          className={`flex items-center gap-1.5 border-b-2 px-2 pb-2 text-meta font-medium transition-colors ${
            activeSubTab === 'catalog'
              ? 'border-status-accent-vivid text-status-accent'
              : 'border-transparent text-ink-muted hover:text-ink-secondary'
          }`}
        >
          <Layers size={14} aria-hidden />
          <span>空间目录 ({catalogTotal})</span>
        </button>
        <button
          type="button"
          role="tab"
          id="data-subtab-sources"
          aria-selected={activeSubTab === 'sources'}
          aria-controls={activeSubTab === 'sources' ? 'data-subtab-panel-sources' : undefined}
          tabIndex={activeSubTab === 'sources' ? 0 : -1}
          ref={(el) => {
            if (el) subTabRefs.current.set('sources', el);
            else subTabRefs.current.delete('sources');
          }}
          onClick={() => setActiveSubTab('sources')}
          className={`flex items-center gap-1.5 border-b-2 px-2 pb-2 text-meta font-medium transition-colors ${
            activeSubTab === 'sources'
              ? 'border-status-accent-vivid text-status-accent'
              : 'border-transparent text-ink-muted hover:text-ink-secondary'
          }`}
        >
          <Database size={14} aria-hidden />
          <span>数据源 ({sources.length})</span>
        </button>
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

      {/* DatasetDescriptor Modal / Drawer */}
      {activeDescriptor && <DatasetDescriptorModal descriptor={activeDescriptor} onClose={closeDescriptor} />}

      {/* Preview Modal */}
      {previewResult && <PreviewModal result={previewResult} onClose={closePreview} />}
    </div>
  );
}

export default DataSourcesTab;
