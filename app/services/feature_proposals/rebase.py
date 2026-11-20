"""rebase 与显式裁决 —— resolutions 应用与候选迁移（G32 WP06）。

裁决是**具体内容的裁决**：应用 resolutions 产生新 ops（keep_current 丢弃
该项修改、take_proposed 保留、use_new_value 经 G14 验证器校验后替换），
必然 bump proposal revision + 新 candidate digest —— 旧批准自动失效
（不存在「点过一次批准、后续换内容仍可提交」的通道）。

rebase（新 base = 当前数据）：无未解决冲突时才可执行；新 base 快照 + 新
digest + revision+1。任何被丢弃的修改都留决策记录（decisions.append），不以
成功 rebase 掩盖内容消失。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple


def _validate_new_field_value(
    field: str, value: Any, base_payload: Any,
) -> Optional[Dict[str, Any]]:
    """use_new_value 的字段值经 G14 验证器复核（不绕过旧校验）。"""
    from app.services.feature_edit.attr_ops import profile_fields, validate_set_fields

    fc = base_payload.get("geojson") if isinstance(base_payload, dict) and isinstance(base_payload.get("geojson"), dict) else base_payload
    profiles = profile_fields(fc) if isinstance(fc, dict) else {}
    findings = validate_set_fields({field: value}, profiles)
    return findings[0] if findings else None


def apply_resolutions_to_ops(
    ops: List[Dict[str, Any]],
    resolutions: List[Dict[str, Any]],
    conflicts: List[Dict[str, Any]],
    *,
    base_payload: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把显式裁决应用到候选 ops。

    返回 (new_ops, applied_notes, rejected)。rejected 非空 = 存在无法应用的
    裁决（新值未过验证器 / 冲突不可按该 action 解决）→ 调用方拒绝整个提交，
    保留草稿。

    冲突按 conflict_id 定位（field:<wfid>:<field> / geometry:<wfid> /
    delete_update:<wfid> / target_deleted:<wfid> / parent_retired:* /
    dependency:* / birth_collision:<wfid>）。
    """
    conflict_by_id = {str(c.get("conflict_id")): c for c in conflicts}
    new_ops = copy.deepcopy(ops)
    applied: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    def _remove_field_from_ops(wfid: str, field: str) -> bool:
        """移除 (wfid, field) 写。plan 级 op（无 wfids）无法逐要素剥离 →
        返回 False（调用方拒绝该裁决，防误伤全体目标；R1-P2-9）。"""
        nonlocal new_ops
        touched = False
        for op in new_ops:
            if op.get("op") != "set_fields":
                continue
            targets = op.get("wfids")
            if targets is None:
                continue  # plan 级 —— 不碰（语义是全体目标）
            if wfid not in targets:
                continue
            fields = op.get("fields") or {}
            if field in fields:
                fields.pop(field, None)
                touched = True
        new_ops = [op for op in new_ops if not (op.get("op") == "set_fields" and not (op.get("fields") or {}))]
        return touched

    def _remove_geometry_effect(wfid: str) -> None:
        """移除对该 wfid 的几何修改（几何 op 的 op 级 wfids 去掉该目标；
        plan 级目标（无 wfids 键）几何 op 无法逐要素剥离 → 拒绝该裁决，
        要求作者把几何 op 改成 op 级 wfids 形态）。"""
        nonlocal new_ops
        for op in new_ops:
            if op.get("op") in ("move_by", "snap_to_ref", "repair_geometry", "replace_geometry"):
                targets = op.get("wfids")
                if targets is None:
                    # plan 级目标：无法无损剥离单要素 —— keep_current 需要
                    # 作者提供 op 级限定（或丢弃整个 op）。
                    continue
                if isinstance(targets, list) and wfid in targets:
                    targets.remove(wfid)
        new_ops = [
            op for op in new_ops
            if not (
                op.get("op") in ("move_by", "snap_to_ref", "repair_geometry", "replace_geometry")
                and op.get("wfids") is not None and not op.get("wfids")
            )
        ]

    def _remove_retire(wfid: str) -> None:
        """delete op 的目标集去掉该 wfid。"""
        nonlocal new_ops
        for op in new_ops:
            if op.get("op") != "delete_features":
                continue
            targets = op.get("wfids")
            if isinstance(targets, list) and wfid in targets:
                targets.remove(wfid)
        new_ops = [
            op for op in new_ops
            if not (op.get("op") == "delete_features" and op.get("wfids") is not None and not op.get("wfids"))
        ]

    def _remove_touch(wfid: str) -> None:
        """移除对已删除要素的一切修改（字段/几何/delete）。"""
        _remove_field_from_ops_all(wfid)
        _remove_geometry_effect(wfid)
        _remove_retire(wfid)

    def _remove_field_from_ops_all(wfid: str) -> None:
        """移除对某 wfid 的全部字段写（target_deleted 的字段面）。"""
        nonlocal new_ops
        for op in new_ops:
            if op.get("op") != "set_fields":
                continue
            targets = op.get("wfids")
            if isinstance(targets, list) and wfid in targets:
                targets.remove(wfid)
        new_ops = [
            op for op in new_ops
            if not (op.get("op") == "set_fields" and op.get("wfids") is not None and not op.get("wfids"))
        ]

    for resolution in resolutions:
        cid = str(resolution.get("conflict_id") or "")
        action = str(resolution.get("action") or "")
        conflict = conflict_by_id.get(cid)
        if conflict is None:
            rejected.append({
                "conflict_id": cid,
                "reason": "conflict_id not found in current conflict set — reload conflicts",
            })
            continue
        kind = conflict.get("kind")
        wfid = str(conflict.get("wfid") or "")
        field = conflict.get("field")

        if kind == "field_value_conflict":
            if action == "keep_current":
                if not _remove_field_from_ops(wfid, str(field)):
                    rejected.append({
                        "conflict_id": cid,
                        "reason": (
                            "the proposed write comes from a plan-level op (no wfids) — "
                            "restrict the op to specific features before resolving per-feature"
                        ),
                    })
                    continue
                applied.append({"conflict_id": cid, "action": action,
                                "effect": f"dropped proposed write on {wfid}.{field}"})
            elif action == "take_proposed":
                applied.append({"conflict_id": cid, "action": action,
                                "effect": f"kept proposed value for {wfid}.{field}"})
            elif action == "use_new_value":
                new_fields = resolution.get("new_fields") or {}
                if field not in new_fields:
                    rejected.append({"conflict_id": cid,
                                     "reason": "use_new_value requires the resolved field in new_fields"})
                    continue
                value = new_fields[str(field)]
                bad = _validate_new_field_value(str(field), value, base_payload)
                if bad is not None:
                    rejected.append({
                        "conflict_id": cid,
                        "reason": "new value rejected by the dataset's field validator",
                        "finding": bad,
                    })
                    continue
                _set_field_value(new_ops, wfid, str(field), value)
                applied.append({"conflict_id": cid, "action": action,
                                "effect": f"resolved {wfid}.{field} to a validated new value"})
            else:
                rejected.append({"conflict_id": cid, "reason": f"invalid action for {kind}"})
        elif kind == "geometry_both_modified":
            if action == "keep_current":
                _remove_geometry_effect(wfid)
                applied.append({"conflict_id": cid, "action": action,
                                "effect": f"dropped proposed geometry change on {wfid}"})
            elif action == "take_proposed":
                applied.append({"conflict_id": cid, "action": action,
                                "effect": f"kept proposed geometry for {wfid}"})
            elif action == "use_new_value":
                new_geometry = resolution.get("new_geometry")
                if not isinstance(new_geometry, dict):
                    rejected.append({"conflict_id": cid, "reason": "use_new_value requires new_geometry"})
                    continue
                ok = _replace_geometry_in_ops(new_ops, wfid, new_geometry)
                if not ok:
                    rejected.append({"conflict_id": cid,
                                     "reason": "no geometry op targets this feature — restructure the proposal"})
                    continue
                applied.append({"conflict_id": cid, "action": action,
                                "effect": f"resolved geometry on {wfid} to the provided value"})
            else:
                rejected.append({"conflict_id": cid, "reason": f"invalid action for {kind}"})
        elif kind in ("delete_update_conflict",):
            if action == "keep_current":
                _remove_retire(wfid)
                # R1-P1-4：退休集合不只来自 delete op —— split/merge 父也退役
                # 该要素；keep_current 必须同时移除结构 op，否则裁决成为静默
                # 空操作（merge 仍退役父并销毁用户保留的修改）。
                structural_removed = False
                pruned: List[Dict[str, Any]] = []
                for op in new_ops:
                    if op.get("op") == "split_feature" and op.get("wfid") == wfid:
                        structural_removed = True
                        continue
                    if op.get("op") == "merge_features":
                        # merge 父集来自 plan 级目标，无法部分剥离 —— 父涉及
                        # 本冲突（conflicts 里该 wfid 的 delete_update 由 merge
                        # 足迹产生）时整 op 移除。
                        # merge 父集 = plan 级目标，无法部分剥离；该 wfid 的
                        # retire 在 delete 剥离后只能来自 merge —— 整 op 移除。
                        if any(o.get("op") == "merge_features" for o in new_ops):
                            structural_removed = True
                            continue
                    pruned.append(op)
                new_ops = pruned
                effect = f"kept current modification; dropped delete of {wfid}"
                if structural_removed:
                    effect += "; retired the structural (split/merge) op on this parent"
                applied.append({"conflict_id": cid, "action": action, "effect": effect})
            elif action == "take_proposed":
                applied.append({"conflict_id": cid, "action": action,
                                "effect": f"kept proposed delete of {wfid} (current modification will be overwritten by the explicit decision)"})
            else:
                rejected.append({"conflict_id": cid,
                                 "reason": "delete_update_conflict only supports keep_current / take_proposed"})
        elif kind == "target_deleted":
            if action == "keep_current":
                _remove_touch(wfid)
                applied.append({"conflict_id": cid, "action": action,
                                "effect": f"dropped changes to deleted feature {wfid}"})
            else:
                rejected.append({
                    "conflict_id": cid,
                    "reason": "target_deleted only supports keep_current — the feature is gone; create a fresh proposal to rebuild",
                })
        elif kind == "parent_retired":
            if action == "keep_current":
                applied.append({"conflict_id": cid, "action": action,
                                "effect": f"retire structural op on retired parent {wfid} — op removed"})
                # split 按 op.wfid 精确移除；merge 的父集来自 plan 级目标无法
                # 从 op 剥离 —— 该 merge op 一并移除（service 在 rebase 重建
                # 目标时同样排除退休父，双保险）。
                new_ops = [
                    op for op in new_ops
                    if not (
                        op.get("op") == "split_feature" and op.get("wfid") == wfid
                    )
                    and not (
                        op.get("op") == "merge_features"
                        and any(
                            str(c.get("wfid") or "") == wfid and c.get("kind") == "parent_retired"
                            for c in conflicts
                        )
                    )
                ]
            else:
                rejected.append({
                    "conflict_id": cid,
                    "reason": "parent_retired only supports keep_current — re-target or create a fresh proposal",
                })
        elif kind == "dependency_changed":
            if action == "keep_current":
                applied.append({"conflict_id": cid, "action": action,
                                "effect": "drop the dependency-consuming op (snap) — re-preview on the new reference if needed"})
                new_ops = [op for op in new_ops if not (op.get("op") == "snap_to_ref")]
            else:
                rejected.append({
                    "conflict_id": cid,
                    "reason": "dependency_changed only supports keep_current — snap must be re-previewed against the new reference",
                })
        elif kind in ("id_ambiguous", "identity_unprovable", "schema_incompatible"):
            rejected.append({
                "conflict_id": cid,
                "reason": f"{kind} is not resolvable in place — create a fresh proposal on the current revision",
            })
        else:
            rejected.append({"conflict_id": cid, "reason": f"unknown conflict kind {kind}"})

    return new_ops, applied, rejected


def _set_field_value(ops: List[Dict[str, Any]], wfid: str, field: str, value: Any) -> None:
    """把 (wfid, field) 的写值替换为 value；找不到明确目标时加一个 op 级限定 op。"""
    for op in ops:
        if op.get("op") != "set_fields":
            continue
        targets = op.get("wfids")
        if targets is None:
            continue  # plan 级目标 op 不碰（语义是全体）
        if wfid in targets and field in (op.get("fields") or {}):
            op.setdefault("fields", {})[field] = value
            return
    # 未命中：附加一个显式限定的 set_fields。
    ops.append({"op": "set_fields", "wfids": [wfid], "fields": {field: value}})


def _replace_geometry_in_ops(
    ops: List[Dict[str, Any]], wfid: str, geometry: Dict[str, Any],
) -> bool:
    """把对 wfid 的几何写替换为给定几何。plan 级 replace_geometry（无
    wfids，作用于全体目标）不可改写 → 返回 False 拒绝（R1-P2-9）。"""
    for op in ops:
        if op.get("op") == "replace_geometry":
            targets = op.get("wfids")
            if targets is None:
                continue  # plan 级 —— 不碰
            if wfid in targets:
                op["geometry"] = geometry
                return True
    ops.append({"op": "replace_geometry", "wfids": [wfid], "geometry": geometry})
    return True


def rebase_preview(
    *,
    report: Dict[str, Any],
    resolutions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """rebase 预览：裁决全部应用后还剩什么（不落盘）。"""
    unresolved = report.get("unresolved_conflicts") or []
    remaining = [c for c in unresolved if str(c.get("conflict_id")) not in {
        str(r.get("conflict_id")) for r in resolutions if isinstance(r, dict)
    }]
    return {
        "verdict": report.get("verdict"),
        "remaining_conflicts": remaining,
        "satisfied": report.get("satisfied"),
        "resolutions_needed": [c.get("conflict_id") for c in remaining],
        "note": (
            "rebase will snapshot the current revision as the new base and bump "
            "the proposal revision; prior approvals become invalid"
        ),
    }
