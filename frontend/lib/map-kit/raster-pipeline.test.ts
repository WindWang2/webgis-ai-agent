import { describe, it, expect } from 'vitest';

import {
  bandStats,
  bandHistogram,
  gridToRgba,
  rampColor,
  COLOR_RAMP,
} from '@/lib/map-kit/raster-canvas';
import {
  LruCache,
  createTimelineLoader,
  shouldRenderFrame,
  formatStepLabel,
} from '@/lib/map-kit/raster-timeline';
import { buildRequest, EMPTY_FORM, parseSlice, parseBbox } from '@/components/sidebar/lakehouse/query-forms';

/** P4/P7 纯函数契约：统计（nodata 剔除）、色带映射、渲染像素、LRU、预取、丢帧决策。 */

describe('raster-canvas — bandStats / nodata 掩膜', () => {
  it('min/max/mean 剔除 nodata', () => {
    const s = bandStats(
      [
        [1, 2, -9999],
        [3, -9999, 5],
      ],
      -9999,
    );
    expect(s.min).toBe(1);
    expect(s.max).toBe(5);
    expect(s.mean).toBeCloseTo(11 / 4);
    expect(s.validCount).toBe(4);
    expect(s.maskedCount).toBe(2);
  });

  it('nodata 缺省时仅剔除非有限值', () => {
    const s = bandStats([[1, NaN, Infinity, 0]]);
    expect(s.validCount).toBe(2);
    expect(s.maskedCount).toBe(2);
  });

  it('全掩膜 → 零值诚实形态', () => {
    const s = bandStats([[-9999, -9999]], -9999);
    expect(s.validCount).toBe(0);
    expect(s.min).toBe(0);
    expect(s.mean).toBe(0);
  });

  it('直方图 bins 计数守恒（nodata 剔除后）', () => {
    const grid = [Array.from({ length: 100 }, (_, i) => i)];
    const hist = bandHistogram(grid, 10);
    expect(hist).toHaveLength(10);
    expect(hist.reduce((acc, d) => acc + d.value, 0)).toBe(100);
  });
});

describe('raster-canvas — 色带与像素', () => {
  it('rampColor 端点钳制 + 中间插值', () => {
    expect(rampColor(0)).toEqual(COLOR_RAMP[0]);
    expect(rampColor(1)).toEqual(COLOR_RAMP[COLOR_RAMP.length - 1]);
    expect(rampColor(-1)).toEqual(COLOR_RAMP[0]);
    expect(rampColor(NaN)).toEqual(COLOR_RAMP[0]);
    const mid = rampColor(0.5);
    expect(mid.every((c) => c >= 0 && c <= 255)).toBe(true);
  });

  it('gridToRgba：nodata → alpha 0（掩膜），有效值不透明', () => {
    const { pixels } = gridToRgba(
      [
        [0, -9999],
        [1, 2],
      ],
      { nodata: -9999 },
    );
    // 像元 (1,0) 是 nodata → alpha 0。
    expect(pixels[(0 * 2 + 1) * 4 + 3]).toBe(0);
    expect(pixels[(1 * 2 + 1) * 4 + 3]).toBe(255);
  });

  it('gridToRgba：拉伸域生效（min→首色，max→末色）', () => {
    const { pixels } = gridToRgba([[0, 10]], { domain: { min: 0, max: 10 } });
    expect(pixels[0]).toBe(COLOR_RAMP[0][0]);
    expect(pixels[4]).toBe(COLOR_RAMP[COLOR_RAMP.length - 1][0]);
  });
});

describe('raster-timeline — LRU 与预取', () => {
  it('LruCache 容量有界 + get 刷新新鲜度', () => {
    const c = new LruCache<string, number>(2);
    c.put('a', 1);
    c.put('b', 2);
    c.get('a'); // a 变最新
    c.put('c', 3); // 淘汰 b
    expect(c.has('a')).toBe(true);
    expect(c.has('b')).toBe(false);
    expect(c.has('c')).toBe(true);
  });

  it('load 命中缓存不重复取，预取前方窗口', async () => {
    const fetched: number[] = [];
    const loader = createTimelineLoader({
      total: 48,
      prefetchAhead: 3,
      fetchSlice: async (i) => {
        fetched.push(i);
        return [[i]];
      },
    });
    const first = await loader.load(0);
    expect(first).toEqual([[0]]);
    // 预取 1..3 → fetches = 4
    await new Promise((r) => setTimeout(r, 0));
    expect(loader.stats().fetches).toBe(4);
    const again = await loader.load(0);
    expect(again).toEqual([[0]]);
    expect(loader.stats().fetches).toBe(4); // 命中缓存 + 在途去重
    expect(loader.has(3)).toBe(true);
    expect(fetched.filter((i) => i === 0)).toHaveLength(1);
  });

  it('越界 index reject；相同 index 并发共享同一 Promise', async () => {
    const loader = createTimelineLoader({ total: 4, fetchSlice: async (i) => [[i]] });
    await expect(loader.load(9)).rejects.toThrow('out of range');
    const [a, b] = [loader.load(1), loader.load(1)];
    expect(await a).toBe(await b);
  });

  it('shouldRenderFrame：重复触发跳过、超预算隔帧渲染', () => {
    expect(shouldRenderFrame(8, 16, 1)).toBe(false); // 同一显示帧内重复
    expect(shouldRenderFrame(8, 16, 10)).toBe(true); // 正常帧
    expect(shouldRenderFrame(40, 16, 10)).toBe(false); // 上次超预算 2 倍且间隔不足
    expect(shouldRenderFrame(40, 16, 20)).toBe(true); // 间隔够了恢复
  });

  it('formatStepLabel：ISO 截断到分钟，非 ISO 原样', () => {
    expect(formatStepLabel('2026-01-01T08:30:00Z')).toBe('2026-01-01 08:30');
    expect(formatStepLabel('step-42')).toBe('step-42');
  });
});

describe('query-forms — buildRequest schema 预校验', () => {
  const SESSION = 'sess-x';

  it('window：全空切片拒绝（对应后端 422）', () => {
    const r = buildRequest({ ...EMPTY_FORM, ref: 'ref:cube/a' }, SESSION);
    expect(r.error).toContain('至少');
  });

  it('window：负索引 / start>stop / 非整数都拒绝', () => {
    for (const bad of ['-1, 4', '8, 2', '1.5, 4']) {
      const r = buildRequest(
        { ...EMPTY_FORM, ref: 'ref:cube/a', window: { time: bad, y: '', x: '' } },
        SESSION,
      );
      expect(r.error).toBeTruthy();
    }
  });

  it('window：合法切片生成带 session_id 的请求体', () => {
    const r = buildRequest(
      { ...EMPTY_FORM, ref: 'ref:cube/a', window: { time: '0, 8', y: '', x: '0, 4' } },
      SESSION,
    );
    expect(r.request?.payload).toEqual({
      session_id: SESSION,
      ref: 'ref:cube/a',
      time: [0, 8],
      x: [0, 4],
    });
  });

  it('labeled：全空选择拒绝；model/scenario 进 body', () => {
    const empty = buildRequest({ ...EMPTY_FORM, mode: 'labeled', ref: 'ref:cube/a' }, SESSION);
    expect(empty.error).toContain('至少');
    const ok = buildRequest(
      {
        ...EMPTY_FORM,
        mode: 'labeled',
        ref: 'ref:cube/a',
        labeled: { ...EMPTY_FORM.labeled, model: 'ecmwf, gfs', scenario: 'rcp45', bbox: '116, 39, 117, 40' },
      },
      SESSION,
    );
    expect(ok.request?.payload).toMatchObject({
      model: ['ecmwf', 'gfs'],
      scenario: ['rcp45'],
      bbox: [116, 39, 117, 40],
    });
  });

  it('scan：bbox 四元校验 + max_rows 上界', () => {
    const bad = buildRequest(
      { ...EMPTY_FORM, mode: 'scan', scan: { ...EMPTY_FORM.scan, ref: 'ref:fabric-parquet/a', bbox: '1,2,3' } },
      SESSION,
    );
    expect(bad.error).toContain('bbox');
    const badRows = buildRequest(
      {
        ...EMPTY_FORM,
        mode: 'scan',
        scan: { ...EMPTY_FORM.scan, ref: 'r', bbox: '1,2,3,4', maxRows: 500_000 },
      },
      SESSION,
    );
    expect(badRows.error).toContain('max_rows');
  });

  it('rs：role 词表校验', () => {
    const r = buildRequest(
      {
        ...EMPTY_FORM,
        mode: 'rs',
        rs: { ...EMPTY_FORM.rs, role: 'thermal', time: '2026-01-01', source: 'x' },
      },
      SESSION,
    );
    expect(r.error).toContain('role');
  });

  it('parseSlice / parseBbox 边界', () => {
    expect(parseSlice('0, 4')).toEqual([0, 4]);
    expect(parseSlice('')).toBeNull();
    expect(parseBbox('116, 39, 117, 40')).toEqual([116, 39, 117, 40]);
    expect(parseBbox('a, b, c, d')).toBeNull();
  });
});
