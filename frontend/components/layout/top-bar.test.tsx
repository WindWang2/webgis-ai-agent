/**
 * #551: the Tweaks panel previously had NO production opener —
 * no code path ever called setTweaksOpen(true), so the panel was unreachable.
 * TopBar now renders a real "UI 调整" entry point wired to the store setter.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TopBar from './top-bar';

const mockSetTweaksOpen = vi.fn();
const mockState = {
  leftPanelOpen: true,
  toggleLeftPanel: vi.fn(),
  aiStatus: 'idle',
  setSettingsOpen: vi.fn(),
  setHistoryOpen: vi.fn(),
  setTweaksOpen: mockSetTweaksOpen,
  is3D: false,
  setIs3D: vi.fn(),
};

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: typeof mockState) => unknown) => selector(mockState),
}));

vi.mock('@/lib/hooks/use-prefers-reduced-motion', () => ({
  usePrefersReducedMotion: () => false,
}));

vi.mock('@/components/map/baselayer-switcher', () => ({
  default: () => <div data-testid="baselayer-switcher" />,
}));

describe('TopBar tweaks opener (#551)', () => {
  it('renders a UI 调整 entry point that calls setTweaksOpen(true)', async () => {
    const user = userEvent.setup();
    render(<TopBar />);

    const opener = screen.getByRole('button', { name: 'UI 调整' });
    expect(opener).toBeInTheDocument();

    await user.click(opener);
    expect(mockSetTweaksOpen).toHaveBeenCalledWith(true);
  });
});