import { describe, expect, it } from 'vitest';
import { compileMapSpec } from './compiler';
import { compileMapSpecToSvg } from './mapspec-to-svg';

const filter = ['==', ['get', 'k'], 1];
const features = [1, 2].map((k) => ({ type: 'Feature', properties: { k, title: String(k) }, geometry: { type: 'Point', coordinates: [k, k] } }));
const spec = { version: '1.0', sources: { s: { type: 'geojson', inlineData: { type: 'FeatureCollection', features } } }, layers: [{ id: 'dots', source: 's', type: 'circle', label: { field: 'title' }, filter }], view: { center: [0, 0], zoom: 8 } };

describe('review filter regressions (#1809, #1810)', () => {
  it('keeps authored filters on ordinary layers and their labels', () => {
    const result = compileMapSpec(spec as never);
    expect(result.style.layers.find((l) => l.id === 'dots')?.filter).toEqual(filter);
    expect(result.style.layers.find((l) => l.id === 'dots-label')?.filter).toEqual(filter);
  });
  it('renders only matching features in SVG', () => {
    const svg = compileMapSpecToSvg(spec);
    expect(svg.match(/<circle /g)).toHaveLength(1);
  });
  it('skips unsupported filters even when a supported branch would match', () => {
    const diagnostics: string[] = [];
    const svg = compileMapSpecToSvg({ ...spec, layers: [{ ...spec.layers[0], filter: ['any', true, ['within', {}]] }] }, { onDiagnostic: (code) => diagnostics.push(code) });
    expect(svg).not.toContain('<circle ');
    expect(diagnostics).toContain('layer_filter_unsupported');
  });
});


describe('SVG label contract (#1826)', () => {
  it('synthesizes authored labels and evaluates native get expressions', () => {
    const spec = {
      version: '1.0', sources: { s: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [
        { type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: { title: 'Harbor' } },
      ] } } },
      layers: [{ id: 'point', type: 'circle', source: 's', label: { field: 'title' } }],
    };
    const svg = compileMapSpecToSvg(spec);
    expect(svg).toContain('Harbor</text>');
    expect(svg).not.toContain('get,title');
    const symbolSpec = { ...spec, layers: [{ id: 'text', type: 'symbol', source: 's', layout: { 'text-field': ['to-string', ['get', 'title']] } }] };
    expect(compileMapSpecToSvg(symbolSpec)).toContain('Harbor</text>');
  });
});
