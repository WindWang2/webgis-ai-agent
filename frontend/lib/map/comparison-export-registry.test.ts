/**
 * W8（ADR-0118）：swipe 对比导出组合 —— 不再静默丢弃副图。
 *
 * 契约：
 * 1. registry（module 级 get/set/clear，防泄漏：clear 后读回 null）；
 * 2. 组合几何：副图 canvas 按 position 裁剪画右侧（分界 x = width*position，
 *    与 live clip-path inset 同侧），分界线绘制；
 * 3. 副图 canvas 不可用（未渲染/跨域污染）→ 只导主图 + 显式 warning 诊断
 *   （comparison_second_view_not_exported）；可用 → comparison_export_composed
 *   （info，detail=百分比）。
 */
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import {
  setComparisonExport,
  getComparisonExport,
  clearComparisonExport,
} from './comparison-export-registry';
import { composeComparisonOnExportCanvas } from '@/lib/map-kit/exporter';

describe('comparison export registry', () => {
  beforeEach(() => clearComparisonExport());

  it('set/get/clear roundtrip（clear 后读回 null —— 不持泄漏引用）', () => {
    expect(getComparisonExport()).toBeNull();
    const state = { getSecondCanvas: () => null, kind: 'swipe', position: 0.5 };
    setComparisonExport(state);
    expect(getComparisonExport()).toBe(state);
    clearComparisonExport();
    expect(getComparisonExport()).toBeNull();
  });

  it('重复 set 覆盖旧注册（单活跃对比态）', () => {
    setComparisonExport({ getSecondCanvas: () => null, kind: 'swipe', position: 0.3 });
    setComparisonExport({ getSecondCanvas: () => null, kind: 'swipe', position: 0.8 });
    expect(getComparisonExport()?.position).toBe(0.8);
  });
});

describe('composeComparisonOnExportCanvas', () => {
  function recordingCtx() {
    const calls = { drawImage: [] as unknown[][], fillRect: [] as unknown[][], fillStyles: [] as unknown[] };
    return {
      ctx: {
        drawImage: vi.fn((...args: unknown[]) => calls.drawImage.push(args)),
        fillRect: vi.fn((...args: unknown[]) => calls.fillRect.push(args)),
        save: vi.fn(),
        restore: vi.fn(),
        set fillStyle(v: unknown) { calls.fillStyles.push(v); },
        get fillStyle() { return ''; },
      } as unknown as CanvasRenderingContext2D,
      calls,
    };
  }

  const crop = { srcX: 0, srcY: 0, srcW: 1000, srcH: 800 };

  it('组合几何：副图按 position 裁右半（分界 x = width*position）+ 分界线', () => {
    const { ctx, calls } = recordingCtx();
    const exportCanvas = {
      width: 1000,
      height: 800,
      getContext: () => ctx,
    } as unknown as HTMLCanvasElement;
    const second = { width: 1000, height: 800 } as HTMLCanvasElement;
    const composed = composeComparisonOnExportCanvas(
      exportCanvas,
      { getSecondCanvas: () => second, kind: 'swipe', position: 0.7 },
      crop,
      { width: 1000, height: 800 },
    );

    expect(composed).toBe(true);
    // drawImage(第二画布, sx, sy, sw, sh, dx, dy, dw, dh) —— 分界 x = 1000*0.7
    expect(calls.drawImage).toHaveLength(1);
    const [src, sx, sy, sw, sh, dx, dy, dw, dh] = calls.drawImage[0] as [
      unknown, number, number, number, number, number, number, number, number,
    ];
    expect(src).toBe(second);
    expect(sx).toBeCloseTo(700); // second.width * 0.7
    expect(sw).toBeCloseTo(300); // second.width * 0.3
    expect(sy).toBe(0);
    expect(sh).toBe(800);
    expect(dx).toBeCloseTo(700); // exportCanvas.width * 0.7
    expect(dw).toBeCloseTo(300);
    expect(dy).toBe(0);
    expect(dh).toBe(800);
    // 分界线画在分界 x 处（竖条 —— fillRect x ∈ {x-1, x}）
    const dividerXs = calls.fillRect.map((a) => a[0] as number);
    expect(dividerXs.some((x) => Math.abs(x - 699) < 2 || Math.abs(x - 700) < 2)).toBe(true);
  });

  it('position 退化值防御性夹取（0 → 0.02）', () => {
    const { ctx, calls } = recordingCtx();
    const exportCanvas = {
      width: 1000,
      height: 800,
      getContext: () => ctx,
    } as unknown as HTMLCanvasElement;
    composeComparisonOnExportCanvas(
      exportCanvas,
      { getSecondCanvas: () => ({ width: 1000, height: 800 } as HTMLCanvasElement), kind: 'swipe', position: 0 },
      crop,
      { width: 1000, height: 800 },
    );
    const [, , , , , dx] = calls.drawImage[0] as [unknown, number, number, number, number, number];
    expect(dx).toBeCloseTo(20); // 1000 * 0.02
  });

  it('副图 canvas 不可用 → 返回 false（调用方回退主图 + 诊断）', () => {
    const { ctx } = recordingCtx();
    const exportCanvas = {
      width: 1000,
      height: 800,
      getContext: () => ctx,
    } as unknown as HTMLCanvasElement;
    expect(
      composeComparisonOnExportCanvas(
        exportCanvas,
        { getSecondCanvas: () => null, kind: 'swipe', position: 0.5 },
        crop,
        { width: 1000, height: 800 },
      ),
    ).toBe(false);
  });
});

// ── runExport 集成：组合/回退都带显式诊断进系统消息 ─────────────────────

import { runExport, type ExportDeps, type ExportRequest } from '@/lib/map-kit/exporter';

vi.mock('@/lib/api/config', () => ({
  API_BASE: 'http://localhost:8001',
}));

vi.mock('@/lib/utils/logger', () => ({
  devOnly: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

function mockFetchUpload(url = '/exports/map.png', filename = 'map.png') {
  const mockFetch = vi.fn((reqUrl: string) => {
    if (typeof reqUrl === 'string' && reqUrl.includes('/api/v1/export')) {
      return Promise.resolve({ ok: true, json: async () => ({ url, filename }) } as any);
    }
    return Promise.resolve({ ok: true, blob: async () => new Blob(['img'], { type: 'image/png' }) } as any);
  }) as Mock;
  vi.stubGlobal('fetch', mockFetch);
  return mockFetch;
}

function createDeps(): ExportDeps {
  const hudState = {
    theme: 'light' as const,
    layers: [],
    addExport: vi.fn(),
    setPendingSystemMessage: vi.fn(),
  };
  return {
    map: {
      getPixelRatio: vi.fn(() => 1),
      setPixelRatio: vi.fn(),
      getCanvas: vi.fn(() => {
        const canvas = document.createElement('canvas');
        canvas.width = 800;
        canvas.height = 600;
        return canvas;
      }),
      getCenter: vi.fn(() => ({ lat: 30, lng: 104 })),
      getZoom: vi.fn(() => 9),
      getBearing: vi.fn(() => 0),
      once: vi.fn((_e: string, cb: () => void) => cb()),
    } as any,
    getHudState: () => hudState,
  };
}

describe('runExport comparison composition', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    clearComparisonExport();
  });

  it('副图可用：系统消息带 comparison_export_composed（detail=百分比）', async () => {
    const deps = createDeps();
    mockFetchUpload('/exports/map.png', 'map.png');
    const second = document.createElement('canvas');
    second.width = 800;
    second.height = 600;
    setComparisonExport({ getSecondCanvas: () => second, kind: 'swipe', position: 0.7 });

    const req: ExportRequest = { title: '对比导出', format: 'png' };
    const outcome = await runExport(deps, req);

    expect(outcome.ok).toBe(true);
    const finalMsg = (deps.getHudState().setPendingSystemMessage as Mock).mock.calls.at(-1)![0] as string;
    expect(finalMsg).toContain('comparison_export_composed');
    expect(finalMsg).toContain('70%');
  });

  it('副图不可用：只导主图 + comparison_second_view_not_exported（不再静默）', async () => {
    const deps = createDeps();
    mockFetchUpload('/exports/map.png', 'map.png');
    setComparisonExport({ getSecondCanvas: () => null, kind: 'swipe', position: 0.5 });

    const req: ExportRequest = { title: '对比导出', format: 'png' };
    const outcome = await runExport(deps, req);

    expect(outcome.ok).toBe(true);
    const finalMsg = (deps.getHudState().setPendingSystemMessage as Mock).mock.calls.at(-1)![0] as string;
    expect(finalMsg).toContain('comparison_second_view_not_exported');
  });
});
