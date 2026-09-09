/**
 * W9（ADR-0118）：多帧导出运行时（atlas pages + small-multiple grid）。
 *
 * 契约：
 * 1. 逐帧确定性执行：where → setFilter(["==",["get",field],equal])（含
 *    `-label` 子层同步）→ 有界 idle 等待 → 抓 canvas → 恢复 filter；
 * 2. extent → fitBounds；结束时相机恢复原状（jumpTo 一次）；
 * 3. 上限 50 帧（超出截断 + atlas_page_limit_truncated）；单帧失败跳过 +
 *    atlas_page_skipped（detail=帧序号/标题）继续；projection='cartogram'
 *    → cartogram_unsupported（warning）+ 按未变形几何渲染；
 * 4. grid 组合几何：行优先、含每帧小标题。
 */
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { composeFrames, composeGridCanvas, type ExportFrame } from './frame-composer';
import { runExport, MapExporterEngine, type ExportDeps, type ExportRequest } from './exporter';
import type { Map } from 'maplibre-gl';

vi.mock('@/lib/api/config', () => ({
  API_BASE: 'http://localhost:8001',
}));

vi.mock('@/lib/utils/logger', () => ({
  devOnly: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

interface MockMapOptions {
  failIdleOnFrame?: number;
  canvasW?: number;
  canvasH?: number;
}

function makeMockMap(opts: MockMapOptions = {}) {
  // 注意：此处不能用 `new Map(...)` —— 测试环境全局 Map 类型被 maplibre
  // 的 Map 类遮蔽（TS2339），用 plain record 承载 filter 断言状态。
  const filters: Record<string, unknown> = {};
  let idleWaits = 0;
  const map = {
    getFilter: vi.fn((id: string) => filters[id]),
    setFilter: vi.fn((id: string, f: unknown) => {
      filters[id] = f;
    }),
    getLayer: vi.fn((id: string) => id === 'lyr' || id === 'lyr-label'),
    fitBounds: vi.fn(),
    getCenter: vi.fn(() => ({ lng: 104, lat: 30 })),
    getZoom: vi.fn(() => 8),
    getBearing: vi.fn(() => 0),
    getPitch: vi.fn(() => 0),
    jumpTo: vi.fn(),
    getCanvas: vi.fn(() => {
      const c = document.createElement('canvas');
      c.width = opts.canvasW ?? 100;
      c.height = opts.canvasH ?? 80;
      return c;
    }),
    once: vi.fn((_e: string, cb: () => void) => {
      idleWaits += 1;
      if (opts.failIdleOnFrame != null && idleWaits === opts.failIdleOnFrame) {
        return; // idle 永不触发 → waitForIdle 超时（注入的 waiter 模拟）
      }
      cb();
    }),
  };
  return map as unknown as Map & Record<string, ReturnType<typeof vi.fn>>;
}

const idle = () => Promise.resolve();

describe('composeFrames', () => {
  it('帧顺序确定性：逐帧 setFilter（含 -label 子层）→ 抓帧 → filter 恢复', async () => {
    const map = makeMockMap();
    const frames: ExportFrame[] = [
      { title: '甲', where: { layerId: 'lyr', field: '区', equal: '锦江' } },
      { title: '乙', where: { layerId: 'lyr', field: '区', equal: '青羊' } },
    ];
    const { canvases, titles, degradations } = await composeFrames(
      { map, waitForIdle: idle },
      frames,
    );

    expect(canvases).toHaveLength(2);
    expect(titles).toEqual(['甲', '乙']);
    expect(degradations).toEqual([]);
    // where 表达式：["==", ["get", field], equal]，label 子层同步
    expect(map.setFilter).toHaveBeenCalledWith('lyr', ['==', ['get', '区'], '锦江']);
    expect(map.setFilter).toHaveBeenCalledWith('lyr-label', ['==', ['get', '区'], '锦江']);
    // 每帧结束 filter 恢复（初始无 filter → null），含 label 子层
    const restores = (map.setFilter as ReturnType<typeof vi.fn>).mock.calls.filter(
      (c) => c[1] === null,
    );
    expect(restores).toHaveLength(4); // 2 帧 × (lyr + lyr-label)
  });

  it('extent → fitBounds；结束时相机恢复原状（jumpTo）', async () => {
    const map = makeMockMap();
    await composeFrames({ map, waitForIdle: idle }, [
      { title: '范围帧', extent: [103.9, 30.6, 104.2, 30.8] },
    ]);

    expect(map.fitBounds).toHaveBeenCalledTimes(1);
    const boundsArg = (map.fitBounds as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(boundsArg).toEqual([[103.9, 30.6], [104.2, 30.8]]);
    // 相机恢复
    expect(map.jumpTo).toHaveBeenCalledWith(
      expect.objectContaining({ center: { lng: 104, lat: 30 }, zoom: 8, bearing: 0, pitch: 0 }),
    );
  });

  it('上限 50 帧：超出截断 + atlas_page_limit_truncated', async () => {
    const map = makeMockMap();
    const frames = Array.from({ length: 55 }, (_, i) => ({ title: `f${i}` }));
    const { canvases, degradations } = await composeFrames({ map, waitForIdle: idle }, frames);

    expect(canvases).toHaveLength(50);
    expect(degradations).toContainEqual(
      expect.objectContaining({ code: 'atlas_page_limit_truncated', detail: '55→50' }),
    );
  });

  it('单帧失败跳过 + atlas_page_skipped（detail=帧序号/标题）继续', async () => {
    const map = makeMockMap({ failIdleOnFrame: 2 });
    const frames: ExportFrame[] = [
      { title: '甲' },
      { title: '乙' },
      { title: '丙' },
    ];
    // 让 waitForIdle 在第 2 帧超时（map.once 不触发 → 注入 30ms 截止模拟）
    const { canvases, degradations } = await composeFrames(
      { map, waitForIdle: (m, t) => new Promise<void>((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('idle timeout')), t ?? 30);
          (m as unknown as { once: (e: string, cb: () => void) => void }).once('idle', () => {
            clearTimeout(timer);
            resolve();
          });
        }), idleTimeoutMs: 30 },
      frames,
    );

    expect(canvases).toHaveLength(2); // 甲、丙
    expect(degradations).toContainEqual(
      expect.objectContaining({ code: 'atlas_page_skipped' }),
    );
    const skip = degradations.find((d) => d.code === 'atlas_page_skipped')!;
    expect(skip.detail).toContain('2');
    expect(skip.detail).toContain('乙');
  });

  it("frames 带 projection:'cartogram' → cartogram_unsupported + 未变形渲染（不伪造）", async () => {
    const map = makeMockMap();
    const { canvases, degradations } = await composeFrames({ map, waitForIdle: idle }, [
      { title: '变形帧', projection: 'cartogram' as unknown as undefined },
    ]);

    expect(canvases).toHaveLength(1); // 按未变形几何照常渲染
    expect(degradations).toContainEqual(
      expect.objectContaining({ code: 'cartogram_unsupported' }),
    );
  });

  it('review-r2 BLOCKER 回归：getCanvas 返回同一 live canvas 时逐帧快照必须各自独立拷贝', async () => {
    // 真实 MapLibre `getCanvas()` 恒返回同一 live canvas 实例（
    // `getCanvas(){return this.canvas}`）。若 composeFrames 直接持引用，
    // 所有帧都指向同一画布 —— 导出产物全部是最后一帧内容（静默错帧）。
    // 快照契约：每帧 push 独立拷贝（与 live 实例不同、帧间互不相同、尺寸一致）。
    const live = Object.assign(document.createElement('canvas'), { width: 100, height: 80 });
    const map = makeMockMap();
    (map.getCanvas as Mock).mockImplementation(() => live);

    const { canvases } = await composeFrames({ map, waitForIdle: idle }, [
      { title: '甲' },
      { title: '乙' },
      { title: '丙' },
    ]);

    expect(canvases).toHaveLength(3);
    for (const snap of canvases) {
      expect(snap).not.toBe(live); // 不持 live 引用 —— 下一帧渲染不覆盖已抓帧
      expect(snap.width).toBe(100);
      expect(snap.height).toBe(80);
    }
    expect(new Set(canvases).size).toBe(3); // 帧间互为独立拷贝
  });

  it('review-r1 死码修复：grid 容器（small-multiple）单帧失败发 small_multiple_panel_skipped', async () => {
    const map = makeMockMap({ failIdleOnFrame: 1 });
    const { canvases, degradations } = await composeFrames(
      { map, waitForIdle: (m, t) => new Promise<void>((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('idle timeout')), t ?? 30);
          (m as unknown as { once: (e: string, cb: () => void) => void }).once('idle', () => {
            clearTimeout(timer);
            resolve();
          });
        }), idleTimeoutMs: 30 },
      [{ title: '甲' }],
      { skippedCode: 'small_multiple_panel_skipped' },
    );

    expect(canvases).toHaveLength(0);
    expect(degradations).toContainEqual(
      expect.objectContaining({ code: 'small_multiple_panel_skipped' }),
    );
    expect(degradations.some((d) => d.code === 'atlas_page_skipped')).toBe(false);
  });
});

describe('composeGridCanvas', () => {
  it('grid 几何：行优先网格 + 每帧小标题（MARGIN/GAP/CAPTION 常量口径）', () => {
    const canvases = [
      Object.assign(document.createElement('canvas'), { width: 100, height: 80 }),
      Object.assign(document.createElement('canvas'), { width: 100, height: 80 }),
      Object.assign(document.createElement('canvas'), { width: 100, height: 80 }),
    ];
    const grid = composeGridCanvas(canvases, ['甲', '乙', '丙']);

    // 3 帧 → cols=2、rows=2；W = 2*MARGIN + 2*100 + 1*GAP；H = 2*MARGIN + 2*(80+CAPTION) + 1*GAP
    expect(grid.width).toBe(24 + 200 + 12);
    expect(grid.height).toBe(24 + 2 * 108 + 12);
  });

  it('review-r2：拼板画布超浏览器尺寸上限 → 诚实抛错（不静默产出空白拼板）', () => {
    // 50 帧 × 4000×4000 → cols=8，width ≈ 32k px > 16384 —— 此前会静默
    // 创建超限画布并上传空白拼板（伪成功），现在必须抛可读错误。
    const canvases = Array.from({ length: 50 }, () =>
      Object.assign(document.createElement('canvas'), { width: 4000, height: 4000 }),
    );
    expect(() => composeGridCanvas(canvases, canvases.map((_, i) => `f${i}`))).toThrow(
      /超出浏览器上限/,
    );
  });
});

// ── runExport 集成：frames 非空 → frame-composer 分支 ────────────────────

function makeRunExportDeps(mapMock: unknown): ExportDeps {
  const hudState = {
    theme: 'light' as const,
    layers: [],
    addExport: vi.fn(),
    setPendingSystemMessage: vi.fn(),
  };
  return {
    // runExport 外层（pixelRatio 管理）与 frame-composer（setFilter 等）
    // 各取所需 —— 合并到同一 mock 上。
    map: {
      getPixelRatio: vi.fn(() => 1),
      setPixelRatio: vi.fn(),
      ...(mapMock as Record<string, unknown>),
    } as any,
    getHudState: () => hudState,
  };
}

function stubUpload() {
  const mockFetch = vi.fn((reqUrl: string) => {
    if (typeof reqUrl === 'string' && reqUrl.includes('/api/v1/export')) {
      return Promise.resolve({ ok: true, json: async () => ({ url: '/exports/atlas.png', filename: 'atlas.png' }) } as any);
    }
    return Promise.resolve({ ok: true, blob: async () => new Blob(['img'], { type: 'image/png' }) } as any);
  }) as Mock;
  vi.stubGlobal('fetch', mockFetch);
}

describe('runExport frames branch（W9）', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('pdf + frames → 多页图集（exportToPDF pages 通道）+ 系统消息披露', async () => {
    const map = makeMockMap();
    const deps = makeRunExportDeps(map);
    stubUpload();
    const pdfSpy = vi
      .spyOn(MapExporterEngine, 'exportToPDF')
      .mockResolvedValue(new Blob(['pdf'], { type: 'application/pdf' }));

    const req: ExportRequest = {
      title: '区县图集',
      format: 'pdf',
      frames: [
        { title: '锦江区', where: { layerId: 'lyr', field: '区', equal: '锦江' } },
        { title: '青羊区', where: { layerId: 'lyr', field: '区', equal: '青羊' } },
      ],
    };
    const outcome = await runExport(deps, req);

    expect(outcome.ok).toBe(true);
    expect(outcome.format).toBe('pdf');
    // pages = 全部帧（首页为封面·嵌入第 1 帧）
    const pdfArgs = pdfSpy.mock.calls[0];
    expect((pdfArgs[3] as { pages?: unknown[] }).pages).toHaveLength(2);
    const finalMsg = (deps.getHudState().setPendingSystemMessage as Mock).mock.calls.at(-1)![0] as string;
    expect(finalMsg).toContain('图集');
    expect(finalMsg).toContain('2 帧');
  });

  it('review-r2：部分帧跳过时成功消息如实注明「成功 X/Y 帧」', async () => {
    const map = makeMockMap({ failIdleOnFrame: 2 }); // 第 2 帧超时 → 跳过
    const deps = makeRunExportDeps(map);
    deps.idleTimeoutMs = 20;
    stubUpload();
    vi.spyOn(MapExporterEngine, 'exportToPDF').mockResolvedValue(
      new Blob(['pdf'], { type: 'application/pdf' }),
    );

    const req: ExportRequest = {
      title: '跳帧图集',
      format: 'pdf',
      frames: [{ title: '甲' }, { title: '乙' }, { title: '丙' }],
    };
    const outcome = await runExport(deps, req);

    expect(outcome.ok).toBe(true);
    const finalMsg = (deps.getHudState().setPendingSystemMessage as Mock).mock.calls.at(-1)![0] as string;
    expect(finalMsg).toContain('成功 2/3 帧');
    expect(finalMsg).toContain('已跳过');
    // 降级清单也如实入清单（atlas_page_skipped）
    expect(finalMsg).toContain('atlas_page_skipped');
  });

  it('png + frames → grid 拼板上传（多帧 grid 系统消息）', async () => {
    const map = makeMockMap();
    const deps = makeRunExportDeps(map);
    stubUpload();

    const req: ExportRequest = {
      title: '小型拼板',
      format: 'png',
      frames: [{ title: '甲' }, { title: '乙' }],
    };
    const outcome = await runExport(deps, req);

    expect(outcome.ok).toBe(true);
    expect(outcome.format).toBe('png');
    const finalMsg = (deps.getHudState().setPendingSystemMessage as Mock).mock.calls.at(-1)![0] as string;
    expect(finalMsg).toContain('grid 拼板');
  });

  it('全帧失败 → 如实失败（outcome.ok=false，错误可读）', async () => {
    const map = makeMockMap({ failIdleOnFrame: 1 }); // 每帧 idle 永不触发
    const deps = makeRunExportDeps(map);
    deps.idleTimeoutMs = 20;
    stubUpload();

    const req: ExportRequest = {
      title: '空图集',
      format: 'png',
      frames: [{ title: '甲' }],
    };
    const outcome = await runExport(deps, req);

    expect(outcome.ok).toBe(false);
    expect(outcome.error).toContain('多帧导出失败');
  });
});
