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

const openDialogs: HTMLElement[] = [];

export function useDialogFocus({
  open,
  containerRef,
  onEscape,
  initialFocusSelector,
}: UseDialogFocusOptions): void {
  // 初始聚焦 + 焦点归还
  useEffect(() => {
    if (!open) return;
    const dialog = containerRef.current;
    if (dialog) openDialogs.push(dialog);
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
      if (dialog) { const index = openDialogs.indexOf(dialog); if (index >= 0) openDialogs.splice(index, 1); }
      restore?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // document 级 Tab 围栏 + Escape
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (openDialogs.at(-1) !== containerRef.current) return;
      if (e.key === 'Escape' && !e.defaultPrevented) {
        if (onEscape) {
          e.stopImmediatePropagation();
          onEscape();
        }
        return;
      }
      if (e.key === 'Tab') {
        trapTabKey(e, containerRef.current);
      }
    };
    // Bubble Escape after the focused control has had a chance to consume it.
    // Capture Tab independently so focus cannot escape through a child handler.
    const onCaptureTab = (e: KeyboardEvent) => { if (e.key === 'Tab') onKey(e); };
    const onBubbleEscape = (e: KeyboardEvent) => { if (e.key === 'Escape') onKey(e); };
    document.addEventListener('keydown', onCaptureTab, true);
    document.addEventListener('keydown', onBubbleEscape);
    return () => {
      document.removeEventListener('keydown', onCaptureTab, true);
      document.removeEventListener('keydown', onBubbleEscape);
    };
  }, [open, onEscape, containerRef]);
}

export default useDialogFocus;
