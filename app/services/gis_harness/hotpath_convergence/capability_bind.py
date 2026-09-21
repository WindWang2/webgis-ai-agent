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
#: dispatch bind 决策面的 policy 版本（ADR-0213：拒绝规则演进时升版，
#: denial decision_id 随之变化 —— 漂移可归因到规则版本）。
CAPABILITY_BIND_POLICY_VERSION = "capability_dispatch_bind.v1"


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

    ADR-0204 D3：本函数保留为兼容包装（#1477 语义与测试不变）；带证据的
    完整求值走 :func:`bind_tool_capability`。
    """
    outcome = bind_tool_capability(
        tool_name, registry=registry, session_id=session_id, situation=situation)
    return outcome.decision if outcome is not None else None


@dataclass
class CapabilityBindOutcome:
    """一次 bind 求值的完整产出：拒绝决定（若有）+ 双面证据（ADR-0204 D4）。

    ``decision is None`` = allowed（或 skipped —— 后者由外层 ``None`` 区分，
    本对象只在工具声明了 capability 且闸开时产出）。evidence 只含
    id/code/score 级事实，无参数、无凭证、无 payload。
    """

    decision: Optional[CapabilityDispatchDecision]
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return self.decision is not None


def bind_tool_capability(
    tool_name: str,
    *,
    registry: Any,
    session_id: str = "",
    situation: Any = None,
) -> Optional[CapabilityBindOutcome]:
    """单次求值：dispatch 期 capability 绑定 + bounded 证据（ADR-0204 D3/D4）。

    返回 ``None`` = skipped（闸关 / 无工具名 / registry 缺席 / 工具未声明
    capability —— 与既有语义一致，零成本早退）。allowed 与 refused 均携带
    evidence；拒绝时附 :class:`CapabilityDispatchDecision`。
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

    #: 允许路径的证据基座（找到即记 rank/score，不重复求值）。
    allowed_hit: Optional[Dict[str, Any]] = None

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
            # allowed 面：记录该工具在本 capability 候选序中的位置（首个
            # 命中的 capability 披露 rank/score/最近替代，有界）。
            if allowed_hit is None:
                ranked = plan.candidates or []
                for rank, c in enumerate(ranked):
                    if c.kind == "tool" and c.id == name:
                        # 披露「最近更优替代 + 下一个竞争者」—— 解释
                        # 「为什么选它 / 更好的是谁」。
                        near = ranked[max(0, rank - 1):rank] + \
                            ranked[rank + 1:rank + 2]
                        allowed_hit = {
                            "capability": cap,
                            "rank": rank,
                            "score": round(float(c.score), 3),
                            "status": str(c.qualification.status),
                            "alternatives": [
                                {"kind": n.kind, "id": n.id,
                                 "score": round(float(n.score), 3),
                                 "status": str(n.qualification.status)}
                                for n in near
                            ],
                        }
                        break
            continue

        # Only refuse when a better (eligible) *dispatchable* provider exists
        # — alternatives feed the LLM's retry, so they must be tool-kind
        # (model candidates are not dispatchable targets); otherwise governor
        # / registry gates remain the honesty path. (Review RB-P2: #1477 原始
        # 语义不区分 kind —— 此处在 situation 接线前修掉，避免带 situation
        # 后出现「拒绝理由只列 model」的不可执行替代。)
        alts = [
            {
                "kind": c.kind,
                "id": c.id,
                "score": round(float(c.score), 3),
                "status": str(c.qualification.status),
            }
            for c in (plan.candidates or [])
            if str(getattr(c, "kind", "")) == "tool"
        ][:4]
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

        decision = CapabilityDispatchDecision(
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
        return CapabilityBindOutcome(
            decision=decision,
            evidence={
                "tool": name[:128],
                "action": "refused",
                "capabilities": list(caps),
                "capability": cap[:128],
                "status": str((qual or {}).get("status") or ""),
                "reason": reason_txt[:240],
                "code": CAPABILITY_INELIGIBLE_CODE,
                "alternatives": alts[:2],
            },
        )

    evidence = {
        "tool": name[:128],
        "action": "allowed",
        "capabilities": list(caps),
        "code": "",
        "reason": "",
        "alternatives": [],
    }
    if allowed_hit is not None:
        evidence["rank_capability"] = str(allowed_hit.get("capability", ""))[:128]
        evidence["rank"] = int(allowed_hit.get("rank", -1))
        evidence["score"] = allowed_hit.get("score")
        evidence["status"] = str(allowed_hit.get("status") or "")
        evidence["alternatives"] = list(allowed_hit.get("alternatives") or [])
    return CapabilityBindOutcome(decision=None, evidence=evidence)


__all__ = [
    "CAPABILITY_BIND_POLICY_VERSION",
    "CAPABILITY_DISPATCH_BIND_ENV",
    "CAPABILITY_INELIGIBLE_CODE",
    "CAPABILITY_INELIGIBLE_KEY",
    "CapabilityDispatchDecision",
    "CapabilityBindOutcome",
    "capability_dispatch_bind_enabled",
    "check_tool_capability_at_dispatch",
    "bind_tool_capability",
]
