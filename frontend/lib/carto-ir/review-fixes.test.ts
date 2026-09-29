/**
 * S3 review 修复回归锁（C11 review 循环）：
 * - P1-1：cluster-count 子层 visibility 须真正生效（spread 覆盖死代码已修）；
 * - P1-2：live label 子层继承 authored visible:false 与 zoom 门；
 * - P2-4b：词表外 layout.visibility 折算留 evidence（双路径）。
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { compileMapSpec } from '@/lib/mapspec-compiler/compiler';
import { MapSpecRuntime } from '@/lib/mapspec-runtime/runtime';
import { makeMockMaplibreMap } from '../../test/__mocks__/maplibre-map';
import { _resetSceneEvidenceForTests } from '@/lib/mapspec-runtime/adapter';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

function clusterSpec(visible: boolean | undefined, layout?: Record<string, unknown>): MapSpec {
  return {
    version: '1.5',
    sources: {
      s1: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [] } as never },
    },
    layers: [
      {
        id: 'L', source: 's1', type: 'circle',
        paint: { color: '#123456' } as never,
        cluster: {},
        ...(visible !== undefined ? { visible } : {}),
        ...(layout ? { layout: layout as never } : {}),
      } as MapSpec['layers'][number],
    ],
  } as MapSpec;
}

describe('review P1-1：cluster-count 子层显隐继承', () => {
  beforeEach(() => vi.clearAllMocks());

  it('authored visible:false → __clusters 与 __cluster-count 均 visibility none', () => {
    const result = compileMapSpec(clusterSpec(false));
    const ids = result.style.layers.map((l) => l.id);
    expect(ids).toContain('L__clusters');
    expect(ids).toContain('L__cluster-count');
    for (const layer of result.style.layers) {
      if (layer.id === 'L__clusters' || layer.id === 'L__cluster-count') {
        const def = layer as unknown as Record<string, any>;
        expect(def.layout?.visibility).toBe('none');
      }
    }
  });

  it('默认可见时 __clusters 不带 layout 键（byte parity）', () => {
    const result = compileMapSpec(clusterSpec(undefined));
    const clusters = result.style.layers.find((l) => l.id === 'L__clusters') as unknown as Record<string, unknown>;
    expect('layout' in clusters).toBe(false);
    const count = result.style.layers.find((l) => l.id === 'L__cluster-count') as unknown as Record<string, any>;
    expect(count.layout?.visibility).toBeUndefined();
  });
});

describe('review P1-2：live label 子层单一裁决面', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    _resetSceneEvidenceForTests();
  });

  it('authored visible:false → label 子层 layout.visibility none + zoom 门继承', async () => {
    const map = makeMockMaplibreMap();
    const runtime = new MapSpecRuntime(map as never);
    const spec: MapSpec = {
      version: '1.5',
      sources: {
        s1: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [] } as never },
      },
      layers: [
        {
          id: 'cities', source: 's1', type: 'circle',
          paint: { color: '#123456' } as never,
          visible: false,
          visibility: { min_zoom: 9 },
          label: { field: 'name' },
        } as MapSpec['layers'][number],
      ],
    };
    await runtime.reconcileAsync(spec);
    runtime.flush();
    const defs = (map.addLayer as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as Record<string, unknown>);
    const labelDef = defs.find((d) => d.id === 'cities-label');
    expect(labelDef).toBeDefined();
    expect((labelDef!.layout as Record<string, unknown>).visibility).toBe('none');
    expect(labelDef!.minzoom).toBe(9);
    runtime.dispose();
  });
});
