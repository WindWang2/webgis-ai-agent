/**
 * Layer Workspace projection（Workbench V4 / Wave 2）—— 纯函数投影层。
 *
 * 把四份既有事实投影成「专业图层工作台」需要的树视图：
 *   - useHudStore.layers（行元数据 + 数组序 = z-order 唯一真相）
 *   - workbenchSlice.layerGroups / layerGroupMembership（用户分组树，UI projection）
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
}

export interface WorkspaceSection {
  /** 用户组 id；未分组区为 null。 */
  id: string | null;
  name: string;
  collapsed: boolean;
  rows: WorkspaceRow[];
}

export interface WorkspaceProjection {
  sections: WorkspaceSection[];
  /** 展开可见的行数（collapsed 区不计）。 */
  visibleRowCount: number;
}

export interface ProjectWorkspaceInput {
  layers: Layer[];
  groups: LayerGroupEntity[];
  membership: Record<string, string>;
  lockedLayerIds?: readonly string[];
  selectedLayerIds?: readonly string[];
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
 * - 用户组按创建序展示（成员按 store 数组序保持 z-order 可读性）；
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

  for (const layer of layers) {
    const row = {
      layer,
      groupId: membership[layer.id] ?? null,
      semanticGroup: layer.group || 'default',
      locked: lockedLayerIds.includes(layer.id),
      selected: selectedLayerIds.includes(layer.id),
    };
    if (!matchesSearch(row, search)) continue;
    const gid = membership[layer.id] ?? null;
    // 用户的 membership 指向已删除的组 → 视作未分组（组实体离场即失效）。
    const bucket = byGroup.has(gid) ? gid : null;
    byGroup.get(bucket)!.push(row);
  }

  const sections: WorkspaceSection[] = [];
  for (const group of groups) {
    sections.push({
      id: group.id,
      name: group.name,
      collapsed: group.collapsed,
      rows: byGroup.get(group.id) ?? [],
    });
  }

  // 语义区分区（未按用户分组的行）。
  const ungroupedRows = byGroup.get(null) ?? [];
  const semanticKeys = new Set<string>();
  for (const row of ungroupedRows) semanticKeys.add(row.semanticGroup);
  for (const key of SEMANTIC_GROUP_ORDER) {
    if (!semanticKeys.has(key)) continue;
    sections.push({
      id: null,
      name: key,
      collapsed: false,
      rows: ungroupedRows.filter((row) => row.semanticGroup === key),
    });
  }
  if (semanticKeys.has('default')) {
    sections.push({
      id: null,
      name: 'default',
      collapsed: false,
      rows: ungroupedRows.filter((row) => row.semanticGroup === 'default'),
    });
  }
  // 兜底：语义组词表外的值（防御 —— 不丢行）。
  for (const key of semanticKeys) {
    if (key === 'default' || (SEMANTIC_GROUP_ORDER as readonly string[]).includes(key)) continue;
    sections.push({
      id: null,
      name: key,
      collapsed: false,
      rows: ungroupedRows.filter((row) => row.semanticGroup === key),
    });
  }

  const visibleRowCount = sections.reduce(
    (sum, section) => sum + (section.collapsed ? 0 : section.rows.length),
    0,
  );
  return { sections, visibleRowCount };
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
