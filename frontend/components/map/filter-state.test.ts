import { describe, it, expect } from 'vitest';
import { resolveFilterState } from './map-panel';
import type { Layer } from '@/lib/types/layer';

const gradLayer = (id: string, breaks: number[]): Layer => ({
  id, name: id, type: 'vector', visible: true, opacity: 1,
  legend_spec: { type: 'graduated', field: 'v', breaks, palette: 'YlOrRd', palette_colors: ['#fff', '#000'] } as Layer['legend_spec'],
});

describe('resolveFilterState — #689 图例-过滤对账纯函数', () => {
  const layers5 = [gradLayer('L1', [0, 10, 20, 30, 40, 50])];

  it('全隐藏（空 ranges）保留空过滤键——地图渲染"无"，不是"全部"', () => {
    const next = resolveFilterState({}, 'L1', [], layers5);
    expect(next.L1).toEqual([]);
  });

  it('恰好全可见（ranges 覆盖所有类）移除过滤键', () => {
    const prev = { L1: [[0, 10]] as number[][] };
    const next = resolveFilterState(prev, 'L1', [[0, 10], [10, 20], [20, 30], [30, 40], [40, 50]], layers5);
    expect(next.L1).toBeUndefined();
  });

  it('部分可见保留 ranges', () => {
    const next = resolveFilterState({}, 'L1', [[10, 20], [30, 40]], layers5);
    expect(next.L1).toEqual([[10, 20], [30, 40]]);
  });

  it('无 graduated spec 的层：非全可见载荷一律保留（防御路径）', () => {
    const plain = [{ id: 'L2', name: 'L2', type: 'vector', visible: true, opacity: 1 } as Layer];
    expect(resolveFilterState({}, 'L2', [[1, 2]], plain).L2).toEqual([[1, 2]]);
  });
});
