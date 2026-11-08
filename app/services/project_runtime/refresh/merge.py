"""G10 UPD-04：baseline / 当前人工编辑态 / 更新候选态的 user-wins 三方合并。

红线：
- 保持三个输入；引用**稳定 entity ID**（layers[].id / components[].id），
  禁止数组下标；候选打乱顺序也不会把颜色贴到错的层。
- 只重放「用户确实改过」的表现层字段（current != baseline）；用户没改的
  字段采用候选值 —— 这才是标准三方合并，不是「一律冻结旧样式」。
- 保留颜色/透明度/可见性、图层顺序、组件位置/族锁、文本修改、表达方式；
  禁止以「更新数据」为由全部重置。
- 被删要素的标注、字段被移除的标签/图表、用户隐藏的已失效层 → 显式冲突，
  绝不凭空迁移到另一对象。
- 硬数据语义错误（单位/CRS/科学结论）不因 user-wins 变成合法结论：
  ``adoptable=False``。

采用既有 MapSpec 形态（app/lib/cartography/mapspec_schema.py）与既有锁读取
（app/contracts/workbench_locks.py）；本模块只产出候选 MapSpec，落盘必须走
权威 ``apply_gis_mutation`` / ``commit_map_operation``（见 execution.py）。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional

from app.contracts.workbench_locks import (
    locked_component_ids_of,
    locked_layer_ids_of,
)

from .contracts import MergeConflict, MergeResult

#: 表现层字段（人工调整必须赢），与 MapSpecLayer 的 extra=allow 兼容。
PRESENTATION_LAYER_KEYS = (
    "paint", "layout", "visible", "visibility", "legend_spec", "label",
    "representation", "scale_profile", "bivariate", "extrusion",
)
#: 顶层表现面（数据语义不在其中）。
TOP_LEVEL_PRESENTATION_KEYS = ("title", "legend")
#: 组件表现面。
COMPONENT_KEYS = ("text", "position", "style", "visible", "familyLock", "locked")


def _by_id(items: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(items, list):
        return out
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            out[item["id"]] = item
    return out


def _order(doc: Mapping[str, Any]) -> List[str]:
    return [str(x.get("id")) for x in (doc.get("layers") or [])
            if isinstance(x, dict) and x.get("id")]


def _merge_mapping_three_way(
    *, key: str, baseline: Any, current: Any, candidate: Any,
    preserved: List[str], entity: str,
) -> Any:
    """单个表现字段的三方合并：用户改过 → current 赢；否则 candidate 赢。"""
    if isinstance(current, (dict, list)) and isinstance(baseline, (dict, list)):
        user_edited = current != baseline
    else:
        user_edited = current != baseline
    if user_edited and current is not None:
        preserved.append(f"{entity}:{key}")
        return copy.deepcopy(current)
    return copy.deepcopy(candidate) if candidate is not None else current


def three_way_merge(
    *,
    baseline: Dict[str, Any],
    current: Dict[str, Any],
    candidate: Dict[str, Any],
    removed_fields: Optional[Mapping[str, List[str]]] = None,
    annotation_targets: Optional[Mapping[str, str]] = None,
    hard_semantic_errors: Optional[List[str]] = None,
) -> MergeResult:
    """产出合并后的候选 MapSpec + 保留项 + 显式冲突 + 可采纳性裁决。"""
    if not all(isinstance(x, dict) for x in (baseline, current, candidate)):
        raise ValueError("three_way_merge requires mapping-shaped MapSpec docs")

    merged: Dict[str, Any] = copy.deepcopy(candidate)
    preserved: List[str] = []
    conflicts: List[MergeConflict] = []
    errors = [str(e)[:200] for e in (hard_semantic_errors or [])]

    # 注意：n_layers / n_comp 必须取自 merged（深拷贝），否则原地修改落在
    # candidate 上而丢失（R1 实测：组件文本未保留）。
    b_layers, c_layers, n_layers = (
        _by_id(baseline.get("layers")), _by_id(current.get("layers")),
        _by_id(merged.get("layers")),
    )

    # ── 1. 逐层：按稳定 id 对齐，用户改过的表现层字段胜出 ──────────
    for lid, nlayer in n_layers.items():
        if lid not in c_layers or lid not in b_layers:
            continue  # 新增层 / 基线缺失：直接用候选
        for key in PRESENTATION_LAYER_KEYS:
            if key not in c_layers[lid] and key not in b_layers[lid]:
                continue
            merged_value = _merge_mapping_three_way(
                key=key,
                baseline=b_layers[lid].get(key),
                current=c_layers[lid].get(key),
                candidate=nlayer.get(key),
                preserved=preserved, entity=f"layer:{lid}",
            )
            if merged_value is None:
                nlayer.pop(key, None)
            else:
                nlayer[key] = merged_value

    # ── 2. 层序：用户改过顺序 → 保留；新层按候选顺序追加 ────────────
    b_order, c_order, n_order = _order(baseline), _order(current), _order(candidate)
    if c_order and c_order != b_order:
        preserved.append("layers:order")
        final_order = [lid for lid in c_order if lid in n_layers]
        final_order += [lid for lid in n_order if lid not in final_order]
    else:
        final_order = n_order
    merged["layers"] = [n_layers[lid] for lid in final_order if lid in n_layers]

    # ── 3. 顶层表现面 ───────────────────────────────────────────────
    for key in TOP_LEVEL_PRESENTATION_KEYS:
        bval, cval, nval = baseline.get(key), current.get(key), candidate.get(key)
        if isinstance(cval, dict) and isinstance(bval, dict):
            winner = copy.deepcopy(nval if isinstance(nval, dict) else {})
            for sub, subval in cval.items():
                if subval != bval.get(sub):
                    winner[sub] = copy.deepcopy(subval)
                    preserved.append(f"{key}:{sub}")
            if winner:
                merged[key] = winner
        else:
            merged[key] = _merge_mapping_three_way(
                key=key, baseline=bval, current=cval, candidate=nval,
                preserved=preserved, entity="mapspec",
            )

    # ── 4. 组件：按 id 对齐（文本 / 位置 / 族锁） ────────────────────
    b_comp, c_comp, n_comp = (
        _by_id(baseline.get("components")), _by_id(current.get("components")),
        _by_id(merged.get("components")),
    )
    for cid, ncomp in n_comp.items():
        if cid not in c_comp or cid not in b_comp:
            continue
        for key in COMPONENT_KEYS:
            if key not in c_comp[cid] and key not in b_comp[cid]:
                continue
            value = _merge_mapping_three_way(
                key=key, baseline=b_comp[cid].get(key),
                current=c_comp[cid].get(key), candidate=ncomp.get(key),
                preserved=preserved, entity=f"component:{cid}",
            )
            if value is None:
                ncomp.pop(key, None)
            else:
                ncomp[key] = value
    if isinstance(merged.get("components"), list):
        c_ids = [str(x.get("id")) for x in (current.get("components") or [])
                 if isinstance(x, dict) and x.get("id")]
        n_ids = [str(x.get("id")) for x in merged["components"]
                 if isinstance(x, dict) and x.get("id")]
        if c_ids and c_ids != [str(x.get("id")) for x in (baseline.get("components") or [])
                               if isinstance(x, dict) and x.get("id")]:
            preserved.append("components:order")
            order = [i for i in c_ids if i in n_comp] + [i for i in n_ids if i not in c_ids]
            merged["components"] = [n_comp[i] for i in order if i in n_comp]

    # ── 5. 族锁：用户锁集合是权威，不被候选重置 ──────────────────────
    cur_layer_locks = locked_layer_ids_of(current)
    cur_comp_locks = locked_component_ids_of(current)
    wb = merged.get("workbench")
    if not isinstance(wb, dict):
        wb = {}
    if cur_layer_locks != locked_layer_ids_of(baseline):
        preserved.append("workbench:lockedLayerIds")
    if cur_comp_locks != locked_component_ids_of(baseline):
        preserved.append("workbench:lockedComponentIds")
    wb["lockedLayerIds"] = list(cur_layer_locks)
    wb["lockedComponentIds"] = list(cur_comp_locks)
    merged["workbench"] = wb

    # ── 6. 显式冲突（不静默迁移到另一对象） ─────────────────────────
    for lid in c_layers:
        if lid in n_layers:
            continue
        target_field = (annotation_targets or {}).get(lid)
        if target_field:
            conflicts.append(MergeConflict(
                kind="deleted_annotation_target", entity_id=lid,
                detail=f"layer removed by candidate but user annotation targets field '{target_field}'",
            ))
        if c_layers[lid].get("visible") is False:
            conflicts.append(MergeConflict(
                kind="hidden_invalid_layer", entity_id=lid,
                detail="user-hidden layer became invalid/removed in candidate",
            ))
        if not target_field and c_layers[lid].get("visible") is not False:
            conflicts.append(MergeConflict(
                kind="deleted_annotation_target", entity_id=lid,
                detail="layer removed by candidate; no automatic rebinding performed",
            ))
    for lid, fields in (removed_fields or {}).items():
        for field in fields:
            conflicts.append(MergeConflict(
                kind="removed_field", entity_id=lid,
                detail=f"field '{field}' removed by candidate; labels/charts referencing it are unresolved",
            ))
    # 用户锁定的实体若被候选删除，必须升级为锁冲突
    for lid in cur_layer_locks:
        if lid not in n_layers and not any(c.entity_id == lid for c in conflicts):
            conflicts.append(MergeConflict(
                kind="lock_violation", entity_id=lid,
                detail="user-locked layer missing from candidate",
            ))

    return MergeResult(
        merged_mapspec=merged,
        preserved=sorted(set(preserved)),
        conflicts=conflicts[:64],
        hard_semantic_errors=errors,
        user_wins=True,
        adoptable=not errors and not conflicts,
    )


__all__ = ["three_way_merge", "PRESENTATION_LAYER_KEYS"]
