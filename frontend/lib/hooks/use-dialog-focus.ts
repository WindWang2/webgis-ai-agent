/**
 * useDialogFocus — modal/drawer 焦点管理（UI V3 共用）。
 *
 * 三个职责：
 *   1. 打开时初始聚焦（默认容器内第一个可聚焦元素，可用 selector 指定）；
 *   2. 关闭时把焦点归还触发元素；
 *   3. document 级 Tab 围栏 + Escape —— 即使焦点落到非交互区域被甩到 body，
 *      下一次 Tab 也会被拉回容器内（修复容器级 onKeyDown trap 的逃逸漏洞）。
 */
import { useEffect, type RefObject } from 'react';
import { getTabbableIn, trapTabKey } from '@/lib/utils/focus';

export interface UseDialogFocusOptions {
  open: boolean;
  containerRef: RefObject<HTMLElement | null>;
  /** Escape 关闭回调；不传则 Escape 不处理 */
  onEscape?: () => void;
  /** 初始聚焦选择器（如 'input'）；缺省聚焦第一个可 Tab 元素 */
  initialFocusSelector?: string;
}

export function useDialogFocus({
  open,
  containerRef,
  onEscape,
  initialFocusSelector,
}: UseDialogFocusOptions): void {
  // 初始聚焦 + 焦点归还
  useEffect(() => {
    if (!open) return;
    const restore = document.activeElement as HTMLElement | null;
    const t = setTimeout(() => {
      const container = containerRef.current;
      if (!container) return;
      const target = initialFocusSelector
        ? container.querySelector<HTMLElement>(initialFocusSelector)
        : null;
      (target ?? getTabbableIn(container)[0] ?? container).focus();
    }, 50);
    return () => {
      clearTimeout(t);
      restore?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // document 级 Tab 围栏 + Escape
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (onEscape) {
          e.stopPropagation();
          onEscape();
        }
        return;
      }
      if (e.key === 'Tab') {
        trapTabKey(e, containerRef.current);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onEscape, containerRef]);
}

export default useDialogFocus;
