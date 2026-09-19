import { describe, expect, it, beforeEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useHudStore } from '@/lib/store/useHudStore';
import { resetSelectionStore } from '@/lib/selection/selection-store';
import { AttributeTablePanel } from './attribute-table-panel';

function makeFeatures(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    type: 'Feature',
    id: `f-${i}`,
    geometry: { type: 'Point', coordinates: [100 + i * 0.001, 30] },
    properties: { name: `点 ${i}`, score: n - i },
  }));
}

/**
 * FE-05 回归：虚拟窗口的 scrollRef/onScroll 未接到 role=grid 滚动容器上，
 * 滚动事件不会更新 scrollTop 状态 —— 超过 ~39 行后滚下去是空白。
 */
describe('AttributeTablePanel 虚拟滚动接线（FE-05）', () => {
  beforeEach(() => {
    cleanup();
    resetSelectionStore();
    useHudStore.getState().resetDockState();
    act(() => {
      useHudStore.setState({ layers: [] as never });
    });
  });

  it('滚动 role=grid 容器后挂载更靠后的行窗口', () => {
    useHudStore.setState({
      layers: [{
        id: 'L1',
        name: '测试图层',
        type: 'vector',
        visible: true,
        opacity: 1,
        source: { type: 'FeatureCollection', features: makeFeatures(1000) },
      }] as never,
    });
    render(<AttributeTablePanel />);

    // 初始窗口只覆盖前 ~39 行
    expect(screen.queryByTestId('attribute-row-300')).toBeNull();

    const grid = screen.getByRole('grid');
    grid.scrollTop = 300 * 28;
    fireEvent.scroll(grid);

    expect(screen.getByTestId('attribute-row-300')).toBeTruthy();
    expect(screen.queryByTestId('attribute-row-0')).toBeNull();
  });
});
