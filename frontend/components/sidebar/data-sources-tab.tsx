'use client';

import { useCallback, useState } from 'react';
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

export function DataSourcesTab() {
  const [activeSubTab, setActiveSubTab] = useState<'catalog' | 'sources'>('catalog');
  const [showAddForm, setShowAddForm] = useState(false);

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
    <div className="flex h-full flex-col overflow-hidden text-[13px]">
      {/* Subtab navigation */}
      <div className="flex shrink-0 gap-2 border-b border-[var(--theme-border)] bg-[var(--theme-bg-glass)] px-2.5 pt-2">
        <button
          type="button"
          onClick={() => setActiveSubTab('catalog')}
          className={`flex items-center gap-1.5 border-b-2 px-2 pb-2 text-[12px] font-medium transition-colors ${
            activeSubTab === 'catalog'
              ? 'border-[var(--agent-accent,#16a34a)] text-[var(--agent-accent,#16a34a)]'
              : 'border-transparent text-[var(--theme-text-muted)] hover:text-[var(--theme-text-secondary)]'
          }`}
        >
          <Layers size={14} aria-hidden />
          <span>空间目录 ({catalogTotal})</span>
        </button>
        <button
          type="button"
          onClick={() => setActiveSubTab('sources')}
          className={`flex items-center gap-1.5 border-b-2 px-2 pb-2 text-[12px] font-medium transition-colors ${
            activeSubTab === 'sources'
              ? 'border-[var(--agent-accent,#16a34a)] text-[var(--agent-accent,#16a34a)]'
              : 'border-transparent text-[var(--theme-text-muted)] hover:text-[var(--theme-text-secondary)]'
          }`}
        >
          <Database size={14} aria-hidden />
          <span>数据源 ({sources.length})</span>
        </button>
      </div>

      {/* Catalog View */}
      {activeSubTab === 'catalog' && (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
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
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
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
