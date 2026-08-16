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
import { templatesApi, type TemplateDetail, type TemplateKind, type TemplateSummary } from '@/lib/api/templates';
import { isApiError } from '@/lib/api/transport';
import { applySymbology, type SymbologySinglePayload } from '@/lib/symbology-apply';
import { useMapAction } from '@/lib/contexts/map-action-context';
import { useHudStore } from '@/lib/store/useHudStore';
import { TILE_PROVIDERS } from '@/lib/providers';
import { useToastStore } from '@/components/ui/toast';
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
  /** Fired only when an apply actually landed (parent shows the success toast). */
  onApply?: (t: TemplateSummary) => void;
}

// ============================================================================
// Apply pipeline (issue #465). Each kind lands somewhere real:
//   basemap   → BASE_LAYER_CHANGE through the shared map action queue (the
//               same path the agent chat uses; swaps the actual base layer
//               and syncs the HUD store label)
//   symbology → LAYER_STYLE_UPDATE on the active layer through the queue
//   layout    → export settings store (consumed by the map exporter)
//   thematic/composite → cannot land from the gallery (need a data field /
//               the full agent pipeline) → explicit error, never false success
// ============================================================================

type DispatchAction = ReturnType<typeof useMapAction>['dispatchAction'];

interface HudApplyState {
  layers?: Array<{ id: string }>;
  focusLayerId?: string | null;
  updateExportSettings?: (updates: Record<string, unknown>) => void;
}

type ApplyOutcome = { ok: true; detail: TemplateDetail } | { ok: false; error: string };

/** Resolve a template providerId to a TILE_PROVIDERS entry (mirrors the
 * base_layer_change matcher: exact id/name, then keyword containment). */
function resolveBasemapProvider(providerId: string) {
  const pid = providerId.trim().toLowerCase();
  if (!pid) return undefined;
  return (
    TILE_PROVIDERS.find((p) => p.id.toLowerCase() === pid) ??
    TILE_PROVIDERS.find((p) => p.name.toLowerCase() === pid) ??
    TILE_PROVIDERS.find((p) =>
      p.keywords.some((k) => {
        const kl = k.toLowerCase();
        return kl === pid || pid.includes(kl);
      })
    )
  );
}

/** The layer a gallery symbology pass targets: the focused layer, else first. */
function activeLayerId(hud: HudApplyState): string | undefined {
  const focused = hud.focusLayerId;
  if (focused && hud.layers?.some((l) => l.id === focused)) return focused;
  return hud.layers?.[0]?.id;
}

function applyBasemapTemplate(
  detail: TemplateDetail,
  dispatchAction: DispatchAction
): ApplyOutcome {
  const providerId = detail.payload?.providerId;
  if (typeof providerId !== 'string' || !providerId) {
    return { ok: false, error: `模板「${detail.name}」缺少底图 providerId，无法应用` };
  }
  const provider = resolveBasemapProvider(providerId);
  if (!provider) {
    return { ok: false, error: `未知底图提供者：${providerId}` };
  }
  // Canonical provider name → the queue's base_layer_change exact-matches it,
  // swaps the live map style and syncs the HUD baseLayer label.
  dispatchAction({ command: 'BASE_LAYER_CHANGE', params: { name: provider.name } });
  return { ok: true, detail };
}

function applySymbologyTemplate(
  detail: TemplateDetail,
  dispatchAction: DispatchAction,
  hud: HudApplyState
): ApplyOutcome {
  const layerId = activeLayerId(hud);
  if (!layerId) {
    return { ok: false, error: '当前地图没有可应用样式的图层，请先加载数据' };
  }
  const payload = detail.payload;
  // Categorical symbology needs a field chosen at apply time — the gallery
  // pass has no field picker, so it cannot land here.
  if (!payload || payload.mode !== 'single') {
    return { ok: false, error: '分类符号化需要在图层上选择字段，请通过 Agent 对话应用' };
  }
  const result = applySymbology(payload as unknown as SymbologySinglePayload, layerId);
  dispatchAction({
    command: result.command,
    params: { layer_id: layerId, style: result.params.style_applied },
  });
  return { ok: true, detail };
}

function applyLayoutTemplate(detail: TemplateDetail, hud: HudApplyState): ApplyOutcome {
  const payload = detail.payload as Record<string, unknown> | undefined;
  if (!payload) {
    return { ok: false, error: `模板「${detail.name}」缺少版式配置，无法应用` };
  }
  // Map the layout template's payload onto the export settings the map
  // exporter consumes (paper/legend/compass/scale/graticule toggles).
  const updates: Record<string, unknown> = {};
  if (typeof payload.paperSize === 'string') updates.paperSize = payload.paperSize;
  if (payload.orientation === 'landscape' || payload.orientation === 'portrait') {
    updates.orientation = payload.orientation;
  }
  if (typeof payload.showLegend === 'boolean') updates.showLegend = payload.showLegend;
  if (typeof payload.showNorthArrow === 'boolean') updates.showCompass = payload.showNorthArrow;
  if (typeof payload.showScaleBar === 'boolean') updates.showScale = payload.showScaleBar;
  if (typeof payload.showGrid === 'boolean') updates.showGraticules = payload.showGrid;
  if (Object.keys(updates).length === 0) {
    return { ok: false, error: `模板「${detail.name}」不包含可应用的版式字段` };
  }
  hud.updateExportSettings?.(updates);
  return { ok: true, detail };
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
  const [applyingId, setApplyingId] = useState<string | null>(null);
  const { dispatchAction } = useMapAction();
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

  // Issue #465: list items are summary DTOs — the backend strips `payload`
  // from GET /templates (summary=true default), so reading payload off the
  // card always saw undefined and every apply silently no-opped while the
  // parent still fired a success toast. The apply now (1) fetches the full
  // detail via GET /templates/{id}, (2) only reports success through
  // onApply when the apply actually landed (dispatched to the map action
  // queue / export settings store), and (3) raises an error toast otherwise.
  const handleApply = useCallback(
    async (t: TemplateSummary) => {
      if (applyingId) return;
      setApplyingId(t.id);
      try {
        const detail = await templatesApi.get(t.id);
        const hud = useHudStore.getState();
        let outcome: ApplyOutcome;
        switch (detail.kind) {
          case 'basemap':
            outcome = applyBasemapTemplate(detail, dispatchAction);
            break;
          case 'symbology':
            outcome = applySymbologyTemplate(detail, dispatchAction, hud);
            break;
          case 'layout':
            outcome = applyLayoutTemplate(detail, hud);
            break;
          default:
            // thematic needs a data field + geojson; composite bundles the
            // full agent pipeline — neither can land from the gallery.
            outcome = {
              ok: false,
              error: '专题/复合模板需要数据字段与渲染流水线，请通过 Agent 对话应用',
            };
        }
        if (outcome.ok) {
          onApply?.(outcome.detail);
        } else {
          useToastStore.getState().addToast(outcome.error, 'error');
        }
      } catch (err) {
        console.warn('[TemplateGalleryV2] apply failed:', err);
        const reason = err instanceof Error ? err.message : '未知错误';
        useToastStore.getState().addToast(`模板「${t.name}」应用失败：${reason}`, 'error');
      } finally {
        setApplyingId(null);
      }
    },
    [applyingId, dispatchAction, onApply]
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
                  applying={applyingId === t.id}
                  applyDisabled={applyingId !== null}
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
  applying,
  applyDisabled,
  onSelect,
  onApply,
}: {
  template: TemplateSummary;
  selected: boolean;
  /** This card's apply fetch is in flight (loading state on the button). */
  applying: boolean;
  /** Any card's apply is in flight — one detail fetch at a time. */
  applyDisabled: boolean;
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
          disabled={applyDisabled}
          aria-busy={applying}
          className="rounded-sm px-2 py-1 text-caption font-medium text-agent-accent transition-opacity hover:opacity-80 disabled:opacity-50"
          style={{
            background: 'color-mix(in srgb, var(--agent-accent) 12%, transparent)',
          }}
        >
          {applying ? '应用中…' : '应用'}
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
