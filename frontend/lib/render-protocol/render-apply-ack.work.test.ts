/**
 * buildRenderApplyAck 的 work echo / sources[] 对账扩展测试
 * （Goal 03，ADR-0225）。
 *
 * 锁定：同代信封才 echo（旧代绝不冒充）、per-source revision 对账
 * （applied / stale + data_revision 披露 / pending / failed）、
 * sourceStale 降级事务 status 为 partial、epoch echo。
 */
import { describe, expect, it } from 'vitest';
import { buildRenderApplyAck } from './render-apply-ack';
import type { MapSpec } from '@/lib/mapspec-compiler/types.generated';
import type { RenderWorkEnvelope } from './render-work';
import { parseRenderWorkEnvelope } from './render-work';

function spec(layers: Array<Record<string, unknown>>, sources: Record<string, unknown> = {}): MapSpec {
  return {
    version: '1.5',
    view: { center: [116, 39], zoom: 10 },
    sources,
    layers,
    layout: { components: [] },
  } as unknown as MapSpec;
}

function envelope(over: Record<string, unknown> = {}): RenderWorkEnvelope {
  const parsed = parseRenderWorkEnvelope({
    schema_version: 'render_work.v1',
    work_id: 'rw-5-fpabc',
    session_id: 'sess-A',
    mapspec_revision: 5,
    mapspec_fingerprint: 'fp-abc',
    observation_target_seq: 12,
    data_dependencies: [
      { source_id: 's1', kind: 'geojson', revision: '4' },
      { source_id: 's2', kind: 'geojson', revision: '2' },
    ],
    layer_work: [],
    component_work: [],
    invalidation: { data_changed: [], layers_added: [], layers_removed: [], components_changed: [] },
    required_capabilities: ['geojson'],
    diagnostics: [],
    work_fingerprint: 'wf-aaa',
    ...over,
  });
  return parsed as RenderWorkEnvelope;
}

const LAYERS = [
  { id: 'l1', type: 'circle', source: 's1', visible: true },
  { id: 'l2', type: 'fill', source: 's2', visible: true },
];

describe('work echo', () => {
  it('echoes same-generation envelope with style epoch', () => {
    const ack = buildRenderApplyAck({
      spec: spec(LAYERS, { s1: { type: 'geojson', content_revision: 4 }, s2: { type: 'geojson', content_revision: 2 } }),
      applied: spec(LAYERS, { s1: { type: 'geojson', content_revision: 4 }, s2: { type: 'geojson', content_revision: 2 } }),
      revision: 5,
      mapIdle: true,
      work: envelope(),
      styleEpoch: 2,
    });
    expect(ack.work).toEqual({
      work_id: 'rw-5-fpabc',
      work_fingerprint: 'wf-aaa',
      style_epoch: 2,
    });
  });

  it('never echoes stale-generation envelope', () => {
    const ack = buildRenderApplyAck({
      spec: spec(LAYERS),
      applied: spec(LAYERS),
      revision: 9, // 信封 revision=5 ≠ 9 —— 旧代信封不得冒充
      mapIdle: true,
      work: envelope(),
    });
    expect(ack.work).toBeUndefined();
    expect(ack.sources).toBeUndefined();
  });

  it('absent envelope → no work block (旧后端兼容)', () => {
    const withSources = spec(LAYERS, {
      s1: { type: 'geojson', content_revision: 4 },
      s2: { type: 'geojson', content_revision: 2 },
    });
    const ack = buildRenderApplyAck({
      spec: withSources, applied: withSources, revision: 5, mapIdle: true, work: null,
    });
    expect(ack.work).toBeUndefined();
    expect(ack.sources).toBeUndefined();
    expect(ack.status).toBe('applied');
  });
});

describe('source reconciliation', () => {
  const appliedFor = (s1: number, s2: number) => spec(LAYERS, {
    s1: { type: 'geojson', content_revision: s1 },
    s2: { type: 'geojson', content_revision: s2 },
  });

  it('marks source stale when data revision lags envelope', () => {
    const ack = buildRenderApplyAck({
      spec: appliedFor(4, 2),
      // 数据面实际在用 s1 旧代 3（envelope 声明 4）→ stale + 披露实际值。
      applied: appliedFor(3, 2),
      revision: 5,
      mapIdle: true,
      work: envelope(),
    });
    expect(ack.sources).toEqual([
      { source_id: 's1', status: 'stale', reason_code: 'stale_data_revision', data_revision: '3' },
      { source_id: 's2', status: 'applied', data_revision: '2' },
    ]);
    // 当前代真实风险 → 事务绝不宣称全量 applied。
    expect(ack.status).toBe('partial');
  });

  it('all revisions aligned → sources applied and status applied', () => {
    const ack = buildRenderApplyAck({
      spec: appliedFor(4, 2),
      applied: appliedFor(4, 2),
      revision: 5,
      mapIdle: true,
      work: envelope(),
    });
    expect(ack.status).toBe('applied');
    expect(ack.sources?.every((s) => s.status === 'applied')).toBe(true);
  });

  it('missing source in applied spec → failed after settle', () => {
    const ack = buildRenderApplyAck({
      spec: appliedFor(4, 2),
      applied: spec(LAYERS.slice(0, 1), { s1: { type: 'geojson', content_revision: 4 } }),
      revision: 5,
      mapIdle: true,
      work: envelope(),
    });
    const s2 = ack.sources?.find((s) => s.source_id === 's2');
    expect(s2?.status).toBe('failed');
    expect(s2?.reason_code).toBe('source_unresolved');
  });

  it('missing source before settle → pending (no false failure)', () => {
    const ack = buildRenderApplyAck({
      spec: appliedFor(4, 2),
      applied: spec(LAYERS.slice(0, 1), { s1: { type: 'geojson', content_revision: 4 } }),
      revision: 5,
      mapIdle: false,
      work: envelope(),
    });
    expect(ack.sources?.find((s) => s.source_id === 's2')?.status).toBe('pending');
  });

  it('no applied basis at all → sources pending (honest absence)', () => {
    const ack = buildRenderApplyAck({
      spec: appliedFor(4, 2),
      applied: null,
      revision: 5,
      mapIdle: true,
      work: envelope(),
    });
    expect(ack.sources?.every((s) => s.status === 'pending')).toBe(true);
  });

  it('source present without revision info → applied without claiming a revision', () => {
    const env = envelope({
      data_dependencies: [{ source_id: 's1', kind: 'geojson' }],
    });
    const ack = buildRenderApplyAck({
      spec: spec(LAYERS.slice(0, 1), { s1: { type: 'geojson' } }),
      applied: spec(LAYERS.slice(0, 1), { s1: { type: 'geojson' } }),
      revision: 5,
      mapIdle: true,
      work: env,
    });
    expect(ack.sources?.[0]).toEqual({ source_id: 's1', status: 'applied' });
  });

  it('bounds source entries and discloses overflow', () => {
    const many = Array.from({ length: 80 }, (_, i) => ({
      source_id: `s${i}`, kind: 'geojson', revision: '1',
    }));
    const env = envelope({ data_dependencies: many });
    const sourcesSpec = Object.fromEntries(
      Array.from({ length: 80 }, (_, i) => [`s${i}`, { type: 'geojson', content_revision: 1 }]),
    );
    const layerList = Array.from({ length: 80 }, (_, i) => ({
      id: `l${i}`, type: 'circle', source: `s${i}`, visible: true,
    }));
    const ack = buildRenderApplyAck({
      spec: spec(layerList, sourcesSpec),
      applied: spec(layerList, sourcesSpec),
      revision: 5,
      mapIdle: true,
      work: env,
    });
    expect(ack.sources?.length).toBe(64);
    // 80 层超 ACK 层预算（64）→ ≥16 披露（信封 deps 已被防御式解析截到
    // 64，源侧不重复计截断 —— envelope 有界纪律在 parse 层）。
    expect(ack.partial_apply.discarded).toBeGreaterThanOrEqual(16);
  });
});

describe('user-wins abstention interplay', () => {
  it('pending user layer stays out of ACK while work echo rides along', () => {
    const env = envelope({
      data_dependencies: [{ source_id: 's1', kind: 'geojson', revision: '4' }],
    });
    const layers = [LAYERS[0]];
    const ack = buildRenderApplyAck({
      spec: spec(layers, { s1: { type: 'geojson', content_revision: 4 } }),
      applied: spec(layers, { s1: { type: 'geojson', content_revision: 4 } }),
      revision: 5,
      mapIdle: true,
      pendingLayerIds: new Set(['l1']),
      work: env,
    });
    expect(ack.layers).toEqual([]);
    expect(ack.work?.work_id).toBe('rw-5-fpabc');
    expect(ack.status).toBe('applied');
  });
});
