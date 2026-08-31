/**
 * Layer status derivation — closed vocabulary (Workspace V2 / Goal C2).
 *
 * Invariants:
 * - deriveLayerStatus is pure: same inputs → same status;
 * - the vocabulary is closed (7 values) and DERIVED from existing facts
 *   (store row / committed revision / latest render observation) — no
 *   parallel status field is written anywhere;
 * - statuses stay semantically distinct: hidden (desired off) ≠ stale
 *   (desired present, runtime diverged) ≠ expired (data gone).
 */
import { describe, expect, it, beforeEach } from 'vitest';
import type { Layer } from '@/lib/types/layer';
import {
  clearLayerEvidence,
  getLayerEvidence,
  recordLayerEvidence,
} from '@/lib/layers/render-evidence';
import { deriveLayerStatus } from '@/lib/layers/layer-status';
import {
  getRefSourceState,
  resetRefSourceCache,
} from '@/lib/mapspec/ref-source-resolver';

function layer(overrides: Partial<Layer> = {}): Layer {
  return {
    id: 'l1',
    name: 'Layer 1',
    type: 'vector',
    visible: true,
    opacity: 1,
    source: { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: null, properties: {} }] },
    ...overrides,
  };
}

describe('deriveLayerStatus', () => {
  beforeEach(() => {
    clearLayerEvidence();
    resetRefSourceCache();
  });

  it('healthy mounted layer is ready', () => {
    recordLayerEvidence(
      { layers: [{ runtime_store_id: 'l1', runtime_layer_count: 2, visible: true }] },
      5,
    );
    expect(
      deriveLayerStatus({ layer: layer(), evidence: getLayerEvidence('l1'), currentRevision: 5 }),
    ).toBe('ready');
  });

  it('desired-off layer is hidden regardless of evidence', () => {
    recordLayerEvidence(
      { layers: [{ runtime_store_id: 'l1', runtime_layer_count: 1, visible: false }] },
      5,
    );
    expect(
      deriveLayerStatus({
        layer: layer({ visible: false }),
        evidence: getLayerEvidence('l1'),
        currentRevision: 5,
      }),
    ).toBe('hidden');
  });

  it('spec revision ahead of observed generation is rendering (awaiting settle)', () => {
    recordLayerEvidence(
      { layers: [{ runtime_store_id: 'l1', runtime_layer_count: 1, visible: true }] },
      5,
    );
    expect(
      deriveLayerStatus({ layer: layer(), evidence: getLayerEvidence('l1'), currentRevision: 7 }),
    ).toBe('rendering');
  });

  it('desired present but unmounted is stale (runtime divergence, not data loss)', () => {
    recordLayerEvidence(
      { layers: [{ runtime_store_id: 'l1', runtime_layer_count: 0, visible: true }] },
      5,
    );
    expect(
      deriveLayerStatus({ layer: layer(), evidence: getLayerEvidence('l1'), currentRevision: 5 }),
    ).toBe('stale');
  });

  it('diverged style/source also derives stale', () => {
    recordLayerEvidence(
      {
        layers: [
          { runtime_store_id: 'l1', runtime_layer_count: 1, visible: true, style_converged: false },
        ],
      },
      5,
    );
    expect(
      deriveLayerStatus({ layer: layer(), evidence: getLayerEvidence('l1'), currentRevision: 5 }),
    ).toBe('stale');
  });

  it('bounded runtime error targeting the family is failed', () => {
    recordLayerEvidence(
      {
        layers: [{ runtime_store_id: 'l1', runtime_layer_count: 1, visible: true }],
        runtime_errors: [{ message: 'layer l1__circle failed', target: 'l1__circle' }],
      },
      5,
    );
    expect(
      deriveLayerStatus({ layer: layer(), evidence: getLayerEvidence('l1'), currentRevision: 5 }),
    ).toBe('failed');
  });

  it('ref-backed row without landed data is loading', () => {
    const l = layer({
      _refId: 'ref:geojson-1',
      source: { type: 'FeatureCollection', features: [] },
    });
    expect(getRefSourceState('ref:geojson-1')).toBe('unresolved');
    expect(deriveLayerStatus({ layer: l })).toBe('loading');
  });

  it('MVT-backed row with no inline features is NOT loading (tiles stream lazily)', () => {
    const l = layer({
      _refId: 'ref:geojson-2',
      _tileUrl: '/tiles/{z}/{x}/{y}.mvt',
      source: { type: 'FeatureCollection', features: [] },
    });
    expect(deriveLayerStatus({ layer: l })).toBe('ready');
  });

  it('no evidence and no failure signals degrades to ready (honest default)', () => {
    expect(deriveLayerStatus({ layer: layer() })).toBe('ready');
  });
});

describe('render evidence stash', () => {
  beforeEach(() => {
    clearLayerEvidence();
  });

  it('records only the latest observation per layer', () => {
    recordLayerEvidence(
      { layers: [{ runtime_store_id: 'a', runtime_layer_count: 1, visible: true }] },
      1,
    );
    recordLayerEvidence(
      { layers: [{ runtime_store_id: 'a', runtime_layer_count: 0, visible: true }] },
      2,
    );
    const evidence = getLayerEvidence('a');
    expect(evidence?.mounted).toBe(false);
    expect(evidence?.revision).toBe(2);
  });

  it('clears on session switch', () => {
    recordLayerEvidence(
      { layers: [{ runtime_store_id: 'a', runtime_layer_count: 1 }] },
      1,
    );
    clearLayerEvidence();
    expect(getLayerEvidence('a')).toBeNull();
  });

  it('stays bounded (latest observation capped)', () => {
    const layers = Array.from({ length: 100 }, (_, i) => ({
      runtime_store_id: `layer-${i}`,
      runtime_layer_count: 1,
    }));
    recordLayerEvidence({ layers }, 1);
    let tracked = 0;
    for (let i = 0; i < 100; i += 1) if (getLayerEvidence(`layer-${i}`)) tracked += 1;
    expect(tracked).toBeLessThanOrEqual(64);
  });

  it('never carries feature payloads (ids/booleans only)', () => {
    recordLayerEvidence(
      { layers: [{ runtime_store_id: 'a', runtime_layer_count: 1, features: new Array(150_000) }] },
      1,
    );
    const evidence = getLayerEvidence('a') as unknown as Record<string, unknown>;
    expect(Object.keys(evidence)).toEqual(
      expect.arrayContaining(['mounted', 'visible', 'converged', 'revision', 'at']),
    );
    expect(JSON.stringify(evidence).length).toBeLessThan(300);
  });
});
