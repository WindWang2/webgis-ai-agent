/**
 * LineageGraphView 渲染测试（ADR-0143 P3/P8）。
 * G1 ≥50 节点血缘图渲染：DOM 节点数完备、渲染完成预算（jsdom 宽松阈值）。
 * G2 a11y：role=img 概要 + 节点 title。
 * G3 节点点击回调。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LineageGraphView } from '@/components/sidebar/project/lineage-graph';
import { makeLineageGraph } from './fixtures';

describe('LineageGraphView', () => {
  it('G1: 60 节点血缘图在预算内完整渲染', async () => {
    const graph = makeLineageGraph(60);
    const t0 = performance.now();
    render(<LineageGraphView graph={graph} />);
    const svg = screen.getByRole('img', { name: /art-1 的血缘图/ });
    const elapsed = performance.now() - t0;
    expect(svg).toBeInTheDocument();
    expect(screen.getByText(/上游 30 条边 · 下游 30 条边/)).toBeInTheDocument();
    // jsdom 无布局引擎，预算放宽：60 节点 SVG 应远低于 2s
    expect(elapsed).toBeLessThan(2_000);
    // 节点完备：60 边表条目 → ≤60 唯一节点（root + parents + consumers 去重）
    const groups = svg.querySelectorAll('g');
    expect(groups.length).toBeGreaterThanOrEqual(50);
    expect(groups.length).toBeLessThanOrEqual(61);
  });

  it('G2: 节点带 title 工具提示（工具/源数据集）', () => {
    const graph = makeLineageGraph(6);
    const { container } = render(<LineageGraphView graph={graph} />);
    // SVG <title> 子元素不进 getByTitle 索引 —— 直接断言 title 文本
    const titles = Array.from(container.querySelectorAll('title')).map((t) => t.textContent ?? '');
    expect(titles.some((t) => t.includes('上游产物 art-up-1'))).toBe(true);
    expect(titles.some((t) => t.includes('源数据集 ds-1'))).toBe(true);
  });

  it('G3: onNodeClick 透传 artifactId', () => {
    const onNodeClick = vi.fn();
    const graph = makeLineageGraph(6);
    const { container } = render(<LineageGraphView graph={graph} onNodeClick={onNodeClick} />);
    const groups = Array.from(container.querySelectorAll('g[title], g'));
    const down = groups.find((g) => g.querySelector('title')?.textContent?.includes('art-down-1'));
    expect(down).toBeTruthy();
    fireEvent.click(down as Element);
    expect(onNodeClick).toHaveBeenCalledWith('art-down-1');
  });
});
