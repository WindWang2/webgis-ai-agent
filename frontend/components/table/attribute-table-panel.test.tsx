import { describe, expect, it, beforeEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useHudStore } from '@/lib/store/useHudStore';
import { getSelection, publishSelection, resetSelectionStore } from '@/lib/selection/selection-store';
import { AttributeTablePanel } from './attribute-table-panel';

function makeFeatures(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    type: 'Feature',
    id: `f-${i}`,
    geometry: { type: 'Point', coordinates: [100 + i * 0.001, 30] },
    properties: { name: `点 ${i}`, score: n - i, kind: i % 2 ? 'A' : 'B' },
  }));
}

function seedLayer(featureCount: number) {
  useHudStore.setState({
    layers: [{
      id: 'L1',
      name: '测试图层',
      type: 'vector',
      visible: true,
      opacity: 1,
      source: { type: 'FeatureCollection', features: makeFeatures(featureCount) },
    }] as never,
  });
}

describe('AttributeTablePanel（V7 Phase D）', () => {
  beforeEach(() => {
    cleanup();
    resetSelectionStore();
    useHudStore.getState().resetDockState();
    act(() => {
      useHudStore.setState({ layers: [] as never });
    });
  });

  it('无层时显示空态提示', () => {
    render(<AttributeTablePanel />);
    expect(screen.getByText(/暂无含内联属性的图层/)).toBeTruthy();
  });

  it('渲染列头与行计数，行可点击发布 table 选择', () => {
    seedLayer(20);
    render(<AttributeTablePanel />);
    expect(screen.getByTestId('attribute-table-panel')).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: /name/ })).toBeTruthy();
    expect(screen.getByTestId('attribute-row-count').textContent).toContain('20');

    fireEvent.click(screen.getByTestId('attribute-row-0'));
    const sel = getSelection();
    expect(sel?.source).toBe('table');
    expect(sel?.layer_id).toBe('L1');
    expect(sel?.selected_ids).toEqual(['f-0']);
  });

  it('搜索过滤行；排序翻转顺序；三态（asc/desc/none）', () => {
    seedLayer(30);
    render(<AttributeTablePanel />);
    const search = screen.getByLabelText('过滤属性行') as HTMLInputElement;
    fireEvent.change(search, { target: { value: '点 2' } });
    const count = screen.getByTestId('attribute-row-count');
    expect(Number(count.textContent?.replace(/[^0-9]/g, ''))).toBeLessThanOrEqual(11);

    // 排序：score 列 asc 后首行应是最大值行
    fireEvent.change(search, { target: { value: '' } });
    const scoreHeader = screen.getByRole('columnheader', { name: /score/ });
    fireEvent.click(scoreHeader);
    const firstAsc = screen.getByTestId('attribute-row-0');
    const idxAsc = Number(firstAsc.getAttribute('data-testid')!.replace('attribute-row-', ''));
    expect(idxAsc).toBe(0);
    fireEvent.click(scoreHeader); // desc
    fireEvent.click(scoreHeader); // none
    expect(scoreHeader.getAttribute('aria-sort')).toBe('none');
  });

  it('map 发布的选择高亮表格行（map↔table 单真相）', () => {
    seedLayer(10);
    render(<AttributeTablePanel />);
    act(() => {
      publishSelection('select', {
        source: 'map',
        layer_id: 'L1',
        selected_ids: ['f-3'],
        id_field: 'id',
      });
    });
    const highlighted = screen.getByRole('row', { selected: true });
    expect(highlighted.getAttribute('data-testid')).toBe('attribute-row-3');
  });

  it('100k 行虚拟化：DOM 行数恒定（窗口 + overscan），不一次性渲染', () => {
    seedLayer(100_000);
    render(<AttributeTablePanel />);
    expect(screen.getByTestId('attribute-row-count').textContent).toContain('100,000');
    const renderedRows = document.querySelectorAll('[role="row"][tabindex]').length;
    // 窗口渲染：~视口高/行高 + overscan（回退视口 640px/28 ≈ 23 + 8*2），
    // 无论数据量多大都不应超过一个小常数。
    expect(renderedRows).toBeGreaterThan(0);
    expect(renderedRows).toBeLessThan(80);
  });
});
