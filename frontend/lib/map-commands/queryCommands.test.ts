import { describe, expect, it, vi, beforeEach } from 'vitest';
import { queryCommands } from './queryCommands';
import type { MapCommandContext } from './types';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';

function makeCtx(map: any, params: Record<string, unknown> = {}): MapCommandContext {
  return {
    map,
    popAction: () => {},
    setDeferredPop: () => {},
    safePop: () => {},
    getHudState: () => ({ setPendingSystemMessage: vi.fn() }),
    setSelectedBaseLayer: () => {},
    command: 'query_features',
    params,
  } as unknown as MapCommandContext;
}

/**
 * #535: 后端 query_map_features 发射 {command: 'query_features', location,
 * buffer_m, summary}，但前端目录在 #205-#208 迁移后从未登记该命令 —— 每次
 * 点探查都得到后端成功 + 前端 unknown_command 失败（prompt.py 还主动推荐）。
 * 这里实现真正的点探查：lng/lat 投影到像素，围绕 buffer_m 折算半径查询
 * 已渲染要素并如实汇报。
 */
describe('query_features command (#535 ghost command landed)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('requiredParams accepts the backend emission shape and rejects junk', () => {
    const { requiredParams } = queryCommands.query_features;
    // 后端形状: {location: [lng, lat], buffer_m, summary}（useMapBridge rest 展开为 params）
    expect(requiredParams({ location: [116.4, 39.9], buffer_m: 10 })).toBe(true);
    expect(requiredParams({ location: [116.4, 39.9] })).toBe(true);
    expect(requiredParams({})).toBe(false);
    expect(requiredParams({ buffer_m: 10 })).toBe(false);
    expect(requiredParams({ location: [116.4] })).toBe(false);
  });

  it('queries rendered features around the projected point and acks with the summary', () => {
    const map = makeMockMaplibreMap({
      // project() 可控：像素圆心固定 (128,128)
      project: () => ({ x: 128, y: 128 }),
      renderedFeatures: [
        { properties: { name: '人民大会堂' } },
        { properties: { title: '天安门' } },
        { properties: { title: '人民大会堂' } }, // 去重
      ],
    });
    const hud = { setPendingSystemMessage: vi.fn() };
    const ctx = makeCtx(map, { location: [116.4, 39.9], buffer_m: 50 });
    (ctx as any).getHudState = () => hud;

    const result = queryCommands.query_features.run(ctx);

    expect(result).toMatchObject({ status: 'succeeded' });
    // 查询以包围 buffer 的像素 bbox 执行（数值真值，非仅 ack）
    const call = map._calls.queryRenderedFeatures[0];
    expect(Array.isArray(call?.geometry)).toBe(true);
    const [x0, y0, x1, y1] = call.geometry as number[];
    expect(x0).toBeLessThan(128);
    expect(x1).toBeGreaterThan(128);
    expect(y0).toBeLessThan(128);
    expect(y1).toBeGreaterThan(128);
    // 汇总去重且如实
    expect(result).toMatchObject({
      result: { featureCount: 3, names: ['人民大会堂', '天安门'] },
    });
    const msg = hud.setPendingSystemMessage.mock.calls[0][0] as string;
    expect(msg).toContain('3 个已渲染要素');
    expect(msg).toContain('人民大会堂');
    expect(msg).toContain('天安门');
  });

  it('reports honestly when no features are found (count 0, no fabrication)', () => {
    const map = makeMockMaplibreMap({
      project: () => ({ x: 100, y: 100 }),
      renderedFeatures: [],
    });
    const hud = { setPendingSystemMessage: vi.fn() };
    const ctx = makeCtx(map, { location: [116.4, 39.9] });
    (ctx as any).getHudState = () => hud;

    const result = queryCommands.query_features.run(ctx);

    expect(result).toMatchObject({ status: 'succeeded', result: { featureCount: 0, names: [] } });
    const msg = hud.setPendingSystemMessage.mock.calls[0][0] as string;
    expect(msg).toContain('未查询到已渲染要素');
    expect(msg).not.toContain('0 个已渲染要素');
  });

  it('fails invalid_params for a non-numeric or malformed location', () => {
    const map = makeMockMaplibreMap();
    expect(queryCommands.query_features.run(makeCtx(map, {}))).toEqual({
      status: 'failed',
      error: 'invalid_params',
    });
    expect(queryCommands.query_features.run(makeCtx(map, { location: ['a', 'b'] }))).toEqual({
      status: 'failed',
      error: 'invalid_params',
    });
  });
});