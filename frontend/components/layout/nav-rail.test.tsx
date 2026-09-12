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
  // Workbench V4：模式词表（缺省 explore = 全量 tab 组合不变式仍可测）
  mode: 'explore',
  modeOrigin: null,
  modeActiveTab: { explore: 'chat', analyze: 'analysis', compose: 'components' },
  // Review R1 MAJOR-3 后：tab 协调在 store 的 setWorkbenchMode 内 ——
  // mock 与 store 同构（写 mode 并落该模式记忆 tab）。
  setWorkbenchMode: vi.fn((mode: string, origin?: string) => {
    store.mode = mode;
    store.modeOrigin = origin ?? 'user';
    setActiveLeftTab((store.modeActiveTab as Record<string, string>)[mode] ?? 'chat');
  }),
};

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: Object.assign(
    (selector: (s: any) => any) => selector(store),
    { getState: () => store },
  ),
}));

// Import AFTER the mock is registered.
import { NavRail } from './nav-rail';

const TAB_LABELS: Record<string, string> = {
  chat: '对话',
  project: '项目',
  data_sources: '数据',
  lakehouse: '数据湖',
  layers: '图层',
  components: '组件',
  analysis: '分析',
  tasks: '任务',
  results: '结果',
  export_layout: '制图',
  // V9（ADR-0145）：智能资产面板追加 tab
  market: '市场',
  modelops: 'ModelOps',
};

describe('NavRail', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    store.activeLeftTab = 'chat';
    store.leftPanelOpen = true;
    store.layers = [];
    store.exports = [];
    store.results = [];
    store.mode = 'explore';
    store.modeOrigin = null;
  });

  it('renders mode-filtered tabs with tablist semantics and roving tabindex (explore)', () => {
    render(<NavRail />);

    const tablist = screen.getByRole('tablist', { name: '工作区面板' });
    expect(tablist).toHaveAttribute('aria-orientation', 'vertical');

    // Workbench V4：explore 模式只渲染 MODE_TABS.explore 组合内的 tab，
    // rail 顺序保持 RAIL_GROUPS 稳定序（不随模式重排 —— 空间记忆不变）。
    // V9（ADR-0141）：数据湖 tab 加入 explore/analyze 词表（数据源之后）；
    // V9（ADR-0145）：market/modelops 智能资产面板尾部追加。
    const tabs = screen.getAllByRole('tab');
    const exploreOrder = ['chat', 'project', 'data_sources', 'lakehouse', 'layers', 'tasks', 'market', 'modelops'];
    expect(tabs).toHaveLength(exploreOrder.length);
    expect(tabs.map((t) => t.getAttribute('aria-label'))).toEqual(
      exploreOrder.map((k) => TAB_LABELS[k])
    );

    // active tab 可聚焦，其余 roving -1
    const chat = screen.getByRole('tab', { name: '对话' });
    expect(chat).toHaveAttribute('aria-selected', 'true');
    expect(chat).toHaveAttribute('tabindex', '0');
    for (const t of tabs) {
      if (t !== chat) expect(t).toHaveAttribute('tabindex', '-1');
    }
  });

  it('mode radio switches mode and lands on the remembered tab', () => {
    render(<NavRail />);
    fireEvent.click(screen.getByTestId('mode-analyze'));
    expect(store.setWorkbenchMode).toHaveBeenCalledWith('analyze', 'user');
    // tab 协调已上收 store（setWorkbenchMode 落 activeLeftTab）—— mock 同构。
    expect(setActiveLeftTab).toHaveBeenCalledWith('analysis'); // modeActiveTab.analyze
  });

  it('agent mode switch shows the revert control; click reverts to explore', () => {
    store.mode = 'compose';
    store.modeOrigin = 'agent';
    render(<NavRail />);
    const revert = screen.getByTestId('mode-agent-revert');
    fireEvent.click(revert);
    expect(store.setWorkbenchMode).toHaveBeenCalledWith('explore', 'user');
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

  it('ArrowDown activates the next tab; ArrowUp wraps to the last (mode-filtered)', () => {
    render(<NavRail />);
    const tablist = screen.getByRole('tablist', { name: '工作区面板' });

    // explore 可见序：chat → project → data_sources → layers → tasks → market → modelops
    fireEvent.keyDown(tablist, { key: 'ArrowDown' });
    expect(setActiveLeftTab).toHaveBeenCalledWith('project');

    fireEvent.keyDown(tablist, { key: 'ArrowUp' });
    expect(setActiveLeftTab).toHaveBeenCalledWith('modelops');
  });

  it('Home/End jump to first/last tab (mode-filtered)', () => {
    store.activeLeftTab = 'layers';
    render(<NavRail />);
    const tablist = screen.getByRole('tablist', { name: '工作区面板' });

    fireEvent.keyDown(tablist, { key: 'End' });
    expect(setActiveLeftTab).toHaveBeenCalledWith('modelops');

    fireEvent.keyDown(tablist, { key: 'Home' });
    expect(setActiveLeftTab).toHaveBeenCalledWith('chat');
  });

  it('shows layer/export count badges only when non-zero (per mode)', () => {
    const { rerender } = render(<NavRail />);
    expect(screen.getByRole('tab', { name: '图层' }).textContent).toBe('');

    store.layers = [{ id: 'L1' }, { id: 'L2' }, { id: 'L3' }];
    store.exports = [{ id: 'E1' }];
    rerender(<NavRail />);
    expect(screen.getByRole('tab', { name: '图层' }).textContent).toBe('3');
    // explore 模式不渲染 制图 tab —— 徽标随模式组合出现。
    expect(screen.queryByRole('tab', { name: '制图' })).toBeNull();

    store.mode = 'compose';
    rerender(<NavRail />);
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
