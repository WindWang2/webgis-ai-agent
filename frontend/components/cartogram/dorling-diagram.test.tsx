/**
 * G47 图面组件：真实 SVG 圆 + 统一缩放 + 键盘选择 + 空状态。
 *
 * 断言落在**被绘制的属性**上（r / data-feature-id / preserveAspectRatio），
 * 而不是"容器里出现了 svg"。
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { DorlingDiagram } from './dorling-diagram';
import type { CartogramLayout } from '@/lib/cartogram/contracts';

function layout(over: Partial<CartogramLayout> = {}): CartogramLayout {
  return {
    kind: 'cartogram_layout',
    schema_version: 'cartogram_layout.v1',
    coordinate_space: 'diagram',
    units: 'diagram_unit',
    is_geographic: false,
    extent: { width: 1000, height: 1000 },
    source: {
      ref_id: 'ref:src', content_revision: 2, content_fingerprint: 'g47f-1',
    },
    plan_digest: 'g47p-1',
    candidate_digest: 'g47c-1',
    status: 'converged',
    publication_ready: true,
    circles: [
      { feature_id: 'b', label: 'B', x: 300, y: 300, radius: 20, value: 1 },
      { feature_id: 'a', label: 'A', x: 700, y: 700, radius: 40, value: 4 },
    ],
    zero_ids: ['z1'],
    missing_ids: [],
    legend: { quantity: 'pop', unit: '人', title: '人口' },
    reference: {
      kind: 'original_outlines',
      coordinate_space: 'diagram',
      outlines: [{ feature_id: 'a', rings: [[[0, 0], [10, 0], [10, 10], [0, 0]]] }],
    },
    metrics: { status: 'pass', failed: [] },
    disclosures: ['示意坐标，非经纬度'],
    layout_ref: 'ref:g47layout-1',
    ...over,
  };
}

describe('DorlingDiagram', () => {
  it('按布局半径绘制真实圆（1:4 数量 → 1:2 半径）', () => {
    render(<DorlingDiagram layout={layout()} selectedId={null} onSelect={() => {}} />);
    const small = document.querySelector('[data-feature-id="b"]') as SVGCircleElement;
    const big = document.querySelector('[data-feature-id="a"]') as SVGCircleElement;
    expect(small).not.toBeNull();
    expect(Number(big.getAttribute('r'))).toBeCloseTo(
      2 * Number(small.getAttribute('r')), 6,
    );
  });

  it('svg 用 contain 比例，不允许非等比拉伸', () => {
    render(<DorlingDiagram layout={layout()} selectedId={null} onSelect={() => {}} />);
    const svg = document.querySelector('svg');
    expect(svg?.getAttribute('preserveAspectRatio')).toBe('xMidYMid meet');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 1000 1000');
  });

  it('点击圆按稳定身份回调', () => {
    const onSelect = vi.fn();
    render(<DorlingDiagram layout={layout()} selectedId={null} onSelect={onSelect} />);
    const circle = document.querySelector('[data-feature-id="a"]') as SVGCircleElement;
    fireEvent.click(circle);
    expect(onSelect).toHaveBeenCalled();
  });

  it('方向键按稳定 id 顺序切换（a → b）', () => {
    const onSelect = vi.fn();
    render(<DorlingDiagram layout={layout()} selectedId="a" onSelect={onSelect} />);
    const app = screen.getByRole('application');
    fireEvent.keyDown(app, { key: 'ArrowRight' });
    expect(onSelect).toHaveBeenCalledWith('b');
  });

  it('选中态显示示意坐标并明确非经纬度', () => {
    render(<DorlingDiagram layout={layout()} selectedId="a" onSelect={() => {}} />);
    const box = document.querySelector('[data-g47-selection="a"]');
    expect(box?.textContent).toContain('非经纬度');
    expect(box?.textContent).toContain('人');
  });

  it('空布局给出明确空状态，而不是空白 svg', () => {
    render(<DorlingDiagram
      layout={layout({ circles: [], publication_ready: false })}
      selectedId={null} onSelect={() => {}} />);
    expect(document.querySelector('[data-g47-diagram="empty"]')).not.toBeNull();
  });
});
