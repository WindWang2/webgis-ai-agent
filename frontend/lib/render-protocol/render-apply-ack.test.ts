/**
 * RenderApplyAck builder 契约测试（F13，ADR-0214 D3）。
 *
 * 锁定：纯投影各态、user-wins pending 弃权、unsupported 显式、
 * settle 语义（mapIdle=false → pending 非失败）、stale 判定、有界截断。
 */
import { describe, expect, it } from 'vitest';
import type { MapSpec } from '@/lib/mapspec-compiler/types.generated';
import {
  APPLY_ACK_SCHEMA_VERSION,
  MAX_ACK_LAYERS,
  buildRenderApplyAck,
  isStaleApplyAck,
} from '@/lib/render-protocol/render-apply-ack';

function spec(overrides: Partial<MapSpec> = {}): MapSpec {
  return {
    version: '1.2',
    sources: {
      'src-a': { type: 'geojson' },
    },
    layers: [
      { id: 'l1', source: 'src-a', type: 'fill' },
      { id: 'l2', source: 'src-a', type: 'line' },
    ],
    layout: { components: [] },
    ...overrides,
  } as MapSpec;
}

function applied(ids: string[], withSources = true): MapSpec {
  return {
    version: '1.2',
    sources: withSources
      ? { 'src-a': { type: 'geojson' } }
      : {},
    layers: ids.map((id) => ({ id, source: 'src-a', type: 'fill' })),
  } as MapSpec;
}

describe('buildRenderApplyAck', () => {
  it('all applied when desired matches applied', () => {
    const ack = buildRenderApplyAck({
      spec: spec(),
      applied: applied(['l1', 'l2']),
      revision: 5,
      mapIdle: true,
    });
    expect(ack.schema_version).toBe(APPLY_ACK_SCHEMA_VERSION);
    expect(ack.mapspec_revision).toBe(5);
    expect(ack.status).toBe('applied');
    expect(ack.layers.map((e) => e.status)).toEqual(['applied', 'applied']);
    expect(ack.partial_apply.discarded).toBe(0);
  });

  it('missing after apply → failed with reason when settled', () => {
    const ack = buildRenderApplyAck({
      spec: spec(),
      applied: applied(['l1']),
      revision: 5,
      mapIdle: true,
    });
    expect(ack.status).toBe('partial');
    expect(ack.layers[1]).toEqual({
      layer_id: 'l2',
      status: 'failed',
      reason_code: 'missing_after_apply',
    });
  });

  it('missing layer is pending (not failed) when settle not idle', () => {
    const ack = buildRenderApplyAck({
      spec: spec(),
      applied: applied(['l1']),
      revision: 5,
      mapIdle: false,
    });
    expect(ack.layers[1]).toEqual({ layer_id: 'l2', status: 'pending' });
    expect(ack.status).toBe('partial'); // skipped/pending 不算，applied 缺一 → 有 failed? no:
  });

  it('no applied spec at all → transaction failed', () => {
    const ack = buildRenderApplyAck({
      spec: spec(),
      applied: null,
      revision: 5,
      mapIdle: true,
    });
    expect(ack.status).toBe('failed');
    expect(ack.layers.every((e) => e.status === 'failed')).toBe(true);
  });

  it('unsupported layer type → skipped with closed reason', () => {
    const s = spec({
      layers: [
        { id: 'l1', source: 'src-a', type: 'fill' },
        // 模拟未来词表扩展/旧前端收到新类型
        { id: 'lx', source: 'src-a', type: 'not-a-type' as never },
      ],
    });
    const ack = buildRenderApplyAck({
      spec: s,
      applied: applied(['l1', 'lx']),
      revision: 5,
      mapIdle: true,
    });
    expect(ack.layers[1]).toEqual({
      layer_id: 'lx',
      status: 'skipped',
      reason_code: 'unsupported_layer_type',
    });
    expect(ack.status).toBe('partial');
  });

  it('user-pending layers are excluded entirely (user-wins)', () => {
    const ack = buildRenderApplyAck({
      spec: spec(),
      applied: applied(['l1']),
      revision: 5,
      mapIdle: true,
      pendingLayerIds: new Set(['l2']),
    });
    expect(ack.layers.map((e) => e.layer_id)).toEqual(['l1']);
    // l2 缺席不得计为 failed（它被弃权，不参与事务结算）
    expect(ack.status).toBe('applied');
  });

  it('alias-aware pending match: custom- prefix and __ family', () => {
    const s = spec({
      layers: [
        { id: 'l1', source: 'src-a', type: 'fill' },
        { id: 'custom-l2', source: 'src-a', type: 'line' },
      ],
    });
    const ack = buildRenderApplyAck({
      spec: s,
      applied: applied(['l1', 'custom-l2']),
      revision: 5,
      mapIdle: true,
      // HUD 行 id（别名）上有 pending —— spec 层 custom-l2 必须被弃权
      pendingLayerIds: new Set(['l2']),
    });
    expect(ack.layers.map((e) => e.layer_id)).toEqual(['l1']);
  });

  it('pendingRemoved excludes layer', () => {
    const ack = buildRenderApplyAck({
      spec: spec(),
      applied: applied(['l1']),
      revision: 5,
      mapIdle: true,
      pendingRemovedIds: ['l2'],
    });
    expect(ack.layers.map((e) => e.layer_id)).toEqual(['l1']);
  });

  it('source unresolved when applied lacks the source id (background exempt)', () => {
    const s = spec({
      layers: [
        { id: 'l1', source: 'src-a', type: 'fill' },
        { id: 'bg', source: '', type: 'background' },
      ],
    });
    const ack = buildRenderApplyAck({
      spec: s,
      applied: { ...applied(['l1', 'bg']), sources: {} },
      revision: 5,
      mapIdle: true,
    });
    expect(ack.layers[0]).toEqual({
      layer_id: 'l1',
      status: 'failed',
      reason_code: 'source_unresolved',
    });
    expect(ack.layers[1].status).toBe('applied');
  });

  it('component ack: mounted applied, missing failed when chrome basis present', () => {
    const s = spec({
      layout: {
        components: [
          { id: 'c1', type: 'title' },
          { id: 'c2', type: 'legend', enabled: false },
          { id: 'c3', type: 'north_arrow' },
        ],
      },
    });
    const ack = buildRenderApplyAck({
      spec: s,
      applied: applied(['l1', 'l2']),
      revision: 5,
      mapIdle: true,
      mountedComponentIds: new Set(['c1']),
    });
    expect(ack.components).toEqual([
      { component_id: 'c1', status: 'applied' },
      { component_id: 'c3', status: 'failed', reason_code: 'missing_after_apply' },
    ]);
  });

  it('component ack: no chrome observation basis → pending, never fabricated failed', () => {
    const s = spec({
      layout: { components: [{ id: 'c1', type: 'title' }] },
    });
    const ack = buildRenderApplyAck({
      spec: s,
      applied: applied(['l1', 'l2']),
      revision: 5,
      mapIdle: true,
      mountedComponentIds: new Set(),
    });
    expect(ack.components).toEqual([{ component_id: 'c1', status: 'pending' }]);
    expect(ack.status).toBe('applied');
  });

  it('truncates at MAX_ACK_LAYERS with disclosure', () => {
    const many = spec({
      layers: Array.from({ length: MAX_ACK_LAYERS + 3 }, (_, i) => ({
        id: `l${i}`,
        source: 'src-a',
        type: 'fill' as const,
      })),
    });
    const ack = buildRenderApplyAck({
      spec: many,
      applied: null,
      revision: 5,
      mapIdle: true,
    });
    expect(ack.layers).toHaveLength(MAX_ACK_LAYERS);
    expect(ack.partial_apply.discarded).toBe(3);
  });

  it('reconcile error is advisory, truncated, and does not fabricate failures', () => {
    const ack = buildRenderApplyAck({
      spec: spec(),
      applied: applied(['l1', 'l2']),
      revision: 5,
      mapIdle: true,
      reconcileError: 'x'.repeat(500),
    });
    expect(ack.status).toBe('applied');
    expect(ack.reconcile_error).toHaveLength(160);
  });
});

describe('isStaleApplyAck', () => {
  const ack = buildRenderApplyAck({
    spec: spec(),
    applied: applied(['l1', 'l2']),
    revision: 7,
    mapIdle: true,
  });

  it('older revision is stale', () => {
    expect(isStaleApplyAck(ack, 9)).toBe(true);
  });

  it('same revision is not stale (idempotent replay)', () => {
    expect(isStaleApplyAck(ack, 7)).toBe(false);
  });

  it('newer ack is not stale', () => {
    expect(isStaleApplyAck(ack, 5)).toBe(false);
  });
});
