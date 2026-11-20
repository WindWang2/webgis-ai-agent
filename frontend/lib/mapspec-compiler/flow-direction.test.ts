import { describe, expect, it } from 'vitest';
import { compileMapSpecToSvg } from './mapspec-to-svg';

function spec(coordinates: unknown, angleDpi = 96): any {
  const edgeSource = 'flow-odf_demo-edges-source';
  return {
    version: '1.10',
    view: { center: [179.95, 30], zoom: 8 },
    sources: {
      [edgeSource]: { type: 'geojson', inlineData: {
        type: 'FeatureCollection',
        features: [{ type: 'Feature', id: 'e_demo', geometry: {
          type: coordinates && (coordinates as any)[0]?.[0]?.[0] === undefined ? 'LineString' : 'MultiLineString',
          coordinates,
        }, properties: { id: 'e_demo', weight: 8, weight_encoded: 1 } }],
      } },
    },
    layers: [{
      id: 'flow-odf_demo-edges', type: 'line', source: edgeSource,
      paint: { width: 4, color: '#111111' },
    }, {
      id: 'flow-odf_demo-direction', type: 'symbol', source: edgeSource,
      metadata: { flow_part: 'direction' },
      layout: {
        'symbol-placement': 'line', 'symbol-spacing': 40,
        'text-field': '▶', 'text-size': 10,
        'text-rotation-alignment': 'map', 'text-keep-upright': false,
      },
      paint: { 'text-color': '#111111' },
    }],
    layout: { components: [] },
  };
}

function rotation(svg: string): number {
  const match = svg.match(/rotate\((-?\d+(?:\.\d+)?)/);
  if (!match) throw new Error(`missing rotated flow direction: ${svg.slice(0, 500)}`);
  return Number(match[1]);
}

describe('OD flow direction SVG parity fixtures', () => {
  it('rotates arrows from the displayed local path tangent, reversing with the edge', () => {
    const forward = compileMapSpecToSvg(spec([[179.9, 30], [180.0, 30.1]]));
    const backward = compileMapSpecToSvg(spec([[180.0, 30.1], [179.9, 30]]));
    expect(Math.abs(rotation(forward) - rotation(backward))).toBeGreaterThan(90);
  });

  it('keeps direction on antimeridian MultiLineString at 96 and 300 DPI', () => {
    const coordinates = [
      [[179.9, 30], [180.0, 30.05]],
      [[-180.0, 30.05], [-179.9, 30.1]],
    ];
    for (const targetDpi of [96, 300]) {
      const svg = compileMapSpecToSvg(spec(coordinates, targetDpi), { targetDpi });
      expect(svg).toContain('▶');
      expect(svg).toContain('rotate(');
      expect(svg).not.toContain('NaN');
    }
  });
});
