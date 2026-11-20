import type { SelectionContext } from './selection-store';

export type FlowNodeDirection = 'inbound' | 'outbound' | 'both';

export interface FlowNodeFeature {
  id: string;
  product_ref: string;
  name?: string;
  inbound?: number;
  outbound?: number;
  net?: number;
}

export function flowNodeSelectionPayload(
  feature: FlowNodeFeature,
  layerId: string,
  direction: FlowNodeDirection = 'both',
) {
  return {
    source: 'map' as const,
    layer_id: layerId,
    feature_id: feature.id,
    selected_ids: [feature.id],
    selected_categories: [] as string[],
    id_field: '$id',
    artifact_ref: feature.product_ref,
    properties: {
      flow_focus: 'node',
      flow_direction: direction,
      ...(feature.name === undefined ? {} : { name: feature.name }),
      ...(feature.inbound === undefined ? {} : { inbound: feature.inbound }),
      ...(feature.outbound === undefined ? {} : { outbound: feature.outbound }),
      ...(feature.net === undefined ? {} : { net: feature.net }),
    },
  };
}

function familyOf(layerId: string): string | null {
  for (const suffix of ['-nodes', '-edges', '-direction']) {
    if (layerId.endsWith(suffix)) return layerId.slice(0, -suffix.length);
  }
  return null;
}

function idExpression(ids: string[]): unknown[] {
  return ['in', ['to-string', ['id']], ['literal', ids]];
}

function endpointExpression(field: string, ids: string[]): unknown[] {
  return ['in', ['to-string', ['get', field]], ['literal', ids]];
}

export function getFlowSelectionFilters(
  selection: SelectionContext | null,
): Record<string, unknown[]> | null {
  if (!selection?.artifact_ref || !familyOf(selection.layer_id)) return null;
  const family = familyOf(selection.layer_id) as string;
  const focus = selection.properties?.flow_focus;
  const ids = selection.selected_ids.slice(0, 50);
  if (!ids.length || (focus !== 'node' && focus !== 'edge')) return null;

  if (focus === 'edge') {
    const expression = idExpression(ids);
    return {
      [`${family}-edges`]: expression,
      [`${family}-direction`]: expression,
    };
  }

  const direction = selection.properties?.flow_direction ?? 'both';
  const filters: Record<string, unknown[]> = {
    [`${family}-nodes`]: idExpression(ids),
  };
  let expression: unknown[];
  if (direction === 'inbound') {
    expression = endpointExpression('destination_node_id', ids);
  } else if (direction === 'outbound') {
    expression = endpointExpression('origin_node_id', ids);
  } else {
    expression = ['any',
      endpointExpression('origin_node_id', ids),
      endpointExpression('destination_node_id', ids),
    ];
  }
  filters[`${family}-edges`] = expression;
  filters[`${family}-direction`] = expression;
  return filters;
}
