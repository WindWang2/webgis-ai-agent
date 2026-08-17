import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { runExport, MapExporterEngine, type ExportDeps, type ExportRequest } from '@/lib/map-kit/exporter';

vi.mock('@/lib/api/config', () => ({
  API_BASE: 'http://localhost:8001',
}));

vi.mock('@/lib/utils/logger', () => ({
  devOnly: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

// ── Test helpers ────────────────────────────────────────────────────

function createMockCanvas() {
  const canvas = document.createElement('canvas');
  canvas.width = 800;
  canvas.height = 600;
  return canvas;
}

beforeEach(() => {
  vi.restoreAllMocks();
});

function createMockMap(overrides: Partial<Record<string, any>> = {}) {
  return {
    getPixelRatio: vi.fn(() => 1),
    setPixelRatio: vi.fn(),
    getCanvas: vi.fn(() => createMockCanvas()),
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
    map: createMockMap(mapOverrides) as any,
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

function mockFetchUpload(url = '/exports/map.png', filename = 'map.png') {
  const mockFetch = vi.fn((reqUrl: string) => {
    if (typeof reqUrl === 'string' && reqUrl.includes('/api/v1/export')) {
      return Promise.resolve({
        ok: true,
        json: async () => ({ url, filename }),
      } as any);
    }
    return Promise.resolve({
      ok: true,
      blob: async () => new Blob(['img-data'], { type: 'image/png' }),
    } as any);
  }) as Mock;
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
    const exportToPDFSpy = vi.spyOn(MapExporterEngine, 'exportToPDF').mockResolvedValue(
      new Blob(['pdf-data'], { type: 'application/pdf' })
    );

    const req: ExportRequest = {
      title: 'PDF Report',
      format: 'pdf',
      paperSize: 'A4',
      orientation: 'landscape',
      author: '作者',
    };
    const outcome = await runExport(deps, req);
    if (!outcome.ok) throw new Error(`[PDF TEST ERROR]: ${outcome.error}`);

    expect(outcome.ok).toBe(true);
    expect(outcome.format).toBe('pdf');

    expect(exportToPDFSpy).toHaveBeenCalledWith(
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
      map: mockMap as any,
      getHudState: () => hudState,
    };

    const outcome = await runExport(deps, { dpi: 192 });

    expect(outcome.ok).toBe(false);
    expect(outcome.error).toContain('Canvas tainted');
    // DPI should still be restored even on error
    expect(mockMap.setPixelRatio).toHaveBeenCalledWith(1);
  });
});

// #527: 高 DPI 导出路径的 map.once('idle') 等待必须有界 —— WebGL 上下文丢失
// 或画布隐藏时 idle 永不触发，无界等待会让 finally 里的 pixelRatio 恢复不可达
// （3.125x @300DPI → ~10x backing store 泄漏）且导出进程永久挂起。
describe('runExport — bounded idle wait (#527)', () => {
  it('idle 永不触发时在 deadline 内失败、恢复 pixelRatio 并给出如实文案', async () => {
    // once('idle') 永不回调（模拟 WebGL 上下文丢失后的挂起状态）
    const mockMap = createMockMap({
      once: vi.fn(() => {}),
    });
    const hudState = createMockHudState();
    const deps: ExportDeps = {
      map: mockMap as any,
      getHudState: () => hudState,
      // 测试注入：把 30s 默认截止压到 20ms，避免测试真等 30 秒
      idleTimeoutMs: 20,
    };

    const outcome = await runExport(deps, { dpi: 192 });

    // 失败的"真实性"：typed error 文案说明是 idle 等待超时，而不是泛化失败
    expect(outcome.ok).toBe(false);
    expect(outcome.error).toContain('idle');
    // pixelRatio 已在 finally 恢复（set 2 → restore 1）
    expect(mockMap.setPixelRatio).toHaveBeenCalledWith(2);
    expect(mockMap.setPixelRatio).toHaveBeenCalledWith(1);
    // 用户可见的失败消息同样如实（不是"排版合成失败"泛化文案）
    const msg = hudState.setPendingSystemMessage.mock.calls.map((c) => String(c[0])).join('\n');
    expect(msg).toContain('idle');
  });

  it('idle 在超时之后才触发：失败先行，pixelRatio 只恢复一次，无二次结算副作用', async () => {
    let idleCb: (() => void) | null = null;
    const mockMap = createMockMap({
      once: vi.fn((_event: string, cb: () => void) => {
        idleCb = cb;
      }),
    });
    const hudState = createMockHudState();
    const deps: ExportDeps = {
      map: mockMap as any,
      getHudState: () => hudState,
      idleTimeoutMs: 20,
    };

    const outcome = await runExport(deps, { dpi: 192 });
    expect(outcome.ok).toBe(false);
    expect(outcome.error).toContain('idle');

    // 超时后 idle 才姗姗来迟 → 已结算的 promise 忽略它，恢复次数不增加
    expect(idleCb).not.toBeNull();
    idleCb!();
    expect(mockMap.setPixelRatio).toHaveBeenCalledTimes(2); // 2 + restore 1，无第三次
    expect(mockMap.setPixelRatio).toHaveBeenLastCalledWith(1);
  });

  it('dpi=96（targetPixelRatio=1）时不设置也不等待，pixelRatio 原样', async () => {
    const mockMap = createMockMap({
      once: vi.fn(() => {}), // 若被调用会挂死 —— 好路径绝不能走到
    });
    mockFetchUpload('/exports/map.png', 'map.png');
    const deps = createDeps();
    (deps as any).map = mockMap;

    const outcome = await runExport(deps, { dpi: 96, format: 'png' });

    expect(outcome.ok).toBe(true);
    expect(mockMap.once).not.toHaveBeenCalled();
    expect(mockMap.setPixelRatio).not.toHaveBeenCalled();
  });
});

// #614：export_map 参数契约 —— dark_mode 请求参数必须优先于 HUD 主题，
// 浅色 HUD + 默认参数（dark_mode=True）也要产出暗色成品。
// runExport 经 MapExporterEngine.composeLayout 调合成器（与 exportToPDF 同款
// 路由），spyOn 静态方法即可断言收到的 theme 选项。
describe('runExport — dark_mode 桥接 (#614)', () => {
  function spyCompose() {
    return vi.spyOn(MapExporterEngine, 'composeLayout').mockImplementation(() => {});
  }

  it('浅色 HUD + dark_mode=true → composeLayout 收到 theme=dark', async () => {
    const deps = createDeps({}, { theme: 'light' });
    mockFetchSuccess();
    const compose = spyCompose();

    const outcome = await runExport(deps, { format: 'png', dark_mode: true });

    expect(outcome.ok).toBe(true);
    expect(compose).toHaveBeenCalledWith(
      expect.anything(),
      expect.anything(),
      expect.anything(),
      expect.objectContaining({ theme: 'dark' }),
    );
  });

  it('浅色 HUD + 未传 dark_mode → 跟随 HUD，theme=light', async () => {
    const deps = createDeps({}, { theme: 'light' });
    mockFetchSuccess();
    const compose = spyCompose();

    await runExport(deps, { format: 'png' });

    expect(compose).toHaveBeenCalledWith(
      expect.anything(),
      expect.anything(),
      expect.anything(),
      expect.objectContaining({ theme: 'light' }),
    );
  });

  it('暗色 HUD + dark_mode=false → 显式覆盖为 theme=light', async () => {
    const deps = createDeps({}, { theme: 'dark' });
    mockFetchSuccess();
    const compose = spyCompose();

    await runExport(deps, { format: 'png', dark_mode: false });

    expect(compose).toHaveBeenCalledWith(
      expect.anything(),
      expect.anything(),
      expect.anything(),
      expect.objectContaining({ theme: 'light' }),
    );
  });

  it('暗色 HUD + 未传 dark_mode → 跟随 HUD，theme=dark', async () => {
    const deps = createDeps({}, { theme: 'dark' });
    mockFetchSuccess();
    const compose = spyCompose();

    await runExport(deps, { format: 'png' });

    expect(compose).toHaveBeenCalledWith(
      expect.anything(),
      expect.anything(),
      expect.anything(),
      expect.objectContaining({ theme: 'dark' }),
    );
  });

  it('PDF 导出 paperSize=A3 直达 exportToPDF，不被折叠为 A4', async () => {
    const deps = createDeps();
    mockFetchUpload('/exports/map.pdf', 'map.pdf');
    const exportToPDFSpy = vi
      .spyOn(MapExporterEngine, 'exportToPDF')
      .mockResolvedValue(new Blob(['pdf-data'], { type: 'application/pdf' }));

    const outcome = await runExport(deps, { title: 'A3', format: 'pdf', paperSize: 'A3' });
    if (!outcome.ok) throw new Error(`[PDF TEST ERROR]: ${outcome.error}`);

    expect(outcome.ok).toBe(true);
    expect(exportToPDFSpy).toHaveBeenCalledWith(
      expect.anything(),
      'A3',
      undefined,
      expect.objectContaining({ paperSize: 'A3' }),
    );
  });
});
