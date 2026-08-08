'use client';

import { useState, useEffect, useRef } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import type { ExportItem } from '@/lib/store/hud-types';
import { useMapAction } from '@/lib/contexts/map-action-context';
import { Download, Trash2, Printer, History } from 'lucide-react';
import { API_BASE } from '@/lib/api/config';


import { devOnly } from "@/lib/utils/logger";
const iconForType: Record<string, string> = {
  png: '🖼',
  pdf: '📄',
  svg: '✏️',
  geojson: '📍',
};

export function MapStudioTab() {
  const [activeSubTab, setActiveSubTab] = useState<'layout' | 'history'>('layout');
  // 审计 findings.md：清空列表是破坏性操作，加确认状态防误触。
  const [confirmingClear, setConfirmingClear] = useState(false);
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (confirmTimerRef.current) {
        clearTimeout(confirmTimerRef.current);
      }
    };
  }, []);
  const exportSettings = useHudStore((s) => s.exportSettings);
  const updateExportSettings = useHudStore((s) => s.updateExportSettings);
  const { dispatchAction } = useMapAction();

  const exports = useHudStore((s) => s.exports);
  const setExports = useHudStore((s) => s.setExports);
  const theme = useHudStore((s) => s.theme);
  const isDark = theme === 'dark';
  const accentColor = useHudStore((s) => s.accentColor);

  // Helper to update specific fields
  const handleChange = (key: keyof typeof exportSettings, value: string | number | boolean) => {
    if (key === 'paperSize' && value === 'A4' && exportSettings.dpi === 300) {
      updateExportSettings({ [key]: value, dpi: 150 });
    } else {
      updateExportSettings({ [key]: value });
    }
  };

  // Auto-enable export mode when subtab is 'layout'
  useEffect(() => {
    if (activeSubTab === 'layout') {
      updateExportSettings({ isExportMode: true });
    } else {
      updateExportSettings({ isExportMode: false });
    }
    return () => {
      updateExportSettings({ isExportMode: false });
    };
  }, [activeSubTab, updateExportSettings]);

  const handleDownload = (item: ExportItem) => {
    // 审计 F37：downloadName 来自后端响应，但防御性校验路径穿越/特殊字符。
    // 只允许字母数字 + . _ -，拒绝 ../ ? # 等。
    const downloadName = item.filename || item.name;
    if (!downloadName || !/^[a-zA-Z0-9._-]+$/.test(downloadName)) {
      devOnly.warn('[MapStudioTab] 拒绝非法 download filename:', downloadName);
      return;
    }
    const a = document.createElement('a');
    a.href = `${API_BASE}/api/v1/export/download/${encodeURIComponent(downloadName)}`;
    a.download = downloadName;
    a.click();
  };

  const handleDelete = (id: string) => {
    setExports(exports.filter(e => e.id !== id));
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Tab Switcher Header */}
      <div 
        className="p-3 border-b flex flex-col gap-2.5 shrink-0"
        style={{
          borderBottomColor: 'var(--theme-border)',
          backgroundColor: 'var(--theme-bg-glass)'
        }}
      >
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xs font-bold tracking-wide uppercase" style={{ color: 'var(--theme-text-primary)' }}>
              制图工坊
            </h2>
            <p className="text-[14px]" style={{ color: 'var(--theme-text-muted)' }}>
              地图排版设计与历史文件导出
            </p>
          </div>
        </div>

        {/* Segmented Control */}
        <div 
          className="flex p-0.5 rounded-lg text-xs"
          style={{
            backgroundColor: 'var(--theme-bg-muted)',
            border: '1px solid var(--theme-border)'
          }}
        >
          <button
            onClick={() => setActiveSubTab('layout')}
            className="flex-1 py-1.5 rounded-md flex items-center justify-center gap-1.5 font-medium transition-all"
            style={{
              backgroundColor: activeSubTab === 'layout' 
                ? 'var(--theme-bg-subtle)' 
                : 'transparent',
              color: activeSubTab === 'layout'
                ? 'var(--theme-text-primary)'
                : 'var(--theme-text-muted)',
              boxShadow: activeSubTab === 'layout' && !isDark 
                ? '0 1px 3px rgba(0,0,0,0.08)' 
                : 'none',
            }}
          >
            <Printer size={13} />
            <span>制图排版</span>
          </button>
          <button
            onClick={() => setActiveSubTab('history')}
            className="flex-1 py-1.5 rounded-md flex items-center justify-center gap-1.5 font-medium transition-all"
            style={{
              backgroundColor: activeSubTab === 'history' 
                ? 'var(--theme-bg-subtle)' 
                : 'transparent',
              color: activeSubTab === 'history'
                ? 'var(--theme-text-primary)'
                : 'var(--theme-text-muted)',
              boxShadow: activeSubTab === 'history' && !isDark 
                ? '0 1px 3px rgba(0,0,0,0.08)' 
                : 'none',
            }}
          >
            <History size={13} />
            <span>导出历史</span>
            {exports.length > 0 && (
              <span 
                className="w-1.5 h-1.5 rounded-full" 
                style={{ backgroundColor: accentColor }}
              />
            )}
          </button>
        </div>
      </div>

      {/* Main Tab Content */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {activeSubTab === 'layout' ? (
          <div className="p-4 space-y-5">
            {/* Titles */}
            <div className="space-y-3.5">
              <div>
                <label className="block text-[15px] font-semibold uppercase tracking-wider mb-1.5" style={{ color: 'var(--theme-text-secondary)' }}>
                  主标题
                </label>
                <input 
                  type="text" 
                  value={exportSettings.title} 
                  onChange={(e) => handleChange('title', e.target.value)} 
                  placeholder="如：成都市高校分布图" 
                  style={{
                    backgroundColor: 'var(--theme-bg-input)',
                    borderColor: 'var(--theme-border)',
                    color: 'var(--theme-text-primary)'
                  }}
                  className="w-full text-xs border rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-500 font-medium" 
                />
              </div>
              
              <div>
                <label className="block text-[15px] font-semibold uppercase tracking-wider mb-1.5" style={{ color: 'var(--theme-text-secondary)' }}>
                  副标题
                </label>
                <input 
                  type="text" 
                  value={exportSettings.subtitle} 
                  onChange={(e) => handleChange('subtitle', e.target.value)} 
                  placeholder="如：数据来源: OSM, 制图日期: 2026" 
                  style={{
                    backgroundColor: 'var(--theme-bg-input)',
                    borderColor: 'var(--theme-border)',
                    color: 'var(--theme-text-primary)'
                  }}
                  className="w-full text-xs border rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-500 font-medium" 
                />
              </div>

              <div>
                <label className="block text-[15px] font-semibold uppercase tracking-wider mb-1.5" style={{ color: 'var(--theme-text-secondary)' }}>
                  作者
                </label>
                <input
                  type="text"
                  value={exportSettings.author}
                  onChange={(e) => handleChange('author', e.target.value)}
                  placeholder="制图者名称"
                  style={{
                    backgroundColor: 'var(--theme-bg-input)',
                    borderColor: 'var(--theme-border)',
                    color: 'var(--theme-text-primary)'
                  }}
                  className="w-full text-xs border rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-500 font-medium"
                />
              </div>

              <div>
                <label className="block text-[15px] font-semibold uppercase tracking-wider mb-1.5" style={{ color: 'var(--theme-text-secondary)' }}>
                  数据来源
                </label>
                <input
                  type="text"
                  value={exportSettings.dataSource}
                  onChange={(e) => handleChange('dataSource', e.target.value)}
                  placeholder="如：OSM, 天地图"
                  style={{
                    backgroundColor: 'var(--theme-bg-input)',
                    borderColor: 'var(--theme-border)',
                    color: 'var(--theme-text-primary)'
                  }}
                  className="w-full text-xs border rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-500 font-medium"
                />
              </div>
            </div>

            {/* Map Decorations / Elements */}
            <div className="space-y-2 border-t pt-4" style={{ borderColor: 'var(--theme-border)' }}>
              <label className="block text-[15px] font-semibold uppercase tracking-wider mb-2.5" style={{ color: 'var(--theme-text-secondary)' }}>
                地图辅助元素
              </label>
              <div className="grid grid-cols-2 gap-3">
                {[
                  { id: 'compass', key: 'showCompass', label: '指北针' },
                  { id: 'scale', key: 'showScale', label: '比例尺' },
                  { id: 'legend', key: 'showLegend', label: '图例' },
                  { id: 'watermark', key: 'showWatermark', label: 'AI 水印' },
                  { id: 'metadata', key: 'showMetadata', label: '元数据' },
                  { id: 'graticules', key: 'showGraticules', label: '坐标格网' },
                ].map((el) => (
                  <label 
                    key={el.id}
                    className="flex items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer transition-all border text-xs font-medium"
                    style={{
                      backgroundColor: exportSettings[el.key as keyof typeof exportSettings] 
                        ? (isDark ? 'rgba(22,163,74,0.06)' : 'rgba(22,163,74,0.03)')
                        : 'transparent',
                      borderColor: exportSettings[el.key as keyof typeof exportSettings]
                        ? `${accentColor}33`
                        : 'var(--theme-border)',
                      color: exportSettings[el.key as keyof typeof exportSettings]
                        ? (isDark ? '#4ade80' : '#16a34a')
                        : 'var(--theme-text-secondary)'
                    }}
                  >
                    <input 
                      id={`checkbox-${el.id}`}
                      type="checkbox" 
                      checked={exportSettings[el.key as keyof typeof exportSettings] as boolean} 
                      onChange={(e) => handleChange(el.key as keyof typeof exportSettings, e.target.checked)} 
                      className="rounded accent-emerald-600 w-3.5 h-3.5"
                    /> 
                    <span>{el.label}</span>
                  </label>
                ))}
              </div>
            </div>

            {/* Paper & Quality Settings */}
            <div className="space-y-3.5 border-t pt-4 font-medium text-xs" style={{ borderColor: 'var(--theme-border)' }}>
              <label className="block text-[15px] font-semibold uppercase tracking-wider" style={{ color: 'var(--theme-text-secondary)' }}>
                排版与品质输出
              </label>

              <div className="space-y-3">
                <div className="flex items-center justify-between gap-4">
                  <span style={{ color: 'var(--theme-text-muted)' }}>输出格式</span>
                  <select 
                    value={exportSettings.format} 
                    onChange={(e) => handleChange('format', e.target.value)} 
                    style={{
                      backgroundColor: 'var(--theme-bg-input)',
                      borderColor: 'var(--theme-border)',
                      color: 'var(--theme-text-primary)'
                    }}
                    className="text-xs border rounded-lg px-2 py-1.5 focus:outline-none"
                  >
                    <option value="png">PNG 高清图片</option>
                    <option value="pdf">PDF 印刷文档</option>
                    <option value="svg">SVG 矢量图</option>
                  </select>
                </div>

                <div className="flex items-center justify-between gap-4">
                  <span style={{ color: 'var(--theme-text-muted)' }}>纸张尺寸</span>
                  <select 
                    value={exportSettings.paperSize} 
                    onChange={(e) => handleChange('paperSize', e.target.value)} 
                    style={{
                      backgroundColor: 'var(--theme-bg-input)',
                      borderColor: 'var(--theme-border)',
                      color: 'var(--theme-text-primary)'
                    }}
                    className="text-xs border rounded-lg px-2 py-1.5 focus:outline-none"
                  >
                    <option value="screen">当前屏幕比例 (Screen)</option>
                    <option value="A4">A4 标准纸张尺寸</option>
                    <option value="A3">A3 大幅面纸张</option>
                  </select>
                </div>

                <div className="flex items-center justify-between gap-4">
                  <span style={{ color: 'var(--theme-text-muted)' }}>纸张方向</span>
                  <select 
                    value={exportSettings.orientation} 
                    onChange={(e) => handleChange('orientation', e.target.value)} 
                    disabled={exportSettings.paperSize === 'screen'} 
                    style={{
                      backgroundColor: 'var(--theme-bg-input)',
                      borderColor: 'var(--theme-border)',
                      color: 'var(--theme-text-primary)'
                    }}
                    className="text-xs border rounded-lg px-2 py-1.5 focus:outline-none disabled:opacity-40"
                  >
                    <option value="landscape">横向 (Landscape)</option>
                    <option value="portrait">纵向 (Portrait)</option>
                  </select>
                </div>

                <div className="flex items-center justify-between gap-4">
                  <span style={{ color: 'var(--theme-text-muted)' }}>解析度 (DPI)</span>
                  <select 
                    value={exportSettings.dpi} 
                    onChange={(e) => handleChange('dpi', Number(e.target.value))} 
                    style={{
                      backgroundColor: 'var(--theme-bg-input)',
                      borderColor: 'var(--theme-border)',
                      color: 'var(--theme-text-primary)'
                    }}
                    className="text-xs border rounded-lg px-2 py-1.5 focus:outline-none"
                  >
                    <option value={96}>标准清晰度 (96 DPI)</option>
                    <option value={150}>高清晰度 (150 DPI)</option>
                    {exportSettings.paperSize === 'screen' && <option value={300}>超清印刷 (300 DPI)</option>}
                  </select>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="p-2 space-y-1 h-full">
            <div className="flex items-center justify-between px-2 py-1">
              <span className="text-[14px] font-semibold uppercase tracking-wider" style={{ color: 'var(--theme-text-muted)' }}>
                历史生成文件 ({exports.length})
              </span>
              {exports.length > 0 && (
                <button
                  onClick={() => {
                    if (confirmingClear) {
                      setExports([]);
                      setConfirmingClear(false);
                      if (confirmTimerRef.current) {
                        clearTimeout(confirmTimerRef.current);
                        confirmTimerRef.current = null;
                      }
                    } else {
                      setConfirmingClear(true);
                      // 3 秒后自动取消确认状态，避免用户误点后永久停留在"确认"文字
                      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
                      confirmTimerRef.current = setTimeout(() => setConfirmingClear(false), 3000);
                    }
                  }}
                  onBlur={() => setConfirmingClear(false)}
                  className="text-[14px] px-2 py-1 rounded transition-colors"
                  style={{ color: '#ef4444' }}
                  onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'rgba(239,68,68,0.15)'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; }}
                  aria-label={confirmingClear ? '再次点击确认清空所有导出文件' : '清空导出列表'}
                >
                  {confirmingClear ? '确认清空？' : '清空列表'}
                </button>
              )}
            </div>

            {exports.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-center px-6">
                <div className="w-10 h-10 rounded-xl flex items-center justify-center mb-2.5" style={{ backgroundColor: 'var(--theme-bg-muted)' }}>
                  <Download size={16} style={{ color: 'var(--theme-text-subtle)' }} />
                </div>
                <p className="text-[15px] font-medium" style={{ color: 'var(--theme-text-muted)' }}>暂无已导出的文件</p>
              </div>
            ) : (
              <div className="space-y-1 overflow-y-auto">
                {exports.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-center gap-2.5 p-2 rounded-lg transition-colors border"
                    style={{ 
                      backgroundColor: 'transparent',
                      borderColor: 'var(--theme-border)'
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'var(--theme-bg-hover)'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; }}
                  >
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center text-base flex-shrink-0" style={{ backgroundColor: 'var(--theme-bg-muted)' }}>
                      {iconForType[item.type] || '📁'}
                    </div>
                    
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-semibold truncate" style={{ color: 'var(--theme-text-primary)' }}>
                        {item.name}
                      </div>
                      <div className="flex items-center gap-1.5 mt-0.5 text-[15px] font-medium">
                        <span className="uppercase px-1 rounded font-mono" style={{ backgroundColor: 'var(--theme-bg-muted)', color: 'var(--theme-text-muted)' }}>
                          {item.type}
                        </span>
                        <span style={{ color: 'var(--theme-text-subtle)' }}>|</span>
                        <span className="font-mono" style={{ color: 'var(--theme-text-muted)' }}>
                          {item.size}
                        </span>
                      </div>
                    </div>

                    <div className="flex items-center gap-0.5 flex-shrink-0">
                      <button
                        onClick={() => handleDownload(item)}
                        aria-label="下载"
                        className="p-1.5 rounded-md transition-colors"
                        style={{ color: 'var(--theme-text-secondary)' }}
                        onMouseEnter={(e) => { 
                          e.currentTarget.style.backgroundColor = 'rgba(22,163,74,0.1)'; 
                          e.currentTarget.style.color = '#16a34a'; 
                        }}
                        onMouseLeave={(e) => { 
                          e.currentTarget.style.backgroundColor = 'transparent'; 
                          e.currentTarget.style.color = 'var(--theme-text-secondary)'; 
                        }}
                        title="下载"
                      >
                        <Download size={13} />
                      </button>
                      <button
                        onClick={() => handleDelete(item.id)}
                        aria-label="删除"
                        className="p-1.5 rounded-md transition-colors"
                        style={{ color: 'var(--theme-text-secondary)' }}
                        onMouseEnter={(e) => { 
                          e.currentTarget.style.backgroundColor = 'rgba(239,68,68,0.1)'; 
                          e.currentTarget.style.color = '#ef4444'; 
                        }}
                        onMouseLeave={(e) => { 
                          e.currentTarget.style.backgroundColor = 'transparent'; 
                          e.currentTarget.style.color = 'var(--theme-text-secondary)'; 
                        }}
                        title="删除"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Action Footer for layout */}
      {activeSubTab === 'layout' && (
        <div 
          className="p-3 bg-transparent border-t shrink-0" 
          style={{ borderColor: 'var(--theme-border)' }}
        >
          <button 
            className="w-full text-white font-bold py-2 rounded-lg shadow-md transition-all text-xs" 
            style={{ 
              background: `linear-gradient(135deg, ${accentColor}, ${accentColor}dd)`,
              boxShadow: `0 4px 12px ${accentColor}25`
            }}
            onClick={() => {
              dispatchAction({
                command: 'export_map',
                params: { ...exportSettings }
              });
            }}
          >
            发布并导出 {exportSettings.format.toUpperCase()}
          </button>
        </div>
      )}
    </div>
  );
}

export default MapStudioTab;
