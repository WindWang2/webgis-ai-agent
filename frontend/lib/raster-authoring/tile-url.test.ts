import { describe, expect, it } from 'vitest';
import {
  buildNumericRasterTileUrl, numericRasterSourceId,
  RASTER_SESSION_PLACEHOLDER, resolveNumericRasterTileUrl, styleIdFromTileUrl,
} from './tile-url';

describe('G20 numeric raster tile URL', () => {
  it('derives a distinct source id per layer sharing one data ref', () => {
    expect(numericRasterSourceId('raster-1', 'layer-a'))
      .not.toBe(numericRasterSourceId('raster-1', 'layer-b'));
    expect(numericRasterSourceId('raster-1', 'layer-a')).toBe('raster-1::layer-a');
  });

  it('carries only ref/layer/style and a session placeholder — no owner token', () => {
    const url = buildNumericRasterTileUrl('ref:raster/abc', 'layer-a', 'style-12345678');
    expect(url).toContain('/api/v1/raster-authoring/tiles/ref%3Araster%2Fabc/{z}/{x}/{y}.png');
    expect(url).toContain(RASTER_SESSION_PLACEHOLDER);
    expect(url).toContain('layer_id=layer-a');
    expect(url).toContain('style_id=style-12345678');
    expect(url).not.toMatch(/token|sig=/i);
    expect(styleIdFromTileUrl(url)).toBe('style-12345678');
  });

  it('refuses to mount when no session is bound', () => {
    const template = buildNumericRasterTileUrl('ref-1', 'l1', 'style-abc');
    expect(resolveNumericRasterTileUrl(template, null)).toBeNull();
    expect(resolveNumericRasterTileUrl(template, 'sess-42'))
      .toBe(template.replace(RASTER_SESSION_PLACEHOLDER, 'sess-42'));
  });
});
