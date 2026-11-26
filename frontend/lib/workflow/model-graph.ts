/**
 * 模型作者图编辑纯函数（可视化 GIS 模型构建器前端域核）。
 *
 * 契约对照（单一事实源在后端，本文件是**作者面静态投影**，不实现第二份
 * 科学语义 —— 与 app/services/model_authoring/contracts.py 的端口推导 /
 * 兼容裁决逐条对齐，词表漂移由 model-graph.test.ts 对齐测试兜住）：
 * - ModelDocument v1 → app/schemas/model_authoring_schema.py；
 * - 端口推导 node_ports / 宽类型兼容 ports_compatible → contracts.py；
 * - transform wire id ``transform:<op>:<author>`` → compile.py。
 *
 * 纯函数、零 I/O、零后端调用：连线合法性预检（canConnect）是纯前端同规则
 * 实现（编辑期即时反馈），后端 validate 仍是唯一权威出口。
 */

/* ── ModelDocument v1 类型（schema 逐字段；layout 是纯 UI 坐标） ─────── */

export type ModelNodeKind = 'data_input' | 'transform' | 'analysis' | 'cartography' | 'output';

export type ModelParamKind =
  | 'number'
  | 'integer'
  | 'string'
  | 'enum'
  | 'boolean'
  | 'field'
  | 'unit'
  | 'time_window';

export interface ModelParamValue {
  value: unknown;
}

export interface ModelNodeParams {
  values: Record<string, ModelParamValue>;
}

export interface ModelNode {
  node_id: string;
  kind: ModelNodeKind;
  title?: string;
  description?: string;
  /** data_input：数据角色（⊆ DATA_ROLES）。 */
  role?: string;
  /** analysis：能力 id（⊆ CapabilityRegistry）。 */
  capability?: string;
  /** analysis：钉定输入端口 artifact 类型。 */
  input_artifact_type?: string;
  /** transform：算子（⊆ EXECUTABLE_TRANSFORM_OPS）。 */
  transform_op?: string;
  params?: ModelNodeParams;
  optional?: boolean;
}

export interface ModelEdge {
  from_node: string;
  /** 缺省 "out"；data_input 节点必须写其 role 名。 */
  from_port?: string;
  to_node: string;
  /** 空串 = 主输入 "in"。 */
  to_port?: string;
}

export interface ModelSource {
  kind: 'manual' | 'project_workflow' | 'session_plan' | 'template';
  ref?: string;
}

export interface ModelParamSpec {
  name: string;
  kind: ModelParamKind;
  label?: string;
  required?: boolean;
  default?: ModelParamValue | null;
  minimum?: number | null;
  maximum?: number | null;
  choices?: string[];
  unit?: string;
  description?: string;
  input_node?: string;
}

export interface ModelLayoutPos {
  x: number;
  y: number;
}

export interface ModelDocument {
  schema_version: '1';
  name: string;
  description?: string;
  source: ModelSource;
  params?: ModelParamSpec[];
  nodes: ModelNode[];
  edges: ModelEdge[];
  output_node_ids?: string[];
  /** 纯 UI 坐标（不参与科学指纹）。 */
  layout?: Record<string, ModelLayoutPos>;
  template_id?: string;
}

/** capabilities 端点的单能力 IO 投影（canConnect 的可选目录上下文）。 */
export interface CapabilityIO {
  input_artifact_types: string[];
  output_artifact_types: string[];
}

export type CapabilityIOMap = Record<string, CapabilityIO>;

/* ── artifact 类型词表（app/lib/gis/artifacts.py 注册表只读快照） ────── */

interface ArtifactMeta {
  category: string;
  geometry: string;
}

/**
 * 注册表快照（category / geometry_kind）。词表漂移容忍：未登记类型按
 * 未注册裁决（同后端 reg.get() is None 分支），新类型只需补表。
 */
const ARTIFACT_TYPES: Record<string, ArtifactMeta> = {
  feature_collection: { category: 'feature_set', geometry: 'unknown' },
  chart_spec: { category: 'table', geometry: 'table' },
  point_feature_set: { category: 'feature_set', geometry: 'point' },
  line_feature_set: { category: 'feature_set', geometry: 'line' },
  polygon_feature_set: { category: 'feature_set', geometry: 'polygon' },
  poi_feature_set: { category: 'feature_set', geometry: 'point' },
  admin_boundary_set: { category: 'feature_set', geometry: 'polygon' },
  admin_aggregate_table: { category: 'aggregate', geometry: 'polygon' },
  stats_table: { category: 'table', geometry: 'table' },
  density_surface: { category: 'surface', geometry: 'polygon' },
  grid_aggregate: { category: 'aggregate', geometry: 'polygon' },
  hotspot_result: { category: 'aggregate', geometry: 'point' },
  proximity_zone: { category: 'zone', geometry: 'polygon' },
  service_area: { category: 'zone', geometry: 'polygon' },
  raster_surface: { category: 'raster', geometry: 'raster' },
  terrain_surface: { category: 'raster', geometry: 'raster' },
  remote_sensing_index: { category: 'raster', geometry: 'raster' },
  rs_cube_descriptor: { category: 'raster', geometry: 'raster' },
  change_set: { category: 'change', geometry: 'polygon' },
  od_matrix: { category: 'network', geometry: 'network' },
  od_table: { category: 'network', geometry: 'network' },
  network_graph: { category: 'network', geometry: 'network' },
  scene_config: { category: 'scene', geometry: 'unknown' },
  // map_image 由 cartography 端口推导使用但不在注册表（同后端 reg.get → None）。
};

/** 宽特征类型（运行时绑定门 ANY-FEATURE 端口，contracts._WIDE_FEATURE_TYPE 同词）。 */
const WIDE_FEATURE_TYPE = 'feature_collection';
/** 宽特征端口可喂入的目标几何族（先于 category 裁决 —— contracts 同序）。 */
/** 运行时绑定门 `_ANY_FEATURE_CATEGORIES`（binding.py 单一事实源）的镜像。 */
const ANY_FEATURE_CATEGORY = 'feature_set';

/**
 * 边兼容裁决（contracts.ports_compatible 同规则 —— 运行时绑定门语义的
 * 忠实镜像，P2-1 对齐）：同类型直通 → 目标空类型（output 汇点）收一切 →
 * 目标宽类型只收 feature_set 类源（`_ANY_FEATURE_CATEGORIES` 同门）→
 * 其余（宽喂窄/窄喂窄≠/未知类型）运行时门必拒 —— 前端同样拒绝。
 */
export function portsCompatible(srcArtifact: string, dstArtifact: string): boolean {
  if (srcArtifact === dstArtifact) return true;
  if (dstArtifact === '') return true; // output 汇点：任意产物
  const src = ARTIFACT_TYPES[srcArtifact];
  const dst = ARTIFACT_TYPES[dstArtifact];
  if (src == null || dst == null) return false;
  if (dstArtifact === WIDE_FEATURE_TYPE) {
    return src.category === ANY_FEATURE_CATEGORY;
  }
  return false;
}

/* ── 角色端口默认（contracts._ROLE_PORT_DEFAULTS 同表） ──────────────── */

/** role → (默认 artifact_type, geometry_kind)；未知角色给宽端口。 */
const ROLE_PORT_DEFAULTS: Record<string, { artifactType: string; geometryKind: string }> = {
  subject: { artifactType: WIDE_FEATURE_TYPE, geometryKind: 'unknown' },
  measure: { artifactType: 'stats_table', geometryKind: 'table' },
  boundary: { artifactType: 'admin_boundary_set', geometryKind: 'polygon' },
  denominator: { artifactType: 'stats_table', geometryKind: 'table' },
  network: { artifactType: WIDE_FEATURE_TYPE, geometryKind: 'network' },
  elevation: { artifactType: 'terrain_surface', geometryKind: 'raster' },
  hazard: { artifactType: WIDE_FEATURE_TYPE, geometryKind: 'unknown' },
  receptor: { artifactType: WIDE_FEATURE_TYPE, geometryKind: 'unknown' },
  population: { artifactType: 'stats_table', geometryKind: 'table' },
  facility: { artifactType: 'poi_feature_set', geometryKind: 'point' },
  criteria: { artifactType: WIDE_FEATURE_TYPE, geometryKind: 'unknown' },
  constraint: { artifactType: WIDE_FEATURE_TYPE, geometryKind: 'unknown' },
  reference: { artifactType: WIDE_FEATURE_TYPE, geometryKind: 'unknown' },
  baseline: { artifactType: WIDE_FEATURE_TYPE, geometryKind: 'unknown' },
  target_time: { artifactType: WIDE_FEATURE_TYPE, geometryKind: 'unknown' },
  comparison_time: { artifactType: WIDE_FEATURE_TYPE, geometryKind: 'unknown' },
};

export interface PortInfo {
  name: string;
  artifactType: string;
  geometryKind: string;
  required: boolean;
}

/**
 * 节点输出端口清单（contracts.node_ports 同推导）：
 * data_input 单输出（名 = role，宽类型按角色默认）；其余单输出 "out"。
 */
export function nodeOutputPorts(node: ModelNode, catalog?: CapabilityIOMap | null): PortInfo[] {
  if (node.kind === 'data_input') {
    const def = ROLE_PORT_DEFAULTS[node.role ?? ''] ?? {
      artifactType: WIDE_FEATURE_TYPE,
      geometryKind: 'unknown',
    };
    return [
      {
        name: node.role || 'out',
        artifactType: def.artifactType,
        geometryKind: def.geometryKind,
        required: true,
      },
    ];
  }
  if (node.kind === 'transform') {
    return [{ name: 'out', artifactType: WIDE_FEATURE_TYPE, geometryKind: 'unknown', required: true }];
  }
  if (node.kind === 'analysis') {
    // 目录缺席时与后端 _cap_io 空集回退同形：feature_collection。
    const produced = catalog?.[node.capability ?? '']?.output_artifact_types ?? [];
    return [
      {
        name: 'out',
        artifactType: produced[0] ?? WIDE_FEATURE_TYPE,
        geometryKind: 'unknown',
        required: true,
      },
    ];
  }
  if (node.kind === 'cartography') {
    return [{ name: 'out', artifactType: 'map_image', geometryKind: 'unknown', required: true }];
  }
  return []; // output：汇点，无输出
}

/**
 * 节点输入端口清单（contracts.node_ports 同推导，P2-1 对齐）：
 * data_input 无输入；transform/cartography 单输入 "in"（宽特征）；
 * output 汇点空 artifactType + required=false（任意产物收口点，收口不做
 * 基数门）；analysis 单输入 "in"（钉定类型或宽特征缺省 —— 与后端同构）。
 */
export function nodeInputPorts(node: ModelNode, _catalog?: CapabilityIOMap | null): PortInfo[] {
  if (node.kind === 'data_input') return [];
  if (node.kind === 'output') {
    return [{ name: 'in', artifactType: '', geometryKind: 'unknown', required: false }];
  }
  if (node.kind === 'transform' || node.kind === 'cartography') {
    return [{ name: 'in', artifactType: WIDE_FEATURE_TYPE, geometryKind: 'unknown', required: true }];
  }
  const pinned = node.input_artifact_type || WIDE_FEATURE_TYPE;
  return [{ name: 'in', artifactType: pinned, geometryKind: 'unknown', required: true }];
}

/* ── 拓扑（环检测 / 层序） ──────────────────────────────────────────── */

/** 邻接表（from → to[]；doc 边序保持）。 */
function adjacency(edges: ModelEdge[]): Map<string, string[]> {
  const adj = new Map<string, string[]>();
  for (const e of edges) {
    const list = adj.get(e.from_node) ?? [];
    list.push(e.to_node);
    adj.set(e.from_node, list);
  }
  return adj;
}

/**
 * DFS 三色找环（validate._find_cycle 同算法）。返回环节点序列（首尾同
 * 回头点）；无环返回 []。
 */
export function findCycle(doc: ModelDocument): string[] {
  const adj = adjacency(doc.edges);
  const WHITE = 0;
  const GRAY = 1;
  const BLACK = 2;
  const color = new Map<string, number>(doc.nodes.map((n) => [n.node_id, WHITE]));
  const stack: string[] = [];
  const found: string[] = [];

  const visit = (nid: string): boolean => {
    color.set(nid, GRAY);
    stack.push(nid);
    for (const nxt of adj.get(nid) ?? []) {
      const state = color.get(nxt) ?? BLACK;
      if (state === GRAY) {
        const idx = stack.indexOf(nxt);
        found.push(...stack.slice(idx), nxt);
        return true;
      }
      if (state === WHITE && visit(nxt)) return true;
    }
    stack.pop();
    color.set(nid, BLACK);
    return false;
  };

  for (const n of doc.nodes) {
    if (color.get(n.node_id) === WHITE && visit(n.node_id)) break;
  }
  return found;
}

/** 添加 from→to 边是否成环（to 当前可达 from 即成环；自环亦然）。 */
export function createsCycle(doc: ModelDocument, fromNode: string, toNode: string): boolean {
  if (fromNode === toNode) return true;
  // 反向 BFS：从 from 沿入边找 to —— 等价于「to 可达 from」。
  const radj = new Map<string, string[]>();
  for (const e of doc.edges) {
    const list = radj.get(e.to_node) ?? [];
    list.push(e.from_node);
    radj.set(e.to_node, list);
  }
  const seen = new Set<string>([fromNode]);
  const stack = [fromNode];
  while (stack.length > 0) {
    const nid = stack.pop() as string;
    if (nid === toNode) return true;
    for (const prev of radj.get(nid) ?? []) {
      if (!seen.has(prev)) {
        seen.add(prev);
        stack.push(prev);
      }
    }
  }
  return false;
}

/** 拓扑层序（Kahn；compile._topo_order 同策略，余量节点按 doc 序追加）。 */
export function topoOrder(doc: ModelDocument): string[] {
  const indeg = new Map<string, number>(doc.nodes.map((n) => [n.node_id, 0]));
  for (const e of doc.edges) {
    indeg.set(e.to_node, (indeg.get(e.to_node) ?? 0) + 1);
  }
  const order: string[] = [];
  const ready = doc.nodes.filter((n) => (indeg.get(n.node_id) ?? 0) === 0).map((n) => n.node_id);
  const queue = [...ready];
  const outdeg = adjacency(doc.edges);
  while (queue.length > 0) {
    const nid = queue.shift() as string;
    order.push(nid);
    for (const nxt of outdeg.get(nid) ?? []) {
      const left = (indeg.get(nxt) ?? 1) - 1;
      indeg.set(nxt, left);
      if (left === 0) queue.push(nxt);
    }
  }
  // 环上节点不在 Kahn 序内 —— 按 doc 序追加（与 compile 同策略，保底全覆盖）。
  for (const n of doc.nodes) {
    if (!order.includes(n.node_id)) order.push(n.node_id);
  }
  return order;
}

/* ── 连线合法性预检（编辑期即时反馈；后端 validate 仍是权威） ─────────── */

export type ConnectRejectReason =
  | 'unknownNode'
  | 'targetDataInput'
  | 'sourcePortUnknown'
  | 'targetPortUnknown'
  | 'cycle'
  | 'typeMismatch';

export interface ConnectVerdict {
  ok: boolean;
  /** i18n key（modelBuilder.connect.*）；ok=true 时为 undefined。 */
  reason?: ConnectRejectReason;
  /** 规范化边（from_port 补 "out"；to_port 空补 "in" 语义保持空串落库）。 */
  edge?: ModelEdge;
}

/**
 * 连线预检（编辑器拖拽连线的即时合法性裁决；validate 的边检查子集）：
 * 1. 端点节点存在；2. 目标不是 data_input（数据不能流入数据入口）；
 * 3. from_port ∈ 源输出端口（data_input 的输出端口名 = role）；
 * 4. to_port（空 = "in"）∈ 目标输入端口；
 * 5. 端口 artifact 类型兼容（宽类型 feature_collection 规则）；
 * 6. 不成环。
 */
export function canConnect(
  doc: ModelDocument,
  fromNode: string,
  fromPort: string,
  toNode: string,
  toPort: string,
  catalog?: CapabilityIOMap | null,
): ConnectVerdict {
  const byId = new Map(doc.nodes.map((n) => [n.node_id, n]));
  const src = byId.get(fromNode);
  const dst = byId.get(toNode);
  if (!src || !dst) return { ok: false, reason: 'unknownNode' };
  if (dst.kind === 'data_input') return { ok: false, reason: 'targetDataInput' };

  const outs = nodeOutputPorts(src, catalog);
  const outPort = outs.find((p) => p.name === (fromPort || 'out'));
  if (!outPort) return { ok: false, reason: 'sourcePortUnknown' };
  const ins = nodeInputPorts(dst, catalog);
  const inPort = ins.find((p) => p.name === (toPort || 'in'));
  if (!inPort) return { ok: false, reason: 'targetPortUnknown' };
  if (!portsCompatible(outPort.artifactType, inPort.artifactType)) {
    return { ok: false, reason: 'typeMismatch' };
  }
  if (createsCycle(doc, fromNode, toNode)) return { ok: false, reason: 'cycle' };
  return {
    ok: true,
    // 落库约定：主输入 to_port 空串（"in" 是推导别名，归一化回空串）。
    edge: {
      from_node: fromNode,
      from_port: fromPort || 'out',
      to_node: toNode,
      to_port: toPort && toPort !== 'in' ? toPort : '',
    },
  };
}

/* ── wire id ↔ 作者 id 映射（compile._wire_node_id 逆变换） ──────────── */

/**
 * 实例 wire node_id → 作者 node_id。transform 节点的 wire id 是
 * ``transform:<op>:<author>``（op 含 ":" 不可能 —— 词表 [a-z]+），
 * ``rsplit(":", 1)[1]`` 可逆还原；其余 kind 恒等映射。
 */
export function authorNodeId(wireId: string): string {
  const sep = wireId.lastIndexOf(':');
  // 无 ":" 恒等；形如 "transform:buffer:n1" 的前缀才 rsplit —— 纯命名空间
  // 前缀（"data:subject"）不是 transform wire id，author id 即整串。
  if (sep > 0 && wireId.startsWith('transform:')) return wireId.slice(sep + 1);
  return wireId;
}

/** 作者节点 → wire node_id（compile 同形；测试与画布徽标对齐用）。 */
export function wireNodeId(node: ModelNode): string {
  if (node.kind === 'transform' && node.transform_op) {
    return `transform:${node.transform_op}:${node.node_id}`;
  }
  return node.node_id;
}

/* ── 分层布局（lineage-adapter 的列宽/行高风格，作者图全量层排） ─────── */

export const LAYOUT_COL_WIDTH = 200;
export const LAYOUT_ROW_HEIGHT = 88;
export const LAYOUT_PADDING = 24;

export interface LayeredLayout {
  positions: Record<string, ModelLayoutPos>;
  maxColumn: number;
  maxRows: number;
}

/**
 * 分层布局：列 = 节点最长路深度（data_input 起算），列内按 topo 序堆叠。
 * 已有 layout 坐标的节点保持原位（编辑者拖放不被重排）；其余节点入列。
 */
export function layeredLayout(doc: ModelDocument): LayeredLayout {
  const positions: Record<string, ModelLayoutPos> = {};
  for (const [nid, pos] of Object.entries(doc.layout ?? {})) positions[nid] = { ...pos };

  // 列 = 最长路深度（入度剥离）；环上节点列 0（不卡死）。
  const depth = new Map<string, number>();
  const indeg = new Map<string, number>(doc.nodes.map((n) => [n.node_id, 0]));
  const radj = new Map<string, string[]>();
  for (const e of doc.edges) {
    indeg.set(e.to_node, (indeg.get(e.to_node) ?? 0) + 1);
    const list = radj.get(e.from_node) ?? [];
    list.push(e.to_node);
    radj.set(e.from_node, list);
  }
  const queue = doc.nodes.filter((n) => (indeg.get(n.node_id) ?? 0) === 0).map((n) => n.node_id);
  for (const nid of queue) depth.set(nid, 0);
  let head = 0;
  while (head < queue.length) {
    const nid = queue[head++];
    for (const nxt of radj.get(nid) ?? []) {
      const candidate = (depth.get(nid) ?? 0) + 1;
      if (candidate > (depth.get(nxt) ?? -1)) depth.set(nxt, candidate);
      const left = (indeg.get(nxt) ?? 1) - 1;
      indeg.set(nxt, left);
      if (left === 0) queue.push(nxt);
    }
  }

  const columns = new Map<number, string[]>();
  for (const n of doc.nodes) {
    if (positions[n.node_id]) continue; // 已有坐标不重排
    const col = depth.get(n.node_id) ?? 0;
    const bucket = columns.get(col) ?? [];
    bucket.push(n.node_id);
    columns.set(col, bucket);
  }

  let maxColumn = 0;
  let maxRows = 0;
  for (const [col, ids] of [...columns].sort((a, b) => a[0] - b[0])) {
    ids.forEach((nid, row) => {
      positions[nid] = {
        x: LAYOUT_PADDING + col * LAYOUT_COL_WIDTH,
        y: LAYOUT_PADDING + row * LAYOUT_ROW_HEIGHT,
      };
    });
    maxColumn = Math.max(maxColumn, col);
    maxRows = Math.max(maxRows, ids.length);
  }
  // 已有坐标也可能超出列范围 —— 尺寸按实际坐标收敛。
  for (const pos of Object.values(positions)) {
    maxColumn = Math.max(maxColumn, Math.round((pos.x - LAYOUT_PADDING) / LAYOUT_COL_WIDTH));
  }
  return { positions, maxColumn, maxRows };
}

/** 新节点落点：目标列（前插节点的下一列）内首个空行；布局冲突时向右顺延。 */
export function findFreeSpot(doc: ModelDocument, afterNodeId?: string): ModelLayoutPos {
  const { positions } = layeredLayout(doc);
  const occupied = new Set(
    Object.values(positions).map((p) => `${Math.round(p.x)}:${Math.round(p.y)}`),
  );
  const anchor = afterNodeId ? positions[afterNodeId] : undefined;
  const baseCol = anchor
    ? Math.round((anchor.x - LAYOUT_PADDING) / LAYOUT_COL_WIDTH) + 1
    : 0;
  for (let col = baseCol; col < baseCol + 16; col++) {
    for (let row = 0; row < 32; row++) {
      const x = LAYOUT_PADDING + col * LAYOUT_COL_WIDTH;
      const y = LAYOUT_PADDING + row * LAYOUT_ROW_HEIGHT;
      if (!occupied.has(`${x}:${y}`)) return { x, y };
    }
  }
  return { x: LAYOUT_PADDING + baseCol * LAYOUT_COL_WIDTH, y: LAYOUT_PADDING };
}

/* ── 节点 slug 词法（schema._check_slug 同规则） ─────────────────────── */

const SLUG_RE = /^[a-z][a-z0-9_]{0,28}$/;

/** 节点/参数名 slug 词法：[a-z][a-z0-9_]、≤29 字符（schema 同规则）。 */
export function isValidSlug(value: string): boolean {
  return SLUG_RE.test(value);
}

/** 唯一化 node_id：非法字符折叠 + 撞名追加序号（生成新节点默认 id 用）。 */
export function uniqueNodeId(doc: ModelDocument, base: string): string {
  const taken = new Set(doc.nodes.map((n) => n.node_id));
  const cleaned = (base.match(/[a-z0-9_]+/g) ?? ['n']).join('_').slice(0, 27) || 'n';
  const stem = /^[a-z]/.test(cleaned) ? cleaned : `n_${cleaned}`;
  if (!taken.has(stem)) return stem;
  for (let i = 2; ; i++) {
    const candidate = `${stem}_${i}`;
    if (candidate.length > 29) return `${stem.slice(0, 29 - 2)}_${i}`;
    if (!taken.has(candidate)) return candidate;
  }
}

/* ── 删除节点（连边级联） ───────────────────────────────────────────── */

/** 删除节点的级联图变更：节点 + 关联边 + layout/output 引用一并清除。 */
export function removeNodeCascade(
  doc: ModelDocument,
  nodeId: string,
): { document: ModelDocument; removedEdges: ModelEdge[] } {
  const removedEdges = doc.edges.filter((e) => e.from_node === nodeId || e.to_node === nodeId);
  const layout = { ...(doc.layout ?? {}) };
  delete layout[nodeId];
  return {
    document: {
      ...doc,
      nodes: doc.nodes.filter((n) => n.node_id !== nodeId),
      edges: doc.edges.filter((e) => e.from_node !== nodeId && e.to_node !== nodeId),
      output_node_ids: (doc.output_node_ids ?? []).filter((oid) => oid !== nodeId),
      layout,
    },
    removedEdges,
  };
}
