"""Bind capability resolution at dispatch (#1395).

``resolve_capabilities`` / ``plan_candidates_v8`` previously produced
advisory evidence only — ``dispatch_tool`` accepted any registered name.
This module is the first production caller of ``plan_candidates_v8``: before
ToolDispatchService runs, re-qualify the tool's declared capabilities and
refuse INELIGIBLE providers when an eligible alternative exists.

Kill-switch: ``GIS_CAPABILITY_DISPATCH_BIND`` (default ON). Fail-open on
missing caps / empty graph / unexpected errors so the chokepoint never
becomes a second planner outage.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CAPABILITY_DISPATCH_BIND_ENV = "GIS_CAPABILITY_DISPATCH_BIND"
CAPABILITY_INELIGIBLE_CODE = "CAPABILITY_INELIGIBLE"
CAPABILITY_INELIGIBLE_KEY = "capability_ineligible"


def _env_truthy(name: str, default: str) -> bool:
    raw = (os.environ.get(name) or default).strip().lower()
    return raw not in ("0", "false", "off", "no")


def capability_dispatch_bind_enabled() -> bool:
    """Default ON — advisory → binding at the Pi/dispatch chokepoint."""
    return _env_truthy(CAPABILITY_DISPATCH_BIND_ENV, "1")


@dataclass
class CapabilityDispatchDecision:
    allowed: bool = True
    code: str = ""
    reason: str = ""
    capability_id: str = ""
    tool_name: str = ""
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    excluded: List[Dict[str, Any]] = field(default_factory=list)

    def denial_text(self) -> str:
        alts = ", ".join(
            f"{a.get('kind')}:{a.get('id')}" for a in self.alternatives[:3]
        ) or "(none)"
        return (
            f"Tool '{self.tool_name}' is INELIGIBLE for capability "
            f"'{self.capability_id}': {self.reason or 'qualification failed'}. "
            f"Eligible alternatives: {alts}."
        )

    def to_details(self) -> Dict[str, Any]:
        return {
            "error": CAPABILITY_INELIGIBLE_KEY,
            "code": CAPABILITY_INELIGIBLE_CODE,
            "tool": self.tool_name[:128],
            "capability": self.capability_id[:128],
            "reason": (self.reason or "")[:240],
            "alternatives": self.alternatives[:4],
            "excluded": self.excluded[:4],
            "retryable": True,
            "adr": "V9-decision-chain/#1395",
        }


def _tool_capabilities(registry: Any, tool_name: str) -> List[str]:
    try:
        meta = registry.metadata(tool_name) or {}
    except Exception:  # noqa: BLE001
        return []
    caps = meta.get("capabilities") or ()
    out: List[str] = []
    for c in caps:
        s = str(c or "").strip()
        if s and s not in out:
            out.append(s[:128])
    return out[:8]


def _situation_from_optional(situation: Any = None):
    from app.services.gis_harness.qualification_v8 import QualificationContext

    if situation is None:
        return QualificationContext()
    if isinstance(situation, QualificationContext):
        return situation
    if isinstance(situation, dict):
        allowed = {
            "task_hint", "geometry_kinds", "crs", "crs_is_geographic",
            "field_names", "feature_count", "raster_bands",
            "resolution_m_per_px", "sensor", "temporal_inputs", "data_bytes",
            "map_layer_count", "gpu_available", "vram_bytes", "memory_bytes",
            "max_latency_class", "owner_scope_key", "offline", "auth_tier",
            "budget_cost_class", "quality_gate", "blocking_issue_codes",
            "dependency_available", "credentials_present",
        }
        kwargs = {k: situation[k] for k in allowed if k in situation}
        try:
            return QualificationContext(**kwargs)
        except Exception:  # noqa: BLE001
            return QualificationContext()
    return QualificationContext()


def check_tool_capability_at_dispatch(
    tool_name: str,
    *,
    registry: Any,
    session_id: str = "",
    situation: Any = None,
) -> Optional[CapabilityDispatchDecision]:
    """Return a denial decision when the tool is INELIGIBLE; else ``None``.

    Production caller of ``plan_candidates_v8`` (#1395). When the dispatched
    tool is excluded for a declared capability *and* at least one eligible
    candidate remains, refuse so the LLM can pick the qualified provider.
    """
    if not capability_dispatch_bind_enabled():
        return None
    name = str(tool_name or "").strip()
    if not name or registry is None:
        return None
    caps = _tool_capabilities(registry, name)
    if not caps:
        return None

    try:
        from app.services.gis_harness.candidate_planner_v8 import plan_candidates_v8
    except Exception:  # noqa: BLE001 — planner unavailable → fail-open
        return None

    ctx = _situation_from_optional(situation)
    sid = str(session_id or "")[:64]

    for cap in caps:
        try:
            plan = plan_candidates_v8(cap, ctx, session_id=sid)
        except Exception:  # noqa: BLE001 — per-cap fail-open
            continue

        excluded_hit = None
        for ex in plan.excluded or []:
            if (
                str(ex.get("kind") or "") == "tool"
                and str(ex.get("id") or "") == name
            ):
                excluded_hit = ex
                break
        if excluded_hit is None:
            continue

        # Only refuse when a better (eligible) provider exists — otherwise
        # governor / registry gates remain the honesty path.
        alts = [
            {
                "kind": c.kind,
                "id": c.id,
                "score": round(float(c.score), 3),
                "status": str(c.qualification.status),
            }
            for c in (plan.candidates or [])[:4]
        ]
        if not alts:
            continue

        qual = excluded_hit.get("qualification") or {}
        reasons = qual.get("reasons") or []
        reason_txt = ""
        if reasons and isinstance(reasons[0], dict):
            reason_txt = (
                f"{reasons[0].get('check', '')}: "
                f"{reasons[0].get('observed', '')} "
                f"(expected {reasons[0].get('expected', '')})"
            ).strip()
        elif isinstance(qual.get("status"), str):
            reason_txt = qual["status"]

        return CapabilityDispatchDecision(
            allowed=False,
            code=CAPABILITY_INELIGIBLE_CODE,
            reason=reason_txt[:240],
            capability_id=cap,
            tool_name=name,
            alternatives=alts,
            excluded=[{
                "kind": "tool",
                "id": name,
                "qualification": qual,
            }],
        )
    return None


__all__ = [
    "CAPABILITY_DISPATCH_BIND_ENV",
    "CAPABILITY_INELIGIBLE_CODE",
    "CAPABILITY_INELIGIBLE_KEY",
    "CapabilityDispatchDecision",
    "capability_dispatch_bind_enabled",
    "check_tool_capability_at_dispatch",
]
