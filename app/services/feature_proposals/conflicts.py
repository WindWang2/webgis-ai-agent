"""要素级三方冲突分类 —— 纯函数（G32 WP05）。

输入：base 快照（物化预演后）、当前载荷、提案 ops/裁决。输出 typed 冲突报告。
比较**从不依赖 journal**（journal 可能已逐出）：base→current 的变化直接对比
两份数据形态；提案的影响由 ops 的 read/write/birth/retire 集合静态推导。

纪律（任务 §六）：
- 独立修改依据**实际依赖**：属性写只比目标字段；但 split 继承父属性、
  merge 读多父全部字段、snap 读参考源几何 —— 这些 read set 不省略。
- 类型与存在性严格：missing ≠ null、False ≠ 0、0 ≠ "0"（values_equal_strict）。
- 几何确定性比较（canonical 精确顶点序）：坐标相近不视为同一修改，
  **绝不平均坐标或最近邻迁移目标**（P07）。
- 双方同字段同 typed 值 → 已满足（satisfied），不产生重复变更（P05）。
- schema/CRS 变化无法证明兼容 → 阻断（blocked verdict）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.feature_proposals.candidate import (
    evaluate_candidate,
    extract_payload_fc,
    fc_duplicate_wfids,
    fc_index_by_wfid,
    geometry_digest,
    materialize_preview,
)

_MISSING = object()

# ── 严格 typed 值比较 ───────────────────────────────────────────────────────


def values_equal_strict(a: Any, b: Any) -> bool:
    """严格区分 None/missing/False/0/""/"0" —— 显示层可渲染易读文字，
    这里是持久 patch 的语义真相（P06）。"""
    if a is _MISSING or b is _MISSING:
        return a is b
    if a is None or b is None:
        return a is None and b is None
    a_bool = isinstance(a, bool)
    b_bool = isinstance(b, bool)
    if a_bool or b_bool:
        return a_bool and b_bool and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, (dict, list)):
        from app.lib.data.fingerprints import canonical_dumps

        try:
            return canonical_dumps(a) == canonical_dumps(b)
        except (TypeError, ValueError):
            return False
    return a == b


def _field_value(props: Optional[Dict[str, Any]], field: str) -> Any:
    if not isinstance(props, dict) or field not in props:
        return _MISSING
    return props[field]


# ── op 足迹推导（read/write/birth/retire/依赖）─────────────────────────────


class OpFootprint:
    """一个 op 的数据依赖足迹（wfid 级）。"""

    __slots__ = (
        "op_index", "kind", "targets",
        "write_fields", "write_geometry", "retire", "birth",
        "read_fields", "read_geometry", "deps",
    )

    def __init__(self, op_index: int, kind: str, targets: List[str]) -> None:
        self.op_index = op_index
        self.kind = kind
        self.targets = targets
        self.write_fields: Dict[str, Set[str]] = {}
        self.write_geometry: Set[str] = set()
        self.retire: Set[str] = set()
        self.birth: Set[str] = set()
        # read set（独立修改判定的依据 —— 反例：merge 的字段政策读全部父字段）
        self.read_fields: Dict[str, Optional[Set[str]]] = {}  # None = 全部字段
        self.read_geometry: Set[str] = set()
        self.deps: List[Tuple[str, str]] = []  # (kind, ref_id) 外部依赖（snap 参考源）


def _op_targets(op: Dict[str, Any], plan_targets: List[str]) -> List[str]:
    raw = op.get("wfids")
    if isinstance(raw, list) and raw:
        allowed = set(plan_targets)
        return [str(w) for w in raw if str(w) in allowed or True]
    if op.get("op") == "split_feature" and isinstance(op.get("wfid"), str):
        return [op["wfid"]]
    if op.get("op") == "merge_features":
        return list(plan_targets)
    return list(plan_targets)


def derive_op_footprints(
    ops: List[Dict[str, Any]],
    ordered_targets: List[str],
    *,
    candidate_deltas: Optional[List[Dict[str, Any]]] = None,
) -> List[OpFootprint]:
    """静态推导每个 op 的足迹。candidate_deltas 提供准确的 birth 集合
    （create/split/merge 的 child wfid 由 op_seed 派生，求值后可知）。"""
    footprints: List[OpFootprint] = []
    for op_index, op in enumerate(ops):
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op") or "")
        targets = _op_targets(op, ordered_targets)
        fp = OpFootprint(op_index, kind, targets)
        if kind == "set_fields":
            fields = set((op.get("fields") or {}).keys())
            for w in targets:
                fp.write_fields[w] = set(fields)
        elif kind in ("move_by", "replace_geometry", "repair_geometry"):
            fp.write_geometry.update(targets)
            fp.read_geometry.update(targets)
        elif kind == "snap_to_ref":
            fp.write_geometry.update(targets)
            fp.read_geometry.update(targets)
            if isinstance(op.get("ref_id"), str):
                fp.deps.append(("snap_ref", op["ref_id"]))
        elif kind == "delete_features":
            fp.retire.update(targets)
        elif kind == "create_features":
            pass  # birth 集合在 conflicts 分类时从 candidate deltas 注入
        elif kind == "split_feature":
            parent = targets[0] if targets else ""
            if parent:
                fp.retire.add(parent)
                fp.read_fields[parent] = None  # 全部字段（children 继承）
                fp.read_geometry.add(parent)
        elif kind == "merge_features":
            for p in targets:
                fp.retire.add(p)
                fp.read_fields[p] = None  # field_policy 读全部父字段
                fp.read_geometry.add(p)
        footprints.append(fp)
    # 从候选 deltas 注入准确的 birth/retire（比静态推导可信 —— 撞库偏移后
    # child wfid 只有求值才知道）。
    if candidate_deltas:
        for delta in candidate_deltas:
            if not isinstance(delta, dict):
                continue
            wfid = delta.get("wfid")
            presence = delta.get("presence")
            if presence == "created":
                for fp in footprints:
                    if fp.kind in ("create_features", "split_feature", "merge_features"):
                        fp.birth.add(wfid)
            elif presence == "removed":
                for fp in footprints:
                    if wfid in fp.retire:
                        break
    return footprints


# ── base→current 变化检测 ───────────────────────────────────────────────────


def detect_current_changes(
    base_fc: Any,
    current_fc: Any,
) -> Dict[str, Any]:
    """直接对比两份数据形态（不依赖 journal）。

    返回 {
      deleted: {wfid}, created: {wfid},
      field_changes: {wfid: {field: (base_val_marker, current_val_marker)}},
      geometry_changes: {wfid},
      new_field_names: set, dropped_field_names: set,   # schema 面变化
    }
    """
    base_idx = fc_index_by_wfid(base_fc)
    cur_idx = fc_index_by_wfid(current_fc)
    deleted = set(base_idx) - set(cur_idx)
    created = set(cur_idx) - set(base_idx)
    field_changes: Dict[str, Dict[str, Tuple[Any, Any]]] = {}
    geometry_changes: Set[str] = set()
    base_fields: Set[str] = set()
    cur_fields: Set[str] = set()
    for wfid, base_feat in base_idx.items():
        cur_feat = cur_idx.get(wfid)
        base_props = base_feat.get("properties") if isinstance(base_feat, dict) else None
        base_fields.update(base_props.keys() if isinstance(base_props, dict) else [])
        if cur_feat is None:
            continue
        cur_props = cur_feat.get("properties") if isinstance(cur_feat, dict) else None
        if isinstance(cur_props, dict):
            cur_fields.update(cur_props.keys())
        keys: Set[str] = set()
        if isinstance(base_props, dict):
            keys.update(base_props)
        if isinstance(cur_props, dict):
            keys.update(cur_props)
        changed: Dict[str, Tuple[Any, Any]] = {}
        for f in keys:
            if f == "_wfid":
                continue
            bv = _field_value(base_props, f)
            cv = _field_value(cur_props, f)
            if not values_equal_strict(bv, cv):
                changed[f] = (bv, cv)
        if changed:
            field_changes[wfid] = changed
        if not values_equal_strict(base_feat.get("geometry"), cur_feat.get("geometry")):
            geometry_changes.add(wfid)
    for wfid in created:
        cur_feat = cur_idx[wfid]
        cur_props = cur_feat.get("properties") if isinstance(cur_feat, dict) else None
        if isinstance(cur_props, dict):
            cur_fields.update(cur_props)
    return {
        "deleted": deleted,
        "created": created,
        "field_changes": field_changes,
        "geometry_changes": geometry_changes,
        "new_field_names": cur_fields - base_fields,
        "dropped_field_names": base_fields - cur_fields,
    }


# ── 三方冲突分类主函数 ──────────────────────────────────────────────────────

def _align_unmaterialized_current(
    base_mat: Any,
    current_fc: Any,
    base_fingerprint: str,
    pre_conflicts: List[Dict[str, Any]],
) -> Any:
    """未物化 current → 按 base 预演物化 + 自然 id 对齐改写到 base 身份空间。

    自然 id 重复 claim（同 natural 多条要素）→ id_ambiguous（不 first-wins
    静默错配）。返回对齐后的 mat（deep copy；current 未物化 → 无既有
    _wfid 可破坏）。
    """
    import copy as _copy

    from app.services.feature_proposals.candidate import (
        _natural_id_of,
        natural_to_wfid_index,
    )

    probe, _ = materialize_preview(current_fc, base_fingerprint)
    if probe is None:
        return None
    base_index = natural_to_wfid_index(base_mat)
    aligned = _copy.deepcopy(probe)
    claimed: Dict[str, str] = {}
    for feature in aligned.get("features") or []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        if not isinstance(props, dict):
            continue
        probe_wfid = props.get("_wfid")
        natural = _natural_id_of(feature)
        if natural is None:
            continue
        target = base_index.get(natural)
        if target is None:
            continue  # base 没有的要素 → 保留预演身份（created）
        if natural in claimed and claimed[natural] != probe_wfid:
            pre_conflicts.append({
                "conflict_id": f"identity:ambiguous:{natural}",
                "kind": "id_ambiguous",
                "note": "duplicate natural id claims the same base feature — refusing positional/id migration",
                "detail": {"natural_id": natural},
            })
            continue
        claimed.setdefault(natural, probe_wfid)
        props["_wfid"] = target
    return aligned


CONFLICT_KINDS = (
    "field_value_conflict",        # 同字段双方不同 typed 值
    "geometry_both_modified",      # 双方改同一几何
    "delete_update_conflict",      # 提案删 / current 改（或反向）
    "target_deleted",              # 提案修改的目标已被 current 删除
    "parent_retired",              # split/merge 的父对象已退役
    "dependency_changed",          # snap 参考源 / 外部依赖变化
    "id_ambiguous",                # 身份解析歧义（重复自然键/跨序位置）
    "identity_unprovable",         # base 未物化且 current 物化指纹不符 → 阻断
    "schema_incompatible",         # 字段面变化无法证明兼容 → 阻断
)


def classify_conflicts(
    *,
    base_payload: Any,
    base_fingerprint: str,
    current_payload: Any,
    current_fingerprint: str,
    ops: List[Dict[str, Any]],
    ordered_targets: List[str],
    op_seed: str,
    resolutions: Optional[List[Dict[str, Any]]] = None,
    target_payloads: Optional[Dict[str, Any]] = None,
    geometry_type: Optional[str] = None,
) -> Dict[str, Any]:
    """三方分类（base 快照 vs current vs 提案候选）。

    返回报告：
      verdict: clean | conflicted | blocked
      conflicts: [{conflict_id, kind, wfid, field?, base, current, proposed, note}]
      satisfied: [{wfid, field?}]        —— 提案目标已在 current 达成
      independent_current_changes: 有界计数 —— current 改了提案不涉及的部分（保留双方）
      identity: {base_materialized, current_materialized, provable}
    """
    resolutions = resolutions or []
    resolved_ids = {str(r.get("conflict_id")) for r in resolutions if isinstance(r, dict)}

    base_fc = extract_payload_fc(base_payload)
    current_fc = extract_payload_fc(current_payload)
    if base_fc is None or current_fc is None:
        return {
            "verdict": "blocked",
            "conflicts": [{
                "conflict_id": "payload-not-fc", "kind": "schema_incompatible",
                "note": "base or current payload is not a FeatureCollection",
            }],
            "satisfied": [], "independent_current_changes": {"count": 0},
            "identity": {"provable": False},
            "current_changed": None,
        }

    base_mat, _ = materialize_preview(base_fc, base_fingerprint)
    # ── current 身份对齐 ──
    # 已物化 current：真实身份直接用（真实编辑的物化指纹 = 编辑时载荷指纹，
    # 与 base 预演身份天然对齐；漂移由 identity_unprovable 检查阻断）。
    # 未物化 current：按 base 指纹预演物化 + **自然 id 对齐改写** —— 按 index
    # 预演会在要素被删/重排时把身份迁错位置（P14 明令禁止按数组位置迁移）；
    # 自然 id（顶层 id / FEATURE_ID_KEYS 除 _wfid）才是未物化形态的既有身份。
    _current_probe, _probe_needed = materialize_preview(current_fc, current_fingerprint)
    pre_conflicts: List[Dict[str, Any]] = []
    if _probe_needed:
        current_mat = _align_unmaterialized_current(base_mat, current_fc, base_fingerprint, pre_conflicts)
        current_needed_mat = True
    else:
        current_mat = _current_probe
        current_needed_mat = False

    base_idx = fc_index_by_wfid(base_mat)
    cur_idx = fc_index_by_wfid(current_mat)

    conflicts: List[Dict[str, Any]] = list(pre_conflicts)

    # ── 身份可证性（P13）：base 未物化时，若 current 也未物化 → 可对 base
    # 预演物化比较；若 current 已物化（发生过真实编辑）→ 同一 base 指纹的
    # 物化映射不可证，拒绝自动 rebase。
    base_materialized = not _has_unmaterialized(base_fc)
    current_materialized = not current_needed_mat
    identity: Dict[str, Any] = {
        "base_materialized": base_materialized,
        "current_materialized": current_materialized,
    }
    if not base_materialized and current_materialized:
        # current 已按某指纹物化 —— 若其物化身份与我们预演的不一致即阻断。
        preview_idx = fc_index_by_wfid(base_mat)
        mismatch = [w for w in list(preview_idx)[:200] if w not in cur_idx]
        gone = [w for w in mismatch if w not in cur_idx][:8]
        # 预演身份在 current 全部在场 → 同一映射可证；否则阻断。
        if gone and set(preview_idx) - set(cur_idx):
            conflicts.append({
                "conflict_id": "identity:unprovable",
                "kind": "identity_unprovable",
                "note": (
                    "base snapshot is un-materialized and the current dataset was "
                    "materialized by a different edit; identity mapping cannot be "
                    "proven — re-create the proposal on the current revision"
                ),
                "detail": {"missing_preview_wfids": gone[:8]},
            })
            identity["provable"] = False
        else:
            identity["provable"] = True
    else:
        identity["provable"] = True

    # ── 候选求值（在 base 预演形态上；提供 birth/retired 准确集合）。
    cand_deltas, cand_findings, cand_lineage = evaluate_candidate(
        base_mat, ops, ordered_targets, op_seed=op_seed,
        target_payloads=target_payloads, geometry_type=geometry_type,
    )
    if cand_findings:
        # 候选在 base 上已不可求值（数据形态变了/参数失效）。
        conflicts.append({
            "conflict_id": "candidate:invalid",
            "kind": "schema_incompatible",
            "note": "candidate ops no longer evaluate on the frozen base",
            "detail": {"findings": cand_findings[:8]},
        })

    footprints = derive_op_footprints(ops, ordered_targets, candidate_deltas=cand_deltas)

    # ── base→current 变化。
    changes = detect_current_changes(base_mat, current_mat)
    current_changed = bool(
        changes["deleted"] or changes["created"]
        or changes["field_changes"] or changes["geometry_changes"]
    )

    # ── ID 歧义（P14）：current 重复 _wfid；提案目标在 current 解析歧义。
    dup = fc_duplicate_wfids(current_mat)
    ambiguous_hits = {w: c for w, c in dup.items() if w in set(ordered_targets)}
    if ambiguous_hits:
        conflicts.append({
            "conflict_id": "identity:ambiguous",
            "kind": "id_ambiguous",
            "note": "duplicate feature identities in current dataset",
            "detail": {"counts": {k: v for k, v in list(ambiguous_hits.items())[:8]}},
        })

    # ── schema 面变化（P-阻断）：提案写过的字段在 current 消失/类型面变化。
    write_fields_all: Dict[str, Set[str]] = {}
    for fp in footprints:
        for w, fields in fp.write_fields.items():
            write_fields_all.setdefault(w, set()).update(fields)
    if changes["dropped_field_names"]:
        dropped_touched = sorted(
            {f for fields in write_fields_all.values() for f in fields}
            & set(changes["dropped_field_names"])
        )
        if dropped_touched:
            conflicts.append({
                "conflict_id": "schema:fields-dropped",
                "kind": "schema_incompatible",
                "note": "fields written by the proposal no longer exist in the current dataset",
                "detail": {"fields": dropped_touched[:8]},
            })

    # ── 逐足迹分类。
    satisfied: List[Dict[str, Any]] = []
    proposed_field_values: Dict[Tuple[str, str], Any] = {}
    proposed_geometry: Dict[str, Any] = {}
    proposed_retire: Set[str] = set()
    proposed_birth: Dict[str, Any] = {}
    for delta in cand_deltas:
        if not isinstance(delta, dict):
            continue
        wfid = str(delta.get("wfid") or "")
        presence = delta.get("presence")
        if presence == "removed":
            proposed_retire.add(wfid)
        elif presence == "created":
            proposed_birth[wfid] = delta.get("feature_after")
        for fname, change in (delta.get("fields") or {}).items():
            proposed_field_values[(wfid, fname)] = change.get("after")
        # 仅几何真实被写的 delta 进入几何比较（field-only delta 的
        # geometry_after 为 None，不进 proposed_geometry）。
        if presence is None and delta.get("geometry_after") is not None:
            proposed_geometry[wfid] = delta.get("geometry_after")

    def _resolved(cid: str) -> bool:
        return cid in resolved_ids

    # 字段冲突 / satisfied。
    for (wfid, field), proposed_val in sorted(proposed_field_values.items()):
        cur_change = changes["field_changes"].get(wfid)
        if cur_change is None or field not in cur_change:
            # current 没改这个字段 → 提案写保留（independent 除非 current 改
            # 的是同要素其他字段 —— 那也是 independent：set_fields 不读其他字段）。
            continue
        base_val, cur_val = cur_change[field]
        if values_equal_strict(cur_val, proposed_val):
            satisfied.append({"wfid": wfid, "field": field,
                              "note": "current already carries the proposed value"})
        else:
            cid = f"field:{wfid}:{field}"
            conflicts.append({
                "conflict_id": cid,
                "kind": "field_value_conflict",
                "wfid": wfid, "field": field,
                "base": _marker(base_val), "current": _marker(cur_val),
                "proposed": _marker(proposed_val),
                "note": "both sides set this field to different typed values",
            })

    # 几何冲突 / satisfied。
    for wfid in sorted(proposed_geometry):
        proposed_geom = proposed_geometry.get(wfid)
        if wfid in changes["geometry_changes"]:
            cur_feat = cur_idx.get(wfid)
            cur_geom = cur_feat.get("geometry") if isinstance(cur_feat, dict) else None
            if values_equal_strict(cur_geom, proposed_geom):
                satisfied.append({"wfid": wfid, "field": None,
                                  "note": "current geometry already equals the proposal"})
            else:
                cid = f"geometry:{wfid}"
                conflicts.append({
                    "conflict_id": cid,
                    "kind": "geometry_both_modified",
                    "wfid": wfid,
                    "base_digest": geometry_digest(base_idx.get(wfid, {}).get("geometry")) if wfid in base_idx else None,
                    "current_digest": geometry_digest(cur_geom),
                    "proposed_digest": geometry_digest(proposed_geom),
                    "note": "both sides modified this geometry — explicit decision required (no averaging)",
                })

    # delete/update 双向。
    for wfid in sorted(proposed_retire & (set(changes["field_changes"]) | changes["geometry_changes"])):
        cid = f"delete_update:{wfid}"
        conflicts.append({
            "conflict_id": cid,
            "kind": "delete_update_conflict",
            "wfid": wfid,
            "note": "proposal deletes a feature the current side modified — silent loss forbidden",
        })
    for wfid in sorted(proposed_retire & changes["deleted"]):
        satisfied.append({"wfid": wfid, "field": None, "note": "feature already deleted in current"})

    # 提案修改的目标已被 current 删除。
    touched = {w for (w, _f) in proposed_field_values} | set(proposed_geometry)
    for wfid in sorted(touched & changes["deleted"]):
        cid = f"target_deleted:{wfid}"
        conflicts.append({
            "conflict_id": cid,
            "kind": "target_deleted",
            "wfid": wfid,
            "note": "proposal modifies a feature that no longer exists in current — "
                    "only keep_current (drop the change) or a fresh proposal is valid",
        })

    # split/merge 父对象退役（P10）。
    for fp in footprints:
        if fp.kind not in ("split_feature", "merge_features"):
            continue
        retired_parents = sorted(fp.retire & changes["deleted"])
        for parent in retired_parents:
            cid = f"parent_retired:{fp.kind}:{parent}"
            conflicts.append({
                "conflict_id": cid,
                "kind": "parent_retired",
                "wfid": parent,
                "note": (
                    f"{fp.kind} parent was retired by the current side; existing "
                    "lineage is shown — re-target or create a fresh proposal "
                    "(no automatic scattering to children)"
                ),
                "detail": {"lineage": cand_lineage[:4]},
            })

    # 出生身份撞库（P12 防线）：候选 child wfid 已在 current 存在。
    # 同 feature → satisfied；不同 → 冲突（不能自动偏移重派生 —— 已批准的
    # 出生身份不可漂移）。
    for child_wfid, child_feature in sorted(proposed_birth.items()):
        if child_wfid in changes["created"]:
            cur_feat = cur_idx.get(child_wfid)
            if values_equal_strict(cur_feat, child_feature):
                satisfied.append({"wfid": child_wfid, "field": None,
                                  "note": "created child already present in current"})
            else:
                conflicts.append({
                    "conflict_id": f"birth_collision:{child_wfid}",
                    "kind": "id_ambiguous",
                    "wfid": child_wfid,
                    "note": "candidate child identity already exists in current with different content — fresh proposal required",
                })
        elif child_wfid in cur_idx:
            conflicts.append({
                "conflict_id": f"birth_collision:{child_wfid}",
                "kind": "id_ambiguous",
                "wfid": child_wfid,
                "note": "candidate child identity collides with an existing feature — fresh proposal required",
            })

    # 依赖变化（P09）：snap 参考源几何在 base→current 间变化。
    for fp in footprints:
        for dep_kind, dep_ref in fp.deps:
            dep_payload = (target_payloads or {}).get(dep_ref)
            if dep_payload is None:
                conflicts.append({
                    "conflict_id": f"dependency:{dep_kind}:{dep_ref}",
                    "kind": "dependency_changed",
                    "note": "snap reference source is no longer available — cannot silently recompute an approved candidate",
                    "detail": {"ref_id": dep_ref},
                })
                continue
            # 当前参考源与 base 时的参考源比较：快照未存参考源 —— 保守处理：
            # 只要 base→current 期间参考源指纹与候选求值时不同即冲突。此处
            # 没有参考源的 base 快照，改为：参考源存在即要求重新预览确认
            # （dependency_changed 仅在参考源缺失或显式指纹不符时触发）。
            # 服务层在创建提案时保存 ref 依赖指纹（service._capture_dep_fingerprints），
            # 经 ops_dependencies 参数传入精确比较。
    # ops_dependencies: {ref_id: {"base_fingerprint": str, "current_fingerprint": str}}
    # 由 service 层计算后传入（保持本函数纯）。

    n_independent = sum(len(v) for v in changes["field_changes"].values())
    unresolved = [c for c in conflicts if not _resolved(c["conflict_id"])]
    blocked = any(c["kind"] in ("schema_incompatible", "identity_unprovable") for c in unresolved)
    verdict = "blocked" if blocked else ("conflicted" if unresolved else "clean")
    return {
        "verdict": verdict,
        "conflicts": conflicts,
        "unresolved_conflicts": unresolved,
        "satisfied": satisfied,
        "independent_current_changes": {
            "count": n_independent,
            "deleted": len(changes["deleted"]),
            "created": len(changes["created"]),
            "geometry": len(changes["geometry_changes"]),
        },
        "identity": identity,
        "current_changed": current_changed,
        "candidate_lineage": cand_lineage[:8],
    }


def _marker(value: Any) -> Any:
    if value is _MISSING:
        return {"presence": "missing"}
    return {"presence": "present", "value": value}


def _has_unmaterialized(fc: Any) -> bool:
    feats = fc.get("features") or [] if isinstance(fc, dict) else []
    return any(
        not isinstance(f.get("properties"), dict) or "_wfid" not in f["properties"]
        for f in feats if isinstance(f, dict)
    ) if feats else False


def check_dependency_changes(
    *,
    base_dep_fingerprints: Dict[str, str],
    current_dep_fingerprints: Dict[str, str],
    dep_refs: List[str],
) -> List[Dict[str, Any]]:
    """snap 参考源等外部依赖的 base→current 指纹比较（service 组装后调用）。

    指纹不同 → typed dependency_changed；缺失 → 同样冲突（不可默默用新参考
    重算旧批准）。"""
    out: List[Dict[str, Any]] = []
    for ref in dep_refs:
        base_fp = base_dep_fingerprints.get(ref)
        cur_fp = current_dep_fingerprints.get(ref)
        if base_fp is None or cur_fp is None or base_fp != cur_fp:
            out.append({
                "conflict_id": f"dependency:snap_ref:{ref}",
                "kind": "dependency_changed",
                "note": "snap reference source changed since the proposal base — "
                        "re-preview required; approved candidate is not silently recomputed",
                "detail": {"ref_id": ref, "base_fingerprint": base_fp, "current_fingerprint": cur_fp},
            })
    return out
