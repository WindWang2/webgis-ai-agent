/**
 * disclosure-labels：kind 词表查找必须只认自有属性（ADR-0238 D6 —— kind
 * 内码/原型链成员不得泄漏进可见文本）。
 */
import { describe, expect, it } from 'vitest';
import { uncertaintyKindLabel } from './disclosure-labels';

describe('uncertaintyKindLabel', () => {
  it('maps known kinds and falls back to the raw key for unknown ones', () => {
    expect(uncertaintyKindLabel('interval')).toBe('区间');
    expect(uncertaintyKindLabel('variance')).toBe('方差');
    expect(uncertaintyKindLabel('custom_kind')).toBe('custom_kind');
    expect(uncertaintyKindLabel(undefined)).toBe('区间');
    expect(uncertaintyKindLabel('   ')).toBe('区间');
  });

  it('does not walk the prototype chain for constructor/toString/valueOf/__proto__', () => {
    // 旧实现 UNCERTAINTY_KIND_LABELS[key] ?? key 会取到 Object.prototype
    // 成员（函数/对象）并直接泄漏进 UI 文本。
    for (const key of ['constructor', 'toString', 'valueOf', '__proto__', 'hasOwnProperty']) {
      const label = uncertaintyKindLabel(key);
      expect(typeof label).toBe('string');
      expect(label).toBe(key);
    }
  });
});
