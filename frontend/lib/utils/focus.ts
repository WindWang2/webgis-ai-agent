/**
 * getTabbableIn — 从 history-drawer 提取的 focus-trap 工具（UI V3 共用）。
 *
 * 返回容器内所有可 Tab 到达的元素（按 DOM 顺序），供 drawer / dialog 实现
 * focus trap 与初始聚焦。只统计可见元素（offsetParent !== null）。
 */
export const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function getTabbableIn(container: HTMLElement | null): HTMLElement[] {
  if (!container) return [];
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => el.offsetParent !== null
  );
}

/**
 * trapTabKey — 在 keydown 处理器中调用；若按的是 Tab 则将焦点循环在容器内。
 * 返回是否消费了该事件（true = 已 preventDefault）。
 */
export function trapTabKey(event: KeyboardEvent, container: HTMLElement | null): boolean {
  if (event.key !== 'Tab') return false;
  const tabbable = getTabbableIn(container);
  if (tabbable.length === 0) {
    event.preventDefault();
    return true;
  }
  const first = tabbable[0];
  const last = tabbable[tabbable.length - 1];
  const active = document.activeElement as HTMLElement | null;
  if (event.shiftKey) {
    if (active === first || !container?.contains(active)) {
      event.preventDefault();
      last.focus();
      return true;
    }
  } else if (active === last || !container?.contains(active)) {
    event.preventDefault();
    first.focus();
    return true;
  }
  return false;
}
