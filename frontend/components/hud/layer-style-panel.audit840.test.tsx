/**
 * audit #840/#841/#842: LayerStylePanel honesty on spec-backed layers.
 *
 * - committed MapSpec layers: style controls disabled with an explanation
 *   (previously silent no-ops); palette/intensity controls removed entirely
 *   (zero consumers on any path); radius slider clamped to the contract
 *   window [4,80].
 * - commitOpacity routes through the rollback wrapper instead of a bare
 *   void commit (no unhandled rejections / state divergence).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LayerStylePanel } from './layer-style-panel';

const mockState = {
  editingLayerId: 'layer-1' as string | null,
  layers: [
    {
      id: 'layer-1',
      name: 'POI 层',
      type: 'vector',
      visible: true,
      opacity: 0.8,
      style: { color: '#00f2ff', radius_px: 30 },
    },
  ],
  updateLayer: vi.fn(),
  setEditingLayerId: vi.fn(),
};

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: typeof mockState) => unknown) => selector(mockState),
}));

const cursorListeners = new Set<() => void>();
let committedSpec: unknown = null;

vi.mock('@/lib/mapspec/session-cursor', () => ({
  getCommittedMapSpec: () => committedSpec,
  subscribeMapSpecLive: (cb: () => void) => {
    cursorListeners.add(cb);
    return () => cursorListeners.delete(cb);
  },
}));

const opacityCommit = vi.fn();
vi.mock('@/lib/mapspec/user-mutation', () => ({
  setLayerOpacityAndCommit: (...args: unknown[]) => opacityCommit(...args),
}));

const heatContractWindow = vi.fn(async () => {
  // mirror assertion source: the adapter must use the 4-60 legacy window
  const src = await import('@/lib/mapspec-runtime/adapter');
  const text = (await import('@/lib/mapspec-runtime/adapter?raw')).default as string;
  return { src, text };
});

beforeEach(() => {
  vi.clearAllMocks();
  committedSpec = null;
  cursorListeners.clear();
});

describe('audit #840: spec-backed gating', () => {
  it('shows style controls when no committed spec exists', () => {
    render(<LayerStylePanel />);
    expect(screen.getByLabelText('填充颜色')).toBeTruthy();
    expect(screen.queryByText(/由 AI 生成的制图规范/)).toBeNull();
  });

  it('disables style controls with an explanation on committed-spec layers', () => {
    committedSpec = { layers: [{ id: 'layer-1' }] };
    render(<LayerStylePanel />);
    expect(screen.getByText(/由 AI 生成的制图规范/)).toBeTruthy();
    // jsdom does not reflect fieldset-disable onto descendants' .disabled
    // property — assert the fieldset gate itself and the notice.
    const color = screen.getByLabelText('填充颜色') as HTMLInputElement;
    const fieldset = color.closest('fieldset') as HTMLFieldSetElement;
    expect(fieldset.disabled).toBe(true);
    // opacity stays functional: it renders outside the gated fieldset
    const opacity = document.querySelector(
      'input[type="range"]:not(fieldset[disabled] input)') as HTMLInputElement | null;
    expect(opacity).toBeTruthy();
    expect(opacity!.closest('fieldset')).toBeNull();
  });

  it('removes the never-consumed palette/intensity controls', () => {
    mockState.layers[0].type = 'heatmap';
    render(<LayerStylePanel />);
    expect(screen.queryByText('色带')).toBeNull();
    expect(screen.queryByText('热力强度')).toBeNull();
    // radius slider survives (has real consumers)
    expect(screen.getByText(/热力半径/)).toBeTruthy();
    mockState.layers[0].type = 'vector';
  });
});

describe('audit #842: opacity commit via rollback wrapper', () => {
  it('commits opacity through setLayerOpacityAndCommit', () => {
    render(<LayerStylePanel />);
    const sliders = screen.getAllByRole('slider');
    const opacitySlider = sliders[sliders.length - 1];
    fireEvent.pointerUp(opacitySlider);
    expect(opacityCommit).toHaveBeenCalled();
  });
});

describe('audit #841: radius windows', () => {
  it('adapter legacy window matches the contract (4-60)', async () => {
    const { text } = await heatContractWindow();
    expect(text).toContain('legacyPx >= 4 && legacyPx <= 60');
    expect(text).not.toContain('legacyPx >= 4 && legacyPx <= 100');
    void text; void (await import('@/lib/mapspec-runtime/adapter'));
  });

  it('slider range is clamped to [4,80]', () => {
    mockState.layers[0].type = 'heatmap';
    render(<LayerStylePanel />);
    const radiusLabel = screen.getByText(/热力半径/);
    const radiusSlider = radiusLabel.parentElement!.querySelector('input[type="range"]') as HTMLInputElement;
    expect(radiusSlider.min).toBe('4');
    expect(radiusSlider.max).toBe('80');
    mockState.layers[0].type = 'vector';
  });
});
