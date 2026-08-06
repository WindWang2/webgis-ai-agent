'use client';

import React, { useState, useMemo, useEffect } from 'react';
import {
  Layers,
  Map,
  Palette,
  Layout,
  Search,
  X,
  Plus,
  Check,
  AlertCircle,
  Sparkles,
  Shield,
  UserCheck,
} from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useMapAction } from '@/lib/contexts/map-action-context';
import { applyBaseline } from '@/lib/basemap-apply';
import { applySymbology } from '@/lib/symbology-apply';
import { resolveThematicPreset } from '@/lib/thematic-apply';
import { resolveStyle } from '@/lib/map-kit/layout-style';

export type TemplateKind = 'basemap' | 'symbology' | 'thematic' | 'layout';
export type SourceFilter = 'all' | 'builtin' | 'user';

interface TemplateItem {
  id: string;
  kind: TemplateKind;
  name: string;
  category?: string;
  keywords?: string[];
  description?: string;
  payload: any;
  is_builtin: boolean;
  thumbnail_url?: string;
}

// Built-in fallback template definitions matching app/schemas/template_schema.py SEED_TEMPLATES
const BUILTIN_TEMPLATES: TemplateItem[] = [
  // 1. Basemap templates
  {
    id: 'tmpl_bm_positron',
    kind: 'basemap',
    name: '学术浅色 (Carto Positron)',
    keywords: ['学术', '浅色', 'positron', '矢量底图'],
    description: 'Carto Positron 高清矢量底图，适合学术论文与报告展绘',
    is_builtin: true,
    payload: {
      providerId: 'carto-positron',
      vectorStyleUrl: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
    },
  },
  {
    id: 'tmpl_bm_dark',
    kind: 'basemap',
    name: '暗色仪表盘 (Dark Matter)',
    keywords: ['暗色', '仪表盘', 'dark-matter', '夜间', '矢量底图'],
    description: 'Carto Dark Matter 高精夜间矢量底图，适合酷炫大屏展示',
    is_builtin: true,
    payload: {
      providerId: 'carto-dark-vec',
      vectorStyleUrl: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    },
  },
  {
    id: 'tmpl_bm_grayscale',
    kind: 'basemap',
    name: '灰度街道 (OSM Grayscale)',
    keywords: ['灰度', '街道', 'osm', '黑白'],
    description: 'OSM 瓦片底图 + 饱和度调至 -1 的灰度沉浸效果',
    is_builtin: true,
    payload: {
      providerId: 'osm',
      rasterFilters: { saturation: -1, contrast: 0.1 },
    },
  },
  {
    id: 'tmpl_bm_hybrid',
    kind: 'basemap',
    name: '卫星影像 + 矢量标注',
    keywords: ['影像', '卫星', '混合', '标注'],
    description: 'ESRI 卫星影像作为底层 + Carto Positron 矢量路网与地名标注',
    is_builtin: true,
    payload: {
      providerId: 'esri-img',
      overlays: [{ providerId: 'carto-positron', opacity: 0.9 }],
    },
  },

  // 2. Symbology templates
  {
    id: 'tmpl_sym_admin_blue',
    kind: 'symbology',
    name: '标准行政区划 (清爽蓝)',
    keywords: ['行政区', '蓝色', '单值', '边界'],
    description: '科技蓝填充 (#3b82f6) + 深蓝高亮描边 (stroke: #1d4ed8)',
    is_builtin: true,
    payload: {
      mode: 'single',
      geometry: 'Polygon',
      style: { fill_color: '#3b82f6', opacity: 0.7, stroke_color: '#1d4ed8', stroke_width: 1.5 },
    },
  },
  {
    id: 'tmpl_sym_heat_red',
    kind: 'symbology',
    name: '警示红线区',
    keywords: ['警示', '红色', '热点', '单值'],
    description: '半透明亮红填充 (#ef4444) + 鲜红粗边匡',
    is_builtin: true,
    payload: {
      mode: 'single',
      geometry: 'Polygon',
      style: { fill_color: '#ef4444', opacity: 0.65, stroke_color: '#b91c1c', stroke_width: 2.0 },
    },
  },
  {
    id: 'tmpl_sym_landuse_cat',
    kind: 'symbology',
    name: '土地利用五色分类',
    keywords: ['土地利用', '分类', '五色', 'landuse'],
    description: '居住/商业/工业/绿地/水体五色标准映射',
    is_builtin: true,
    payload: {
      mode: 'categorical',
      geometry: 'Polygon',
      field: 'landuse',
      colorMap: {
        residential: '#fde047',
        commercial: '#f97316',
        industrial: '#a855f7',
        green: '#22c55e',
        water: '#06b6d4',
      },
    },
  },

  // 3. Thematic templates
  {
    id: 'tmpl_th_pop_choro',
    kind: 'thematic',
    name: '经济人口自然断裂分级 (Jenks)',
    keywords: ['人口', 'GDP', '自然断裂', 'Jenks', '分级图'],
    description: '采用 Fisher-Jenks 自然断裂点 5 级分类 + YlOrRd 暖色渐变',
    is_builtin: true,
    payload: {
      variant: 'choropleth',
      method: 'natural_breaks',
      k: 5,
      palette: 'YlOrRd',
    },
  },
  {
    id: 'tmpl_th_viridis_quant',
    kind: 'thematic',
    name: '生态指数分位数 (Viridis)',
    keywords: ['生态', '绿化', '分位数', 'Viridis'],
    description: '5 级等分位数分类 + Viridis 高对比色带',
    is_builtin: true,
    payload: {
      variant: 'choropleth',
      method: 'quantiles',
      k: 5,
      palette: 'Viridis',
    },
  },
  {
    id: 'tmpl_th_heatmap',
    kind: 'thematic',
    name: '热力密集度分布',
    keywords: ['热力图', '密集', '密度', 'heatmap'],
    description: '原生 MapLibre 动态热力图，半径 30px，红黄蓝连续过渡',
    is_builtin: true,
    payload: {
      variant: 'heatmap',
      intensity: 0.85,
      radius: 30,
      heatPalette: ['#0000ff', '#00ffff', '#00ff00', '#ffff00', '#ff0000'],
    },
  },

  // 4. Layout templates
  {
    id: 'tmpl_ly_academic',
    kind: 'layout',
    name: '学术期刊精装版式',
    keywords: ['学术', '论文', 'serif', '双语'],
    description: 'Georgia 衬线字体、深灰色标题、带经纬度网格与标准出图边距',
    is_builtin: true,
    payload: {
      paperSize: 'A4',
      orientation: 'landscape',
      title: '学术研究地图',
      style: {
        fontFamily: 'Georgia, serif',
        titleColor: '#0f172a',
        accentColor: '#334155',
        graticuleColor: '#cbd5e1',
        marginPx: 56,
      },
    },
  },
  {
    id: 'tmpl_ly_presentation',
    kind: 'layout',
    name: '汇报演示暗色大屏',
    keywords: ['汇报', '大屏', '暗色', '青色'],
    description: '高亮青色标题 (#00f2ff)、暗色渐变页眉与宽幅展示布局',
    is_builtin: true,
    payload: {
      paperSize: 'A4',
      orientation: 'landscape',
      title: '城市大数据分析看板',
      style: {
        fontFamily: 'Inter, sans-serif',
        titleColor: '#00f2ff',
        accentColor: '#38bdf8',
        marginPx: 24,
      },
    },
  },
];

export interface TemplateGalleryProps {
  open: boolean;
  onClose: () => void;
  onApplyTemplate?: (template: TemplateItem, options?: { field?: string; layerId?: string }) => void;
}

export function TemplateGallery({ open, onClose, onApplyTemplate }: TemplateGalleryProps) {
  const [activeKind, setActiveKind] = useState<TemplateKind>('basemap');
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');
  const [search, setSearch] = useState('');
  const [templates, setTemplates] = useState<TemplateItem[]>(BUILTIN_TEMPLATES);
  const [promptMessage, setPromptMessage] = useState<string | null>(null);

  // Field picker dialog state for thematic templates
  const [selectedThematicTmpl, setSelectedThematicTmpl] = useState<TemplateItem | null>(null);
  const [selectedField, setSelectedField] = useState<string>('');

  // Save-As modal state
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [newTmplName, setNewTmplName] = useState('');
  const [newTmplDesc, setNewTmplDesc] = useState('');
  const [newTmplKind, setNewTmplKind] = useState<TemplateKind>('symbology');
  const [saveSuccessMsg, setSaveSuccessMsg] = useState<string | null>(null);

  const selectedLayerId = useHudStore((s: any) => s.selectedLayerId);
  const layers = useHudStore((s) => s.layers);
  const setBaseLayer = useHudStore((s) => s.setBaseLayer);
  // Twin seam: the gallery emits the SAME commands as backend apply_template.
  const dispatchAction = useMapAction().dispatchAction;

  // Fetch templates from GET /api/v1/templates or fall back to BUILTIN_TEMPLATES
  useEffect(() => {
    if (!open) return;
    fetch('/api/v1/templates')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          setTemplates(data);
        }
      })
      .catch(() => {
        // Keep BUILTIN_TEMPLATES fallback
      });
  }, [open]);

  const filteredTemplates = useMemo(() => {
    return templates.filter((t) => {
      if (t.kind !== activeKind) return false;
      if (sourceFilter === 'builtin' && !t.is_builtin) return false;
      if (sourceFilter === 'user' && t.is_builtin) return false;

      if (!search.trim()) return true;
      const q = search.toLowerCase();
      return (
        t.name.toLowerCase().includes(q) ||
        (t.description || '').toLowerCase().includes(q) ||
        t.keywords?.some((k) => k.toLowerCase().includes(q))
      );
    });
  }, [templates, activeKind, sourceFilter, search]);

  const handleApply = (tmpl: TemplateItem) => {
    setPromptMessage(null);

    // Twin seam: gallery dispatch mirrors backend apply_template per kind.
    // Both callers MUST emit the same command + payload shape (spec invariant).
    if (tmpl.kind === 'basemap') {
      const providerId = tmpl.payload.providerId;
      if (providerId) {
        setBaseLayer(providerId);
      }
      // applyBaseline produces the resolved MapLibre style (incl. rasterFilters / overlays);
      // emit BASE_LAYER_CHANGE so the handler swaps the full style, not just the name.
      try {
        applyBaseline(tmpl.payload);
        dispatchAction({ command: 'BASE_LAYER_CHANGE', params: { ...tmpl.payload } });
      } catch {
        /* basemap style resolution is best-effort; setBaseLayer above is the fallback */
      }
      onApplyTemplate?.(tmpl);
    } else if (tmpl.kind === 'symbology') {
      if (!selectedLayerId) {
        setPromptMessage('请先选择图层再套用符号化模板');
        return;
      }
      const { command, params } = applySymbology(tmpl.payload, selectedLayerId);
      dispatchAction({ command, params });
      onApplyTemplate?.(tmpl, { layerId: selectedLayerId });
    } else if (tmpl.kind === 'thematic') {
      if (!selectedLayerId) {
        setPromptMessage('请先选择图层再套用专题图模板');
        return;
      }
      // Open field selector — dispatch happens in handleConfirmThematicField
      setSelectedThematicTmpl(tmpl);
      setSelectedField('');
    } else if (tmpl.kind === 'layout') {
      // resolveStyle merges template overrides with light/dark defaults; emit export_map
      // so the next export honors the layout payload (handler reads the layout fields).
      resolveStyle('light', tmpl.payload.style);
      dispatchAction({ command: 'export_map', params: { ...tmpl.payload } });
      onApplyTemplate?.(tmpl);
    }
  };

  const handleConfirmThematicField = () => {
    if (!selectedThematicTmpl || !selectedField.trim()) return;
    // Twin seam: emit add_native_heatmap (heatmap) or create_thematic_map (choropleth),
    // the same variant split as backend apply_template.
    const { toolCall } = resolveThematicPreset(selectedThematicTmpl.payload, selectedField.trim());
    dispatchAction({
      command: toolCall.tool as any,
      params: { ...toolCall.params, layerId: selectedLayerId || undefined },
    });
    onApplyTemplate?.(selectedThematicTmpl, {
      field: selectedField.trim(),
      layerId: selectedLayerId || undefined,
    });
    setSelectedThematicTmpl(null);
  };

  const handleSaveAsTemplate = async () => {
    if (!newTmplName.trim()) return;

    const samplePayloads: Record<TemplateKind, any> = {
      basemap: { providerId: 'carto-positron', vectorStyleUrl: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json' },
      symbology: { mode: 'single', geometry: 'Polygon', style: { fill_color: '#3b82f6', opacity: 0.8 } },
      thematic: { variant: 'choropleth', method: 'natural_breaks', k: 5, palette: 'YlOrRd' },
      layout: { paperSize: 'A4', orientation: 'landscape', title: newTmplName },
    };

    // US32: when saving a symbology template with a selected layer, capture that
    // layer's actual style instead of the hardcoded sample. (No current layer →
    // fall back to the sample so the modal still works.)
    if (newTmplKind === 'symbology' && selectedLayerId) {
      const layer = layers.find((l) => l.id === selectedLayerId);
      const layerStyle = layer?.style;
      if (layerStyle && Object.keys(layerStyle).length > 0) {
        samplePayloads.symbology = {
          mode: 'single',
          geometry: 'Polygon',
          style: { ...layerStyle },
        };
      }
    }

    const reqData = {
      name: newTmplName.trim(),
      kind: newTmplKind,
      description: newTmplDesc.trim() || `用户自定义${newTmplName}`,
      keywords: ['用户', '自定义', newTmplKind],
      payload: samplePayloads[newTmplKind],
    };

    try {
      const res = await fetch('/api/v1/templates', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(reqData),
      });

      if (res.ok) {
        const created = await res.json();
        setTemplates((prev) => [created, ...prev]);
        setSaveSuccessMsg(`已成功保存模板 "${created.name}"`);
        setShowSaveModal(false);
        setNewTmplName('');
        setNewTmplDesc('');
        setSourceFilter('user');
      }
    } catch {
      setPromptMessage('保存模板失败');
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-sm transition-opacity">
      <div
        role="dialog"
        aria-label="地图制图模板库"
        className="w-full max-w-2xl h-full bg-slate-900 text-slate-100 shadow-2xl flex flex-col border-l border-slate-800 animate-in slide-in-from-right duration-200"
      >
        {/* Header */}
        <div className="p-4 border-b border-slate-800 flex items-center justify-between bg-slate-900/90">
          <div className="flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-sky-400" />
            <h2 className="text-lg font-semibold text-slate-100">地图制图模板库</h2>
            <span className="text-xs px-2 py-0.5 rounded-full bg-sky-500/10 text-sky-400 border border-sky-500/20 font-mono">
              Gallery
            </span>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowSaveModal(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-sky-600 hover:bg-sky-500 text-white rounded-lg transition-colors shadow-sm"
            >
              <Plus className="w-4 h-4" />
              另存为模板
            </button>
            <button
              onClick={onClose}
              className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Prompt Alert */}
        {promptMessage && (
          <div className="mx-4 mt-3 p-3 bg-amber-500/10 border border-amber-500/20 text-amber-300 text-xs rounded-lg flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{promptMessage}</span>
          </div>
        )}

        {/* Save Success Alert */}
        {saveSuccessMsg && (
          <div className="mx-4 mt-3 p-3 bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-xs rounded-lg flex items-center gap-2">
            <Check className="w-4 h-4 shrink-0" />
            <span>{saveSuccessMsg}</span>
          </div>
        )}

        {/* 4 Kind Tabs */}
        <div className="px-4 pt-3 border-b border-slate-800 bg-slate-950/40">
          <div className="flex space-x-1">
            <button
              onClick={() => setActiveKind('basemap')}
              className={`flex items-center gap-2 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeKind === 'basemap'
                  ? 'border-sky-400 text-sky-400 bg-sky-500/5'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              <Map className="w-4 h-4" />
              底图模板
            </button>
            <button
              onClick={() => setActiveKind('symbology')}
              className={`flex items-center gap-2 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeKind === 'symbology'
                  ? 'border-sky-400 text-sky-400 bg-sky-500/5'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              <Layers className="w-4 h-4" />
              符号化
            </button>
            <button
              onClick={() => setActiveKind('thematic')}
              className={`flex items-center gap-2 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeKind === 'thematic'
                  ? 'border-sky-400 text-sky-400 bg-sky-500/5'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              <Palette className="w-4 h-4" />
              专题图
            </button>
            <button
              onClick={() => setActiveKind('layout')}
              className={`flex items-center gap-2 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeKind === 'layout'
                  ? 'border-sky-400 text-sky-400 bg-sky-500/5'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              <Layout className="w-4 h-4" />
              版式布局
            </button>
          </div>
        </div>

        {/* Filter & Search Toolbar */}
        <div className="p-4 border-b border-slate-800 flex items-center justify-between gap-3 bg-slate-900">
          <div className="flex items-center bg-slate-800 p-0.5 rounded-lg text-xs">
            <button
              onClick={() => setSourceFilter('all')}
              className={`px-3 py-1 rounded-md transition-all ${
                sourceFilter === 'all' ? 'bg-slate-700 text-white font-medium shadow-sm' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              全部
            </button>
            <button
              onClick={() => setSourceFilter('builtin')}
              className={`px-3 py-1 rounded-md transition-all flex items-center gap-1 ${
                sourceFilter === 'builtin' ? 'bg-slate-700 text-white font-medium shadow-sm' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Shield className="w-3 h-3 text-sky-400" />
              内置
            </button>
            <button
              onClick={() => setSourceFilter('user')}
              className={`px-3 py-1 rounded-md transition-all flex items-center gap-1 ${
                sourceFilter === 'user' ? 'bg-slate-700 text-white font-medium shadow-sm' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <UserCheck className="w-3 h-3 text-indigo-400" />
              我的
            </button>
          </div>

          <div className="relative flex-1 max-w-xs">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索模板名称、关键字..."
              className="w-full pl-9 pr-3 py-1.5 text-xs bg-slate-800 border border-slate-700 rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-sky-500"
            />
          </div>
        </div>

        {/* Template Grid Container */}
        <div className="flex-1 p-4 overflow-y-auto grid grid-cols-2 gap-4">
          {filteredTemplates.length === 0 ? (
            <div className="col-span-2 py-12 text-center text-slate-500 text-sm">
              未找到匹配的模板
            </div>
          ) : (
            filteredTemplates.map((tmpl) => (
              <div
                key={tmpl.id}
                className="bg-slate-800/80 border border-slate-700/80 hover:border-sky-500/60 rounded-xl p-3 flex flex-col justify-between transition-all hover:shadow-lg group"
              >
                <div>
                  {/* Thumbnail Preview Area */}
                  <ThumbnailPreview item={tmpl} />

                  <div className="mt-2.5 flex items-start justify-between gap-2">
                    <h3 className="text-sm font-semibold text-slate-100 group-hover:text-sky-300 transition-colors">
                      {tmpl.name}
                    </h3>
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded font-mono shrink-0 ${
                        tmpl.is_builtin
                          ? 'bg-slate-700 text-slate-300'
                          : 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30'
                      }`}
                    >
                      {tmpl.is_builtin ? '内置' : '我的'}
                    </span>
                  </div>

                  <p className="mt-1 text-xs text-slate-400 line-clamp-2">{tmpl.description}</p>

                  {/* Keywords tags */}
                  {tmpl.keywords && tmpl.keywords.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {tmpl.keywords.slice(0, 3).map((kw, i) => (
                        <span key={i} className="text-[10px] px-1.5 py-0.5 bg-slate-900/60 text-slate-400 rounded">
                          #{kw}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                <div className="mt-3 pt-2 border-t border-slate-700/50 flex items-center justify-between">
                  <span className="text-[10px] font-mono text-slate-500">{tmpl.id}</span>
                  <button
                    onClick={() => handleApply(tmpl)}
                    className="px-3 py-1 text-xs font-medium bg-sky-600 hover:bg-sky-500 text-white rounded-md transition-colors shadow-sm flex items-center gap-1"
                  >
                    套用
                  </button>
                </div>
              </div>
            ))
          )}
        </div>

        {/* Thematic Field Picker Dialog */}
        {selectedThematicTmpl && (
          <div className="absolute inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
            <div className="bg-slate-900 border border-slate-700 rounded-xl p-5 w-full max-w-sm shadow-2xl">
              <h3 className="text-base font-semibold text-slate-100">选择数值分类字段</h3>
              <p className="text-xs text-slate-400 mt-1">
                即将套用专题图模板 &ldquo;{selectedThematicTmpl.name}&rdquo;
              </p>
              <div className="mt-4">
                <label className="block text-xs font-medium text-slate-300 mb-1">字段名称 (Field Name)</label>
                <input
                  type="text"
                  value={selectedField}
                  onChange={(e) => setSelectedField(e.target.value)}
                  placeholder="例如: gdp, population, density"
                  className="w-full px-3 py-2 text-sm bg-slate-800 border border-slate-700 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:border-sky-500"
                />
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <button
                  onClick={() => setSelectedThematicTmpl(null)}
                  className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={handleConfirmThematicField}
                  disabled={!selectedField.trim()}
                  className="px-4 py-1.5 text-xs font-medium bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white rounded-lg transition-colors"
                >
                  确认生成专题图
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Save-As Template Modal */}
        {showSaveModal && (
          <div className="absolute inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
            <div className="bg-slate-900 border border-slate-700 rounded-xl p-5 w-full max-w-md shadow-2xl">
              <h3 className="text-base font-semibold text-slate-100">保存为新模板</h3>
              <div className="mt-4 space-y-3">
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1">模板名称</label>
                  <input
                    type="text"
                    value={newTmplName}
                    onChange={(e) => setNewTmplName(e.target.value)}
                    placeholder="输入模板名称..."
                    className="w-full px-3 py-2 text-sm bg-slate-800 border border-slate-700 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:border-sky-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1">模板类别</label>
                  <select
                    value={newTmplKind}
                    onChange={(e) => setNewTmplKind(e.target.value as TemplateKind)}
                    className="w-full px-3 py-2 text-sm bg-slate-800 border border-slate-700 rounded-lg text-slate-100 focus:outline-none focus:border-sky-500"
                  >
                    <option value="symbology">符号化 (Symbology)</option>
                    <option value="basemap">底图 (Basemap)</option>
                    <option value="thematic">专题图 (Thematic)</option>
                    <option value="layout">版式布局 (Layout)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1">描述</label>
                  <textarea
                    value={newTmplDesc}
                    onChange={(e) => setNewTmplDesc(e.target.value)}
                    placeholder="可选的模板使用说明..."
                    rows={2}
                    className="w-full px-3 py-2 text-sm bg-slate-800 border border-slate-700 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:border-sky-500"
                  />
                </div>
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <button
                  onClick={() => setShowSaveModal(false)}
                  className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={handleSaveAsTemplate}
                  disabled={!newTmplName.trim()}
                  className="px-4 py-1.5 text-xs font-medium bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white rounded-lg transition-colors"
                >
                  保存模板
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Lightweight Static Thumbnail Component ─────────────────────────────

function ThumbnailPreview({ item }: { item: TemplateItem }) {
  if (item.kind === 'basemap') {
    const isVector = item.payload.vectorStyleUrl || item.payload.providerId?.includes('vec');
    return (
      <div className="w-full h-24 rounded-lg bg-slate-950 border border-slate-800 relative overflow-hidden flex items-center justify-center">
        <div
          className={`absolute inset-0 ${
            item.payload.providerId?.includes('dark')
              ? 'bg-gradient-to-br from-slate-900 via-slate-950 to-cyan-950'
              : 'bg-gradient-to-br from-slate-100 via-sky-50 to-slate-200'
          }`}
        />
        <span
          className={`relative text-xs font-semibold px-2.5 py-1 rounded-full border shadow-sm ${
            isVector
              ? 'bg-sky-500/20 text-sky-300 border-sky-500/40'
              : 'bg-amber-500/20 text-amber-300 border-amber-500/40'
          }`}
        >
          {isVector ? 'GL Vector' : 'Raster Tile'}
        </span>
      </div>
    );
  }

  if (item.kind === 'symbology') {
    const style = item.payload.style || {};
    const colorMap = item.payload.colorMap || {};
    const colors = item.payload.mode === 'categorical' ? Object.values(colorMap) as string[] : [style.fill_color || '#3b82f6'];

    return (
      <div className="w-full h-24 rounded-lg bg-slate-950 border border-slate-800 flex items-center justify-center p-3">
        {item.payload.mode === 'categorical' ? (
          <div className="flex gap-1.5 items-center justify-center w-full">
            {colors.slice(0, 5).map((c, i) => (
              <div
                key={i}
                className="w-6 h-12 rounded border border-slate-700 shadow-sm"
                style={{ backgroundColor: c }}
              />
            ))}
          </div>
        ) : (
          <div
            className="w-16 h-12 rounded-md border shadow-md flex items-center justify-center"
            style={{
              backgroundColor: style.fill_color || '#3b82f6',
              opacity: style.opacity ?? 0.8,
              borderColor: style.stroke_color || '#1d4ed8',
              borderWidth: `${style.stroke_width || 1}px`,
            }}
          />
        )}
      </div>
    );
  }

  if (item.kind === 'thematic') {
    const isHeatmap = item.payload.variant === 'heatmap';
    const heatColors = item.payload.heatPalette || ['#0000ff', '#00ff00', '#ffff00', '#ff0000'];

    return (
      <div className="w-full h-24 rounded-lg bg-slate-950 border border-slate-800 p-3 flex flex-col justify-center gap-2">
        <div className="text-[10px] font-mono text-slate-400 flex items-center justify-between">
          <span>{isHeatmap ? 'Heatmap Density' : `Palette (${item.payload.palette || 'YlOrRd'})`}</span>
          <span>{isHeatmap ? '30px' : `${item.payload.k || 5}-breaks`}</span>
        </div>
        <div
          className="w-full h-6 rounded border border-slate-800 shadow-inner"
          style={{
            background: isHeatmap
              ? `linear-gradient(to right, ${heatColors.join(', ')})`
              : 'linear-gradient(to right, #ffffb2, #fed976, #feb24c, #fd8d3c, #f03b20, #bd0026)',
          }}
        />
      </div>
    );
  }

  // Layout kind: wireframe skeleton diagram
  return (
    <div className="w-full h-24 rounded-lg bg-slate-950 border border-slate-800 p-2 flex flex-col justify-between relative overflow-hidden">
      {/* Header bar wireframe */}
      <div className="h-3 w-3/4 bg-slate-700/60 rounded" />
      {/* Map area wireframe */}
      <div className="flex-1 my-1 border border-dashed border-slate-700/60 rounded flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border border-slate-700/50" />
      </div>
      {/* Footer wireframe */}
      <div className="h-2 w-1/2 bg-slate-800/80 rounded" />
    </div>
  );
}
