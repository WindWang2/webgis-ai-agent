import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

/**
 * 停靠语义的宿主分离（Workspace V2 / Goal C5）：
 * - FloatingChrome 停靠态：静态流式渲染（无 absolute 定位），“取消停靠”
 *   只写 dock slice —— 绝不产生语义组件 patch（placement/enabled）；
 * - MapSpecChrome 跳过停靠实例（同一实例不双渲染）。
 */

const mutationMocks = vi.hoisted(() => ({
  commitComponentPatch: vi.fn(() => Promise.resolve()),
}));

vi.mock('@/lib/mapspec/component-mutation', () => ({
  ...mutationMocks,
  getComponentPlacementOverride: () => undefined,
  subscribeComponentOverrides: () => () => {},
  getComponentOverridesGeneration: () => 0,
  setComponentPlacementOverride: vi.fn(),
}));

import { useHudStore } from '@/lib/store/useHudStore';
import { renderComponent } from '@/components/map/map-components';
import { MapSpecChrome } from '@/components/map/map-spec-chrome';

const CTX = { spec: null, zoom: 10, centerLat: 30, bearing: 0 } as const;

function chartComponent(id = 'chart-panel'): MapSpecComponent {
  return {
    id,
    type: 'chart_panel',
    enabled: true,
    options: { chart: { title: '各区学校数量', type: 'bar', data: [{ name: '武侯区', value: 12 }] } },
  } as unknown as MapSpecComponent;
}

describe('FloatingChrome docked mode', () => {
  beforeEach(() => {
    useHudStore.getState().resetDockState();
    mutationMocks.commitComponentPatch.mockClear();
  });

  it('floating (undocked) panel keeps absolute positioning semantics', () => {
    const { container } = render(<>{renderComponent(chartComponent(), CTX)}</>);
    const panel = screen.getByTestId('spec-chrome-chart-panel');
    expect(panel.className).toContain('absolute');
    expect(container).toBeTruthy();
  });

  it('docked panel renders static flow without absolute positioning', () => {
    useHudStore.getState().dockPanel('chart-panel', 'right');
    render(<>{renderComponent(chartComponent(), CTX)}</>);
    const panel = screen.getByTestId('spec-chrome-chart-panel');
    expect(panel.getAttribute('data-docked')).toBe('right');
    expect(panel.className).not.toContain('absolute');
    expect(panel.className).toContain('relative');
  });

  it('undock button only writes dock state — no semantic component patch', async () => {
    useHudStore.getState().dockPanel('chart-panel', 'right');
    render(<>{renderComponent(chartComponent(), CTX)}</>);
    await userEvent.click(screen.getByRole('button', { name: '取消停靠' }));
    expect(useHudStore.getState().dockPlacements['chart-panel']).toBeUndefined();
    expect(mutationMocks.commitComponentPatch).not.toHaveBeenCalled();
  });

  it('collapse in dock still goes through the semantic patch channel', async () => {
    useHudStore.getState().dockPanel('chart-panel', 'right');
    render(<>{renderComponent(chartComponent(), CTX)}</>);
    await userEvent.click(screen.getByRole('button', { name: '折叠面板' }));
    expect(mutationMocks.commitComponentPatch).toHaveBeenCalledWith(
      'chart-panel',
      { placement: { mode: 'anchor', anchor: 'top-left', collapsed: true } },
    );
  });
});

describe('MapSpecChrome skips docked instances', () => {
  beforeEach(() => {
    useHudStore.getState().resetDockState();
  });

  it('docked chart panel is not rendered in the chrome surface', () => {
    useHudStore.getState().dockPanel('chart-panel', 'right');
    render(
      <MapSpecChrome
        components={[chartComponent()]}
        zoom={10}
        centerLat={30}
        bearing={0}
        spec={null}
      />,
    );
    expect(screen.queryByTestId('spec-chrome-chart-panel')).toBeNull();
  });

  it('undocked chart panel renders in the chrome surface', () => {
    render(
      <MapSpecChrome
        components={[chartComponent()]}
        zoom={10}
        centerLat={30}
        bearing={0}
        spec={null}
      />,
    );
    expect(screen.getByTestId('spec-chrome-chart-panel')).toBeTruthy();
  });
});
