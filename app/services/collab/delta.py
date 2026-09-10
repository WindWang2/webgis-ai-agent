"""Workbench delta —— 组织态增量补丁的纯函数管线（Workbench V6 / ADR-0119）。

delta schema（**绝对值语义** —— 全部字段是目标态而非增减量，重放幂等）::

    {
      "setGroups":       [{"id": str, "name"?: str, "collapsed"?: bool,
                           "parentId"?: str|null}, ...]   # 部分字段更新；
                                                          # id 不存在 = 创建（要求 name）
      "removeGroupIds":  [str, ...]                      # 级联子孙 + membership 清空
      "membershipSet":   [{"layerId": str, "groupId": str}, ...]
      "membershipClear": [layerId, ...]
      "locksAdd":        [layerId, ...]
      "locksRemove":     [layerId, ...]
    }

应用管线（固定次序，评审 R1-M4 —— setGroups → removeGroupIds →
membershipSet → membershipClear → locks）：
1. setGroups：create / 部分字段 patch（parentId 必须引用当前已存在组）；
2. removeGroupIds：与 setGroups 同 id → 冲突；级联移除子孙组；
3. membershipSet：目标组必须此刻存在；同键 Clear 后 Set（Set 优先）；
4. locksAdd / locksRemove；
5. 结果 doc 重跑引擎全量校验（调用方负责）+ 盖 `_rev`（调用方负责）。

mode 不在 delta 域（V5 R1-M2 决策一致：mode 不参与组织态撤销/增量）。
本模块纯函数、无 IO；引擎与前端共享同一语义（前端 TypeScript 侧为
镜像实现，differential 测试锚定）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

#: delta 各列表域上限（patch 级；超出 = 400，不静默截断）。
MAX_SET_GROUPS = 2000
MAX_REMOVE_GROUPS = 2000
MAX_MEMBERSHIP_SET = 2000
MAX_MEMBERSHIP_CLEAR = 2000
MAX_LOCKS = 2000
#: delta 载荷字节上限（总线与持久化通道各自预算中的 delta 份额）。
MAX_DELTA_BYTES = 64 * 1024

_RESERVED_DOC_KEYS = frozenset({"version", "groups", "membership", "lockedLayerIds", "mode", "_rev"})


class DeltaError(ValueError):
    """delta 非法（结构/引用/冲突/超限）——调用方映射 400。"""


def validate_delta(delta: Any) -> Dict[str, Any]:
    """结构校验（不查引用）；非法抛 DeltaError。返回规范化后的 delta。"""
    if not isinstance(delta, dict):
        raise DeltaError("workbench delta must be an object.")
    unknown = set(delta) - {
        "setGroups", "removeGroupIds", "membershipSet", "membershipClear",
        "locksAdd", "locksRemove",
    }
    if unknown:
        raise DeltaError(f"workbench delta has unknown fields: {sorted(unknown)!r}.")
    if not any(delta.get(k) for k in delta):
        raise DeltaError("workbench delta is empty.")

    def _str_list(field: str) -> List[str]:
        value = delta.get(field)
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
            raise DeltaError(f"workbench delta.{field} must be a list of non-empty strings.")
        return value

    set_groups = delta.get("setGroups") or []
    if not isinstance(set_groups, list) or len(set_groups) > MAX_SET_GROUPS:
        raise DeltaError(f"workbench delta.setGroups must be a list ≤ {MAX_SET_GROUPS}.")
    norm_groups: List[Dict[str, Any]] = []
    for entry in set_groups:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise DeltaError("workbench delta.setGroups entries require string id.")
        patch: Dict[str, Any] = {"id": entry["id"]}
        if "name" in entry:
            if not isinstance(entry["name"], str) or not entry["name"]:
                raise DeltaError("workbench delta.setGroups name must be a non-empty string.")
            patch["name"] = entry["name"][:200]
        if "collapsed" in entry:
            if not isinstance(entry["collapsed"], bool):
                raise DeltaError("workbench delta.setGroups collapsed must be boolean.")
            patch["collapsed"] = entry["collapsed"]
        if "parentId" in entry:
            if entry["parentId"] is not None and not isinstance(entry["parentId"], str):
                raise DeltaError("workbench delta.setGroups parentId must be a string or null.")
            patch["parentId"] = entry["parentId"]
        norm_groups.append(patch)

    remove_ids = _str_list("removeGroupIds")
    if len(remove_ids) > MAX_REMOVE_GROUPS:
        raise DeltaError(f"workbench delta.removeGroupIds ≤ {MAX_REMOVE_GROUPS}.")

    membership_set = delta.get("membershipSet") or []
    if not isinstance(membership_set, list) or len(membership_set) > MAX_MEMBERSHIP_SET:
        raise DeltaError(f"workbench delta.membershipSet ≤ {MAX_MEMBERSHIP_SET} entries.")
    norm_ms: List[Tuple[str, str]] = []
    for entry in membership_set:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("layerId"), str)
            or not entry["layerId"]
            or not isinstance(entry.get("groupId"), str)
            or not entry["groupId"]
        ):
            raise DeltaError("workbench delta.membershipSet entries require layerId+groupId strings.")
        norm_ms.append((entry["layerId"], entry["groupId"]))

    membership_clear = _str_list("membershipClear")
    if len(membership_clear) > MAX_MEMBERSHIP_CLEAR:
        raise DeltaError(f"workbench delta.membershipClear ≤ {MAX_MEMBERSHIP_CLEAR}.")

    locks_add = _str_list("locksAdd")
    locks_remove = _str_list("locksRemove")
    if len(locks_add) > MAX_LOCKS or len(locks_remove) > MAX_LOCKS:
        raise DeltaError(f"workbench delta locks ≤ {MAX_LOCKS}.")

    norm = {
        "setGroups": norm_groups,
        "removeGroupIds": remove_ids,
        "membershipSet": norm_ms,
        "membershipClear": membership_clear,
        "locksAdd": locks_add,
        "locksRemove": locks_remove,
    }
    # R1-M4 预算兑现：patch 自身 ≤64KB（防请求体放大；全量 doc 由 256KB 闸管）。
    import json as _json

    try:
        encoded = len(_json.dumps(norm, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise DeltaError("workbench delta is not JSON-serializable.") from None
    if encoded > MAX_DELTA_BYTES:
        raise DeltaError(f"workbench delta exceeds {MAX_DELTA_BYTES // 1024}KB; split the patch.")
    return norm


def _descendants(groups_by_id: Dict[str, Dict[str, Any]], root_ids: set[str]) -> set[str]:
    """子孙组 id（含自身）；parent 缺失按根处理（与投影/归一化语义一致）。"""
    children: Dict[str, List[str]] = {}
    for gid, g in groups_by_id.items():
        parent = g.get("parentId")
        if isinstance(parent, str) and parent in groups_by_id:
            children.setdefault(parent, []).append(gid)
    out: set[str] = set()
    stack = list(root_ids)
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        stack.extend(children.get(cur, []))
    return out


def apply_delta(doc: Optional[Dict[str, Any]], delta: Dict[str, Any]) -> Dict[str, Any]:
    """把（已 validate 的）delta 应用到当前 doc → 新 doc（COW，不改入参）。

    结构性非法（引用不存在组、set+remove 同 id 等）抛 DeltaError —— 调用方
    映射 400（无半更新）。应用结果仍需引擎 `_workbench_doc_error` 全量校验
    （深度/环/体积在结果级复核 —— 部分字段 patch 可能造出超深链）。
    """
    base: Dict[str, Any] = dict(doc) if isinstance(doc, dict) else {}
    groups: List[Dict[str, Any]] = [dict(g) for g in base.get("groups", []) or [] if isinstance(g, dict)]
    membership: Dict[str, str] = {
        str(k): str(v) for k, v in (base.get("membership", {}) or {}).items()
    }
    locked: List[str] = [str(x) for x in (base.get("lockedLayerIds", []) or [])]

    groups_by_id: Dict[str, Dict[str, Any]] = {g["id"]: g for g in groups}
    version = base.get("version", 5)
    mode = base.get("mode", "explore")

    # 1. setGroups：create / 部分字段 patch。
    for patch in delta["setGroups"]:
        gid = patch["id"]
        existing = groups_by_id.get(gid)
        if existing is None:
            if "name" not in patch:
                raise DeltaError(f"workbench delta: group {gid!r} does not exist; create requires name.")
            collapsed = patch.get("collapsed")
            node = {
                "id": gid,
                "name": patch.get("name", gid),
                "collapsed": bool(collapsed) if collapsed is not None else False,
                "parentId": patch.get("parentId"),
            }
            groups.append(node)
            groups_by_id[gid] = node
        else:
            for field in ("name", "collapsed", "parentId"):
                if field in patch:
                    existing[field] = patch[field]

    # 2. removeGroupIds：set+remove 同 id 冲突；级联子孙 + membership 清空。
    remove_ids = set(delta["removeGroupIds"])
    conflict = remove_ids & {p["id"] for p in delta["setGroups"]}
    if conflict:
        raise DeltaError(f"workbench delta: setGroups and removeGroupIds collide on {sorted(conflict)!r}.")
    if remove_ids:
        cascade = _descendants(groups_by_id, remove_ids)
        groups = [g for g in groups if g["id"] not in cascade]
        for gid in cascade:
            groups_by_id.pop(gid, None)
        if membership:
            membership = {lid: gid for lid, gid in membership.items() if gid not in cascade}

    # 3. membershipSet（目标组必须此刻存在）→ membershipClear（Set 优先）。
    for layer_id, gid in delta["membershipSet"]:
        if gid not in groups_by_id:
            raise DeltaError(f"workbench delta: membershipSet target group {gid!r} does not exist.")
        membership[layer_id] = gid
    cleared = set(delta["membershipClear"]) - {lid for lid, _ in delta["membershipSet"]}
    for layer_id in cleared:
        membership.pop(layer_id, None)

    # 4. locks。
    lock_set = set(locked)
    lock_set.update(delta["locksAdd"])
    lock_set.difference_update(delta["locksRemove"])
    locked = list(lock_set)

    return {
        "version": version,
        "groups": groups,
        "membership": membership,
        "lockedLayerIds": locked,
        "mode": mode if isinstance(mode, str) else "explore",
    }


def doc_workbench_revision(doc: Optional[Dict[str, Any]]) -> Optional[int]:
    """读取 doc 的 `_rev` 盖章（无盖章的旧 doc 返回 None）。"""
    if not isinstance(doc, dict):
        return None
    rev = doc.get("_rev")
    if isinstance(rev, int) and rev >= 0:
        return rev
    return None
