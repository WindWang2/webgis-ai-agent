/**
 * RenderPerfProbes 契约测试（F13，ADR-0214 D5）。
 *
 * 锁定：desired 变化 → settle 的 patch latency、会话级 TTFR 一次性、
 * 诚实缺席（无 desired 记录不虚构 0）、reset 语义、块形状有界。
 * 时间用 vi.useFakeTimers 控制 Date.now（模块内不做时钟注入 —— 生产
 * 路径零配置）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  resetDataPlaneObservabilityForTests,
  recordDataPlaneEvent,
} from '@/lib/data-plane/observability';
import { commitMapSpecDocument } from '@/lib/mapspec/session-cursor';
import {
  PERF_SCHEMA_VERSION,
  _resetRenderProbesForTests,
  noteRenderSettled,
  resetRenderProbes,
  snapshotRenderPerfBlock,
} from '@/lib/telemetry/render-probes';

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
  _resetRenderProbesForTests();
  resetDataPlaneObservabilityForTests();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('render probes', () => {
  it('no desired change → no fabricated latency fields', () => {
    noteRenderSettled({ mapIdle: true });
    const block = snapshotRenderPerfBlock({ mapIdle: true });
    expect(block.schema_version).toBe(PERF_SCHEMA_VERSION);
    expect(block.ttfr_ms).toBeUndefined();
    expect(block.patch_latency_ms).toBeUndefined();
    expect(block.map_idle).toBe(true);
  });

  it('patch latency = desired bump → settle', () => {
    // 首个 note 建立订阅；随后 commit 触发 listener（lastDesiredAt 锚点）
    noteRenderSettled({ mapIdle: true }); // 订阅建立（此 settle 无 desired 锚点）
    vi.advanceTimersByTime(10);
    commitMapSpecDocument({ version: '1.0', sources: {}, layers: [] }, 1);
    vi.advanceTimersByTime(120);
    noteRenderSettled({ mapIdle: true });
    const block = snapshotRenderPerfBlock({ mapIdle: true });
    expect(block.patch_latency_ms).toBe(120);
    // TTFR 锚点 = 首个 desired 变化（t=10）→ 首次 idle settle（t=130）
    expect(block.ttfr_ms).toBe(120);
  });

  it('steady-state re-settle does not rewrite patch latency', () => {
    noteRenderSettled({ mapIdle: true }); // t=0：订阅建立，lastSettled=0
    vi.advanceTimersByTime(5);
    commitMapSpecDocument({ version: '1.0', sources: {}, layers: [] }, 1); // t=5
    vi.advanceTimersByTime(100);
    noteRenderSettled({ mapIdle: true }); // t=105：desired(5) > settled(0) → 100
    // 无新 desired 变化的重复采集（同稳态观察）不改写
    vi.advanceTimersByTime(5000);
    noteRenderSettled({ mapIdle: true });
    const block = snapshotRenderPerfBlock({ mapIdle: true });
    expect(block.patch_latency_ms).toBe(100);
  });

  it('ttfr reported once per session; reset clears', () => {
    noteRenderSettled({ mapIdle: true });
    vi.advanceTimersByTime(5);
    commitMapSpecDocument({ version: '1.0', sources: {}, layers: [] }, 1);
    vi.advanceTimersByTime(70);
    noteRenderSettled({ mapIdle: true });
    expect(snapshotRenderPerfBlock({ mapIdle: true }).ttfr_ms).toBe(70);
    // 会话切换 reset → 无新 desired 记录 → 缺席（不残留旧值）
    resetRenderProbes();
    expect(snapshotRenderPerfBlock({ mapIdle: true }).ttfr_ms).toBeUndefined();
  });

  it('ttfr anchors at first idle settle following the first desired bump', () => {
    noteRenderSettled({ mapIdle: true }); // t=0：仅建立订阅
    vi.advanceTimersByTime(5);
    commitMapSpecDocument({ version: '1.0', sources: {}, layers: [] }, 1); // t=5
    vi.advanceTimersByTime(50);
    // 首次 settle 超时（mapIdle=false）→ ttfr 仍缺席
    noteRenderSettled({ mapIdle: false });
    expect(snapshotRenderPerfBlock({ mapIdle: false }).ttfr_ms).toBeUndefined();
    vi.advanceTimersByTime(30);
    // t=85：mapIdle 落定 —— ttfr = 85-5 = 80（首 desired → 首 idle settle）
    noteRenderSettled({ mapIdle: true });
    expect(snapshotRenderPerfBlock({ mapIdle: true }).ttfr_ms).toBe(80);
  });

  it('render failures and data plane counters flow through bounded block', () => {
    recordDataPlaneEvent('fetch-ok', {});
    recordDataPlaneEvent('cache-hit', {});
    const block = snapshotRenderPerfBlock({ renderFailures: 2, mapIdle: true });
    expect(block.render_failures).toBe(2);
    expect(block.data_plane.fetchOk).toBe(1);
    expect(block.data_plane.cacheHits).toBe(1);
    expect(typeof block.cache_bytes).toBe('number');
  });
});
