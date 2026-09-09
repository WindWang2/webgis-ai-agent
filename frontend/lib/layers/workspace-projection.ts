/**
 * Layer Workspace projection（Workbench V4 / Wave 2 → V5 嵌套树 / W1）—— 纯函数投影层。
 *
 * 把四份既有事实投影成「专业图层工作台」需要的树视图：
 *   - useHudStore.layers（行元数据 + 数组序 = z-order 唯一真相）
 *   - workbenchSlice.layerGroups / layerGroupMembership（用户分组树，V5 嵌套）
 *   - workbenchSlice.lockedLayerIds（锁定护栏）
 *   - workbenchSlice.selectedLayerIds（批量操作多选）
 *
 * 纪律：只读、无副作用、不写 store / MapSpec —— 分组与选择是 UI projection，
 * 权威地图内容仍以 MapSpec / backend contract 为准（ADR-0104）。
 * 派生顺序恒等于 store 数组序（= z-order），分组只切分视图不改顺序。
 */
import type { Layer } from '@/lib/types/layer';
import type { LayerGroupEntity } from '@/lib/store/slices/workbenchSlice';

/** 语义组（Layer.group）在树中的展示顺序（未分组恒在最后）。 */
export const SEMANTIC_GROUP_ORDER: readonly string[] = ['base', 'reference', 'analysis'];

export interface WorkspaceRow {
  layer: Layer;
  /** 用户分组 id（无 → null，落在「未分组」区）。 */
  groupId: string | null;
  /** 语义组标签（Layer.group；分析结果挂载路径写入）。 */
  semanticGroup: string;
  locked: boolean;
  selected: boolean;
  /** Review R1（perf MAJOR-2）：可见性在投影时随行携带 —— 组头批量开关
   *  不再对全表做 O(members × total) 的 find 扫描。 */
  visible: boolean;
}

export interface WorkspaceSection {
  /** 用户组 id；未分组区为 null。 */
  id: string | null;
  name: string;
  collapsed: boolean;
  /** V5 嵌套深度（根 = 0；未分组语义区恒 0）。 */
  depth: number;
  rows: WorkspaceRow[];
}

export interface WorkspaceProjection {
  sections: WorkspaceSection[];
  /** 展开可见的行数（自身或任一祖先 collapsed 的区不计）。 */
  visibleRowCount: number;
  /** V5：被祖先折叠隐藏的区 id 集合（渲染层跳过其行 —— 折叠 ≠ 删除）。 */
  hiddenSectionIds: Set<string>;
}

export interface ProjectWorkspaceInput {
  layers: Layer[];
  groups: LayerGroupEntity[];
  membership: Record<string, string>;
  lockedLayerIds?: readonly string[] | ReadonlySet<string>;
  selectedLayerIds?: readonly string[] | ReadonlySet<string>;
  /** 名称搜索（大小写不敏感子串；空 = 不过滤）。 */
  search?: string;
}

function matchesSearch(row: WorkspaceRow, search: string): boolean {
  if (!search) return true;
  const needle = search.trim().toLowerCase();
  if (!needle) return true;
  return row.layer.name.toLowerCase().includes(needle)
    || row.layer.id.toLowerCase().includes(needle)
    || (!!row.layer._refId && row.layer._refId.toLowerCase().includes(needle));
}

/**
 * 投影工作台树：
 * - 用户组按嵌套树 DFS 展示（同层保持创建序；成员按 store 数组序保持
 *   z-order 可读性）；父组 collapsed 时子组与行全部隐藏（折叠传播）；
 * - 未按用户分组的行按**语义组**聚区（base/reference/analysis/未分组），
 *   语义区不可折叠/重命名 —— 那是挂载语义，不是用户组织结构；
 * - search 过滤行（不折叠区：命中 0 行的区仍展示抬头，与 QGIS 一致）。
 */
export function projectWorkspace(input: ProjectWorkspaceInput): WorkspaceProjection {
  // 边界健壮性：部分消费方（测试/mock、渐进接入路径）可能缺 workbench
  // 字段 —— 投影层按「无分组/无锁定/无选择」缺省，绝不抛错。
  const layers = input.layers ?? [];
  const groups = input.groups ?? [];
  const membership = input.membership ?? {};
  const lockedLayerIds = input.lockedLayerIds ?? [];
  const selectedLayerIds = input.selectedLayerIds ?? [];
  const search = input.search?.trim() ?? '';

  const byGroup = new Map<string | null, WorkspaceRow[]>();
  for (const group of groups) byGroup.set(group.id, []);
  byGroup.set(null, []);

  // R2-M5：Set 化锁定/选择 —— 10k 层投影从 O(n×(L+S)) 降为 O(n)。
  const lockedSet = lockedLayerIds instanceof Set
    ? lockedLayerIds
    : new Set(lockedLayerIds);
  const selectedSet = selectedLayerIds instanceof Set
    ? selectedLayerIds
    : new Set(selectedLayerIds);

  for (const layer of layers) {
    const row = {
      layer,
      groupId: membership[layer.id] ?? null,
      semanticGroup: layer.group || 'default',
      locked: lockedSet.has(layer.id),
      selected: selectedSet.has(layer.id),
      visible: layer.visible !== false,
    };
    if (!matchesSearch(row, search)) continue;
    const gid = membership[layer.id] ?? null;
    // 用户的 membership 指向已删除的组 → 视作未分组（组实体离场即失效）。
    const bucket = byGroup.has(gid) ? gid : null;
    byGroup.get(bucket)!.push(row);
  }

  const sections: WorkspaceSection[] = [];
  // 嵌套用户组：根组（含父缺失孤儿）按创建序 DFS；折叠传播 = 祖先 collapsed
  // 时整个子树（行 + 子组）不可见。单 pass 同时产出 sections 与 visibleRowCount。
  const byId = new Map(groups.map((g) => [g.id, g]));
  const childrenOf = new Map<string | null, LayerGroupEntity[]>();
  for (const group of groups) {
    // 父缺失 → 孤儿提升为根（与 doc.ts normalize 语义一致，兜底不丢组）。
    const key = group.parentId != null && byId.has(group.parentId) ? group.parentId : null;
    const bucket = childrenOf.get(key) ?? [];
    bucket.push(group);
    childrenOf.set(key, bucket);
  }
  let visibleRowCount = 0;
  const hiddenSectionIds = new Set<string>();
  // 防御：visited 集保证重复 id / 异常数据（id 撞车）下 walk 恒终止 ——
  // 投影层绝不抛错、不无限递归。
  const walked = new Set<string>();
  const walkGroups = (nodes: LayerGroupEntity[], depth: number, ancestorHidden: boolean) => {
    for (const group of nodes) {
      if (group.id != null && walked.has(group.id)) continue;
      if (group.id != null) walked.add(group.id);
      const hidden = ancestorHidden || group.collapsed;
      const rows = byGroup.get(group.id) ?? [];
      sections.push({
        id: group.id,
        name: group.name,
        collapsed: group.collapsed,
        depth,
        rows,
      });
      if (hidden) hiddenSectionIds.add(group.id);
      else visibleRowCount += rows.length;
      walkGroups(childrenOf.get(group.id) ?? [], depth + 1, hidden);
    }
  };
  walkGroups(childrenOf.get(null) ?? [], 0, false);

  // 语义区分区（未按用户分组的行；恒可见 —— 不参与嵌套折叠）。
  const ungroupedRows = byGroup.get(null) ?? [];
  const semanticKeys = new Set<string>();
  for (const row of ungroupedRows) semanticKeys.add(row.semanticGroup);
  for (const key of SEMANTIC_GROUP_ORDER) {
    if (!semanticKeys.has(key)) continue;
    const rows = ungroupedRows.filter((row) => row.semanticGroup === key);
    sections.push({ id: null, name: key, collapsed: false, depth: 0, rows });
    visibleRowCount += rows.length;
  }
  if (semanticKeys.has('default')) {
    const rows = ungroupedRows.filter((row) => row.semanticGroup === 'default');
    sections.push({ id: null, name: 'default', collapsed: false, depth: 0, rows });
    visibleRowCount += rows.length;
  }
  // 兜底：语义组词表外的值（防御 —— 不丢行）。
  for (const key of semanticKeys) {
    if (key === 'default' || (SEMANTIC_GROUP_ORDER as readonly string[]).includes(key)) continue;
    const rows = ungroupedRows.filter((row) => row.semanticGroup === key);
    sections.push({ id: null, name: key, collapsed: false, depth: 0, rows });
    visibleRowCount += rows.length;
  }

  return { sections, visibleRowCount, hiddenSectionIds };
}

/** 语义组显示名（与现 layers-tab 词表一致）。 */
export function semanticGroupLabel(name: string): string {
  switch (name) {
    case 'analysis': return '分析结果';
    case 'base': return '底图';
    case 'reference': return '参考数据';
    case 'default': return '未分组';
    default: return name;
  }
}
