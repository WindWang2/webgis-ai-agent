/**
 * Regression tests for ISSUE-001/002/003 (commit 9766389):
 * The dropdown's onClick handler must dual-write to BOTH:
 *   - useMapAction.setSelectedBaseLayer(idx)   — drives MAP_STYLES[idx]
 *   - useHudStore.setBaseLayer(canonicalName)  — drives status bar / HUD / env summary
 *
 * If either side is dropped, label drifts out of sync with the rendered tiles.
 * This file pins the user-click half of the fix; the AI-driven half is pinned by
 * map-action-handler.test.tsx::regression ISSUE-002 (BASE_LAYER_CHANGE).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { BaselayerSwitcher } from './baselayer-switcher';

const mockSetSelectedBaseLayer = vi.fn();
const mockSetBaseLayer = vi.fn();
let selectedBaseLayer = 0;
let baseLayer = 'Carto Light';

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: { baseLayer: string; setBaseLayer: typeof mockSetBaseLayer }) => unknown) =>
    selector({ baseLayer, setBaseLayer: mockSetBaseLayer }),
}));

vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({
    selectedBaseLayer,
    setSelectedBaseLayer: mockSetSelectedBaseLayer,
  }),
}));

vi.mock('@/lib/providers', () => ({
  TILE_PROVIDERS: [
    { name: 'Carto Light', keywords: ['carto', 'light'] },
    { name: 'Carto Dark', keywords: ['dark'] },
    { name: 'ESRI 影像', keywords: ['satellite', '卫星'] },
  ],
}));

describe('BaselayerSwitcher', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    selectedBaseLayer = 0;
    baseLayer = 'Carto Light';
  });

  it('renders the current label from TILE_PROVIDERS[selectedBaseLayer]', () => {
    render(<BaselayerSwitcher />);
    // Trigger button is the only button with the current label
    expect(screen.getByRole('button', { name: /Base layer/i })).toHaveTextContent('Carto Light');
  });

  it('regression ISSUE-001/003: clicking an item dual-writes to BOTH stores', () => {
    render(<BaselayerSwitcher />);

    // Open dropdown
    fireEvent.click(screen.getByRole('button', { name: /Base layer/i }));

    // Click the second item (Carto Dark, idx=1)
    const darkOption = screen.getByRole('option', { name: 'Carto Dark' });
    fireEvent.click(darkOption);

    // The bug pre-9766389: only one of these would be called. Both must fire.
    expect(mockSetSelectedBaseLayer).toHaveBeenCalledWith(1);
    expect(mockSetBaseLayer).toHaveBeenCalledWith('Carto Dark');
  });

  it('regression ISSUE-001/003: third item also dual-writes', () => {
    render(<BaselayerSwitcher />);
    fireEvent.click(screen.getByRole('button', { name: /Base layer/i }));
    fireEvent.click(screen.getByRole('option', { name: 'ESRI 影像' }));

    expect(mockSetSelectedBaseLayer).toHaveBeenCalledWith(2);
    expect(mockSetBaseLayer).toHaveBeenCalledWith('ESRI 影像');
  });

  it('closes the dropdown after a selection', () => {
    render(<BaselayerSwitcher />);
    fireEvent.click(screen.getByRole('button', { name: /Base layer/i }));
    expect(screen.queryByRole('listbox')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('option', { name: 'Carto Dark' }));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('a11y: trigger button has aria-haspopup and aria-expanded reflects state', () => {
    render(<BaselayerSwitcher />);
    const trigger = screen.getByRole('button', { name: /Base layer/i });

    expect(trigger).toHaveAttribute('aria-haspopup', 'listbox');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
  });

  it('a11y: active item has aria-selected=true', () => {
    selectedBaseLayer = 1; // Carto Dark active
    render(<BaselayerSwitcher />);
    fireEvent.click(screen.getByRole('button', { name: /Base layer/i }));

    const darkOption = screen.getByRole('option', { name: 'Carto Dark' });
    expect(darkOption).toHaveAttribute('aria-selected', 'true');

    const lightOption = screen.getByRole('option', { name: 'Carto Light' });
    expect(lightOption).toHaveAttribute('aria-selected', 'false');
  });

  it('a11y: Escape closes the dropdown', () => {
    render(<BaselayerSwitcher />);
    fireEvent.click(screen.getByRole('button', { name: /Base layer/i }));
    expect(screen.queryByRole('listbox')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('a11y: clicking outside closes the dropdown', () => {
    render(
      <div>
        <BaselayerSwitcher />
        <div data-testid='outside'>elsewhere</div>
      </div>
    );
    fireEvent.click(screen.getByRole('button', { name: /Base layer/i }));
    expect(screen.queryByRole('listbox')).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByTestId('outside'));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('falls back to HUD baseLayer when selectedBaseLayer index is out of range', () => {
    selectedBaseLayer = 99; // out of range
    baseLayer = 'Some Stored Layer';
    render(<BaselayerSwitcher />);

    expect(screen.getByRole('button', { name: /Base layer/i })).toHaveTextContent('Some Stored Layer');
  });
});
