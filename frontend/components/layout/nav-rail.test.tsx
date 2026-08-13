import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

/**
 * NavRail (UI V3) — 主导航竖排图标栏。
 *
 * Pin 的契约：
 *   - 8 个 tab（chat/project/data_sources/layers/analysis/tasks/results/export_layout）
 *     带 role=tab + aria-selected + roving tabindex；
 *   - 点击 inactive tab → setActiveLeftTab；点击 active tab → toggleLeftPanel；
 *   - ArrowUp/Down/Home/End 键盘导航（自动激活语义）；
 *   - 图层/导出/结果徽标计数；模板库按钮 → setTemplatesOpen(true)。
 */

const setActiveLeftTab = vi.fn();
const toggleLeftPanel = vi.fn();
const setTemplatesOpen = vi.fn();

const store: Record<string, unknown> = {
  activeLeftTab: 'chat',
  setActiveLeftTab,
  leftPanelOpen: true,
  toggleLeftPanel,
  setTemplatesOpen,
  layers: [],
  exports: [],
  results: [],
};

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) => selector(store),
}));

// Import AFTER the mock is registered.
import { NavRail } from './nav-rail';

const TAB_ORDER = ['chat', 'project', 'data_sources', 'layers', 'analysis', 'tasks', 'results', 'export_layout'];
const TAB_LABELS: Record<string, string> = {
  chat: '对话',
  project: '项目',
  data_sources: '数据',
  layers: '图层',
  analysis: '分析',
  tasks: '任务',
  results: '结果',
  export_layout: '制图',
};

describe('NavRail', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    store.activeLeftTab = 'chat';
    store.leftPanelOpen = true;
    store.layers = [];
    store.exports = [];
    store.results = [];
  });

  it('renders 8 tabs with tablist semantics and roving tabindex', () => {
    render(<NavRail />);

    const tablist = screen.getByRole('tablist', { name: '工作区面板' });
    expect(tablist).toHaveAttribute('aria-orientation', 'vertical');

    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(8);
    expect(tabs.map((t) => t.getAttribute('aria-label'))).toEqual(
      TAB_ORDER.map((k) => TAB_LABELS[k])
    );

    // active tab 可聚焦，其余 roving -1
    const chat = screen.getByRole('tab', { name: '对话' });
    expect(chat).toHaveAttribute('aria-selected', 'true');
    expect(chat).toHaveAttribute('tabindex', '0');
    for (const t of tabs) {
      if (t !== chat) expect(t).toHaveAttribute('tabindex', '-1');
    }
  });

  it('aria-selected reflects the active tab even when the panel is collapsed', () => {
    store.leftPanelOpen = false;
    render(<NavRail />);
    // APG：aria-selected 表达当前 tab；折叠态由折叠按钮 aria-expanded 传达
    expect(screen.getByRole('tab', { name: '对话' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: '展开面板' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('clicking an inactive tab activates it', () => {
    render(<NavRail />);
    fireEvent.click(screen.getByRole('tab', { name: '图层' }));
    expect(setActiveLeftTab).toHaveBeenCalledWith('layers');
    expect(toggleLeftPanel).not.toHaveBeenCalled();
  });

  it('clicking the active tab toggles the panel (map-first collapse)', () => {
    render(<NavRail />);
    fireEvent.click(screen.getByRole('tab', { name: '对话' }));
    expect(toggleLeftPanel).toHaveBeenCalledTimes(1);
    expect(setActiveLeftTab).not.toHaveBeenCalled();
  });

  it('ArrowDown activates the next tab; ArrowUp wraps to the last', () => {
    render(<NavRail />);
    const tablist = screen.getByRole('tablist', { name: '工作区面板' });

    fireEvent.keyDown(tablist, { key: 'ArrowDown' });
    expect(setActiveLeftTab).toHaveBeenCalledWith('project');

    fireEvent.keyDown(tablist, { key: 'ArrowUp' });
    expect(setActiveLeftTab).toHaveBeenCalledWith('export_layout');
  });

  it('Home/End jump to first/last tab', () => {
    store.activeLeftTab = 'layers';
    render(<NavRail />);
    const tablist = screen.getByRole('tablist', { name: '工作区面板' });

    fireEvent.keyDown(tablist, { key: 'End' });
    expect(setActiveLeftTab).toHaveBeenCalledWith('export_layout');

    fireEvent.keyDown(tablist, { key: 'Home' });
    expect(setActiveLeftTab).toHaveBeenCalledWith('chat');
  });

  it('shows layer/export count badges only when non-zero', () => {
    const { rerender } = render(<NavRail />);
    expect(screen.getByRole('tab', { name: '图层' }).textContent).toBe('');

    store.layers = [{ id: 'L1' }, { id: 'L2' }, { id: 'L3' }];
    store.exports = [{ id: 'E1' }];
    rerender(<NavRail />);
    expect(screen.getByRole('tab', { name: '图层' }).textContent).toBe('3');
    expect(screen.getByRole('tab', { name: '制图' }).textContent).toBe('1');
  });

  it('template gallery button opens the templates drawer', () => {
    render(<NavRail />);
    fireEvent.click(screen.getByRole('button', { name: '模板库' }));
    expect(setTemplatesOpen).toHaveBeenCalledWith(true);
  });

  it('collapse button reflects panel state via aria-expanded', () => {
    const { rerender } = render(<NavRail />);
    expect(screen.getByRole('button', { name: '折叠面板' })).toHaveAttribute('aria-expanded', 'true');

    store.leftPanelOpen = false;
    rerender(<NavRail />);
    const expand = screen.getByRole('button', { name: '展开面板' });
    expect(expand).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(expand);
    expect(toggleLeftPanel).toHaveBeenCalledTimes(1);
  });
});
