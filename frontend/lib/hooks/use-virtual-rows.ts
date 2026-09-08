'use client';
/**
 * 轻量行窗口虚拟化（Workbench V5 / W8）—— 10k 图层树不 O(N) 渲染。
 *
 * 设计约束：
 * - 固定行高（uniform rowHeight）：算术确定性，无测量循环、无布局抖动；
 * - 自研 ~40 行：不引第三方依赖（仓库无 react-window/virtuoso）；
 * - ResizeObserver 测视口高（不可用时回退 640px）；
 * - overscan 默认 8：滚动时上下缓冲，避免快速滚动白屏。
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export interface VirtualRowsResult {
  scrollRef: React.RefObject<HTMLDivElement | null>;
  /** 渲染窗口 [start, end)（含 overscan）。 */
  start: number;
  end: number;
  /** 总内容高度 = itemCount * rowHeight（撑出原生滚动条）。 */
  totalHeight: number;
  /** 窗口 translateY 偏移（= start * rowHeight）。 */
  offsetY: number;
  onScroll: () => void;
}

const FALLBACK_VIEWPORT_H = 640;

export function useVirtualRows(
  itemCount: number,
  rowHeight: number,
  overscan = 8,
): VirtualRowsResult {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportH, setViewportH] = useState(FALLBACK_VIEWPORT_H);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((entries) => {
      const h = entries[0]?.contentRect.height;
      if (typeof h === 'number' && h > 0) setViewportH(h);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (el) setScrollTop(el.scrollTop);
  }, []);

  const firstVisible = Math.floor(scrollTop / rowHeight);
  const start = Math.max(0, firstVisible - overscan);
  const visibleCount = Math.ceil(viewportH / rowHeight) + overscan * 2;
  const end = Math.min(itemCount, start + visibleCount);

  return {
    scrollRef,
    start,
    end,
    totalHeight: itemCount * rowHeight,
    offsetY: start * rowHeight,
    onScroll,
  };
}
