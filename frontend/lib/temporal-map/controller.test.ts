/**
 * temporal-map 播放控制器测试（GOAL 03 M4/M7）。
 *
 * Oracle 锚点：
 * - displayed_at 门控：markApplied 之前 displayed 不更新；
 * - 乱序拒绝：stale seq 回执被丢弃并计数；
 * - 取数缓存（M7）：同 fingerprint 命中零 loader 调用（拖动防风暴）；
 * - destroy/会话清理停播清缓存。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MapSpecTemporalConfig, TemporalFrame } from '@/lib/mapspec-compiler/types';
import {
  FrameFetchCache,
  TemporalPlaybackController,
  resetTemporalMetricsForTests,
  temporalMetrics,
} from './controller';
import {
  getDisplayedTemporalFrame,
  getLiveTemporalFrame,
  getLiveTemporalRequest,
  markLiveApplied,
  requestLiveFrame,
  resetTemporalLiveState,
  setActiveController,
} from './live-frame';

function frame(id: string, year: number): TemporalFrame {
  return {
    id, label: String(year),
    start: `${year}-01-01T00:00:00Z`, end: `${year + 1}-01-01T00:00:00Z`,
    feature_count: 1,
  } as TemporalFrame;
}

function cfg(): MapSpecTemporalConfig {
  return {
    enabled: true,
    frames: [frame('f-2021', 2021), frame('f-2022', 2022), frame('f-2023', 2023)],
    cursor_frame: 'f-2021',
    playback: { playing: false, rate: 1 },
    time_field: 'year',
    time_representation: 'year_int',
    granularity: 'year',
    source_ref: 'ref:s',
  } as unknown as MapSpecTemporalConfig;
}

beforeEach(() => {
  resetTemporalMetricsForTests();
  resetTemporalLiveState();
});

afterEach(() => {
  resetTemporalLiveState();
});

describe('seq 顺序与 displayed_at 门控', () => {
  it('markApplied 前不更新 displayed；之后更新', () => {
    const c = new TemporalPlaybackController();
    setActiveController(c);
    const f = frame('f-2023', 2023);
    requestLiveFrame(f);
    expect(getLiveTemporalFrame()?.id).toBe('f-2023');
    // 未回执：displayed 仍旧（门控 —— UI 不得先行宣称新时间）。
    expect(getDisplayedTemporalFrame()?.id).toBeUndefined();
    const req = getLiveTemporalRequest()!;
    expect(markLiveApplied(req.seq, req.frame)).toBe(true);
    expect(getDisplayedTemporalFrame()?.id).toBe('f-2023');
  });

  it('乱序/stale 回执被拒绝并计数', () => {
    const c = new TemporalPlaybackController();
    setActiveController(c);
    const applied: string[] = [];
    c.onApplied = (f) => applied.push(f.id);
    const a = c.requestFrame(frame('f-2021', 2021));
    const b = c.requestFrame(frame('f-2022', 2022));
    // b（新）先应用，a（旧）后到 —— a 必须被拒（快帧取消不乱序）。
    expect(c.markApplied(b.seq, b.frame)).toBe(true);
    expect(c.markApplied(a.seq, a.frame)).toBe(false);
    expect(temporalMetrics.staleRejected).toBe(1);
    expect(applied).toEqual(['f-2022']);
  });

  it('destroy 后拒绝一切回执', () => {
    const c = new TemporalPlaybackController();
    const a = c.requestFrame(frame('f-2021', 2021));
    c.destroy();
    expect(c.markApplied(a.seq, a.frame)).toBe(false);
  });
});

describe('FrameFetchCache（M7 防风暴）', () => {
  it('同 key 命中 → loader 只调一次', async () => {
    const cache = new FrameFetchCache<string>();
    const loader = vi.fn(async () => 'value');
    expect(await cache.fetch('k', loader)).toBe('value');
    expect(await cache.fetch('k', loader)).toBe('value');
    expect(loader).toHaveBeenCalledTimes(1);
    expect(temporalMetrics.cacheHits).toBe(1);
    expect(temporalMetrics.fetches).toBe(1);
  });

  it('in-flight 去重：并发同 key 只发一次请求', async () => {
    const cache = new FrameFetchCache<string>();
    let resolveFn: (v: string) => void = () => {};
    const loader = vi.fn(() => new Promise<string>((res) => { resolveFn = res; }));
    const p1 = cache.fetch('k', loader);
    const p2 = cache.fetch('k', loader);
    resolveFn('v');
    expect(await p1).toBe('v');
    expect(await p2).toBe('v');
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it('LRU 上限 24（长播放内存有界）', async () => {
    const cache = new FrameFetchCache<number>();
    for (let i = 0; i < 30; i += 1) {
      await cache.fetch(`k${i}`, async () => i);
    }
    expect(cache.size).toBe(24);
  });
});

describe('播放节拍', () => {
  it('rate 越界钳制；interval 有下限', () => {
    const c = new TemporalPlaybackController();
    expect(c.intervalFor(1)).toBe(1000);
    expect(c.intervalFor(8)).toBe(125);
    expect(c.intervalFor(99)).toBe(125);
    expect(c.intervalFor(0.1)).toBe(4000);
    expect(c.intervalFor(1000)).toBeGreaterThanOrEqual(80);
  });

  it('播放到末帧停播（onEnd 回执）', () => {
    vi.useFakeTimers();
    try {
      const c = new TemporalPlaybackController();
      const advanced: string[] = [];
      let ended = false;
      c.start(cfg(), (f) => advanced.push(f.id), () => { ended = true; });
      vi.advanceTimersByTime(1000);
      vi.advanceTimersByTime(1000);
      vi.advanceTimersByTime(1000);
      expect(advanced).toEqual(['f-2022', 'f-2023']);
      expect(ended).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it('stop/destroy 取消计时器（路由切换取消面）', () => {
    vi.useFakeTimers();
    try {
      const c = new TemporalPlaybackController();
      const advanced: string[] = [];
      c.start(cfg(), (f) => advanced.push(f.id));
      c.stop();
      vi.advanceTimersByTime(5000);
      expect(advanced).toEqual([]);
    } finally {
      vi.useRealTimers();
    }
  });
});
