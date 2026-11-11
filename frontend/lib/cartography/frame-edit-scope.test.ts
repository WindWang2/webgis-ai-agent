/**
 * G25 编辑范围路由测试 —— A03「仅改第二幅不动其余」+ scope 命名。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';

import {
  commitLayerOverrideForFrame,
  frameEditScopeLabel,
  resolveEditScope,
} from '@/lib/cartography/frame-edit-scope';
import { setMapSpecSessionCursor, commitMapSpecDocument } from '@/lib/mapspec/session-cursor';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

const mutations: Array<Record<string, unknown>> = [];

function specWithFrames(): MapSpec {
  return {
    view: { center: [104, 30], zoom: 9 },
    sources: {},
    layers: [{ id: 'layer-0', type: 'circle', source: 's0' }],
    layout: {
      frames: [
        { id: 'fp-1', title: '2020', enabled: true, layerOverrides: { 'layer-0': { opacity: 0.9 } } },
        { id: 'fp-2', title: '2022', enabled: true },
        { id: 'fp-3', title: '2024', enabled: true },
      ],
    },
  } as unknown as MapSpec;
}

beforeEach(() => {
  mutations.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('/mapspec/mutations')) {
        mutations.push(JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>);
        return new Response(JSON.stringify({ status: 'ok', mutation_revision: 13 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response('{}', { status: 200 });
    }),
  );
  setMapSpecSessionCursor('s-scope', 12, 'tok');
  commitMapSpecDocument(specWithFrames() as never, 12);
});

describe('resolveEditScope / frameEditScopeLabel（P5 scope 命名）', () => {
  it('网格开 + 有活动画幅 → 仅本幅；否则整个产品', () => {
    expect(resolveEditScope(true, 'fp-2')).toBe('frame_only');
    expect(resolveEditScope(false, 'fp-2')).toBe('product');
    expect(resolveEditScope(true, null)).toBe('product');
    expect(frameEditScopeLabel('frame_only')).toBe('仅本幅');
    expect(frameEditScopeLabel('product')).toBe('整个产品');
  });
});

describe('commitLayerOverrideForFrame（A03 仅本幅）', () => {
  it('override 落到目标帧 layerOverrides；其余帧逐字携带；根层不动', async () => {
    const out = await commitLayerOverrideForFrame('fp-2', {
      layerId: 'layer-0',
      override: { opacity: 0.5 },
    });
    expect(out).toBe('routed');
    expect(mutations).toHaveLength(1);
    const body = mutations[0] as {
      intent: string;
      expected_revision: number;
      frames: Array<{ id?: string; layerOverrides?: Record<string, { opacity?: number }> }>;
    };
    expect(body.intent).toBe('set_layout');
    expect(body.expected_revision).toBe(12); // CAS
    // 目标帧：override 合并写入（与既有 override 键合并）。
    expect(body.frames.find((f) => f.id === 'fp-2')?.layerOverrides?.['layer-0']).toEqual({ opacity: 0.5 });
    // 其余帧逐字携带 —— fp-1 的既有 override 不丢、fp-3 无 override。
    expect(body.frames.find((f) => f.id === 'fp-1')?.layerOverrides?.['layer-0']).toEqual({ opacity: 0.9 });
    expect(body.frames.find((f) => f.id === 'fp-3')?.layerOverrides).toBeUndefined();
  });

  it('同层既有 override 键合并（不整层覆写）', async () => {
    await commitLayerOverrideForFrame('fp-1', {
      layerId: 'layer-0',
      override: { visible: false },
    });
    const body = mutations[0] as { frames: Array<{ id?: string; layerOverrides?: Record<string, unknown> }> };
    expect(body.frames.find((f) => f.id === 'fp-1')?.layerOverrides?.['layer-0']).toEqual({
      opacity: 0.9,
      visible: false,
    });
  });

  it('帧缺席 → 不发 mutation（不写根级默认）', async () => {
    const out = await commitLayerOverrideForFrame('fp-x', {
      layerId: 'layer-0',
      override: { opacity: 0.5 },
    });
    expect(out).toBe('routed');
    expect(mutations).toHaveLength(0);
  });
});
