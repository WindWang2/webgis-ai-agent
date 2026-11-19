import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { CartographyResultCard } from './cartography-result-card';

const result = {
  legend_spec: {
    type: 'graduated' as const,
    field: 'pop',
    breaks: [0, 100, 500, 1000],
    palette: 'YlOrRd',
    palette_colors: ['#fff', '#aaa', '#000'],
  },
  layer_meta: { title: '成都人口分布' },
};

describe('CartographyResultCard', () => {
  it('renders title and field info', () => {
    render(<CartographyResultCard result={result} layerId="layer-1" />);
    expect(screen.getByText('成都人口分布')).toBeInTheDocument();
    expect(screen.getByText(/pop/)).toBeInTheDocument();
  });

  it('renders palette swatches', () => {
    const { container } = render(<CartographyResultCard result={result} layerId="layer-1" />);
    const swatches = container.querySelectorAll('[data-testid="card-swatch"]');
    expect(swatches.length).toBe(3);
  });

  it('clicking 高亮 button calls onFocus with layerId', () => {
    const onFocus = vi.fn();
    render(<CartographyResultCard result={result} layerId="layer-1" onFocus={onFocus} />);
    fireEvent.click(screen.getByText(/高亮此图层/));
    expect(onFocus).toHaveBeenCalledWith('layer-1');
  });

  it('returns null when no legend_spec', () => {
    const { container } = render(<CartographyResultCard result={{}} layerId="x" />);
    expect(container.firstChild).toBeNull();
  });

  it('shows bounded cartographic quality failures without requiring a legend', () => {
    render(
      <CartographyResultCard
        result={{
          cartographic_review: {
            status: 'failed_unrepairable',
            repair_count: 0,
            checks: [
              { rule: 'THEMATIC_LEGEND', status: 'fail', message: '专题样式缺少图例' },
            ],
          },
        }}
        layerId="layer-1"
      />,
    );

    expect(screen.getByText('地图需要处理')).toBeInTheDocument();
    expect(screen.getByText('专题样式缺少图例')).toBeInTheDocument();
  });

  it('reports automatic repairs on a passing review', () => {
    render(
      <CartographyResultCard
        result={{
          cartographic_review: {
            stage: 'desired_state',
            status: 'passed',
            repair_count: 1,
            checks: [],
          },
        }}
        layerId="layer-1"
      />,
    );

    expect(screen.getByText(/已自动修复 1 项/)).toBeInTheDocument();
    expect(screen.getByText(/等待运行时验证/)).toBeInTheDocument();
  });

  it('#2184：reliability_dual_channel 图例渲染 summary 与估计值通道色板，不崩溃', () => {
    const { container } = render(
      <CartographyResultCard
        result={{
          legend_spec: {
            type: 'reliability_dual_channel' as const,
            title: '估计值 · 不确定度',
            estimate: { label: '估计值', palette_colors: ['#eee', '#999', '#111'] },
            measure: { kind: 'cv' },
          },
        }}
        layerId="layer-g41"
      />,
    );
    expect(screen.getByText('估计值 · 不确定度')).toBeInTheDocument();
    const swatches = container.querySelectorAll('[data-testid="card-swatch"]');
    expect(swatches.length).toBe(3);
    expect((swatches[0] as HTMLElement).style.backgroundColor).toBeTruthy();
  });

  it('#2184：reliability_dual_channel 无 title 时回退 estimate.label，缺色板时不渲染色块', () => {
    const { container } = render(
      <CartographyResultCard
        result={{
          legend_spec: {
            type: 'reliability_dual_channel' as const,
            estimate: { label: '人口估计' },
          },
        }}
        layerId="layer-g41"
      />,
    );
    expect(screen.getByText('人口估计')).toBeInTheDocument();
    expect(container.querySelectorAll('[data-testid="card-swatch"]')).toHaveLength(0);
  });

  it('does not present partial deterministic evidence as a quality pass', () => {
    render(
      <CartographyResultCard
        result={{
          cartographic_review: {
            stage: 'desired_state',
            status: 'partial',
            checks: [{ rule: 'CRS_EVIDENCE', status: 'not_evaluated' }],
          },
        }}
        layerId="result"
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('制图质量：证据不完整');
    expect(screen.queryByText(/制图质量：通过/)).not.toBeInTheDocument();
  });
});
