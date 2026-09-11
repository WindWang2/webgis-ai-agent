/**
 * full-master-audit-2026-09 Batch 2 前端修复回归测试。
 *
 * - #1200（C-1）：agent remove_layer 的 durability POST 必须实际发出
 *   （enqueuedSessionId 传参；此前守卫把 undefined 判为会话切换 → 静默 reflected）。
 * - #1201（C-2）：durability 响应 await 后会话复核 —— 旧会话迟到响应不得
 *   写新会话游标（visibility/component-lifecycle 两通道）。
 * - #1202（C-3）：agent reorder_layer 对 spec 承载层提交 reorder_layers。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  commitMapSpecDocument,
  getCommittedMapSpec,
  getMapSpecSessionCursor,
  resetLiveState,
  setMapSpecRevision,
  setMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';
import { layerCommands } from './layerCommands';
import type { MapCommandContext } from './types';

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: true,
    status,
    headers: new Map([['content-type', 'application/json']]),
    json: async () => body,
  };
}

function makeCtx(
  command: string,
  params: Record<string, unknown>,
  layers: any[],
  map = makeMockMaplibreMap(),
  extraHud: Record<string, unknown> = {},
): MapCommandContext {
  return {
    map,
    popAction: () => {},
    setDeferredPop: () => {},
    safePop: () => {},
    getHudState: () => ({ layers, ...extraHud }),
    setSelectedBaseLayer: () => {},
    command,
    params,
  } as unknown as MapCommandContext;
}

async function drain(times = 12): Promise<void> {
  for (let i = 0; i < times; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

beforeEach(async () => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset();
  await new Promise((resolve) => setTimeout(resolve, 0));
  resetLiveState();
  useHudStore.getState().clearLayers();
  setMapSpecSessionCursor('sess-A', 5);
});

describe('#1200 agent remove_layer durability POST 实际发出', () => {
  it('spec 承载层删除后向 mapspec/mutations 发出 intent=remove_layer', async () => {
    commitMapSpecDocument({
      version: '1.0',
      sources: { s1: { type: 'geojson', ref_id: 'ref:x1' } },
      layers: [{ id: 'spec-layer-a', source: 's1', type: 'circle' }],
    } as any, 5);

    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'spec-layer-a__point', type: 'circle' });
    const removeLayer = vi.fn();
    const layers = [
      { id: 'spec-layer-a', visible: true, group: 'analysis', _mapspecLayerId: 'spec-layer-a' },
    ];
    const ctx = makeCtx('remove_layer', { layer_id: 'spec-layer-a' }, layers, map, { removeLayer });

    fetchMock.mockImplementationOnce(() => Promise.resolve(jsonResponse({
      success: true,
      mutation_revision: 6,
      mapspec: { version: '1.0', sources: {}, layers: [] },
    })));

    const result = layerCommands.remove_layer.run(ctx) as any;
    expect(result.status).toBe('succeeded');
    await drain();

    const calls = fetchMock.mock.calls.filter(([url]) => String(url).includes('mapspec/mutations'));
    expect(calls.length).toBeGreaterThanOrEqual(1);
    const body = JSON.parse((calls[0][1] as RequestInit).body as string);
    expect(body.intent).toBe('remove_layer');
    expect(body.layer_id).toBe('spec-layer-a');
    expect(body.expected_revision).toBe(5);
  });
});

describe('#1201 durability 响应 await 后会话复核', () => {
  it('visibility durability 响应迟到（已切会话）不污染新会话游标', async () => {
    let release!: (value: unknown) => void;
    fetchMock.mockImplementationOnce(() => new Promise((resolve) => {
      release = resolve;
    }));

    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'vis-layer__fill', type: 'fill' });
    map.setLayoutProperty('vis-layer__fill', 'visibility', 'visible');
    const layers = [{ id: 'vis-layer', visible: true, group: 'analysis' }];
    const ctx = makeCtx('layer_visibility_update', { layer_id: 'vis-layer', visible: false }, layers, map);

    const result = layerCommands.layer_visibility_update.run(ctx) as any;
    expect(result.result?.confirmed).toBe(true);
    await drain(4);

    // POST 在飞时切到会话 B（游标重置 revision=0、committed=null）
    setMapSpecSessionCursor('sess-B', 0);
    setMapSpecRevision(0);

    release(jsonResponse({
      success: true,
      mutation_revision: 6,
      mapspec: { version: '1.0', sources: {}, layers: [{ id: 'A-层' }] },
    }));
    await drain();

    expect(getMapSpecSessionCursor().revision).toBe(0);
    expect(getCommittedMapSpec()).toBeNull();
  });
});

describe('#1202 agent reorder_layer 提交 reorder_layers', () => {
  it('store 重排后对 spec 承载层发出 intent=reorder_layers POST', async () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'la__point', type: 'circle' });
    map.addLayer({ id: 'lb__point', type: 'circle' });
    const reorderLayers = vi.fn();
    const layers = [
      { id: 'la', visible: true, group: 'analysis', _mapspecLayerId: 'la' },
      { id: 'lb', visible: true, group: 'analysis', _mapspecLayerId: 'lb' },
    ];
    const ctx = makeCtx('reorder_layer', { layer_id: 'lb', position: 'top' }, layers, map, { reorderLayers });

    fetchMock.mockImplementationOnce(() => Promise.resolve(jsonResponse({
      success: true,
      mutation_revision: 6,
      mapspec: { version: '1.0', sources: {}, layers: [] },
    })));

    const result = layerCommands.reorder_layer.run(ctx) as any;
    expect(result.status).toBe('succeeded');
    expect(result.result?.store_updated).toBe(true);
    expect(reorderLayers).toHaveBeenCalled();
    await drain();

    const calls = fetchMock.mock.calls.filter(([url]) => String(url).includes('mapspec/mutations'));
    expect(calls.length).toBeGreaterThanOrEqual(1);
    const body = JSON.parse((calls[0][1] as RequestInit).body as string);
    expect(body.intent).toBe('reorder_layers');
    expect(body.layer_ids[0]).toBe('lb');
  });
});
