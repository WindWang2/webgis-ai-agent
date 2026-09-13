import { describe, it, expect, vi } from 'vitest';
import {
  enterHighDpiRender,
  detectRasterSourceIds,
  MapIdleTimeoutError,
  waitForMapIdle,
  DEGRADE_REPAINT_TIMEOUT_MS,
  EXPORT_IDLE_TIMEOUT_MS,
  idleTimeoutWithinWatchdog,
  type HighDpiMapLike,
} from './highdpi';

/** 可编排 idle 行为的假 map：每次 once('idle') 由脚本决定是否触发/何时触发。 */
function makeMap(opts: {
  pixelRatio?: number;
  /** 每次 once('idle') 的响应：'fire'（立即）/ 'never'（永不）/ 毫秒延迟。 */
  idleScript?: Array<'fire' | 'never' | number>;
  sources?: Record<string, { type?: string }>;
} = {}) {
  const calls: { setPixelRatio: number[] } = { setPixelRatio: [] };
  let idleCall = 0;
  const map = {
    getPixelRatio: vi.fn(() => opts.pixelRatio ?? 1),
    setPixelRatio: vi.fn((r: number) => {
      calls.setPixelRatio.push(r);
    }),
    once: vi.fn((_e: 'idle', cb: () => void) => {
      const action = opts.idleScript?.[Math.min(idleCall, (opts.idleScript?.length ?? 1) - 1)] ?? 'fire';
      idleCall += 1;
      if (action === 'fire') cb();
      else if (typeof action === 'number') setTimeout(cb, action);
      // 'never' → 不触发（等待方超时）
    }),
    getStyle: vi.fn(() => ({ sources: opts.sources })),
  };
  return { map: map as unknown as HighDpiMapLike & Record<string, ReturnType<typeof vi.fn>>, calls };
}

describe('waitForMapIdle（#527 有界等待）', () => {
  it('idle 触发 → resolve', async () => {
    const { map } = makeMap();
    await expect(waitForMapIdle(map, 50)).resolves.toBeUndefined();
  });

  it('idle 永不触发 → MapIdleTimeoutError', async () => {
    const { map } = makeMap({ idleScript: ['never'] });
    await expect(waitForMapIdle(map, 20)).rejects.toBeInstanceOf(MapIdleTimeoutError);
  });
});

describe('detectRasterSourceIds', () => {
  it('仅列 raster 源；style 缺席 → 空数组（测试环境容错）', () => {
    const withRaster = makeMap({
      sources: { osm: { type: 'raster' }, pts: { type: 'geojson' } },
    });
    expect(detectRasterSourceIds(withRaster.map)).toEqual(['osm']);
    const bare = makeMap();
    (bare.map.getStyle as ReturnType<typeof vi.fn>).mockReturnValue(undefined);
    expect(detectRasterSourceIds(bare.map)).toEqual([]);
  });
});

describe('enterHighDpiRender（ADR-0157 P1）', () => {
  it('dpi ≤ 96 → skipped：不触碰 pixelRatio', async () => {
    const { map, calls } = makeMap();
    const r = await enterHighDpiRender(map, 96);
    expect(r.mode).toBe('skipped');
    expect(r.degradations).toEqual([]);
    expect(calls.setPixelRatio).toEqual([]);
  });

  it('高 DPI + idle 正常 → rerendered（矢量细节真重渲染路径）', async () => {
    const { map, calls } = makeMap({ pixelRatio: 1 });
    const r = await enterHighDpiRender(map, 300, 100);
    expect(r.mode).toBe('rerendered');
    expect(r.degradations).toEqual([]);
    expect(calls.setPixelRatio).toEqual([300 / 96]);
  });

  it('高 DPI + 栅格源在场 → 附 raster_tile_detail_limited_highdpi 信息披露', async () => {
    const { map } = makeMap({
      sources: { osm: { type: 'raster' }, vec: { type: 'geojson' } },
    });
    const r = await enterHighDpiRender(map, 300, 100);
    expect(r.mode).toBe('rerendered');
    expect(r.degradations).toHaveLength(1);
    expect(r.degradations[0].code).toBe('raster_tile_detail_limited_highdpi');
    expect(r.degradations[0].detail).toContain('osm');
  });

  it('idle 超时 → 降级 degraded-native（恢复原始比率 + 重绘后返回诊断码）', async () => {
    // 第一次 idle 永不触发（高 DPI 重渲染卡死），降级回退后的重绘 idle 立即触发。
    const { map, calls } = makeMap({ pixelRatio: 1, idleScript: ['never', 'fire'] });
    const r = await enterHighDpiRender(map, 300, 30);
    expect(r.mode).toBe('degraded-native');
    expect(r.degradations).toHaveLength(1);
    expect(r.degradations[0].code).toBe('highdpi_rerender_timeout_degraded');
    // 序列：升到目标比率 → 超时 → 恢复原始比率（调用方 finally 不再重复升高）。
    expect(calls.setPixelRatio).toEqual([300 / 96, 1]);
  });

  it('降级回退后重绘也超时 → 类型化失败（无画面可捕获，不伪造产物）', async () => {
    const { map, calls } = makeMap({ pixelRatio: 2, idleScript: ['never', 'never'] });
    await expect(enterHighDpiRender(map, 300, 20)).rejects.toThrow(/未完成重绘/);
    // 比率已恢复到进入前（2），不会滞留在高 DPI 泄漏态。
    expect(calls.setPixelRatio).toEqual([300 / 96, 2]);
  });

  it('非 idle 超时异常原样上抛（恢复责任在调用方 finally）', async () => {
    const { map } = makeMap();
    (map.once as ReturnType<typeof vi.fn>).mockImplementation(() => {
      throw new Error('boom');
    });
    await expect(enterHighDpiRender(map, 300, 100)).rejects.toThrow('boom');
  });

  it('截止常量语义：默认 30s / 降级重绘 3s（契约 pin）', () => {
    expect(EXPORT_IDLE_TIMEOUT_MS).toBe(30_000);
    expect(DEGRADE_REPAINT_TIMEOUT_MS).toBe(3_000);
  });
});

describe('idleTimeoutWithinWatchdog（review：降级链路落在队列看门狗内）', () => {
  it('30s 看门狗 → idle 截止 20s（最坏链路 20+3+5=28s < 30s，降级来得及完成）', () => {
    expect(idleTimeoutWithinWatchdog(30_000)).toBe(20_000);
  });

  it('极小看门狗 → 5s 下限（低于此值 idle 等待失去意义，直接走降级也比挂死好）', () => {
    expect(idleTimeoutWithinWatchdog(6_000)).toBe(5_000);
    expect(idleTimeoutWithinWatchdog(0)).toBe(5_000);
  });
});
