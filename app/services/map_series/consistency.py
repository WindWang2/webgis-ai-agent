"""Map Series 跨成员一致性检查（WP3）—— 结构化 findings，封闭词表 codes。

只读：从成员 session 的权威 MapSpec 读表达证据（组件/图例方法/色带/
资产绑定），与系列计划（规范 pin + comparability 契约）对照。读不到会话
数据时如实 ``SESSION_UNAVAILABLE``，绝不编造证据。

负例纪律（防过度强制）：不同指标不强制同色域 —— PALETTE_MISMATCH 只在
``dimension.axis == indicator`` 且 comparability 声明可比组（shared_palette
+ 成员绑定共同可比角色）时生效；其余组合绝不产生该 finding。

传播意图一致性（LOCKED_FIELD_CONFLICT）：``intents`` 缺省不传 —— 传播
preview/apply 侧传入时，与成员 ``locked_fields``（user-wins 锁）相交即
warning 披露。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from app.services.map_series import service as ms_service
from app.services.map_series.contract import MapSeriesV1
from app.services.map_series.store import get_series

#: 封闭词表（每条真实现；新增码 = 本模块唯一权威点）。
SPEC_PIN_MISSING = "SPEC_PIN_MISSING"
SESSION_UNAVAILABLE = "SESSION_UNAVAILABLE"
UNIT_MISMATCH = "UNIT_MISMATCH"
CLASSIFICATION_SCOPE_CONFLICT = "CLASSIFICATION_SCOPE_CONFLICT"
PALETTE_MISMATCH_SHARED = "PALETTE_MISMATCH_SHARED"
TITLE_MISSING = "TITLE_MISSING"
SOURCE_MISSING = "SOURCE_MISSING"
LEGEND_MISSING = "LEGEND_MISSING"
ASSET_VERSION_DRIFT = "ASSET_VERSION_DRIFT"
EXTENT_DIVERGENCE = "EXTENT_DIVERGENCE"
LOCKED_FIELD_CONFLICT = "LOCKED_FIELD_CONFLICT"

#: 传播意图 → 可锁表达字段（user-wins；词表 = design_specs LOCKABLE_FIELDS）。
INTENT_LOCK_FIELDS: Dict[str, str] = {
    "palette": "palettes",
    "classification": "classification_method",
    "components": "required_components",
    "typography": "typography",
}

_MAX_FINDINGS = 100


def _finding(
    code: str,
    severity: str,
    message: str,
    *,
    member_id: Optional[str] = None,
    component: Optional[str] = None,
    fix_hint: Optional[str] = None,
) -> Dict[str, Any]:
    item: Dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if member_id:
        item["member_id"] = member_id
    if component:
        item["component"] = component
    if fix_hint:
        item["fix_hint"] = fix_hint
    return item


def _majority(values: List[str]) -> str:
    """出现最多的取值（并列取先见序；调用方保证非空）。"""
    counts: Dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    best = None
    best_n = -1
    for v in values:  # 先见序优先
        if counts[v] > best_n:
            best, best_n = v, counts[v]
    return str(best)


def _parse_intents(intents: Optional[Dict[str, Any]]) -> Optional[Any]:
    if intents is None:
        return None
    from app.services.map_series.propagation import PropagationIntents

    return PropagationIntents.model_validate(intents)


def _parse_plan(series_payload: Dict[str, Any]) -> Optional[MapSeriesV1]:
    try:
        return MapSeriesV1.model_validate(series_payload)
    except ValidationError:
        return None


def _locked_conflict_findings(plan: MapSeriesV1, intents: Any) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    present = intents.model_dump(exclude_none=True, exclude_defaults=False)
    intent_fields = [k for k in INTENT_LOCK_FIELDS if present.get(k)]
    for member in plan.members:
        if not member.enabled:
            continue
        hit = sorted(
            {INTENT_LOCK_FIELDS[k] for k in intent_fields}
            & set(member.locked_fields)
        )
        if hit:
            findings.append(
                _finding(
                    LOCKED_FIELD_CONFLICT,
                    "warning",
                    (
                        f"propagation intents touch user-locked fields {hit} "
                        f"on member '{member.member_id}' — they will be skipped"
                    ),
                    member_id=member.member_id,
                    fix_hint="解除成员 locked_fields 或从传播意图中移除对应面",
                )
            )
    return findings


async def run_series_consistency(
    db: Any,
    series_id: str,
    *,
    intents: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """运行系列一致性检查 → ``{findings, ok}``（ok = 无 error 级 findings）。

    只读：不写任何会话/数据库行。``intents`` 可选（传播侧自查锁定冲突）。
    """
    series = get_series(db, series_id)
    plan = _parse_plan(series.payload or {})
    findings: List[Dict[str, Any]] = []

    if plan is None:
        return {
            "findings": [
                _finding(
                    SPEC_PIN_MISSING,
                    "error",
                    "series payload failed typed validation — plan unreadable",
                    fix_hint="重落一版通过 map_series.v1 校验的计划",
                )
            ],
            "ok": False,
        }

    typed_intents = _parse_intents(intents)
    if typed_intents is not None:
        findings.extend(_locked_conflict_findings(plan, typed_intents))

    # ── 规范 pin 解析（软废弃兼容：退役规范仍可解析） ──────────────────
    spec_payload: Optional[Dict[str, Any]] = None
    try:
        from app.services.design_specs.versioning import get_spec, get_version

        spec = get_spec(db, str(plan.design_spec_id))
        version_row = get_version(db, str(plan.design_spec_id), plan.design_spec_version)
        spec_payload = version_row.payload if version_row is not None else spec.payload
    except Exception:  # noqa: BLE001 —— pin 不可解析：逐成员 error（可定位）
        for member in plan.members:
            if not member.enabled:
                continue
            findings.append(
                _finding(
                    SPEC_PIN_MISSING,
                    "error",
                    (
                        f"design spec pin '{plan.design_spec_id}' v"
                        f"{plan.design_spec_version or '?'} is not resolvable "
                        f"for member '{member.member_id}'"
                    ),
                    member_id=member.member_id,
                    fix_hint="检查 design_spec_id/version 或重新绑定存在的规范",
                )
            )

    comparable_roles: Dict[str, Optional[str]] = {}
    if isinstance(spec_payload, dict):
        for role in spec_payload.get("data_roles") or []:
            if isinstance(role, dict) and role.get("comparable_across_maps"):
                comparable_roles[str(role.get("name") or "")] = (
                    str(role.get("unit")) if role.get("unit") else None
                )
    for role, unit in (plan.comparability.unit_contract or {}).items():
        comparable_roles.setdefault(str(role), str(unit))

    # ── 成员会话证据 ────────────────────────────────────────────────────
    evidence: Dict[str, Dict[str, Any]] = {}
    for member in plan.members:
        if not member.enabled:
            continue
        mapspec = await ms_service.load_member_mapspec(member.session_id)
        if mapspec is None:
            findings.append(
                _finding(
                    SESSION_UNAVAILABLE,
                    "warning",
                    (
                        f"member '{member.member_id}' session MapSpec is "
                        "unavailable — related checks not_evaluated"
                    ),
                    member_id=member.member_id,
                    fix_hint="确认会话仍存活（有权威 MapSpec）后重跑一致性",
                )
            )
            continue
        bound_fields = tuple(member.data_bindings.values())
        primary = ms_service.primary_thematic_layer(mapspec, bound_fields)
        evidence[member.member_id] = {
            "mapspec": mapspec,
            "component_types": ms_service.component_types(mapspec),
            "primary": primary,
            "method": ms_service.legend_method(primary) if primary else None,
            "palette": ms_service.palette_evidence(primary) if primary else None,
            "unit": ms_service.declared_unit(primary) if primary else None,
            "bounds": ms_service.source_bounds(mapspec),
            "assets": ms_service.symbol_asset_bindings(mapspec),
            "thematics": ms_service.thematic_layers(mapspec, bound_fields),
        }

    # ── UNIT_MISMATCH（error）：可比角色单位跨成员/对照契约不一致 ────────
    role_members: Dict[str, List[str]] = {}
    for member in plan.members:
        if not member.enabled:
            continue
        for role in member.data_bindings:
            if role in comparable_roles:
                role_members.setdefault(role, []).append(member.member_id)
    for role, member_ids in role_members.items():
        role_unit = comparable_roles.get(role)
        observed: Dict[str, Optional[str]] = {}
        for mid in member_ids:
            ev = evidence.get(mid)
            if ev is None:
                continue  # SESSION_UNAVAILABLE 已披露
            observed[mid] = ev.get("unit")
        for mid, unit in observed.items():
            if unit and role_unit and str(unit) != str(role_unit):
                findings.append(
                    _finding(
                        UNIT_MISMATCH,
                        "error",
                        (
                            f"member '{mid}' binds comparable role '{role}' with "
                            f"declared unit '{unit}' but the contract requires "
                            f"'{role_unit}'"
                        ),
                        member_id=mid,
                        component=str(
                            (evidence[mid].get("primary") or {}).get("id") or ""
                        ),
                        fix_hint=f"统一 {role} 的单位声明为 {role_unit}",
                    )
                )
        distinct = {str(u) for u in observed.values() if u}
        if len(distinct) > 1:
            deviants = [
                mid for mid, u in observed.items()
                if u and str(u) != _majority([x for x in observed.values() if x])
            ]
            for mid in deviants:
                findings.append(
                    _finding(
                        UNIT_MISMATCH,
                        "error",
                        (
                            f"member '{mid}' unit '{observed[mid]}' diverges from "
                            f"the series majority for comparable role '{role}'"
                        ),
                        member_id=mid,
                        fix_hint=f"对齐 {role} 跨成员单位",
                    )
                )

    # ── CLASSIFICATION_SCOPE_CONFLICT（error） ──────────────────────────
    if plan.comparability.shared_classification:
        methods = {
            mid: ev.get("method") for mid, ev in evidence.items() if ev.get("method")
        }
        distinct = set(methods.values())
        if len(distinct) > 1:
            majority_method = _majority(list(methods.values()))
            for mid, method in methods.items():
                if method != majority_method:
                    findings.append(
                        _finding(
                            CLASSIFICATION_SCOPE_CONFLICT,
                            "error",
                            (
                                f"shared_classification is declared but member "
                                f"'{mid}' uses method '{method}' while the series "
                                f"majority uses '{majority_method}'"
                            ),
                            member_id=mid,
                            component=str(
                                (evidence[mid].get("primary") or {}).get("id") or ""
                            ),
                            fix_hint="对齐成员分类方法（传播 classification 意图）",
                        )
                    )

    # ── PALETTE_MISMATCH_SHARED（error；负例闸门见模块 docstring） ───────
    gate = (
        plan.dimension.axis == "indicator"
        and plan.comparability.shared_palette
        and len(role_members) > 0  # comparability 声明的可比组存在被共同绑定
    )
    if gate:
        palettes = {
            mid: ev.get("palette") for mid, ev in evidence.items() if ev.get("palette")
        }
        distinct = set(palettes.values())
        if len(distinct) > 1:
            majority_palette = _majority(list(palettes.values()))
            for mid, palette in palettes.items():
                if palette != majority_palette:
                    findings.append(
                        _finding(
                            PALETTE_MISMATCH_SHARED,
                            "error",
                            (
                                f"shared_palette is declared (comparable indicator "
                                f"group) but member '{mid}' palette diverges"
                            ),
                            member_id=mid,
                            component=str(
                                (evidence[mid].get("primary") or {}).get("id") or ""
                            ),
                            fix_hint="传播 palette 意图对齐成员色带",
                        )
                    )

    # ── 组件缺席 / 图例缺席 / 资产漂移 / 视域分歧 ───────────────────────
    asset_revisions: Dict[str, Dict[str, List[str]]] = {}
    for member in plan.members:
        if not member.enabled:
            continue
        ev = evidence.get(member.member_id)
        if ev is None:
            continue
        comp_types = set(ev["component_types"])
        if "title" not in comp_types:
            findings.append(
                _finding(
                    TITLE_MISSING,
                    "warning",
                    f"member '{member.member_id}' has no title component",
                    member_id=member.member_id,
                    component="title",
                    fix_hint="ensure title 组件（传播 components 意图）",
                )
            )
        if "attribution" not in comp_types:
            findings.append(
                _finding(
                    SOURCE_MISSING,
                    "warning",
                    f"member '{member.member_id}' has no attribution/source component",
                    member_id=member.member_id,
                    component="attribution",
                    fix_hint="ensure attribution 组件并注明数据来源",
                )
            )
        has_legend_component = "legend" in comp_types
        for layer in ev["thematics"]:
            has_legend_spec = isinstance(layer.get("legend_spec"), dict)
            if has_legend_spec or has_legend_component:
                continue
            findings.append(
                _finding(
                    LEGEND_MISSING,
                    "warning",
                    (
                        f"member '{member.member_id}' thematic layer "
                        f"'{layer.get('id')}' has no legend_spec and no legend "
                        "component"
                    ),
                    member_id=member.member_id,
                    component=str(layer.get("id") or ""),
                    fix_hint="为专题层补 legend_spec 或 ensure legend 组件",
                )
            )
        for binding in ev["assets"]:
            asset_revisions.setdefault(binding["asset_id"], {}).setdefault(
                str(binding.get("revision")), []
            ).append(member.member_id)
        bounds = ev.get("bounds")
        if bounds is not None and not member.extent_override:
            ev["bounds"] = bounds

    for asset_id, rev_map in asset_revisions.items():
        if len(rev_map) <= 1:
            continue
        majority_rev = _majority(list(rev_map.keys()))
        for rev, mids in rev_map.items():
            if rev == majority_rev:
                continue
            for mid in mids:
                findings.append(
                    _finding(
                        ASSET_VERSION_DRIFT,
                        "warning",
                        (
                            f"member '{mid}' binds symbol asset '{asset_id}' at "
                            f"revision {rev} while the series majority uses "
                            f"{majority_rev}"
                        ),
                        member_id=mid,
                        component=asset_id,
                        fix_hint="对齐成员 symbol_asset revision 绑定",
                    )
                )

    if plan.comparability.shared_extent_by_role:
        bounds_map = {
            mid: ev["bounds"] for mid, ev in evidence.items() if ev.get("bounds")
        }
        if len(bounds_map) >= 2:
            mids = list(bounds_map)
            anchor = mids[0]

            def _iou(a: Any, b: Any) -> float:
                ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
                iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
                inter = ix * iy
                area_a = (a[2] - a[0]) * (a[3] - a[1])
                area_b = (b[2] - b[0]) * (b[3] - b[1])
                denom = area_a + area_b - inter
                return inter / denom if denom > 0 else 0.0

            for mid in mids[1:]:
                if _iou(bounds_map[anchor], bounds_map[mid]) < 0.5:
                    findings.append(
                        _finding(
                            EXTENT_DIVERGENCE,
                            "info",
                            (
                                f"members '{anchor}' and '{mid}' diverge in extent "
                                "(bbox IoU < 0.5) — disclosure only"
                            ),
                            member_id=mid,
                            fix_hint="如需对齐可设置成员 extent_override",
                        )
                    )

    findings = findings[:_MAX_FINDINGS]
    ok = not any(f["severity"] == "error" for f in findings)
    return {"findings": findings, "ok": ok, "series_id": series.id}


__all__ = [
    "run_series_consistency",
    "INTENT_LOCK_FIELDS",
    "SPEC_PIN_MISSING",
    "SESSION_UNAVAILABLE",
    "UNIT_MISMATCH",
    "CLASSIFICATION_SCOPE_CONFLICT",
    "PALETTE_MISMATCH_SHARED",
    "TITLE_MISSING",
    "SOURCE_MISSING",
    "LEGEND_MISSING",
    "ASSET_VERSION_DRIFT",
    "EXTENT_DIVERGENCE",
    "LOCKED_FIELD_CONFLICT",
]
