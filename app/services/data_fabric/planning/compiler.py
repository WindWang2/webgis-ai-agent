"""Acquisition plan compiler (DS3, ADR-0173) — request → D2 AcquisitionPlan.

Steps are emitted in execution order; each step records whether it was
pushed down to the source (per the declared pushdown capabilities) or will
run locally after fetch — invisible local cost is forbidden.

``choose_plan`` picks the cheapest candidate under budget; when none fits it
returns the best plan plus concrete **downgrade suggestions** (aggregate /
sample / shrink bbox) instead of failing — the caller decides.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.services.data_fabric.acquisition_limits import effective_feature_limit
from app.services.data_fabric.contracts import (
    AcquisitionBudget,
    AcquisitionPlan,
    AcquisitionStep,
)
from app.services.data_fabric.planning.cost_model import estimate_cost


class PlanRequest(BaseModel):
    """What the caller wants (filters carried by the acquisition request)."""

    model_config = ConfigDict(extra="allow")

    dataset_key: str                       # "<source_id>/<dataset_id>"
    bbox: Optional[List[float]] = None
    time_range: Optional[List[str]] = None
    columns: Optional[List[str]] = None
    aggregate: Optional[str] = None        # e.g. "count" / "mean:value"
    group_by: Optional[str] = None
    sample_rate: float = 1.0               # 1.0 = no sampling
    limit: Optional[int] = None            # max rows wanted
    version: str = "latest"                # "latest" | pinned token
    budget: Optional[AcquisitionBudget] = None


class _SourceFacts(BaseModel):
    """The declared facts the compiler needs about one candidate source."""

    model_config = ConfigDict(extra="allow")

    source_id: str
    source_name: str = ""
    protocol: str
    endpoint: str = ""
    title: str = ""
    description: str = ""
    fields: List[str] = Field(default_factory=list)
    data_type: str = "vector"
    feature_count: Optional[int] = None
    bbox: Optional[List[float]] = None
    temporal_start: Optional[str] = None
    temporal_end: Optional[str] = None
    license: str = "unknown"
    granularity: Optional[str] = None
    verified: bool = False
    local: bool = False
    requests_per_minute: Optional[int] = None
    pushdown: Dict[str, bool] = Field(default_factory=dict)

    @classmethod
    def from_card(cls, card: Any) -> "_SourceFacts":
        from app.services.data_fabric.retrieval.cards import DatasetCard

        assert isinstance(card, DatasetCard)
        return cls(
            source_id=card.source_id,
            source_name=card.source_name,
            protocol=card.protocol,
            title=card.title,
            description=card.description,
            fields=list(card.fields),
            data_type=card.data_type,
            bbox=card.bbox,
            temporal_start=card.temporal_start,
            temporal_end=card.temporal_end,
            license=card.license,
            granularity=card.granularity,
            verified=card.verified,
            local=card.local,
            requests_per_minute=card.requests_per_minute,
        )


class PlanCompiler:
    """Compiles plan candidates from declared source facts (no I/O)."""

    def compile(self, request: PlanRequest, facts: _SourceFacts) -> AcquisitionPlan:
        pd = facts.pushdown or {}
        steps: List[AcquisitionStep] = [
            AcquisitionStep(step_type="source_select", source_id=facts.source_id,
                            params={"endpoint": facts.endpoint, "protocol": facts.protocol})
        ]
        if request.version != "latest":
            steps.append(AcquisitionStep(step_type="version_pin", source_id=facts.source_id,
                                         params={"pin": request.version}))
        if request.bbox:
            steps.append(AcquisitionStep(
                step_type="bbox_clip", source_id=facts.source_id,
                params={"bbox": request.bbox, "pushed_down": bool(pd.get("bbox"))},
            ))
        if request.time_range:
            steps.append(AcquisitionStep(
                step_type="time_filter", source_id=facts.source_id,
                params={"range": request.time_range, "pushed_down": bool(pd.get("time_filter"))},
            ))
        if request.columns:
            steps.append(AcquisitionStep(
                step_type="field_projection", source_id=facts.source_id,
                params={"columns": request.columns, "pushed_down": bool(pd.get("projection"))},
            ))
        if request.aggregate:
            steps.append(AcquisitionStep(
                step_type="aggregate_pushdown", source_id=facts.source_id,
                params={"aggregate": request.aggregate, "group_by": request.group_by,
                        "pushed_down": bool(pd.get("aggregation"))},
            ))
        est_rows = self._estimate_rows(request, facts)
        if not request.aggregate and facts.requests_per_minute is not None and not facts.local \
                and est_rows > (request.limit or 0) and est_rows > 1000 and pd.get("pagination"):
            steps.append(AcquisitionStep(
                step_type="pagination", source_id=facts.source_id,
                params={"page_size": 1000, "expected_rows": est_rows},
            ))
        if 0.0 < request.sample_rate < 1.0:
            steps.append(AcquisitionStep(
                step_type="sampling", source_id=facts.source_id,
                params={"rate": request.sample_rate},
            ))

        cost = estimate_cost(
            feature_count=facts.feature_count,
            fields=len(request.columns or facts.fields) or 4,
            geometry_type=facts.data_type if facts.data_type in {"point", "table"} else "polygon"
            if facts.data_type == "vector" else facts.data_type,
            protocol=facts.protocol,
            request_bbox=request.bbox,
            coverage_bbox=facts.bbox,
            aggregation=bool(request.aggregate),
            limit=request.limit,
            local=facts.local,
            requests_per_minute=facts.requests_per_minute,
        )
        # viewport-aware cap for the inline carrier (A7 policy consumption)
        effective_cap = effective_feature_limit(50_000, viewport_features=request.limit)
        if cost.rows and cost.rows > effective_cap and not request.aggregate:
            cost.rows = effective_cap

        plan = AcquisitionPlan(
            plan_id=_plan_id(request, facts),
            dataset_key=request.dataset_key,
            version=request.version,
            steps=steps,
            cost_estimate=cost,
            budget=request.budget,
            explain="",  # filled by explain_plan (kept out of the diff-sensitive id)
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        plan.explain = _explain_text(plan, facts)
        return plan

    @staticmethod
    def _estimate_rows(request: PlanRequest, facts: _SourceFacts) -> int:
        base = facts.feature_count if facts.feature_count is not None else 10_000
        rows = int(base * (request.sample_rate if request.sample_rate else 1.0))
        if request.limit:
            rows = min(rows, request.limit)
        return rows


def _plan_id(request: PlanRequest, facts: _SourceFacts) -> str:
    """Deterministic plan id from the *structural* request + source (stable
    across replays; the created_at/explain do not participate)."""
    import hashlib
    import json as _json

    structural = {
        "dataset_key": request.dataset_key,
        "bbox": request.bbox,
        "time_range": request.time_range,
        "columns": request.columns,
        "aggregate": request.aggregate,
        "group_by": request.group_by,
        "sample_rate": request.sample_rate,
        "limit": request.limit,
        "version": request.version,
        "source_id": facts.source_id,
    }
    digest = hashlib.sha256(_json.dumps(structural, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    return f"plan-{digest}"


def _explain_text(plan: AcquisitionPlan, facts: _SourceFacts) -> str:
    pushed = [s.step_type for s in plan.steps if s.params.get("pushed_down")]
    local = [s.step_type for s in plan.steps if s.params.get("pushed_down") is False]
    parts = [
        f"计划 {plan.plan_id}：从 {facts.source_name or facts.source_id}（{facts.protocol}）"
        f"获取 {plan.dataset_key.split('/', 1)[-1]}",
    ]
    if pushed:
        parts.append(f"下推到源：{'、'.join(pushed)}")
    if local:
        parts.append(f"取回后本地处理：{'、'.join(local)}（源不支持该下推）")
    c = plan.cost_estimate
    parts.append(
        f"预计 {c.rows or 0} 行 / {c.bytes or 0} 字节 / {c.latency_ms or 0} ms"
        + (f" / {c.quota} 次请求" if c.quota else "")
    )
    return "；".join(parts) + "。"


# ── Budget-aware selection ───────────────────────────────────────────────────


def _within(plan: AcquisitionPlan, budget: Optional[AcquisitionBudget]) -> bool:
    if budget is None:
        return True
    c = plan.cost_estimate
    if budget.max_rows is not None and (c.rows or 0) > budget.max_rows:
        return False
    if budget.max_bytes is not None and (c.bytes or 0) > budget.max_bytes:
        return False
    if budget.max_ms is not None and (c.latency_ms or 0.0) > budget.max_ms:
        return False
    if budget.max_quota is not None and (c.quota or 0.0) > budget.max_quota:
        return False
    return True


def _budget_score(plan: AcquisitionPlan) -> float:
    c = plan.cost_estimate
    return (c.rows or 0) * 1.0 + (c.bytes or 0) * 0.001 + (c.latency_ms or 0.0)


def choose_plan(
    request: PlanRequest,
    facts: _SourceFacts,
    compiler: Optional[PlanCompiler] = None,
) -> Tuple[AcquisitionPlan, List[str]]:
    """Best plan under budget; over budget → degraded variants + suggestions.

    Returns ``(plan, suggestions)``. The returned plan is the cheapest of the
    candidates that fits; if nothing fits, the least-cost plan is returned
    with concrete suggestions — choosing to proceed (degraded) or stop is the
    caller's decision, the planner never lies by returning a plan that
    claims to fit.
    """
    compiler = compiler or PlanCompiler()

    candidates: List[AcquisitionPlan] = [compiler.compile(request, facts)]
    suggestions: List[str] = []

    if request.budget is not None:
        # degraded variant 1: aggregate instead of raw rows
        if not request.aggregate:
            agg_req = request.model_copy(update={"aggregate": "count", "group_by": request.group_by or "district"})
            candidates.append(compiler.compile(agg_req, facts))
        # degraded variant 2: sample down
        if request.sample_rate >= 1.0:
            candidates.append(compiler.compile(
                request.model_copy(update={"sample_rate": 0.25}), facts))
        # degraded variant 3: clip bbox to the coverage centre window (1/4 area)
        if request.bbox and len(request.bbox) == 4:
            x0, y0, x1, y1 = request.bbox
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            clipped = [cx - (x1 - x0) / 4.0, cy - (y1 - y0) / 4.0, cx + (x1 - x0) / 4.0, cy + (y1 - y0) / 4.0]
            candidates.append(compiler.compile(
                request.model_copy(update={"bbox": clipped}), facts))

    fitting = [p for p in candidates if _within(p, request.budget)]
    if fitting:
        best = min(fitting, key=_budget_score)
        if best is not candidates[0]:
            suggestions = _suggestions_for(best)
        return best, suggestions

    best = min(candidates, key=_budget_score)
    suggestions = _suggestions_for(best)
    return best, suggestions


def _suggestions_for(plan: AcquisitionPlan) -> List[str]:
    steps = {s.step_type for s in plan.steps}
    out: List[str] = []
    if "aggregate_pushdown" in steps:
        out.append("改为聚合粒度（count/分组）以减少行数")
    if "sampling" in steps:
        out.append("按 25% 抽样降低载荷")
    if "bbox_clip" in steps:
        out.append("缩小空间范围（约 1/4 面积）")
    if not out:
        out.append("提高预算或降低 limit")
    return out


def compile_plan(request: PlanRequest, facts: _SourceFacts) -> AcquisitionPlan:
    return PlanCompiler().compile(request, facts)


def facts_from_card(card: Any) -> _SourceFacts:
    return _SourceFacts.from_card(card)


def facts_from_source_definition(source_id: str, dataset_id: str) -> _SourceFacts:
    """Build facts straight from the registry (declared facts only)."""
    from app.services.data_fabric.source_registry import source_registry_service

    s = source_registry_service.get(source_id)
    decl = next((d for d in s.datasets if d.dataset_id == dataset_id), None)
    return _SourceFacts(
        source_id=s.source_id,
        source_name=s.name,
        protocol=s.protocol,
        endpoint=s.endpoint,
        title=(decl.title if decl else s.name),
        description=(decl.description if decl else s.description),
        fields=[f.get("name") for f in (decl.fields if decl else []) if isinstance(f, dict) and f.get("name")],
        data_type=(decl.data_type if decl else "vector"),
        feature_count=None,  # declared facts never invent a row count
        bbox=(decl.bbox if decl else None),
        temporal_start=s.temporal_coverage.start,
        temporal_end=s.temporal_coverage.end,
        license=(decl.license if decl and decl.license else s.license),
        granularity=(decl.granularity if decl else None),
        verified=s.verified,
        local=s.protocol in {"local_file", "geopackage"} or s.source_id.startswith("local_"),
        requests_per_minute=s.quota.requests_per_minute,
        pushdown=dict(
            bbox=s.pushdown.bbox,
            cql=s.pushdown.cql,
            aggregation=s.pushdown.aggregation,
            time_filter=s.pushdown.time_filter,
            projection=s.pushdown.projection,
            pagination=s.pushdown.pagination,
        ),
    )


__all__ = [
    "PlanRequest",
    "PlanCompiler",
    "_SourceFacts",
    "compile_plan",
    "choose_plan",
    "facts_from_card",
    "facts_from_source_definition",
]
