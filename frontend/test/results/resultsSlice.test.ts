import { describe, it, expect, beforeEach } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import { MAX_RESULTS } from '@/lib/store/slices/resultsSlice';
import type { LayerDescriptor, StepResultEvent } from '@/lib/results/types';

beforeEach(() => {
  useHudStore.getState().clearResults();
});

function hotspotStep(overrides: Partial<StepResultEvent> = {}): StepResultEvent {
  return {
    step_id: 'step-1',
    tool: 'hotspot_analysis',
    geojson_ref: 'ref:geojson-h1',
    result: {
      success: true,
      summary: 'Hot spots found.',
      bbox: [0, 0, 1, 1],
      data: { type: 'FeatureCollection', features: [], hot_spots_count: 5, cold_spots_count: 2, distance_band_m: 800 },
    },
    ...overrides,
  };
}

describe('resultsSlice — capture + bounded history', () => {
  it('captures a step_result into the registry and selects nothing by default', () => {
    const id = useHudStore.getState().captureStepResult(hotspotStep());
    expect(id).toBe('step-1');
    const results = useHudStore.getState().results;
    expect(results).toHaveLength(1);
    expect(results[0].tool).toBe('hotspot_analysis');
    expect(results[0].status).toBe('completed');
    expect(results[0].capturedAt).toBeTypeOf('number');
  });

  it('bounds the registry to MAX_RESULTS (newest kept, oldest dropped)', () => {
    for (let i = 0; i < MAX_RESULTS + 5; i++) {
      useHudStore.getState().captureStepResult(hotspotStep({ step_id: `step-${i}` }));
    }
    expect(useHudStore.getState().results).toHaveLength(MAX_RESULTS);
    // Newest first; the last captured should be at the head.
    expect(useHudStore.getState().results[0].id).toBe(`step-${MAX_RESULTS + 4}`);
  });

  it('dedups by id (re-emitted step_result updates in place, no duplicate)', () => {
    useHudStore.getState().captureStepResult(hotspotStep({ step_id: 'dup' }));
    useHudStore.getState().captureStepResult(hotspotStep({ step_id: 'dup' }));
    expect(useHudStore.getState().results).toHaveLength(1);
  });

  it('ignores propose_plan orchestration events', () => {
    const id = useHudStore.getState().captureStepResult({ tool: 'propose_plan', result: { success: true, plan_id: 'p1' } });
    expect(id).toBeUndefined();
    expect(useHudStore.getState().results).toHaveLength(0);
  });

  it('filters pure map-action tools (display_layer) that carry no inspectable result', () => {
    const id = useHudStore.getState().captureStepResult({ tool: 'display_layer', result: { success: true } });
    expect(id).toBeUndefined();
    expect(useHudStore.getState().results).toHaveLength(0);
  });

  it('strips heavy payload keys from the stored raw to bound memory', () => {
    useHudStore.getState().captureStepResult({
      step_id: 'heavy',
      tool: 'hotspot_analysis',
      geojson_ref: 'ref:geojson-h',
      result: { success: true, summary: 'ok', data: { type: 'FeatureCollection', features: new Array(1000) }, stats: { n: 1 } },
    });
    const raw = useHudStore.getState().results[0].raw as Record<string, unknown>;
    expect(raw.data).toBeUndefined(); // heavy FC stripped
    expect(raw.summary).toBe('ok'); // metadata retained
    expect((raw as any).stats).toEqual({ n: 1 }); // light scalars retained
  });

  it('captures tool_call args as input evidence for the matching step_result', () => {
    useHudStore.getState().captureToolCallArgs('buffer_analysis', JSON.stringify({ geojson: 'ref:geojson-in', distance: 300 }));
    useHudStore.getState().captureStepResult({
      step_id: 's-buf',
      tool: 'buffer_analysis',
      geojson_ref: 'ref:geojson-out',
      result: { success: true, summary: 'ok' },
    });
    const r = useHudStore.getState().results[0];
    expect(r.inputs[0].ref).toBe('ref:geojson-in');
    expect(r.parameters).toEqual(expect.arrayContaining([expect.objectContaining({ source: 'distance', value: 300 })]));
  });
});

describe('resultsSlice — enrich + select + clear', () => {
  it('enriches output metadata from the descriptor without fabricating CRS', () => {
    useHudStore.getState().captureStepResult(hotspotStep());
    const descriptor: LayerDescriptor = { feature_count: 42, geometry_types: ['Point'], bbox: [0, 0, 1, 1], estimated_bytes: 12345 };
    useHudStore.getState().enrichResultOutput('step-1', 'ref:geojson-h1', descriptor);
    const out = useHudStore.getState().results[0].outputs[0];
    expect(out.featureCount).toBe(42);
    expect(out.geometryTypes).toEqual(['Point']);
    expect(out.estimatedBytes).toBe(12345);
    // CRS must remain undefined (descriptor carries none) — never fabricated.
    expect(out.crs).toBeUndefined();
  });

  it('select / remove / clear manage selection + registry', () => {
    useHudStore.getState().captureStepResult(hotspotStep());
    useHudStore.getState().selectResult('step-1');
    expect(useHudStore.getState().selectedResultId).toBe('step-1');
    useHudStore.getState().removeResult('step-1');
    expect(useHudStore.getState().results).toHaveLength(0);
    expect(useHudStore.getState().selectedResultId).toBeNull();

    useHudStore.getState().captureStepResult(hotspotStep());
    useHudStore.getState().clearResults();
    expect(useHudStore.getState().results).toHaveLength(0);
    expect(useHudStore.getState().selectedResultId).toBeNull();
  });
});
