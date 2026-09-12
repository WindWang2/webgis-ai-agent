/**
 * lineage-adapter 单测（ADR-0143 P3）：分层布局不变式。
 * L1 列位置：上游 −depth、根 0、下游 +depth（BFS 深度钳到 5）。
 * L2 去重：同 artifact 多条边坍缩为一个节点，边保留。
 * L3 完备：parents+consumers 全部出现在布局中。
 */
import { describe, it, expect } from 'vitest';
import { adaptLineage } from '@/components/sidebar/project/lineage-adapter';
import type { LineageGraph } from '@/lib/api/project';
import { makeLineageGraph } from './fixtures';

describe('adaptLineage', () => {
  it('L1: 按深度分层（上游负列/根 0/下游正列）', () => {
    const graph: LineageGraph = {
      artifact_id: 'root',
      parents: [
        {
          lineage_id: 'l1',
          artifact_id: 'root',
          parent_artifact_id: 'up-deep',
          depth: 3,
        },
        {
          lineage_id: 'l2',
          artifact_id: 'root',
          parent_artifact_id: 'up-shallow',
          depth: 1,
        },
      ],
      consumers: [
        {
          lineage_id: 'l3',
          consumer_artifact_id: 'down-1',
          parent_artifact_id: 'root',
          depth: 2,
        },
      ],
    };
    const layout = adaptLineage(graph);
    const byId = Object.fromEntries(layout.nodes.map((n) => [n.id, n]));
    expect(byId['root'].column).toBe(0);
    expect(byId['up-deep'].column).toBe(-3);
    expect(byId['up-shallow'].column).toBe(-1);
    expect(byId['down-1'].column).toBe(2);
  });

  it('L2: 同节点多边坍缩；深度取最小；边保留', () => {
    const graph: LineageGraph = {
      artifact_id: 'root',
      parents: [
        { lineage_id: 'a', artifact_id: 'root', parent_artifact_id: 'p', depth: 2 },
        { lineage_id: 'b', artifact_id: 'root', parent_artifact_id: 'p', depth: 1 },
      ],
      consumers: [],
    };
    const layout = adaptLineage(graph);
    const pNodes = layout.nodes.filter((n) => n.id === 'p');
    expect(pNodes).toHaveLength(1);
    expect(pNodes[0].column).toBe(-1);
    expect(layout.edges.filter((e) => e.from === 'p')).toHaveLength(2);
  });

  it('L3: 60 节点血缘图完备且布局有界（压力图夹具）', () => {
    const graph = makeLineageGraph(60) as unknown as LineageGraph;
    const layout = adaptLineage(graph);
    expect(layout.nodes.length).toBeGreaterThanOrEqual(50);
    // 列范围钳制在 [-5, +5]
    expect(layout.minColumn).toBeGreaterThanOrEqual(-5);
    expect(layout.maxColumn).toBeLessThanOrEqual(5);
    for (const n of layout.nodes) {
      expect(n.row).toBeGreaterThanOrEqual(0);
    }
  });

  it('空血缘：只有根节点', () => {
    const layout = adaptLineage({ artifact_id: 'solo', parents: [], consumers: [] });
    expect(layout.nodes).toHaveLength(1);
    expect(layout.nodes[0].kind).toBe('root');
  });
});
