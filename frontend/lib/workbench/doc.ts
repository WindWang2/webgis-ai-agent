/**
 * Workbench Document V5 —— 工作台组织态的持久化模型（Workbench V5 / W1）。
 *
 * V4 的分组树（workbenchSlice.layerGroups）是单层、易失的 UI projection。
 * V5 把「组织态」（嵌套分组树 / 成员归属 / 锁 / 模式）提升为 WorkbenchDocV5，
 * 经 MapSpec `workbench` 分支（patch_workbench_state 意图，同一锁+CAS 链）
 * 持久化 —— 本模块只提供纯函数模型与迁移/归一化，不触碰 store / 网络。
 *
 * 单一事实源不变式：doc 是 backend MapSpec 的前端投影；store 只是运行时
 * 载体。不在这里建第二持久化（localStorage / IndexedDB）。
 */
import type { WorkbenchMode } from '@/lib/store/slices/workbenchSlice';

/** 嵌套深度上限（扁平 V4 = 1；V5 允许 4 层，防失控深树与栈风险）。 */
export const WORKBENCH_GROUP_MAX_DEPTH = 4;

/**
 * doc 持久化体积上限（真实 UTF-8 字节；后端同款闸）。
 * 256KB 依据 10k 图层目标场景：全量 membership（layerId→groupId 平铺）在
 * 10k 键时约 250-300KB —— 64KB 会让旗舰场景的持久化整体停摆（R2-C1）。
 * 组织态仍不携带数据载荷（大载荷属 layers/sources/ref 通道）。
 */
export const WORKBENCH_DOC_MAX_BYTES = 256 * 1024;

/** 恢复归一化的数量上限（与体积闸共同约束 —— 防畸形广播/手写 payload）。 */
export const WORKBENCH_MAX_GROUPS = 2000;
export const WORKBENCH_GROUP_NAME_MAX = 200;
export const WORKBENCH_MAX_MEMBERSHIP = 20_000;
export const WORKBENCH_MAX_LOCKED = 20_000;

export interface WorkbenchGroupNode {
  id: string;
  name: string;
  collapsed: boolean;
  /** null = 根；必须指向树中更早出现的组（无环）。 */
  parentId: string | null;
}

/** Workbench V5 组织态文档（后端 mapspec.workbench 的 schema）。 */
export interface WorkbenchDocV5 {
  version: 5;
  groups: WorkbenchGroupNode[];
  /** layerId → 组 id；指向不存在组的成员在投影时视作未分组。 */
  membership: Record<string, string>;
  lockedLayerIds: string[];
  mode: WorkbenchMode;
}

/** V4 扁平分组实体（无 parentId）。 */
export interface WorkbenchGroupV4 {
  id: string;
  name: string;
  collapsed: boolean;
}

export function emptyWorkbenchDoc(): WorkbenchDocV5 {
  return { version: 5, groups: [], membership: {}, lockedLayerIds: [], mode: 'explore' };
}

/**
 * V4 → V5 迁移：V4 分组从无持久化（会话切换即清空），因此不存在盘上
 * 历史；本函数用于运行时旧 store 形状（无 parentId 字段）升级 —— 全部
 * 视作根组，成员归属与折叠态原样保留。
 */
export function migrateV4Groups(
  groups: readonly WorkbenchGroupV4[],
  membership: Record<string, string>,
  lockedLayerIds: readonly string[],
  mode: WorkbenchMode,
): WorkbenchDocV5 {
  return {
    version: 5,
    groups: groups.map((g) => ({ id: g.id, name: g.name, collapsed: g.collapsed, parentId: null })),
    membership: { ...membership },
    lockedLayerIds: [...lockedLayerIds],
    mode,
  };
}

/** 树助手的结构最小类型（LayerGroupEntity 的 parentId 是可选字段）。 */
export interface GroupNodeLike {
  id: string;
  parentId?: string | null;
}

/** 组 id → 深度（根 = 1）；parentId 指向不存在组视作根（防御脏数据）。 */
export function groupDepth(groups: readonly GroupNodeLike[], id: string): number {
  const byId = new Map(groups.map((g) => [g.id, g]));
  let depth = 1;
  let cur = byId.get(id);
  const seen = new Set<string>([id]);
  while (cur?.parentId) {
    if (seen.has(cur.parentId)) return depth; // 环：按当前深度截断
    seen.add(cur.parentId);
    cur = byId.get(cur.parentId);
    if (!cur) break;
    depth += 1;
  }
  return depth;
}

/** 子孙组 id 集合（不含自身）。V6：children 索引一次建表 —— O(n)（原
 * frontier.includes 为 O(n×f)，大树下 reparent 校验放大，G11）。 */
export function descendantGroupIds(
  groups: readonly GroupNodeLike[],
  id: string,
): Set<string> {
  const out = new Set<string>();
  const childrenOf = new Map<string, string[]>();
  for (const g of groups) {
    if (g.parentId == null) continue;
    const bucket = childrenOf.get(g.parentId);
    if (bucket != null) bucket.push(g.id);
    else childrenOf.set(g.parentId, [g.id]);
  }
  let frontier = [id];
  while (frontier.length > 0) {
    const next: string[] = [];
    for (const fid of frontier) {
      for (const child of childrenOf.get(fid) ?? []) {
        if (!out.has(child)) {
          out.add(child);
          next.push(child);
        }
      }
    }
    frontier = next;
  }
  return out;
}

/**
 * reparent 合法性：目标父不能是自己或自己的子孙（成环），且落点深度
 * 不超上限。返回 null = 非法（调用方拒绝并保持原状）。
 */
export function canReparentGroup(
  groups: readonly GroupNodeLike[],
  id: string,
  newParentId: string | null,
): boolean {
  if (id === newParentId) return false;
  if (newParentId == null) return true;
  if (!groups.some((g) => g.id === newParentId)) return false;
  if (descendantGroupIds(groups, id).has(newParentId)) return false;
  const newParentDepth = groupDepth(groups, newParentId);
  const subtreeHeight = subtreeMaxDepth(groups, id);
  return newParentDepth + subtreeHeight <= WORKBENCH_GROUP_MAX_DEPTH;
}

/** 子树高度（自身为 1；叶子 = 1）。V6：children 索引层序提升 —— O(n)。 */
export function subtreeMaxDepth(groups: readonly GroupNodeLike[], id: string): number {
  const childrenOf = new Map<string, string[]>();
  for (const g of groups) {
    if (g.parentId == null) continue;
    const bucket = childrenOf.get(g.parentId);
    if (bucket != null) bucket.push(g.id);
    else childrenOf.set(g.parentId, [g.id]);
  }
  // 层序：起点为第 1 层；环由 visited 防护（与投影层同款防御）。
  const visited = new Set<string>([id]);
  let frontier = [id];
  let depth = 1;
  while (frontier.length > 0) {
    const next: string[] = [];
    for (const fid of frontier) {
      for (const child of childrenOf.get(fid) ?? []) {
        if (!visited.has(child)) {
          visited.add(child);
          next.push(child);
        }
      }
    }
    if (next.length > 0) depth += 1;
    frontier = next;
  }
  return depth;
}

/** 根组按创建序展开（组实体在 doc.groups 的数组序 = 各层内展示序）。 */
export function rootGroupsFirst(groups: readonly GroupNodeLike[]): GroupNodeLike[] {
  return groups.filter((g) => {
    if (g.parentId == null) return true;
    // 父缺失的孤儿视作根（防御：恢复归一化已保证，这里兜底不丢组）。
    return !groups.some((p) => p.id === g.parentId);
  });
}

/**
 * 恢复归一化：后端/广播来的任意 payload → 合法 doc；非法则返回 null
 * （调用方保持当前 doc 不变）。环/超深/孤儿在结构层修复（孤儿提升为根，
 * 超深环截断），成员/锁保留原始键（stale layer id 由既有 prune 清理）。
 * 数量上限（组数/名称长度/成员与锁键数）与体积闸共同约束畸形 payload。
 */
export function normalizeWorkbenchDoc(raw: unknown): WorkbenchDocV5 | null {
  if (typeof raw !== 'object' || raw == null) return null;
  const candidate = raw as Partial<WorkbenchDocV5> & { version?: number };
  if (candidate.version !== 5) return null;
  if (!Array.isArray(candidate.groups) || candidate.groups.length > WORKBENCH_MAX_GROUPS) return null;
  const mode: WorkbenchMode =
    candidate.mode === 'analyze' || candidate.mode === 'compose' ? candidate.mode : 'explore';

  const groups: WorkbenchGroupNode[] = [];
  const seen = new Set<string>();
  for (const g of candidate.groups) {
    if (typeof g !== 'object' || g == null) continue;
    const node = g as Partial<WorkbenchGroupNode>;
    if (typeof node.id !== 'string' || node.id.length === 0 || seen.has(node.id)) continue;
    seen.add(node.id);
    const rawName = typeof node.name === 'string' && node.name.length > 0 ? node.name : node.id;
    groups.push({
      id: node.id,
      name: rawName.slice(0, WORKBENCH_GROUP_NAME_MAX),
      collapsed: node.collapsed === true,
      parentId: null, // 先全部落根，第二轮再恢复合法父子
    });
  }
  // 第二轮：恢复 parentId（仅当父已存在、不成环、不超深）。
  const declared = candidate.groups as Array<Partial<WorkbenchGroupNode>>;
  for (const g of groups) {
    const rawParent = declared.find((r) => r?.id === g.id)?.parentId;
    if (typeof rawParent !== 'string' || rawParent === g.id) continue;
    if (!groups.some((p) => p.id === rawParent)) continue;
    // 临时把 g 挂到 rawParent 后校验整树深度（防止恢复超深链）。
    const trial = groups.map((x) => (x.id === g.id ? { ...x, parentId: rawParent } : x));
    if (canReparentGroup(trial, g.id, rawParent)) g.parentId = rawParent;
  }

  const membership: Record<string, string> = {};
  if (typeof candidate.membership === 'object' && candidate.membership != null) {
    let count = 0;
    for (const [layerId, gid] of Object.entries(candidate.membership)) {
      if (count >= WORKBENCH_MAX_MEMBERSHIP) break;
      if (typeof layerId === 'string' && typeof gid === 'string') {
        membership[layerId] = gid;
        count += 1;
      }
    }
  }
  const lockedLayerIds = Array.isArray(candidate.lockedLayerIds)
    ? candidate.lockedLayerIds
        .filter((id): id is string => typeof id === 'string')
        .slice(0, WORKBENCH_MAX_LOCKED)
    : [];
  return { version: 5, groups, membership, lockedLayerIds, mode };
}

/** 持久化体积预检（与后端 64KB 闸对齐；超限调用方应拒绝并提示）。 */
export function workbenchDocBytes(doc: WorkbenchDocV5): number {
  try {
    return new TextEncoder().encode(JSON.stringify(doc)).length;
  } catch {
    return Number.MAX_SAFE_INTEGER;
  }
}
