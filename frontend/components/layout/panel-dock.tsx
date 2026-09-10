'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { PanelRightClose, PanelBottomClose } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useSyncExternalStore } from 'react';
import {
  getCommittedMapSpec,
  getMapSpecLiveGeneration,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import {
  renderComponent,
} from '@/components/map/map-components';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
import type { RendererContext } from '@/components/map/map-components/types';
import {
  BOTTOM_DOCK_DEFAULT_HEIGHT,
  RIGHT_DOCK_DEFAULT_WIDTH,
  STATIC_DOCK_PANELS,
  type DockArea,
} from '@/lib/store/slices/dockSlice';
import { AttributeTablePanel } from '@/components/table/attribute-table-panel';
import { AgentRunPanel } from '@/components/agent/agent-run-panel';

/**
 * Panel Dock Host（Workspace V2 / Goal C5 → V7 布局系统）—— 轻量 dock 基座的渲染面。
 *
 * - 停靠的面板实例（chart/statistics）从地图 chrome 的 FloatingChrome
 *   定位体系移到右/下停靠区渲染；**内容渲染器与 chrome 同源**（同一
 *   registry + 同一 placement 语义 —— dock 只换宿主，不换组件实现）；
 * - dock 归属是工作区 UI 状态（dockSlice），与语义组件状态（MapSpec
 *   placement/enabled/collapsed）分离 —— 停靠不写 MapSpec，浮动不丢数据；
 * - 多面板停靠时以标签页切换（单面板直接展示）；
 * - 渲染上下文（zoom/centerLat/bearing）取自 HUD viewport —— 面板内容
 *   不依赖地图实例本身（图表/统计均为数据面板）；
 * - V7 布局系统：两区可共存、尺寸可拖拽（键盘可达）并持久化、折叠保留
 *   成员（重新展开即恢复布局）、Escape 折叠。
 */

const RIGHT_DOCK_MIN_WIDTH = 260;
const RIGHT_DOCK_MAX_WIDTH = 560;
const BOTTOM_DOCK_MIN_HEIGHT = 160;
const BOTTOM_DOCK_MAX_HEIGHT = 560;
const RESIZE_STEP = 24;

function useRendererContext(): RendererContext {
  const zoom = useHudStore((s) => Math.round(s.viewport?.zoom ?? 10));
  const centerLat = useHudStore((s) => s.viewport?.center?.[1] ?? 30);
  const specGeneration = useSyncExternalStore(subscribeMapSpecLive, getMapSpecLiveGeneration);
  // committed spec 是唯一地图真相 —— 图例/色条渲染器按 layerId 回读。
  const spec = useMemo<MapSpec | null>(
    () => getCommittedMapSpec(),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- generation drives the re-read
    [specGeneration],
  );
  return useMemo(
    () => ({ spec, zoom, centerLat, bearing: 0 }),
    [spec, zoom, centerLat],
  );
}

function DockedPanelBody({ componentId }: { componentId: string }) {
  // V7：静态工作台面板（属性表等）不来自 MapSpec components —— 分发器
  // 保持无 hooks，hooks 全部下沉到 SpecPanelBody（条件调用红线）。
  if (STATIC_DOCK_PANELS.has(componentId)) {
    return <StaticDockPanel panelId={componentId} />;
  }
  return <SpecPanelBody componentId={componentId} />;
}

/** spec 承载面板：内容渲染器与地图 chrome 同源（committed spec 唯一真相）。 */
function SpecPanelBody({ componentId }: { componentId: string }) {
  const ctx = useRendererContext();
  const specGeneration = useSyncExternalStore(subscribeMapSpecLive, getMapSpecLiveGeneration);
  const component = useMemo(() => {
    const spec = getCommittedMapSpec();
    return (spec?.layout?.components ?? []).find((c) => c.id === componentId) ?? null;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- specGeneration drives re-lookup
  }, [componentId, specGeneration]);
  if (!component || component.enabled === false) return null;
  const node = renderComponent(component, ctx);
  return <>{node}</>;
}

/** V7：静态面板注册表（内容与 spec 演进无关，dock 归属同样有效）。 */
function StaticDockPanel({ panelId }: { panelId: string }) {
  if (panelId === 'attribute-table') return <AttributeTablePanel />;
  if (panelId === 'agent-run') return <AgentRunPanel />;
  return null;
}

/**
 * V7：dock 尺寸拖拽（与 context-panel 同款性能纪律）—— pointermove 只写
 * ref 草稿 + RAF 写 CSS 变量，终止时恰好一次提交 store。不做全局 store 逐
 * 事件写（停靠区宿主与地图 chrome 都订阅尺寸时会被逐帧放大）。
 */
interface DockDragState {
  pointerId: number;
  startPointer: number;
  startSize: number;
  /** 最新钳制草稿尺寸。 */
  size: number;
  rafId: number | null;
  rafPending: boolean;
  detachListeners: Array<() => void>;
}

function DockResizeHandle({
  area,
  currentSize,
  onCommit,
  label,
}: {
  area: DockArea;
  currentSize: number;
  onCommit: (size: number) => void;
  label: string;
}) {
  const dragRef = useRef<DockDragState | null>(null);
  const hostRef = useRef<HTMLElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const min = area === 'right' ? RIGHT_DOCK_MIN_WIDTH : BOTTOM_DOCK_MIN_HEIGHT;
  const max = area === 'right' ? RIGHT_DOCK_MAX_WIDTH : BOTTOM_DOCK_MAX_HEIGHT;
  const clampSize = useCallback(
    (v: number) => Math.min(max, Math.max(min, Math.round(v))),
    [min, max],
  );

  const applyDraft = useCallback((size: number) => {
    // 右区拖左缘：草稿写 width；下区拖顶缘：草稿写 height。
    hostRef.current?.style.setProperty(
      area === 'right' ? '--dock-draft-w' : '--dock-draft-h',
      `${size}px`,
    );
  }, [area]);

  // 拖拽结束后摘除草稿变量：Chrome 根的 width/height 回落到
  // var(--dock-draft-*, {store}px) 的 store 回退值。不摘除的话残留变量
  // 会永远压过后续的 store 变化（双击复位/键盘调整看似失效）。
  const clearDraft = useCallback(() => {
    hostRef.current?.style.removeProperty(area === 'right' ? '--dock-draft-w' : '--dock-draft-h');
  }, [area]);

  const scheduleApply = useCallback(() => {
    const d = dragRef.current;
    if (!d || d.rafPending) return;
    d.rafPending = true;
    const id = requestAnimationFrame(() => {
      d.rafPending = false;
      if (dragRef.current !== d) return;
      applyDraft(d.size);
    });
    d.rafId = id;
  }, [applyDraft]);

  const terminateDrag = useCallback(() => {
    const d = dragRef.current;
    if (!d) return;
    dragRef.current = null;
    if (d.rafId !== null) cancelAnimationFrame(d.rafId);
    d.detachListeners.forEach((detach) => detach());
    if (d.size !== d.startSize) {
      // 先让可见宽度 == 即将提交的宽度，提交后摘除草稿变量 —— store 重渲染
      // 的回退值随即接管（时序：同步 style 写 + 同步 store 写在同一事件里，
      // React 提交前不会闪回旧尺寸）。
      applyDraft(d.size);
      onCommit(d.size);
    }
    clearDraft();
    setDragging(false);
  }, [applyDraft, clearDraft, onCommit]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return;
      if (dragRef.current) return;
      e.preventDefault();
      const d: DockDragState = {
        pointerId: e.pointerId,
        startPointer: area === 'right' ? e.clientX : e.clientY,
        startSize: currentSize,
        size: currentSize,
        rafId: null,
        rafPending: false,
        detachListeners: [],
      };
      dragRef.current = d;
      applyDraft(d.size);
      setDragging(true);
      const onMove = (ev: PointerEvent) => {
        if (dragRef.current !== d || ev.pointerId !== d.pointerId) return;
        const delta = area === 'right' ? d.startPointer - ev.clientX : d.startPointer - ev.clientY;
        d.size = clampSize(d.startSize + delta);
        scheduleApply();
      };
      const onEnd = (ev: PointerEvent) => {
        if (dragRef.current === d && ev.pointerId === d.pointerId) terminateDrag();
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onEnd);
      window.addEventListener('pointercancel', onEnd);
      const blur = () => terminateDrag();
      window.addEventListener('blur', blur);
      d.detachListeners.push(() => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onEnd);
        window.removeEventListener('pointercancel', onEnd);
        window.removeEventListener('blur', blur);
      });
    },
    [area, currentSize, applyDraft, clampSize, scheduleApply, terminateDrag],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const axisPrev = area === 'right' ? 'ArrowLeft' : 'ArrowUp';
      const axisNext = area === 'right' ? 'ArrowRight' : 'ArrowDown';
      if (e.key !== axisPrev && e.key !== axisNext) return;
      e.preventDefault();
      const delta = e.key === axisNext ? RESIZE_STEP : -RESIZE_STEP;
      onCommit(clampSize(currentSize + delta));
    },
    [area, currentSize, clampSize, onCommit],
  );

  return (
    <div
      ref={(el) => {
        hostRef.current = el?.parentElement ?? null;
      }}
      role="separator"
      aria-orientation={area === 'right' ? 'vertical' : 'horizontal'}
      aria-label={label}
      aria-valuenow={currentSize}
      aria-valuemin={min}
      aria-valuemax={max}
      title="拖拽调整尺寸（双击复位）"
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerUp={terminateDrag}
      onPointerCancel={terminateDrag}
      onLostPointerCapture={terminateDrag}
      onKeyDown={onKeyDown}
      onDoubleClick={() => onCommit(area === 'right' ? RIGHT_DOCK_DEFAULT_WIDTH : BOTTOM_DOCK_DEFAULT_HEIGHT)}
      className={
        area === 'right'
          ? 'absolute -left-1 bottom-0 top-0 z-50 w-2 cursor-col-resize touch-none hover:bg-status-accent-soft focus-visible:bg-status-accent-soft'
          : 'absolute -top-1 left-0 right-0 z-50 h-2 cursor-row-resize touch-none hover:bg-status-accent-soft focus-visible:bg-status-accent-soft'
      }
      style={dragging ? { background: 'var(--agent-accent)', opacity: 0.4 } : undefined}
    />
  );
}

function DockChrome({
  area,
  title,
  size,
  bottomInset = 0,
  tabs,
  activePanel,
  onSelect,
  onCollapse,
  onUndock,
  onResize,
}: {
  area: DockArea;
  title: string;
  /** 区尺寸（width/height，读自 store；拖拽中由草稿变量覆盖）。 */
  size: number;
  /** 右区共存的底部抬升（底部停靠区开启时右区不再延伸到视口底）。 */
  bottomInset?: number;
  tabs: Array<{ id: string; label: string }>;
  activePanel: string | null;
  onSelect: (id: string) => void;
  onCollapse: () => void;
  onUndock: () => void;
  onResize: (size: number) => void;
}) {
  // Wave 11（audit 07 P1）：dock 标签页此前无键盘导航 —— roving tabindex +
  // 方向键（WAI-APG tabs；与 nav-rail 同款习惯，水平 tablist 用 ←/→）。
  const tabRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const onTablistKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (tabs.length === 0) return;
      const currentIndex = tabs.findIndex((t) => t.id === activePanel);
      let nextIndex: number | null = null;
      if (e.key === 'ArrowRight') nextIndex = ((currentIndex < 0 ? 0 : currentIndex) + 1) % tabs.length;
      else if (e.key === 'ArrowLeft')
        nextIndex = ((currentIndex < 0 ? 0 : currentIndex) - 1 + tabs.length) % tabs.length;
      else if (e.key === 'Home') nextIndex = 0;
      else if (e.key === 'End') nextIndex = tabs.length - 1;
      if (nextIndex === null) return;
      e.preventDefault();
      const next = tabs[nextIndex];
      onSelect(next.id);
      tabRefs.current.get(next.id)?.focus();
    },
    [tabs, activePanel, onSelect],
  );
  const closeLabel = area === 'right' ? '收起右侧停靠区' : '收起底部停靠区';
  return (
    <div
      data-dock-region={area}
      onKeyDown={(e) => {
        // V7：焦点在停靠区内按 Escape 折叠（焦点返回不可达之外——先折叠，
        // 折叠后整棵子树 visibility:hidden，焦点自然回落 body）。
        if (e.key === 'Escape' && !(e.target as HTMLElement)?.closest('input, textarea, select')) {
          e.stopPropagation();
          onCollapse();
        }
      }}
      className={
        area === 'right'
          ? 'absolute right-0 top-0 z-40 flex flex-col border-l border-edge-subtle bg-surface-panel/95 backdrop-blur-sm shadow-panel'
          : 'absolute bottom-0 left-0 right-0 z-40 flex flex-col border-t border-edge-subtle bg-surface-panel/95 backdrop-blur-sm shadow-panel'
      }
      style={{
        ...(area === 'right'
          ? { width: `var(--dock-draft-w, ${size}px)`, bottom: bottomInset > 0 ? bottomInset : 0, maxWidth: '85vw' }
          : { height: `var(--dock-draft-h, ${size}px)`, maxHeight: '60vh' }),
      }}
      role="region"
      aria-label={title}
    >
      <DockResizeHandle
        area={area}
        currentSize={size}
        onCommit={onResize}
        label={area === 'right' ? '调整右侧停靠区宽度' : '调整底部停靠区高度'}
      />
      <div className="flex shrink-0 items-center gap-1 border-b border-edge-subtle px-panel py-1">
        <span className="eyebrow">{title}</span>
        <span className="flex-1" />
        {tabs.length > 1 && (
          <div
            role="tablist"
            aria-label="停靠面板"
            onKeyDown={onTablistKeyDown}
            className="flex items-center gap-0.5"
          >
            {tabs.map((tab) => (
              <button
                key={tab.id}
                ref={(el) => {
                  if (el) tabRefs.current.set(tab.id, el);
                  else tabRefs.current.delete(tab.id);
                }}
                type="button"
                role="tab"
                aria-selected={tab.id === activePanel}
                tabIndex={tab.id === activePanel ? 0 : -1}
                onClick={() => onSelect(tab.id)}
                className={
                  tab.id === activePanel
                    ? 'rounded-xs border border-status-info-border bg-status-info-soft px-1.5 py-0.5 text-micro text-status-info'
                    : 'rounded-xs px-1.5 py-0.5 text-micro text-ink-muted transition-colors hover:text-ink'
                }
              >
                {tab.label}
              </button>
            ))}
          </div>
        )}
        <button
          type="button"
          aria-label={`全部浮回地图（${area === 'right' ? '右' : '下'}区面板取消停靠）`}
          title="全部浮回地图"
          onClick={onUndock}
          className="rounded-xs p-0.5 text-ink-muted transition-colors hover:text-ink"
        >
          <svg aria-hidden viewBox="0 0 24 24" className="h-icon-md w-icon-md" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <rect x="12" y="12" width="7" height="7" rx="1" fill="currentColor" stroke="none" />
          </svg>
        </button>
        <button
          type="button"
          aria-label={closeLabel}
          onClick={onCollapse}
          className="rounded-xs p-0.5 text-ink-muted transition-colors hover:text-ink"
        >
          {area === 'right' ? (
            <PanelRightClose aria-hidden className="h-icon-md w-icon-md" />
          ) : (
            <PanelBottomClose aria-hidden className="h-icon-md w-icon-md" />
          )}
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-panel">
        {activePanel ? <DockedPanelBody key={activePanel} componentId={activePanel} /> : null}
      </div>
    </div>
  );
}

function panelLabel(type: string, id: string): string {
  if (type === 'chart_panel') return '图表';
  if (type === 'statistics_panel') return '统计';
  if (id === 'attribute-table') return '属性表';
  if (id === 'agent-run') return '执行详情';
  return id;
}

export function PanelDockHost() {
  // committed spec 变化（面板增删/重命名/禁用）时重算标签与实例。
  const specGeneration = useSyncExternalStore(subscribeMapSpecLive, getMapSpecLiveGeneration);
  const rightDock = useHudStore((s) => s.rightDock);
  const bottomDock = useHudStore((s) => s.bottomDock);
  const rightDockWidth = useHudStore((s) => s.rightDockWidth);
  const bottomDockHeight = useHudStore((s) => s.bottomDockHeight);
  const toggleDock = useHudStore((s) => s.toggleDock);
  const undockRegion = useHudStore((s) => s.undockRegion);
  const setActiveDockPanel = useHudStore((s) => s.setActiveDockPanel);
  const setRightDockWidth = useHudStore((s) => s.setRightDockWidth);
  const setBottomDockHeight = useHudStore((s) => s.setBottomDockHeight);

  // 面板标题来自 committed spec 的组件类型（dock 状态不复制语义标签）。
  // spec 变化（增删/重命名）驱动重建 id→type 表；dock 面板列表是调用参数。
  const tabsById = useMemo(() => {
    const spec = getCommittedMapSpec();
    return new Map((spec?.layout?.components ?? []).map((c) => [c.id, c] as const));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- generation drives the re-read
  }, [specGeneration]);
  const pruneDockPanels = useHudStore((s) => s.pruneDockPanels);
  useEffect(() => {
    // spec 演进：离开 MapSpec 的组件实例，其 dock 归属失效（不留空壳/幽灵）。
    // V7（审计 §2-M）：只在 committed spec 是「组件完备文档」时 prune ——
    // SSE 中间态文档可能暂时缺 components 数组（空 id 集），此前每次
    // spec generation 都执行 prune，一次中间态就把全部 dock 归属永久清空。
    // 静态工作台面板恒有效（不来自 spec）。
    const spec = getCommittedMapSpec();
    if (!spec || !Array.isArray(spec.layout?.components)) return;
    pruneDockPanels(new Set([...tabsById.keys(), ...STATIC_DOCK_PANELS]));
  }, [tabsById, pruneDockPanels]);
  const tabsFor = useCallback(
    (ids: string[]) =>
      ids
        .map((id) => {
          // V7：静态工作台面板恒有效（不来自 spec）。
          if (STATIC_DOCK_PANELS.has(id)) return { id, label: panelLabel('', id) };
          const comp = tabsById.get(id);
          return comp ? { id, label: panelLabel(comp.type, id) } : null;
        })
        .filter((t): t is { id: string; label: string } => t !== null),
    [tabsById],
  );

  return (
    <>
      {/* V7（审计 §2-C）：两区独立渲染。此前 IIFE 在右侧停靠时提前 return，
          右/下两个停靠区永远无法同时显示（底部面板静默不可见）。共存时
          右区抬高底部停靠区的高度，避免两区相互压盖。 */}
      {(() => {
        const rightTabs = tabsFor(rightDock.panels);
        const bottomTabs = tabsFor(bottomDock.panels);
        const showRight = rightDock.open && rightTabs.length > 0;
        const showBottom = bottomDock.open && bottomTabs.length > 0;
        return (
          <>
            {showRight && (
              <DockChrome
                key="dock-right"
                area="right"
                title="停靠面板"
                size={rightDockWidth}
                bottomInset={showBottom ? bottomDockHeight : 0}
                tabs={rightTabs}
                activePanel={rightTabs.some((t) => t.id === rightDock.activePanel)
                  ? (rightDock.activePanel as string)
                  : rightTabs[rightTabs.length - 1].id}
                onSelect={(id) => setActiveDockPanel('right', id)}
                onCollapse={() => toggleDock('right')}
                onUndock={() => undockRegion('right')}
                onResize={setRightDockWidth}
              />
            )}
            {showBottom && (
              <DockChrome
                key="dock-bottom"
                area="bottom"
                title="停靠面板"
                size={bottomDockHeight}
                tabs={bottomTabs}
                activePanel={bottomTabs.some((t) => t.id === bottomDock.activePanel)
                  ? (bottomDock.activePanel as string)
                  : bottomTabs[bottomTabs.length - 1].id}
                onSelect={(id) => setActiveDockPanel('bottom', id)}
                onCollapse={() => toggleDock('bottom')}
                onUndock={() => undockRegion('bottom')}
                onResize={setBottomDockHeight}
              />
            )}
          </>
        );
      })()}
    </>
  );
}

export default PanelDockHost;
