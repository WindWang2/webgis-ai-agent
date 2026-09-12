/**
 * ac-08（ADR-0157 P4/P7）· WYSIWYG exporter 接线契约。
 *
 * - 纸张档（A4）+ fit_to_frame → 相机 fitBounds 到图框纵横比导出范围
 *   （⊇ 遮罩），导出后相机恢复（jumpTo 回存档）；
 * - screen 档 → 不干预相机（遮罩即导出件）；
 * - fit 超时（idle 不触发）→ 回退旧裁切语义 + extent_fit_timeout_degraded
 *   如实披露（不静默）。
 */
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { runExport, type ExportDeps, type ExportRequest } from './exporter';

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8001' }));
vi.mock('@/lib/utils/logger', () => ({
  devOnly: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

function makeDeps(opts: { bounds?: { getWest: () => number; getSouth: () => number; getEast: () => number; getNorth: () => number }; failIdle?: boolean } = {}): ExportDeps {
  const hudState = {
    theme: 'light' as const,
    layers: [],
    addExport: vi.fn(),
    setPendingSystemMessage: vi.fn(),
  };
  const map: Record<string, unknown> = {
    getPixelRatio: vi.fn(() => 1),
    setPixelRatio: vi.fn(),
    getCanvas: vi.fn(() => {
      const c = document.createElement('canvas');
      c.width = 800;
      c.height = 600;
      return c;
    }),
    getCenter: vi.fn(() => ({ lng: 104.06, lat: 30.67 })),
    getZoom: vi.fn(() => 10),
    getBearing: vi.fn(() => 0),
    getPitch: vi.fn(() => 0),
    fitBounds: vi.fn(),
    jumpTo: vi.fn(),
    once: vi.fn((_e: string, cb: () => void) => {
      if (!opts.failIdle) cb();
    }),
  };
  if (opts.bounds) map.getBounds = vi.fn(() => opts.bounds);
  return {
    map: map as unknown as ExportDeps['map'],
    getHudState: () => hudState,
  };
}

function stubUpload() {
  const mockFetch = vi.fn((reqUrl: string) => {
    if (typeof reqUrl === 'string' && reqUrl.includes('/api/v1/export')) {
      return Promise.resolve({ ok: true, json: async () => ({ url: '/exports/e.png', filename: 'e.png' }) } as any);
    }
    return Promise.resolve({ ok: true, blob: async () => new Blob(['img'], { type: 'image/png' }) } as any);
  }) as Mock;
  vi.stubGlobal('fetch', mockFetch);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('WYSIWYG 出图范围（ADR-0157 P4）', () => {
  it('A4 纸张档 → fitBounds 被调用且导出范围 ⊇ 遮罩；导出后相机恢复', async () => {
    const deps = makeDeps({
      bounds: { getWest: () => 104.0, getSouth: () => 30.6, getEast: () => 104.1, getNorth: () => 30.7 },
    });
    stubUpload();
    const req = { title: 'T', format: 'png', paperSize: 'A4' } as ExportRequest;
    const outcome = await runExport(deps, req);
    expect(outcome.ok).toBe(true);

    const map = deps.map as unknown as { fitBounds: Mock; jumpTo: Mock };
    expect(map.fitBounds).toHaveBeenCalledTimes(1);
    const [boundsArg, fitOpts] = map.fitBounds.mock.calls[0] as unknown as [
      [[number, number], [number, number]],
      Record<string, unknown>,
    ];
    const [[w0, s0], [e0, n0]] = boundsArg;
    // 导出范围 ⊇ 遮罩（东西扩张语义：经度展宽；纬度不变维允许 1e-9 浮点误差）
    expect(w0).toBeLessThanOrEqual(104.0);
    expect(e0).toBeGreaterThanOrEqual(104.1);
    expect(s0).toBeCloseTo(30.6, 9);
    expect(n0).toBeCloseTo(30.7, 9);
    expect(fitOpts['duration']).toBe(0);
    expect(fitOpts['padding']).toBe(0);
    // 相机恢复（WYSIWYG 不留相机残迹）
    expect(map.jumpTo).toHaveBeenCalled();
  });

  it('screen 档 → 不干预相机', async () => {
    const deps = makeDeps({
      bounds: { getWest: () => 104.0, getSouth: () => 30.6, getEast: () => 104.1, getNorth: () => 30.7 },
    });
    stubUpload();
    const outcome = await runExport(deps, { title: 'T' } as ExportRequest);
    expect(outcome.ok).toBe(true);
    const map = deps.map as unknown as { fitBounds: Mock };
    expect(map.fitBounds).not.toHaveBeenCalled();
  });

  it('fit idle 超时 → 回退裁切语义 + extent_fit_timeout_degraded 披露，导出仍成功', async () => {
    const deps = makeDeps({
      bounds: { getWest: () => 104.0, getSouth: () => 30.6, getEast: () => 104.1, getNorth: () => 30.7 },
      failIdle: true,
    });
    deps.idleTimeoutMs = 50;
    stubUpload();
    const outcome = await runExport(deps, { title: 'T', paperSize: 'A4' } as ExportRequest);
    expect(outcome.ok).toBe(true);
    const finalMsg = (deps.getHudState().setPendingSystemMessage as Mock).mock.calls.at(-1)![0] as string;
    expect(finalMsg).toContain('extent_fit_timeout_degraded');
    // 回退后相机恢复
    const map = deps.map as unknown as { jumpTo: Mock };
    expect(map.jumpTo).toHaveBeenCalled();
  });
});
