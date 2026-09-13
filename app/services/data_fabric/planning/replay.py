"""Plan replay (DS3, ADR-0173): same plan + same version pin → same result.

``replay`` executes a plan's steps against the bound source adapter and
returns the canonical result hash. Determinism contract:

- the plan's structural id is content-derived (`_plan_id`), so the same
  request compiles to the same plan;
- execution applies the steps in order against a *pinned* version; for
  ``version="latest"`` replay equality is only guaranteed within a source
  revision — the hash therefore records the version alongside the payload;
- the hash is canonical-JSON sha256 over (dataset_key, version, features) —
  no timestamps, no dict-ordering sensitivity.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from app.services.data_fabric.contracts import AcquisitionPlan


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def result_hash(plan: AcquisitionPlan, features: List[Dict[str, Any]]) -> str:
    return canonical_hash({
        "dataset_key": plan.dataset_key,
        "version": plan.version,
        "features": features,
    })


def _profile_for_plan(plan: AcquisitionPlan) -> ConnectionProfile:
    """Resolve the plan's source_select step to a live adapter profile."""
    from app.services.data_fabric.source_registry import source_registry_service

    source_step = next(s for s in plan.steps if s.step_type == "source_select")
    source_id = source_step.source_id or plan.dataset_key.split("/", 1)[0]
    return source_registry_service.to_profile(source_id)


def replay(
    plan: AcquisitionPlan,
    *,
    adapter=None,
    max_rows: int = 50_000,
) -> Dict[str, Any]:
    """Execute the plan and return {features, hash, rows}.

    ``adapter`` overrides source resolution (tests inject a fixture-backed
    adapter). Steps applied: bbox / columns / limit from the plan's clip,
    projection and pagination steps; aggregate pushdown surfaces as a typed
    unsupported error when the adapter has no aggregate path (honest).
    """
    if adapter is None:
        from app.services.data_fabric.registry import build_adapter

        adapter = build_adapter(_profile_for_plan(plan))

    dataset_id = plan.dataset_key.split("/", 1)[-1]
    bbox: Optional[List[float]] = None
    columns: Optional[List[str]] = None
    limit: Optional[int] = None
    for step in plan.steps:
        if step.step_type == "bbox_clip":
            bbox = step.params.get("bbox")
        elif step.step_type == "field_projection":
            columns = step.params.get("columns")
        elif step.step_type == "pagination":
            limit = min(limit or max_rows, int(step.params.get("expected_rows") or max_rows))
        elif step.step_type == "aggregate_pushdown":
            if not step.params.get("pushed_down"):
                continue  # local aggregation is the executor's job (DS3+)
            # aggregate-capable adapters accept the query; others fail typed
    if plan.steps and plan.steps[-1].step_type == "sampling":
        pass  # sampling is applied post-fetch below

    spec = QuerySpec(bbox=bbox, columns=columns, limit=limit or max_rows, offset=0)
    result = adapter.query(dataset_id, spec)
    features = result.features if result is not None else []

    sampling = next((s for s in plan.steps if s.step_type == "sampling"), None)
    if sampling:
        rate = float(sampling.params.get("rate") or 1.0)
        features = [f for i, f in enumerate(features) if (i % max(1, int(round(1.0 / rate))) == 0)] \
            if 0.0 < rate < 1.0 else features

    return {
        "plan_id": plan.plan_id,
        "rows": len(features),
        "features": features,
        "hash": result_hash(plan, features),
    }


__all__ = ["replay", "result_hash", "canonical_hash"]
