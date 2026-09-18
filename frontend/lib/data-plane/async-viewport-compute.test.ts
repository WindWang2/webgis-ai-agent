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
      { type: 'filter-thin', token: 1, viewport: [116, 39.5, 117.5, 40.5], budget: 100, jobId: 1 },
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
      { type: 'filter-thin', token: 42, viewport: VIEW, budget: 10, jobId: 1 },
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

describe('computeFilterThinAsync — worker path (scripted Worker over REAL handler)', () => {
  /**
   * 反 mock 假验证纪律（review P1 教训）：脚本 worker **必须**走真实
   * handleViewportComputeRequest（与 viewport.worker.ts 同一条注册路径），
   * 不得在测试里手补协议字段 —— jobId 回传、unknown-token 重传都由
   * 真实 handler 负责，测试只提供消息泵。
   */
  function makeRealHandlerWorker() {
    const store = new Map<number, ReturnType<typeof fc>>();
    const listeners: Array<(e: MessageEvent) => void> = [];
    const posted: ViewportComputeRequest[] = [];
    const worker = {
      __wired: undefined as boolean | undefined,
      onerror: null as unknown,
      postMessage: (msg: ViewportComputeRequest) => {
        posted.push(msg);
        // 与 viewport.worker.ts 完全同款：真实 handler + 原样回传结果。
        handleViewportComputeRequest(msg, store, (res) => {
          listeners.forEach((l) => l({ data: res } as MessageEvent));
        });
      },
      addEventListener: (_t: string, cb: (e: MessageEvent) => void) => {
        listeners.push(cb);
      },
      terminate: () => {},
    };
    return { worker, store, posted };
  }

  function stubWorker(w: object): void {
    // vi.fn 箭头实现不可 new —— 用 class stub（返回同一实例）。
    vi.stubGlobal('Worker', class {
      constructor() {
        return w;
      }
    });
    // 不 stub URL：createWorker 需要 new URL(...) 构造 worker 入口地址。
  }

  it('jobId echo via real handler; init-raw once per raw ref; token-only jobs after', async () => {
    const { worker, posted } = makeRealHandlerWorker();
    stubWorker(worker);

    const raw = fc(VIEWPORT_WORKER_MIN_FEATURES + 1);
    const r1 = await computeFilterThinAsync(raw, VIEW, 500);
    expect(r1).toEqual(computeFilterThin(raw, VIEW, 500)); // 真实 handler 的裁剪结果

    await computeFilterThinAsync(raw, FAR, 500);
    const kinds = posted.map((m) => m.type);
    expect(kinds.filter((t) => t === 'init-raw')).toHaveLength(1);
    expect(kinds.filter((t) => t === 'filter-thin')).toHaveLength(2);
  });

  it('unknown-token (FIFO evicted) → auto re-init + retry, still resolves with data', async () => {
    const { worker, store, posted } = makeRealHandlerWorker();
    stubWorker(worker);

    const raw = fc(VIEWPORT_WORKER_MIN_FEATURES + 1);
    await computeFilterThinAsync(raw, VIEW, 500);
    // 模拟 worker 端 FIFO 逐出：清空 store（主线程 token 台账仍标记已上传）。
    store.clear();
    const r2 = await computeFilterThinAsync(raw, FAR, 500);
    expect(r2).toEqual(computeFilterThin(raw, FAR, 500)); // 重传后成功，不挂起
    const kinds = posted.map((m) => m.type);
    // re-init 恰多发生一次（unknown-token 触发）
    expect(kinds.filter((t) => t === 'init-raw')).toHaveLength(2);
  });

  it('memo is written on worker success (same viewport → no extra round trip)', async () => {
    const { worker, posted } = makeRealHandlerWorker();
    stubWorker(worker);

    const raw = fc(VIEWPORT_WORKER_MIN_FEATURES + 1);
    await computeFilterThinAsync(raw, VIEW, 500);
    const before = posted.length;
    await computeFilterThinAsync(raw, VIEW, 500); // 同视口同预算 → memo 命中
    expect(posted.length).toBe(before);
  });
});
