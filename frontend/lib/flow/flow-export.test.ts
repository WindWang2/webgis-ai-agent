import { describe, expect, it } from 'vitest';
import { verifyFlowMapSpec } from './flow-export';

function fixture(): any {
  const source = 'flow-odf_demo-edges-source';
  return {
    version: '1.10',
    sources: { [source]: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [{
      type: 'Feature', id: 'e1', geometry: { type: 'LineString', coordinates: [[104, 30], [104.1, 30.1]] }, properties: { id: 'e1', weight: 5 },
    }] } } },
    layers: [{
      id: 'flow-odf_demo-edges', type: 'line', source,
      flow: {
        product_ref: 'ref:flow-product-demo', family_id: 'odf_demo', source_ref: 'ref:bench-demo', source_revision: 2,
        direction_mode: 'directed', show_arrows: true, weight_unit: 'trips',
        stats: { edges: { displayed: 1 } }, encoding: { transform: 'linear' }, filters: { top_n: 20 },
      },
      legend_spec: { type: 'continuous', field: 'weight_encoded', direction_mode: 'directed', unit: 'trips', transform: 'linear' },
    }, {
      id: 'flow-odf_demo-direction', type: 'symbol', source,
      layout: { 'symbol-placement': 'line', 'text-field': '▶', 'text-keep-upright': false },
    }],
    layout: { components: [] },
  };
}

describe('OD flow export completeness', () => {
  it('accepts a complete directed product and preserves receipt facts', () => {
    const result = verifyFlowMapSpec(fixture(), { renderedSvg: '<text>▶</text>' });
    expect(result.complete).toBe(true);
    expect(result.products[0]).toMatchObject({ source_revision: 2, declared_edges: 1, actual_edges: 1 });
  });

  it('fails closed for missing direction, stale legend and source count mismatch', () => {
    const spec = fixture();
    spec.layers = [spec.layers[0]];
    spec.layers[0].legend_spec.field = 'trips';
    spec.layers[0].flow.stats.edges.displayed = 2;
    const result = verifyFlowMapSpec(spec, { renderedSvg: '<path/>' });
    expect(result.complete).toBe(false);
    expect(new Set(result.issues.map((issue) => issue.code))).toEqual(new Set([
      'flow_direction_missing', 'flow_legend_stale', 'flow_edge_count_mismatch', 'flow_direction_render_failed',
    ]));
  });

  it('requires no arrow for undirected totals but rejects a false direction', () => {
    const spec = fixture();
    spec.layers[0].flow.direction_mode = 'undirected-total';
    spec.layers[0].legend_spec.direction_mode = 'undirected-total';
    spec.layers = [spec.layers[0]];
    expect(verifyFlowMapSpec(spec).complete).toBe(true);
    spec.layers.push(fixture().layers[1]);
    expect(verifyFlowMapSpec(spec).issues.map((issue) => issue.code)).toEqual(['flow_direction_unexpected']);
  });
});
