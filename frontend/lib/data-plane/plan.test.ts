import { describe, it, expect } from 'vitest';

import {
  buildLoadPlan,
  lodBandForZoom,
  FALLBACK_BYTES_PER_FEATURE,
  DEFAULT_VECTOR_TILE_THRESHOLD,
} from '@/lib/data-plane/plan';

/**
 * DataPlaneLoadPlan 契约测试（extreme-scale runtime v2）。
 *
 * planner 是纯函数：同输入恒同输出。decisions 排序确定性
 * （band 降序 → estFeatures 升序 → layerId 字典序），LOD band 只标注
 * 显示档位、绝不改变分析事实（分析侧走 ensureLayerData 全量通道）。
 */

const IN_VIEW: [number, number, number, number] = [116.0, 39.5, 116.9, 40.5];
const FAR_WEST: [number, number, number, number] = [100.0, 30.0, 101.0, 31.0];

function desc(over: Partial<NonNullable<Parameters<typeof buildLoadPlan>[0][number]['descriptor']>> = {}) {
  return {
    feature_count: 100,
    mvt_capable: true,
    estimated_bytes: undefined as number | undefined,
    bbox: IN_VIEW as [number, number, number, number] | null,
    ...over,
  };
}

describe('buildLoadPlan — empty / degenerate input', () => {
  it('empty layer list → empty plan, never over budget', () => {
    const plan = buildLoadPlan([], { bounds: IN_VIEW, zoom: 10 });
    expect(plan.decisions).toEqual([]);
    expect(plan.totalEstBytes).toBe(0);
    expect(plan.overBudget).toBe(false);
    expect(plan.hydrateCount).toBe(0);
  });

  it('no ref, no tile → local mode, nothing scheduled', () => {
    const plan = buildLoadPlan(
      [{ layerId: 'l1', visible: true }],
      { bounds: IN_VIEW, zoom: 10 },
    );
    expect(plan.decisions).toHaveLength(1);
    expect(plan.decisions[0].mode).toBe('local');
    expect(plan.decisions[0].reasonCode).toBe('local:no-ref');
    expect(plan.hydrateCount).toBe(0);
  });

  it('NaN zoom fails closed to overview band (not NaN propagation)', () => {
    expect(lodBandForZoom(Number.NaN)).toBe('overview');
    expect(lodBandForZoom(-3)).toBe('overview');
    expect(lodBandForZoom(Number.POSITIVE_INFINITY)).toBe('full');
  });
});

describe('buildLoadPlan — mode selection', () => {
  it('large mvt-capable ref with tile url → mvt mode', () => {
    const plan = buildLoadPlan(
      [{
        layerId: 'big',
        visible: true,
        hasTileUrl: true,
        descriptor: desc({ feature_count: DEFAULT_VECTOR_TILE_THRESHOLD + 1 }),
      }],
      { bounds: IN_VIEW, zoom: 12 },
    );
    expect(plan.decisions[0].mode).toBe('mvt');
    expect(plan.decisions[0].reasonCode).toBe('mvt:large-ref');
    expect(plan.hydrateCount).toBe(0);
  });

  it('tile url without mvt_capable descriptor never selects mvt', () => {
    const plan = buildLoadPlan(
      [{
        layerId: 'liar-tile',
        visible: true,
        hasTileUrl: true,
        descriptor: desc({ mvt_capable: false, feature_count: 50_000 }),
      }],
      { bounds: IN_VIEW, zoom: 12 },
    );
    expect(plan.decisions[0].mode).toBe('hydrate');
  });

  it('small hydrated ref → inline (no fetch scheduled)', () => {
    const plan = buildLoadPlan(
      [{
        layerId: 'small',
        visible: true,
        hydrated: true,
        refId: 'ref:a',
        descriptor: desc({ feature_count: 500 }),
      }],
      { bounds: IN_VIEW, zoom: 12 },
    );
    expect(plan.decisions[0].mode).toBe('inline');
    expect(plan.hydrateCount).toBe(0);
  });

  it('small unhydrated visible in-viewport ref → hydrate + interactive urgency', () => {
    const plan = buildLoadPlan(
      [{
        layerId: 'need',
        visible: true,
        hydrated: false,
        refId: 'ref:b',
        descriptor: desc({ feature_count: 800 }),
      }],
      { bounds: IN_VIEW, zoom: 12 },
    );
    const d = plan.decisions[0];
    expect(d.mode).toBe('hydrate');
    expect(d.urgency).toBe('interactive');
    expect(d.reasonCode).toBe('hydrate:viewport-visible');
    expect(plan.hydrateCount).toBe(1);
  });

  it('invisible hydrate candidate with deferOffViewport → deferred', () => {
    const plan = buildLoadPlan(
      [{
        layerId: 'hidden',
        visible: false,
        refId: 'ref:c',
        descriptor: desc(),
      }],
      { bounds: IN_VIEW, zoom: 12 },
      { deferOffViewport: true },
    );
    expect(plan.decisions[0].mode).toBe('deferred');
    expect(plan.decisions[0].urgency).toBe('idle');
  });

  it('default policy keeps off-viewport hydration schedulable (priority-ordered, not dropped)', () => {
    const plan = buildLoadPlan(
      [{
        layerId: 'far',
        visible: true,
        refId: 'ref:d',
        descriptor: desc({ bbox: FAR_WEST }),
      }],
      { bounds: IN_VIEW, zoom: 12 },
    );
    const d = plan.decisions[0];
    expect(d.mode).toBe('hydrate');
    expect(d.inViewport).toBe(false);
    expect(d.urgency).not.toBe('interactive');
    expect(d.reasonCode).toBe('hydrate:off-viewport');
  });
});

describe('buildLoadPlan — viewport geometry', () => {
  it('descriptor bbox intersecting viewport → inViewport true (touching edge inclusive)', () => {
    const plan = buildLoadPlan(
      [{
        layerId: 'edge',
        visible: true,
        refId: 'ref:e',
        descriptor: desc({ bbox: [116.9, 39.0, 120.0, 41.0] }),
      }],
      { bounds: IN_VIEW, zoom: 12 },
    );
    expect(plan.decisions[0].inViewport).toBe(true);
  });

  it('null bbox → conservative inViewport true + unknown-extent reason', () => {
    const plan = buildLoadPlan(
      [{
        layerId: 'unknown',
        visible: true,
        refId: 'ref:f',
        descriptor: desc({ bbox: null }),
      }],
      { bounds: IN_VIEW, zoom: 12 },
    );
    expect(plan.decisions[0].inViewport).toBe(true);
    expect(plan.decisions[0].reasonCode).toBe('hydrate:unknown-extent');
  });
});

describe('buildLoadPlan — LOD band (display-only annotation)', () => {
  it('zoom bands: <8 overview, <12 coarse, ≥12 full', () => {
    expect(lodBandForZoom(5)).toBe('overview');
    expect(lodBandForZoom(8)).toBe('coarse');
    expect(lodBandForZoom(11.5)).toBe('coarse');
    expect(lodBandForZoom(12)).toBe('full');
  });

  it('plan carries the band on every decision; band never flips mode to local/deferred', () => {
    const plan = buildLoadPlan(
      [{ layerId: 'l', visible: true, refId: 'ref:g', descriptor: desc() }],
      { bounds: IN_VIEW, zoom: 4 },
    );
    expect(plan.decisions[0].lodBand).toBe('overview');
    expect(plan.decisions[0].mode).toBe('hydrate');
  });
});

describe('buildLoadPlan — size estimation', () => {
  it('descriptor estimated_bytes wins when finite positive', () => {
    const plan = buildLoadPlan(
      [{
        layerId: 'known',
        visible: true,
        refId: 'ref:h',
        descriptor: desc({ estimated_bytes: 4096 }),
      }],
      { bounds: IN_VIEW, zoom: 12 },
    );
    expect(plan.decisions[0].estBytes).toBe(4096);
  });

  it('missing bytes → feature_count × fallback constant (deterministic heuristic)', () => {
    const plan = buildLoadPlan(
      [{
        layerId: 'guess',
        visible: true,
        refId: 'ref:i',
        descriptor: desc({ feature_count: 10, estimated_bytes: undefined }),
      }],
      { bounds: IN_VIEW, zoom: 12 },
    );
    expect(plan.decisions[0].estBytes).toBe(10 * FALLBACK_BYTES_PER_FEATURE);
    expect(plan.totalEstBytes).toBe(10 * FALLBACK_BYTES_PER_FEATURE);
  });

  it('no descriptor at all → zero-byte estimate, plan still produced', () => {
    const plan = buildLoadPlan(
      [{ layerId: 'bare', visible: true }],
      { bounds: IN_VIEW, zoom: 12 },
    );
    expect(plan.decisions[0].estBytes).toBe(0);
    expect(plan.decisions[0].estFeatures).toBe(0);
  });
});

describe('buildLoadPlan — budget', () => {
  it('total under budget → overBudget false', () => {
    const plan = buildLoadPlan(
      [{ layerId: 'a', visible: true, refId: 'ref:j', descriptor: desc({ estimated_bytes: 500 }) }],
      { bounds: IN_VIEW, zoom: 12 },
      { budgetBytes: 1000 },
    );
    expect(plan.overBudget).toBe(false);
  });

  it('total over budget → overBudget true (planner only reports; eviction is the ledger’s job)', () => {
    const plan = buildLoadPlan(
      [
        { layerId: 'a', visible: true, refId: 'ref:k', descriptor: desc({ estimated_bytes: 800 }) },
        { layerId: 'b', visible: false, refId: 'ref:l', descriptor: desc({ estimated_bytes: 700 }) },
      ],
      { bounds: IN_VIEW, zoom: 12 },
      { budgetBytes: 1000 },
    );
    expect(plan.overBudget).toBe(true);
    expect(plan.totalEstBytes).toBe(1500);
  });

  it('no budgetBytes → never over budget', () => {
    const plan = buildLoadPlan(
      [{ layerId: 'a', visible: true, refId: 'ref:m', descriptor: desc({ estimated_bytes: 1e12 }) }],
      { bounds: IN_VIEW, zoom: 12 },
    );
    expect(plan.overBudget).toBe(false);
  });
});

describe('buildLoadPlan — deterministic ordering', () => {
  const layers = [
    { layerId: 'z-hidden-far', visible: false, refId: 'ref:1', descriptor: desc({ feature_count: 10, bbox: FAR_WEST }) },
    { layerId: 'a-visible-view', visible: true, refId: 'ref:2', descriptor: desc({ feature_count: 3000 }) },
    { layerId: 'm-visible-view-small', visible: true, refId: 'ref:3', descriptor: desc({ feature_count: 50 }) },
    { layerId: 'b-visible-view-small', visible: true, refId: 'ref:4', descriptor: desc({ feature_count: 50 }) },
    { layerId: 'y-visible-far', visible: true, refId: 'ref:5', descriptor: desc({ feature_count: 20, bbox: FAR_WEST }) },
  ];

  it('order: visible+in-viewport first (small before large), then visible off-viewport, then hidden', () => {
    const plan = buildLoadPlan(layers, { bounds: IN_VIEW, zoom: 12 });
    expect(plan.decisions.map((d) => d.layerId)).toEqual([
      'b-visible-view-small',
      'm-visible-view-small',
      'a-visible-view',
      'y-visible-far',
      'z-hidden-far',
    ]);
  });

  it('same input twice → identical plan (pure/deterministic, no hidden state)', () => {
    const one = buildLoadPlan(layers, { bounds: IN_VIEW, zoom: 12 });
    const two = buildLoadPlan(layers, { bounds: IN_VIEW, zoom: 12 });
    expect(two).toEqual(one);
  });

  it('priority is monotone with output order (non-increasing)', () => {
    const plan = buildLoadPlan(layers, { bounds: IN_VIEW, zoom: 12 });
    for (let i = 1; i < plan.decisions.length; i += 1) {
      expect(plan.decisions[i - 1].priority).toBeGreaterThanOrEqual(plan.decisions[i].priority);
    }
  });
});

describe('buildLoadPlan — custom threshold option', () => {
  it('vectorTileThreshold option overrides the 5000 default', () => {
    const plan = buildLoadPlan(
      [{
        layerId: 'mid',
        visible: true,
        hasTileUrl: true,
        refId: 'ref:n',
        descriptor: desc({ feature_count: 6000 }),
      }],
      { bounds: IN_VIEW, zoom: 12 },
      { vectorTileThreshold: 10_000 },
    );
    expect(plan.decisions[0].mode).toBe('hydrate');
  });
});
