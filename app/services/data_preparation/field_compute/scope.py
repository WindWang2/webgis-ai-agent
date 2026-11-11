"""作用范围解析（G19 / WP06）—— all_rows / 可靠 selection / 受限 predicate。

硬纪律：**绝不把前端当前页、绘制命中样本或截断 ID 集当全数据选中**。
selection 必须同时具备：完整标志、selection_revision、稳定唯一 ID 字段、
未被截断的 ID 列表；任一不满足 → ``SELECTION_UNRELIABLE`` 并给恢复路径
（改用 all_rows 或可靠 predicate）。范围计算默认保留全体行，只对选中行
生成值；``subset_export`` 是另一项显式操作，不隐式丢行。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple

from app.services.data_preparation.field_compute.contracts import (
    CompileError, ScopeSpec,
)

SELECTION_RECOVERY_HINT = (
    "选择集不可靠：请重新选择（完整 ID 集 + revision），或改用 all_rows / "
    "显式 predicate。前端当前页、命中样本与截断 ID 集一律不算全量选中。")


def _strict_scalar(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    raise CompileError("INVALID_PREDICATE", "谓词值必须是标量")


def predicate_match(props: Dict[str, Any], pred: Any) -> bool:
    """closed 谓词求值（严格类型；不隐式把 bool/空串当数值）。"""
    field, op = pred.field, pred.op
    raw = props.get(field)
    if op == "is_null":
        return raw is None
    if op == "not_null":
        return raw is not None
    if raw is None:
        return False
    if op == "in":
        candidates = [_strict_scalar(v) for v in pred.values]
        return any(_strict_eq(raw, c) for c in candidates)
    target = _strict_scalar(pred.value)
    if isinstance(raw, bool) or isinstance(target, bool):
        if not (isinstance(raw, bool) and isinstance(target, bool)):
            return False
        a, b2 = raw, target
    elif isinstance(raw, (int, float)) and isinstance(target, (int, float)):
        a, b2 = float(raw), float(target)
        if not (math.isfinite(a) and math.isfinite(b2)):
            return False
    elif isinstance(raw, str) and isinstance(target, str):
        a, b2 = raw, target
    else:
        return False
    if op == "eq":
        return a == b2
    if op == "ne":
        return a != b2
    if op == "lt":
        return a < b2
    if op == "le":
        return a <= b2
    if op == "gt":
        return a > b2
    if op == "ge":
        return a >= b2
    raise CompileError("INVALID_PREDICATE", f"unknown predicate op {op!r}")


def _strict_eq(a: Any, b: Any) -> bool:
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, bool):
        return a is b
    return a == b


def validate_selection(
    scope: ScopeSpec,
    selection_payload: Any,
    features: Sequence[Dict[str, Any]],
    available_fields: Sequence[str],
) -> Tuple[List[bool], Dict[str, Any]]:
    """selection payload + 行 → (mask, summary)。不可靠即拒（计算前）。"""
    if not scope.selection_complete:
        raise CompileError("SELECTION_UNRELIABLE", "selection_complete=false",
                           hint=SELECTION_RECOVERY_HINT)
    if not scope.selection_revision:
        raise CompileError("SELECTION_UNRELIABLE", "缺少 selection_revision",
                           hint=SELECTION_RECOVERY_HINT)
    if not scope.id_field or scope.id_field not in set(available_fields):
        raise CompileError("SELECTION_UNRELIABLE",
                           "缺少可靠的稳定 ID 字段（不可用数组下标伪造）",
                           hint=SELECTION_RECOVERY_HINT)
    if scope.selection_total <= 0 or scope.selection_total > len(features):
        raise CompileError("SELECTION_UNRELIABLE",
                           f"selection_total={scope.selection_total} 与行数 {len(features)} 不符",
                           hint=SELECTION_RECOVERY_HINT)
    if isinstance(selection_payload, dict):
        if selection_payload.get("truncated") or selection_payload.get("sample"):
            raise CompileError("SELECTION_UNRELIABLE", "选择集被标记为截断/样本",
                               hint=SELECTION_RECOVERY_HINT)
        if selection_payload.get("revision") not in (None, "", scope.selection_revision):
            raise CompileError("SELECTION_STALE",
                               "selection payload revision 与计划锚不一致",
                               hint="重新选择并刷新预览")
        ids = selection_payload.get("ids")
    else:
        ids = selection_payload
    if not isinstance(ids, (list, tuple)):
        raise CompileError("SELECTION_UNRELIABLE", "选择集缺少 ids 列表",
                           hint=SELECTION_RECOVERY_HINT)
    if len(ids) != scope.selection_total:
        raise CompileError("SELECTION_UNRELIABLE",
                           f"ids 长度 {len(ids)} ≠ selection_total {scope.selection_total}",
                           hint=SELECTION_RECOVERY_HINT)
    # 稳定 ID 资格：全表该字段必须唯一且非 null（否则精确选中更新不可信）
    seen: set[str] = set()
    for feat in features:
        props = feat.get("properties") if isinstance(feat, dict) else None
        raw = props.get(scope.id_field) if isinstance(props, dict) else None
        if raw is None or isinstance(raw, (dict, list)):
            raise CompileError("SELECTION_UNRELIABLE",
                               f"ID 字段 {scope.id_field!r} 存在空值/非标量",
                               hint=SELECTION_RECOVERY_HINT)
        key = f"{type(raw).__name__}:{raw}"
        if key in seen:
            raise CompileError("SELECTION_UNRELIABLE",
                               f"ID 字段 {scope.id_field!r} 不唯一（不可用行序伪造身份）",
                               hint=SELECTION_RECOVERY_HINT)
        seen.add(key)
    id_set = {f"{type(x).__name__}:{x}" for x in ids}
    mask = []
    for feat in features:
        props = feat.get("properties") if isinstance(feat, dict) else None
        raw = props.get(scope.id_field) if isinstance(props, dict) else None
        mask.append(f"{type(raw).__name__}:{raw}" in id_set)
    actual = sum(mask)
    if actual != scope.selection_total:
        raise CompileError("SELECTION_UNRELIABLE",
                           f"实际命中 {actual} 行 ≠ 声明 {scope.selection_total} 行",
                           hint=SELECTION_RECOVERY_HINT)
    return mask, {
        "mode": "selection", "id_field": scope.id_field,
        "selection_revision": scope.selection_revision,
        "selection_total": scope.selection_total,
        "matched_rows": actual, "complete": True,
        "unselected_policy": scope.unselected_policy,
        "subset_export": scope.subset_export,
    }


def resolve_scope(
    scope: ScopeSpec,
    features: Sequence[Dict[str, Any]],
    available_fields: Sequence[str],
    selection_payload: Any = None,
) -> Tuple[List[bool], Dict[str, Any]]:
    """→ (选中掩码, 范围摘要)。无副作用；selection 校验失败即抛。"""
    if scope.mode == "all_rows":
        mask = [True] * len(features)
        return mask, {
            "mode": "all_rows", "row_count": len(features),
            "matched_rows": len(features), "complete": True,
            "unselected_policy": scope.unselected_policy,
            "subset_export": scope.subset_export,
        }
    if scope.mode == "predicate":
        if scope.predicate is None:
            raise CompileError("INVALID_PREDICATE", "predicate 模式缺少谓词")
        if scope.predicate.field not in set(available_fields):
            raise CompileError("UNKNOWN_FIELD",
                               f"谓词字段 {scope.predicate.field!r} 不在 schema")
        mask = [predicate_match(
            (f.get("properties") if isinstance(f, dict) else None) or {},
            scope.predicate) for f in features]
        return mask, {
            "mode": "predicate", "predicate": scope.predicate.canonical(),
            "row_count": len(features), "matched_rows": sum(mask),
            "complete": True,
            "unselected_policy": scope.unselected_policy,
            "subset_export": scope.subset_export,
        }
    return validate_selection(scope, selection_payload, features,
                              available_fields)


__all__ = ["SELECTION_RECOVERY_HINT", "predicate_match",
           "validate_selection", "resolve_scope"]
