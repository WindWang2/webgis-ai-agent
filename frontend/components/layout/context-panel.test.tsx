import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

/**
 * ContextPanel (UI V3) — 统一上下文面板。
 *
 * Pin 的契约：
 *   - role=tabpanel + PanelHeader（title/description/badge/close）；
 *   - tab 切换即卸载（只渲染 active tab 的内容）；
 *   - 折叠时 aria-hidden + visibility 隐藏；
 *   - 右缘 separator 键盘调宽（280–420 clamp）。
 */

const toggleLeftPanel = vi.fn();
const setSidebarWidth = vi.fn();

const store: Record<string, unknown> = {
  activeLeftTab: 'chat',
  leftPanelOpen: true,
  toggleLeftPanel,
  sidebarWidth: 320,
  setSidebarWidth,
  layers: [],
  exports: [],
  results: [],
};

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) => selector(store),
}));

// 重依赖 tab 全部打桩，只验证 ContextPanel 自身的编排逻辑。
vi.mock('@/components/sidebar/chat-tab', () => ({ ChatTab: () => <div data-testid="tab-chat" /> }));
vi.mock('@/components/sidebar/project-tab', () => ({ ProjectTab: () => <div data-testid="tab-project" /> }));
vi.mock('@/components/sidebar/layers-tab', () => ({ LayersTab: () => <div data-testid="tab-layers" /> }));
vi.mock('@/components/sidebar/analysis-tab', () => ({ AnalysisTab: () => <div data-testid="tab-analysis" /> }));
vi.mock('@/components/sidebar/data-sources-tab', () => ({ DataSourcesTab: () => <div data-testid="tab-data-sources" /> }));
vi.mock('@/components/sidebar/map-studio-tab', () => ({ MapStudioTab: () => <div data-testid="tab-map-studio" /> }));
vi.mock('@/components/sidebar/tasks-tab', () => ({ TasksTab: () => <div data-testid="tab-tasks" /> }));
vi.mock('@/components/sidebar/results-tab', () => ({ ResultsTab: () => <div data-testid="tab-results" /> }));

// Import AFTER the mocks are registered.
import { ContextPanel } from './context-panel';

const baseProps = {
  messages: [],
  aiStatus: 'idle' as const,
  onSend: vi.fn(),
};

describe('ContextPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    store.activeLeftTab = 'chat';
    store.leftPanelOpen = true;
    store.sidebarWidth = 320;
    store.layers = [];
    store.exports = [];
  });

  it('renders tabpanel with PanelHeader meta for the active tab', () => {
    render(<ContextPanel {...baseProps} />);

    const panel = screen.getByRole('tabpanel');
    expect(panel).toHaveAttribute('id', 'workspace-panel');
    expect(panel).toHaveAttribute('aria-labelledby', 'rail-tab-chat');

    expect(screen.getByText('对话')).toBeInTheDocument();
    expect(screen.getByText('AI 地理智能体')).toBeInTheDocument();
    expect(screen.getByTestId('tab-chat')).toBeInTheDocument();
    // 切换即卸载：其它 tab 内容不渲染
    expect(screen.queryByTestId('tab-layers')).not.toBeInTheDocument();
    expect(screen.queryByTestId('tab-tasks')).not.toBeInTheDocument();
  });

  it('switches content + header meta when the active tab changes', () => {
    store.activeLeftTab = 'layers';
    store.layers = [{ id: 'L1' }, { id: 'L2' }];
    render(<ContextPanel {...baseProps} />);

    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'rail-tab-layers');
    expect(screen.getByText('图层')).toBeInTheDocument();
    // 徽标 = 图层数
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByTestId('tab-layers')).toBeInTheDocument();
    expect(screen.queryByTestId('tab-chat')).not.toBeInTheDocument();
  });

  it('maps legacy exports tab onto the map studio panel', () => {
    store.activeLeftTab = 'exports';
    render(<ContextPanel {...baseProps} />);
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'rail-tab-export_layout');
    expect(screen.getByText('制图工坊')).toBeInTheDocument();
    expect(screen.getByTestId('tab-map-studio')).toBeInTheDocument();
  });

  it('close button collapses the panel', () => {
    render(<ContextPanel {...baseProps} />);
    fireEvent.click(screen.getByRole('button', { name: '收起面板' }));
    expect(toggleLeftPanel).toHaveBeenCalledTimes(1);
  });

  it('is aria-hidden when collapsed', () => {
    store.leftPanelOpen = false;
    render(<ContextPanel {...baseProps} />);
    expect(screen.getByRole('tabpanel', { hidden: true })).toHaveAttribute('aria-hidden', 'true');
  });

  it('separator keyboard arrows resize within 280–420 clamp', () => {
    render(<ContextPanel {...baseProps} />);
    const separator = screen.getByRole('separator', { name: '调整面板宽度' });
    expect(separator).toHaveAttribute('aria-valuenow', '320');

    fireEvent.keyDown(separator, { key: 'ArrowRight' });
    expect(setSidebarWidth).toHaveBeenCalledWith(336);

    fireEvent.keyDown(separator, { key: 'ArrowLeft' });
    expect(setSidebarWidth).toHaveBeenCalledWith(304);
  });

  it('separator clamps at the max width', () => {
    store.sidebarWidth = 420;
    render(<ContextPanel {...baseProps} />);
    const separator = screen.getByRole('separator', { name: '调整面板宽度' });
    fireEvent.keyDown(separator, { key: 'ArrowRight' });
    expect(setSidebarWidth).toHaveBeenCalledWith(420);
  });

  it('double-click resets the width to the 320 default', () => {
    store.sidebarWidth = 400;
    render(<ContextPanel {...baseProps} />);
    fireEvent.doubleClick(screen.getByRole('separator', { name: '调整面板宽度' }));
    expect(setSidebarWidth).toHaveBeenCalledWith(320);
  });
});
