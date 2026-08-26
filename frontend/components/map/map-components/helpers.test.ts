/**
 * placement 定位原语测试（D1/D4）：floating 内联样式 / anchor 旧槽位透传 /
 * variant 解析优先级。
 */
import { describe, it, expect } from 'vitest';
import { isFloating, placementStyle, resolveVariant } from './helpers';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

function comp(partial: Partial<MapSpecComponent> & { id: string; type: MapSpecComponent['type'] }): MapSpecComponent {
  return { enabled: true, position: 'none', ...partial };
}

describe('isFloating', () => {
  it('缺省 placement（旧 spec）→ false', () => {
    expect(isFloating(comp({ id: 'a', type: 'chart_panel' }))).toBe(false);
  });

  it('anchor placement → false', () => {
    expect(isFloating(comp({ id: 'a', type: 'chart_panel', placement: { mode: 'anchor', anchor: 'top-left' } }))).toBe(false);
  });

  it('floating placement → true', () => {
    expect(isFloating(comp({ id: 'a', type: 'chart_panel', placement: { mode: 'floating', x: 12, y: 34 } }))).toBe(true);
  });
});

describe('placementStyle', () => {
  it('floating：left/top/zIndex（缺省 40）+ 可选 width/height', () => {
    const style = placementStyle(comp({
      id: 'a',
      type: 'chart_panel',
      placement: { mode: 'floating', x: 12.4, y: 34.6, width: 320, height: 240, zIndex: 60 },
    }));
    expect(style).toEqual({ left: 12, top: 35, width: 320, height: 240, zIndex: 60 });
  });

  it('floating 缺省字段：zIndex 兜底 40，无 width/height 键', () => {
    const style = placementStyle(comp({
      id: 'a',
      type: 'chart_panel',
      placement: { mode: 'floating', x: 0, y: 0 },
    }));
    expect(style).toEqual({ left: 0, top: 0, zIndex: 40 });
  });

  it('anchor / 缺省 placement → undefined（旧槽位类继续生效）', () => {
    expect(placementStyle(comp({ id: 'a', type: 'chart_panel' }))).toBeUndefined();
    expect(placementStyle(comp({ id: 'a', type: 'chart_panel', placement: { mode: 'anchor', anchor: 'top-left' } }))).toBeUndefined();
  });
});

describe('resolveVariant', () => {
  it('options.variant 优先于 component.variant，缺省回退 fallback', () => {
    expect(resolveVariant(comp({ id: 'a', type: 'legend', options: { variant: 'compact' }, variant: 'report' }), 'academic')).toBe('compact');
    expect(resolveVariant(comp({ id: 'a', type: 'legend', variant: 'report' }), 'academic')).toBe('report');
    expect(resolveVariant(comp({ id: 'a', type: 'legend' }), 'academic')).toBe('academic');
    expect(resolveVariant(comp({ id: 'a', type: 'legend', options: { variant: 42 } as unknown as Record<string, unknown> }), 'x')).toBe('x');
  });
});
