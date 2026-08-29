import { describe, expect, it } from 'vitest';
import {
  COMPONENT_LAYOUT_META,
  keyboardMoveDelta,
  resolveSlotLayout,
} from './layout-runtime';
import { buildTopSlotIndexes, stackedTopStyle } from '@/components/map/map-components/helpers';

/** v2 Phase 9（#1079）：ComponentLayoutRuntime 契约。 */

describe('resolveSlotLayout', () => {
  it('top-left 三组件（annotation/statistics/chart）按优先级分层不互压', () => {
    const comps = [
      { type: 'chart_panel' },
      { type: 'statistics_panel' },
      { type: 'annotation' },
    ];
    const layout = resolveSlotLayout(comps);
    expect(layout.get(comps[2])?.index).toBe(0); // annotation priority 0
    expect(layout.get(comps[1])?.index).toBe(1); // statistics priority 1
    expect(layout.get(comps[0])?.index).toBe(2); // chart priority 2
    for (const c of comps) {
      expect(layout.get(c)?.slot).toBe('top-left');
      expect(layout.get(c)?.slotSize).toBe(3);
    }
  });

  it('显式 position 覆盖缺省槽位', () => {
    const comps = [{ type: 'chart_panel', position: 'top-right' }];
    const layout = resolveSlotLayout(comps);
    expect(layout.get(comps[0])?.slot).toBe('top-right');
  });

  it('scale_bar 恒贴底（bottom-right 内 priority 覆盖为最低）', () => {
    const comps = [
      { type: 'continuous_colorbar' },
      { type: 'scale_bar' },
    ];
    const layout = resolveSlotLayout(comps);
    expect(layout.get(comps[1])?.index).toBe(0);
    expect(layout.get(comps[0])?.index).toBe(1);
  });

  it('单组件槽 index=0 且 slotSize=1（消费方免偏移）', () => {
    const comps = [{ type: 'title' }];
    const layout = resolveSlotLayout(comps);
    expect(layout.get(comps[0])).toEqual({ slot: 'top-center', index: 0, slotSize: 1 });
  });

  it('未知类型回落 none 不参与堆叠', () => {
    const comps = [{ type: 'mystery_widget' }];
    const layout = resolveSlotLayout(comps);
    expect(layout.get(comps[0])).toBeUndefined();
  });
});

describe('stackedTopStyle / buildTopSlotIndexes', () => {
  it('同槽多组件产出偏移样式，单槽与 index 0 无偏移', () => {
    const chart = { type: 'chart_panel', id: 'c1' } as any;
    const stats = { type: 'statistics_panel', id: 's1' } as any;
    const solo = { type: 'north_arrow', id: 'n1' } as any;
    const indexes = buildTopSlotIndexes([chart, stats, solo]);
    // priority 小者贴边：statistics(1) idx 0，chart(2) idx 1
    expect(indexes.get(stats)).toBe(0);
    expect(indexes.get(chart)).toBe(1);
    expect(indexes.has(solo)).toBe(false); // top-right 单组件
    expect(stackedTopStyle(chart, indexes)).toEqual({ top: 'calc(0.75rem + 36px)' });
    expect(stackedTopStyle(stats, indexes)).toBeUndefined(); // idx 0 无偏移
    expect(stackedTopStyle(solo, indexes)).toBeUndefined();
  });
});

describe('keyboardMoveDelta', () => {
  it('方向键 8px，Shift/Alt 24px', () => {
    expect(keyboardMoveDelta('ArrowUp', false)).toEqual({ dx: 0, dy: -8 });
    expect(keyboardMoveDelta('ArrowRight', true)).toEqual({ dx: 24, dy: 0 });
    expect(keyboardMoveDelta('Enter', false)).toBeNull();
  });
});

describe('COMPONENT_LAYOUT_META', () => {
  it('title 独占 top-center；五个锚定槽有代表组件（bottom-center 为保留槽）', () => {
    expect(COMPONENT_LAYOUT_META.title?.exclusive).toBe(true);
    const slots = new Set(Object.values(COMPONENT_LAYOUT_META).map((m) => m.defaultSlot));
    for (const s of ['top-left', 'top-center', 'top-right', 'bottom-left', 'bottom-right']) {
      expect(slots.has(s as any)).toBe(true);
    }
  });
});
