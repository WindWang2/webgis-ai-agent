import { useEffect, type RefObject } from 'react';

/**
 * Keeps an always-mounted-but-hidden panel out of the accessibility tree AND out
 * of the tab order.
 *
 * `aria-hidden` alone is not enough — and is in fact an ARIA violation — when the
 * hidden container still holds focusable elements: a keyboard user tabs into
 * controls they cannot see, and a screen reader announces nothing when focus
 * lands there. Several panels in this app stay mounted so they can animate in
 * and out, so they hit exactly that case.
 *
 * `inert` is the platform answer (it removes the subtree from hit-testing, focus
 * and the a11y tree in one go) but React 18 has no `inert` prop, so it is set
 * imperatively here. Browsers without `inert` fall back to the `aria-hidden` the
 * caller already sets, which is the pre-existing behaviour rather than a
 * regression.
 */
export function useInertWhenClosed(ref: RefObject<HTMLElement | null>, open: boolean): void {
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (open) {
      node.removeAttribute('inert');
    } else {
      node.setAttribute('inert', '');
    }
  }, [ref, open]);
}
