import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { runExport, type ExportDeps, type ExportRequest } from './exporter';

/** ADR-0157 P1 单飞锁：引擎级互斥 —— 第二个并发 runExport 得到如实失败。 */

function makeDeps(): ExportDeps {
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
        const c = document.createElement('canvas');
        c.width = 100;
        c.height = 80;
        return c;
      }),
      getZoom: vi.fn(() => 8),
      getCenter: vi.fn(() => ({ lng: 104, lat: 30 })),
      getBearing: vi.fn(() => 0),
      getPitch: vi.fn(() => 0),
    } as unknown as ExportDeps['map'],
    getHudState: () => hudState,
  };
}

describe('runExport 单飞锁（ADR-0157 P1 资源纪律）', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('并发第二调用立即失败并说明串行纪律；首个任务不受影响', async () => {
    let releaseUpload: (v: unknown) => void = () => {};
    const mockFetch = vi.fn((reqUrl: string) => {
      if (typeof reqUrl === 'string' && reqUrl.includes('/api/v1/export')) {
        return new Promise((resolve) => {
          releaseUpload = resolve;
        }) as unknown as Promise<Response>;
      }
      return Promise.resolve({ ok: true, blob: async () => new Blob(['img'], { type: 'image/png' }) } as any);
    }) as Mock;
    vi.stubGlobal('fetch', mockFetch);

    const deps = makeDeps();
    const first = runExport(deps, { title: '首图' } as ExportRequest);
    // 首个任务在 upload 处挂起（in-flight）→ 第二调用应立即得到 busy。
    const second = await runExport(deps, { title: '抢跑' } as ExportRequest);
    expect(second.ok).toBe(false);
    expect(second.error).toContain('进行中');

    // 等首个任务真正到达 upload fetch（动态 import 链路的微任务时序不定），
    // 再放行 —— 否则 releaseUpload 还是 noop，首个任务会永远挂起。
    await vi.waitFor(
      () => {
        const hitUpload = mockFetch.mock.calls.some(
          ([u]) => typeof u === 'string' && u.includes('/api/v1/export'),
        );
        if (!hitUpload) throw new Error('upload fetch not reached yet');
      },
      { timeout: 8_000, interval: 20 },
    );
    releaseUpload({
      ok: true,
      json: async () => ({ url: '/exports/export.png', filename: 'export.png' }),
    });
    const firstOutcome = await first;
    expect(firstOutcome.ok).toBe(true);

    // 释放后锁已清除 → 再次调用可正常进入导出。
    vi.stubGlobal(
      'fetch',
      vi.fn((reqUrl: string) => {
        if (typeof reqUrl === 'string' && reqUrl.includes('/api/v1/export')) {
          return Promise.resolve({ ok: true, json: async () => ({ url: '/exports/e.png', filename: 'e.png' }) } as any);
        }
        return Promise.resolve({ ok: true, blob: async () => new Blob(['img'], { type: 'image/png' }) } as any);
      }) as Mock,
    );
    const third = await runExport(makeDeps(), { title: '再导' } as ExportRequest);
    expect(third.ok).toBe(true);
  });
});
