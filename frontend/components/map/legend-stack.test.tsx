import { beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { LegendStack } from './legend-stack';
import type { LegendSpec } from '@/lib/map-kit/types';
import { useHudStore } from '@/lib/store/useHudStore';

/** 图例栈收折契约：默认只展开最新一层（贴底），其余收成一行窄条，
 * 点击 eyebrow 切换 —— 多层会话不再整列遮盖地图（2026-08-25 用户反馈）。 */

vi.mock('@/lib/utils/logger', () => ({
  devOnly: { warn: vi.fn(), info: vi.fn(), error: vi.fn() },
}));

function graduated(field: string): LegendSpec {
  return {
    type: 'graduated',
    field,
    breaks: [0, 10, 20],
    palette: 'YlOrRd',
    palette_colors: ['#ffffb2', '#fd8d3c'],
  } as unknown as LegendSpec;
}

const ENTRIES = [
  { id: 'L1', name: '成都市各区县小学数量', legendSpec: graduated('primary_school_count') },
  { id: 'L2', name: '分析结果: result-chatcmpl-tool-x', legendSpec: graduated('count') },
  { id: 'L3', name: '成都市小学空间分布', legendSpec: graduated('density') },
];

beforeEach(() => {
  cleanup();
  useHudStore.getState().clearLayers();
});

describe('LegendStack', () => {
  it('多层时默认只展开最新一层，其余收折为一行标题', () => {
    render(<LegendStack entries={ENTRIES} />);
    // 最新层(L3)的图例卡可见
    expect(screen.getAllByText('density').length).toBeGreaterThan(0);
    // 收折层不渲染图例体：字段名不出现
    expect(screen.queryByText('primary_school_count')).toBeNull();
    expect(screen.queryByText('count')).toBeNull();
    // 每层的名字条都在（收折形态仍是可点击的一行）
    for (const name of ['成都市各区县小学数量', '分析结果: result-chatcmpl-tool-x', '成都市小学空间分布']) {
      expect(screen.getByTitle(name)).toBeTruthy();
    }
  });

  it('点击收折层的标题行展开，再点收起', () => {
    render(<LegendStack entries={ENTRIES} />);
    const row = screen.getByTitle('成都市各区县小学数量').closest('button')!;
    expect(row.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(row);
    expect(screen.getAllByText('primary_school_count').length).toBeGreaterThan(0);
    expect(row.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(row);
    expect(screen.queryByText('primary_school_count')).toBeNull();
  });

  it('单层时默认展开', () => {
    render(<LegendStack entries={[ENTRIES[0]]} />);
    expect(screen.getAllByText('primary_school_count').length).toBeGreaterThan(0);
  });

  it('空数组不渲染任何条目', () => {
    const { container } = render(<LegendStack entries={[]} />);
    expect(container.querySelector('[data-testid], button, div')).toBeNull();
  });

  it('聚焦层带高亮环类名', () => {
    render(<LegendStack entries={[{ ...ENTRIES[0], flashing: true }]} />);
    const wrap = screen.getByTitle('成都市各区县小学数量').closest('div');
    expect(wrap?.className).toContain('ring-status-accent-vivid');
  });
});
