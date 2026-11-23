/**
 * setup.ts 全局 localStorage mock 必须是可读回的存储，而非纯 spy：
 * 浏览器里 setItem(k, v) 后 getItem(k) === v，length/key 可遍历。
 */
import { describe, expect, it } from 'vitest';

describe('global localStorage mock', () => {
  it('setItem 后可读回，removeItem/clear 同步生效', () => {
    localStorage.setItem('setup-test-k1', 'v1');
    expect(localStorage.getItem('setup-test-k1')).toBe('v1');
    expect(localStorage.length).toBe(1);
    expect(localStorage.key(0)).toBe('setup-test-k1');

    localStorage.setItem('setup-test-k2', 'v2');
    expect(localStorage.length).toBe(2);
    expect(localStorage.key(1)).toBe('setup-test-k2');

    localStorage.removeItem('setup-test-k1');
    expect(localStorage.getItem('setup-test-k1')).toBeNull();

    localStorage.clear();
    expect(localStorage.length).toBe(0);
    expect(localStorage.getItem('setup-test-k2')).toBeNull();
  });
});
