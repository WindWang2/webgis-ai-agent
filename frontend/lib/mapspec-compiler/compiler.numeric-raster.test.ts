import { describe, expect, it } from 'vitest';
import { compileMapSpec, validateMapSpec } from './compiler';
import { numericRasterSourceId, RASTER_SESSION_PLACEHOLDER } from '../raster-authoring/tile-url';
import type { MapSpec } from './types';

const style = (styleId: string, bands: number[]) => ({
  schema_version: 'raster_expression.v1', mode: bands.length === 1 ? 'single' : 'rgb',
  bands, stretch: { min: 0, max: 500, source: 'manual', method: 'manual',
    approximate: false, sample_count: null, per_channel: null },
  colormap: bands.length === 1 ? 'viridis' : null,
  opacity: 0.7, resampling: 'bilinear', classification: 'continuous',
  data_revision: 'fp:abc', style_id: styleId,
});

function numericSpec(): MapSpec {
  return {
    version: '1.9',
    sources: {
      shared: {
        type: 'numeric_raster', schema_version: 'raster_expression.v1',
        refId: 'ref:raster/four-band', bounds: [10, 19, 11, 20], crs: 'EPSG:4326',
        dataRevision: 'fp:abc', bandCount: 4,
      },
    },
    layers: [
      { id: 'layer-a', source: 'shared', type: 'raster',
        paint: {}, raster_style: style('style-aaaa', [1]) },
      { id: 'layer-b', source: 'shared', type: 'raster',
        paint: {}, raster_style: style('style-bbbb', [4, 3, 2]) },
    ],
  } as unknown as MapSpec;
}

describe('G20 numeric raster compilation', () => {
  it('accepts numeric_raster sources and discloses the headless session placeholder', () => {
    const spec = numericSpec();
    const verdict = validateMapSpec(spec);
    expect(verdict.errors.filter((e) => e.code === 'UNKNOWN_SOURCE_TYPE')).toHaveLength(0);
    const result = compileMapSpec(spec);
    expect(result.report.success).toBe(true);
    expect(result.report.warnings.some((w) => w.includes('numeric raster'))).toBe(true);
  });

  it('same data ref + two layers => two independent tile sources and tile URLs', () => {
    const result = compileMapSpec(numericSpec());
    const a = numericRasterSourceId('shared', 'layer-a');
    const b = numericRasterSourceId('shared', 'layer-b');
    const srcA = result.style.sources[a] as unknown as Record<string, unknown>;
    const srcB = result.style.sources[b] as unknown as Record<string, unknown>;
    expect(srcA.type).toBe('raster');
    expect(srcB.type).toBe('raster');
    const urlA = (srcA.tiles as string[])[0];
    const urlB = (srcB.tiles as string[])[0];
    expect(urlA).toContain('style_id=style-aaaa');
    expect(urlB).toContain('style_id=style-bbbb');
    expect(urlA).toContain(RASTER_SESSION_PLACEHOLDER);
    expect(urlA).not.toBe(urlB);
    // 原始 shared source 不作为 MapLibre 源产出
    expect(result.style.sources.shared).toBeUndefined();
    // 层指向各自派生源
    const layers = result.style.layers as unknown as Array<Record<string, unknown>>;
    expect(layers.find((l) => l.id === 'layer-a')?.source).toBe(a);
    expect(layers.find((l) => l.id === 'layer-b')?.source).toBe(b);
    // opacity 只在服务端瓦片应用一次
    expect((layers.find((l) => l.id === 'layer-a')?.paint as Record<string, unknown>)['raster-opacity'])
      .toBeUndefined();
  });

  it('legacy baked-PNG raster source keeps the image source + raster-opacity path', () => {
    const spec = {
      version: '1.0',
      sources: { heat: { type: 'raster', imageRef: 'ref:raster/1', bounds: [0, 0, 1, 1] } },
      layers: [{ id: 'heat-layer', source: 'heat', type: 'raster', paint: { opacity: 0.4 } }],
    } as unknown as MapSpec;
    const result = compileMapSpec(spec);
    expect((result.style.sources.heat as Record<string, unknown>).type).toBe('image');
    const layer = (result.style.layers as unknown as Array<{ paint: Record<string, unknown> }>)[0];
    expect(layer.paint['raster-opacity']).toBe(0.4);
  });
});
