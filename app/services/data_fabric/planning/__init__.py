"""ads-v1 acquisition planning (DS3, ADR-0173).

Compiles a data request into the frozen D2 ``AcquisitionPlan``: ordered steps
(source select → version pin → bbox clip → time filter → field projection →
aggregate pushdown → pagination → sampling), a cost estimate aligned with the
federated costing components, budget-aware plan choice with **downgrade
suggestions** (never a hard failure), a deterministic explain text, and
replay (same plan + same version pin → same result hash).

Pushdown discipline: a step is pushed to the source only when the source's
declared capability says so (`pushdown` in the registry / AdapterSpec flags);
filters that cannot be pushed stay in the plan with ``pushed_down=false`` so
the local cost is visible instead of silently optimised away.
"""
from app.services.data_fabric.planning.compiler import (
    PlanCompiler,
    PlanRequest,
    choose_plan,
    compile_plan,
    facts_from_card,
    facts_from_source_definition,
)
from app.services.data_fabric.planning.cost_model import estimate_cost
from app.services.data_fabric.planning.explain import explain_plan
from app.services.data_fabric.planning.replay import replay, result_hash

__all__ = [
    "PlanCompiler",
    "PlanRequest",
    "compile_plan",
    "choose_plan",
    "facts_from_card",
    "facts_from_source_definition",
    "estimate_cost",
    "explain_plan",
    "replay",
    "result_hash",
]
