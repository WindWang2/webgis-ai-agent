/**
 * model-graph 纯函数测试 —— 与后端词表对齐矩阵。
 *
 * 兼容裁决逐条对照 app/services/model_authoring/contracts.py 的
 * node_ports / ports_compatible（fixture 类型名取自 app/lib/gis/artifacts.py
 * 注册表快照）；环检测对照 validate._find_cycle；wire id 对照 compile.py。
 */
import { describe, expect, it } from 'vitest';
import {
  authorNodeId,
  canConnect,
  createsCycle,
  findCycle,
  findFreeSpot,
  isValidSlug,
  layeredLayout,
  nodeInputPorts,
  nodeOutputPorts,
  portsCompatible,
  removeNodeCascade,
  topoOrder,
  uniqueNodeId,
  wireNodeId,
  type CapabilityIOMap,
  type ModelDocument,
  type ModelNode,
} from './model-graph';

function node(partial: Partial<ModelNode> & { node_id: string }): ModelNode {
  return { kind: 'transform', ...partial };
}

function doc(partial: Partial<ModelDocument> & { nodes: ModelNode[] }): ModelDocument {
  return {
    schema_version: '1',
    name: 't',
    source: { kind: 'manual' },
    edges: [],
    ...partial,
  };
}

/** 目录投影（capabilities 端点形状）：polygon buffer 类能力收宽特征，产统计表。 */
const CATALOG: CapabilityIOMap = {
  'cap.hotspot': {
    input_artifact_types: ['feature_collection', 'stats_table'],
    output_artifact_types: ['hotspot_result'],
  },
  'cap.density': {
    input_artifact_types: ['point_feature_set'],
    output_artifact_types: ['density_surface'],
  },
};

describe('portsCompatible（contracts.ports_compatible 对齐）', () => {
  it('同类型直通', () => {
    expect(portsCompatible('stats_table', 'stats_table')).toBe(true);
    expect(portsCompatible('hotspot_result', 'hotspot_result')).toBe(true);
  });

  it('目标宽类型（feature_collection）收一切', () => {
    expect(portsCompatible('stats_table', 'feature_collection')).toBe(true);
    expect(portsCompatible('raster_surface', 'feature_collection')).toBe(true);
    expect(portsCompatible('map_image', 'feature_collection')).toBe(true);
  });

  it('源宽类型喂特征族（几何 point/line/polygon/network 或 category feature_set/zone/network）', () => {
    expect(portsCompatible('feature_collection', 'point_feature_set')).toBe(true);
    expect(portsCompatible('feature_collection', 'admin_boundary_set')).toBe(true);
    expect(portsCompatible('feature_collection', 'proximity_zone')).toBe(true);
    expect(portsCompatible('feature_collection', 'network_graph')).toBe(true);
    // 几何 polygon 先于 category 裁决：aggregate 族 polygon 也收（后端同序）。
    expect(portsCompatible('feature_collection', 'admin_aggregate_table')).toBe(true);
  });

  it('源宽类型不喂 table/raster；几何族 polygon 的 aggregate 仍收（几何先于 category）', () => {
    expect(portsCompatible('feature_collection', 'stats_table')).toBe(false);
    expect(portsCompatible('feature_collection', 'chart_spec')).toBe(false);
    expect(portsCompatible('feature_collection', 'raster_surface')).toBe(false);
    // grid_aggregate 几何 polygon —— 后端 _FEATURE_GEOMETRIES 先裁，收。
    expect(portsCompatible('feature_collection', 'grid_aggregate')).toBe(true);
  });

  it('非宽类型不同 category 拒绝；未注册类型仅同名放行', () => {
    expect(portsCompatible('stats_table', 'chart_spec')).toBe(true); // 同 table 族
    expect(portsCompatible('stats_table', 'hotspot_result')).toBe(false);
    // map_image 不在注册表（cartography 输出）—— 同名直通、异名拒绝。
    expect(portsCompatible('map_image', 'map_image')).toBe(true);
    expect(portsCompatible('map_image', 'scene_config')).toBe(false);
  });
});

describe('端口推导（contracts.node_ports 对齐）', () => {
  it('data_input 输出端口名 = role；角色默认宽类型；未知角色给宽端口', () => {
    const subject = node({ node_id: 'a', kind: 'data_input', role: 'subject' });
    expect(nodeOutputPorts(subject)).toEqual([
      { name: 'subject', artifactType: 'feature_collection', geometryKind: 'unknown', required: true },
    ]);
    const measure = node({ node_id: 'm', kind: 'data_input', role: 'measure' });
    expect(nodeOutputPorts(measure)[0].artifactType).toBe('stats_table');
    const unknownRole = node({ node_id: 'u', kind: 'data_input', role: 'whatever' });
    expect(nodeOutputPorts(unknownRole)[0].artifactType).toBe('feature_collection');
  });

  it('transform 单输入/输出 "in"/"out"（宽特征）；output 无输出端口', () => {
    const n = node({ node_id: 'b', kind: 'transform', transform_op: 'buffer' });
    expect(nodeInputPorts(n)[0].artifactType).toBe('feature_collection');
    expect(nodeOutputPorts(n)[0].name).toBe('out');
    const sink = node({ node_id: 'o', kind: 'output' });
    expect(nodeOutputPorts(sink)).toEqual([]);
    expect(nodeInputPorts(sink)[0].name).toBe('in');
  });

  it('analysis 输入 = 钉定类型或目录接受集首项；输出 = 目录产出首项；目录缺席回退宽类型', () => {
    const pinned = node({ node_id: 'c', kind: 'analysis', capability: 'cap.density', input_artifact_type: 'poi_feature_set' });
    expect(nodeInputPorts(pinned, CATALOG)[0].artifactType).toBe('poi_feature_set');
    const unpinned = node({ node_id: 'c2', kind: 'analysis', capability: 'cap.density' });
    expect(nodeInputPorts(unpinned, CATALOG)[0].artifactType).toBe('point_feature_set');
    expect(nodeOutputPorts(unpinned, CATALOG)[0].artifactType).toBe('density_surface');
    const noCatalog = node({ node_id: 'c3', kind: 'analysis', capability: 'cap.x' });
    expect(nodeInputPorts(noCatalog)[0].artifactType).toBe('feature_collection');
    expect(nodeOutputPorts(noCatalog)[0].artifactType).toBe('feature_collection');
  });

  it('cartography 产 map_image', () => {
    const c = node({ node_id: 'k', kind: 'cartography' });
    expect(nodeOutputPorts(c)[0].artifactType).toBe('map_image');
    expect(nodeInputPorts(c)[0].artifactType).toBe('feature_collection');
  });
});

describe('canConnect 合法/非法矩阵', () => {
  const base = doc({
    nodes: [
      node({ node_id: 'src', kind: 'data_input', role: 'subject' }),
      node({ node_id: 'tbl', kind: 'data_input', role: 'measure' }),
      node({ node_id: 'buf', kind: 'transform', transform_op: 'buffer' }),
      node({ node_id: 'cap', kind: 'analysis', capability: 'cap.hotspot' }),
      node({ node_id: 'map', kind: 'cartography' }),
      node({ node_id: 'sink', kind: 'output' }),
    ],
  });

  it('合法：data_input(role 输出端口) → transform → analysis → output', () => {
    expect(canConnect(base, 'src', 'subject', 'buf', '', CATALOG).ok).toBe(true);
    expect(canConnect(base, 'buf', 'out', 'cap', '', CATALOG).ok).toBe(true);
    expect(canConnect(base, 'buf', 'out', 'sink', 'in', CATALOG).ok).toBe(true);
  });

  it('from_port 必须写 data_input 的 role 名（写 "out" 拒绝）', () => {
    expect(canConnect(base, 'src', 'out', 'buf', '', CATALOG)).toMatchObject({ ok: false, reason: 'sourcePortUnknown' });
    // to_port 缺省 = 主输入 "in"。
    const v = canConnect(base, 'src', 'subject', 'buf', '', CATALOG);
    expect(v.edge).toEqual({ from_node: 'src', from_port: 'subject', to_node: 'buf', to_port: '' });
  });

  it('data_input 无输入端口：流入数据入口拒绝', () => {
    expect(canConnect(base, 'buf', 'out', 'src', '', CATALOG)).toMatchObject({ ok: false, reason: 'targetDataInput' });
  });

  it('table 角色不收宽特征源；宽特征可进 analysis 的 table 钉定? —— 宽→table 拒', () => {
    // cap.hotspot 接受集首项 feature_collection —— measure 表可进（table→wide ok）。
    expect(canConnect(base, 'tbl', 'measure', 'cap', '', CATALOG).ok).toBe(true);
    // 宽特征 → stats_table 端口（transform 的 in 是宽类型，不涉 stats_table）；
    // 直接验证端口兼容裁决：wide→stats_table 拒。
    expect(portsCompatible('feature_collection', 'stats_table')).toBe(false);
  });

  it('类型不匹配：stats_table → 点集钉定能力拒绝', () => {
    // 目录只给 cap.hotspot 钉 point_feature_set（覆盖 base 的默认目录）。
    expect(canConnect(base, 'tbl', 'measure', 'cap', '', {
      'cap.hotspot': { input_artifact_types: ['point_feature_set'], output_artifact_types: ['hotspot_result'] },
    })).toMatchObject({ ok: false, reason: 'typeMismatch' });
  });

  it('未知端口名拒绝', () => {
    expect(canConnect(base, 'src', 'subject', 'buf', 'side', CATALOG)).toMatchObject({ ok: false, reason: 'targetPortUnknown' });
    expect(canConnect(base, 'src', 'nope', 'buf', '', CATALOG)).toMatchObject({ ok: false, reason: 'sourcePortUnknown' });
  });

  it('成环拒绝（transform → 自身 / 既有下游回连非 data_input 节点）', () => {
    const loop = doc({
      nodes: [...base.nodes, node({ node_id: 'buf2', kind: 'transform', transform_op: 'clip' })],
      edges: [
        { from_node: 'src', from_port: 'subject', to_node: 'buf', to_port: '' },
        { from_node: 'buf', to_node: 'buf2', to_port: '' },
      ],
    });
    // 回连 data_input 先触发 targetDataInput（数据不能流入数据入口）。
    expect(canConnect(loop, 'buf', 'out', 'src', '', CATALOG)).toMatchObject({ ok: false, reason: 'targetDataInput' });
    expect(canConnect(loop, 'buf2', 'out', 'buf', '', CATALOG)).toMatchObject({ ok: false, reason: 'cycle' });
    expect(canConnect(loop, 'buf', 'out', 'buf', '', CATALOG)).toMatchObject({ ok: false, reason: 'cycle' });
    expect(canConnect(loop, 'buf', 'out', 'buf2', '', CATALOG).ok).toBe(true);
  });
});

describe('拓扑（环检测 / 层序）', () => {
  it('findCycle 复刻 DFS 三色：无环 [] / 有环含回头点', () => {
    const acyclic = doc({
      nodes: [node({ node_id: 'a' }), node({ node_id: 'b' }), node({ node_id: 'c' })],
      edges: [
        { from_node: 'a', to_node: 'b' },
        { from_node: 'b', to_node: 'c' },
      ],
    });
    expect(findCycle(acyclic)).toEqual([]);
    const cyclic = doc({
      nodes: [node({ node_id: 'a' }), node({ node_id: 'b' }), node({ node_id: 'c' })],
      edges: [
        { from_node: 'a', to_node: 'b' },
        { from_node: 'b', to_node: 'c' },
        { from_node: 'c', to_node: 'a' },
      ],
    });
    const cycle = findCycle(cyclic);
    expect(cycle).toHaveLength(4);
    expect(cycle[0]).toBe(cycle[3]);
  });

  it('createsCycle：自环 true；to 可达 from 时 true；普通前向边 false', () => {
    const d = doc({
      nodes: [node({ node_id: 'a' }), node({ node_id: 'b' }), node({ node_id: 'c' })],
      edges: [{ from_node: 'a', to_node: 'b' }],
    });
    expect(createsCycle(d, 'a', 'a')).toBe(true);
    expect(createsCycle(d, 'b', 'a')).toBe(true);
    expect(createsCycle(d, 'a', 'c')).toBe(false);
    expect(createsCycle(d, 'b', 'c')).toBe(false);
  });

  it('topoOrder：Kahn 序 + 环上节点保底追加', () => {
    const d = doc({
      nodes: [node({ node_id: 'c' }), node({ node_id: 'a' }), node({ node_id: 'b' })],
      edges: [
        { from_node: 'a', to_node: 'b' },
        { from_node: 'b', to_node: 'c' },
      ],
    });
    expect(topoOrder(d)).toEqual(['a', 'b', 'c']);
    const cyclic = doc({
      nodes: [node({ node_id: 'x' }), node({ node_id: 'y' })],
      edges: [
        { from_node: 'x', to_node: 'y' },
        { from_node: 'y', to_node: 'x' },
      ],
    });
    expect(topoOrder(cyclic).sort()).toEqual(['x', 'y']);
  });
});

describe('wire id 映射（compile._wire_node_id 对齐）', () => {
  it('transform 节点 wire id = transform:<op>:<author>；authorNodeId rsplit 可逆', () => {
    expect(wireNodeId({ node_id: 'n1', kind: 'transform', transform_op: 'buffer' })).toBe('transform:buffer:n1');
    expect(authorNodeId('transform:buffer:n1')).toBe('n1');
    expect(authorNodeId('transform:reproject:long_node_id_29char_max')).toBe('long_node_id_29char_max');
  });

  it('非 transform wire id 恒等（含普通命名空间前缀）', () => {
    expect(authorNodeId('n1')).toBe('n1');
    expect(authorNodeId('data:subject')).toBe('data:subject');
    expect(wireNodeId({ node_id: 'n1', kind: 'analysis', capability: 'c' })).toBe('n1');
  });
});

describe('分层布局 / 落点', () => {
  it('layeredLayout：列 = 最长路深度，已有坐标不重排', () => {
    const d = doc({
      nodes: [
        node({ node_id: 'src', kind: 'data_input', role: 'subject' }),
        node({ node_id: 'buf' }),
        node({ node_id: 'cap', kind: 'analysis', capability: 'c' }),
        node({ node_id: 'sink', kind: 'output' }),
      ],
      edges: [
        { from_node: 'src', from_port: 'subject', to_node: 'buf', to_port: '' },
        { from_node: 'buf', to_node: 'cap', to_port: '' },
        { from_node: 'cap', to_node: 'sink', to_port: '' },
      ],
      layout: { sink: { x: 900, y: 5 } },
    });
    const layout = layeredLayout(d);
    expect(layout.positions.src.x).toBeLessThan(layout.positions.buf.x);
    expect(layout.positions.buf.x).toBeLessThan(layout.positions.cap.x);
    expect(layout.positions.sink).toEqual({ x: 900, y: 5 });
  });

  it('findFreeSpot：锚点右侧下一列首个空位', () => {
    const d = doc({
      nodes: [
        node({ node_id: 'a' }),
        node({ node_id: 'b', layout_ready: undefined } as Partial<ModelNode> & { node_id: string }),
      ],
      layout: { a: { x: 24, y: 24 }, b: { x: 224, y: 24 } },
    });
    const spot = findFreeSpot(d, 'a');
    expect(spot.x).toBe(224);
    expect(spot.y).not.toBe(24); // (224,24) 已被 b 占用 → 顺延下一行
  });
});

describe('slug 词法与级联删除', () => {
  it('isValidSlug：[a-z][a-z0-9_] ≤29', () => {
    expect(isValidSlug('buffer_in_300m')).toBe(true);
    expect(isValidSlug('Buffer')).toBe(false);
    expect(isValidSlug('9lives')).toBe(false);
    expect(isValidSlug('a'.repeat(29))).toBe(true);
    expect(isValidSlug('a'.repeat(30))).toBe(false);
    expect(isValidSlug('has-dash')).toBe(false);
  });

  it('uniqueNodeId：撞名追加序号且不超 29', () => {
    const d = doc({ nodes: [node({ node_id: 'buf' }), node({ node_id: 'buf_2' })] });
    expect(uniqueNodeId(d, 'buf')).toBe('buf_3');
    expect(uniqueNodeId(d, 'Buffer Zone!')).toMatch(/^[a-z][a-z0-9_]*$/);
  });

  it('removeNodeCascade：节点 + 关联边 + layout/output 引用级联清除', () => {
    const d = doc({
      nodes: [node({ node_id: 'a' }), node({ node_id: 'b' }), node({ node_id: 'c' })],
      edges: [
        { from_node: 'a', to_node: 'b' },
        { from_node: 'b', to_node: 'c' },
        { from_node: 'a', to_node: 'c' },
      ],
      output_node_ids: ['c'],
      layout: { a: { x: 0, y: 0 }, b: { x: 1, y: 1 } },
    });
    const { document: next, removedEdges } = removeNodeCascade(d, 'b');
    expect(next.nodes.map((n) => n.node_id)).toEqual(['a', 'c']);
    // 触及 b 的两条边级联删除；不触及的 a→c 保留。
    expect(next.edges).toEqual([{ from_node: 'a', to_node: 'c' }]);
    expect(removedEdges).toEqual([
      { from_node: 'a', to_node: 'b' },
      { from_node: 'b', to_node: 'c' },
    ]);
    expect(next.layout).toEqual({ a: { x: 0, y: 0 } });
  });
});
