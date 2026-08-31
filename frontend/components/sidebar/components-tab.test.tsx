import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

/**
 * Components Manager UI（Workspace V2 / Goal C4）—— 实例列表来自 committed
 * MapSpec（共享解析层），动作全部走既有 patch_component CAS 通道。
 * 契约：列表/启用计数正确渲染；hide/show/collapse/reset/置顶按钮产出
 * 正确 patch 载荷；空态诚实（无会话/无组件）。
 */

const mutationMocks = vi.hoisted(() => ({
  commitComponentPatch: vi.fn(() => Promise.resolve()),
}));

vi.mock('@/lib/mapspec/component-mutation', () => ({
  ...mutationMocks,
  subscribeComponentOverrides: () => () => {},
  getComponentOverridesGeneration: () => 0,
}));

const cursorState: { spec: unknown } = { spec: null };

vi.mock('@/lib/mapspec/session-cursor', () => ({
  getCommittedMapSpec: () => cursorState.spec,
  getMapSpecLiveGeneration: () => 0,
  subscribeMapSpecLive: () => () => {},
}));

import { ComponentsTab } from './components-tab';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

function specWith(components: MapSpec['layout']['components']): MapSpec {
  return {
    version: '1.0',
    sources: {},
    layers: [],
    layout: { components },
  } as MapSpec;
}

describe('ComponentsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    cursorState.spec = null;
  });

  it('renders empty state without a session', () => {
    render(<ComponentsTab sessionId={null} />);
    expect(screen.getByText('暂无地图组件')).toBeTruthy();
  });

  it('renders empty state when the spec has no manageable components', () => {
    cursorState.spec = specWith([
      { id: 'grid', type: 'graticule', enabled: true },
    ] as MapSpec['layout']['components']);
    render(<ComponentsTab sessionId="s1" />);
    expect(screen.getByText('暂无地图组件')).toBeTruthy();
  });

  it('lists manageable instances with type labels and layer bindings', () => {
    cursorState.spec = specWith([
      { id: 'title', type: 'title', enabled: true, options: { text: '成都小学分布' } },
      { id: 'legend-main', type: 'continuous_colorbar', enabled: true, options: { layerId: 'poi-heat' } },
      { id: 'chart-panel', type: 'chart_panel', enabled: false, options: { chartRef: 'ref:chart-1' } },
    ] as MapSpec['layout']['components']);
    render(<ComponentsTab sessionId="s1" />);
    expect(screen.getByText('标题')).toBeTruthy();
    expect(screen.getByText('连续色条')).toBeTruthy();
    expect(screen.getByText(/poi-heat/)).toBeTruthy();
    // 3 个组件 · 2 启用
    expect(screen.getByText('3')).toBeTruthy();
  });

  it('hide action commits an enabled:false patch on the same component truth', async () => {
    cursorState.spec = specWith([
      { id: 'chart-panel', type: 'chart_panel', enabled: true, options: { chartRef: 'ref:chart-1' } },
    ] as MapSpec['layout']['components']);
    render(<ComponentsTab sessionId="s1" />);
    await userEvent.click(screen.getByRole('button', { name: '隐藏 图表面板' }));
    expect(mutationMocks.commitComponentPatch).toHaveBeenCalledWith(
      'chart-panel',
      { enabled: false },
    );
  });

  it('show action commits an enabled:true patch', async () => {
    cursorState.spec = specWith([
      { id: 'chart-panel', type: 'chart_panel', enabled: false, options: {} },
    ] as MapSpec['layout']['components']);
    render(<ComponentsTab sessionId="s1" />);
    await userEvent.click(screen.getByRole('button', { name: '显示 图表面板' }));
    expect(mutationMocks.commitComponentPatch).toHaveBeenCalledWith(
      'chart-panel',
      { enabled: true },
    );
  });

  it('collapse action preserves the anchored placement', async () => {
    cursorState.spec = specWith([
      { id: 'chart-panel', type: 'chart_panel', enabled: true, options: {} },
    ] as MapSpec['layout']['components']);
    render(<ComponentsTab sessionId="s1" />);
    await userEvent.click(screen.getByRole('button', { name: '折叠 图表面板' }));
    expect(mutationMocks.commitComponentPatch).toHaveBeenCalledWith(
      'chart-panel',
      { placement: { mode: 'anchor', anchor: 'top-left', collapsed: true } },
    );
  });

  it('reset position action returns a floating panel to its default slot', async () => {
    cursorState.spec = specWith([
      {
        id: 'legend-main', type: 'legend', enabled: true,
        placement: { mode: 'floating', x: 20, y: 40, width: 220, height: 160 },
        options: { layerId: 'district' },
      },
    ] as MapSpec['layout']['components']);
    render(<ComponentsTab sessionId="s1" />);
    await userEvent.click(screen.getByRole('button', { name: '重置位置 分级图例' }));
    expect(mutationMocks.commitComponentPatch).toHaveBeenCalledWith(
      'legend-main',
      { placement: { mode: 'anchor', anchor: 'bottom-left' } },
    );
  });
});
