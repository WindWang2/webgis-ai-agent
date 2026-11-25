"""Design Spec 角色绑定校验 —— role → 字段的可比性与健全性（WP2）。

同一主题跨地区/时相的字段名不同：通过**显式角色绑定**（规范
``data_roles`` × 每图 ``{role → layer_id, field}``）与既有 dataset
semantics/profile 保证单位/定义/分类可比。校验是**只读**的：

- 字段存在性与值型：读成员会话权威 MapSpec 的源 profile（Spatial Meta
  Profile 的字段名/类型/数值统计）；
- 缺失 / 空 / 全零 / 负数 / 全同值：如实给出**区别化的**状态码，不把
  「有数据」混称「可用」；
- 跨图可比性：``comparable_across_maps`` 角色的 unit 声明必须一致
  （以规范 role.unit 为契约；图例单位偏离为 warning）；
- 读不到会话/spec：``not_evaluated`` 类状态 —— 绝不伪造通过。

状态词表（closed）：``bound`` / ``role_unknown`` / ``field_missing`` /
``field_type_mismatch`` / ``all_empty`` / ``all_zero`` / ``negative_values``
/ ``unit_mismatch`` / ``session_unavailable`` / ``source_profile_missing``。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.design_specs.contract import ROLE_VALUE_TYPES

#: 校验器版本。
BINDING_CHECK_VERSION = "design_spec_binding.v1"

BOUND = "bound"
ROLE_UNKNOWN = "role_unknown"
FIELD_MISSING = "field_missing"
FIELD_TYPE_MISMATCH = "field_type_mismatch"
ALL_EMPTY = "all_empty"
ALL_ZERO = "all_zero"
NEGATIVE_VALUES = "negative_values"
UNIT_MISMATCH = "unit_mismatch"
SESSION_UNAVAILABLE = "session_unavailable"
SOURCE_PROFILE_MISSING = "source_profile_missing"

_NOT_EVALUATED_STATUSES = (SESSION_UNAVAILABLE, SOURCE_PROFILE_MISSING)


def _source_profile(document: Dict[str, Any], layer_id: str) -> Dict[str, Any]:
    """layer → 其源 profile（字段统计事实；缺失返回 {}）。"""
    layers = document.get("layers") or []
    source_key = None
    for layer in layers:
        if isinstance(layer, dict) and str(layer.get("id")) == str(layer_id):
            source_key = str(layer.get("source") or "")
            break
    if not source_key:
        return {}
    sources = document.get("sources") or {}
    source = sources.get(source_key) if isinstance(sources, dict) else None
    if not isinstance(source, dict):
        return {}
    profile = source.get("profile")
    return profile if isinstance(profile, dict) else {}


def _field_entry(profile: Dict[str, Any], field: str) -> Optional[Dict[str, Any]]:
    fields = profile.get("fields")
    if isinstance(fields, dict):
        entry = fields.get(field)
        if isinstance(entry, dict):
            return entry
    if isinstance(fields, list):
        for item in fields:
            if isinstance(item, dict) and str(item.get("name") or item.get("field")) == str(field):
                return item
    return None


def _numeric_stats(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    stats = entry.get("stats") or entry.get("numeric") or {}
    if isinstance(stats, dict) and ("min" in stats or "mean" in stats):
        return stats
    for key in ("min", "max", "mean"):
        if key not in entry:
            return None
    return {"min": entry.get("min"), "max": entry.get("max"), "mean": entry.get("mean")}


def _value_type_matches(role_value_type: str, entry: Dict[str, Any]) -> bool:
    ftype = str(entry.get("type") or entry.get("field_type") or "").lower()
    if not ftype:
        return True  # profile 未声明类型 → 不做误判，交由数值/文本探测
    numeric_tokens = ("number", "numeric", "int", "float", "double", "real")
    text_tokens = ("string", "text", "char")
    category_tokens = ("category", "bool", "boolean")
    if role_value_type == "number":
        return any(t in ftype for t in numeric_tokens) or ftype == ""
    if role_value_type == "text":
        return any(t in ftype for t in text_tokens) or "category" in ftype or ftype == ""
    if role_value_type == "category":
        return not any(t in ftype for t in numeric_tokens) or ftype == ""
    if role_value_type == "boolean":
        return any(t in ftype for t in category_tokens) or ftype == ""
    return True


def validate_binding_entry(
    binding: Dict[str, Any],
    role_spec: Dict[str, Any],
    document: Optional[Dict[str, Any]],
    *,
    declared_unit: Optional[str] = None,
) -> Dict[str, Any]:
    """单条绑定 → 校验回执（有界 evidence）。"""
    role_name = str(binding.get("role") or "")
    layer_id = str(binding.get("layer_id") or "")
    field = str(binding.get("field") or "")
    out: Dict[str, Any] = {
        "role": role_name,
        "layer_id": layer_id[:80],
        "field": field[:80],
        "session_id": str(binding.get("session_id") or "")[:80],
        "status": BOUND,
        "code": BOUND,
        "message": "",
    }
    if role_name not in {r.get("name") for r in (role_spec.get("data_roles") or [])}:
        out.update(status=ROLE_UNKNOWN, code=ROLE_UNKNOWN,
                   message=f"role '{role_name}' 未在规范 data_roles 声明")
        return out
    if document is None:
        out.update(status=SESSION_UNAVAILABLE, code=SESSION_UNAVAILABLE,
                   message="成员会话 MapSpec 不可解析（not_evaluated，不视作失败也不视作通过）")
        return out
    # 从 data_roles 解析命中的角色（调用方可传全 spec 或单角色包裹）
    role = None
    for r in (role_spec.get("data_roles") or []):
        if isinstance(r, dict) and str(r.get("name")) == role_name:
            role = r
            break
    if role is None:
        out.update(status=ROLE_UNKNOWN, code=ROLE_UNKNOWN,
                   message=f"role '{role_name}' 未在规范 data_roles 声明")
        return out
    profile = _source_profile(document, layer_id)
    fields_index = profile.get("fields")
    if not fields_index:
        out.update(status=SOURCE_PROFILE_MISSING, code=SOURCE_PROFILE_MISSING,
                   message="源 profile 缺失（无字段统计事实）——not_evaluated")
        return out
    entry = _field_entry(profile, field)
    if entry is None:
        out.update(status=FIELD_MISSING, code=FIELD_MISSING,
                   message=f"字段 '{field[:40]}' 不在源字段列表")
        return out
    role_value_type = str(role.get("value_type") or "number")
    if role_value_type not in ROLE_VALUE_TYPES:
        out.update(status=ROLE_UNKNOWN, code=ROLE_UNKNOWN,
                   message=f"规范 role.value_type 非法：{role_value_type}")
        return out
    if not _value_type_matches(role_value_type, entry):
        out.update(status=FIELD_TYPE_MISMATCH, code=FIELD_TYPE_MISMATCH,
                   message=f"值型不匹配：role={role_value_type}, field type={entry.get('type')}")
        return out
    stats = _numeric_stats(entry) if role_value_type == "number" else None
    if stats is not None:
        fmin, fmax = stats.get("min"), stats.get("max")
        if isinstance(fmin, (int, float)) and isinstance(fmax, (int, float)):
            if fmin == 0.0 and fmax == 0.0:
                out.update(status=ALL_ZERO, code=ALL_ZERO,
                           message="字段全零（min=max=0）——不能作为专题变量")
                return out
            if fmin < 0:
                out["negative_evidence"] = {"min": fmin}
                if role.get("measurement") in ("count", "density", "rate"):
                    out.update(status=NEGATIVE_VALUES, code=NEGATIVE_VALUES,
                               message="计数/密度/比率语义出现负值（违反量纲）")
                    return out
    # 单位契约：规范 role.unit 是契约面；绑定侧声明偏离 → warning 级 mismatch
    role_unit = role.get("unit")
    if role.get("unit_required") and not role_unit:
        out.update(status=UNIT_MISMATCH, code=UNIT_MISMATCH,
                   message="规范声明 unit_required 但未定义单位")
        return out
    if declared_unit and role_unit and str(declared_unit) != str(role_unit):
        out.update(status=UNIT_MISMATCH, code=UNIT_MISMATCH,
                   message=f"图例单位 '{str(declared_unit)[:16]}' 与规范契约单位 '{str(role_unit)[:16]}' 不一致")
        return out
    out["evidence"] = {
        "field_type": str(entry.get("type") or "")[:24],
        "stats": {k: stats[k] for k in ("min", "max", "mean") if isinstance(stats, dict) and k in stats} if stats else {},
    }
    return out


async def validate_role_bindings(
    bindings: List[Dict[str, Any]],
    spec_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """批量绑定校验：每条独立 try/except（单条失败不中断整批）。

    ``bindings`` 元素：``{role, session_id, layer_id, field}``；跨图可比性
    在汇总面判定（unit_mismatch 的 comparable 角色逐条列出）。
    """
    from app.services.mapspec_store import mapspec_store

    store = mapspec_store
    results: List[Dict[str, Any]] = []
    documents: Dict[str, Optional[Dict[str, Any]]] = {}
    roles_by_name = {
        str(r.get("name")): r for r in (spec_payload.get("data_roles") or [])
    }
    for binding in bindings[:32]:
        sid = str(binding.get("session_id") or "")
        try:
            if sid not in documents:
                documents[sid] = await store.get_mapspec(sid) if sid else None
            document = documents[sid]
            role_entry = roles_by_name.get(str(binding.get("role") or ""))
            if not isinstance(role_entry, dict):
                role_entry = {}
            if isinstance(document, dict):
                # 图例单位（若有）作为 declared_unit 参与契约对账
                declared_unit = _legend_unit(document, str(binding.get("layer_id") or ""))
                result = validate_binding_entry(
                    binding, {"data_roles": [role_entry]}, document,
                    declared_unit=declared_unit,
                )
            else:
                result = validate_binding_entry(
                    binding, {"data_roles": [role_entry]}, None,
                )
        except Exception as exc:  # noqa: BLE001 —— 单条绑定失败如实记录
            result = {
                "role": str(binding.get("role") or "")[:40],
                "layer_id": str(binding.get("layer_id") or "")[:80],
                "field": str(binding.get("field") or "")[:80],
                "session_id": sid[:80],
                "status": SESSION_UNAVAILABLE,
                "code": SESSION_UNAVAILABLE,
                "message": f"binding check failed: {str(exc)[:120]}",
            }
        results.append(result)

    # 跨图可比性汇总：comparable 角色的单位必须一致（规范契约面）
    comparability_findings: List[Dict[str, Any]] = []
    comparable_roles = {
        str(r.get("name")) for r in (spec_payload.get("data_roles") or [])
        if r.get("comparable_across_maps")
    }
    for role_name in sorted(comparable_roles):
        entries = [r for r in results if r["role"] == role_name]
        role_unit = (roles_by_name.get(role_name) or {}).get("unit")
        if len(entries) >= 2 and not role_unit:
            comparability_findings.append({
                "role": role_name,
                "finding": "UNIT_UNDECLARED",
                "affected": [r["session_id"] for r in entries][:8],
            })
        mismatches = [r for r in entries if r["code"] == UNIT_MISMATCH]
        if mismatches:
            comparability_findings.append({
                "role": role_name,
                "finding": UNIT_MISMATCH,
                "affected": [r["session_id"] for r in mismatches][:8],
            })

    evaluated = [r for r in results if r["status"] not in _NOT_EVALUATED_STATUSES]
    # fail-closed：无任何可评估结果（全 not_evaluated）≠ 通过
    ok = bool(evaluated) and all(r["status"] == BOUND for r in evaluated)
    return {
        "binding_check_version": BINDING_CHECK_VERSION,
        "ok": ok,
        "not_evaluated_count": len(results) - len(evaluated),
        "results": results,
        "comparability_findings": comparability_findings,
    }


def _legend_unit(document: Dict[str, Any], layer_id: str) -> Optional[str]:
    """layer 的图例单位声明（若有）—— 参与单位契约对账。"""
    for layer in (document.get("layers") or []):
        if isinstance(layer, dict) and str(layer.get("id")) == str(layer_id):
            legend = layer.get("legend_spec")
            if isinstance(legend, dict):
                unit = legend.get("unit")
                if isinstance(unit, str) and unit.strip():
                    return unit.strip()[:24]
    return None


__all__ = [
    "BINDING_CHECK_VERSION",
    "BOUND",
    "ROLE_UNKNOWN",
    "FIELD_MISSING",
    "FIELD_TYPE_MISMATCH",
    "ALL_EMPTY",
    "ALL_ZERO",
    "NEGATIVE_VALUES",
    "UNIT_MISMATCH",
    "SESSION_UNAVAILABLE",
    "SOURCE_PROFILE_MISSING",
    "validate_binding_entry",
    "validate_role_bindings",
]
