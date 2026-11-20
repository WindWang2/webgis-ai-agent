import { describe, expect, it } from 'vitest';
import type { SelectionContext } from './selection-store';
import { flowNodeSelectionPayload, getFlowSelectionFilters } from './flow-selection';

function nodeSelection(direction = 'both'): SelectionContext {
  return {
    source: 'map',
    kind: 'select',
    layer_id: 'flow-odf_demo-nodes',
    artifact_ref: 'ref:flow-product-demo',
    feature_id: 'odn_x',
    selected_ids: ['odn_x'],
    selected_categories: [],
    id_field: '$id',
    properties: { flow_focus: 'node', flow_direction: direction },
    revision: 1,
  };
}

describe('OD flow transient node focus', () => {
  it('builds bounded node payload without touching MapSpec', () => {
    const payload = flowNodeSelectionPayload({
      id: 'odn_x', product_ref: 'ref:flow-product-demo', name: '武侯区',
      inbound: 3, outbound: 8, net: 5,
    }, 'flow-odf_demo-nodes', 'inbound');
    expect(payload).toMatchObject({
      source: 'map', layer_id: 'flow-odf_demo-nodes', feature_id: 'odn_x',
      selected_ids: ['odn_x'], id_field: '$id', artifact_ref: 'ref:flow-product-demo',
      properties: { flow_focus: 'node', flow_direction: 'inbound', name: '武侯区' },
    });
  });

  it('projects inbound/outbound/both to the same node and related directed edges', () => {
    const inbound = getFlowSelectionFilters(nodeSelection('inbound'));
    expect(inbound?.['flow-odf_demo-nodes']).toEqual(['in', ['to-string', ['id']], ['literal', ['odn_x']]]);
    expect(inbound?.['flow-odf_demo-edges']).toEqual(
      ['in', ['to-string', ['get', 'destination_node_id']], ['literal', ['odn_x']]],
    );
    expect(inbound?.['flow-odf_demo-direction']).toEqual(inbound?.['flow-odf_demo-edges']);

    const outbound = getFlowSelectionFilters(nodeSelection('outbound'));
    expect(outbound?.['flow-odf_demo-edges']).toEqual(
      ['in', ['to-string', ['get', 'origin_node_id']], ['literal', ['odn_x']]],
    );

    const both = getFlowSelectionFilters(nodeSelection('both'));
    expect(both?.['flow-odf_demo-edges']).toEqual(['any',
      ['in', ['to-string', ['get', 'origin_node_id']], ['literal', ['odn_x']]],
      ['in', ['to-string', ['get', 'destination_node_id']], ['literal', ['odn_x']]],
    ]);
  });

  it('keeps edge focus off the node layer and ignores non-flow selections', () => {
    const edge = nodeSelection('both');
    edge.layer_id = 'flow-odf_demo-edges';
    edge.properties = { flow_focus: 'edge' };
    const filters = getFlowSelectionFilters(edge);
    expect(filters?.['flow-odf_demo-edges']).toBeDefined();
    expect(filters?.['flow-odf_demo-direction']).toBeDefined();
    expect(filters?.['flow-odf_demo-nodes']).toBeUndefined();
    expect(getFlowSelectionFilters(null)).toBeNull();
    expect(getFlowSelectionFilters({ ...nodeSelection(), layer_id: 'ordinary-layer' })).toBeNull();
  });
});
