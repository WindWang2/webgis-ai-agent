import { describe, it, expect } from 'vitest';
import { normalizeStepResult, parseBBox } from '@/lib/results/normalize';
import type { ArgsContext, StepResultEvent } from '@/lib/results/types';

function step(partial: Partial<StepResultEvent>): StepResultEvent {
  return { tool: 'unknown', ...partial };
}

describe('parseBBox', () => {
  it('accepts a 4-number [W,S,E,N] array', () => {
    expect(parseBBox([1, 2, 3, 4])).toEqual([1, 2, 3, 4]);
  });
  it('rejects non-finite / wrong-length', () => {
    expect(parseBBox([1, 2, 3])).toBeUndefined();
    expect(parseBBox([1, 2, 3, NaN])).toBeUndefined();
    expect(parseBBox('1,2,3,4')).toBeUndefined();
  });
});

describe('normalizeStepResult — family + metrics', () => {
  it('classifies buffer vector output with captured args (inputs + params)', () => {
    const argsCtx: ArgsContext = {
      captured: true,
      args: { geojson: 'ref:geojson-abc123', distance: 500, unit: 'meters' },
    };
    const res = normalizeStepResult(
      step({
        step_id: 's1',
        tool: 'buffer_analysis',
        geojson_ref: 'ref:geojson-abc123',
        result: { success: true, summary: '已生成缓冲区。', bbox: [116, 39, 117, 40] },
      }),
      argsCtx,
    );
    expect(res.family).toBe('buffer');
    expect(res.toolLabel).toBe('缓冲区分析');
    expect(res.status).toBe('completed');
    expect(res.outputs[0].kind).toBe('vector');
    expect(res.outputs[0].ref).toBe('ref:geojson-abc123');
    expect(res.outputs[0].hasLayer).toBe(true);
    expect(res.inputs).toHaveLength(1);
    expect(res.inputs[0].ref).toBe('ref:geojson-abc123');
    expect(res.parameters).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: 'distance', value: 500 }),
      expect.objectContaining({ source: 'unit', value: 'meters' }),
    ]));
    expect(res.bbox).toEqual([116, 39, 117, 40]);
  });

  it('extracts Moran’s I statistical metrics from result.data scalars', () => {
    const res = normalizeStepResult(
      step({
        tool: 'moran_i',
        result: {
          success: true,
          summary: 'Spatial autocorrelation computed.',
          data: { moran_i: 0.4231, expected_i: -0.01, p_value: 0.001, pattern: 'Clustered', n_features: 88 },
        },
      }),
    );
    expect(res.family).toBe('spatial_stats');
    const labels = res.metrics.map((m) => m.label);
    expect(labels).toEqual(expect.arrayContaining(["Moran's I", 'p 值', '空间模式', '要素数']));
    const moran = res.metrics.find((m) => m.label === "Moran's I");
    expect(moran?.value).toBe(0.4231);
    expect(moran?.emphasis).toBe('primary');
    // no geojson_ref => statistic output, no bound layer
    expect(res.outputs[0].kind).toBe('statistic');
    expect(res.outputs[0].hasLayer).toBe(false);
  });

  it('extracts hotspot counts from data envelope keys (without touching features)', () => {
    const res = normalizeStepResult(
      step({
        tool: 'hotspot_analysis',
        geojson_ref: 'ref:geojson-h1',
        result: {
          success: true,
          summary: 'Hot spots found.',
          // data is a FeatureCollection whose features we must NOT read;
          // envelope scalars live alongside `features`.
          data: { type: 'FeatureCollection', features: new Array(120), hot_spots_count: 12, cold_spots_count: 7, distance_band_m: 1000 },
          bbox: [0, 0, 1, 1],
        },
      }),
    );
    const byLabel = Object.fromEntries(res.metrics.map((m) => [m.label, m.value]));
    expect(byLabel['热点数']).toBe(12);
    expect(byLabel['冷点数']).toBe(7);
    expect(res.outputs[0].featureCount).toBeUndefined(); // not fabricated; filled by descriptor
  });

  it('extracts cluster method + n_clusters', () => {
    const res = normalizeStepResult(
      step({
        tool: 'spatial_cluster',
        geojson_ref: 'ref:geojson-c1',
        result: { success: true, summary: 'ok', data: { method: 'kmeans', n_clusters: 5, cluster_stats: [{}, {}, {}] } },
      }),
    );
    const byLabel = Object.fromEntries(res.metrics.map((m) => [m.label, m.value]));
    expect(byLabel['方法']).toBe('kmeans');
    expect(byLabel['簇数']).toBe(5);
    expect(byLabel['簇统计']).toBe('3 组');
  });

  it('classifies raster reclassify as raster output without a layer', () => {
    const res = normalizeStepResult(
      step({
        tool: 'raster_reclassify',
        result: { success: true, summary: 'done', data: { result_path: '/data/out.tif' }, result_path: '/data/out.tif' },
      }),
      { captured: true, args: { raster_path: '/data/in.tif', scheme: [{ min: 0, max: 1, value: 1, label: 'low' }] } },
    );
    expect(res.family).toBe('raster');
    expect(res.outputs[0].kind).toBe('raster');
    expect(res.outputs[0].hasLayer).toBe(false);
  });

  it('extracts NDVI remote-sensing metrics', () => {
    const res = normalizeStepResult(
      step({
        tool: 'compute_ndvi',
        result: { status: 'ok', bbox: [100, 30, 101, 31], ndvi_stats: { min: -0.2, max: 0.8, mean: 0.31 }, vegetation_coverage: 62.5 },
      }),
    );
    expect(res.family).toBe('remote_sensing');
    const byLabel = Object.fromEntries(res.metrics.map((m) => [m.label, m.value]));
    expect(byLabel['NDVI 最大']).toBe(0.8);
    expect(byLabel['植被覆盖度']).toBe(62.5);
    expect(res.outputs[0].kind).toBe('raster');
  });

  it('detects unreachable-facilities warning from isochrone summary prose', () => {
    const res = normalizeStepResult(
      step({
        tool: 'isochrone_network',
        geojson_ref: 'ref:geojson-iso',
        result: { success: true, summary: 'Isochrone built. 3 facility(ies) unreachable (disconnected from the road network).' },
      }),
    );
    expect(res.warnings.some((w) => w.code === 'unreachable_facilities')).toBe(true);
    expect(res.status).toBe('warning');
  });

  it('treats heatmap image output as a visible layer', () => {
    const res = normalizeStepResult(
      step({
        tool: 'heatmap_data',
        result: { type: 'heatmap_raster', image: 'data:image/png;base64,xxxx', bbox: [0, 0, 1, 1], total_points: 5432, metadata: { render_type: 'raster', point_count: 5432 } },
      }),
    );
    expect(res.outputs[0].kind).toBe('image');
    expect(res.outputs[0].hasLayer).toBe(true);
    const byLabel = Object.fromEntries(res.metrics.map((m) => [m.label, m.value]));
    expect(byLabel['总点数']).toBe(5432);
  });
});

describe('normalizeStepResult — truthfulness (CRS / unknown / failed)', () => {
  it('never fabricates CRS (output.crs stays undefined)', () => {
    const res = normalizeStepResult(
      step({ tool: 'buffer_analysis', geojson_ref: 'ref:geojson-x', result: { success: true, summary: 'ok' } }),
    );
    expect(res.outputs[0].crs).toBeUndefined();
  });

  it('handles an unknown / minimal tool gracefully (generic family, no metrics)', () => {
    const res = normalizeStepResult(
      step({ tool: 'some_custom_tool', result: { success: true, summary: 'done' } }),
    );
    expect(res.family).toBe('generic');
    expect(res.status).toBe('completed');
    expect(res.metrics).toEqual([]);
    expect(res.outputs[0].kind).toBe('statistic');
  });

  it('classifies zonal_stats as a VECTOR output (regression: was mislabeled raster)', () => {
    // zonal_stats returns a FeatureCollection (polygons enriched with raster stats),
    // backed by a geojson_ref — it must render as vector, never raster.
    const res = normalizeStepResult(
      step({ tool: 'zonal_stats', geojson_ref: 'ref:geojson-zonal', result: { success: true, summary: 'ok' } }),
    );
    expect(res.outputs[0].kind).toBe('vector');
    expect(res.outputs[0].hasLayer).toBe(true);
  });

  it('marks a failed result (success=false + error_type)', () => {
    const res = normalizeStepResult(
      step({ tool: 'hotspot_analysis', result: { success: false, error_type: 'VALIDATION_ERROR', summary: 'Missing value_field.', correction_hint: 'Provide value_field.' } }),
    );
    expect(res.status).toBe('failed');
    expect(res.warnings.some((w) => w.code === 'correction_hint')).toBe(true);
  });

  it('does NOT treat the _streaming_note transport artifact as a result warning', () => {
    const res = normalizeStepResult(
      step({ tool: 'buffer_analysis', geojson_ref: 'ref:geojson-big', result: { success: true, summary: 'ok', _streaming_note: '大体积要素数据已过滤。' } }),
    );
    // _streaming_note is a transport detail (full layer is auto-loaded); it must
    // NOT mark the result approximate/partial or every vector result would be "partial".
    expect(res.warnings.some((w) => w.code === 'geometry_dropped')).toBe(false);
    expect(res.approximate).toBeUndefined();
    expect(res.status).toBe('completed');
  });
});

describe('normalizeStepResult — args context + legend', () => {
  it('returns no inputs/params when args were not captured', () => {
    const res = normalizeStepResult(
      step({ tool: 'buffer_analysis', geojson_ref: 'ref:geojson-y', result: { success: true, summary: 'ok' } }),
      { captured: false },
    );
    expect(res.inputs).toEqual([]);
    expect(res.parameters).toEqual([]);
  });

  it('extracts legend_spec from top-level and from data', () => {
    const top = normalizeStepResult(
      step({ tool: 'h3_binning', geojson_ref: 'ref:geojson-h3', result: { success: true, summary: 'ok', legend_spec: { type: 'graduated', field: 'val', breaks: [0, 1], palette: 'p', palette_colors: ['#fff', '#000'] } } }),
    );
    expect(top.legendSpec?.type).toBe('graduated');

    const nested = normalizeStepResult(
      step({ tool: 'kde_contours', result: { type: 'FeatureCollection', summary: 'ok', data: { legend_spec: { type: 'continuous', min: 0, max: 1, palette: 'p', palette_colors: ['#000', '#fff'] } } } }),
    );
    expect(nested.legendSpec?.type).toBe('continuous');
  });

  it('records background job linkage in provenance', () => {
    const res = normalizeStepResult(
      step({ tool: 'compute_ndvi', background_job_ids: ['101', '102'], result: { status: 'ok', bbox: [0, 0, 1, 1] } }),
    );
    expect(res.backgroundJobIds).toEqual(['101', '102']);
    expect(res.provenance.some((p) => p.kind === 'run')).toBe(true);
  });
});
