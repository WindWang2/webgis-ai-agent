/**
 * Interaction IR V1 outbox 测试（ADR-0227 C/G）：
 * 批量/重试/永久丢弃分类 / replace_pending 合并 / 水印同步收敛 /
 * 队列上界 / keepalive 冲刷分块 / per-tab 身份与 seq 持久化。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  createInteractionOutbox,
  getClientInstanceId,
  nextClientSeq,
  type InteractionOutboxOptions,
} from './outbox';
import type { InteractionEventV1, InteractionIngestResponse } from './contract';

/* ── 测试基建：可控时钟 / fetch mock / 内存 storage ─────────────────── */

class FakeStorage {
  private map = new Map<string, string>();
  getItem(key: string): string | null {
    return this.map.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.map.set(key, value);
  }
}

interface Harness {
  outbox: ReturnType<typeof createInteractionOutbox>;
  fetchCalls: Array<{ url: string; body: { events: InteractionEventV1[] }; keepalive: boolean }>;
  respondWith: (factory: (body: { events: InteractionEventV1[] }) => Response) => void;
  advance: (ms: number) => Promise<void>;
  storage: FakeStorage;
}

function createHarness(overrides: Partial<InteractionOutboxOptions> = {}): Harness {
  vi.useFakeTimers();
  const storage = new FakeStorage();
  const fetchCalls: Harness['fetchCalls'] = [];
  let responder: ((body: { events: InteractionEventV1[] }) => Response) | null = null;
  const fetchImpl = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? '{}')) as { events: InteractionEventV1[] };
    fetchCalls.push({
      url: String(url),
      body,
      keepalive: init?.keepalive === true,
    });
    if (responder) return responder(body);
    return new Response(JSON.stringify({ accepted: body.events.length, duplicates: 0, stale: 0, rejected: 0, acks: [] }), { status: 200 });
  });
  const now = vi.fn(() => Date.now());
  const outbox = createInteractionOutbox({
    getSessionId: () => 'sess-test',
    getOwnerToken: () => 'tok',
    random: () => 0.5,
    now,
    fetchImpl: fetchImpl as unknown as typeof fetch,
    storage,
    ...overrides,
  });
  return {
    outbox,
    fetchCalls,
    respondWith: (factory) => { responder = factory; },
    advance: async (ms) => {
      now.mockReturnValue(now() + ms);
      // advanceTimersByTimeAsync 而非 advanceTimersByTime + 定次微任务冲刷：
      // POST 回调里 await response.json() 消费体流所需微任务跳数随
      // Node/undici 版本浮动，固定 3 跳在部分版本不足 —— settleBatch 迟至本轮
      // advance 之后才跑，重试定时器才被创建，重试断言恒挂。async 版在推进
      // 时钟前先放行挂起的 promise，对齐 outbox「定时器回调内做异步 IO」的
      // 真实形态。
      await vi.advanceTimersByTimeAsync(ms);
    },
    storage,
  };
}

function event(seq: number, kind: InteractionEventV1['kind'] = 'feature_selected'): InteractionEventV1 {
  return {
    schema_version: 'interaction_ir.v1',
    event_id: `evt-${seq.toString().padStart(8, '0')}`,
    actor: 'user',
    client_instance_id: 'tab-test0001',
    client_seq: seq,
    kind,
    payload: kind === 'viewport_settled'
      ? { center: [116, 39], zoom: 10 + seq }
      : { layer_id: 'roads', feature_id: `f${seq}` },
  };
}

const flushMicrotasks = async (): Promise<void> => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/* ── 行为 ─────────────────────────────────────────────────────────── */

describe('interaction outbox · 批量与合并', () => {
  it('debounce 后单批 POST（多事件合批）', async () => {
    const h = createHarness();
    h.outbox.enqueue(event(1));
    h.outbox.enqueue(event(2));
    h.outbox.enqueue(event(3));
    expect(h.fetchCalls.length).toBe(0);
    await h.advance(500);
    expect(h.fetchCalls.length).toBe(1);
    expect(h.fetchCalls[0].body.events.map((e) => e.client_seq)).toEqual([1, 2, 3]);
    expect(h.fetchCalls[0].url).toContain('/api/v1/chat/sessions/sess-test/interaction-events');
  });

  it('viewport replace_pending：队列内只保留最新未发', async () => {
    const h = createHarness();
    h.outbox.enqueue(event(1, 'viewport_settled'));
    h.outbox.enqueue(event(2, 'viewport_settled'));
    h.outbox.enqueue(event(3, 'viewport_settled'));
    await h.advance(500);
    expect(h.fetchCalls.length).toBe(1);
    expect(h.fetchCalls[0].body.events).toHaveLength(1);
    expect(h.fetchCalls[0].body.events[0].client_seq).toBe(3);
  });

  it('队列上界溢出丢最旧（bounded）', async () => {
    const h = createHarness({ maxQueue: 4, debounceMs: 10_000 });
    for (let i = 1; i <= 6; i += 1) h.outbox.enqueue(event(i));
    h.outbox.flush();
    const seqs = h.fetchCalls.flatMap((c) => c.body.events.map((e) => e.client_seq));
    expect(seqs).toEqual([3, 4, 5, 6]);
  });

  it('批 ≤50 分块 POST', async () => {
    const h = createHarness();
    for (let i = 1; i <= 120; i += 1) h.outbox.enqueue(event(i));
    h.outbox.flush();
    expect(h.fetchCalls.length).toBe(3);
    expect(h.fetchCalls.map((c) => c.body.events.length)).toEqual([50, 50, 20]);
  });
});

describe('interaction outbox · 重试与丢弃', () => {
  it('5xx 暂态重试，exhausted 后放弃', async () => {
    const h = createHarness({ debounceMs: 100, retryBaseMs: 100, retryMaxMs: 400 });
    h.respondWith(() => new Response('boom', { status: 503 }));
    h.outbox.enqueue(event(1));
    await h.advance(100);
    expect(h.fetchCalls.length).toBe(1);
    await h.advance(300);
    expect(h.fetchCalls.length).toBe(2);
    await h.advance(1000);
    expect(h.fetchCalls.length).toBe(3);
    await h.advance(5000);
    expect(h.fetchCalls.length).toBe(3); // 3 次尝试耗尽
  });

  it('401/403 永久丢弃不重试', async () => {
    const h = createHarness({ debounceMs: 50 });
    h.respondWith(() => new Response('denied', { status: 403 }));
    h.outbox.enqueue(event(1));
    await h.advance(50);
    await h.advance(5000);
    expect(h.fetchCalls.length).toBe(1);
  });

  it('persist_degraded 2xx 按暂态重试（同批重发），exhausted 后放弃', async () => {
    const h = createHarness({ debounceMs: 100, retryBaseMs: 100, retryMaxMs: 400 });
    // 后端持久化降级对账信道：2xx + persist_degraded 且**不带** watermarks
    // （服务端内存态已折叠但未落库）—— 客户端必须重发，重放幂等收敛。
    h.respondWith(() => new Response(
      JSON.stringify({
        accepted: 1, duplicates: 0, stale: 0, rejected: 0, acks: [],
        persist_degraded: true,
      }),
      { status: 200 },
    ));
    h.outbox.enqueue(event(1));
    await h.advance(100);
    expect(h.fetchCalls.length).toBe(1);
    await h.advance(300);
    expect(h.fetchCalls.length).toBe(2);
    // 退避后重发的是同一批事件（replace_pending 风暴只发最后 settled 态）。
    expect(h.fetchCalls[1].body.events.map((e) => e.client_seq)).toEqual([1]);
    await h.advance(1000);
    expect(h.fetchCalls.length).toBe(3);
    await h.advance(5000);
    expect(h.fetchCalls.length).toBe(3); // 3 次尝试耗尽（同 5xx 用例）
  });

  it('persist_degraded 后恢复：带水印的成功响应收敛重发', async () => {
    const h = createHarness({ debounceMs: 50, retryBaseMs: 100 });
    let call = 0;
    h.respondWith(() => {
      call += 1;
      if (call === 1) {
        return new Response(
          JSON.stringify({
            accepted: 1, duplicates: 0, stale: 0, rejected: 0, acks: [],
            persist_degraded: true,
          }),
          { status: 200 },
        );
      }
      const instance = getClientInstanceId(h.storage);
      const body: InteractionIngestResponse = {
        accepted: 1, duplicates: 0, stale: 0, rejected: 0, acks: [],
        watermarks: { [instance]: 1 },
      };
      return new Response(JSON.stringify(body), { status: 200 });
    });
    h.outbox.enqueue(event(1));
    await h.advance(50);
    expect(h.fetchCalls.length).toBe(1);
    await h.advance(500);
    expect(h.fetchCalls.length).toBe(2);
    expect(h.outbox.acknowledgedSeq()).toBe(1);
    // 水印到达后收敛：不再有第三次发送。
    await h.advance(5000);
    expect(h.fetchCalls.length).toBe(2);
  });
});

describe('interaction outbox · 水印同步（ack 丢失收敛）', () => {
  it('watermarks 裁剪已记账事件，避免无限重发', async () => {
    const h = createHarness({ debounceMs: 50, retryBaseMs: 100 });
    // 先失败（服务端 5xx），重试成功并回报水印 —— 水印键是 outbox 的真实
    // per-tab 实例 id（生产中 bus 与 outbox 共用 getClientInstanceId 源）。
    let call = 0;
    h.respondWith(() => {
      call += 1;
      if (call === 1) return new Response('net', { status: 500 });
      const instance = getClientInstanceId(h.storage);
      const body: InteractionIngestResponse = {
        accepted: 2, duplicates: 0, stale: 0, rejected: 0, acks: [],
        watermarks: { [instance]: 2 },
      };
      return new Response(JSON.stringify(body), { status: 200 });
    });
    h.outbox.enqueue(event(1));
    h.outbox.enqueue(event(2));
    await h.advance(50);
    expect(h.fetchCalls.length).toBe(1);
    await h.advance(500);
    expect(h.fetchCalls.length).toBe(2);
    expect(h.outbox.acknowledgedSeq()).toBe(2);
    // 收敛：水印到达后不再有第三次发送（否则弱网下无限重发）。
    await h.advance(5000);
    expect(h.fetchCalls.length).toBe(2);
  });
});

describe('interaction outbox · keepalive 卸载冲刷', () => {
  it('pagehide 冲刷走 keepalive 且限单批预算', async () => {
    const h = createHarness({ debounceMs: 60_000 });
    for (let i = 1; i <= 80; i += 1) h.outbox.enqueue(event(i));
    h.outbox.flushKeepalive();
    await flushMicrotasks();
    expect(h.fetchCalls.length).toBe(1); // 单批 ≤50，超出部分放弃
    expect(h.fetchCalls[0].keepalive).toBe(true);
    expect(h.fetchCalls[0].body.events).toHaveLength(50);
  });
});

describe('client 身份与 seq（per-tab）', () => {
  it('sessionStorage 维持实例 id 与单调 seq 跨读取', () => {
    const storage = new FakeStorage();
    const id1 = getClientInstanceId(storage);
    const id2 = getClientInstanceId(storage);
    expect(id1).toBe(id2);
    expect(nextClientSeq(storage)).toBe(1);
    expect(nextClientSeq(storage)).toBe(2);
    expect(nextClientSeq(storage)).toBe(3);
  });

  it('storage 不可用时退化内存态且不抛', () => {
    expect(() => getClientInstanceId(null)).not.toThrow();
    expect(() => nextClientSeq(null)).not.toThrow();
  });

  it('畸形 storage 值不污染 seq（只接受纯数字）', () => {
    const storage = new FakeStorage();
    storage.setItem('intir:client-seq', 'NaN; drop table');
    expect(nextClientSeq(storage)).toBe(1);
  });
});
