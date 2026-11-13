/**
 * T-F3：G33 categorical 镜像——类型化键（key_type）与显式 other 桶
 * （legend_spec.default）在 live 表达式投影中的行为；与后端
 * spec_to_paint 同口径。
 */
import { describe, expect, it } from 'vitest';
import { legendSpecToColorExpression } from './thematic-paint';

describe('G33 categorical key_type / default mirror', () => {
  it('numeric key_type emits typed number cases (MapLibre strict match)', () => {
    const expr = legendSpecToColorExpression({
      type: 'categorical',
      field: 'code',
      categories: [
        { key: 'a', color: '#aaaaaa' },
        { key: '1', color: '#111111', key_type: 'number' },
      ],
    }) as unknown[];
    // ["match", ["get","code"], "a", "#aaaaaa", 1, "#111111", <default>]
    expect(expr[0]).toBe('match');
    expect(expr).toContain(1);
    expect(expr).toContain('#111111');
    const idx = expr.indexOf(1);
    expect(expr[idx + 1]).toBe('#111111');
  });

  it('explicit default (other bucket) wins over last-category color', () => {
    const expr = legendSpecToColorExpression({
      type: 'categorical',
      field: 'k',
      categories: [
        { key: 'x', color: '#111111' },
        { key: 'y', color: '#222222' },
      ],
      default: { color: '#cccccc', label: 'Other' },
    }) as unknown[];
    const fallback = expr[expr.length - 1];
    expect(fallback).toBe('#cccccc'); // 不再借用末类 #222222
  });

  it('legacy specs without default keep the last-category-color contract', () => {
    const expr = legendSpecToColorExpression({
      type: 'categorical',
      field: 'k',
      categories: [
        { key: 'x', color: '#111111' },
        { key: 'y', color: '#222222' },
      ],
    }) as unknown[];
    expect(expr[expr.length - 1]).toBe('#222222');
  });
});

describe('G33 categorical key_type: bool parity with backend vocabulary', () => {
  it('emits typed boolean cases for key_type "bool" (backend emits "bool", not "boolean")', () => {
    const expr = legendSpecToColorExpression({
      type: 'categorical',
      field: 'flag',
      categories: [
        { key: 'True', color: '#ff0000', key_type: 'bool' },
        { key: 'False', color: '#0000ff', key_type: 'bool' },
      ],
    }) as unknown[];
    expect(expr[0]).toBe('match');
    expect(expr).toContain(true);
    expect(expr).toContain(false);
    const iTrue = expr.indexOf(true);
    const iFalse = expr.indexOf(false);
    expect(expr[iTrue + 1]).toBe('#ff0000');
    expect(expr[iFalse + 1]).toBe('#0000ff');
  });
});
