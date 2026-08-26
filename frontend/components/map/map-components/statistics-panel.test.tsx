/**
 * statistics_panel 渲染器测试（D2）：行渲染 / emphasis 强调 / compact
 * variant / 坏载荷空态降级。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';

import { renderComponent } from './index';
import { getComponentRenderer } from './registry';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

function statsComp(options: Record<string, unknown>): MapSpecComponent {
  return { id: 'stats-1', type: 'statistics_panel', enabled: true, options };
}

function renderPanel(options: Record<string, unknown>) {
  return render(
    <>
      {renderComponent(statsComp(options), { spec: null, zoom: 10, centerLat: 30, bearing: 0 })}
    </>,
  );
}

const STATS = {
  title: '成都小学统计',
  items: [
    { label: '学校总数', value: 128, unit: '所' },
    { label: '覆盖行政区', value: 20 },
    { label: '密度最高', value: '锦江区', emphasis: true },
  ],
};

describe('statistics_panel renderer', () => {
  it('statistics_panel 在 registry 注册（side-effect import）', () => {
    expect(getComponentRenderer('statistics_panel')).toBeDefined();
  });

  it('渲染统计行：label / value / unit / 标题', () => {
    renderPanel({ stats: STATS });
    expect(screen.getByText('成都小学统计')).toBeTruthy();
    expect(screen.getByText('学校总数')).toBeTruthy();
    expect(screen.getByText('128')).toBeTruthy();
    expect(screen.getByText('所')).toBeTruthy();
    expect(screen.getByText('覆盖行政区')).toBeTruthy();
    expect(screen.getByText('20')).toBeTruthy();
  });

  it('emphasis 行高亮（data-emphasis + 强调样式类）', () => {
    renderPanel({ stats: STATS });
    const row = screen.getByText('锦江区').closest('div[data-emphasis]');
    expect(row).toBeTruthy();
    expect(row?.getAttribute('data-emphasis')).toBe('true');
    expect(row?.className).toContain('font-semibold');
    // 非 emphasis 行不带标记
    expect(screen.getByText('学校总数').closest('div[data-emphasis]')).toBeNull();
  });

  it('compact variant：data-variant=compact', () => {
    renderPanel({ stats: STATS, variant: 'compact' });
    const panel = screen.getByTestId('spec-chrome-statistics-panel');
    expect(panel.getAttribute('data-variant')).toBe('compact');
  });

  it('坏 stats 载荷 → 空态卡片（不崩）', () => {
    renderPanel({ stats: { items: '不是数组' } });
    expect(screen.getByText('暂无统计数据')).toBeTruthy();
  });

  it('缺省 stats → 空态占位', () => {
    renderPanel({});
    expect(screen.getByText('暂无统计数据')).toBeTruthy();
  });
});
