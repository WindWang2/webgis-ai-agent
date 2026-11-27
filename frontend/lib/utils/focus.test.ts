import { afterEach, describe, expect, it } from 'vitest';
import { getTabbableIn, trapTabKey } from './focus';

/**
 * jsdom 不做布局计算，test/setup.ts 把 offsetParent 统一垫成 body。这里按
 * Chromium 实测语义还原 position:fixed 元素：视口包含块下 offsetParent 恒为
 * null，但布局盒可见（getClientRects() 非空）。
 */
function mockFixedElement(el: HTMLElement): void {
  Object.defineProperty(el, 'offsetParent', {
    get: () => null,
    configurable: true,
  });
  Object.defineProperty(el, 'getClientRects', {
    value: () => [{ top: 0, left: 0, right: 10, bottom: 10, width: 10, height: 10 }] as unknown as DOMRectList,
    configurable: true,
  });
}

function mount(...nodes: HTMLElement[]): void {
  for (const node of nodes) document.body.appendChild(node);
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('getTabbableIn', () => {
  it('keeps position:fixed tabbable elements (browsers report offsetParent === null for them)', () => {
    const drawer = document.createElement('div');
    drawer.style.position = 'fixed';
    const closeBtn = document.createElement('button');
    drawer.appendChild(closeBtn);
    mount(drawer);
    mockFixedElement(closeBtn);

    expect(getTabbableIn(drawer)).toEqual([closeBtn]);
  });

  it('still filters out display:none elements', () => {
    const drawer = document.createElement('div');
    const hiddenBtn = document.createElement('button');
    hiddenBtn.style.display = 'none';
    const shownBtn = document.createElement('button');
    drawer.append(hiddenBtn, shownBtn);
    mount(drawer);

    expect(getTabbableIn(drawer)).toEqual([shownBtn]);
  });
});

describe('trapTabKey', () => {
  it('moves focus into a fixed drawer instead of swallowing Tab with no target', () => {
    const drawer = document.createElement('div');
    drawer.style.position = 'fixed';
    const btn = document.createElement('button');
    drawer.appendChild(btn);
    mount(drawer);
    mockFixedElement(btn);

    const event = new KeyboardEvent('keydown', { key: 'Tab', cancelable: true, bubbles: true });
    expect(trapTabKey(event, drawer)).toBe(true);
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(btn);
  });
});
