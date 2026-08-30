/**
 * FE-P2-3 回归：制图观测→修复回路的客户端总预算熔断。
 *
 * 去重环（16 条）淘汰后旧 action_id 可重新派发；若修复每轮都改变观测
 * （A↔B 震荡 / 后端持续换新 action_id），回路理论无界。总预算
 * （MAX_TOTAL_SESSION_REPAIRS=8）耗尽后必须停止派发，且会话切换重置额度。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import type { MapSpecRuntime } from '@/lib/mapspec-runtime';
import { useCartographicObservation } from '@/lib/hooks/use-cartographic-observation';

vi.mock('@/lib/api/transport', () => ({
  apiFetch: vi.fn(),
}));

vi.mock('@/lib/mapspec-runtime', () => ({
  // 观测载荷随层指纹变化——去重键必须随之变化（否则第二轮起被键去重，
  // 预算回路测不到）。
  collectCartographicRuntimeObservation: (
    _map: unknown,
    _spec: unknown,
    layers: Array<{ _mapspecFingerprint?: string }>,
  ) => ({
    session_id: 'sid-budget',
    mapspec_fingerprint: layers?.[0]?._mapspecFingerprint ?? 'fp-0',
    layers: [],
    style_loaded: true,
  }),
}));

vi.mock('@/lib/utils/logger', () => ({
  devOnly: { warn: vi.fn(), log: vi.fn(), error: vi.fn() },
}));

// ADR-0088 runtime repair：保留真实 session-cursor（revision 游标），
// 仅对 commitMapSpecDocument 打桩以便断言修复后 spec 的提交。
vi.mock('@/lib/mapspec/session-cursor', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/mapspec/session-cursor')>();
  return { ...actual, commitMapSpecDocument: vi.fn(() => true) };
});

const { apiFetch } = await import('@/lib/api/transport');

function makeLayer(fingerprint: string) {
  return {
    id: 'L1', name: 'L1', type: 'vector', visible: true, opacity: 1,
    group: 'analysis', _mapspecFingerprint: fingerprint, _mapspecGenerationAt: 1,
  } as never;
}

async function flushAsync(): Promise<void> {
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe('useCartographicObservation repair budget (FE-P2-3)', () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function renderBudgetHook() {
    const dispatchAction = vi.fn();
    const runtimeRef = { current: null as MapSpecRuntime | null };
    const hook = renderHook(
      ({ sessionId }) => useCartographicObservation({
        runtimeRef,
        sessionId,
        ownerToken: null,
        dispatchAction,
      }),
      { initialProps: { sessionId: 'sid-budget' } },
    );
    return { hook, dispatchAction };
  }

  async function issueObservations(count: number, fingerprintSeq: number[] = []) {
    const { hook, dispatchAction } = renderBudgetHook();
    // 每次观测换一个新指纹（观测变化 → 键不同 → 必发 POST），
    // 后端每次返回一个全新 action_id 的修复（去重环无法拦截），
    // 修复指纹回显当次签发指纹（INV-4 指纹门）。
    let seq = 0;
    let issuedFp = '';
    vi.mocked(apiFetch).mockImplementation(async () => ({
      repair_action: {
        type: 'layer_visibility_update',
        action_id: `repair-${seq++}`,
        params: { mapspec_fingerprint: issuedFp },
      },
    }));
    const map = { getStyle: () => ({ layers: [] }) } as never;
    for (let i = 0; i < count; i += 1) {
      const fp = fingerprintSeq[i] ?? 1000 + i;
      issuedFp = `fp-${fp}`;
      hook.result.current({
        map,
        spec: { layers: [] } as never,
        layers: [makeLayer(issuedFp)],
      });
       
      await flushAsync();
    }
    return dispatchAction;
  }

  it('dispatches repairs until the budget is exhausted, then suspends', async () => {
    const dispatchAction = await issueObservations(12);
    // 预算 8：第 9 次起不再派发（12 次观测仅 8 次 dispatch）
    expect(dispatchAction).toHaveBeenCalledTimes(8);
  });

  it('budget resets when the session changes', async () => {
    const dispatchAction = vi.fn();
    const runtimeRef = { current: null as MapSpecRuntime | null };
    const hook = renderHook(
      ({ sessionId }) => useCartographicObservation({
        runtimeRef, sessionId, ownerToken: null, dispatchAction,
      }),
      { initialProps: { sessionId: 'sid-a' } },
    );
    let seq = 0;
    let issuedFp = '';
    vi.mocked(apiFetch).mockImplementation(async () => ({
      repair_action: {
        type: 'layer_visibility_update',
        action_id: `repair-${seq++}`,
        params: { mapspec_fingerprint: issuedFp },
      },
    }));
    const map = { getStyle: () => ({ layers: [] }) } as never;

    for (let i = 0; i < 10; i += 1) {
      issuedFp = `fp-a-${i}`;
      hook.result.current({
        map, spec: { layers: [] } as never, layers: [makeLayer(issuedFp)],
      });
       
      await flushAsync();
    }
    expect(dispatchAction).toHaveBeenCalledTimes(8);

    // 会话切换：额度重置，可继续派发
    hook.rerender({ sessionId: 'sid-b' });
    for (let i = 0; i < 3; i += 1) {
      issuedFp = `fp-b-${i}`;
      hook.result.current({
        map, spec: { layers: [] } as never, layers: [makeLayer(issuedFp)],
      });
       
      await flushAsync();
    }
    expect(dispatchAction).toHaveBeenCalledTimes(11);
  });
});


describe('useCartographicObservation runtime repair spec commit (ADR-0088)', () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  async function issueOne(response: Record<string, unknown>) {
    const { commitMapSpecDocument } = await import('@/lib/mapspec/session-cursor');
    vi.mocked(commitMapSpecDocument).mockClear();
    const dispatchAction = vi.fn();
    const runtimeRef = { current: null as MapSpecRuntime | null };
    const hook = renderHook(
      ({ sessionId }) => useCartographicObservation({
        runtimeRef, sessionId, ownerToken: null, dispatchAction,
      }),
      { initialProps: { sessionId: 'sid-rr' } },
    );
    vi.mocked(apiFetch).mockImplementation(async () => response as never);
    hook.result.current({
      map: { getStyle: () => ({ layers: [] }) } as never,
      spec: { layers: [] } as never,
      layers: [makeLayer('fp-rr')],
    });
    await flushAsync();
    return commitMapSpecDocument;
  }

  it('commits the reasserted spec with its revision after a runtime repair', async () => {
    const commit = await issueOne({
      runtime_repair: {
        applied: ['reassert_spec_layer:poi-main'],
        exhausted: false,
        mapspec: { layers: [{ id: 'poi-main' }], sources: {} },
        mutation_revision: 42,
      },
    });
    expect(commit).toHaveBeenCalledWith(
      { layers: [{ id: 'poi-main' }], sources: {} },
      42,
    );
  });

  it('does not commit when the response carries no runtime repair', async () => {
    const commit = await issueOne({ observation_sequence: 3 });
    expect(commit).not.toHaveBeenCalled();
  });

  it('still commits a runtime repair spec after the dispatch budget is exhausted', async () => {
    const { commitMapSpecDocument } = await import('@/lib/mapspec/session-cursor');
    vi.mocked(commitMapSpecDocument).mockClear();
    const dispatchAction = vi.fn();
    const runtimeRef = { current: null as MapSpecRuntime | null };
    const hook = renderHook(
      ({ sessionId }) => useCartographicObservation({
        runtimeRef, sessionId, ownerToken: null, dispatchAction,
      }),
      { initialProps: { sessionId: 'sid-budget-rr' } },
    );
    let seq = 0;
    let issuedFp = '';
    vi.mocked(apiFetch).mockImplementation(async () => ({
      repair_action: {
        type: 'layer_visibility_update',
        action_id: `repair-${seq++}`,
        params: { mapspec_fingerprint: issuedFp },
      },
      // 仅最后一轮携带 runtime reassert 的 spec —— 预算耗尽不得吞掉提交
      ...(seq >= 9 ? {
        runtime_repair: {
          applied: ['reassert_spec_layer:poi-main'],
          exhausted: false,
          mapspec: { layers: [{ id: 'poi-main' }], sources: {} },
          mutation_revision: 7,
        },
      } : {}),
    } as never));
    const map = { getStyle: () => ({ layers: [] }) } as never;
    for (let i = 0; i < 9; i += 1) {
      issuedFp = `fp-rr-${i}`;
      hook.result.current({
        map, spec: { layers: [] } as never, layers: [makeLayer(issuedFp)],
      });
      await flushAsync();
    }
    // 预算 8：第 9 个 repair 不再派发，但其 runtime_repair spec 仍提交
    expect(dispatchAction).toHaveBeenCalledTimes(8);
    expect(commitMapSpecDocument).toHaveBeenCalledWith(
      { layers: [{ id: 'poi-main' }], sources: {} },
      7,
    );
  });
});
