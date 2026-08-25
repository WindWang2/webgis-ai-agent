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

// ── #806: 打开即聚焦 listbox（键盘漫游立即可用） ─────────────────────────

describe('BaselayerSwitcher keyboard focus (#806)', () => {
  it('打开下拉后焦点落在 listbox 上，ArrowDown+Enter 可完成选择', () => {
    render(<BaselayerSwitcher />);
    fireEvent.click(screen.getByRole('button', { name: /Base layer/i }));
    const listbox = screen.getByRole('listbox');
    expect(document.activeElement).toBe(listbox);
    // 方向键在 listbox 上直接生效（此前焦点留在按钮上，方向键无响应）
    fireEvent.keyDown(listbox, { key: 'ArrowDown' });
    fireEvent.keyDown(listbox, { key: 'Enter' });
    expect(mockSetSelectedBaseLayer).toHaveBeenCalled();
    expect(mockSetBaseLayer).toHaveBeenCalled();
  });
});


// U-9（#891）：aria-selected 只表达真实选中 —— 键盘漫游（ArrowDown 移动
// activeIdx 视觉高亮）不得把漫游项播报为已选中。
describe('U-9 (#891) aria-selected 语义', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    selectedBaseLayer = 0;
    baseLayer = 'Carto Light';
  });

  it('ArrowDown 漫游后仍只有真实选中项 aria-selected=true', () => {
    render(<BaselayerSwitcher />);
    const trigger = screen.getByRole('button', { name: /base layer/i });
    fireEvent.click(trigger);
    const selectedBefore = screen
      .getAllByRole('option')
      .filter((o) => o.getAttribute('aria-selected') === 'true').length;
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    const options = screen.getAllByRole('option');
    const selectedAfter = options.filter((o) => o.getAttribute('aria-selected') === 'true');
    expect(selectedAfter.length).toBe(selectedBefore);
    expect(selectedAfter.length).toBe(1);
  });
});
