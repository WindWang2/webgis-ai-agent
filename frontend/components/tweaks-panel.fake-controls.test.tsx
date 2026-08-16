/**
 * #551: Tweaks panel fake children removed — 信息密度 (density) and
 * 显示地图网格 (showGrid) had zero consumers outside the panel itself
 * (MapCanvas, the only showGrid consumer, is an unmounted dead component;
 * density had no consumer at all). Controls whose store fields DO have real
 * consumers (accentColor / fontSize / theme / sidebarWidth / hudOpen /
 * ragPanelOpen) stay.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TweaksPanel } from './tweaks-panel';

const mockState = {
  tweaksOpen: true,
  setTweaksOpen: vi.fn(),
  accentColor: '#15803d',
  setAccentColor: vi.fn(),
  theme: 'light',
  setTheme: vi.fn(),
  fontSize: 15,
  setFontSize: vi.fn(),
  hudOpen: false,
  setHudOpen: vi.fn(),
  ragPanelOpen: false,
  setRagPanelOpen: vi.fn(),
  sidebarWidth: 330,
  setSidebarWidth: vi.fn(),
};

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: typeof mockState) => unknown) => selector(mockState),
}));

vi.mock('@/lib/hooks/use-inert', () => ({
  useInertWhenClosed: () => undefined,
}));

vi.mock('@/lib/hooks/use-dialog-focus', () => ({
  useDialogFocus: () => undefined,
}));

describe('TweaksPanel fake children (#551)', () => {
  it('fake controls with zero consumers are gone; real controls remain', () => {
    render(<TweaksPanel />);

    expect(screen.queryByText('信息密度')).not.toBeInTheDocument();
    expect(screen.queryByText('显示地图网格')).not.toBeInTheDocument();

    // children with real consumers stay reachable
    expect(screen.getByText('主题色')).toBeInTheDocument();
    expect(screen.getByText('字体大小')).toBeInTheDocument();
    expect(screen.getByText('主题')).toBeInTheDocument();
    expect(screen.getByText('侧边栏宽度')).toBeInTheDocument();
    expect(screen.getByText('Agent 环境 HUD')).toBeInTheDocument();
    expect(screen.getByText('RAG 独立面板')).toBeInTheDocument();
  });
});