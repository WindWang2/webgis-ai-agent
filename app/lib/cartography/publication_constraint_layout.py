"""Explicit page-profile consumption of the existing layout solver.

``solve_layout_v5`` is the only solver. This module does not invent another
one. A missing or unsupported page profile is ``not_evaluated``: callers must
leave the document unchanged so default publication bytes stay the same.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.lib.cartography.layout_constraint_spec import derive_layout_constraints
from app.lib.cartography.layout_solver import (
    LayoutParticipantV4,
    page_profiles,
    solve_layout_v4,
    solve_layout_v5,
)
from app.lib.cartography.render_scene import DEFAULT_COMPONENT_ANCHOR

BROWSER_EXPORT_CONSTRAINT_NOTE = (
    "constraint_layout=not_evaluated: 浏览器栅格导出不运行出版约束求解。"
    "矢量出版路径仅在显式 page_profile（如 a4_landscape）时执行既有 "
    "solve_layout_v5。未经验证的导出不是出版级成功。"
)

_ANCHORS = frozenset({
    "top-left", "top-center", "top-right",
    "bottom-left", "bottom-center", "bottom-right",
})
_SOLVER_PROFILES = frozenset(page_profiles())


def not_evaluated(page_profile: str = "", reason: str = "page_profile_absent") -> Dict[str, Any]:
    """Disclosure for a path that did not run the constraint solver."""
    return {
        "constraint_layout": "not_evaluated",
        "page_profile": str(page_profile or "")[:32],
        "reason": reason,
        "placements": [],
        "suppressed": [],
        "findings": [],
        "key_component_ids": [],
    }


def _components(raw: Any) -> List[Mapping[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [item for item in raw if isinstance(item, Mapping) and item.get("id") and item.get("type")]


def _zone(comp: Mapping[str, Any]) -> str:
    placement = comp.get("placement") if isinstance(comp.get("placement"), Mapping) else {}
    anchor = placement.get("anchor") if placement.get("mode") == "anchor" else ""
    zone = anchor or comp.get("position") or ""
    if isinstance(zone, str) and zone in _ANCHORS:
        return zone
    fallback = DEFAULT_COMPONENT_ANCHOR.get(str(comp.get("type") or ""), "none")
    return fallback if fallback in _ANCHORS else "bottom-right"


def _participants(components: Iterable[Mapping[str, Any]]) -> List[LayoutParticipantV4]:
    participants: List[LayoutParticipantV4] = []
    for comp in components:
        if comp.get("enabled") is False:
            continue
        priority = comp.get("priority", 50)
        try:
            priority_i = int(priority)
        except (TypeError, ValueError):
            priority_i = 50
        participants.append(LayoutParticipantV4(
            id=str(comp["id"])[:64],
            type=str(comp["type"])[:48],
            requested_zone=_zone(comp),
            priority=max(0, min(priority_i, 1000)),
            optional=comp.get("optional", True) is not False,
        ))
    return participants


def _report_from_solution(solution: Any, spec: Any, profile: str) -> Dict[str, Any]:
    placements = [
        {
            "id": item.id,
            "type": item.type,
            "zone": item.zone,
            "moved": bool(item.moved),
        }
        for item in solution.placements
    ]
    suppressed = [
        {"id": item.id, "type": item.type, "reason": item.reason}
        for item in solution.suppressed
    ]
    findings = [
        {"code": finding.code, "component_id": finding.component_id}
        for finding in solution.constraint_findings
    ]
    return {
        "constraint_layout": "evaluated",
        "page_profile": profile,
        "reason": "",
        "placements": placements[:32],
        "placement_zones": {item["id"]: item["zone"] for item in placements},
        "suppressed": suppressed[:16],
        "suppressed_ids": [item["id"] for item in suppressed],
        "findings": findings[:16],
        "key_component_ids": list(spec.key_component_ids),
        "disclosures_truncated": (
            len(placements) > 32 or len(suppressed) > 16 or len(findings) > 16
        ),
    }


def evaluate_constraint_layout(
    components: Any,
    *,
    page_profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Run ``solve_layout_v5`` only for an explicit solver page profile."""
    profile = str(page_profile or "").strip()
    if not profile:
        return not_evaluated("", "page_profile_absent")
    if profile not in _SOLVER_PROFILES:
        return not_evaluated(profile, "profile_unsupported")
    parts = _participants(_components(components))
    spec = derive_layout_constraints(
        [dict(comp) for comp in _components(components) if comp.get("enabled") is not False],
        page_profile=profile,
    )
    solution = solve_layout_v5(parts, page_profile=profile, constraint_spec=spec)
    return _report_from_solution(solution, spec, profile)


def resolve_cartographer_layout(
    participants: Iterable[LayoutParticipantV4],
    purpose: str,
) -> Tuple[Any, Dict[str, Any]]:
    """Print profiles use v5 once. Screen purposes stay on v4 viewport."""
    parts = list(participants)
    if purpose not in _SOLVER_PROFILES:
        solution = solve_layout_v4(parts, page_profile="viewport")
        reason = "page_profile_absent" if not purpose else "profile_unsupported"
        return solution, not_evaluated(purpose, reason)
    spec = derive_layout_constraints(
        [
            {
                "id": part.id,
                "type": part.type,
                "optional": part.optional,
                "enabled": True,
            }
            for part in parts
        ],
        page_profile=purpose,
    )
    solution = solve_layout_v5(parts, page_profile=purpose, constraint_spec=spec)
    return solution, _report_from_solution(solution, spec, purpose)


def prepare_publication_document(
    document: Dict[str, Any],
    *,
    page_profile: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return the original document unless an explicit profile is evaluated.

    Identity on the default path is the byte-stability contract: callers must
    compile ``document`` itself when the report says ``not_evaluated``.
    """
    layout = document.get("layout") if isinstance(document, dict) else None
    components = layout.get("components") if isinstance(layout, dict) else None
    report = evaluate_constraint_layout(components, page_profile=page_profile)
    if report["constraint_layout"] != "evaluated" or not isinstance(document, dict):
        return document, report
    cloned = copy.deepcopy(document)
    cloned_layout = cloned.get("layout")
    if not isinstance(cloned_layout, dict):
        cloned_layout = {}
        cloned["layout"] = cloned_layout
    cloned_layout["constraint_layout"] = "evaluated"
    cloned_layout["page_profile"] = report["page_profile"]
    workbench = cloned.get("workbench") if isinstance(cloned.get("workbench"), dict) else {}
    locked = {
        str(item) for item in (workbench.get("lockedComponentIds") or [])
        if isinstance(item, str)
    }
    zones = report.get("placement_zones") or {
        item["id"]: item.get("zone") for item in report.get("placements") or []
    }
    suppressed = set(report.get("suppressed_ids") or [
        item["id"] for item in report.get("suppressed") or []
    ])
    keys = set(report.get("key_component_ids") or [])
    kept_locks: List[str] = []
    for comp in cloned_layout.get("components") or []:
        if not isinstance(comp, dict):
            continue
        cid = str(comp.get("id") or "")
        placement = comp.get("placement") if isinstance(comp.get("placement"), dict) else {}
        if placement.get("mode") == "floating" or cid in locked:
            if cid:
                kept_locks.append(cid)
            continue
        zone = zones.get(cid) or ""
        if zone in _ANCHORS:
            comp["position"] = zone
            if placement.get("mode") == "anchor":
                placement["anchor"] = zone
                comp["placement"] = placement
        if cid in suppressed and cid not in keys:
            comp["enabled"] = False
    if kept_locks:
        report = dict(report)
        report["user_lock_kept"] = kept_locks[:16]
    return cloned, report
