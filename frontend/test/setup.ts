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
    // jsdom stores the underlying bytes in _buffer (a Node Buffer) on the impl
    // object; reach it via the public wrapper's symbol or FileReader fallback.
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

