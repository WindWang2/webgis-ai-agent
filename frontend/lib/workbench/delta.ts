/**
 * Workbench delta —— 组织态增量补丁纯函数（Workbench V6 / ADR-0119）。
 *
 * 与后端 `app/services/collab/delta.py` 语义镜像（differential 测试锚定）：
 * 绝对值语义（重放幂等）、固定应用次序、部分字段组更新、引用存在性检查。
 * undo 反演 = inverse delta（field 级），不再整表快照回写（R1-M3/M5）。
 */
import type { WorkbenchDocV5 } from './doc';

/** 组部分字段补丁（缺省键不动；id 不存在 = 创建，要求 name）。 */
export interface WorkbenchGroupPatch {
  id: string;
  name?: string;
  collapsed?: boolean;
  parentId?: string | null;
}

export interface MembershipSetEntry {
  layerId: string;
  groupId: string;
}

/** delta 各列表域上限（与后端同值；超出 = 拒绝提交，回退全量路径）。 */
export const MAX_SET_GROUPS = 2000;
export const MAX_REMOVE_GROUPS = 2000;
export const MAX_MEMBERSHIP_SET = 2000;
export const MAX_MEMBERSHIP_CLEAR = 2000;
export const MAX_LOCKS = 2000;

export interface WorkbenchDelta {
  setGroups?: WorkbenchGroupPatch[];
  removeGroupIds?: string[];
  membershipSet?: MembershipSetEntry[];
  membershipClear?: string[];
  locksAdd?: string[];
  locksRemove?: string[];
}

interface GroupsById {
  [id: string]: { id: string; name: string; collapsed: boolean; parentId: string | null };
}

function groupsByIdOf(doc: WorkbenchDocV5): GroupsById {
  const map: GroupsById = {};
  for (const g of doc.groups) map[g.id] = { ...g };
  return map;
}

/** 子孙组 id（含自身）；parent 缺失按根处理（与投影/归一化语义一致）。 */
function descendantsOf(groupsById: GroupsById, roots: Set<string>): Set<string> {
  const children: Record<string, string[]> = {};
  for (const [gid, g] of Object.entries(groupsById)) {
    if (g.parentId != null && groupsById[g.parentId]) {
      (children[g.parentId] ??= []).push(gid);
    }
  }
  const out = new Set<string>();
  const stack = [...roots];
  while (stack.length > 0) {
    const cur = stack.pop()!;
    if (out.has(cur)) continue;
    out.add(cur);
    for (const child of children[cur] ?? []) stack.push(child);
  }
  return out;
}

export class WorkbenchDeltaError extends Error {}

/**
 * 应用 delta（已 validate 的形状）→ 新 doc（不改入参）。
 * 结构性非法（引用不存在组、set+remove 同 id）抛 WorkbenchDeltaError。
 */
export function applyWorkbenchDelta(doc: WorkbenchDocV5, delta: WorkbenchDelta): WorkbenchDocV5 {
  const groupsById = groupsByIdOf(doc);

  // 1. setGroups：create / 部分字段 patch。
  for (const patch of delta.setGroups ?? []) {
    const existing = groupsById[patch.id];
    if (existing == null) {
      if (patch.name == null || patch.name === '') {
        throw new WorkbenchDeltaError(`group ${patch.id} does not exist; create requires name.`);
      }
      groupsById[patch.id] = {
        id: patch.id,
        name: patch.name,
        collapsed: patch.collapsed ?? false,
        parentId: patch.parentId ?? null,
      };
    } else {
      if (patch.name !== undefined) existing.name = patch.name;
      if (patch.collapsed !== undefined) existing.collapsed = patch.collapsed;
      if (patch.parentId !== undefined) existing.parentId = patch.parentId;
    }
  }

  // 2. removeGroupIds：set+remove 同 id 冲突；级联子孙 + membership 清空。
  const removeIds = new Set(delta.removeGroupIds ?? []);
  for (const patch of delta.setGroups ?? []) {
    if (removeIds.has(patch.id)) {
      throw new WorkbenchDeltaError(`setGroups and removeGroupIds collide on ${patch.id}.`);
    }
  }
  let removedGroups: Set<string> = new Set();
  if (removeIds.size > 0) {
    removedGroups = descendantsOf(groupsById, removeIds);
    for (const gid of removedGroups) delete groupsById[gid];
  }

  // 3. membershipSet（目标组必须此刻存在）→ membershipClear（Set 优先）。
  // 只清指向**本次被移除组**的键（与后端管线一致 —— 既有悬空键透传容忍）。
  const membership: Record<string, string> = {};
  for (const [layerId, groupId] of Object.entries(doc.membership)) {
    if (removedGroups.has(groupId)) continue;
    membership[layerId] = groupId;
  }
  for (const { layerId, groupId } of delta.membershipSet ?? []) {
    if (!groupsById[groupId]) {
      throw new WorkbenchDeltaError(`membershipSet target group ${groupId} does not exist.`);
    }
    membership[layerId] = groupId;
  }
  const setIds = new Set((delta.membershipSet ?? []).map((m) => m.layerId));
  for (const layerId of delta.membershipClear ?? []) {
    if (!setIds.has(layerId)) delete membership[layerId];
  }

  // 4. locks（保序：先 add 后 remove 的净效果，集合语义）。
  const lockSet = new Set(doc.lockedLayerIds);
  for (const id of delta.locksAdd ?? []) lockSet.add(id);
  for (const id of delta.locksRemove ?? []) lockSet.delete(id);

  return {
    version: 5,
    groups: Object.values(groupsById),
    membership,
    lockedLayerIds: [...lockSet],
    mode: doc.mode,
  };
}

/**
 * 结构化 diff：before → after 的最小 delta（O(n)）。
 * 无差异返回 null（零提交）。diff 失败/超限由调用方回退全量路径。
 * 非法变更（如 membership 指向不存在的组）返回 null（宁可全量也不要非法 delta）。
 */
export function diffWorkbenchDocs(before: WorkbenchDocV5, after: WorkbenchDocV5): WorkbenchDelta | null {
  const delta: WorkbenchDelta = {};
  const groupIdsBefore = new Set(before.groups.map((g) => g.id));
  const groupIdsAfter = new Set(after.groups.map((g) => g.id));
  const beforeById = new Map(before.groups.map((g) => [g.id, g]));

  // 组：改字段（before 有）+ 新建（before 无）。
  const setGroups: WorkbenchGroupPatch[] = [];
  for (const g of after.groups) {
    const prev = beforeById.get(g.id);
    if (prev == null) {
      setGroups.push({ id: g.id, name: g.name, collapsed: g.collapsed, parentId: g.parentId });
    } else if (
      prev.name !== g.name || prev.collapsed !== g.collapsed || prev.parentId !== g.parentId
    ) {
      const patch: WorkbenchGroupPatch = { id: g.id };
      if (prev.name !== g.name) patch.name = g.name;
      if (prev.collapsed !== g.collapsed) patch.collapsed = g.collapsed;
      if (prev.parentId !== g.parentId) patch.parentId = g.parentId;
      setGroups.push(patch);
    }
  }
  if (setGroups.length > 0) delta.setGroups = setGroups;
  if (setGroups.length > MAX_SET_GROUPS) return null;

  // 删除的组。
  const removeGroupIds = [...groupIdsBefore].filter((id) => !groupIdsAfter.has(id));
  if (removeGroupIds.length > 0) delta.removeGroupIds = removeGroupIds;
  if (removeGroupIds.length > MAX_REMOVE_GROUPS) return null;

  // membership：键级增改删。
  const membershipSet: MembershipSetEntry[] = [];
  const membershipClear: string[] = [];
  for (const [layerId, groupId] of Object.entries(after.membership)) {
    if (before.membership[layerId] !== groupId) membershipSet.push({ layerId, groupId });
  }
  for (const layerId of Object.keys(before.membership)) {
    if (after.membership[layerId] == null) membershipClear.push(layerId);
  }
  if (membershipSet.length > 0) delta.membershipSet = membershipSet;
  if (membershipClear.length > 0) delta.membershipClear = membershipClear;
  if (membershipSet.length > MAX_MEMBERSHIP_SET || membershipClear.length > MAX_MEMBERSHIP_CLEAR) return null;

  // 锁：集合差。
  const beforeLocks = new Set(before.lockedLayerIds);
  const afterLocks = new Set(after.lockedLayerIds);
  const locksAdd = after.lockedLayerIds.filter((id) => !beforeLocks.has(id));
  const locksRemove = before.lockedLayerIds.filter((id) => !afterLocks.has(id));
  if (locksAdd.length > 0) delta.locksAdd = locksAdd;
  if (locksRemove.length > 0) delta.locksRemove = locksRemove;
  if (locksAdd.length > MAX_LOCKS || locksRemove.length > MAX_LOCKS) return null;

  // mode 不在 delta 域（V5 R1-M2）；delta 覆盖不了 mode 变更 → 由全量路径处理。
  if (before.mode !== after.mode) return null;

  if (Object.keys(delta).length === 0) return null;

  // 合法性预检：diff 出的 membershipSet 必须指向 after 中存在的组（否则
  // 服务端会 400 —— 此时回退全量让服务端校验兜底语义保持一致）。
  for (const { groupId } of membershipSet) {
    if (!groupIdsAfter.has(groupId)) return null;
  }
  // 新建组的 parentId 必须指向 after 中存在的组（顺序无关的目标态）。
  for (const patch of setGroups) {
    if (patch.parentId != null && !groupIdsAfter.has(patch.parentId)) return null;
  }
  return delta;
}

/**
 * inverse delta：撤销 forward 在 before → after 上的效果（field 级）。
 * 构造自捕获的 forward delta 与前置 doc —— 并发下只回退本命令触碰的字段
 * （R1-M5：不再整表快照回写）。不可构造时抛错（调用方 journal-only）。
 */
export function invertWorkbenchDelta(
  forward: WorkbenchDelta,
  beforeDoc: WorkbenchDocV5,
): WorkbenchDelta {
  const inverse: WorkbenchDelta = {};

  // 反演 locks。
  if (forward.locksAdd?.length) inverse.locksRemove = [...forward.locksAdd];
  if (forward.locksRemove?.length) inverse.locksAdd = [...forward.locksRemove];

  // 反演 membership：Set → 恢复 before 值（before 无该键 → Clear）；
  // Clear → 恢复 before 值。forward 未触碰的键不动。
  const membershipSetBack: MembershipSetEntry[] = [];
  const membershipClearBack: string[] = [];
  for (const { layerId } of forward.membershipSet ?? []) {
    const prev = beforeDoc.membership[layerId];
    if (prev == null) membershipClearBack.push(layerId);
    else membershipSetBack.push({ layerId, groupId: prev });
  }
  for (const layerId of forward.membershipClear ?? []) {
    const prev = beforeDoc.membership[layerId];
    if (prev != null) membershipSetBack.push({ layerId, groupId: prev });
  }
  if (membershipSetBack.length > 0) inverse.membershipSet = membershipSetBack;
  if (membershipClearBack.length > 0) inverse.membershipClear = membershipClearBack;

  // 反演组：被删的 → 恢复 before 全节点（含被级联删的子孙）；被建的 → 删除；
  // 被改的 → 只回被改的字段。
  const createdIds = new Set<string>();
  const removedTrees: WorkbenchGroupPatch[] = [];
  const patchedBack: WorkbenchGroupPatch[] = [];
  const beforeById = new Map(beforeDoc.groups.map((g) => [g.id, g]));

  for (const patch of forward.setGroups ?? []) {
    const prev = beforeById.get(patch.id);
    if (prev == null) {
      createdIds.add(patch.id); // forward 创建 → inverse 删除
    } else {
      const back: WorkbenchGroupPatch = { id: patch.id };
      if (patch.name !== undefined && patch.name !== prev.name) back.name = prev.name;
      if (patch.collapsed !== undefined && patch.collapsed !== prev.collapsed) back.collapsed = prev.collapsed;
      if (patch.parentId !== undefined && patch.parentId !== prev.parentId) back.parentId = prev.parentId;
      if (back.name !== undefined || back.collapsed !== undefined || back.parentId !== undefined) {
        patchedBack.push(back);
      }
    }
  }
  for (const id of forward.removeGroupIds ?? []) {
    // 级联删除需恢复整个被删子树（before 中的子孙闭包）。
    const stack = [id];
    const seen = new Set<string>();
    while (stack.length > 0) {
      const cur = stack.pop()!;
      if (seen.has(cur)) continue;
      seen.add(cur);
      const prev = beforeById.get(cur);
      if (prev == null) continue;
      removedTrees.push({ id: prev.id, name: prev.name, collapsed: prev.collapsed, parentId: prev.parentId });
      // before 中以 cur 为父的组也在被删闭包内（与后端级联语义一致）。
      for (const g of beforeDoc.groups) {
        if (g.parentId === cur && !seen.has(g.id)) stack.push(g.id);
      }
    }
  }

  // 删除组的 inverse 先于字段回补（保证 parentId 引用存在性：先建组再挂父）。
  if (removedTrees.length > 0) inverse.setGroups = removedTrees;
  if (patchedBack.length > 0) (inverse.setGroups ??= []).push(...patchedBack);
  if (createdIds.size > 0) inverse.removeGroupIds = [...createdIds];

  // inverse 中 membershipSet 的目标组必须在 forward 前的 doc 中存在（不然
  // inverse 无法构造 —— 调用方捕获后 journal-only）。
  for (const m of inverse.membershipSet ?? []) {
    if (!beforeDoc.groups.some((g) => g.id === m.groupId)) {
      throw new WorkbenchDeltaError(`cannot invert: group ${m.groupId} missing in prior doc.`);
    }
  }

  if (Object.keys(inverse).length === 0) {
    throw new WorkbenchDeltaError('nothing to invert.');
  }
  return inverse;
}
