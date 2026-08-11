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
 * The component is intentionally self-contained: importable from a drawer,
 * a popover, or a standalone page. The Twin Seam (apply via local
 * applyBaseline / applySymbology / resolveThematicPreset) is preserved
 * from the older gallery so the user-visible behavior is identical.
 */

'use client';

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Search, Loader2, ChevronLeft, ChevronRight, Layers, Map as MapIcon, Palette, Layout as LayoutIcon, BarChart3, Sparkles } from 'lucide-react';
import { templatesApi, type TemplateKind, type TemplateSummary } from '@/lib/api/templates';
import { isApiError } from '@/lib/api/transport';
import { applyBaseline, type BasemapPayload } from '@/lib/basemap-apply';
import { applySymbology, type SymbologyPayload } from '@/lib/symbology-apply';
import { resolveThematicPreset } from '@/lib/thematic-apply';
import { resolveStyle } from '@/lib/map-kit/layout-style';

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
      .then((data) => {
        if (controller.signal.aborted) return;
        setTemplates(data as unknown as TemplateSummary[]);
        // The backend Page envelope includes total; apiFetch unwraps to the
        // bare list, so we use the page length as a fallback upper bound
        // for "more pages exist" and the user-visible "page X / Y".
        setTotal(data.length === PAGE_SIZE ? (page + 1) * PAGE_SIZE + 1 : page * PAGE_SIZE + data.length);
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
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-sm">
      <div
        role="dialog"
        aria-label="地图制图模板库 V2"
        className="w-full max-w-3xl h-full bg-slate-900 text-slate-100 shadow-2xl flex flex-col border-l border-slate-800"
      >
        <Header onClose={onClose} search={search} setSearch={setSearch} loading={loading} />

        <KindTabs active={activeKind} onChange={setActiveKind} />

        <div className="flex-1 overflow-y-auto p-4">
          {error && (
            <div className="mb-3 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
              {error}
            </div>
          )}

          {loading && templates.length === 0 ? (
            <div className="flex items-center justify-center py-16 text-slate-400">
              <Loader2 className="h-5 w-5 animate-spin mr-2" /> 加载中…
            </div>
          ) : templates.length === 0 ? (
            <div className="text-center text-slate-500 py-12 text-sm">无匹配模板</div>
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
    <div className="p-4 border-b border-slate-800 bg-slate-900/90 flex items-center justify-between">
      <div>
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-hud-cyan" />
          地图制图模板库
          <span className="text-xs font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">V2</span>
        </h2>
        <p className="text-xs text-slate-500 mt-0.5">60+ 模板 · 按 kind/关键词搜索 · 快速应用</p>
      </div>
      <div className="flex items-center gap-2">
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索模板…"
            className="pl-8 pr-3 py-1.5 rounded-md bg-slate-800 border border-slate-700 text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-hud-cyan/50 w-56"
          />
        </div>
        {loading && <Loader2 className="h-4 w-4 animate-spin text-slate-400" />}
        <button
          onClick={onClose}
          className="text-slate-500 hover:text-slate-200 text-xl leading-none px-2"
          aria-label="关闭"
        >
          ×
        </button>
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
    <div className="px-4 pt-3 border-b border-slate-800 flex gap-1">
      {KIND_TABS.map(({ kind, label, Icon }) => (
        <button
          key={kind}
          onClick={() => onChange(kind)}
          className={`px-3 py-2 text-sm rounded-t-md flex items-center gap-1.5 transition-colors ${
            active === kind
              ? 'bg-slate-800 text-hud-cyan border-b-2 border-hud-cyan'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <Icon className="h-3.5 w-3.5" />
          {label}
        </button>
      ))}
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
      className={`group rounded-lg border p-3 cursor-pointer transition-all ${
        selected
          ? 'border-hud-cyan bg-slate-800/60 ring-1 ring-hud-cyan/30'
          : 'border-slate-800 bg-slate-900/40 hover:border-slate-600 hover:bg-slate-800/40'
      }`}
    >
      <div className="flex items-start gap-2">
        <div
          className="w-10 h-10 rounded-md flex-shrink-0 flex items-center justify-center text-xs font-mono text-white/80"
          style={{ background: swatch }}
          aria-hidden
        >
          {template.kind === 'composite' ? '⧉' : template.kind[0]?.toUpperCase()}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-slate-100 truncate">{template.name}</div>
          <div className="text-xs text-slate-500 line-clamp-2 mt-0.5">{template.description || '—'}</div>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {(template.keywords || []).slice(0, 3).map((k) => (
              <span key={k} className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">
                {k}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="mt-2 flex items-center justify-between">
        <span className="text-[10px] text-slate-500 font-mono">{template.id}</span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            handleApplyClick();
          }}
          className="text-[11px] px-2 py-1 rounded bg-hud-cyan/15 text-hud-cyan hover:bg-hud-cyan/25 transition-colors"
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
    <div className="p-3 border-t border-slate-800 flex items-center justify-between bg-slate-900/90">
      <span className="text-xs text-slate-500">第 {page + 1} / {totalPages} 页</span>
      <div className="flex gap-2">
        <button
          onClick={() => onPage(page - 1)}
          disabled={isFirstPage}
          className="px-2 py-1 rounded bg-slate-800 text-slate-300 text-sm flex items-center gap-1 disabled:opacity-40 hover:bg-slate-700"
        >
          <ChevronLeft className="h-4 w-4" /> 上一页
        </button>
        <button
          onClick={() => onPage(page + 1)}
          disabled={isLastPage}
          className="px-2 py-1 rounded bg-slate-800 text-slate-300 text-sm flex items-center gap-1 disabled:opacity-40 hover:bg-slate-700"
        >
          下一页 <ChevronRight className="h-4 w-4" />
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
