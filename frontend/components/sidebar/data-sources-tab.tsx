'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Database,
  RefreshCw,
  Plus,
  Trash2,
  Activity,
  Layers,
  Search,
  Info,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Eye,
  Download,
} from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useToastStore } from '@/components/ui/toast';
import {
  dataFabricApi,
  DataSource,
  CatalogItem,
  DatasetDescriptor,
  QueryResult,
} from '@/lib/api/data-fabric';

// A-F-08: search-as-you-type debounce window — rapid keystrokes collapse into a
// single catalog fetch once the user pauses typing.
export const CATALOG_SEARCH_DEBOUNCE_MS = 300;

export function DataSourcesTab() {
  const [activeSubTab, setActiveSubTab] = useState<'catalog' | 'sources'>('catalog');

  // Data Sources state
  const [sources, setSources] = useState<DataSource[]>([]);
  const [loadingSources, setLoadingSources] = useState(false);
  const [showAddForm, setShowAddForm] = useState(false);

  // Form state
  const [newName, setNewName] = useState('');
  const [newType, setNewType] = useState('ogc_api');
  const [newUrl, setNewUrl] = useState('');
  const [newAllowPrivate, setNewAllowPrivate] = useState(false);
  const [submittingSource, setSubmittingSource] = useState(false);

  // Spatial Catalog state
  const [catalogItems, setCatalogItems] = useState<CatalogItem[]>([]);
  const [catalogTotal, setCatalogTotal] = useState(0);
  const [searchQuery, setSearchQuery] = useState('');
  // A-F-08: the value actually sent to the catalog endpoint; follows searchQuery
  // after a quiet period (debounce), so typing never fires a fetch per keystroke.
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [selectedSourceFilter, setSelectedSourceFilter] = useState('');

  // Modal / Drawer state
  const [activeDescriptor, setActiveDescriptor] = useState<DatasetDescriptor | null>(null);
  const [previewResult, setPreviewResult] = useState<QueryResult | null>(null);
  const [materializingId, setMaterializingId] = useState<string | null>(null);

  const addToast = useToastStore((s) => s.addToast);
  const addLayer = useHudStore((s) => s.addLayer);
  const theme = useHudStore((s) => s.theme);
  const isDark = theme === 'dark';

  const fetchSources = useCallback(async () => {
    setLoadingSources(true);
    try {
      const res = await dataFabricApi.listDataSources();
      setSources(res.sources || []);
    } catch (e) {
      addToast(e instanceof Error ? e.message : '获取数据源列表失败', 'error');
    } finally {
      setLoadingSources(false);
    }
  }, [addToast]);

  // A-F-08: each catalog fetch aborts the previous in-flight one and carries a
  // sequence number, so a slow/stale response can never clobber newer results.
  const catalogReqRef = useRef<{ controller: AbortController | null; seq: number }>({
    controller: null,
    seq: 0,
  });

  const fetchCatalog = useCallback(async () => {
    const seq = ++catalogReqRef.current.seq;
    catalogReqRef.current.controller?.abort();
    const controller = new AbortController();
    catalogReqRef.current.controller = controller;
    setLoadingCatalog(true);
    try {
      const res = await dataFabricApi.listSpatialCatalog({
        q: debouncedSearchQuery,
        source_id: selectedSourceFilter || undefined,
        limit: 50,
        signal: controller.signal,
      });
      if (seq !== catalogReqRef.current.seq) return; // superseded by a newer query
      setCatalogItems(res.items || []);
      setCatalogTotal(res.total || 0);
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return; // superseded / unmount
      addToast(e instanceof Error ? e.message : '获取空间目录失败', 'error');
    } finally {
      if (seq === catalogReqRef.current.seq) setLoadingCatalog(false);
    }
  }, [debouncedSearchQuery, selectedSourceFilter, addToast]);

  // A-F-08: debounce search-as-you-type (the raw input stays immediate).
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearchQuery(searchQuery), CATALOG_SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [searchQuery]);

  // A-F-08: cancel any in-flight catalog fetch on unmount. The ref is a stable
  // mutable data ref (not a rendered node), so reading .current in cleanup is
  // intentional — it must abort the *latest* controller, not the mount-time one.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => catalogReqRef.current.controller?.abort();
  }, []);

  useEffect(() => {
    fetchSources();
  }, [fetchSources]);

  useEffect(() => {
    fetchCatalog();
  }, [fetchCatalog]);

  const handleCreateSource = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim() || !newUrl.trim()) {
      addToast('请填写数据源名称和 Endpoint URL', 'warning');
      return;
    }
    setSubmittingSource(true);
    try {
      await dataFabricApi.createDataSource({
        name: newName.trim(),
        source_type: newType,
        endpoint_url: newUrl.trim(),
        allow_private: newAllowPrivate,
      });
      addToast('数据源注册成功', 'success');
      setShowAddForm(false);
      setNewName('');
      setNewUrl('');
      fetchSources();
      fetchCatalog();
    } catch (err) {
      addToast(err instanceof Error ? err.message : '注册失败', 'error');
    } finally {
      setSubmittingSource(false);
    }
  };

  const handleProbe = async (sourceId: string) => {
    try {
      const res = await dataFabricApi.probeDataSource(sourceId);
      addToast(`连通测试结果: ${res.status} (${res.message})`, res.status === 'healthy' ? 'success' : 'warning');
      fetchSources();
    } catch (err) {
      addToast(err instanceof Error ? err.message : '探测失败', 'error');
    }
  };

  const handleSync = async (sourceId: string) => {
    try {
      const res = await dataFabricApi.syncDataSourceCatalog(sourceId);
      addToast(`目录同步成功，共同步 ${res.synced_count} 个数据集`, 'success');
      fetchCatalog();
    } catch (err) {
      addToast(err instanceof Error ? err.message : '同步失败', 'error');
    }
  };

  const handleDeleteSource = async (sourceId: string) => {
    if (!confirm('确定要删除该数据源及其绑定的目录项吗？')) return;
    try {
      await dataFabricApi.deleteDataSource(sourceId);
      addToast('数据源已删除', 'success');
      fetchSources();
      fetchCatalog();
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

  const renderStatusBadge = (status: string) => {
    if (status === 'healthy' || status === 'active') {
      return (
        <span className="inline-flex items-center gap-1 text-[10px] text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded font-medium">
          <CheckCircle2 size={11} /> 正常
        </span>
      );
    }
    if (status === 'degraded') {
      return (
        <span className="inline-flex items-center gap-1 text-[10px] text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded font-medium">
          <AlertTriangle size={11} /> 降级
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 text-[10px] text-rose-600 bg-rose-50 px-1.5 py-0.5 rounded font-medium">
        <XCircle size={11} /> 离线
      </span>
    );
  };

  return (
    <div className="flex flex-col h-full overflow-hidden text-[13px]">
      {/* Subtab navigation */}
      <div className={`flex shrink-0 border-b px-2.5 pt-2 gap-2 ${isDark ? 'border-zinc-800 bg-zinc-950/20' : 'border-slate-200/80 bg-slate-50/50'}`}>
        <button
          onClick={() => setActiveSubTab('catalog')}
          className={`flex items-center gap-1.5 pb-2 px-2 text-[12.5px] font-medium border-b-2 transition-colors ${
            activeSubTab === 'catalog'
              ? 'border-emerald-600 text-emerald-600'
              : 'border-transparent text-slate-500 hover:text-slate-700'
          }`}
        >
          <Layers size={14} />
          <span>空间目录 ({catalogTotal})</span>
        </button>
        <button
          onClick={() => setActiveSubTab('sources')}
          className={`flex items-center gap-1.5 pb-2 px-2 text-[12.5px] font-medium border-b-2 transition-colors ${
            activeSubTab === 'sources'
              ? 'border-emerald-600 text-emerald-600'
              : 'border-transparent text-slate-500 hover:text-slate-700'
          }`}
        >
          <Database size={14} />
          <span>数据源 ({sources.length})</span>
        </button>
      </div>

      {/* Catalog View */}
      {activeSubTab === 'catalog' && (
        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
          {/* Search bar */}
          <div className="p-2.5 shrink-0 space-y-2 border-b border-slate-100 dark:border-zinc-800">
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-2.5 text-slate-400" />
              <input
                type="text"
                placeholder="搜索空间数据集、图层或关键词..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className={`w-full pl-8 pr-3 py-1.5 text-[12px] rounded-lg border focus:outline-none focus:ring-1 focus:ring-emerald-500 ${
                  isDark ? 'bg-zinc-900 border-zinc-700 text-zinc-200' : 'bg-white border-slate-200 text-slate-700'
                }`}
              />
            </div>

            {sources.length > 0 && (
              <select
                value={selectedSourceFilter}
                onChange={(e) => setSelectedSourceFilter(e.target.value)}
                className={`w-full px-2 py-1 text-[11.5px] rounded border ${
                  isDark ? 'bg-zinc-900 border-zinc-700 text-zinc-300' : 'bg-white border-slate-200 text-slate-600'
                }`}
              >
                <option value="">全部数据源</option>
                {sources.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.source_type})
                  </option>
                ))}
              </select>
            )}
          </div>

          {/* Catalog items list */}
          <div className="flex-1 overflow-y-auto p-2 space-y-2">
            {loadingCatalog ? (
              <div className="py-8 text-center text-slate-400">正在加载空间目录...</div>
            ) : catalogItems.length === 0 ? (
              <div className="py-8 text-center text-slate-400">暂无符合条件的空间数据集</div>
            ) : (
              catalogItems.map((item) => (
                <div
                  key={item.id}
                  className={`p-2.5 rounded-xl border transition-all hover:shadow-sm ${
                    isDark ? 'bg-zinc-900/80 border-zinc-800' : 'bg-white border-slate-100'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <h4 className="font-semibold text-[13px] text-slate-800 dark:text-zinc-100 truncate">
                        {item.title || item.name}
                      </h4>
                      <p className="text-[11px] text-slate-500 dark:text-zinc-400 line-clamp-1 mt-0.5">
                        {item.description || item.name}
                      </p>
                    </div>
                    <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 dark:bg-zinc-800 dark:text-zinc-300 font-mono">
                      {item.geometry_type || 'Vector'}
                    </span>
                  </div>

                  <div className="flex items-center gap-2 mt-2 pt-2 border-t border-slate-100 dark:border-zinc-800/80 text-[10.5px]">
                    <button
                      onClick={() => handleShowDescriptor(item.id)}
                      className="flex items-center gap-1 px-2 py-1 rounded bg-slate-100 hover:bg-slate-200 text-slate-700 dark:bg-zinc-800 dark:hover:bg-zinc-700 dark:text-zinc-300 transition"
                    >
                      <Info size={12} />
                      <span>契约</span>
                    </button>
                    <button
                      onClick={() => handlePreview(item.id)}
                      className="flex items-center gap-1 px-2 py-1 rounded bg-slate-100 hover:bg-slate-200 text-slate-700 dark:bg-zinc-800 dark:hover:bg-zinc-700 dark:text-zinc-300 transition"
                    >
                      <Eye size={12} />
                      <span>预览</span>
                    </button>
                    <button
                      onClick={() => handleMaterializeAndLoad(item)}
                      disabled={materializingId === item.id}
                      className="ml-auto flex items-center gap-1 px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-700 text-white font-medium transition disabled:opacity-50"
                    >
                      <Download size={12} />
                      <span>{materializingId === item.id ? '实例化中...' : '加载至地图'}</span>
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* Sources View */}
      {activeSubTab === 'sources' && (
        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
          <div className="p-2.5 shrink-0 flex items-center justify-between border-b border-slate-100 dark:border-zinc-800">
            <span className="text-[11.5px] font-medium text-slate-500">已注册数据源</span>
            <button
              onClick={() => setShowAddForm(!showAddForm)}
              className="flex items-center gap-1 text-[11.5px] px-2 py-1 rounded bg-emerald-600 hover:bg-emerald-700 text-white font-medium transition"
            >
              <Plus size={13} />
              <span>{showAddForm ? '取消' : '添加数据源'}</span>
            </button>
          </div>

          {/* Form Modal / Dropdown */}
          {showAddForm && (
            <form onSubmit={handleCreateSource} className="p-3 bg-slate-50 dark:bg-zinc-900 border-b border-slate-200 dark:border-zinc-800 space-y-2 shrink-0">
              <h5 className="font-semibold text-[12px] text-slate-800 dark:text-zinc-200">注册新数据源</h5>
              <div>
                <label className="text-[11px] text-slate-500">数据源名称</label>
                <input
                  type="text"
                  placeholder="例如: 国家地理 WFS 服务"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  className="w-full px-2 py-1 text-[11.5px] rounded border border-slate-200 dark:border-zinc-700 dark:bg-zinc-800"
                />
              </div>
              <div className="flex gap-2">
                <div className="w-1/2">
                  <label className="text-[11px] text-slate-500">协议类型</label>
                  <select
                    value={newType}
                    onChange={(e) => setNewType(e.target.value)}
                    className="w-full px-2 py-1 text-[11.5px] rounded border border-slate-200 dark:border-zinc-700 dark:bg-zinc-800"
                  >
                    <option value="ogc_api">OGC API Features</option>
                    <option value="postgis">PostGIS 数据库</option>
                    <option value="wfs">OGC WFS</option>
                    <option value="wms">OGC WMS</option>
                    <option value="wmts">OGC WMTS</option>
                    <option value="arcgis">ArcGIS REST</option>
                    <option value="pmtiles">PMTiles</option>
                  </select>
                </div>
                <div className="w-1/2 flex items-center pt-4">
                  <label className="flex items-center gap-1.5 text-[11px] text-slate-600 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={newAllowPrivate}
                      onChange={(e) => setNewAllowPrivate(e.target.checked)}
                      className="rounded border-slate-300 text-emerald-600"
                    />
                    <span>允许内网 (SSRF)</span>
                  </label>
                </div>
              </div>
              <div>
                <label className="text-[11px] text-slate-500">Endpoint URL / 连接地址</label>
                <input
                  type="text"
                  placeholder="https://..."
                  value={newUrl}
                  onChange={(e) => setNewUrl(e.target.value)}
                  className="w-full px-2 py-1 text-[11.5px] rounded border border-slate-200 dark:border-zinc-700 dark:bg-zinc-800 font-mono"
                />
              </div>
              <button
                type="submit"
                disabled={submittingSource}
                className="w-full py-1.5 text-[12px] bg-emerald-600 hover:bg-emerald-700 text-white font-medium rounded transition disabled:opacity-50"
              >
                {submittingSource ? '提交中...' : '提交注册并同步'}
              </button>
            </form>
          )}

          {/* Sources list */}
          <div className="flex-1 overflow-y-auto p-2 space-y-2">
            {loadingSources ? (
              <div className="py-8 text-center text-slate-400">加载数据源...</div>
            ) : sources.length === 0 ? (
              <div className="py-8 text-center text-slate-400">暂无注册的数据源</div>
            ) : (
              sources.map((s) => (
                <div key={s.id} className="p-2.5 rounded-xl border border-slate-100 dark:border-zinc-800 bg-white dark:bg-zinc-900/80">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-slate-800 dark:text-zinc-100">{s.name}</span>
                    {renderStatusBadge(s.status)}
                  </div>
                  <div className="text-[11px] font-mono text-slate-500 dark:text-zinc-400 truncate mt-1">
                    {s.endpoint_url}
                  </div>
                  <div className="flex items-center gap-2 mt-2 pt-2 border-t border-slate-100 dark:border-zinc-800 text-[10.5px]">
                    <button
                      onClick={() => handleProbe(s.id)}
                      className="flex items-center gap-1 text-slate-600 hover:text-slate-800 dark:text-zinc-400"
                    >
                      <Activity size={12} />
                      <span>探查</span>
                    </button>
                    <button
                      onClick={() => handleSync(s.id)}
                      className="flex items-center gap-1 text-slate-600 hover:text-slate-800 dark:text-zinc-400"
                    >
                      <RefreshCw size={12} />
                      <span>同步</span>
                    </button>
                    <button
                      onClick={() => handleDeleteSource(s.id)}
                      className="ml-auto flex items-center gap-1 text-rose-500 hover:text-rose-700"
                    >
                      <Trash2 size={12} />
                      <span>删除</span>
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* DatasetDescriptor Modal / Drawer */}
      {activeDescriptor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="bg-white dark:bg-zinc-900 rounded-2xl p-4 max-w-md w-full max-h-[80vh] overflow-y-auto space-y-3 shadow-xl">
            <div className="flex items-center justify-between border-b pb-2">
              <h3 className="font-bold text-[14px] text-slate-800 dark:text-zinc-100">DatasetDescriptor 契约</h3>
              <button onClick={() => setActiveDescriptor(null)} className="text-slate-400 hover:text-slate-600">✕</button>
            </div>
            <div className="space-y-2 text-[12px]">
              <div><span className="font-semibold">ID:</span> {activeDescriptor.id}</div>
              <div><span className="font-semibold">标题:</span> {activeDescriptor.title}</div>
              <div><span className="font-semibold">几何类型:</span> {activeDescriptor.geometry_type}</div>
              <div><span className="font-semibold">SRS 坐标系:</span> {activeDescriptor.srs}</div>
              <div><span className="font-semibold">Bounding Box:</span> {JSON.stringify(activeDescriptor.bbox)}</div>
              <div>
                <span className="font-semibold">字段 Schema ({activeDescriptor.fields?.length || 0}):</span>
                <div className="mt-1 bg-slate-50 dark:bg-zinc-800 p-2 rounded max-h-40 overflow-y-auto font-mono text-[11px]">
                  {activeDescriptor.fields?.map((f, i) => (
                    <div key={i}>{f.name}: <span className="text-emerald-600">{f.type}</span></div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Preview Modal */}
      {previewResult && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="bg-white dark:bg-zinc-900 rounded-2xl p-4 max-w-lg w-full max-h-[80vh] overflow-y-auto space-y-3 shadow-xl">
            <div className="flex items-center justify-between border-b pb-2">
              <h3 className="font-bold text-[14px] text-slate-800 dark:text-zinc-100">数据样例预览 ({previewResult.features.length} 要素)</h3>
              <button onClick={() => setPreviewResult(null)} className="text-slate-400 hover:text-slate-600">✕</button>
            </div>
            <div className="bg-slate-900 text-emerald-400 p-3 rounded-xl font-mono text-[11px] overflow-x-auto max-h-60">
              <pre>{JSON.stringify(previewResult.features.slice(0, 3), null, 2)}</pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default DataSourcesTab;
