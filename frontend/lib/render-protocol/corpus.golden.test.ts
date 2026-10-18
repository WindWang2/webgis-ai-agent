/**
 * RenderWork corpus golden 测试（TS 侧，Goal 03 ADR-0225）。
 *
 * 三重锁的 TS 半边：
 *   1. contract_manifest.json ≡ contract-manifest.ts 镜像（词表/预算零漂移）；
 *   2. render_work_golden.json 的 ack 场景 ≡ 真实 buildRenderApplyAck 输出
 *      语义断言（与 Python 侧 envelope 重放测试共用同一份 JSON —— 跨语言
 *      单一语料）。
 *
 * 语料再生：`python tests/cartography/render_work_corpus/generate.py`
 * （手改 JSON = 伪造一致性 —— 由双侧重放测试爆出）。
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  APPLY_ACK_MANIFEST,
  RENDER_PERF_MANIFEST,
  RENDER_WORK_MANIFEST,
  RENDERER_CAPABILITY_MANIFEST,
} from './contract-manifest';
import { buildRenderApplyAck } from './render-apply-ack';
import { parseRenderWorkEnvelope } from './render-work';
import type { MapSpec } from '@/lib/mapspec-compiler/types.generated';

// vitest cwd = frontend/；语料在仓库根 tests/cartography/render_work_corpus/
// （与 Python 侧共用同一份 JSON —— 跨语言单一语料）。
const CORPUS_DIR = join(
  process.cwd(),
  '..',
  'tests/cartography/render_work_corpus',
);
const manifest = JSON.parse(
  readFileSync(join(CORPUS_DIR, 'contract_manifest.json'), 'utf-8'),
);
const golden = JSON.parse(
  readFileSync(join(CORPUS_DIR, 'render_work_golden.json'), 'utf-8'),
);

describe('contract manifest triple-lock (JSON ≡ TS)', () => {
  it('render_work block', () => {
    expect(RENDER_WORK_MANIFEST.schema_version).toBe(manifest.render_work.schema_version);
    expect(RENDER_WORK_MANIFEST.max_data_dependencies).toBe(manifest.render_work.max_data_dependencies);
    expect(RENDER_WORK_MANIFEST.max_layer_work).toBe(manifest.render_work.max_layer_work);
    expect(RENDER_WORK_MANIFEST.max_component_work).toBe(manifest.render_work.max_component_work);
    expect(RENDER_WORK_MANIFEST.max_diagnostics).toBe(manifest.render_work.max_diagnostics);
    expect([...RENDER_WORK_MANIFEST.source_kinds].sort()).toEqual(manifest.render_work.source_kinds);
  });

  it('apply_ack block', () => {
    expect(APPLY_ACK_MANIFEST.schema_version).toBe(manifest.apply_ack.schema_version);
    expect([...APPLY_ACK_MANIFEST.statuses]).toEqual(manifest.apply_ack.statuses);
    expect([...APPLY_ACK_MANIFEST.entry_statuses]).toEqual(manifest.apply_ack.entry_statuses);
    expect([...APPLY_ACK_MANIFEST.reason_codes].sort()).toEqual(manifest.apply_ack.reason_codes);
    expect(APPLY_ACK_MANIFEST.max_layers).toBe(manifest.apply_ack.max_layers);
    expect(APPLY_ACK_MANIFEST.max_components).toBe(manifest.apply_ack.max_components);
    expect(APPLY_ACK_MANIFEST.max_sources).toBe(manifest.apply_ack.max_sources);
  });

  it('perf + capability blocks', () => {
    expect(RENDER_PERF_MANIFEST.schema_version).toBe(manifest.perf.schema_version);
    expect(RENDERER_CAPABILITY_MANIFEST.schema_version).toBe(manifest.capability.schema_version);
    expect({ ...RENDERER_CAPABILITY_MANIFEST.source_type_levels }).toEqual(
      manifest.capability.source_type_levels,
    );
    expect({ ...RENDERER_CAPABILITY_MANIFEST.feature_support }).toEqual(
      manifest.capability.feature_support,
    );
    // #1617 红线：未合并不虚报 supported。
    expect(manifest.capability.feature_support.semantic_zoom_representation).toBe(false);
  });
});

// ── golden 场景 → buildRenderApplyAck 输入重建 ─────────────────────────

interface AckScenarioInput {
  revision: number;
  mapIdle: boolean;
  pendingLayerIds: string[];
  pendingRemovedIds: string[];
  applied_layers: string[];
  applied_sources: Record<string, number | string | null>;
  mounted_component_ids: string[];
}

function reconstructApplied(
  specJson: Record<string, unknown>,
  input: AckScenarioInput,
): MapSpec {
  const specLayers = (specJson.layers as Array<Record<string, unknown>>) ?? [];
  const appliedLayerObjs = specLayers.filter(
    (l) => typeof l.id === 'string' && input.applied_layers.includes(l.id),
  );
  const sources: Record<string, unknown> = {};
  for (const [sid, rev] of Object.entries(input.applied_sources)) {
    sources[sid] = rev === null
      ? { type: 'geojson' }
      : { type: 'geojson', content_revision: rev };
  }
  return {
    version: '1.5',
    view: { center: [116, 39], zoom: 10 },
    sources,
    layers: appliedLayerObjs,
    layout: { components: (specJson.layout as { components?: unknown[] })?.components ?? [] },
  } as unknown as MapSpec;
}

function buildForScenario(name: string) {
  const sc = golden.scenarios[name];
  const specJson = sc.projector_input.mapspec as Record<string, unknown>;
  const desired = specJson as unknown as MapSpec;
  const ackInput = sc.ack_input as AckScenarioInput;
  const work = parseRenderWorkEnvelope(sc.envelope);
  return buildRenderApplyAck({
    spec: desired,
    applied: reconstructApplied(specJson, ackInput),
    revision: ackInput.revision,
    mapIdle: ackInput.mapIdle,
    pendingLayerIds: new Set(ackInput.pendingLayerIds),
    pendingRemovedIds: ackInput.pendingRemovedIds,
    mountedComponentIds: new Set(ackInput.mounted_component_ids),
    work,
  });
}

describe('golden scenario semantics (real builder)', () => {
  it('has 12 scenarios', () => {
    expect(Object.keys(golden.scenarios)).toHaveLength(12);
  });

  it('s01: ten layers fully applied + work echo + source applied', () => {
    const ack = buildForScenario('s01_ten_layer_full_apply');
    expect(ack.status).toBe('applied');
    expect(ack.layers).toHaveLength(10);
    expect(ack.layers.every((l) => l.status === 'applied')).toBe(true);
    expect(ack.work?.work_id).toBe(golden.scenarios.s01_ten_layer_full_apply.envelope.work_id);
    expect(ack.sources).toEqual([
      { source_id: 'src-main', status: 'applied', data_revision: '3' },
    ]);
  });

  it('s02: unsupported layer type → skipped + partial', () => {
    const ack = buildForScenario('s02_unsupported_layer_type_partial');
    const bad = ack.layers.find((l) => l.layer_id === 'bad-type');
    expect(bad).toMatchObject({ status: 'skipped', reason_code: 'unsupported_layer_type' });
    expect(ack.layers.find((l) => l.layer_id === 'ok-fill')?.status).toBe('applied');
    expect(ack.status).toBe('partial');
  });

  it('s03: style-only patch keeps source applied', () => {
    const ack = buildForScenario('s03_style_only_patch_preserves_data');
    expect(ack.status).toBe('applied');
    expect(ack.sources?.[0]).toMatchObject({ source_id: 'src-main', status: 'applied' });
  });

  it('s04: data revision lag → source stale + transaction partial', () => {
    const ack = buildForScenario('s04_same_ref_revision_changed');
    expect(ack.sources?.[0]).toEqual({
      source_id: 'src-main',
      status: 'stale',
      reason_code: 'stale_data_revision',
      data_revision: '3',
    });
    expect(ack.status).toBe('partial');
  });

  it('s05: user-hidden layer abstains (not failed)', () => {
    const ack = buildForScenario('s05_user_hidden_layer_abstains');
    expect(ack.layers.find((l) => l.layer_id === 'user-hid')).toBeUndefined();
    expect(ack.layers.find((l) => l.layer_id === 'kept')?.status).toBe('applied');
    expect(ack.status).toBe('applied');
  });

  it('s06: missing after settle → failed + closed reason code', () => {
    const ack = buildForScenario('s06_missing_after_apply_failed');
    expect(ack.layers[0]).toMatchObject({
      layer_id: 'ghost', status: 'failed', reason_code: 'missing_after_apply',
    });
    expect(ack.status).toBe('failed');
  });

  it('s07: unmountable component → failed with mounted basis', () => {
    const ack = buildForScenario('s07_component_unsupported_pending');
    const ok = ack.components.find((c) => c.component_id === 'comp-1');
    const bad = ack.components.find((c) => c.component_id === 'comp-2');
    expect(ok?.status).toBe('applied');
    expect(bad).toMatchObject({ status: 'failed', reason_code: 'missing_after_apply' });
    expect(ack.status).toBe('partial');
  });

  it('s08: representation layer applies; envelope has no restatement', () => {
    const ack = buildForScenario('s08_representation_passthrough');
    expect(ack.layers[0]).toMatchObject({ layer_id: 'rep-layer', status: 'applied' });
    expect(JSON.stringify(ack)).not.toContain('point_cluster');
  });

  it('s09: MVT/DEM sources applied without fabricated revisions', () => {
    const ack = buildForScenario('s09_mvt_vector_source');
    // deps 按源 id 排序（信封确定性）—— dem-y < tiles-x。
    expect(ack.sources).toEqual([
      { source_id: 'dem-y', status: 'applied' },
      { source_id: 'tiles-x', status: 'applied' },
    ]);
  });

  it('s10: fabric ref dependency reconciles by revision', () => {
    const ack = buildForScenario('s10_data_fabric_ref_dependency');
    expect(ack.sources?.[0]).toMatchObject({
      source_id: 'src-fab', status: 'applied', data_revision: '9',
    });
  });

  it('s11: reconnect replay identical to s01 (determinism)', () => {
    const a = buildForScenario('s01_ten_layer_full_apply');
    const b = buildForScenario('s11_reconnect_idempotent_replay');
    expect(b).toEqual(a);
  });

  it('s12: layer removal ACKs the surviving five', () => {
    const ack = buildForScenario('s12_layer_remove_and_invalidation');
    expect(ack.layers).toHaveLength(5);
    expect(ack.layers.every((l) => l.status === 'applied')).toBe(true);
    expect(ack.sources?.[0]).toMatchObject({ status: 'applied', data_revision: '4' });
  });
});
