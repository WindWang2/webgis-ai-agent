"""Declarative cartographic standards — rule graph, packs, deterministic QA.

Layer contract (ADR-0200): rules are DATA, measurements are the existing
engines. This package never re-implements classification/layout/CVD math and
never issues a second top-level map verdict; it derives *obligations* from
purpose × audience × medium, evaluates them deterministically against the
credential-safe cartographic projection, and routes safe fixes back to the
existing AUTO_SAFE mutation / component-autofill vocabularies.

Public seams:
- :data:`app.lib.cartography.standards.rule` — vocabularies + CartographicRule
- :data:`app.lib.cartography.standards.graph` — RuleGraph (order/conflicts)
- :data:`app.lib.cartography.standards.pack` — StandardsPack + registry
- :data:`app.lib.cartography.standards.profile` — ProfileSpec resolution
- :data:`app.lib.cartography.standards.qa` — evaluate_standards_qa / gate
"""
from __future__ import annotations

from app.lib.cartography.standards.rule import (
    DATA_SEMANTICS,
    FIX_ROUTES,
    MAP_AUDIENCES,
    MAP_MEDIUMS,
    MAP_PURPOSES,
    QUALITY_LOOP_OPERATIONS,
    RULE_KINDS,
    RULE_SEVERITIES,
    CartographicRule,
    RuleApplicability,
    StandardsRuleError,
)
from app.lib.cartography.standards.graph import RuleGraph, RuleGraphError
from app.lib.cartography.standards.pack import (
    StandardsPack,
    StandardsPackError,
    StandardsRegistry,
)
from app.lib.cartography.standards.profile import (
    MAP_PROFILE_AXES,
    ProfileSpec,
    ProfileSpecError,
    infer_profile,
    resolve_profile,
)
from app.lib.cartography.standards.qa import (
    STANDARDS_QA_CONTRACT_VERSION,
    StandardsQAReport,
    evaluate_standards_qa,
    standards_precompile_gate,
)

__all__ = [
    "DATA_SEMANTICS",
    "FIX_ROUTES",
    "MAP_AUDIENCES",
    "MAP_MEDIUMS",
    "MAP_PROFILE_AXES",
    "MAP_PURPOSES",
    "QUALITY_LOOP_OPERATIONS",
    "RULE_KINDS",
    "RULE_SEVERITIES",
    "STANDARDS_QA_CONTRACT_VERSION",
    "CartographicRule",
    "ProfileSpec",
    "ProfileSpecError",
    "RuleApplicability",
    "RuleGraph",
    "RuleGraphError",
    "StandardsPack",
    "StandardsPackError",
    "StandardsQAReport",
    "StandardsRegistry",
    "StandardsRuleError",
    "evaluate_standards_qa",
    "infer_profile",
    "resolve_profile",
    "standards_precompile_gate",
]
