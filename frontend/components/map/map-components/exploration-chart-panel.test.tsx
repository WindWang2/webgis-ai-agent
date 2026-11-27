/** G46 联合探索图表：客户端截断（BAR_MAX/BIN_MAX）必须如实披露剩余条数。 */
import { afterEach, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { renderComponent } from './index';
import { setPreviewData } from '@/lib/linked-exploration/preview-store';

function seedPreview(kind: 'category' | 'range', rows: number, bins: number) {
  const statistics = {
    kind,
    field: 'f',
    aggregation: 'count',
    matched: Math.max(rows, bins),
    scanned: Math.max(rows, bins),
    ...(kind === 'category'
      ? {
          rows: Array.from({ length: rows }, (_, i) => ({
            label: `c${i}`,
            count: 1,
            machine_value: `c${i}`,
            is_null: false,
            is_other: false,
          })),
        }
      : {
          bins: Array.from({ length: bins }, (_, i) => ({
            index: i,
            gte: i,
            lte: i + 1,
            upper_exclusive: false,
            label: `${i}–${i + 1}`,
            count: 1,
          })),
        }),
  };
  setPreviewData({
    charts: [{
      chart_id: 'c1',
      kind,
      field: 'f',
      aggregation: 'count',
      title: 'f',
      facet: { scope: '', note: '', statistics },
      full: { scope: '', note: '', statistics },
    }],
  } as never);
}

function renderPanel(kind: 'category' | 'range') {
  // renderComponent 以普通函数调用 renderer（hook 归属调用方组件），因此
  // 必须在真正的 React 组件 render 内调用，不能先求值再 render。
  function Harness() {
    return (
      <>
        {renderComponent(
          {
            id: 'g46-chart',
            type: 'exploration_chart_panel',
            enabled: true,
            options: { chartId: 'c1', kind, field: 'f' },
          } as never,
          {} as never,
        )}
      </>
    );
  }
  return render(<Harness />);
}

afterEach(() => {
  setPreviewData(null);
});

describe('ExplorationChartPanel client truncation disclosure', () => {
  it('分类条超过 BAR_MAX=18 时披露 …+N', () => {
    seedPreview('category', 20, 0);
    renderPanel('category');
    expect(screen.getByTestId('g46-chart-c1-client-truncated').textContent).toContain('…+2');
  });

  it('直方图分箱超过 BIN_MAX=32 时披露 …+N', () => {
    seedPreview('range', 0, 35);
    renderPanel('range');
    expect(screen.getByTestId('g46-chart-c1-client-truncated').textContent).toContain('…+3');
  });
});
