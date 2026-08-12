import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest';

const localStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
};

Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
});

// jsdom's Blob polyfill (jsdom 26) does not implement the standard
// arrayBuffer() / text() methods, which makes it impossible to read back the
// bytes of a Blob produced by libraries like jsPDF in tests. Polyfill them so
// E2E export tests can assert PDF magic bytes (#271). These are no-ops in a
// real browser where Blob.prototype.arrayBuffer already exists.
if (typeof Blob !== 'undefined' && typeof Blob.prototype.arrayBuffer !== 'function') {
  Blob.prototype.arrayBuffer = async function (): Promise<ArrayBuffer> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = reader.result as ArrayBuffer;
        resolve(result);
      };
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(this);
    });
  };
}

if (typeof HTMLCanvasElement !== 'undefined') {
  HTMLCanvasElement.prototype.getContext = function () {
    return {
      drawImage: () => {},
      fillText: () => {},
      fillRect: () => {},
      createLinearGradient: () => ({ addColorStop: () => {} }),
      beginPath: () => {},
      moveTo: () => {},
      lineTo: () => {},
      closePath: () => {},
      fill: () => {},
      stroke: () => {},
      strokeRect: () => {},
      arc: () => {},
      arcTo: () => {},
      save: () => {},
      restore: () => {},
      translate: () => {},
      rotate: () => {},
      setLineDash: () => {},
      measureText: () => ({ width: 50 }),
      fillStyle: '',
      strokeStyle: '',
      lineWidth: 1,
      font: '',
      textAlign: 'left',
      shadowColor: '',
      shadowBlur: 0,
    } as any;
  };
  HTMLCanvasElement.prototype.toDataURL = function () {
    return 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAC0lEQVR4XmNgQAcAABIAAQu7JJwAAAAASUVORK5CYII=';
  };
}

// jsdom 不做布局计算，offsetParent 恒为 null，而共用焦点工具
// （lib/utils/focus.ts）以 `offsetParent !== null` 过滤可见元素 ——
// 不垫一层的话 useDialogFocus 的 Tab 围栏在测试里永远找不到可聚焦元素。
// 按 display:none / hidden 之外的元素返回 body，语义与浏览器一致。
Object.defineProperty(HTMLElement.prototype, 'offsetParent', {
  configurable: true,
  get(this: HTMLElement) {
    return this.style.display === 'none' || this.hidden ? null : document.body;
  },
});

