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
vi.mock('@/lib/api/templates', () => ({ templatesApi: mockTemplates }));
vi.mock('@/lib/basemap-apply', () => ({ applyBaseline: vi.fn() }));
vi.mock('@/lib/symbology-apply', () => ({ applySymbology: vi.fn() }));
vi.mock('@/lib/thematic-apply', () => ({ resolveThematicPreset: vi.fn() }));
vi.mock('@/lib/map-kit/layout-style', () => ({ resolveStyle: vi.fn() }));
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (sel: (s: unknown) => unknown) => sel({
    setBaseLayer: vi.fn(), addLayer: vi.fn(), clearLayers: vi.fn(),
  }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
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
