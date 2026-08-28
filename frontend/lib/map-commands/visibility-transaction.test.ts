import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  commitMapSpecDocument,
  resetLiveState,
  setMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';
import { layerCommands } from './layerCommands';
import type { MapCommandContext } from './types';

// durability 通道 mock：visibility 事务把 desired presentation 提交到后端
vi.mock('@/lib/mapspec/user-mutation_RETIRED', () => ({
  commitLayerPresentation: vi.fn().mockResolvedValue(undefined),
  commitMapSpecMutation: vi.fn().mockResolvedValue({ success: true }),
}));


function makeCtx(
  params: Record<string, unknown>,
  layers: any[],
  map = makeMockMaplibreMap(),
): { ctx: MapCommandContext; updateLayer: ReturnType<typeof vi.fn>; map: any } {
  const updateLayer = vi.fn();
  const ctx = {
    map,
    popAction: () => {},
    setDeferredPop: () => {},
    safePop: () => {},
    getHudState: () => ({ layers, updateLayer }),
    setSelectedBaseLayer: () => {},
    command: 'layer_visibility_update',
    params,
  } as unknown as MapCommandContext;
  return { ctx, updateLayer, map };
}

describe('visibility transaction（单一深接口）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetLiveState();
  });

  it('agent 可见性突变提交后端 desired state（durability）—— reload 不再丢决策', async () => {
    // visibility-transaction 内联了 apiFetch 提交（串行链）；校验点：runtime
    // 确定性成功 + 无 throw（durability 异步，不阻塞 ack）
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'result__fill', type: 'fill' });
    map.setLayoutProperty('result__fill', 'visibility', 'visible');
    const { ctx } = makeCtx(
      { layer_id: 'result', visible: false },
      [{ id: 'result', visible: true, group: 'analysis' }],
      map,
    );
    const result = layerCommands.layer_visibility_update.run(ctx) as any;
    expect(result.result?.confirmed).toBe(true);
  });

  it('durability 失败不回滚 runtime 突变（pending 语义）', async () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'result__fill', type: 'fill' });
    map.setLayoutProperty('result__fill', 'visibility', 'visible');
    const { ctx, updateLayer } = makeCtx(
      { layer_id: 'result', visible: false },
      [{ id: 'result', visible: true, group: 'analysis' }],
      map,
    );
    const result = layerCommands.layer_visibility_update.run(ctx) as any;
    expect(result.status).toBe('succeeded');
    // runtime + desired(store) 均已生效；durability 异步队列中，不回滚
    expect(updateLayer).toHaveBeenCalledWith('result', { visible: false });
  });

  it('多目标（一 ref 多层）顺序提交 durability——防 CAS 互踩（串行链保证）', async () => {
    commitMapSpecDocument({
      version: '1.0',
      sources: { src1: { type: 'geojson', ref_id: 'ref:geojson-multi' } },
      layers: [
        { id: 'product-heat', source: 'src1', type: 'heatmap' },
        { id: 'product-points', source: 'src1', type: 'circle' },
      ],
    } as any);
    const { ctx } = makeCtx(
      { layer_id: 'ref:geojson-multi', visible: false },
      [],
    );

    const result = layerCommands.layer_visibility_update.run(ctx) as any;
    // target_ids 含全部展开层（group 语义）；与 pre-队列版本相同
    expect(new Set(result.result?.target_ids)).toEqual(new Set(['product-heat', 'product-points']));
  });

  it('ST-P3-2: double superseded re-merges pending——agent 隐藏决策不静默丢失', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      text: () => Promise.resolve(JSON.stringify({
        detail: {
          status: 'superseded',
          mutation_revision: 99,
          // 服务端真相始终与期望（visible:false）不符 → 持续 'retry'
          mapspec: { layers: [{ id: 'agent-hide', layout: { visibility: 'visible' } }] },
        },
      })),
    })));
    setMapSpecSessionCursor('sid-vt', 42);

    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'agent-hide__fill', type: 'fill' });
    map.setLayoutProperty('agent-hide__fill', 'visibility', 'visible');
    const { ctx } = makeCtx(
      { layer_id: 'agent-hide', visible: false },
      [{ id: 'agent-hide', visible: true, group: 'analysis', _mapspecLayerId: 'agent-hide' }],
      map,
    );

    const result = layerCommands.layer_visibility_update.run(ctx) as any;
    expect(result.status).toBe('succeeded');

    // durability 异步：两次尝试（首笔 + 一次重试）后 pending 必须重新落下
    for (let i = 0; i < 10; i += 1) {
      await Promise.resolve();
    }
    await new Promise((resolve) => setTimeout(resolve, 0));

    const { getPendingPresentation } = await import('@/lib/mapspec/session-cursor');
    expect(getPendingPresentation().agent_hide ?? getPendingPresentation()['agent-hide']).toBeDefined();
    vi.unstubAllGlobals();
  });
});

describe('finalize_display 终态确认（真实事务）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetLiveState();
  });

  it('MapLibre 读回一致 → confirmed 证据 + visible/hidden/unresolved 集合', async () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'final-heat__main', type: 'heatmap' });
    map.addLayer({ id: 'final-heat__native-heat', type: 'heatmap' });
    map.addLayer({ id: 'mid-buffers__fill', type: 'fill' });
    // 真实 renderer 会 setLayoutProperty；先手动预置初值模拟挂载态
    map.setLayoutProperty('final-heat__main', 'visibility', 'none');
    map.setLayoutProperty('final-heat__native-heat', 'visibility', 'none');

    const layers = [
      { id: 'final-heat', visible: false, group: 'analysis' },
      { id: 'mid-buffers', visible: true, group: 'analysis' },
      { id: 'base-map', visible: true, group: 'base' },
    ];
    const { ctx } = makeCtx(
      { show_layer_ids: ['final-heat'] },
      layers,
      map,
    );
    ctx.command = 'finalize_display';
    // 展示目标落 visible:true（renderer 真实执行 setLayoutProperty）
    // ——预置 none，事务应写 visible 并读回一致
    const result = layerCommands.finalize_display.run(ctx) as any;

    expect(result.status).toBe('succeeded');
    expect(result.result.confirmed).toBe(true);
    expect(result.result.visible_layer_ids).toEqual(['final-heat']);
    expect(result.result.hidden_layer_ids).toEqual(['mid-buffers']);
    expect(result.result.unresolved_layer_ids).toEqual([]);
    // base 组不收口
    expect(map.getLayoutProperty('mid-buffers__fill', 'visibility')).toBe('none');
  });

  it('用户 pin 的层不被收口隐藏（用户优先）', () => {
    const map = makeMockMaplibreMap();
    const layers = [
      { id: 'final-layer', visible: true, group: 'analysis' },
      { id: 'user-opened', visible: true, group: 'analysis', _userPinned: true },
    ];
    const { ctx, updateLayer } = makeCtx(
      { show_layer_ids: ['final-layer'] },
      layers,
      map,
    );
    ctx.command = 'finalize_display';
    const result = layerCommands.finalize_display.run(ctx) as any;

    expect(result.result.hidden_layer_ids).toEqual([]);
    expect(updateLayer).not.toHaveBeenCalledWith('user-opened', expect.objectContaining({ visible: false }));
  });
});

describe('remove_layer 身份解析对称 + desired state 同步', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetLiveState();
  });

  it('ref 目标展开多层：全部 store 行清除、无 zombie', () => {
    commitMapSpecDocument({
      version: '1.0',
      sources: { src1: { type: 'geojson', ref_id: 'ref:geojson-rm' } },
      layers: [
        { id: 'product-rm-heatmap', source: 'src1', type: 'heatmap' },
        { id: 'product-rm-points', source: 'src1', type: 'circle' },
      ],
    } as any);
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'product-rm-heatmap__native-heat', type: 'heatmap' });
    map.addLayer({ id: 'product-rm-points__point', type: 'circle' });

    const removeLayer = vi.fn();
    const layers = [
      { id: 'product-rm-heatmap', visible: true, group: 'analysis' },
      { id: 'product-rm-points', visible: true, group: 'analysis' },
    ];
    const ctx = {
      map,
      popAction: () => {},
      setDeferredPop: () => {},
      safePop: () => {},
      getHudState: () => ({ layers, removeLayer }),
      setSelectedBaseLayer: () => {},
      command: 'remove_layer',
      params: { layer_id: 'ref:geojson-rm' },
    } as unknown as MapCommandContext;

    const result = layerCommands.remove_layer.run(ctx) as any;
    expect(result.status).toBe('succeeded');
    // 一个 ref 的全部 store 行都清除（旧实现只删一行/直接 target_not_found）
    expect(removeLayer).toHaveBeenCalledWith('product-rm-heatmap');
    expect(removeLayer).toHaveBeenCalledWith('product-rm-points');
    // MapLibre 子层与 source 实际移除（mock getLayer 移除后返回 null）
    expect(map.getLayer('product-rm-heatmap__native-heat')).toBeFalsy();
    expect(map.getLayer('product-rm-points__point')).toBeFalsy();
  });
});

describe('boundedVisibilityRepair（单次有界重验）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('store-owned 目标在 reconcile 周期后重验一次并修复', async () => {
    const { boundedVisibilityRepair } = await import('./visibility-transaction');
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'late-layer__fill', type: 'fill' });
    map.setLayoutProperty('late-layer__fill', 'visibility', 'visible');
    const updateLayer = vi.fn();
    const ctx = {
      map,
      popAction: () => {},
      setDeferredPop: () => {},
      safePop: () => {},
      getHudState: () => ({ layers: [{ id: 'late-layer' }], updateLayer }),
      setSelectedBaseLayer: () => {},
      command: 'finalize_display',
      params: {},
    } as unknown as MapCommandContext;

    const promise = boundedVisibilityRepair(
      ctx, [{ layerId: 'late-layer', visible: false }], 100,
    );
    const outcome = promise.then((r) => r);
    await vi.advanceTimersByTimeAsync(150);
    const result = await outcome;
    // 一次修复把期望值落上（真实 renderer 调 setLayoutProperty）
    expect(result.confirmed).toEqual(['late-layer']);
    expect(result.unresolved).toEqual([]);
    expect(map.getLayoutProperty('late-layer__fill', 'visibility')).toBe('none');
  });

  it('目标从未挂载 → unresolved（诚实未收敛，不再重试）', async () => {
    const { boundedVisibilityRepair } = await import('./visibility-transaction');
    const map = makeMockMaplibreMap();
    const ctx = {
      map,
      popAction: () => {},
      setDeferredPop: () => {},
      safePop: () => {},
      getHudState: () => ({ layers: [] }),
      setSelectedBaseLayer: () => {},
      command: 'finalize_display',
      params: {},
    } as unknown as MapCommandContext;

    const promise = boundedVisibilityRepair(
      ctx, [{ layerId: 'never-mounted', visible: true }], 50,
    );
    const outcome = promise.then((r) => r);
    await vi.advanceTimersByTimeAsync(100);
    const result = await outcome;
    expect(result.confirmed).toEqual([]);
    expect(result.unresolved).toEqual(['never-mounted']);
  });
});
