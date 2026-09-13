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
      // ADR-0081：PDF subtitle 与 canvas 同链（请求参数 > spec 组件 > 空串）
      '',
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

// #527 / ADR-0157 P1：高 DPI 导出路径的 map.once('idle') 等待必须有界（WebGL
// 上下文丢失/画布隐藏时 idle 永不触发），且超时**不再是整体失败** —— §0.5
// 降级契约：恢复原始 pixelRatio、短界等一次重绘后以当前分辨率画布导出
//（degraded-native + highdpi_rerender_timeout_degraded 诊断）；只有降级回退
// 后的重绘也超时（无画面可捕获）才类型化失败（"未完成重绘"，无 'idle' 字样）。
describe('runExport — bounded idle wait + degrade contract (#527 / ADR-0157 P1)', () => {
  it('idle 在重渲染截止内不触发 → 降级 degraded-native 成功（诊断码入消息 + pixelRatio 恢复）', async () => {
    // 第 1 次 once（高 DPI 重渲染等待）永不回调；第 2 次 once（降级回退后的
    // 重绘等待）立即回调 —— 与 highdpi.test.ts 的 idleScript ['never','fire'] 同脚本。
    let idleRegistrations = 0;
    const mockMap = createMockMap({
      once: vi.fn((_event: string, cb: () => void) => {
        idleRegistrations += 1;
        if (idleRegistrations >= 2) cb();
      }),
    });
    const hudState = createMockHudState();
    const deps: ExportDeps = {
      map: mockMap as any,
      getHudState: () => hudState,
      // 测试注入：把 30s 默认截止压到 20ms，避免测试真等 30 秒
      idleTimeoutMs: 20,
    };
    mockFetchSuccess('/exports/map.png', 'map.png');

    const outcome = await runExport(deps, { dpi: 192, format: 'png' });

    // 降级语义：导出成功（不再整体失败），消息带降级说明 + 诊断码。
    expect(outcome.ok).toBe(true);
    expect(outcome.format).toBe('png');
    const msg = hudState.setPendingSystemMessage.mock.calls.map((c) => String(c[0])).join('\n');
    expect(msg).toContain('高 DPI 重渲染超时，已降级为当前分辨率');
    expect(msg).toContain('highdpi_rerender_timeout_degraded');
    // pixelRatio：升到 2 → 超时降级在引擎内恢复 1 → finally 兜底再恢复 1（幂等）。
    expect(mockMap.setPixelRatio).toHaveBeenCalledWith(2);
    expect(mockMap.setPixelRatio).toHaveBeenCalledWith(1);
    expect(mockMap.setPixelRatio).toHaveBeenCalledTimes(3);
    expect(mockMap.setPixelRatio).toHaveBeenLastCalledWith(1);
  });

  it('降级回退后的重绘 idle 也不触发 → 类型化失败先行（"未完成重绘"），pixelRatio 保持恢复态且迟到 idle 无二次结算', async () => {
    const idleCbs: Array<() => void> = [];
    const mockMap = createMockMap({
      once: vi.fn((_event: string, cb: () => void) => {
        idleCbs.push(cb); // 永不自动触发 —— 重渲染与降级重绘两段都靠 deadline 超时
      }),
    });
    const hudState = createMockHudState();
    const deps: ExportDeps = {
      map: mockMap as any,
      getHudState: () => hudState,
      idleTimeoutMs: 20,
    };

    vi.useFakeTimers();
    try {
      const promise = runExport(deps, { dpi: 192 });
      await vi.advanceTimersByTimeAsync(20); // 重渲染 idle 截止（deps.idleTimeoutMs）
      await vi.advanceTimersByTimeAsync(3_000); // 降级回退重绘截止（DEGRADE_REPAINT_TIMEOUT_MS）
      const outcome = await promise;

      // 失败的"真实性"：新契约的类型化文案 —— 降级回退后无法捕获画面，
      // 不再是旧契约的 idle 超时失败（旧断言 error 包含 'idle' 已失效）。
      expect(outcome.ok).toBe(false);
      expect(outcome.error).toContain('高 DPI 超时降级回退后');
      expect(outcome.error).toContain('未完成重绘');
      expect(outcome.error).not.toContain('idle');
      const msg = hudState.setPendingSystemMessage.mock.calls.map((c) => String(c[0])).join('\n');
      expect(msg).toContain('未完成重绘');
      // pixelRatio：升 2 → 引擎降级路径恢复 1 → finally 兜底 1（保持恢复态）。
      expect(mockMap.setPixelRatio).toHaveBeenCalledTimes(3);
      expect(mockMap.setPixelRatio).toHaveBeenLastCalledWith(1);

      // 迟到的 idle 姗姗来迟 → 已结算的 promise 忽略它，恢复次数不增加。
      for (const cb of idleCbs) cb();
      await Promise.resolve();
      expect(mockMap.setPixelRatio).toHaveBeenCalledTimes(3);
      expect(mockMap.setPixelRatio).toHaveBeenLastCalledWith(1);
    } finally {
      vi.useRealTimers();
    }
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
      '',
      expect.objectContaining({ paperSize: 'A3' }),
    );
  });
});

describe('runExport — spec 组件事实源（v3 Phase H）', () => {
  function spyCompose() {
    return vi.spyOn(MapExporterEngine, 'composeLayout').mockImplementation(() => {});
  }

  async function commitSpecWithComponents(components: any[]) {
    const cursor = await import('@/lib/mapspec/session-cursor');
    cursor.setMapSpecSessionCursor(`spec-sub-${Math.random().toString(36).slice(2, 8)}`);
    cursor.commitMapSpecDocument({
      id: 'spec-sub',
      version: '1.0',
      layers: [],
      layout: { components },
      view: {},
    } as any);
  }

  it('subtitle 组件文本进入导出成品（与 title 同链：请求参数 > spec > 空串）', async () => {
    await commitSpecWithComponents([
      { id: 'c-title', type: 'title', slot: 'title', enabled: true, options: { text: '成都小学分布' } },
      { id: 'c-sub', type: 'subtitle', slot: 'subtitle', enabled: true, options: { text: '2026 年秋学期' } },
    ]);
    const deps = createDeps();
    mockFetchSuccess();
    const compose = spyCompose();

    const outcome = await runExport(deps, { format: 'png' });
    expect(outcome.ok).toBe(true);
    expect(compose).toHaveBeenCalledWith(
      expect.anything(),
      '成都小学分布',
      '2026 年秋学期',
      expect.anything(),
    );
  });

  it('显式 subtitle 请求参数优先于 spec 组件文本', async () => {
    await commitSpecWithComponents([
      { id: 'c-sub', type: 'subtitle', slot: 'subtitle', enabled: true, options: { text: 'spec 副标题' } },
    ]);
    const deps = createDeps();
    mockFetchSuccess();
    const compose = spyCompose();

    await runExport(deps, { format: 'png', title: 'T', subtitle: 'req 副标题' });
    expect(compose).toHaveBeenCalledWith(
      expect.anything(),
      'T',
      'req 副标题',
      expect.anything(),
    );
  });

  it('禁用的 subtitle 组件不生效（enabled=false）', async () => {
    await commitSpecWithComponents([
      { id: 'c-sub', type: 'subtitle', slot: 'subtitle', enabled: false, options: { text: '不应出现' } },
    ]);
    const deps = createDeps();
    mockFetchSuccess();
    const compose = spyCompose();

    await runExport(deps, { format: 'png', title: 'T' });
    expect(compose).toHaveBeenCalledWith(
      expect.anything(),
      'T',
      '',
      expect.anything(),
    );
  });
});

describe('Scenario G — live vs PNG vs PDF vs SVG 关键组件语义一致（ADR-0081/0084）', () => {
  it('三种格式共用同一 chrome 模型输入（组件语义与格式无关；经真实 spec 提交 seam）', async () => {
    // 终审修复：此前空 deps → getCommittedMapSpec()=null → 三个空模型
    // 互相相等（恒真）。现在经真实 seam 提交带组件的 spec。
    const { commitMapSpecDocument, resetLiveState } = await import(
      '@/lib/mapspec/session-cursor'
    );
    const spec = {
      layers: [
        {
          id: 'ly', visible: true, type: 'fill',
          legend_spec: { type: 'continuous', min: 0, max: 10, palette_colors: ['#ffffb2', '#f03b20'] },
        },
      ],
      sources: {},
      layout: {
        components: [
          { id: 'title', type: 'title', enabled: true, options: { text: '成都小学分布' } },
          { id: 'scale-bar', type: 'scale_bar', enabled: true },
          {
            id: 'colorbar-main', type: 'continuous_colorbar', enabled: true,
            options: { layerId: 'ly' },
          },
        ],
      },
    };
    expect(commitMapSpecDocument(spec, 1)).toBe(true);
    const composeSpy = vi
      .spyOn(MapExporterEngine, 'composeLayout')
      .mockImplementation((async () => createMockCanvas()) as any);
    vi.spyOn(MapExporterEngine, 'exportToPDF').mockResolvedValue(
      new Blob(['pdf'], { type: 'application/pdf' }),
    );
    const chromes: unknown[] = [];
    composeSpy.mockImplementation((((
      _canvas: unknown,
      _title: unknown,
      _subtitle: unknown,
      options: Record<string, any>,
    ) => {
      chromes.push(options.chrome);
      return Promise.resolve(createMockCanvas());
    }) as unknown) as any);

    try {
      for (const format of ['png', 'svg', 'pdf'] as const) {
        const deps = createDeps();
        mockFetchUpload(`/exports/map.${format}`, `map.${format}`);
        const outcome = await runExport(deps, { title: '成都小学分布', format });
        expect(outcome.ok).toBe(true);
      }
      expect(chromes.length).toBe(3);
      // 非空：模型确实携带 spec 组件（title/scale/colorbar）—— 空模型恒等
      // 的恒真断言已不可能
      const first = chromes[0] as Record<string, any>;
      expect(first?.fromSpec).toBe(true);
      expect(first?.title?.text).toBe('成都小学分布');
      expect(first?.scaleBar).toBeDefined();
      expect(first?.colorbar).toBeDefined();
      // 三格式 chrome 模型完全一致（共享 resolver + 布局求解器在模型构建
      // 层保证 parity —— 格式只影响画布封装）
      expect(chromes[0]).toEqual(chromes[1]);
      expect(chromes[1]).toEqual(chromes[2]);
    } finally {
      resetLiveState();
    }
  });
});
