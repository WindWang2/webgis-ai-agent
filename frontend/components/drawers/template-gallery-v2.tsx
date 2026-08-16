/**
 * Template Gallery V2 — F-FE-TPL.
 *
 * A focused, performant replacement for the older gallery drawer. Key V2
 * differences:
 *
 *   - Memoized template cards (React.memo + stable callbacks) so the list
 *     does not re-render on every keystroke
 *   - Debounced search (200ms) + AbortSignal so the request can't race
 *     with the user typing
 *   - Pagination (50 per page) via the new backend Page[T] contract; no
 *     one-time fetch of every template
 *   - Each card's preview is metadata-only (no GeoJSON, no map render
 *     during scroll) — the gallery stays light at 50+ templates
 *   - Tabbed by kind: basemap / symbology / layout / thematic / composite
 *   - Built-in cache via the Fast Path (templatesApi.list hits the shared
 *     in-memory cache, so two components opening the gallery share one
 *     roundtrip)
 *
 * UI V3：作为工作区右侧 drawer 挂载（nav rail「模板」入口，与
 * history/settings overlay 互斥）。视觉从永久暗色 slate + 未定义的
 * hud-cyan 收敛到 --theme-* 设计令牌 + accent；补齐 dialog 语义
 * （aria-modal / Escape / focus trap / 焦点归还）。
 */

'use client';

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Loader2, ChevronLeft, ChevronRight, Layers, Map as MapIcon, Palette, Layout as LayoutIcon, BarChart3, Sparkles, X } from 'lucide-react';
import { templatesApi, type TemplateKind, type TemplateSummary } from '@/lib/api/templates';
import { isApiError } from '@/lib/api/transport';
import { applyBaseline, type BasemapPayload } from '@/lib/basemap-apply';
import { applySymbology, type SymbologyPayload } from '@/lib/symbology-apply';
import { resolveThematicPreset } from '@/lib/thematic-apply';
import { resolveStyle } from '@/lib/map-kit/layout-style';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { LoadingState } from '@/components/shared/loading-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { EmptyState } from '@/components/shared/empty-state';
import { IconButton } from '@/components/shared/icon-button';
import { SearchField } from '@/components/shared/search-field';

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 200;

const KIND_TABS: Array<{ kind: TemplateKind | 'all'; label: string; Icon: React.ComponentType<{ className?: string }> }> = [
  { kind: 'all', label: '全部', Icon: Layers },
  { kind: 'basemap', label: '底图', Icon: MapIcon },
  { kind: 'symbology', label: '符号', Icon: Palette },
  { kind: 'layout', label: '版式', Icon: LayoutIcon },
  { kind: 'thematic', label: '专题', Icon: BarChart3 },
  { kind: 'composite', label: '复合', Icon: Sparkles },
];

export interface TemplateGalleryV2Props {
  open: boolean;
  onClose: () => void;
  onApply?: (t: TemplateSummary) => void;
}

export function TemplateGalleryV2({ open, onClose, onApply }: TemplateGalleryV2Props) {
  const [activeKind, setActiveKind] = useState<TemplateKind | 'all'>('all');
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [page, setPage] = useState(0);
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTemplate, setActiveTemplate] = useState<TemplateSummary | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  // Debounce search input so each keystroke does not fire a request.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [search]);

  // Reset page when filters change.
  useEffect(() => {
    setPage(0);
  }, [activeKind, debouncedSearch]);

  // Fetch a single page. AbortController guarantees the response cannot
  // land after the user has moved on (search keystroke, kind switch,
  // drawer close).
  useEffect(() => {
    if (!open) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    templatesApi
      .list({
        kind: activeKind === 'all' ? undefined : activeKind,
        q: debouncedSearch.trim() || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        signal: controller.signal,
      })
      .then((result) => {
        if (controller.signal.aborted) return;
        // Issue #464: the backend returns the Page envelope {items, total,
        // ...} — consume items/total from it. (The old code treated the
        // envelope as a bare array: templates.map threw on every success and
        // the whole-app ErrorBoundary surfaced a System Error page.)
        setTemplates(result.items);
        setTotal(result.total);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if (isApiError(err) || (err instanceof Error && err.name !== 'AbortError')) {
          setError(err instanceof Error ? err.message : '加载模板失败');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [open, activeKind, debouncedSearch, page]);

  // Dialog 焦点管理（共用 hook）：初始聚焦搜索框 / 焦点归还 /
  // document 级 Tab 围栏 + Escape（修复焦点落到非交互区后 trap 失效）。
  useDialogFocus({
    open,
    containerRef: dialogRef,
    onEscape: onClose,
    initialFocusSelector: 'input',
  });

  // Memoized handler so the card list below doesn't re-render on parent
  // updates unrelated to selection.
  const handleSelect = useCallback((t: TemplateSummary) => {
    setActiveTemplate(t);
  }, []);

  const handleApply = useCallback(
    (t: TemplateSummary) => {
      try {
        switch (t.kind) {
          case 'basemap': {
            const payload = (t as unknown as { payload?: BasemapPayload }).payload;
            if (payload?.providerId) {
              applyBaseline(payload);
            }
            break;
          }
          case 'symbology': {
            const payload = (t as unknown as { payload?: SymbologyPayload }).payload;
            if (payload) {
              // The gallery pass doesn't bind a specific layer; the renderer
              // dispatches to the active layer or surfaces a picker.
              applySymbology(payload, '');
            }
            break;
          }
          case 'thematic': {
            const payload = (t as unknown as { payload?: Record<string, unknown> }).payload ?? {};
            // Gallery pass: no active field — empty string signals the
            // renderer to surface a field picker.
            type ThematicMethod = 'quantiles' | 'equal_interval' | 'natural_breaks' | 'lisa';
            const method = payload.method as ThematicMethod | undefined;
            // Thematic preset dispatch is a discriminated union (choropleth
            // vs heatmap) with different optional fields. Branch on variant
            // to give TS the right shape.
            const variant = (payload.variant as 'choropleth' | 'heatmap') ?? 'choropleth';
            if (variant === 'heatmap') {
              resolveThematicPreset(
                {
                  variant: 'heatmap',
                  intensity: (payload as { intensity?: number }).intensity,
                  radius: (payload as { radius?: number }).radius,
                  heatPalette: (payload as { heatPalette?: string[] }).heatPalette,
                },
                ''
              );
            } else {
              const k = (payload as { k?: number }).k;
              const palette = (payload as { palette?: string }).palette;
              // Build the choropleth payload with only the keys the type
              // actually allows (omit `method` entirely when missing — the
              // TypeAdapter treats undefined as invalid).
              const choroplethPayload: {
                variant: 'choropleth';
                method?: 'quantiles' | 'equal_interval' | 'natural_breaks' | 'lisa';
                k?: number;
                palette?: string;
              } = { variant: 'choropleth' };
              if (method) (choroplethPayload as { method: typeof method }).method = method;
              if (k !== undefined) choroplethPayload.k = k;
              if (palette !== undefined) choroplethPayload.palette = palette;
              resolveThematicPreset(
                choroplethPayload as unknown as Parameters<typeof resolveThematicPreset>[0],
                ''
              );
            }
            break;
          }
          case 'layout': {
            const payload = (t as unknown as { payload?: { style?: Record<string, unknown> } }).payload;
            if (payload?.style) {
              resolveStyle(payload.style as never);
            }
            break;
          }
          case 'composite': {
            // Composite templates bundle the other 4 kinds; the agent
            // dispatch pipeline resolves them. Surface a hint for the user.
            console.info('[TemplateGalleryV2] composite apply — use the Agent chat for full pipeline.');
            break;
          }
        }
        onApply?.(t);
      } catch (err) {
        console.warn('[TemplateGalleryV2] apply failed:', err);
      }
    },
    [onApply],
  );

  if (!open) return null;

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const isFirstPage = page === 0;
  const isLastPage = page >= totalPages - 1;

  return (
    <div
      /* V4：scrim 改语义 token（--surface-scrim 即原来的 rgba(15,23,42,0.32)）。
         A：去掉 blur(4px) —— scrim 已经盖住地图，blur 只增加合成成本。 */
      className="fixed inset-0 z-[90] flex justify-end bg-surface-scrim"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="template-gallery-title"
        tabIndex={-1}
        /* 宽度走 --drawer-w（见 globals.css）：约束是"地图仍然可见"，
           两个右侧抽屉共用同一条规则。 */
        className="flex h-full flex-col border-l border-edge-subtle bg-surface-overlay shadow-drawer animate-slide-from-right"
        style={{ width: 'var(--drawer-w)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <Header onClose={onClose} search={search} setSearch={setSearch} loading={loading} />

        <KindTabs active={activeKind} onChange={setActiveKind} />

        <div className="flex-1 overflow-y-auto p-4">
          {error && (
            <InlineNotice variant="error" className="mb-3">
              {error}
            </InlineNotice>
          )}

          {loading && templates.length === 0 ? (
            <LoadingState label="加载模板…" />
          ) : templates.length === 0 ? (
            <EmptyState icon={Sparkles} title="无匹配模板" description="调整关键词或切换分类重试" />
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3" data-testid="tpl-grid">
              {templates.map((t) => (
                <TemplateCard
                  key={t.id}
                  template={t}
                  selected={activeTemplate?.id === t.id}
                  onSelect={handleSelect}
                  onApply={handleApply}
                />
              ))}
            </div>
          )}
        </div>

        <Footer page={page} totalPages={totalPages} isFirstPage={isFirstPage} isLastPage={isLastPage} onPage={setPage} />
      </div>
    </div>
  );
}

// ============================================================================
// Sub-components — kept in the same file because the V2 gallery is one
// logical unit and splitting them adds 3 files for a 200-line UI surface.
// ============================================================================

function Header({
  onClose,
  search,
  setSearch,
  loading,
}: {
  onClose: () => void;
  search: string;
  setSearch: (s: string) => void;
  loading: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-edge-subtle p-4">
      <div className="min-w-0">
        <h2 id="template-gallery-title" className="flex items-center gap-2 text-title font-semibold text-ink">
          <Sparkles className="h-4 w-4 text-agent-accent" aria-hidden />
          地图制图模板库
        </h2>
        <p className="mt-0.5 text-meta text-ink-muted">模板 · 按分类/关键词搜索 · 快速应用</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {/* 受控 + debounce=0：fetch 防抖由组件自身的 200ms debouncedSearch 承担；
            SearchField 提供 Escape 清空与清除按钮。 */}
        <SearchField
          value={search}
          onChange={setSearch}
          placeholder="搜索模板…"
          aria-label="搜索模板"
          debounceMs={0}
        />
        {loading && <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none text-ink-muted" aria-hidden />}
        <IconButton label="关闭模板库" icon={X} onClick={onClose} />
      </div>
    </div>
  );
}

function KindTabs({
  active,
  onChange,
}: {
  active: TemplateKind | 'all';
  onChange: (k: TemplateKind | 'all') => void;
}) {
  return (
    // 分类过滤是 toggle-button 组（不控制 tabpanel），不用 tablist 语义。
    <div aria-label="模板分类" className="flex gap-1 overflow-x-auto border-b border-edge-subtle px-4 pt-2">
      {KIND_TABS.map(({ kind, label, Icon }) => {
        const selected = active === kind;
        return (
          <button
            key={kind}
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(kind)}
            className={`flex items-center gap-1.5 whitespace-nowrap rounded-t-md px-3 py-2 text-body transition-colors ${
              selected ? '' : 'hover:bg-surface-hover'
            }`}
            style={{
              /* 选中项的文字是 accent 作文字 —— 暗色下原色 2.96–3.40:1，
                 用 text-safe 派生；底部的指示条仍是 fill，用原色。 */
              color: selected ? 'var(--agent-accent)' : 'var(--text-secondary)',
              boxShadow: selected ? 'inset 0 -2px 0 var(--agent-accent)' : undefined,
            }}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden />
            {label}
          </button>
        );
      })}
    </div>
  );
}

const TemplateCard = React.memo(function TemplateCard({
  template,
  selected,
  onSelect,
  onApply,
}: {
  template: TemplateSummary;
  selected: boolean;
  onSelect: (t: TemplateSummary) => void;
  onApply: (t: TemplateSummary) => void;
}) {
  // Pre-compute a static color swatch from the template id (stable hash)
  // so the card has visual identity without rendering the actual map.
  const swatch = useMemo(() => swatchFromId(template.id), [template.id]);
  const handleClick = useCallback(() => onSelect(template), [template, onSelect]);
  const handleApplyClick = useCallback(() => onApply(template), [template, onApply]);

  return (
    <div
      onClick={handleClick}
      className="group cursor-pointer rounded-md border p-3 transition-colors"
      style={{
        borderColor: selected ? 'var(--agent-accent)' : 'var(--border-subtle)',
        background: selected ? 'var(--surface-selected)' : 'var(--surface-raised)',
        boxShadow: selected ? '0 0 0 1px color-mix(in srgb, var(--agent-accent) 35%, transparent)' : undefined,
      }}
    >
      <div className="flex items-start gap-2">
        <div
          className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-md font-mono text-xs text-white/90"
          style={{ background: swatch }}
          aria-hidden
        >
          {template.kind === 'composite' ? '⧉' : template.kind[0]?.toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-body font-medium text-ink">{template.name}</div>
          <div className="mt-0.5 line-clamp-2 text-meta text-ink-muted">{template.description || '—'}</div>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {(template.keywords || []).slice(0, 3).map((k) => (
              <span key={k} className="rounded-sm bg-surface-sunken px-1.5 py-0.5 text-micro text-ink-secondary">
                {k}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="mt-2 flex items-center justify-between">
        <span className="font-mono text-micro text-ink-muted">{template.id}</span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            handleApplyClick();
          }}
          className="rounded-sm px-2 py-1 text-caption font-medium text-agent-accent transition-opacity hover:opacity-80"
          style={{
            background: 'color-mix(in srgb, var(--agent-accent) 12%, transparent)',
          }}
        >
          应用
        </button>
      </div>
    </div>
  );
});

function Footer({
  page,
  totalPages,
  isFirstPage,
  isLastPage,
  onPage,
}: {
  page: number;
  totalPages: number;
  isFirstPage: boolean;
  isLastPage: boolean;
  onPage: (p: number) => void;
}) {
  return (
    <div className="flex items-center justify-between border-t border-edge-subtle p-3">
      <span className="text-meta text-ink-muted">第 {page + 1} / {totalPages} 页</span>
      <div className="flex gap-2">
        <button
          onClick={() => onPage(page - 1)}
          disabled={isFirstPage}
          className="flex items-center gap-1 rounded-md border border-edge-subtle bg-surface-raised px-2 py-1 text-meta text-ink-secondary hover:bg-surface-hover disabled:opacity-40"
        >
          <ChevronLeft className="h-3.5 w-3.5" aria-hidden /> 上一页
        </button>
        <button
          onClick={() => onPage(page + 1)}
          disabled={isLastPage}
          className="flex items-center gap-1 rounded-md border border-edge-subtle bg-surface-raised px-2 py-1 text-meta text-ink-secondary hover:bg-surface-hover disabled:opacity-40"
        >
          下一页 <ChevronRight className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
    </div>
  );
}

/** Generate a stable HSL color from the template id (no random per render). */
function swatchFromId(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) {
    h = (h * 31 + id.charCodeAt(i)) % 360;
  }
  return `hsl(${h}, 55%, 35%)`;
}
