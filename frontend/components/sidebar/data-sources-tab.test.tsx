import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { CATALOG_SEARCH_DEBOUNCE_MS } from './data-sources-tab';

/**
 * A-F-08: search-as-you-type used to fire one GET /data-fabric/catalog per
 * keystroke (the effect re-ran fetchCatalog on every searchQuery change) with
 * no debounce and no cancellation of the previous in-flight request.
 *
 * These tests pin the fix contract:
 *   - N rapid keystrokes → exactly 1 downstream catalog fetch, after the
 *     300ms quiet window, carrying the final query;
 *   - a newer debounced fetch aborts the still-pending previous one and its
 *     late/stale response is discarded (never clobbers the UI);
 *   - typing in the catalog search does not refetch the data-sources list.
 */

// Selector-style store mocks (same pattern as layers-tab.test.tsx): the vi.mock
// factory only reads these lazily, at render time, so hoisting is safe.
const toastStore = { toasts: [] as unknown[], addToast: vi.fn(), removeToast: vi.fn() };
vi.mock('@/components/ui/toast', () => ({
  useToastStore: (selector: (s: typeof toastStore) => any) => selector(toastStore),
}));

const hudStore = { addLayer: vi.fn(), updateLayer: vi.fn(), theme: 'dark' };
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: typeof hudStore) => any) => selector(hudStore),
}));

vi.mock('@/lib/api/data-fabric', () => ({
  dataFabricApi: {
    listDataSources: vi.fn(),
    listSpatialCatalog: vi.fn(),
    createDataSource: vi.fn(),
    probeDataSource: vi.fn(),
    syncDataSourceCatalog: vi.fn(),
    deleteDataSource: vi.fn(),
    getCatalogItemDescriptor: vi.fn(),
    previewCatalogItem: vi.fn(),
    materializeCatalogItem: vi.fn(),
    fetchRefGeoJSON: vi.fn(),
  },
}));

// Import AFTER the mocks are registered so the component picks them up.
import { DataSourcesTab } from './data-sources-tab';
import { dataFabricApi } from '@/lib/api/data-fabric';

type CatalogResponse = Awaited<ReturnType<typeof dataFabricApi.listSpatialCatalog>>;

const listSpatialCatalog = () => vi.mocked(dataFabricApi.listSpatialCatalog);

function emptyCatalog(): CatalogResponse {
  return { total: 0, limit: 50, offset: 0, items: [] };
}

function makeCatalogItem(id: string, title: string): CatalogResponse['items'][number] {
  return {
    id,
    source_id: 's1',
    name: title,
    title,
    description: `${title} 描述`,
    feature_type: 'vector',
  };
}

describe('DataSourcesTab — catalog search debounce (A-F-08)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(dataFabricApi.listDataSources).mockResolvedValue({ sources: [] });
    listSpatialCatalog().mockResolvedValue(emptyCatalog());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('coalesces N rapid search keystrokes into a single catalog fetch with the final query', async () => {
    vi.useFakeTimers();
    render(<DataSourcesTab />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    // Mount fetch fired immediately (initial catalog load is not delayed).
    expect(listSpatialCatalog()).toHaveBeenCalledTimes(1);

    // Three rapid keystrokes inside the debounce window → still no fetch yet.
    const input = screen.getByPlaceholderText(/搜索空间数据集/);
    fireEvent.change(input, { target: { value: 's' } });
    fireEvent.change(input, { target: { value: 'sc' } });
    fireEvent.change(input, { target: { value: 'scho' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CATALOG_SEARCH_DEBOUNCE_MS - 1);
    });
    expect(listSpatialCatalog()).toHaveBeenCalledTimes(1);

    // Once the quiet window elapses: exactly one fetch, with the final query.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(listSpatialCatalog()).toHaveBeenCalledTimes(2);
    expect(listSpatialCatalog()).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: 'scho', limit: 50 })
    );

    // Further typing after a settled fetch still collapses to one fetch per pause.
    fireEvent.change(input, { target: { value: 'school' } });
    fireEvent.change(input, { target: { value: 'schools' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CATALOG_SEARCH_DEBOUNCE_MS);
    });
    expect(listSpatialCatalog()).toHaveBeenCalledTimes(3);
    expect(listSpatialCatalog()).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: 'schools' })
    );
  });

  it('aborts the previous in-flight catalog fetch and discards its stale response', async () => {
    vi.useFakeTimers();
    let resolveFirst!: (v: CatalogResponse) => void;
    listSpatialCatalog()
      .mockImplementationOnce(
        () =>
          new Promise<CatalogResponse>((resolve) => {
            resolveFirst = resolve;
          })
      )
      .mockResolvedValueOnce({ total: 1, limit: 50, offset: 0, items: [makeCatalogItem('2', 'schools result')] });

    render(<DataSourcesTab />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    // First request is still in flight with an un-aborted signal.
    const firstSignal = listSpatialCatalog().mock.calls[0][0]?.signal;
    expect(firstSignal?.aborted).toBe(false);

    // A newer query debounces, then fires the second fetch → aborts the first.
    fireEvent.change(screen.getByPlaceholderText(/搜索空间数据集/), { target: { value: 'schools' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CATALOG_SEARCH_DEBOUNCE_MS);
    });
    expect(listSpatialCatalog().mock.calls[1][0]?.signal?.aborted).toBe(false);
    expect(firstSignal?.aborted).toBe(true);

    // The superseded first request resolves late — its result must not clobber
    // the newer results already shown.
    resolveFirst({ total: 1, limit: 50, offset: 0, items: [makeCatalogItem('1', 'stale result')] });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.queryByText('stale result')).not.toBeInTheDocument();
    expect(screen.getByText('schools result')).toBeInTheDocument();
    expect(listSpatialCatalog()).toHaveBeenCalledTimes(2);
  });

  it('does not refetch the data-sources list when the catalog search query changes', async () => {
    vi.useFakeTimers();
    render(<DataSourcesTab />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(dataFabricApi.listDataSources).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByPlaceholderText(/搜索空间数据集/), { target: { value: 'sch' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CATALOG_SEARCH_DEBOUNCE_MS);
    });

    expect(dataFabricApi.listDataSources).toHaveBeenCalledTimes(1);
    expect(listSpatialCatalog()).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// #463 — materialize ("加载至地图") must target the REAL session and produce a
// fetchable, renderable ref-backed layer (previously: a phantom
// window.__WEBGIS_SESSION_ID__ global with zero writers sent
// session_id='default_session' — 401 for anonymous users, false success +
// invisible layer for authed ones).
// ---------------------------------------------------------------------------

import { ApiError } from '@/lib/api/transport';

const materialize = () => vi.mocked(dataFabricApi.materializeCatalogItem);
const fetchRef = () => vi.mocked(dataFabricApi.fetchRefGeoJSON);

const GEOJSON = {
  type: 'FeatureCollection' as const,
  features: [
    { type: 'Feature' as const, properties: {}, geometry: { type: 'Point', coordinates: [116.4, 39.9] } },
    { type: 'Feature' as const, properties: {}, geometry: { type: 'Point', coordinates: [116.5, 39.95] } },
  ],
};

async function renderWithCatalogItem(props?: { sessionId?: string | null; ownerToken?: string | null }) {
  listSpatialCatalog().mockResolvedValue({
    total: 1, limit: 50, offset: 0, items: [makeCatalogItem('cat1', '学校分布')],
  });
  render(<DataSourcesTab sessionId={props?.sessionId} ownerToken={props?.ownerToken} />);
  const button = await screen.findByRole('button', { name: /加载至地图/ });
  return button;
}

describe('DataSourcesTab — materialize session + layer wiring (#463)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(dataFabricApi.listDataSources).mockResolvedValue({ sources: [] });
    listSpatialCatalog().mockResolvedValue(emptyCatalog());
    materialize().mockResolvedValue({
      success: true,
      ref_id: 'ref:df-abc123',
      feature_count: 2,
      total_count: 2,
      dataset_id: 'cat1',
      source_id: 's1',
      title: '学校分布',
    });
    fetchRef().mockResolvedValue(GEOJSON);
  });

  it('threads the real sessionId/ownerToken from props into the materialize request', async () => {
    const button = await renderWithCatalogItem({ sessionId: 'sess-real-42', ownerToken: 'tok-7' });
    await act(async () => {
      fireEvent.click(button);
    });

    expect(materialize()).toHaveBeenCalledTimes(1);
    expect(materialize()).toHaveBeenCalledWith({
      session_id: 'sess-real-42',
      catalog_item_id: 'cat1',
      ownerToken: 'tok-7',
    });
    // Never the phantom fallback id.
    expect(materialize().mock.calls[0][0]?.session_id).not.toBe('default_session');
  });

  it('adds a fetchable ref-backed layer (source present, not skipped by the mapspec adapter) and hydrates it', async () => {
    const button = await renderWithCatalogItem({ sessionId: 'sess-real-42' });
    await act(async () => {
      fireEvent.click(button);
    });

    expect(hudStore.addLayer).toHaveBeenCalledTimes(1);
    const layer = hudStore.addLayer.mock.calls[0][0];
    expect(layer.id).toBe('df-cat1');
    expect(layer._refId).toBe('ref:df-abc123');
    // A placeholder FeatureCollection source keeps the layer renderable — the
    // mapspec adapter skips sourceless layers outright (#463).
    expect(layer.source).toBeDefined();
    expect(layer.source.type).toBe('FeatureCollection');
    expect(layer.source.metadata?.ref_id).toBe('ref:df-abc123');

    // Fetch-on-demand hydrates the layer with the stored ref payload.
    expect(fetchRef()).toHaveBeenCalledWith('ref:df-abc123', 'sess-real-42', expect.anything());
    expect(hudStore.updateLayer).toHaveBeenCalledWith('df-cat1', { source: GEOJSON });
  });

  it('keeps the layer but warns when the ref payload fetch fails (no false silence)', async () => {
    fetchRef().mockRejectedValue(new Error('boom'));
    const button = await renderWithCatalogItem({ sessionId: 'sess-real-42' });
    await act(async () => {
      fireEvent.click(button);
    });

    expect(hudStore.addLayer).toHaveBeenCalledTimes(1);
    const toastCalls = toastStore.addToast.mock.calls.map((c: unknown[]) => String(c[0]));
    expect(toastCalls.some((t: string) => t.includes('加载失败'))).toBe(true);
  });

  it('shows a clear login-required error on 401 instead of a raw status toast', async () => {
    materialize().mockRejectedValue(new ApiError(401, 'Unauthorized', { detail: 'Not authenticated' }, 'req-1', 'DataFabric API error'));
    const button = await renderWithCatalogItem({ sessionId: 'sess-real-42' });
    await act(async () => {
      fireEvent.click(button);
    });

    expect(hudStore.addLayer).not.toHaveBeenCalled();
    const messages = toastStore.addToast.mock.calls.map((c: unknown[]) => String(c[0]));
    expect(messages.some((m: string) => m.includes('登录'))).toBe(true);
    expect(messages.some((m: string) => m.includes('401'))).toBe(false);
  });

  it('blocks materialize with actionable guidance when no session exists', async () => {
    const button = await renderWithCatalogItem({ sessionId: null });
    await act(async () => {
      fireEvent.click(button);
    });

    expect(materialize()).not.toHaveBeenCalled();
    expect(hudStore.addLayer).not.toHaveBeenCalled();
    const messages = toastStore.addToast.mock.calls.map((c: unknown[]) => String(c[0]));
    expect(messages.some((m: string) => m.includes('会话'))).toBe(true);
  });
});
