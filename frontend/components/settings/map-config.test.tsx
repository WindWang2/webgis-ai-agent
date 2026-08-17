/**
 * #550 regression + contract tests: the settings basemap cards must be sourced
 * from the SAME vocabulary that drives rendering (TILE_PROVIDERS), and a card
 * click must dual-write index + canonical name — otherwise switching is a no-op
 * and labels drift (pre-fix: cards showed DEFAULT_MAP_STYLES demo names that
 * intersect TILE_PROVIDERS zero times).
 *
 * Contract (not a mock-pair test): the store's useHudStore and map-action
 * context are mocked, but TILE_PROVIDERS is the REAL catalogue — every
 * rendered card name is asserted to be ∈ TILE_PROVIDERS[].name.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MapConfig } from './map-config';
import { TILE_PROVIDERS } from '@/lib/providers';

const mockSetSelectedBaseLayer = vi.fn();
const mockSetBaseLayer = vi.fn();
let selectedBaseLayer = 0;
let baseLayer: string | null = 'Carto 深色';

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: { baseLayer: string | null; setBaseLayer: typeof mockSetBaseLayer }) => unknown) =>
    selector({ baseLayer, setBaseLayer: mockSetBaseLayer }),
}));

vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({
    selectedBaseLayer,
    setSelectedBaseLayer: mockSetSelectedBaseLayer,
  }),
}));

describe('MapConfig basemap cards (#550)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    selectedBaseLayer = 0;
    baseLayer = 'Carto 深色';
  });

  it('contract: every rendered card name is a real TILE_PROVIDERS name (zero demo vocabulary)', () => {
    render(<MapConfig />);

    // One card button per provider (aria-pressed marks the active one); the
    // button text is `${provider.name}${desc}`, so match by prefix.
    const cardButtons = screen.getAllByRole('button', { pressed: false })
      .concat(screen.getAllByRole('button', { pressed: true }));
    const texts = cardButtons.map((b) => b.textContent ?? '');

    for (const provider of TILE_PROVIDERS) {
      expect(texts.some((t) => t.startsWith(provider.name))).toBe(true);
    }
    // Card count equals the catalogue size (plus the 3 CRS options have no
    // aria-pressed, so they don't pollute this set).
    expect(cardButtons.length).toBe(TILE_PROVIDERS.length);

    // No demo-vocabulary names may appear anywhere.
    for (const demoName of ['OSM Voyager', 'OSM Dark', 'Satellite', 'Topo', 'Blank White']) {
      expect(screen.queryByText(demoName)).not.toBeInTheDocument();
    }
  });

  it('contract: clicking a card dual-writes index AND canonical provider name', async () => {
    const user = userEvent.setup();
    render(<MapConfig />);

    const target = TILE_PROVIDERS[2]; // e.g. 'Carto 浅色' or 'OSM 地图'
    await user.click(screen.getByRole('button', { name: new RegExp(target.name) }));

    expect(mockSetSelectedBaseLayer).toHaveBeenCalledWith(2);
    expect(mockSetBaseLayer).toHaveBeenCalledWith(target.name);
  });

  it('clicking the last card dual-writes its real index and name', async () => {
    const user = userEvent.setup();
    render(<MapConfig />);

    const last = TILE_PROVIDERS[TILE_PROVIDERS.length - 1];
    await user.click(screen.getByRole('button', { name: new RegExp(last.name) }));

    expect(mockSetSelectedBaseLayer).toHaveBeenCalledWith(TILE_PROVIDERS.length - 1);
    expect(mockSetBaseLayer).toHaveBeenCalledWith(last.name);
  });

  it('active card tracks the selectedBaseLayer index', () => {
    selectedBaseLayer = 1;
    baseLayer = TILE_PROVIDERS[1].name; // keep name consistent with the index
    render(<MapConfig />);
    expect(screen.getAllByRole('button', { pressed: true })).toHaveLength(1);
    expect(screen.getByRole('button', { pressed: true })).toHaveTextContent(TILE_PROVIDERS[1].name);
  });

  it('the fake "Add Custom Basemap" form is gone (it could never render a provider)', () => {
    render(<MapConfig />);
    expect(screen.queryByText(/Add Custom Basemap/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/My Custom Map/i)).not.toBeInTheDocument();
  });
});