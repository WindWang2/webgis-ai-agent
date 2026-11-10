import { describe, expect, it } from 'vitest';
import {
  initialState, phaseLabelKey, reduceRasterAuthoring,
} from './state';
import type { RasterExpressionDraft, RasterProbeResult, RasterReceipt } from './contracts';

const draft: RasterExpressionDraft = {
  schema_version: 'raster_expression.v1', mode: 'single', bands: [1],
  autoStretch: false, stretchMin: 0, stretchMax: 10, perChannel: [[0, 10]],
  colormap: 'viridis', opacity: 1, resampling: 'bilinear',
  classification: 'continuous',
};

const receipt = {
  success: true, committed: true, state: 'committed',
  layer_id: 'l1', source_id: 's1', ref_id: 'ref-1', mapspec_revision: 3,
  data_revision: 'fp:1', expression_version: 'raster_expression.v1',
  style_id: 'abc', statistics_provenance: {
    source: 'manual', method: 'manual', sample_count: null, approximate: false,
  },
  expression: { ...draft, stretch: { min: 0, max: 10 }, style_id: 'abc', data_revision: 'fp:1' },
  legend: { type: 'continuous', stretch_provenance: { source: 'manual', method: 'manual', sample_count: null, approximate: false } },
  expression_statement: {},
} as unknown as RasterReceipt;

const probe = (id: string) => ({ points: [{ id }] } as unknown as RasterProbeResult);

describe('G20 authoring state machine', () => {
  it('separates drag preview / pending / committed / failed', () => {
    let s = initialState();
    s = reduceRasterAuthoring(s, { type: 'descriptor-loaded', draft, committed: null });
    expect(s.phase).toBe('idle');
    s = reduceRasterAuthoring(s, { type: 'draft-changed', draft: { ...draft, opacity: 0.5 } });
    expect(s.phase).toBe('preview');
    expect(phaseLabelKey(s)).toBe('rasterAuthoring.state.preview');
    s = reduceRasterAuthoring(s, { type: 'commit-start' });
    expect(s.phase).toBe('pending');
    s = reduceRasterAuthoring(s, { type: 'commit-ok', receipt });
    expect(s.phase).toBe('committed');
    expect(s.committed?.style_id).toBe('abc');
    s = reduceRasterAuthoring(s, { type: 'draft-changed', draft });
    s = reduceRasterAuthoring(s, {
      type: 'failed',
      error: { success: false, code: 'CAS_SUPERSEDED', message: 'conflict', field: 'bands' },
    });
    expect(s.phase).toBe('failed');
    expect(s.errorField).toBe('bands');
    s = reduceRasterAuthoring(s, { type: 'restore-committed', draft });
    expect(s.phase).toBe('committed');
    expect(s.error).toBeNull();
  });

  it('external committed style (refresh/undo) discards the temporary draft', () => {
    let s = reduceRasterAuthoring(initialState(), {
      type: 'descriptor-loaded', draft, committed: null,
    });
    s = reduceRasterAuthoring(s, {
      type: 'draft-changed', draft: { ...draft, colormap: 'magma' },
    });
    s = reduceRasterAuthoring(s, {
      type: 'external-committed', committed: receipt.expression as never, draft,
    });
    expect(s.phase).toBe('committed');
    expect(s.draft?.colormap).toBe('viridis');
  });

  it('fast clicks A then B: late A never overwrites B', () => {
    let s = reduceRasterAuthoring(initialState(), { type: 'probe-start', seq: 1 });
    s = reduceRasterAuthoring(s, { type: 'probe-start', seq: 2 });
    s = reduceRasterAuthoring(s, { type: 'probe-ok', seq: 1, result: probe('A') });
    expect(s.probe).toBeNull();
    expect(s.probeSeq).toBe(2);
    s = reduceRasterAuthoring(s, { type: 'probe-ok', seq: 2, result: probe('B') });
    expect(s.probe?.points[0].id).toBe('B');
    expect(s.probePending).toBe(false);
    s = reduceRasterAuthoring(s, { type: 'probe-failed', seq: 1, error: {
      success: false, code: 'RENDER_FAILED', message: 'late',
    } });
    expect(s.probe?.points[0].id).toBe('B');
  });
});
