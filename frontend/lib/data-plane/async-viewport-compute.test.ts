import { describe, it, expect, afterEach, vi } from 'vitest';

import {
  computeFilterThin,
  handleViewportComputeRequest,
  computeFilterThinAsync,
  resetViewportComputeForTests,
  VIEWPORT_WORKER_MIN_FEATURES,
} from '@/lib/data-plane/async-viewport-compute';
import type {
  ViewportComputeRequest,
  ViewportComputeResult,
} from '@/lib/data-plane/async-viewport-compute';

/**
 * 离主线程视口计算契约：纯实现与 worker 协议零分叉（同一 computeFilterThin）；
 * Worker 不可用 → 同步回退（同形状 Promise）；worker 端 raw store FIFO 有界；
 * unknown token → data:null + error（调用方回退，绝不渲染错误数据）。
 */

function fc(n: number) {
  return {
    type: 'FeatureCollection' as const,
    features: Array.from({ length: n }, (_, i) => ({
      type: 'Feature' as const,
      id: i,
      geometry: { type: 'Point' as const, coordinates: [116 + i * 0.001, 39.9] },
      properties: { i },
    })),
  };
}

const VIEW: [number, number, number, number] = [116.0, 39.5, 116.5, 40.5];
const FAR: [number, number, number, number] = [100.0, 30.0, 101.0, 31.0];

afterEach(() => {
  resetViewportComputeForTests();
  vi.unstubAllGlobals?.();
});

describe('protocol — pure worker-side handler', () => {
  it('filter-thin returns deterministic trimmed collection', () => {
    const store = new Map<number, ReturnType<typeof fc>>();
    const posted: ViewportComputeResult[] = [];
    const raw = fc(1200); // 全部落在宽视口内 → thin 的 minFilter(1000) 生效
    handleViewportComputeRequest({ type: 'init-raw', token: 1, raw }, store, (r) => posted.push(r));
    handleViewportComputeRequest(
      { type: 'filter-thin', token: 1, viewport: [116, 39.5, 117.5, 40.5], budget: 100 },
      store,
      (r) => posted.push(r),
    );
    expect(posted).toHaveLength(1);
    expect(posted[0].error).toBeUndefined();
    expect(posted[0].data!.features.length).toBeLessThanOrEqual(100);
    // 同输入同输出（与主线程纯函数零分叉）
    expect(posted[0].data).toEqual(computeFilterThin(raw, [116, 39.5, 117.5, 40.5], 100));
  });

  it('unknown token → data null + error (safe fallback signal)', () => {
    const store = new Map();
    const posted: ViewportComputeResult[] = [];
    handleViewportComputeRequest(
      { type: 'filter-thin', token: 42, viewport: VIEW, budget: 10 },
      store,
      (r) => posted.push(r),
    );
    expect(posted[0].data).toBeNull();
    expect(posted[0].error).toBe('unknown-token');
  });

  it('raw store is FIFO bounded', () => {
    const store = new Map();
    const noop = () => {};
    for (let t = 1; t <= 10; t += 1) {
      handleViewportComputeRequest({ type: 'init-raw', token: t, raw: fc(t) }, store, noop);
    }
    expect(store.size).toBe(8);
    expect(store.has(1)).toBe(false);
    expect(store.has(3)).toBe(true);
    expect(store.has(10)).toBe(true);
  });
});

describe('computeFilterThinAsync — main-thread behavior', () => {
  it('jsdom (no Worker) → synchronous fallback with correct result', async () => {
    const raw = fc(2000);
    const result = await computeFilterThinAsync(raw, VIEW, 500);
    expect(result).toEqual(computeFilterThin(raw, VIEW, 500));
  });

  it('same viewport memo → identical reference, no recompute', async () => {
    const raw = fc(2000);
    const a = await computeFilterThinAsync(raw, VIEW, 500);
    const b = await computeFilterThinAsync(raw, VIEW, 500);
    expect(a).toBe(b);
  });

  it('threshold constant is the documented 20k', () => {
    expect(VIEWPORT_WORKER_MIN_FEATURES).toBe(20_000);
  });
});

describe('computeFilterThinAsync — worker path (scripted Worker)', () => {
  it('posts init-raw once per raw ref, then token-only jobs; resolves with worker data', async () => {
    const messages: ViewportComputeRequest[] = [];
    const listeners: Array<(e: MessageEvent) => void> = [];
    const fakeWorker = {
      __wired: undefined as boolean | undefined,
      postMessage: vi.fn((msg: ViewportComputeRequest & { jobId?: number }) => {
        messages.push(msg);
        // 脚本化的 worker 端：真实现直接复用纯 handler（协议零分叉证明）。
        const store = (fakeWorker as unknown as { __store: Map<number, ReturnType<typeof fc>> }).__store;
        handleViewportComputeRequest(msg, store, (res) => {
          listeners.forEach((l) => l({ data: { ...res, jobId: msg.jobId } } as MessageEvent));
        });
      }),
      addEventListener: (_t: string, cb: (e: MessageEvent) => void) => {
        listeners.push(cb);
      },
      terminate: () => {},
    };
    (fakeWorker as unknown as { __store: Map<number, ReturnType<typeof fc>> }).__store = new Map();
    // vi.fn 箭头实现不可 new —— 用 class stub（返回同一 fake 实例）。
    vi.stubGlobal('Worker', class {
      constructor() {
        return fakeWorker;
      }
    });
    // 不 stub URL：createWorker 需要 new URL(...) 构造 worker 入口地址。

    const raw = fc(VIEWPORT_WORKER_MIN_FEATURES + 1);
    const p1 = computeFilterThinAsync(raw, VIEW, 500);
    const r1 = await p1;

    // init-raw 恰一次 + filter-thin 一次
    const kinds = messages.map((m) => m.type);
    expect(kinds.filter((t) => t === 'init-raw')).toHaveLength(1);
    expect(kinds.filter((t) => t === 'filter-thin')).toHaveLength(1);
    expect(r1).toEqual(computeFilterThin(raw, VIEW, 500));

    // 第二次同 raw 不同视口：只发 filter-thin（token 复用，不再上传 raw）
    await computeFilterThinAsync(raw, FAR, 500);
    const kinds2 = messages.map((m) => m.type);
    expect(kinds2.filter((t) => t === 'init-raw')).toHaveLength(1);
    expect(kinds2.filter((t) => t === 'filter-thin')).toHaveLength(2);
  });
});
