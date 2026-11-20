import { describe, expect, it } from 'vitest';
import {
  bandLabel, buildExpressionRequest, defaultDraft, draftFieldErrors,
  draftFromCommitted, isDraftDirty,
  type RasterDescriptor, type RasterExpressionSpec,
} from './contracts';

function descriptor(bands: number): RasterDescriptor {
  const list = Array.from({ length: bands }, (_, i) => ({
    index: i + 1, label: `band ${i + 1}`, description: '', dtype: 'float32',
    unit: null, scale: 1, offset: 0, scale_declared: false,
    offset_declared: false, role: null, vmin: 100 * (i + 1), vmax: 200 * (i + 1),
  }));
  return {
    success: true, ref_id: 'ref-1', data_revision: 'fp:1', content_fingerprint: null,
    crs: 'EPSG:4326', crs_present: true, bounds_epsg4326: [0, 0, 1, 1],
    band_count: bands, bands: list, statistics: null, histograms: [],
    capabilities: { numeric_bands: true, tiles: true, probe: true, classified_semantics_requires_declaration: true },
    defaults: {
      schema_version: 'raster_expression.v1', mode: bands < 3 ? 'single' : 'rgb',
      bands: bands < 3 ? [1] : [1, 2, 3], stretch: 'auto', colormap: 'gray',
      opacity: 1, resampling: 'bilinear', classification: 'continuous',
    },
    disclosures: [],
  };
}

describe('G20 expression draft contract', () => {
  it('two-band source defaults to a supported single-band expression', () => {
    const d = defaultDraft(descriptor(2));
    expect(d.mode).toBe('single');
    expect(d.bands).toEqual([1]);
    expect(defaultDraft(descriptor(4)).mode).toBe('rgb');
  });

  it('builds explicit request bodies and keeps per-channel stretch separate', () => {
    const d = { ...defaultDraft(descriptor(4)), autoStretch: false,
      stretchMin: 0, stretchMax: 500,
      perChannel: [[0, 100], [0, 200], [0, 300]] as [number, number][] };
    const req = buildExpressionRequest(d) as Record<string, unknown>;
    expect(req.mode).toBe('rgb');
    expect(req.bands).toEqual([1, 2, 3]);
    // RGB 下全局端点由逐通道派生（评审 P2-4：全局滑块不是死控件）。
    expect(req.stretch).toEqual({ min: 0, max: 300, per_channel: [[0, 100], [0, 200], [0, 300]] });
    expect(req.colormap).toBeUndefined();
  });

  it('maps invalid drafts to the same specific backend error codes', () => {
    const d = defaultDraft(descriptor(4));
    expect(draftFieldErrors({ ...d, bands: [1, 2] }, 4)).toMatchObject({ bands: 'BAND_COUNT_UNSUPPORTED' });
    expect(draftFieldErrors({ ...d, bands: [1, 9, 9] }, 4)).toMatchObject({ bands: 'BAND_OUT_OF_RANGE' });
    expect(draftFieldErrors({ ...d, autoStretch: false, stretchMin: 5, stretchMax: 1 }, 4))
      .toMatchObject({ stretch: 'STRETCH_REVERSED' });
    expect(draftFieldErrors({ ...d, colormap: 'nope' }, 4)).toMatchObject({ colormap: 'UNKNOWN_COLORMAP' });
    expect(draftFieldErrors({ ...d, classification: 'classified' }, 4))
      .toMatchObject({ resampling: 'CLASSIFIED_INTERPOLATION' });
  });

  it('never guesses a band role from index/dtype', () => {
    expect(bandLabel(descriptor(4).bands[3], 4, 'zh')).toBe('第 4 波段');
    expect(bandLabel(descriptor(4).bands[3], 4, 'en')).toBe('Band 4');
  });

  it('review P1-1: default draft keeps stretch=auto (never fakes an exact manual range)', () => {
    const d = defaultDraft(descriptor(4));
    expect(d.autoStretch).toBe(true);
    const req = buildExpressionRequest(d) as { stretch: unknown };
    expect(req.stretch).toBe('auto');
  });

  it('review P1-2 / P2-4: per-channel changes are dirty and RGB globals are derived', () => {
    const committed = {
      schema_version: 'raster_expression.v1', mode: 'rgb', bands: [4, 3, 2],
      stretch: { min: 0, max: 300, source: 'manual', method: 'manual',
        approximate: false, sample_count: null,
        per_channel: [[0, 100], [0, 200], [0, 300]] },
      opacity: 1, resampling: 'bilinear', classification: 'continuous',
      data_revision: 'fp:1', style_id: 'abc',
    } as unknown as RasterExpressionSpec;
    const restored = draftFromCommitted(descriptor(4), committed);
    expect(isDraftDirty(restored, committed)).toBe(false);
    const changed = {
      ...restored,
      perChannel: restored.perChannel.map((p, i) =>
        i === 1 ? [p[0], p[1] + 50] as [number, number] : p),
    };
    expect(isDraftDirty(changed, committed)).toBe(true);
    expect(isDraftDirty({ ...restored, autoStretch: true }, committed)).toBe(true);
    const req = buildExpressionRequest(changed) as {
      stretch: { min: number; max: number; per_channel: number[][] };
    };
    expect(req.stretch.per_channel[1][1]).toBe(250);
    expect(req.stretch.max).toBe(300);  // 全局端点由逐通道派生，不是死控件
  });

  it('detects dirty drafts and restores from a committed style', () => {
    const committed = {
      schema_version: 'raster_expression.v1', mode: 'rgb', bands: [4, 3, 2],
      stretch: { min: 0, max: 500, source: 'manual', method: 'manual',
        approximate: false, sample_count: null,
        per_channel: [[0, 100], [0, 200], [0, 300]] },
      opacity: 1, resampling: 'bilinear', classification: 'continuous',
      data_revision: 'fp:1', style_id: 'abc',
    } as unknown as RasterExpressionSpec;
    const restored = draftFromCommitted(descriptor(4), committed);
    expect(restored.bands).toEqual([4, 3, 2]);
    expect(restored.perChannel).toEqual([[0, 100], [0, 200], [0, 300]]);
    expect(isDraftDirty(restored, committed)).toBe(false);
    expect(isDraftDirty({ ...restored, opacity: 0.4 }, committed)).toBe(true);
  });
});
