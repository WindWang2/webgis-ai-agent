'use client';

import { useState, useEffect, useId, type ReactNode } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import type { ExportItem, ExportSettings } from '@/lib/store/hud-types';
import { useMapAction } from '@/lib/contexts/map-action-context';
import { Download, Printer, History, ChevronDown, Lock } from 'lucide-react';
import { API_BASE } from '@/lib/api/config';
import { downloadWithAuth } from '@/lib/api/authenticated-download';
import { describeApiError } from '@/lib/api/transport';
import { useToastStore } from '@/components/ui/toast';
import { useAuthUser } from '@/lib/auth/use-auth-user';
import { IconButton } from '@/components/shared/icon-button';
import { ConfirmAction } from '@/components/shared/confirm-action';
import { EmptyState } from '@/components/shared/empty-state';
import { devOnly } from '@/lib/utils/logger';

const iconForType: Record<string, string> = {
  png: '🖼',
  pdf: '📄',
  svg: '✏️',
  geojson: '📍',
};

const DECORATION_ITEMS = [
  { key: 'showCompass', label: '指北针' },
  { key: 'showScale', label: '比例尺' },
  { key: 'showLegend', label: '图例' },
  { key: 'showWatermark', label: 'AI 水印' },
  { key: 'showMetadata', label: '元数据' },
  { key: 'showGraticules', label: '坐标格网' },
] as const;

const PAPER_LABEL: Record<ExportSettings['paperSize'], string> = {
  screen: '屏幕',
  A4: 'A4',
  A3: 'A3',
};
const ORIENT_LABEL: Record<ExportSettings['orientation'], string> = {
  landscape: '横向',
  portrait: '纵向',
};
const FORMAT_LABEL: Record<ExportSettings['format'], string> = {
  png: 'PNG',
  pdf: 'PDF',
  svg: 'SVG',
  geojson: 'GeoJSON',
};

type SubTab = 'layout' | 'history';

/** 制图排版表单的可折叠分区（渐进式披露）。 */
function StudioSection({
  title,
  summary,
  defaultOpen = false,
  children,
}: {
  title: string;
  summary: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const sectionId = useId();
  return (
    <section
      className="overflow-hidden rounded-md border border-edge-subtle bg-surface-overlay"
    >
      <button
        id={sectionId}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={`${sectionId}-content`}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left transition-colors hover:bg-surface-hover"
      >
        <ChevronDown
          size={14}
          aria-hidden
          className={`shrink-0 text-ink-muted transition-transform ${open ? 'rotate-180' : ''}`}
        />
        <span className="min-w-0 flex-1">
          {/* 长制图标题会折行撑高整行，而下方摘要却在截断 —— 标题同样
              truncate，并给 title 提示补回被截断的全文。 */}
          <span className="block truncate text-body font-semibold text-ink" title={title}>
            {title}
          </span>
          <span className="mt-0.5 block truncate text-caption text-ink-muted">
            {summary}
          </span>
        </span>
      </button>
      {open && (
        <div
          id={`${sectionId}-content`}
          role="region"
          aria-labelledby={sectionId}
          className="border-t border-edge-subtle px-3 pb-3 pt-3"
        >
          {children}
        </div>
      )}
    </section>
  );
}

export function MapStudioTab() {
  const [activeSubTab, setActiveSubTab] = useState<SubTab>('layout');
  const exportSettings = useHudStore((s) => s.exportSettings);
  const updateExportSettings = useHudStore((s) => s.updateExportSettings);
  const leftPanelOpen = useHudStore((s) => s.leftPanelOpen);
  const exports = useHudStore((s) => s.exports);
  const setExports = useHudStore((s) => s.setExports);
  const { dispatchAction } = useMapAction();
  const authUser = useAuthUser();

  // Helper to update specific fields
  const handleChange = (key: keyof typeof exportSettings, value: string | number | boolean) => {
    if (key === 'paperSize' && value === 'A4' && exportSettings.dpi === 300) {
      updateExportSettings({ [key]: value, dpi: 150 });
    } else {
      updateExportSettings({ [key]: value });
    }
  };

  // Export-mode visibility fix: the tab stays mounted while the context panel
  // collapses, so isExportMode must follow leftPanelOpen too — otherwise the
  // export mask stays on the map with the panel hidden. Cleanup still resets
  // false on unmount.
  useEffect(() => {
    updateExportSettings({ isExportMode: activeSubTab === 'layout' && leftPanelOpen });
    return () => {
      updateExportSettings({ isExportMode: false });
    };
  }, [activeSubTab, leftPanelOpen, updateExportSettings]);

  const handleDownload = async (item: ExportItem) => {
    // 审计 F37：downloadName 来自后端响应，但防御性校验路径穿越/特殊字符。
    // 只允许字母数字 + . _ -，拒绝 ../ ? # 等。
    const downloadName = item.filename || item.name;
    if (!downloadName || !/^[a-zA-Z0-9._-]+$/.test(downloadName)) {
      devOnly.warn('[MapStudioTab] 拒绝非法 download filename:', downloadName);
      return;
    }
    // #515: 下载端点只认 Bearer；裸 <a> 导航无法携带 header → 恒 401。
    // 改走 transport 的鉴权 blob 下载（含 401 刷新），文件名以后端
    // Content-Disposition 为准，fallback 到校验过的 downloadName。
    try {
      await downloadWithAuth(
        `${API_BASE}/api/v1/export/download/${encodeURIComponent(downloadName)}`,
        { filename: downloadName },
      );
    } catch (err) {
      // #738: a failed download must be user-visible — devOnly.warn made the
      // button look dead (indistinguishable from a no-op).
      devOnly.warn('[MapStudioTab] 导出下载失败:', err);
      useToastStore.getState().addToast(
        `导出下载失败：${describeApiError(err, '网络错误或文件已失效')}`,
        'error',
      );
    }
  };

  const handleDelete = (id: string) => {
    setExports(exports.filter(e => e.id !== id));
  };

  // 折叠时分区标题下的一行摘要
  const filledDocFields = [
    exportSettings.title,
    exportSettings.subtitle,
    exportSettings.author,
    exportSettings.dataSource,
  ].filter(Boolean).length;
  const docSummary = filledDocFields === 0 ? '尚未填写' : `已填写 ${filledDocFields}/4 项`;
  const enabledElements = DECORATION_ITEMS.filter((el) => exportSettings[el.key]).length;
  const elementSummary = `${enabledElements}/${DECORATION_ITEMS.length} 启用`;
  const outputSummary =
    `${PAPER_LABEL[exportSettings.paperSize]} · ${ORIENT_LABEL[exportSettings.orientation]} · ` +
    `${exportSettings.dpi}dpi · ${FORMAT_LABEL[exportSettings.format]}`;

  const titleId = useId();
  const subtitleId = useId();
  const authorId = useId();
  const dataSourceId = useId();
  const formatId = useId();
  const paperSizeId = useId();
  const orientationId = useId();
  const dpiId = useId();

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Segmented subtab switcher（制图排版 / 导出历史）—— PanelHeader 已提供面板标题 */}
      <div className="shrink-0 border-b border-edge-subtle bg-surface-overlay p-3 pb-2.5">
        <div
          role="tablist"
          aria-label="制图工坊子页签"
          className="flex rounded-md border border-edge-subtle bg-surface-sunken p-0.5 text-meta"
          onKeyDown={(e) => {
            if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
            e.preventDefault();
            // Review P2 修复：APG activation-on-focus —— 方向键切换后焦点跟随
            // 到新激活的 tab（roving tabindex 已随之更新）。
            setActiveSubTab((prev) => {
              const next = prev === 'layout' ? 'history' : 'layout';
              setTimeout(() => document.getElementById(`map-studio-tab-${next}`)?.focus(), 0);
              return next;
            });
          }}
        >
          <button
            role="tab"
            id="map-studio-tab-layout"
            aria-selected={activeSubTab === 'layout'}
            // 仅当前激活 tab 指向实际渲染的 panel（另一 panel 未挂载）
            aria-controls={activeSubTab === 'layout' ? 'map-studio-panel-layout' : undefined}
            tabIndex={activeSubTab === 'layout' ? 0 : -1}
            onClick={() => setActiveSubTab('layout')}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-md py-1.5 font-medium transition-all"
            style={{
              backgroundColor: activeSubTab === 'layout'
                ? 'var(--surface-raised)'
                : 'transparent',
              color: activeSubTab === 'layout'
                ? 'var(--text-primary)'
                : 'var(--text-muted)',
              boxShadow: activeSubTab === 'layout'
                ? '0 1px 2px var(--border-strong)'
                : 'none',
            }}
          >
            <Printer size={13} aria-hidden />
            <span>制图排版</span>
          </button>
          <button
            role="tab"
            id="map-studio-tab-history"
            aria-selected={activeSubTab === 'history'}
            aria-controls={activeSubTab === 'history' ? 'map-studio-panel-history' : undefined}
            tabIndex={activeSubTab === 'history' ? 0 : -1}
            onClick={() => setActiveSubTab('history')}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-md py-1.5 font-medium transition-all"
            style={{
              backgroundColor: activeSubTab === 'history'
                ? 'var(--surface-raised)'
                : 'transparent',
              color: activeSubTab === 'history'
                ? 'var(--text-primary)'
                : 'var(--text-muted)',
              boxShadow: activeSubTab === 'history'
                ? '0 1px 2px var(--border-strong)'
                : 'none',
            }}
          >
            <History size={13} aria-hidden />
            <span>导出历史</span>
            {exports.length > 0 && (
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ backgroundColor: 'var(--agent-accent, #16a34a)' }}
                aria-hidden
              />
            )}
          </button>
        </div>
      </div>

      {/* Main Tab Content */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {activeSubTab === 'layout' ? (
          <div
            role="tabpanel"
            id="map-studio-panel-layout"
            aria-labelledby="map-studio-tab-layout"
            className="p-3 space-y-2"
          >
            {/* 文档：图名 / 作者 / 数据来源 */}
            <StudioSection title="文档" summary={docSummary} defaultOpen>
              <div className="space-y-3">
                <div>
                  <label htmlFor={titleId} className="mb-1.5 block text-meta font-medium text-ink-secondary">
                    主标题
                  </label>
                  <input
                    id={titleId}
                    type="text"
                    value={exportSettings.title}
                    onChange={(e) => handleChange('title', e.target.value)}
                    placeholder="如：成都市高校分布图"
                    className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-3 py-2 text-body font-medium text-ink focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)]"
                  />
                </div>

                <div>
                  <label htmlFor={subtitleId} className="mb-1.5 block text-meta font-medium text-ink-secondary">
                    副标题
                  </label>
                  <input
                    id={subtitleId}
                    type="text"
                    value={exportSettings.subtitle}
                    onChange={(e) => handleChange('subtitle', e.target.value)}
                    placeholder="如：数据来源: OSM, 制图日期: 2026"
                    className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-3 py-2 text-body font-medium text-ink focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)]"
                  />
                </div>

                <div>
                  <label htmlFor={authorId} className="mb-1.5 block text-meta font-medium text-ink-secondary">
                    作者
                  </label>
                  <input
                    id={authorId}
                    type="text"
                    value={exportSettings.author}
                    onChange={(e) => handleChange('author', e.target.value)}
                    placeholder="制图者名称"
                    className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-3 py-2 text-body font-medium text-ink focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)]"
                  />
                </div>

                <div>
                  <label htmlFor={dataSourceId} className="mb-1.5 block text-meta font-medium text-ink-secondary">
                    数据来源
                  </label>
                  <input
                    id={dataSourceId}
                    type="text"
                    value={exportSettings.dataSource}
                    onChange={(e) => handleChange('dataSource', e.target.value)}
                    placeholder="如：OSM, 天地图"
                    className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-3 py-2 text-body font-medium text-ink focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)]"
                  />
                </div>
              </div>
            </StudioSection>

            {/* 地图元素：装饰开关 */}
            <StudioSection title="地图元素" summary={elementSummary}>
              <div className="grid grid-cols-2 gap-2.5">
                {DECORATION_ITEMS.map((el) => {
                  const enabled = exportSettings[el.key];
                  return (
                    <label
                      key={el.key}
                      className="flex items-center gap-2 rounded-md border px-3 py-2.5 text-meta font-medium transition-all cursor-pointer"
                      style={{
                        backgroundColor: enabled
                          ? 'color-mix(in srgb, var(--agent-accent, #16a34a) 12%, transparent)'
                          : 'transparent',
                        borderColor: enabled
                          ? 'color-mix(in srgb, var(--agent-accent, #16a34a) 35%, transparent)'
                          : 'var(--border-subtle)',
                        /* 选中项文字是 accent 作文字 —— 用 text-safe 变体。 */
                        color: enabled
                          ? 'var(--agent-accent)'
                          : 'var(--text-secondary)'
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={enabled}
                        onChange={(e) => handleChange(el.key, e.target.checked)}
                        className="rounded w-3.5 h-3.5"
                        style={{ accentColor: 'var(--agent-accent, #16a34a)' }}
                      />
                      <span>{el.label}</span>
                    </label>
                  );
                })}
              </div>
            </StudioSection>

            {/* 页面与输出：纸张 / 方向 / DPI / 格式 */}
            <StudioSection title="页面与输出" summary={outputSummary}>
              <div className="space-y-3 text-body font-medium">
                <div className="flex items-center justify-between gap-4">
                  <label htmlFor={formatId} className="text-ink-muted">输出格式</label>
                  <select
                    id={formatId}
                    value={exportSettings.format}
                    onChange={(e) => handleChange('format', e.target.value)}
                    className="rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-body text-ink focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)]"
                  >
                    <option value="png">PNG 高清图片</option>
                    <option value="pdf">PDF 印刷文档</option>
                    <option value="svg">SVG（内嵌位图）</option>
                  </select>
                </div>

                <div className="flex items-center justify-between gap-4">
                  <label htmlFor={paperSizeId} className="text-ink-muted">纸张尺寸</label>
                  <select
                    id={paperSizeId}
                    value={exportSettings.paperSize}
                    onChange={(e) => handleChange('paperSize', e.target.value)}
                    className="rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-body text-ink focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)]"
                  >
                    <option value="screen">当前屏幕比例 (Screen)</option>
                    <option value="A4">A4 标准纸张尺寸</option>
                    <option value="A3">A3 大幅面纸张</option>
                  </select>
                </div>

                <div className="flex items-center justify-between gap-4">
                  <label htmlFor={orientationId} className="text-ink-muted">纸张方向</label>
                  <select
                    id={orientationId}
                    value={exportSettings.orientation}
                    onChange={(e) => handleChange('orientation', e.target.value)}
                    disabled={exportSettings.paperSize === 'screen'}
                    className="rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-body text-ink focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)] disabled:opacity-40"
                  >
                    <option value="landscape">横向 (Landscape)</option>
                    <option value="portrait">纵向 (Portrait)</option>
                  </select>
                </div>

                <div className="flex items-center justify-between gap-4">
                  <label htmlFor={dpiId} className="text-ink-muted">解析度 (DPI)</label>
                  <select
                    id={dpiId}
                    value={exportSettings.dpi}
                    onChange={(e) => handleChange('dpi', Number(e.target.value))}
                    className="rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-body text-ink focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)]"
                  >
                    <option value={96}>标准清晰度 (96 DPI)</option>
                    <option value={150}>高清晰度 (150 DPI)</option>
                    {exportSettings.paperSize === 'screen' && <option value={300}>超清印刷 (300 DPI)</option>}
                  </select>
                </div>
              </div>
            </StudioSection>
          </div>
        ) : (
          <div
            role="tabpanel"
            id="map-studio-panel-history"
            aria-labelledby="map-studio-tab-history"
            className="p-2 space-y-1 h-full"
          >
            <div className="flex items-center justify-between px-2 py-1">
              <span className="text-meta font-semibold text-ink-secondary">
                历史生成文件 ({exports.length})
              </span>
              {exports.length > 0 && (
                <ConfirmAction label="清空列表" confirmLabel="确认清空？" onConfirm={() => setExports([])} />
              )}
            </div>

            {exports.length === 0 ? (
              <EmptyState icon={Download} title="暂无已导出的文件" description="完成的导出会出现在这里" />
            ) : (
              <div className="space-y-1 overflow-y-auto">
                {exports.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-center gap-2.5 rounded-md border border-edge-subtle bg-transparent p-2 transition-colors"
                    onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'var(--surface-hover)'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; }}
                  >
                    <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md bg-surface-sunken text-base">
                      {iconForType[item.type] || '📁'}
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="truncate text-meta font-semibold text-ink">
                        {item.name}
                      </div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-caption font-medium">
                        <span className="rounded-sm bg-surface-sunken px-1 font-mono uppercase text-ink-muted">
                          {item.type}
                        </span>
                        <span className="text-ink-disabled" aria-hidden>|</span>
                        <span className="font-mono text-ink-muted">
                          {item.size}
                        </span>
                      </div>
                    </div>

                    <div className="flex items-center gap-1 flex-shrink-0">
                      <IconButton label="下载" icon={Download} iconSize={13} onClick={() => handleDownload(item)} />
                      <ConfirmAction label="删除" confirmLabel="确认删除？" onConfirm={() => handleDelete(item.id)} />
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
        <div className="shrink-0 border-t border-edge-subtle bg-transparent p-3">
          {/* #469：POST /api/v1/export 需要认证。匿名（默认模式）点击必定 401
              且此前静默失败 —— 未登录时禁用按钮并给出可见的登录引导，
              而不是让每次导出无声地失败。 */}
          <button
            className="w-full rounded-md py-1.5 text-body font-semibold text-ink-on-accent transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
            style={{
              background: 'linear-gradient(135deg, var(--agent-accent), color-mix(in srgb, var(--agent-accent) 87%, transparent))',
              boxShadow: '0 4px 12px color-mix(in srgb, var(--agent-accent) 15%, transparent)'
            }}
            disabled={!authUser}
            title={authUser ? undefined : '导出功能需要登录账号（设置 → 账户）'}
            onClick={() => {
              if (!authUser) return;
              dispatchAction({
                command: 'export_map',
                params: { ...exportSettings }
              });
            }}
          >
            {authUser ? (
              <>发布并导出 {exportSettings.format.toUpperCase()}</>
            ) : (
              <span className="inline-flex items-center justify-center gap-1.5">
                <Lock size={13} aria-hidden />
                登录后可导出 {exportSettings.format.toUpperCase()}
              </span>
            )}
          </button>
          {!authUser && (
            <p className="mt-1.5 text-center text-caption text-ink-muted">
              导出需要登录账号 — 请先在 设置 → 账户 登录
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default MapStudioTab;
