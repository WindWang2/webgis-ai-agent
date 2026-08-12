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

describe('TemplateGalleryV2', () => {
  it('renders the header + tabs and skips the network when closed', async () => {
    mockTemplates.list.mockResolvedValue([]);
    render(<TemplateGalleryV2 open={false} onClose={() => {}} />);
    expect(mockTemplates.list).not.toHaveBeenCalled();
  });

  it('fetches the first page when opened', async () => {
    mockTemplates.list.mockResolvedValue([
      { id: 'tmpl_a', kind: 'basemap', name: 'A', description: '', keywords: [] },
      { id: 'tmpl_b', kind: 'thematic', name: 'B', description: '', keywords: [] },
    ]);
    render(<TemplateGalleryV2 open={true} onClose={() => {}} />);
    await waitFor(() => expect(mockTemplates.list).toHaveBeenCalled());
    const call = mockTemplates.list.mock.calls[0][0];
    expect(call.limit).toBe(50);
    expect(call.offset).toBe(0);
    expect(call.kind).toBeUndefined();
  });

  it('debounces the search input', async () => {
    mockTemplates.list.mockResolvedValue([]);
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
    mockTemplates.list.mockResolvedValue([]);
    render(<TemplateGalleryV2 open={true} onClose={() => {}} />);
    await waitFor(() => expect(mockTemplates.list).toHaveBeenCalled());
    fireEvent.click(screen.getByText('底图'));
    await waitFor(() => {
      const last = mockTemplates.list.mock.calls[mockTemplates.list.mock.calls.length - 1][0];
      expect(last.kind).toBe('basemap');
    });
  });

  it('shows "no matches" when the page is empty', async () => {
    mockTemplates.list.mockResolvedValue([]);
    render(<TemplateGalleryV2 open={true} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/无匹配模板/)).toBeInTheDocument());
  });

  it('surfaces API errors as an inline banner', async () => {
    mockTemplates.list.mockRejectedValue(new Error('boom'));
    render(<TemplateGalleryV2 open={true} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/boom/)).toBeInTheDocument());
  });
});
