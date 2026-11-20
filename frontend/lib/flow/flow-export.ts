import type { ExportDegradationCode } from '@/lib/map-kit/export-chrome';

export interface FlowExportIssue {
  code: ExportDegradationCode;
  severity: 'error';
  layer_id: string;
  detail: string;
}

export interface FlowExportVerification {
  complete: boolean;
  evaluated: boolean;
  issues: FlowExportIssue[];
  products: Array<Record<string, unknown>>;
}

function sourceFeatures(source: any): any[] | null {
  const payload = source?.inlineData ?? source?.data;
  return Array.isArray(payload?.features) ? payload.features : null;
}

export function verifyFlowMapSpec(
  input: any,
  options: { renderedSvg?: string; renderDiagnostics?: Array<Record<string, unknown>> } = {},
): FlowExportVerification {
  const issues: FlowExportIssue[] = [];
  const products: Array<Record<string, unknown>> = [];
  const spec = input && typeof input === 'object' ? input : {};
  const layers = Array.isArray(spec.layers) ? spec.layers.filter((layer: any) => layer && typeof layer === 'object') : [];
  const sources = spec.sources ?? {};
  const add = (code: ExportDegradationCode, layerId: string, detail: string) => {
    issues.push({ code, severity: 'error', layer_id: layerId, detail });
  };

  for (const primary of layers) {
    const flow = primary.flow;
    if (!flow || typeof flow !== 'object') continue;
    const family = String(flow.family_id ?? '');
    const layerId = String(primary.id ?? '');
    const mode = String(flow.direction_mode ?? '');
    const declared = Number(flow.stats?.edges?.displayed ?? 0);
    const sourceId = String(primary.source ?? '');
    const features = sourceFeatures(sources[sourceId]);
    if (features === null) {
      add('flow_source_unavailable', layerId, `source=${sourceId} is not hydrated`);
    } else if (features.length !== declared) {
      add('flow_edge_count_mismatch', layerId, `declared=${declared} actual=${features.length}`);
    }
    const directionId = `flow-${family}-direction`;
    const direction = layers.find((layer: any) => layer.id === directionId);
    const directionRequired = Boolean(flow.show_arrows) && mode !== 'undirected-total' && declared > 0;
    if (directionRequired && !direction) add('flow_direction_missing', directionId, `${mode} requires a direction-mark layer`);
    if (mode === 'undirected-total' && direction) add('flow_direction_unexpected', directionId, 'undirected-total must not imply a direction');
    if (direction) {
      const layout = direction.layout ?? {};
      if (
        direction.type !== 'symbol'
        || direction.source !== sourceId
        || layout['symbol-placement'] !== 'line'
        || !layout['text-field']
        || layout['text-keep-upright'] !== false
      ) {
        add('flow_direction_invalid', directionId, 'direction layer does not share the edge path/tangent semantics');
      }
    }
    const legend = primary.legend_spec ?? {};
    const encoding = flow.encoding ?? {};
    const expectedLegendField = legend.type === 'continuous' ? 'weight_encoded' : 'weight';
    if (
      legend.field !== expectedLegendField
      || legend.direction_mode !== mode
      || legend.unit !== flow.weight_unit
      || legend.transform !== encoding.transform
    ) {
      add('flow_legend_stale', layerId, 'legend field/mode/unit/transform differs from the flow product');
    }
    if (directionRequired && options.renderedSvg && !options.renderedSvg.includes('▶')) {
      add('flow_direction_render_failed', directionId, 'no direction mark reached the rendered SVG');
    }
    products.push({
      product_ref: flow.product_ref,
      family_id: family,
      source_ref: flow.source_ref,
      source_revision: flow.source_revision,
      source_fingerprint: flow.source_fingerprint,
      direction_mode: mode,
      filters: flow.filters ?? {},
      time_window: flow.time_window ?? null,
      encoding,
      product_fingerprint: flow.product_fingerprint,
      declared_edges: declared,
      actual_edges: features?.length ?? null,
    });
  }
  for (const diagnostic of options.renderDiagnostics ?? []) {
    const layerId = String(diagnostic.layer_id ?? '');
    if (layerId.endsWith('-direction')) add('flow_direction_render_failed', layerId, String(diagnostic.code ?? 'render failure'));
  }
  const unique = new Map<string, FlowExportIssue>();
  for (const issue of issues) unique.set(`${issue.code}|${issue.layer_id}|${issue.detail}`, issue);
  return {
    complete: unique.size === 0,
    evaluated: products.length > 0,
    issues: [...unique.values()],
    products,
  };
}
