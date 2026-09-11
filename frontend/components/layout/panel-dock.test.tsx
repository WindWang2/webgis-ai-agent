/**
 * PanelDockHost（V7 布局系统）—— 双区共存 / 折叠保留成员 / 尺寸渲染。
 *
 * 契约（审计 §2-C/§2-M 的组件面）：
 * - 右/下两个停靠区独立渲染，可同时显示（此前 IIFE 短路使两区互斥）；
 * - 折叠（toggleDock）隐藏该区但不销毁归属 —— 重新展开即恢复；
 * - 区尺寸读自 store（持久化白名单），共存时右区抬升底部区高度。
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import { useEffect } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { commitMapSpecDocument, setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { PanelDockHost } from './panel-dock';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

vi.mock('@/components/map/map-components', () => ({
  renderComponent: (component: { id: string; type: string }) => (
    <div data-testid={`docked-component-${component.id}`}>{component.id}</div>
  ),
}));

function specWithPanels(): MapSpec {
  return {
    version: '1.0',
    layers: [],
    layout: {
      components: [
        { id: 'chart-a', type: 'chart_panel', title: '图 A', enabled: true },
        { id: 'stat-b', type: 'statistics_panel', title: '统 B', enabled: true },
      ],
    },
  } as unknown as MapSpec;
}

/** 测试内直读 store 的探针（避免测试组件自身订阅漂移）。 */
function Probe({ onChange }: { onChange: () => void }) {
  useEffect(() => {
    const unsub = useHudStore.subscribe(onChange);
    return unsub;
  }, [onChange]);
  return null;
}

describe('PanelDockHost（V7 布局系统）', () => {
  beforeEach(() => {
    cleanup();
    useHudStore.getState().resetDockState();
    setMapSpecSessionCursor('sess-dock-test', 1);
    commitMapSpecDocument(specWithPanels(), 1);
  });

  it('右/下两个停靠区可同时渲染（互斥回归修复）', () => {
    useHudStore.getState().dockPanel('chart-a', 'right');
    useHudStore.getState().dockPanel('stat-b', 'bottom');
    render(<PanelDockHost />);
    expect(screen.getByTestId('docked-component-chart-a')).toBeTruthy();
    expect(screen.getByTestId('docked-component-stat-b')).toBeTruthy();
    expect(document.querySelector('[data-dock-region="right"]')).toBeTruthy();
    expect(document.querySelector('[data-dock-region="bottom"]')).toBeTruthy();
  });

  it('折叠底部区：区消失、归属保留；重新展开即恢复', () => {
    useHudStore.getState().dockPanel('chart-a', 'right');
    useHudStore.getState().dockPanel('stat-b', 'bottom');
    render(<PanelDockHost />);
    expect(screen.getByTestId('docked-component-stat-b')).toBeTruthy();

    act(() => {
      useHudStore.getState().toggleDock('bottom');
    });
    expect(screen.queryByTestId('docked-component-stat-b')).toBeNull();
    const s = useHudStore.getState();
    expect(s.dockPlacements['stat-b']).toBe('bottom');
    expect(s.bottomDock.panels).toEqual(['stat-b']);

    act(() => {
      useHudStore.getState().toggleDock('bottom');
    });
    expect(screen.getByTestId('docked-component-stat-b')).toBeTruthy();
  });

  it('区尺寸来自 store（拖拽提交后的持久化值直接驱动渲染）', () => {
    useHudStore.getState().dockPanel('stat-b', 'bottom');
    useHudStore.getState().setBottomDockHeight(420);
    render(<PanelDockHost />);
    const region = document.querySelector('[data-dock-region="bottom"]') as HTMLElement;
    expect(region.style.getPropertyValue('height')).toBe('var(--dock-draft-h, 420px)');
  });

  it('成员离开 spec（组件完备文档）时 prune 后区不再渲染', () => {
    useHudStore.getState().dockPanel('chart-a', 'right');
    // 组件完备但已移除 chart-a 的新代次 spec
    const next = specWithPanels();
    next.layout!.components = next.layout!.components!.filter((c) => c.id !== 'chart-a');
    commitMapSpecDocument(next, 2);
    render(<PanelDockHost />);
    expect(document.querySelector('[data-dock-region="right"]')).toBeNull();
    expect(useHudStore.getState().dockPlacements['chart-a']).toBeUndefined();
  });

  it('中间态 spec（缺 components 数组）不触发 prune（归属保留）', () => {
    useHudStore.getState().dockPanel('chart-a', 'right');
    commitMapSpecDocument({ version: '1.0', layers: [] } as unknown as MapSpec, 2);
    render(<Probe onChange={() => {}} />);
    expect(useHudStore.getState().dockPlacements['chart-a']).toBe('right');
  });
});
