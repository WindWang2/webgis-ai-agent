import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { runExport, type ExportDeps, type ExportRequest } from './index';

// ── Mock map-kit/exporter ───────────────────────────────────────────

vi.mock('@/lib/map-kit/exporter', () => ({
  prepareExportCanvas: vi.fn((_canvas: any) => ({
    canvas: {
      width: 800,
      height: 600,
      getContext: () => ({
        drawImage: vi.fn(),
        fillText: vi.fn(),
        fillRect: vi.fn(),
        createLinearGradient: () => ({ addColorStop: vi.fn() }),
        beginPath: vi.fn(),
        moveTo: vi.fn(),
        lineTo: vi.fn(),
        closePath: vi.fn(),
        fill: vi.fn(),
        stroke: vi.fn(),
        strokeRect: vi.fn(),
        arc: vi.fn(),
        arcTo: vi.fn(),
        save: vi.fn(),
        restore: vi.fn(),
        translate: vi.fn(),
        rotate: vi.fn(),
        setLineDash: vi.fn(),
        measureText: () => ({ width: 50 }),
        set fillStyle(_: any) {},
        set strokeStyle(_: any) {},
        set lineWidth(_: any) {},
        set font(_: any) {},
        set textAlign(_: any) {},
        set shadowColor(_: any) {},
        set shadowBlur(_: any) {},
      }),
      toDataURL: () => 'data:image/png;base64,AAAA',
    },
    scaleX: 1,
    scaleY: 1,
    srcX: 0,
    srcY: 0,
    srcW: 800,
    srcH: 600,
  })),
  composeLayout: vi.fn(),
  exportToPDF: vi.fn(async () => new Blob(['pdf-data'], { type: 'application/pdf' })),
}));

vi.mock('@/lib/api/config', () => ({
  API_BASE: 'http://localhost:8001',
}));

vi.mock('@/lib/utils/logger', () => ({
  devOnly: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

// ── Test helpers ────────────────────────────────────────────────────

function createMockMap(overrides: Partial<Record<string, any>> = {}) {
  return {
    getPixelRatio: vi.fn(() => 1),
    setPixelRatio: vi.fn(),
    getCanvas: vi.fn(() => ({
      width: 800,
      height: 600,
      getContext: () => null,
      toDataURL: () => 'data:image/png;base64,AAAA',
    })),
    getCenter: vi.fn(() => ({ lat: 39.9, lng: 116.4 })),
    getZoom: vi.fn(() => 10),
    getBearing: vi.fn(() => 0),
    once: vi.fn((_event: string, cb: () => void) => cb()),
    ...overrides,
  };
}

function createMockHudState(overrides: Partial<Record<string, any>> = {}) {
  return {
    theme: 'light' as const,
    layers: [],
    addExport: vi.fn(),
    setPendingSystemMessage: vi.fn(),
    ...overrides,
  };
}

function createDeps(
  mapOverrides: Partial<Record<string, any>> = {},
  hudOverrides: Partial<Record<string, any>> = {},
): ExportDeps {
  const hudState = createMockHudState(hudOverrides);
  return {
    map: createMockMap(mapOverrides),
    getHudState: () => hudState,
  };
}

function mockFetchSuccess(url = '/exports/test.png', filename = 'test.png') {
  const mockFetch = vi.fn() as Mock;
  // First call: dataUrl fetch for PNG → blob
  mockFetch.mockResolvedValueOnce({
    ok: true,
    blob: async () => new Blob(['img-data'], { type: 'image/png' }),
  });
  // Second call: upload POST → { url, filename }
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ url, filename }),
  });
  vi.stubGlobal('fetch', mockFetch);
  return mockFetch;
}

function mockFetchUpload(url = '/exports/test.png', filename = 'test.png') {
  const mockFetch = vi.fn() as Mock;
  // Single call: upload POST → { url, filename }
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ url, filename }),
  });
  vi.stubGlobal('fetch', mockFetch);
  return mockFetch;
}

beforeEach(() => {
  vi.restoreAllMocks();
});

// ── Tests ───────────────────────────────────────────────────────────

describe('runExport', () => {
  it('PNG export happy path', async () => {
    const deps = createDeps();
    mockFetchSuccess('/exports/map.png', 'map.png');

    const req: ExportRequest = { title: '测试地图', format: 'png' };
    const outcome = await runExport(deps, req);

    expect(outcome.ok).toBe(true);
    expect(outcome.format).toBe('png');
    expect(outcome.url).toBe('/exports/map.png');
    expect(outcome.filename).toBe('map.png');

    // addExport should have been called with type 'png'
    const hudState = deps.getHudState();
    expect(hudState.addExport).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'png', name: '测试地图' }),
    );
    // System message should have been set (loading + result)
    expect(hudState.setPendingSystemMessage).toHaveBeenCalledTimes(2);
  });

  it('SVG export wraps PNG in SVG container', async () => {
    const deps = createDeps();
    mockFetchUpload('/exports/map.svg', 'map.svg');

    const req: ExportRequest = { title: 'SVG Map', format: 'svg' };
    const outcome = await runExport(deps, req);

    expect(outcome.ok).toBe(true);
    expect(outcome.format).toBe('svg');
    expect(outcome.url).toBe('/exports/map.svg');

    const hudState = deps.getHudState();
    expect(hudState.addExport).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'svg' }),
    );
  });

  it('PDF export uses jsPDF', async () => {
    const deps = createDeps();
    mockFetchUpload('/exports/map.pdf', 'map.pdf');

    const req: ExportRequest = {
      title: 'PDF Report',
      format: 'pdf',
      paperSize: 'A4',
      orientation: 'landscape',
      author: '作者',
    };
    const outcome = await runExport(deps, req);

    expect(outcome.ok).toBe(true);
    expect(outcome.format).toBe('pdf');

    const { exportToPDF } = await import('@/lib/map-kit/exporter');
    expect(exportToPDF).toHaveBeenCalledWith(
      expect.anything(),       // canvas
      'PDF Report',            // title
      undefined,               // subtitle
      expect.objectContaining({ paperSize: 'A4', orientation: 'landscape', author: '作者' }),
    );
  });

  it('high DPI sets and restores pixel ratio', async () => {
    const mockMap = createMockMap();
    const deps = createDeps();
    // Override the map to track pixel ratio calls
    (deps as any).map = mockMap;
    mockFetchSuccess();

    const req: ExportRequest = { dpi: 192 }; // 2x
    await runExport(deps, req);

    expect(mockMap.setPixelRatio).toHaveBeenCalledWith(2); // 192/96
    // Should restore original
    expect(mockMap.setPixelRatio).toHaveBeenCalledWith(1);
    // Total: set to 2, then restore to 1
    expect(mockMap.setPixelRatio).toHaveBeenCalledTimes(2);
  });

  it('upload failure returns ok=false with error message', async () => {
    const deps = createDeps();
    const mockFetch = vi.fn() as Mock;
    // dataUrl fetch
    mockFetch.mockResolvedValueOnce({
      ok: true,
      blob: async () => new Blob(['img-data']),
    });
    // upload POST → fail
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
    });
    vi.stubGlobal('fetch', mockFetch);

    const outcome = await runExport(deps, { format: 'png' });

    expect(outcome.ok).toBe(false);
    expect(outcome.format).toBe('png');
    expect(outcome.error).toContain('500');
  });

  it('canvas extraction failure restores DPI', async () => {
    const mockMap = createMockMap({
      getCanvas: vi.fn(() => {
        throw new Error('Canvas tainted');
      }),
    });
    const hudState = createMockHudState();
    const deps: ExportDeps = {
      map: mockMap,
      getHudState: () => hudState,
    };

    const outcome = await runExport(deps, { dpi: 192 });

    expect(outcome.ok).toBe(false);
    expect(outcome.error).toContain('Canvas tainted');
    // DPI should still be restored even on error
    expect(mockMap.setPixelRatio).toHaveBeenCalledWith(1);
  });
});
