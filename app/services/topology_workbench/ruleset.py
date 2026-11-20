"""Topology Workbench —— 规则作者与持久版本（WP02）。

规则集 = 有名规则集合（封闭词表 + 源角色绑定需求 + 容差/阈值/单位/边界
政策/启用位）。修改规则集内容即生成**新版本**（version+1，digest 重算）；
历史运行持有自己的 ruleset_digest，读取原配置不受后续编辑影响。

校验纪律（fail-fast，任务书 WP02）：
- 未知规则类型 / 未知严重级 / 词表外角色 / 未知参数键 → ValueError；
- 负容差、NaN、超量级、字符串数值 → ValueError；
- 必需角色缺失、complete 角色语义由 RULE_ROLE_SPECS 钉住；
- 规则数 ≤ MAX_RULES_PER_SET，rule_id 唯一且 ≤64 字符。
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Tuple

from app.services.topology_workbench.contracts import (
    BOUNDARY_POLICIES,
    CONNECTOR_KINDS,
    MAX_AREA_THRESHOLD_M2,
    MAX_LINEAR_TOLERANCE_M,
    MAX_RULES_PER_SET,
    RULE_ROLE_SPECS,
    RULE_TYPES,
    SEVERITIES,
)

# 每类规则的合法参数键（封闭；未知键拒绝 —— 不做静默忽略）。
_RULE_PARAM_KEYS: Dict[str, frozenset] = {
    "polygons_no_overlap": frozenset({
        "overlap_area_threshold_m2",   # 重叠面积阈值（默认 0.0 = 任何正面积重叠）
        "boundary_touch",              # 共边/共点是否违规（默认 false：面积重叠才算）
    }),
    "area_coverage": frozenset({
        "gap_area_threshold_m2",       # 缺口面积阈值（默认 0.0）
    }),
    "objects_covered_by_target": frozenset({
        "boundary_policy",             # covered | within（默认 covered）
        "outside_area_threshold_m2",   # 越界面积阈值（面要素，默认 0.0）
        "outside_length_threshold_m",  # 越界长度阈值（线要素，默认 0.0）
        "outside_distance_threshold_m", # 越界距离阈值（点要素，默认 0.0）
    }),
    "cross_layer_no_overlap": frozenset({
        "overlap_area_threshold_m2",
    }),
    "line_endpoints_connected": frozenset({
        "tolerance_m",                 # 端点连接容差（线性米，必填 >0）
        "connector_kind",              # points | lines | points+lines（默认 points）
        "allow_line_line",             # 线端点与线的连接是否计入（默认 False）
    }),
}

_NUM_PARAMS: Dict[str, Tuple[str, ...]] = {
    # 参数键 → (默认值, 上限, 是否允许 0)
    "overlap_area_threshold_m2": (0.0, MAX_AREA_THRESHOLD_M2, True),
    "gap_area_threshold_m2": (0.0, MAX_AREA_THRESHOLD_M2, True),
    "outside_area_threshold_m2": (0.0, MAX_AREA_THRESHOLD_M2, True),
    "outside_length_threshold_m": (0.0, MAX_LINEAR_TOLERANCE_M, True),
    "outside_distance_threshold_m": (0.0, MAX_LINEAR_TOLERANCE_M, True),
    "tolerance_m": (None, MAX_LINEAR_TOLERANCE_M, False),  # 必填
}


def _validate_number(key: str, value: Any) -> float:
    """数值参数护栏：有限、非 NaN、非负、≤上限。"""
    default, upper, allow_zero = _NUM_PARAMS[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"参数 {key} 必须是数值，收到 {type(value).__name__}")
    v = float(value)
    if math.isnan(v) or math.isinf(v):
        raise ValueError(f"参数 {key} 必须是有限数值")
    if v < 0:
        raise ValueError(f"参数 {key} 不允许负值：{v}")
    if not allow_zero and v <= 0:
        raise ValueError(f"参数 {key} 必须 > 0")
    if v > upper:
        raise ValueError(f"参数 {key} 超出上限 {upper}：{v}")
    return v


def validate_rule(rule: Dict[str, Any], *, index: int = 0) -> Dict[str, Any]:
    """单条规则 dict → 规范化形状（新 dict）；非法即 ValueError（typed 信息在 message）。"""
    if not isinstance(rule, dict):
        raise ValueError(f"rules[{index}] 必须是对象")
    rule_id = rule.get("rule_id")
    if not isinstance(rule_id, str) or not (1 <= len(rule_id) <= 64):
        raise ValueError(f"rules[{index}].rule_id 必须是 1..64 字符")
    rule_type = rule.get("rule_type")
    if rule_type not in RULE_TYPES:
        raise ValueError(
            f"rules[{index}].rule_type '{rule_type}' 不在封闭词表 {list(RULE_TYPES)} 内"
        )
    severity = rule.get("severity", "warn")
    if severity not in SEVERITIES:
        raise ValueError(f"rules[{index}].severity '{severity}' 不在词表 {SEVERITIES} 内")
    enabled = rule.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"rules[{index}].enabled 必须是布尔")
    description = str(rule.get("description") or "")[:200]

    params_in = rule.get("params") or {}
    if not isinstance(params_in, dict):
        raise ValueError(f"rules[{index}].params 必须是对象")
    allowed = _RULE_PARAM_KEYS[rule_type]
    unknown = set(params_in) - allowed
    if unknown:
        raise ValueError(
            f"rules[{index}]（{rule_type}）未知参数键：{sorted(unknown)}；"
            f"合法键 {sorted(allowed)}"
        )
    params: Dict[str, Any] = {}
    for key, raw in params_in.items():
        if key in _NUM_PARAMS:
            params[key] = _validate_number(key, raw)
        elif key == "boundary_policy":
            if raw not in BOUNDARY_POLICIES:
                raise ValueError(
                    f"rules[{index}].params.boundary_policy '{raw}' "
                    f"不在词表 {sorted(BOUNDARY_POLICIES)} 内"
                )
            params[key] = raw
        elif key == "connector_kind":
            if raw not in CONNECTOR_KINDS:
                raise ValueError(
                    f"rules[{index}].params.connector_kind '{raw}' "
                    f"不在词表 {sorted(CONNECTOR_KINDS)} 内"
                )
            params[key] = raw
        elif key in ("boundary_touch", "allow_line_line"):
            if not isinstance(raw, bool):
                raise ValueError(f"rules[{index}].params.{key} 必须是布尔")
            params[key] = raw
        else:  # pragma: no cover — _RULE_PARAM_KEYS 与分支不一致即缺陷
            raise ValueError(f"参数 {key} 缺少校验分支（词表缺陷）")

    # 类型特定的必填默认（在 normalize 时补齐，digest 稳定）
    if rule_type == "line_endpoints_connected" and "tolerance_m" not in params:
        raise ValueError("line_endpoints_connected 必须提供 params.tolerance_m（线性米，>0）")

    return {
        "rule_id": rule_id,
        "rule_type": rule_type,
        "severity": severity,
        "enabled": enabled,
        "description": description,
        "params": params,
    }


def validate_ruleset_rules(rules: Any) -> List[Dict[str, Any]]:
    """规则集合校验：数量、唯一 id、规范化。"""
    if not isinstance(rules, list) or not rules:
        raise ValueError("rules 必须是非空数组")
    if len(rules) > MAX_RULES_PER_SET:
        raise ValueError(f"规则数 {len(rules)} 超出上限 {MAX_RULES_PER_SET}")
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for i, r in enumerate(rules):
        norm = validate_rule(r, index=i)
        if norm["rule_id"] in seen:
            raise ValueError(f"rule_id 重复：{norm['rule_id']}")
        seen.add(norm["rule_id"])
        out.append(norm)
    return out


def ruleset_digest(rules: List[Dict[str, Any]]) -> str:
    """规范化投影的 sha256（启用位参与；同集同指纹 —— 与 DQ ruleset_digest 同纪律）。"""
    projection = [
        {
            "id": r["rule_id"],
            "t": r["rule_type"],
            "s": r["severity"],
            "p": r["params"],
            "e": bool(r["enabled"]),
        }
        for r in rules
    ]
    canonical = json.dumps(
        projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def rule_catalog() -> List[Dict[str, Any]]:
    """规则目录（REST GET /rules/catalog 与工具描述共用单一事实源）。"""
    catalog = []
    for rt in RULE_TYPES:
        spec = RULE_ROLE_SPECS[rt]
        roles = [
            {"role": role, "requirement": req}
            for role, req in sorted(spec.items())
        ]
        defaults: Dict[str, Any] = {}
        for key, (default, _upper, _az) in _NUM_PARAMS.items():
            if key in _RULE_PARAM_KEYS[rt] and default is not None:
                defaults[key] = default
        if "boundary_policy" in _RULE_PARAM_KEYS[rt]:
            defaults["boundary_policy"] = "covered"
        if "connector_kind" in _RULE_PARAM_KEYS[rt]:
            defaults["connector_kind"] = "points"
        if "boundary_touch" in _RULE_PARAM_KEYS[rt]:
            defaults["boundary_touch"] = False
        if "allow_line_line" in _RULE_PARAM_KEYS[rt]:
            defaults["allow_line_line"] = False
        catalog.append({
            "rule_type": rt,
            "roles": roles,
            "params": sorted(_RULE_PARAM_KEYS[rt]),
            "defaults": defaults,
            "severities": list(SEVERITIES),
        })
    return catalog


def required_roles(rule_type: str) -> List[str]:
    spec = RULE_ROLE_SPECS[rule_type]
    return sorted(r for r, req in spec.items() if req.startswith("required"))


def complete_roles(rule_type: str) -> List[str]:
    """必须全量快照的角色（携带 scope 的绑定会被拒绝 —— T12）。"""
    spec = RULE_ROLE_SPECS[rule_type]
    return sorted(r for r, req in spec.items() if req.endswith("+complete"))


def normalize_run_bindings(
    rules: List[Dict[str, Any]],
    bindings: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """运行提交期的绑定校验：enabled 规则的每个必需角色都必须有绑定。

    返回 {rule_id: {role: binding}}（规范化投影，参与运行复用键）；
    绑定形状非法/必需角色缺失 → ValueError。
    scope 允许性（complete 角色禁 scope）在 snapshots 解析时判定，
    这里只做形状校验，保证错误信息可带角色上下文。
    """
    if not isinstance(bindings, dict) or not bindings:
        raise ValueError("bindings 必须是非空对象（角色名 → 绑定）")
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for rule in rules:
        if not rule.get("enabled"):
            continue
        rt = rule["rule_type"]
        rid = rule["rule_id"]
        spec = RULE_ROLE_SPECS[rt]
        out[rid] = {}
        for role, req in spec.items():
            is_required = req.startswith("required")
            binding = bindings.get(role)
            if binding is None:
                if is_required:
                    raise ValueError(
                        f"规则 {rid}（{rt}）的必需角色 '{role}' 未绑定"
                    )
                continue
            if not isinstance(binding, dict):
                raise ValueError(f"角色 '{role}' 的绑定必须是对象")
            out[rid][role] = _normalize_binding(role, rt, binding)
    return out


def _normalize_binding(role: str, rule_type: str, binding: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.topology_workbench.contracts import (
        BBOX_BINDABLE_ROLES,
        BINDING_KINDS,
        SCOPE_KINDS,
    )

    kind = binding.get("kind")
    if kind not in BINDING_KINDS:
        raise ValueError(
            f"角色 '{role}' 绑定 kind '{kind}' 不在词表 {sorted(BINDING_KINDS)} 内"
        )
    norm: Dict[str, Any] = {"kind": kind}
    if kind == "session_ref":
        ref = binding.get("ref")
        if not isinstance(ref, str) or not ref.startswith("ref:"):
            raise ValueError(f"角色 '{role}' session_ref 绑定需要 ref: 前缀的 ref")
        norm["ref"] = ref[:128]
        sid = binding.get("session_id")
        if sid is not None:
            norm["session_id"] = str(sid)[:255]
    elif kind == "project_artifact":
        pid = binding.get("project_id")
        aid = binding.get("artifact_id")
        if not isinstance(pid, str) or not pid:
            raise ValueError(f"角色 '{role}' project_artifact 绑定需要 project_id")
        if not isinstance(aid, str) or not aid:
            raise ValueError(f"角色 '{role}' project_artifact 绑定需要 artifact_id")
        norm["project_id"] = pid[:255]
        norm["artifact_id"] = aid[:255]
        rev = binding.get("revision_no")
        if rev is not None:
            if isinstance(rev, bool) or not isinstance(rev, int) or rev < 1:
                raise ValueError(f"角色 '{role}' revision_no 必须是正整数")
            norm["revision_no"] = rev
    else:  # bbox —— 仅特定角色可绑定
        if role not in BBOX_BINDABLE_ROLES:
            raise ValueError(
                f"角色 '{role}' 不允许 bbox 绑定（允许：{sorted(BBOX_BINDABLE_ROLES)}）"
            )
        bbox = binding.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise ValueError(f"角色 '{role}' bbox 必须是 [w,s,e,n] 四元组")
        try:
            w, s, e, n = (float(v) for v in bbox)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"角色 '{role}' bbox 坐标必须是数值") from exc
        if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= 90 and -90 <= n <= 90):
            raise ValueError(f"角色 '{role}' bbox 超出 WGS84 值域")
        if s > n:
            raise ValueError(f"角色 '{role}' bbox 南北颠倒（s > n）")
        if w > e:
            raise ValueError(
                f"角色 '{role}' bbox 跨反经线（w > e）—— 该区域当前不支持"
            )
        norm["bbox"] = [w, s, e, n]

    scope = binding.get("scope")
    if scope is not None:
        if not isinstance(scope, dict):
            raise ValueError(f"角色 '{role}' scope 必须是对象")
        sk = scope.get("kind")
        if sk not in SCOPE_KINDS:
            raise ValueError(
                f"角色 '{role}' scope.kind '{sk}' 不在词表 {sorted(SCOPE_KINDS)} 内"
            )
        if kind == "bbox":
            raise ValueError("bbox 绑定不能同时携带 scope")
        norm_scope: Dict[str, Any] = {"kind": sk}
        if sk == "bbox":
            sb = scope.get("bbox")
            if not isinstance(sb, (list, tuple)) or len(sb) != 4:
                raise ValueError(f"角色 '{role}' scope.bbox 必须是四元组")
            try:
                sw, ss, se, sn = (float(v) for v in sb)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"角色 '{role}' scope.bbox 坐标必须是数值") from exc
            if not (-180 <= sw <= 180 and -180 <= se <= 180 and -90 <= ss <= 90 and -90 <= sn <= 90):
                raise ValueError(f"角色 '{role}' scope.bbox 超出 WGS84 值域")
            if ss > sn or sw > se:
                raise ValueError(f"角色 '{role}' scope.bbox 非法（颠倒或跨反经线）")
            norm_scope["bbox"] = [sw, ss, se, sn]
        elif sk == "where":
            where = scope.get("where")
            if not isinstance(where, dict):
                raise ValueError(f"角色 '{role}' scope.where 必须是 typed 谓词 AST 对象")
            norm_scope["where"] = where
        else:  # ids
            ids = scope.get("ids")
            if not isinstance(ids, list) or not ids:
                raise ValueError(f"角色 '{role}' scope.ids 必须是非空数组")
            if len(ids) > 200:
                raise ValueError(f"角色 '{role}' scope.ids 上限 200")
            norm_scope["ids"] = [str(i)[:128] for i in ids]
        norm["scope"] = norm_scope

    # 选择句柄（G24）：提交时解析为 scope，句柄本身绝不持久化。
    handle = binding.get("selection_handle")
    if handle is not None:
        if "scope" in norm:
            raise ValueError(f"角色 '{role}' 不能同时携带 scope 与 selection_handle")
        if not isinstance(handle, str) or not (8 <= len(handle) <= 128):
            raise ValueError(f"角色 '{role}' selection_handle 形状非法")
        norm["selection_handle"] = handle
    return norm


__all__ = [
    "validate_rule", "validate_ruleset_rules", "ruleset_digest",
    "rule_catalog", "required_roles", "complete_roles",
    "normalize_run_bindings",
]
