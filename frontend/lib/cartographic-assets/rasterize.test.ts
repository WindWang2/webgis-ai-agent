import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SymbolAssetDocument, SymbolPrimitive, TextureParams } from './contracts';
import { rasterizeAreaTexture, swatchSvgString } from './rasterize';

const textureParams: TextureParams = {
  spacing_px: 12,
  angle_deg: 45,
  stroke_width_px: 1.5,
  pattern_scale: 1,
  fg_color: '#334155',
  bg_color: 'transparent',
};

function tileDoc(
  viewbox: [number, number],
  primitives: SymbolPrimitive[] = [],
): SymbolAssetDocument {
  return {
    schema_version: 1,
    kind: 'area_texture',
    content_digest: 'sha256:' + 'a'.repeat(64),
    pattern: { type: 'svg_tile', tile_viewbox: viewbox, tile_primitives: primitives },
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('rasterizeAreaTexture svg_tile', () => {
  it('uses one scale factor when the scaled tile exceeds the pixel cap', () => {
    let canvas: HTMLCanvasElement | undefined;
    const scale = vi.fn();
    const ctx = {
      scale,
      setTransform: vi.fn(),
      getImageData: vi.fn((_x: number, _y: number, w: number, h: number) => ({
        width: w,
        height: h,
        data: new Uint8ClampedArray(w * h * 4),
      })),
    };
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
      canvas = this;
      return ctx as unknown as CanvasRenderingContext2D;
    });

    const image = rasterizeAreaTexture(tileDoc([300, 12]), textureParams, 2);

    expect(image.width).toBe(256);
    expect(image.height).toBe(10);
    expect(canvas?.width).toBe(256);
    expect(canvas?.height).toBe(10);
    expect(scale).toHaveBeenCalledTimes(1);
    const [sx, sy] = scale.mock.calls[0];
    expect(sx).toBe(sy);
    expect(sx).toBeCloseTo(256 / 300);
  });
});

describe('swatchSvgString primitive attributes', () => {
  it('escapes primitive fill and stroke for both point and tile swatches', () => {
    const payload = '#fff"><script>alert(1)</script><rect fill="';
    const primitives: SymbolPrimitive[] = [
      { op: 'rect', x: 0, y: 0, w: 12, h: 12, fill: payload, stroke: payload },
      { op: 'line', x: 0, y: 0, w: 12, h: 12, stroke: payload },
    ];
    const pointDoc: SymbolAssetDocument = {
      schema_version: 1,
      kind: 'point_symbol',
      content_digest: 'sha256:' + 'b'.repeat(64),
      viewbox: [12, 12],
      primitives,
    };

    for (const svg of [
      swatchSvgString(pointDoc, {}, 24, 24),
      swatchSvgString(tileDoc([12, 12], primitives), {}, 24, 24),
    ]) {
      expect(svg).not.toContain(payload);
      expect(svg).not.toContain('<script>');
      expect(svg).toContain('fill="#fff&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;&lt;rect fill=&quot;"');
      expect(svg).toContain('stroke="#fff&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;&lt;rect fill=&quot;"');
    }
  });
});
