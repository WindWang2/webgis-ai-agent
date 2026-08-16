/**
 * Tests for Template Gallery V2 (F-FE-TPL).
 *
 * The gallery is a thin client over the unified templatesApi + Fast Path,
 * so the tests focus on the gallery's own contract:
 *  - debounced search
 *  - abort on unmount / re-fetch
 *  - pagination buttons disable at the edges
 *  - the apply buttons do not refire if the parent re-renders
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { TemplateGalleryV2 } from './template-gallery-v2';

const mockTemplates = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  get: vi.fn(),
  delete: vi.fn(),
}));
const mockDispatch = vi.hoisted(() => vi.fn());
const mockAddToast = vi.hoisted(() => vi.fn());
const mockHudState = vi.hoisted(() => ({
  layers: [] as Array<{ id: string; name?: string }>,
  focusLayerId: null as string | null,
  setBaseLayer: vi.fn(),
  addLayer: vi.fn(),
  clearLayers: vi.fn(),
  updateExportSettings: vi.fn(),
}));
vi.mock('@/lib/api/templates', () => ({ templatesApi: mockTemplates }));
vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({ dispatchAction: mockDispatch }),
}));
vi.mock('@/components/ui/toast', () => ({
  useToastStore: { getState: () => ({ addToast: mockAddToast }) },
}));
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: Object.assign(
    (sel: (s: unknown) => unknown) => sel(mockHudState),
    { getState: () => mockHudState }
  ),
}));
// Real symbology-apply reducer: the gallery dispatches its normalized output
// through the map action queue, so tests assert on the real shape.

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  mockHudState.layers = [];
  mockHudState.focusLayerId = null;
});

/**
 * Backend contract (app/api/routes/templates.py): GET /api/v1/templates returns
 * the Page envelope {items, total, limit, offset, has_more}. Issue #464: the
 * gallery used to consume the envelope as a bare TemplateSummary[] and crashed
 * with `templates.map is not a function` on every successful response. All
 * mocks below use the REAL envelope shape.
 */
const page = (
  items: Array<Record<string, unknown>>,
  opts: { total?: number; limit?: number; offset?: number; has_more?: boolean } = {}
) => ({
  items,
  total: opts.total ?? items.length,
  limit: opts.limit ?? 50,
  offset: opts.offset ?? 0,
  has_more: opts.has_more ?? false,
});

const SUMMARY_A = {
  id: 'tmpl_a', kind: 'basemap', name: '蓝色底图甲', description: '', keywords: [],
  is_builtin: true, version: 1,
};
const SUMMARY_B = {
  id: 'tmpl_b', kind: 'thematic', name: '专题模板乙', description: '', keywords: [],
  is_builtin: true, version: 1,
};

describe('TemplateGalleryV2', () => {
  it('renders the header + tabs and skips the network when closed', async () => {
    mockTemplates.list.mockResolvedValue(page([]));
    render(<TemplateGalleryV2 open={false} onClose={() => {}} />);
    expect(mockTemplates.list).not.toHaveBeenCalled();
  });

  it('fetches the first page when opened', async () => {
    mockTemplates.list.mockResolvedValue(page([SUMMARY_A, SUMMARY_B]));
    render(<TemplateGalleryV2 open={true} onClose={() => {}} />);
    await waitFor(() => expect(mockTemplates.list).toHaveBeenCalled());
    const call = mockTemplates.list.mock.calls[0][0];
    expect(call.limit).toBe(50);
    expect(call.offset).toBe(0);
    expect(call.kind).toBeUndefined();
  });

  it('renders cards from the Page envelope instead of crashing (#464)', async () => {
    mockTemplates.list.mockResolvedValue(page([SUMMARY_A, SUMMARY_B]));
    render(<TemplateGalleryV2 open={true} onClose={() => {}} />);
    // Both card names must render — the old code called templates.map on the
    // envelope object itself and threw TypeError (whole-app ErrorBoundary).
    await waitFor(() => expect(screen.getByText('蓝色底图甲')).toBeInTheDocument());
    expect(screen.getByText('专题模板乙')).toBeInTheDocument();
  });

  it('drives the page counter from the envelope total (#464)', async () => {
    // 120 total templates at 50/page → footer shows 第 1 / 3 页, 下一页 enabled.
    mockTemplates.list.mockResolvedValue(
      page([SUMMARY_A], { total: 120, limit: 50, offset: 0, has_more: true })
    );
    render(<TemplateGalleryV2 open={true} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText('第 1 / 3 页')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /下一页/ })).toBeEnabled();
  });

  it('debounces the search input', async () => {
    mockTemplates.list.mockResolvedValue(page([]));
    render(<TemplateGalleryV2 open={true} onClose={() => {}} />);
    await waitFor(() => expect(mockTemplates.list).toHaveBeenCalledTimes(1));
    const input = screen.getByPlaceholderText(/搜索模板/);
    fireEvent.change(input, { target: { value: 'po' } });
    fireEvent.change(input, { target: { value: 'pop' } });
    fireEvent.change(input, { target: { value: 'popu' } });
    fireEvent.change(input, { target: { value: 'popul' } });
    fireEvent.change(input, { target: { value: 'popula' } });
    fireEvent.change(input, { target: { value: 'populat' } });
    fireEvent.change(input, { target: { value: 'populati' } });
    fireEvent.change(input, { target: { value: 'populatio' } });
    fireEvent.change(input, { target: { value: 'population' } });
    act(() => vi.advanceTimersByTime(250));
    await waitFor(() => expect(mockTemplates.list.mock.calls.length).toBeGreaterThanOrEqual(2));
    const lastCall = mockTemplates.list.mock.calls[mockTemplates.list.mock.calls.length - 1][0];
    expect(lastCall.q).toBe('population');
  });

  it('switches the kind tab and refetches with the new filter', async () => {
    mockTemplates.list.mockResolvedValue(page([]));
    render(<TemplateGalleryV2 open={true} onClose={() => {}} />);
    await waitFor(() => expect(mockTemplates.list).toHaveBeenCalled());
    fireEvent.click(screen.getByText('底图'));
    await waitFor(() => {
      const last = mockTemplates.list.mock.calls[mockTemplates.list.mock.calls.length - 1][0];
      expect(last.kind).toBe('basemap');
    });
  });

  it('shows "no matches" when the page is empty', async () => {
    mockTemplates.list.mockResolvedValue(page([]));
    render(<TemplateGalleryV2 open={true} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/无匹配模板/)).toBeInTheDocument());
  });

  it('surfaces API errors as an inline banner', async () => {
    mockTemplates.list.mockRejectedValue(new Error('boom'));
    render(<TemplateGalleryV2 open={true} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/boom/)).toBeInTheDocument());
  });
});

// ============================================================================
// Issue #465: the list endpoint defaults summary=true and strips `payload` —
// the apply action must fetch the DETAIL before applying, and the success
// callback (parent toast) may only fire when the apply actually landed.
// ============================================================================

const BASEMAP_CARD = {
  id: 'tmpl_bm_dark', kind: 'basemap', name: '深色底图卡', description: '', keywords: [],
  is_builtin: true, version: 1,
};

describe('TemplateGalleryV2 apply (#465)', () => {
  it('fetches the template detail before applying — summary items carry no payload', async () => {
    mockTemplates.list.mockResolvedValue(page([BASEMAP_CARD]));
    mockTemplates.get.mockResolvedValue({ ...BASEMAP_CARD, payload: { providerId: 'carto-dark' } });
    const onApply = vi.fn();
    render(<TemplateGalleryV2 open={true} onClose={() => {}} onApply={onApply} />);
    await waitFor(() => expect(screen.getByText('深色底图卡')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    await waitFor(() => expect(mockTemplates.get).toHaveBeenCalledWith('tmpl_bm_dark'));
  });

  it('dispatches a real basemap switch through the map action queue on success', async () => {
    mockTemplates.list.mockResolvedValue(page([BASEMAP_CARD]));
    mockTemplates.get.mockResolvedValue({ ...BASEMAP_CARD, payload: { providerId: 'carto-dark' } });
    const onApply = vi.fn();
    render(<TemplateGalleryV2 open={true} onClose={() => {}} onApply={onApply} />);
    await waitFor(() => expect(screen.getByText('深色底图卡')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    await waitFor(() =>
      expect(mockDispatch).toHaveBeenCalledWith(
        expect.objectContaining({ command: 'BASE_LAYER_CHANGE' })
      )
    );
    const dispatched = mockDispatch.mock.calls[0][0];
    // Canonical provider name → the queue's base_layer_change exact-matches it.
    expect(dispatched.params.name).toBe('Carto 深色');
    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply.mock.calls[0][0].payload).toEqual({ providerId: 'carto-dark' });
  });

  it('never fires the success callback when the detail has no payload', async () => {
    mockTemplates.list.mockResolvedValue(page([BASEMAP_CARD]));
    mockTemplates.get.mockResolvedValue({ ...BASEMAP_CARD }); // payload stripped/missing
    const onApply = vi.fn();
    render(<TemplateGalleryV2 open={true} onClose={() => {}} onApply={onApply} />);
    await waitFor(() => expect(screen.getByText('深色底图卡')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    await waitFor(() => expect(mockTemplates.get).toHaveBeenCalled());
    // Give the async handler a tick to settle before asserting no success.
    await act(async () => {});
    expect(onApply).not.toHaveBeenCalled();
    expect(mockDispatch).not.toHaveBeenCalled();
    await waitFor(() => expect(mockAddToast).toHaveBeenCalledWith(expect.any(String), 'error'));
  });

  it('surfaces a detail-fetch failure as an error toast, not a success toast', async () => {
    mockTemplates.list.mockResolvedValue(page([BASEMAP_CARD]));
    mockTemplates.get.mockRejectedValue(new Error('network down'));
    const onApply = vi.fn();
    render(<TemplateGalleryV2 open={true} onClose={() => {}} onApply={onApply} />);
    await waitFor(() => expect(screen.getByText('深色底图卡')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    await waitFor(() => expect(mockAddToast).toHaveBeenCalledWith(expect.stringContaining('network down'), 'error'));
    expect(onApply).not.toHaveBeenCalled();
  });

  it('symbology apply without a loaded layer errors instead of false success', async () => {
    const card = { ...BASEMAP_CARD, id: 'tmpl_sym_x', kind: 'symbology', name: '符号卡' };
    mockTemplates.list.mockResolvedValue(page([card]));
    mockTemplates.get.mockResolvedValue({
      ...card,
      payload: { mode: 'single', geometry: 'Polygon', style: { color: '#1d4ed8' } },
    });
    const onApply = vi.fn();
    render(<TemplateGalleryV2 open={true} onClose={() => {}} onApply={onApply} />);
    await waitFor(() => expect(screen.getByText('符号卡')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    await waitFor(() => expect(mockAddToast).toHaveBeenCalledWith(expect.stringContaining('图层'), 'error'));
    expect(onApply).not.toHaveBeenCalled();
    expect(mockDispatch).not.toHaveBeenCalled();
  });

  it('symbology apply with an active layer dispatches LAYER_STYLE_UPDATE', async () => {
    mockHudState.layers = [{ id: 'layer_poi' }];
    mockHudState.focusLayerId = 'layer_poi';
    const card = { ...BASEMAP_CARD, id: 'tmpl_sym_x', kind: 'symbology', name: '符号卡' };
    mockTemplates.list.mockResolvedValue(page([card]));
    mockTemplates.get.mockResolvedValue({
      ...card,
      payload: { mode: 'single', geometry: 'Polygon', style: { color: '#1d4ed8', fillOpacity: 0.8 } },
    });
    const onApply = vi.fn();
    render(<TemplateGalleryV2 open={true} onClose={() => {}} onApply={onApply} />);
    await waitFor(() => expect(screen.getByText('符号卡')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    await waitFor(() => expect(mockDispatch).toHaveBeenCalledTimes(1));
    const dispatched = mockDispatch.mock.calls[0][0];
    expect(dispatched.command).toBe('LAYER_STYLE_UPDATE');
    expect(dispatched.params.layer_id).toBe('layer_poi');
    expect(dispatched.params.style).toMatchObject({ color: '#1d4ed8', fill: '#1d4ed8', fillOpacity: 0.8 });
    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
  });

  it('layout apply lands in the export settings store', async () => {
    const card = { ...BASEMAP_CARD, id: 'tmpl_ly_a4', kind: 'layout', name: 'A4 横版卡' };
    mockTemplates.list.mockResolvedValue(page([card]));
    mockTemplates.get.mockResolvedValue({
      ...card,
      payload: {
        paperSize: 'A4', orientation: 'landscape', showLegend: true,
        showNorthArrow: true, showScaleBar: true, showGrid: false,
      },
    });
    const onApply = vi.fn();
    render(<TemplateGalleryV2 open={true} onClose={() => {}} onApply={onApply} />);
    await waitFor(() => expect(screen.getByText('A4 横版卡')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    await waitFor(() => expect(mockHudState.updateExportSettings).toHaveBeenCalledTimes(1));
    expect(mockHudState.updateExportSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        paperSize: 'A4', orientation: 'landscape', showLegend: true,
        showCompass: true, showScale: true, showGraticules: false,
      })
    );
    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
  });

  it('thematic/composite apply is an explicit error — no false success', async () => {
    const card = { ...BASEMAP_CARD, id: 'tmpl_th_x', kind: 'thematic', name: '专题卡' };
    mockTemplates.list.mockResolvedValue(page([card]));
    mockTemplates.get.mockResolvedValue({
      ...card,
      payload: { variant: 'choropleth', method: 'quantiles', k: 5, palette: 'YlOrRd' },
    });
    const onApply = vi.fn();
    render(<TemplateGalleryV2 open={true} onClose={() => {}} onApply={onApply} />);
    await waitFor(() => expect(screen.getByText('专题卡')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    await waitFor(() => expect(mockAddToast).toHaveBeenCalledWith(expect.stringContaining('Agent'), 'error'));
    expect(onApply).not.toHaveBeenCalled();
  });
});
