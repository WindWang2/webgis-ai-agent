"""Standards QA — post-compile report + pre-compile gate (ADR-0200).

This module composes the graph, profile and checkers into two bounded,
deterministic artifacts:

- :func:`evaluate_standards_qa` — the post-compile QA report. A *projection*,
  explicitly not a second map verdict: it travels alongside (never instead of)
  the CartographyReport from semantic_checks.
- :func:`standards_precompile_gate` — the compile gate. Blocks only on
  error-severity violations, which require an explicitly declared (strict)
  profile; inferred profiles cap errors at warning so legacy maps never gain
  blocking failures (back-compat guard, ADR-0200 D4).

Same input → byte-identical dict output (deterministic replay contract).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.standards.evaluate import (
    StandardsContext,
    _CHECKERS,
    _Outcome,
)
from app.lib.cartography.standards.graph import (
    RuleGraph,
    StandardsViolation,
)
from app.lib.cartography.standards.pack import (
    StandardsPack,
    StandardsRegistry,
)
from app.lib.cartography.standards.packs import get_standards_registry
from app.lib.cartography.standards.profile import (
    ProfileSpec,
    ProfileSpecError,
    resolve_profile,
)
from app.lib.cartography.standards.rule import StandardsRuleError

#: Contract version of the report shape itself (bump on shape change).
STANDARDS_QA_CONTRACT_VERSION = "1.0"

_STATUS_ORDER = {"error": 0, "warning": 1, "info": 2}
_PI_CARD_VIOLATIONS = 5
_PI_CARD_MESSAGE_CHARS = 200

_OBLIGATION_STATUSES = ("satisfied", "violated", "not_evaluated", "not_applicable")


@dataclass(frozen=True)
class StandardsQAReport:
    """Immutable QA result; ``to_dict`` is the stable serialized face."""

    enabled: bool
    status: str
    pack: StandardsPack
    profile: ProfileSpec
    obligations: Tuple[Dict[str, Any], ...]
    violations: Tuple[StandardsViolation, ...]
    counters: Dict[str, int]
    pi_card: Dict[str, Any]
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": "standards_qa",
            "contract_version": STANDARDS_QA_CONTRACT_VERSION,
            "enabled": self.enabled,
            "status": self.status,
            "pack": {
                "pack_id": self.pack.pack_id,
                "version": self.pack.version,
                "fingerprint": self.pack.fingerprint,
            },
            "profile": self.profile.to_dict(),
            "obligations": [dict(o) for o in self.obligations],
            "violations": [v.to_dict() for v in self.violations],
            "counters": dict(self.counters),
            "pi_card": self.pi_card,
            "note": self.note,
        }


def evaluate_standards_qa(
    mapspec: Dict[str, Any],
    source_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    profile: Optional[ProfileSpec] = None,
    strict: Optional[bool] = None,
    pack: Optional[StandardsPack] = None,
    pack_id: str = "core",
    pack_version: Optional[str] = None,
    registry: Optional[StandardsRegistry] = None,
    enabled: bool = True,
) -> StandardsQAReport:
    """Evaluate versioned standards obligations against a MapSpec candidate.

    ``profile=None`` infers from existing MapSpec vocabulary (non-strict);
    explicit axes make the profile strict. ``strict=True`` on an inferred
    profile is rejected — strictness must be a declared decision.
    """
    resolved_profile = _resolve_profile_arg(profile, strict, mapspec)
    if not enabled:
        pack_ = pack if pack is not None else _resolve_pack(
            registry, pack_id, pack_version)
        return StandardsQAReport(
            enabled=False, status="disabled", pack=pack_, profile=resolved_profile,
            obligations=(), violations=(),
            counters={s: 0 for s in _OBLIGATION_STATUSES},
            pi_card=_pi_card(StandardsQAReport(
                enabled=False, status="disabled", pack=pack_,
                profile=resolved_profile, obligations=(), violations=(),
                counters={s: 0 for s in _OBLIGATION_STATUSES}, pi_card={},
            )),
            note="standards qa disabled by caller",
        )
    pack_ = pack if pack is not None else _resolve_pack(registry, pack_id, pack_version)
    graph = pack_.graph()
    ctx = StandardsContext(mapspec, source_profiles)
    return _evaluate(graph, pack_, resolved_profile, ctx)


def standards_precompile_gate(
    mapspec: Dict[str, Any],
    source_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    profile: Optional[ProfileSpec] = None,
    strict: Optional[bool] = None,
    pack: Optional[StandardsPack] = None,
    pack_id: str = "core",
    pack_version: Optional[str] = None,
    registry: Optional[StandardsRegistry] = None,
    enabled: bool = True,
) -> Dict[str, Any]:
    """Pre-compile gate: ``block`` only on error-severity violations."""
    report = evaluate_standards_qa(
        mapspec, source_profiles, profile=profile, strict=strict, pack=pack,
        pack_id=pack_id, pack_version=pack_version, registry=registry,
        enabled=enabled,
    )
    blocking = [v for v in report.violations if v.severity == "error"]
    if not report.enabled:
        verdict = "disabled"
    elif blocking:
        verdict = "block"
    else:
        verdict = "allow"
    return {
        "stage": "standards_precompile_gate",
        "verdict": verdict,
        "blocking": [v.to_dict() for v in blocking],
        "counters": dict(report.counters),
        "report": report.to_dict(),
    }


# ── internals ─────────────────────────────────────────────────────────────


def _resolve_profile_arg(
    profile: Optional[ProfileSpec],
    strict: Optional[bool],
    mapspec: Dict[str, Any],
) -> ProfileSpec:
    if profile is not None:
        if not isinstance(profile, ProfileSpec):
            raise ProfileSpecError("profile 必须是 ProfileSpec")
        if strict is True and not profile.strict:
            raise ProfileSpecError(
                "推断 profile 不得强制 strict：请显式声明 purpose/audience/medium")
        return profile
    resolved = resolve_profile(mapspec=mapspec)
    if strict is True:
        raise ProfileSpecError(
            "strict=True 需要显式 profile（ inferred profile 恒非严格，back-compat）")
    if strict is not None and strict is not False:
        raise ProfileSpecError("strict 必须是 bool 或 None")
    return resolved


def _resolve_pack(
    registry: Optional[StandardsRegistry],
    pack_id: str,
    pack_version: Optional[str],
) -> StandardsPack:
    reg = registry if registry is not None else get_standards_registry()
    resolved = reg.resolve(pack_id, pack_version)
    assert resolved is not None  # required=True default
    return resolved


def _effective_severity(rule, profile: ProfileSpec) -> str:
    """Non-strict profiles cap errors at warning (legacy back-compat)."""
    if not profile.strict and rule.severity == "error":
        return "warning"
    return rule.severity


def _evaluate(
    graph: RuleGraph,
    pack: StandardsPack,
    profile: ProfileSpec,
    ctx: StandardsContext,
) -> StandardsQAReport:
    obligations: List[Dict[str, Any]] = []
    violations: List[StandardsViolation] = []
    status_by_rule: Dict[str, str] = {}
    order = graph.evaluation_order()
    for rule in order:
        record: Dict[str, Any] = rule.explain(
            effective_severity=_effective_severity(rule, profile))
        if not rule.applies_when.matches(
            purpose=profile.purpose,
            audience=profile.audience,
            medium=profile.medium,
            data_semantics=ctx.data_semantics,
        ):
            record["status"] = "not_applicable"
            record["reason"] = {"axis": rule.applies_when.explain()}
            status_by_rule[rule.rule_id] = "not_applicable"
            obligations.append(record)
            continue
        blocked_by = sorted(
            dep for dep in rule.requires
            if status_by_rule.get(dep) in ("violated", "not_evaluated")
        )
        vacuous = sorted(
            dep for dep in rule.requires
            if status_by_rule.get(dep) == "not_applicable"
        )
        if blocked_by or vacuous:
            status = "not_evaluated" if blocked_by else "not_applicable"
            record["status"] = status
            record["reason"] = {
                "blocked_by": blocked_by, "vacuous_depends": vacuous,
            }
            status_by_rule[rule.rule_id] = status
            obligations.append(record)
            continue
        checker = _CHECKERS.get(rule.kind)
        if checker is None:
            raise StandardsRuleError(
                f"kind {rule.kind!r} 无 checker（词表与实现漂移，fail-closed）")
        outcome: _Outcome = checker(
            rule, ctx, profile, _effective_severity(rule, profile))
        record["status"] = outcome.status
        reason: Dict[str, Any] = {}
        if outcome.note:
            reason["note"] = outcome.note
        if reason:
            record["reason"] = reason
        status_by_rule[rule.rule_id] = outcome.status
        obligations.append(record)
        violations.extend(outcome.violations)
    applied = frozenset(
        rid for rid, status in status_by_rule.items() if status == "violated")
    violations.extend(graph.conflict_violations(
        applied=applied,
        # RULE_CONFLICT obeys the same profile cap as per-rule severities:
        # inferred (legacy) profiles must never gain blocking failures.
        severity="error" if profile.strict else "warning",
    ))
    # deterministic ordering: graph order for obligations, (severity, graph
    # order, layer, source) for violations — stable across runs
    order_index = {r.rule_id: i for i, r in enumerate(order)}
    violations.sort(key=lambda v: (
        _STATUS_ORDER.get(v.severity, 3),
        order_index.get(v.rule_id, len(order_index)),
        v.layer_id, v.source_id,
    ))
    counters = {s: 0 for s in _OBLIGATION_STATUSES}
    for record in obligations:
        counters[record["status"]] = counters.get(record["status"], 0) + 1
    if any(record["status"] == "violated" for record in obligations):
        status = "violations"
    elif counters["not_evaluated"] == len(obligations) and obligations:
        status = "not_evaluated"
    else:
        status = "pass"
    report = StandardsQAReport(
        enabled=True, status=status, pack=pack, profile=profile,
        obligations=tuple(obligations), violations=tuple(violations),
        counters=counters, pi_card={},
    )
    return dataclasses.replace(report, pi_card=_pi_card(report))


def _pi_card(report: StandardsQAReport) -> Dict[str, Any]:
    """Bounded harness-card projection (ADR-0200 D10).

    Fixed-shape, size-capped by construction; never carries layer payloads.
    """
    top = sorted(
        report.violations,
        key=lambda v: (
            _STATUS_ORDER.get(v.severity, 3), v.rule_id, v.layer_id),
    )[:_PI_CARD_VIOLATIONS]
    return {
        "card": "standards_qa",
        "pack": f"{report.pack.pack_id}@{report.pack.version}",
        "profile": {
            "purpose": report.profile.purpose,
            "audience": report.profile.audience,
            "medium": report.profile.medium,
            "strict": report.profile.strict,
        },
        "status": report.status,
        "counters": dict(report.counters),
        "violation_count": len(report.violations),
        "top_violations": [
            {
                "rule_id": v.rule_id,
                "severity": v.severity,
                "message": v.message[:_PI_CARD_MESSAGE_CHARS],
                "layer_id": v.layer_id,
            }
            for v in top
        ],
        "bounds": {
            "top_violations": _PI_CARD_VIOLATIONS,
            "message_chars": _PI_CARD_MESSAGE_CHARS,
        },
        "pack_fingerprint": report.pack.fingerprint,
    }


__all__ = [
    "STANDARDS_QA_CONTRACT_VERSION",
    "StandardsQAReport",
    "evaluate_standards_qa",
    "standards_precompile_gate",
]
