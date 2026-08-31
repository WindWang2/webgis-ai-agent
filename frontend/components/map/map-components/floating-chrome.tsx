'use client';
import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { ChevronDown, ChevronRight, RotateCcw, X } from 'lucide-react';
import type { ComponentPlacement, MapSpecComponent } from '@/lib/mapspec-compiler/types';
import {
  commitComponentPatch,
  getComponentOverridesGeneration,
  getComponentPlacementOverride,
  setComponentPlacementOverride,
  subscribeComponentOverrides,
} from '@/lib/mapspec/component-mutation';
import { useHudStore } from '@/lib/store/useHudStore';
import { useSmallViewport } from '@/lib/hooks/use-small-viewport';
import { DEFAULT_POSITION, isFloating, placementStyle, positionClass, resolvePosition, stackedTopStyle } from './helpers';
import { devOnly } from '@/lib/utils/logger';
import { keyboardMoveDelta } from '@/lib/map-components/layout-runtime';
import { COLLAPSIBLE_PANEL_TYPES } from '@/lib/map-components/resolve-layout';


/**
 * FloatingChrome —— 浮动面板交互壳（D4）：拖拽 / 缩放 / 折叠 / 隐藏 / 复位。
 *
 * - 原生 pointer events + setPointerCapture（不引 dnd-kit/framer-motion）；
 * - 手势期间只在 React 本地 state 里做 rAF 节流的瞬态 transform，
 *   pointerup 时一次 commitComponentPatch（无逐帧提交，MapSpec 仍是
 *   唯一 desired-state 源）；
 * - 提交乐观 override（component-mutation store）垫在 spec 之上，回流
 *   收敛后自动失效；
 * - anchor 缺省不动旧槽位语义；拖动锚定面板就地转 floating（D4）。
 */

const EDGE_MARGIN = 8;    // 父容器内边距（拖拽钳制）
const MIN_WIDTH = 160;    // 最小尺寸（缩放钳制）
const MIN_HEIGHT = 120;
const MAX_WIDTH = 960;    // 后端 placement 字段上限（Field le）
const MAX_HEIGHT = 720;

interface Geometry {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface Gesture {
  pointerId: number;
  mode: 'drag' | 'resize';
  startClientX: number;
  startClientY: number;
  origin: Geometry;
  parentSize: { width: number; height: number };
}

/**
 * 乐观 placement 订阅钩子：手势提交后、committed spec 回流前，
 * 渲染 override 版组件（spec 组件被浅拷贝覆盖 placement）。
 */
export function usePlacementPatchedComponent(component: MapSpecComponent): MapSpecComponent {
  // 代数只作重渲信号（#1008 同款 useSyncExternalStore 订阅模式）
  useSyncExternalStore(subscribeComponentOverrides, getComponentOverridesGeneration);
  const override = getComponentPlacementOverride(component.id);
  return override ? { ...component, placement: override } : component;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/** 父容器尺寸（jsdom 无布局 → 0，调用方据此跳过钳制）。 */
function measureParent(el: HTMLElement | null): { width: number; height: number } {
  const parent = (el?.offsetParent ?? el?.parentElement) as HTMLElement | null;
  if (!parent || typeof parent.getBoundingClientRect !== 'function') return { width: 0, height: 0 };
  const rect = parent.getBoundingClientRect();
  return { width: rect.width, height: rect.height };
}

export interface FloatingChromeProps {
  /** 已合并 override 的 spec 组件（usePlacementPatchedComponent 产物）。 */
  component: MapSpecComponent;
  title: string;
  /** variant 透出为 data-variant（测试/样式钩子；视觉差异由渲染器自己组合）。 */
  dataVariant?: string;
  /** transparent variant：去掉 .map-chrome 卡片底（仅留细边）。 */
  transparent?: boolean;
  /** 面板体附加类（渲染器按 variant 组合自己的内容样式）。 */
  bodyClassName?: string;
  className?: string;
  testId?: string;
  /** v2(#1079)：顶槽堆叠索引（锚定态同槽避让）；floating 态忽略。 */
  topSlotIndexes?: Map<MapSpecComponent, number>;
  children: React.ReactNode;
}

/**
 * Workspace V2（Goal C5）：组件停靠在 dock 区时（dockSlice —— 工作区 UI
 * 状态，与语义 placement 分离），本壳改为静态流式渲染（无拖拽/缩放手势、
 * 无绝对定位）—— 同一渲染器在 chrome 与 dock 两个宿主下复用，语义
 * 组件状态（placement/enabled/collapsed）不受停靠影响。
 */
export function FloatingChrome({
  component,
  title,
  dataVariant,
  transparent,
  bodyClassName,
  className,
  testId,
  topSlotIndexes,
  children,
}: FloatingChromeProps) {
  // 内部再合并一次 override（幂等）：直接使用 FloatingChrome 的调用方
  // （渲染器已合并过）与裸组件都能在乐观提交后即时重渲
  const merged = usePlacementPatchedComponent(component);
  // 停靠区归属（工作区状态；单字段选择器，dock 变化才重渲）。
  const dockRegion = useHudStore((s) => s.dockPlacements[component.id] ?? 'float');
  const dockPanel = useHudStore((s) => s.dockPanel);
  const docked = dockRegion !== 'float';
  // Scenario H 视口折叠建议（派生、非持久）：小视口上面板族折叠到标题
  // 条 —— 用户 placement.collapsed 与浮动放置优先（建议不覆盖两者）。
  const smallViewport = useSmallViewport();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const gestureRef = useRef<Gesture | null>(null);
  const keyCommitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingRef = useRef<Geometry | null>(null);
  const rafRef = useRef(0);
  // 手势瞬态几何（仅手势期间存在；pointerup 后清空，渲染回归 spec/override）
  const [transient, setTransient] = useState<Geometry | null>(null);

  useEffect(() => () => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    // v2(review 5/6-B11)：键盘去抖计时器随卸载清理 —— 500ms 窗口内卸载
    // 后触发 durable 提交会对错误会话发 stale placement POST。
    if (keyCommitTimerRef.current) {
      clearTimeout(keyCommitTimerRef.current);
      keyCommitTimerRef.current = null;
    }
  }, []);

  const placement = merged.placement;
  const floating = isFloating(merged);

  function measureOrigin(el: HTMLElement): Geometry {
    const rect = el.getBoundingClientRect();
    const parent = (el.offsetParent ?? el.parentElement) as HTMLElement | null;
    const parentRect = parent?.getBoundingClientRect();
    if (placement?.mode === 'floating') {
      return {
        x: placement.x ?? 0,
        y: placement.y ?? 0,
        width: placement.width ?? Math.round(rect.width),
        height: placement.height ?? Math.round(rect.height),
      };
    }
    // 锚定 → floating 转换：以当前渲染位置/尺寸起步
    return {
      x: Math.round(rect.left - (parentRect?.left ?? 0)),
      y: Math.round(rect.top - (parentRect?.top ?? 0)),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    };
  }

  function computeNext(gesture: Gesture, clientX: number, clientY: number): Geometry {
    const dx = clientX - gesture.startClientX;
    const dy = clientY - gesture.startClientY;
    const { origin, parentSize, mode } = gesture;
    const hasLayout = parentSize.width > 0 && parentSize.height > 0;
    if (mode === 'resize') {
      return {
        x: origin.x,
        y: origin.y,
        width: clamp(
          origin.width + dx,
          MIN_WIDTH,
          hasLayout ? Math.max(MIN_WIDTH, parentSize.width - origin.x - EDGE_MARGIN) : MAX_WIDTH,
        ),
        height: clamp(
          origin.height + dy,
          MIN_HEIGHT,
          hasLayout ? Math.max(MIN_HEIGHT, parentSize.height - origin.y - EDGE_MARGIN) : MAX_HEIGHT,
        ),
      };
    }
    let x = origin.x + dx;
    let y = origin.y + dy;
    if (hasLayout) {
      // 钳制在父容器内（8px 边距；jsdom 无布局时跳过 —— rect 全零会退化）
      x = clamp(x, EDGE_MARGIN, Math.max(EDGE_MARGIN, parentSize.width - origin.width - EDGE_MARGIN));
      y = clamp(y, EDGE_MARGIN, Math.max(EDGE_MARGIN, parentSize.height - origin.height - EDGE_MARGIN));
    }
    return { x, y, width: origin.width, height: origin.height };
  }

  function toPlacement(geometry: Geometry): ComponentPlacement {
    const next: ComponentPlacement = {
      mode: 'floating',
      x: Math.round(geometry.x),
      y: Math.round(geometry.y),
      collapsed: placement?.mode === 'floating' ? (placement.collapsed ?? false) : false,
    };
    if (geometry.width > 0) next.width = Math.round(Math.min(geometry.width, MAX_WIDTH));
    if (geometry.height > 0) next.height = Math.round(Math.min(geometry.height, MAX_HEIGHT));
    if (placement?.mode === 'floating' && placement.zIndex !== undefined) {
      next.zIndex = placement.zIndex;
    }
    return next;
  }

  /** 手势/按钮收尾：乐观 override + 单次 CAS 提交（失败回滚 override）。 */
  function commitPlacement(nextPlacement: ComponentPlacement, override: ComponentPlacement | null): void {
    setComponentPlacementOverride(merged.id, override);
    commitComponentPatch(merged.id, { placement: nextPlacement }).catch((err) => {
      setComponentPlacementOverride(merged.id, null);
      devOnly.warn('[floating-chrome] placement 提交失败，已回滚本地 override', err);
    });
  }

  function startGesture(e: React.PointerEvent<HTMLElement>, mode: 'drag' | 'resize') {
    // 主键才触发手势（button 缺省视为 0 —— 合成事件可能不带 button）
    const button = typeof e.button === 'number' ? e.button : 0;
    if (button !== 0) return;
    const el = containerRef.current;
    if (!el) return;
    gestureRef.current = {
      pointerId: e.pointerId,
      mode,
      startClientX: e.clientX,
      startClientY: e.clientY,
      origin: measureOrigin(el),
      parentSize: measureParent(el),
    };
    pendingRef.current = null;
    try {
      // 指针捕获：移出元素后 move/up 仍路由回手势元素（jsdom 无实现，静默）
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch { /* jsdom/老浏览器无 pointer capture —— 事件仍在元素上派发 */ }
    e.preventDefault();
  }

  function onTitlePointerDown(e: React.PointerEvent<HTMLDivElement>) {
    // 标题栏上的按钮（折叠/复位/隐藏）不触发拖拽
    if ((e.target as HTMLElement).closest('button')) return;
    startGesture(e, 'drag');
  }

  function onPointerMove(e: React.PointerEvent<HTMLElement>) {
    const gesture = gestureRef.current;
    if (!gesture || gesture.pointerId !== e.pointerId) return;
    pendingRef.current = computeNext(gesture, e.clientX, e.clientY);
    if (rafRef.current === 0) {
      // rAF 节流：逐 pointermove 只记账，帧回调里一次性落到本地 state
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = 0;
        if (pendingRef.current) setTransient(pendingRef.current);
      });
    }
  }

  function finishGesture(
    e: React.PointerEvent<HTMLElement>,
    options: { cancelled?: boolean } = {},
  ) {
    const gesture = gestureRef.current;
    if (!gesture || gesture.pointerId !== e.pointerId) return;
    if (options.cancelled) {
      // 指针被系统取消（滚动抢占）——舍弃瞬态，不提交半成品 placement
      gestureRef.current = null;
      pendingRef.current = null;
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = 0;
      }
      setTransient(null);
      return;
    }
    const finalGeometry = computeNext(gesture, e.clientX, e.clientY);
    gestureRef.current = null;
    pendingRef.current = null;
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    setTransient(null);
    const origin = gesture.origin;
    const moved =
      finalGeometry.x !== origin.x || finalGeometry.y !== origin.y ||
      finalGeometry.width !== origin.width || finalGeometry.height !== origin.height;
    if (!moved) return; // 原地点击（无位移）不提交
    const nextPlacement = toPlacement(finalGeometry);
    commitPlacement(nextPlacement, nextPlacement);
  }

  /** 折叠：placement.collapsed 持久化（floating 原地折叠；锚定转 anchor placement）。 */
  function toggleCollapse() {
    const collapsed = !(placement?.collapsed ?? false);
    const nextPlacement: ComponentPlacement = placement?.mode === 'floating'
      ? { ...placement, collapsed }
      : { mode: 'anchor', anchor: resolvePosition(merged), collapsed };
    commitPlacement(nextPlacement, nextPlacement);
  }

  /** 隐藏：enabled=false 提交（spec 回流后组件退出 chrome 面）。 */
  function hidePanel() {
    commitComponentPatch(merged.id, { enabled: false }).catch((err) => {
      devOnly.warn('[floating-chrome] 隐藏提交失败', err);
    });
  }

  /** 复位：清 override + 提交 anchor 缺省槽位（回到类型默认布局）。 */
  function resetPlacement() {
    const anchor = DEFAULT_POSITION[merged.type] ?? resolvePosition(merged) ?? 'top-left';
    const nextPlacement: ComponentPlacement = { mode: 'anchor', anchor };
    commitPlacement(nextPlacement, null);
  }

  const collapsed =
    (placement?.collapsed ?? false)
    || (smallViewport && !docked && !floating && COLLAPSIBLE_PANEL_TYPES.has(merged.type));
  const gestureActive = transient !== null && !docked;
  // 手势期间 inline left/top 优先于槽位类；floating 正常态走 placementStyle
  const resolvedStyle: React.CSSProperties | undefined = gestureActive
    ? {
      left: Math.round(transient.x),
      top: Math.round(transient.y),
      width: transient.width > 0 ? transient.width : undefined,
      height: transient.height > 0 ? transient.height : undefined,
      // 拖拽中浮起（锚定转 floating 的手势也按浮动层级走）
      zIndex: placementStyle(merged)?.zIndex ?? 40,
    }
    : floating
      ? placementStyle(merged)
      : stackedTopStyle(merged, topSlotIndexes);

  // v2(#1079)：键盘移动 —— 方向键 8px、Shift/Alt+方向键 24px；以当前
  // 几何为原点换算 delta 后走与指针手势相同的提交通道（乐观 override +
  // 单次 CAS）。锚定态首次移动即转 floating（与拖拽语义一致）。
  function onTitleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    const delta = keyboardMoveDelta(e.key, e.shiftKey || e.altKey);
    if (!delta) return;
    e.preventDefault();
    const el = containerRef.current;
    if (!el) return;
    const origin = measureOrigin(el);
    const parent = measureParent(el);
    const hasLayout = parent.width > 0 || parent.height > 0;
    const next: Geometry = {
      x: hasLayout
        ? clamp(origin.x + delta.dx, EDGE_MARGIN, Math.max(EDGE_MARGIN, parent.width - origin.width - EDGE_MARGIN))
        : origin.x + delta.dx,
      y: hasLayout
        ? clamp(origin.y + delta.dy, EDGE_MARGIN, Math.max(EDGE_MARGIN, parent.height - origin.height - EDGE_MARGIN))
        : origin.y + delta.dy,
      width: origin.width,
      height: origin.height,
    };
    const nextPlacement = toPlacement(next);
    // v2(review R4-P2-8)：键重复（~30Hz）不得每键一次 CAS —— 乐观 override
    // 即时生效，durable 提交按 500ms 静默去抖（与指针手势的"手势中节流、
    // 收尾单次提交"同款纪律）。
    setComponentPlacementOverride(merged.id, nextPlacement);
    if (keyCommitTimerRef.current) clearTimeout(keyCommitTimerRef.current);
    keyCommitTimerRef.current = setTimeout(() => {
      keyCommitTimerRef.current = null;
      commitPlacement(nextPlacement, nextPlacement);
    }, 500);
  }

  if (docked) {
    // 停靠态：静态流式布局（宿主是 dock 区容器）；保留折叠/隐藏语义
    // （placement.collapsed / enabled 走同一 CAS 通道），“复位”改为
    // 取消停靠（回到地图 chrome 的浮动定位体系）。
    return (
      <div
        role="region"
        aria-label={`${title} 面板（已停靠）`}
        data-testid={testId}
        data-variant={dataVariant}
        data-docked={dockRegion}
        className={`${transparent
          ? 'border border-map-chrome-border bg-transparent text-map-chrome-ink'
          : 'map-chrome text-map-chrome-ink'} relative flex w-full flex-col overflow-hidden rounded-chrome ${className ?? ''}`}
      >
        <div className="flex select-none items-center justify-between gap-2 border-b border-map-chrome-border px-2 py-1">
          <span className="min-w-0 truncate text-caption font-medium text-map-chrome-ink" title={title}>
            {title}
          </span>
          <span className="flex shrink-0 items-center gap-0.5">
            <button
              type="button"
              aria-label={collapsed ? '展开面板' : '折叠面板'}
              onClick={toggleCollapse}
              className="rounded p-0.5 text-map-chrome-ink-muted transition-colors hover:text-map-chrome-ink"
            >
              {collapsed ? (
                <ChevronRight aria-hidden className="h-icon-sm w-icon-sm" />
              ) : (
                <ChevronDown aria-hidden className="h-icon-sm w-icon-sm" />
              )}
            </button>
            <button
              type="button"
              aria-label="取消停靠"
              onClick={() => dockPanel(component.id, 'float')}
              className="rounded p-0.5 text-map-chrome-ink-muted transition-colors hover:text-map-chrome-ink"
            >
              <X aria-hidden className="h-icon-sm w-icon-sm" />
            </button>
          </span>
        </div>
        {collapsed ? null : (
          <div className={`min-h-0 flex-1 ${bodyClassName ?? 'p-2'}`}>{children}</div>
        )}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      role="region"
      aria-label={`${title} 面板（方向键移动，Shift+方向键大幅移动）`}
      data-testid={testId}
      data-variant={dataVariant}
      className={`${transparent
        ? 'border border-map-chrome-border bg-transparent text-map-chrome-ink'
        : 'map-chrome text-map-chrome-ink'} absolute flex flex-col overflow-hidden rounded-chrome ${gestureActive || floating ? '' : `z-30 ${positionClass(merged)}`} ${className ?? ''}`}
      style={resolvedStyle}
    >
      <div
        data-testid={testId ? `${testId}-title-bar` : 'floating-chrome-title-bar'}
        tabIndex={0}
        aria-keyshortcuts="ArrowUp ArrowDown ArrowLeft ArrowRight Shift+ArrowUp Shift+ArrowDown Shift+ArrowLeft Shift+ArrowRight"
        className="flex cursor-grab select-none touch-none items-center justify-between gap-2 border-b border-map-chrome-border px-2 py-1 outline-none focus-visible:ring-1 focus-visible:ring-map-chrome-ink/40 active:cursor-grabbing"
        onKeyDown={onTitleKeyDown}
        onPointerDown={onTitlePointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={finishGesture}
        onPointerCancel={(e) => finishGesture(e, { cancelled: true })}
      >
        <span className="min-w-0 truncate text-caption font-medium text-map-chrome-ink" title={title}>
          {title}
        </span>
        <span className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            aria-label={collapsed ? '展开面板' : '折叠面板'}
            onClick={toggleCollapse}
            className="rounded p-0.5 text-map-chrome-ink-muted transition-colors hover:text-map-chrome-ink"
          >
            {collapsed ? (
              <ChevronRight aria-hidden className="h-icon-sm w-icon-sm" />
            ) : (
              <ChevronDown aria-hidden className="h-icon-sm w-icon-sm" />
            )}
          </button>
          <button
            type="button"
            aria-label="重置位置"
            onClick={resetPlacement}
            className="rounded p-0.5 text-map-chrome-ink-muted transition-colors hover:text-map-chrome-ink"
          >
            <RotateCcw aria-hidden className="h-icon-sm w-icon-sm" />
          </button>
          <button
            type="button"
            aria-label="隐藏面板"
            onClick={hidePanel}
            className="rounded p-0.5 text-map-chrome-ink-muted transition-colors hover:text-map-chrome-ink"
          >
            <X aria-hidden className="h-icon-sm w-icon-sm" />
          </button>
        </span>
      </div>
      {collapsed ? null : (
        <>
          <div className={`min-h-0 flex-1 ${bodyClassName ?? 'p-2'}`}>{children}</div>
          {/* 缩放手柄（东南角） */}
          <div
            aria-hidden
            data-testid={testId ? `${testId}-resize-handle` : 'floating-chrome-resize-handle'}
            className="absolute bottom-0 right-0 h-3 w-3 cursor-se-resize touch-none"
            style={{ background: 'linear-gradient(135deg, transparent 50%, var(--map-chrome-border) 50%)' }}
            onPointerDown={(e) => startGesture(e, 'resize')}
            onPointerMove={onPointerMove}
            onPointerUp={finishGesture}
            onPointerCancel={(e) => finishGesture(e, { cancelled: true })}
          />
        </>
      )}
    </div>
  );
}
