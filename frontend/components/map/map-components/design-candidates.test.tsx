/**
 * /goal carto-design M7：design_candidates 渲染器回归。
 * 注册在场 + 防御式解析（候选/适宜性徽标/typed blocked/空态）+
 * 只读披露语义（无内置接受动作——统一 action 走 webgis_map_design_select）。
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { allRegisteredTypes, getComponentRenderer } from './registry';
import { renderComponent } from './index';

const DIGEST = {
  count: 3,
  selected: 'visual_heatmap@density_surface',
  candidates: [
    { m: 'visual_heatmap', f: 'density_surface', s: 'suitable' },
    { m: 'point_cluster', f: 'aggregation', s: 'suitable' },
    {
      m: 'graduated_point',
      f: 'point',
      s: 'conditional',
      why: '点数 300 ≥ 250——过密点图线索互蚀，建议聚合/网格替代',
    },
  ],
  blocked: [],
  honored: [],
};

describe('design_candidates renderer', () => {
  beforeEach(() => {
    cleanup();
  });

  it('registers the design_candidates type', () => {
    expect(allRegisteredTypes()).toContain('design_candidates');
    expect(getComponentRenderer('design_candidates')).toBeDefined();
  });

  it('renders candidates with suitability badges and conditional reason', () => {
    const el = renderComponent({
      id: 'design-candidates',
      type: 'design_candidates',
      enabled: true,
      position: 'top-left',
      priority: 49,
      options: { design: DIGEST },
    } as never, {} as never);
    const { container } = render(<>{el}</>);
    const rows = screen.getAllByTestId('design-candidate-row');
    expect(rows).toHaveLength(3);
    expect(container.textContent).toContain('visual_heatmap');
    expect(container.textContent).toContain('point_cluster');
    // 条件候选必须带理由（为何弃/为何改——可解释面）
    expect(container.textContent).toContain('过密点图');
    // selected 标注（digest.selected 前缀命中模型名）
    expect(container.textContent).toContain('当前方案');
  });

  it('renders typed blocked candidate with blocked badge', () => {
    const el = renderComponent({
      id: 'design-candidates',
      type: 'design_candidates',
      enabled: true,
      position: 'top-left',
      priority: 49,
      options: {
        design: {
          count: 1,
          candidates: [
            {
              m: 'visual_heatmap',
              f: 'density_surface',
              s: 'blocked',
              why: '点数 3 < HEATMAP_MIN_POINTS=10',
            },
          ],
          blocked: ['visual_heatmap'],
        },
      },
    } as never, {} as never);
    const { container } = render(<>{el}</>);
    expect(container.textContent).toContain('HEATMAP_MIN_POINTS');
    expect(screen.getAllByTestId('design-candidate-row')).toHaveLength(1);
  });

  it('renders empty state on bad/absent payload (defensive)', () => {
    for (const options of [{}, { design: null }, { design: { candidates: [] } }]) {
      const el = renderComponent({
        id: 'design-candidates',
        type: 'design_candidates',
        enabled: true,
        position: 'none',
        priority: 49,
        options,
      } as never, {} as never);
      const { container } = render(<>{el}</>);
      expect(container.textContent).toContain('暂无设计方案候选');
      cleanup();
    }
  });
});
