import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { ExportItem, ExportSettings } from '@/lib/store/hud-types';

/**
 * MapStudioTab — UI V3 收敛：
 *  1. 制图排版表单的渐进式折叠分区（默认只展开「文档」）；
 *  2. 分段控件（制图排版/导出历史）的 tablist/tab/tabpanel 语义；
 *  3. isExportMode 跟随子页签 AND leftPanelOpen —— 面板折叠时即使
 *     停留在制图排版子页签，也必须把导出蒙层清掉（tab 不会卸载）。
 *
 * store 用可变 mockState（同 layers-tab.test.tsx 模式）：
 * useHudStore: (sel) => sel(mockState)，测试内直接改 mockState 字段。
 */
const updateExportSettings = vi.fn();
const setExports = vi.fn();
const dispatchAction = vi.fn();

const mockState: Record<string, any> = {
  exportSettings: {
    isExportMode: false,
    title: '',
    subtitle: '',
    author: '',
    dataSource: '',
    showWatermark: true,
    showCompass: true,
    showScale: true,
    showLegend: true,
    showMetadata: true,
    showGraticules: false,
    paperSize: 'screen',
    orientation: 'landscape',
    dpi: 96,
    format: 'png',
  } satisfies ExportSettings,
  updateExportSettings,
  exports: [] as ExportItem[],
  setExports,
  accentColor: '#16a34a',
  leftPanelOpen: true,
};

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) => selector(mockState),
}));

vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({ dispatchAction }),
}));

// Import AFTER the mocks are registered so the component picks them up.
import { MapStudioTab } from './map-studio-tab';

beforeEach(() => {
  vi.clearAllMocks();
  mockState.leftPanelOpen = true;
  mockState.exports = [];
});

describe('MapStudioTab — 渐进式折叠分区', () => {
  it('默认只展开「文档」，其余分区折叠且控件不可见', () => {
    render(<MapStudioTab />);

    expect(screen.getByRole('button', { name: /文档/ })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: /地图元素/ })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('button', { name: /页面与输出/ })).toHaveAttribute('aria-expanded', 'false');

    // 文档区控件可见（主标题输入框）
    expect(screen.getByLabelText('主标题')).toBeInTheDocument();
    // 折叠分区控件不可见
    expect(screen.queryByLabelText('指北针')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('输出格式')).not.toBeInTheDocument();
  });

  it('点击分区标题切换 aria-expanded 并显示/隐藏控件', () => {
    render(<MapStudioTab />);

    const elementsHeader = screen.getByRole('button', { name: /地图元素/ });
    fireEvent.click(elementsHeader);
    expect(elementsHeader).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText('指北针')).toBeInTheDocument();

    fireEvent.click(elementsHeader);
    expect(elementsHeader).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByLabelText('指北针')).not.toBeInTheDocument();
  });

  it('折叠分区标题带当前值摘要（地图元素: 启用数量, 页面与输出: 纸张·方向·DPI·格式）', () => {
    render(<MapStudioTab />);

    const elementsHeader = screen.getByRole('button', { name: /地图元素/ });
    expect(elementsHeader.textContent).toContain('5/6 启用');

    const outputHeader = screen.getByRole('button', { name: /页面与输出/ });
    expect(outputHeader.textContent).toContain('屏幕 · 横向 · 96dpi · PNG');
  });
});

describe('MapStudioTab — 分段控件 tab 语义', () => {
  it('渲染 tablist/tab/tabpanel，aria-selected 跟随激活子页签', () => {
    render(<MapStudioTab />);

    expect(screen.getByRole('tablist')).toBeInTheDocument();
    const layoutTab = screen.getByRole('tab', { name: /制图排版/ });
    const historyTab = screen.getByRole('tab', { name: /导出历史/ });

    expect(layoutTab).toHaveAttribute('aria-selected', 'true');
    expect(historyTab).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'map-studio-tab-layout');

    fireEvent.click(historyTab);
    expect(layoutTab).toHaveAttribute('aria-selected', 'false');
    expect(historyTab).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'map-studio-tab-history');
  });

  it('有导出文件时「导出历史」tab 显示 accent 圆点', () => {
    mockState.exports = [{ id: 'e1', name: 'a.png', type: 'png', size: '1.2MB', date: '' }];
    render(<MapStudioTab />);

    const historyTab = screen.getByRole('tab', { name: /导出历史/ });
    expect(historyTab.querySelector('span[aria-hidden]')).not.toBeNull();
  });
});

describe('MapStudioTab — isExportMode 跟随子页签与面板可见性', () => {
  it('面板折叠时即使在「制图排版」也写入 isExportMode:false', () => {
    mockState.leftPanelOpen = false;
    render(<MapStudioTab />);
    expect(updateExportSettings).toHaveBeenLastCalledWith({ isExportMode: false });
  });

  it('面板打开 + 「制图排版」时写入 isExportMode:true', () => {
    mockState.leftPanelOpen = true;
    render(<MapStudioTab />);
    expect(updateExportSettings).toHaveBeenLastCalledWith({ isExportMode: true });
  });

  it('切到「导出历史」写入 false，切回「制图排版」写回 true', () => {
    mockState.leftPanelOpen = true;
    render(<MapStudioTab />);

    fireEvent.click(screen.getByRole('tab', { name: /导出历史/ }));
    expect(updateExportSettings).toHaveBeenLastCalledWith({ isExportMode: false });

    fireEvent.click(screen.getByRole('tab', { name: /制图排版/ }));
    expect(updateExportSettings).toHaveBeenLastCalledWith({ isExportMode: true });
  });

  it('面板折叠/重新打开时跟随 leftPanelOpen 更新', () => {
    mockState.leftPanelOpen = true;
    const { rerender } = render(<MapStudioTab />);
    expect(updateExportSettings).toHaveBeenLastCalledWith({ isExportMode: true });

    mockState.leftPanelOpen = false;
    rerender(<MapStudioTab />);
    expect(updateExportSettings).toHaveBeenLastCalledWith({ isExportMode: false });

    mockState.leftPanelOpen = true;
    rerender(<MapStudioTab />);
    expect(updateExportSettings).toHaveBeenLastCalledWith({ isExportMode: true });
  });

  it('卸载时 cleanup 复位 isExportMode:false', () => {
    mockState.leftPanelOpen = true;
    const { unmount } = render(<MapStudioTab />);
    vi.clearAllMocks();
    unmount();
    expect(updateExportSettings).toHaveBeenLastCalledWith({ isExportMode: false });
  });
});
