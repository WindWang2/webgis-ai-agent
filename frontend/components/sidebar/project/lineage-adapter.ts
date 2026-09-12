/**
 * LineageGraph → layered DAG adapter (ADR-0143 P3).
 *
 * Backend shape (recon §2.3): `{artifact_id, parents[], consumers[]}` — an
 * edge list keyed by `parent_artifact_id` adjacency with BFS `depth ≤ 5`.
 * There is no node array and no coordinates; this adapter derives a layered
 * layout: root in column 0, upstream nodes in columns −depth…−1, downstream
 * in columns +1…+depth, stacked per column.
 *
 * Kept dependency-free and pure so the layout is unit-testable and the render
 * perf assertion (≥50 nodes) exercises exactly what the component draws.
 */

import type { LineageGraph } from '@/lib/api/project';

export interface LineageNode {
  id: string;
  label: string;
  kind: 'root' | 'parent' | 'consumer';
  column: number;
  /** Stack index within the column. */
  row: number;
  depth: number;
  producingTool?: string | null;
  sourceDatasetId?: string | null;
}

export interface LineageEdge {
  from: string;
  to: string;
  kind: 'parent' | 'consumer';
}

export interface LineageLayout {
  rootId: string;
  nodes: LineageNode[];
  edges: LineageEdge[];
  /** Column range, inclusive; 0 = root. */
  minColumn: number;
  maxColumn: number;
  /** Max stack height across columns. */
  maxRows: number;
}

function shorten(id: string): string {
  return id.length <= 12 ? id : `${id.slice(0, 12)}…`;
}

export function adaptLineage(graph: LineageGraph): LineageLayout {
  const nodes: LineageNode[] = [];
  const edges: LineageEdge[] = [];
  const seen = new Set<string>();

  const columnBuckets = new Map<number, string[]>();

  const place = (node: LineageNode) => {
    if (seen.has(node.id)) return;
    seen.add(node.id);
    nodes.push(node);
    const bucket = columnBuckets.get(node.column) ?? [];
    bucket.push(node.id);
    columnBuckets.set(node.column, bucket);
  };

  // De-dup rows first so repeated edges to the same artifact collapse.
  const parentsByNode = new Map<string, { depth: number; tool: string | null; sourceDatasetId: string | null }>();
  for (const p of graph.parents ?? []) {
    const existing = parentsByNode.get(p.parent_artifact_id);
    const depth = p.depth ?? 1;
    if (!existing || depth < existing.depth) {
      parentsByNode.set(p.parent_artifact_id, {
        depth,
        tool: p.producing_tool ?? null,
        sourceDatasetId: p.source_dataset_id ?? null,
      });
    }
    edges.push({ from: p.parent_artifact_id, to: graph.artifact_id, kind: 'parent' });
  }
  const consumersByNode = new Map<string, { depth: number; tool: string | null }>();
  for (const c of graph.consumers ?? []) {
    const existing = consumersByNode.get(c.consumer_artifact_id);
    const depth = c.depth ?? 1;
    if (!existing || depth < existing.depth) {
      consumersByNode.set(c.consumer_artifact_id, { depth, tool: c.producing_tool ?? null });
    }
    edges.push({ from: graph.artifact_id, to: c.consumer_artifact_id, kind: 'consumer' });
  }

  for (const [id, meta] of parentsByNode) {
    place({
      id,
      label: shorten(id),
      kind: 'parent',
      column: -Math.min(5, meta.depth),
      row: 0,
      depth: meta.depth,
      producingTool: meta.tool,
      sourceDatasetId: meta.sourceDatasetId,
    });
  }
  for (const [id, meta] of consumersByNode) {
    place({
      id,
      label: shorten(id),
      kind: 'consumer',
      column: Math.min(5, meta.depth),
      row: 0,
      depth: meta.depth,
      producingTool: meta.tool,
    });
  }
  place({
    id: graph.artifact_id,
    label: shorten(graph.artifact_id),
    kind: 'root',
    column: 0,
    row: 0,
    depth: 0,
  });

  let maxRows = 0;
  let minColumn = 0;
  let maxColumn = 0;
  for (const [column, ids] of columnBuckets) {
    ids.forEach((id, row) => {
      const node = nodes.find((n) => n.id === id);
      if (node) node.row = row;
    });
    maxRows = Math.max(maxRows, ids.length);
    minColumn = Math.min(minColumn, column);
    maxColumn = Math.max(maxColumn, column);
  }

  return { rootId: graph.artifact_id, nodes, edges, minColumn, maxColumn, maxRows };
}
