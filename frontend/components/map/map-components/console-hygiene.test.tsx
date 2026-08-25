import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderComponent } from './index';
import { registerComponentRenderer } from './registry';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

/**
 * #1008 — map-components 的裸 console 清理为 devOnly 门禁（生产不再泄漏
 * 内部组件类型 / 错误细节）。vitest 环境 NODE_ENV=test（非 development），
 * devOnly 静默——任何 console.warn/error 都视为回归。
 */
describe('renderComponent — devOnly console hygiene (#1008)', () => {
  beforeEach(() => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('unknown component type degrades silently (null, no console.warn)', () => {
    const unknown = { id: 'x', type: 'definitely_not_a_renderer' } as unknown as MapSpecComponent;
    const node = renderComponent(unknown, { spec: null, zoom: 10, centerLat: 30, bearing: 0 });
    expect(node).toBeNull();
    expect(console.warn).not.toHaveBeenCalled();
  });

  it('renderer throw degrades silently (null, no console.error)', () => {
    // 注册一个必抛错的渲染器，直接覆盖 catch 分支
    registerComponentRenderer('boom_test_type', () => {
      throw new Error('renderer exploded');
    });
    const boom = { id: 'x', type: 'boom_test_type' } as unknown as MapSpecComponent;
    const node = renderComponent(boom, { spec: null, zoom: 10, centerLat: 30, bearing: 0 });
    expect(node).toBeNull();
    expect(console.error).not.toHaveBeenCalled();
  });
});
