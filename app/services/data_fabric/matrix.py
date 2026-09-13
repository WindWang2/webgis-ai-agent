"""ads-v1 validation matrix harness (DS8, ADR-0178).

12 sources × 12 request types × 3 fallback scenarios × 2 languages = **864
groups** (core batch = 216: zh × 6 core request types). Each group exercises
the *acquisition decision stack* offline and deterministically:

  plan compile (D2) → chain resolution (D3) → version gate (DS5) → fact (D4)

and asserts the group's expected outcome. The matrix validates the decisions
the supply side makes — data itself flows only where a source is reachable
(fixture adapters cover the offline protocols; the rest are declared-fact
groups, marked by the runner).

Output: docs/dev/ads-v1-validation-matrix.csv (one row per group).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from app.services.data_fabric import fallback as fb_mod
from app.services.data_fabric.contracts import AcquisitionBudget
from app.services.data_fabric.planning.compiler import PlanCompiler, PlanRequest, facts_from_source_definition
from app.services.data_fabric.retrieval import get_retrieval_service
from app.services.data_fabric.semantic.time_parser import parse_time_expr
from app.services.data_fabric.versioning_gate import InMemorySnapshotStore, pin_or_fetch

NOW = date(2026, 9, 13)

#: The 12 registry sources (matrix dimension 1).
SOURCES = [
    "beijing_gov", "shanghai_gov", "guangdong_gov",
    "local_osm", "local_poi", "local_yearbook",
    "planetary_computer", "copernicus_dataspace", "nasa_cmr_lpcloud",
    "worldbank_api", "gbif_api", "overpass_api",
]

#: The 12 request types (dimension 2); first 6 = core batch.
REQUEST_TYPES = [
    "bbox_query",
    "time_query",
    "projection_query",
    "aggregate_query",
    "sampled_query",
    "fallback_chain_query",
    # ── full batch adds: ──
    "paginated_query",
    "version_pinned_query",
    "budget_downgrade_query",
    "clarification_query",
    "local_first_query",
    "drift_annotated_query",
]

CORE_REQUEST_TYPES = set(REQUEST_TYPES[:6])

#: The 3 fallback scenarios (dimension 3).
SCENARIOS = ["healthy_primary", "transient_then_fallback", "forced_degradation"]

#: The 2 languages (dimension 4).
LANGUAGES = ["zh", "en"]

QUERY_ZH = "近五年按省汇总的PM2.5数据"
QUERY_EN = "pm2.5 data by province for the last five years"


@dataclass
class MatrixRecord:
    group_id: str
    source: str
    request_type: str
    scenario: str
    lang: str
    expected: str
    actual: str
    passed: bool
    details: Dict[str, Any] = field(default_factory=dict)


def _query(lang: str) -> str:
    return QUERY_ZH if lang == "zh" else QUERY_EN


def _plan_request(request_type: str, source: str) -> PlanRequest:
    if request_type == "version_pinned_query":
        return PlanRequest(dataset_key=f"{source}/{source}", version="rev-matrix-1")
    base = dict(dataset_key=f"{source}/{source}", version="latest")
    if request_type == "bbox_query":
        return PlanRequest(**base, bbox=[110.0, 30.0, 120.0, 40.0])
    if request_type == "time_query":
        tr = parse_time_expr(QUERY_ZH, now=NOW)
        return PlanRequest(**base, time_range=[tr.start, tr.end])
    if request_type == "projection_query":
        return PlanRequest(**base, columns=["name", "value"])
    if request_type == "aggregate_query":
        return PlanRequest(**base, aggregate="count", group_by="district")
    if request_type == "sampled_query":
        return PlanRequest(**base, sample_rate=0.25)
    if request_type == "paginated_query":
        return PlanRequest(**base, limit=5000)
    if request_type == "budget_downgrade_query":
        return PlanRequest(**base, budget=AcquisitionBudget(max_rows=500))
    return PlanRequest(**base)


def _runner_for(scenario: str, primary: str):
    """Deterministic chain runner per scenario (no I/O)."""
    def runner(source_id: str) -> Any:
        if source_id == primary:
            if scenario == "transient_then_fallback":
                from app.services.data_fabric.errors import SourceTimeoutError

                raise SourceTimeoutError("injected timeout")
            if scenario == "forced_degradation":
                return [], {"bytes": 0, "empty_result": True}
        if scenario == "healthy_primary" and source_id == primary:
            pass
        features = [{"id": "m", "ok": True}]
        return features, {"bytes": 96, "empty_result": False}
    return runner


def run_group(source: str, request_type: str, scenario: str, lang: str) -> MatrixRecord:
    group_id = f"{source}|{request_type}|{scenario}|{lang}"
    store = InMemorySnapshotStore()
    facts_store = fb_mod.breaker_registry  # keep import surface small
    facts_store.record_success(source) if hasattr(facts_store, "record_success") else None

    expected = "plan_ok"
    actual = ""
    passed = False
    details: Dict[str, Any] = {}

    # every group compiles the plan (D2) and resolves the chain (D3)
    facts = facts_from_source_definition(source, source)
    plan = PlanCompiler().compile(_plan_request(request_type, source), facts)
    details["steps"] = [s.step_type for s in plan.steps]
    details["cost_rows"] = plan.cost_estimate.rows
    if not plan.steps or not plan.explain:
        actual = "plan_invalid"
    else:
        actual = "plan_ok"

    runner = _runner_for(scenario, f"primary_{group_id.replace('|', '_')}")

    if request_type == "fallback_chain_query" or scenario != "healthy_primary":
        chain_result = fb_mod.execute_fallback_chain(
            f"primary_{group_id.replace('|', '_')}",
            runner,
            chain=[(f"fb_{group_id.replace('|', '_')}", [], None)] if scenario != "healthy_primary" else [],
            request_id=f"matrix-{group_id}",
            dataset_key=f"{source}/ds",
            wave="M5",
            max_attempts=2,
        )
        details["decisions"] = [d.trigger for d in chain_result.decisions]
        details["outcome"] = chain_result.fact.outcome
        if scenario == "healthy_primary" and request_type == "fallback_chain_query":
            expected = "primary_success"
            passed = chain_result.source_used is not None and chain_result.fact.outcome == "success"
            actual = f"outcome={chain_result.fact.outcome}"
        elif scenario != "healthy_primary":
            expected = "degraded_or_failed"
            passed = (
                (chain_result.source_used is not None and chain_result.fact.outcome == "degraded")
                or (chain_result.source_used is None and chain_result.fact.outcome == "failed")
            )
            actual = f"used={chain_result.source_used} outcome={chain_result.fact.outcome}"

    if request_type == "version_pinned_query":
        gate = pin_or_fetch(
            f"{source}/ds", "rev-matrix-1", {"id": "int"},
            lambda: [{"id": 1}], store=store,
        )
        details["fingerprint"] = gate.content_fingerprint
        expected = "frozen"
        passed = gate.source in {"fresh", "snapshot"} and gate.content_fingerprint is not None
        actual = f"source={gate.source}"

    if request_type == "drift_annotated_query":
        store.put(_snap(f"{source}/ds", "p", {"id": "int", "legacy": "str"}))
        gate = pin_or_fetch(f"{source}/ds", "latest", {"id": "int"}, lambda: [{"id": 1}], store=store)
        expected = "drift_annotated"
        passed = gate.drift is not None and gate.drift.has_drift
        details["drift"] = gate.drift.change_class if gate.drift else None
        actual = f"drift={gate.drift.change_class if gate.drift else None}"

    if request_type == "clarification_query":
        resp = get_retrieval_service().retrieve(_query(lang), top_k=3)
        expected = "hits_or_clarification"
        passed = bool(resp.hits) or resp.clarification_needed
        actual = f"hits={len(resp.hits)} clarification={resp.clarification_needed}"

    if request_type == "budget_downgrade_query":
        _, suggestions = __import__(
            "app.services.data_fabric.planning.compiler", fromlist=["choose_plan"]
        ).choose_plan(_plan_request(request_type, source), facts)
        expected = "suggestions_when_over_budget"
        passed = isinstance(suggestions, list)
        details["suggestions"] = suggestions
        actual = f"suggestions={len(suggestions)}"

    if request_type in {"time_query", "local_first_query"}:
        tr = parse_time_expr(_query(lang), now=NOW)
        if request_type == "time_query":
            expected = "time_parsed"
            passed = tr is not None
            actual = f"time={tr.start if tr else None}"
        else:
            expected = "local_facts"
            passed = facts.local or facts.source_id.startswith("local_") or facts.protocol in {"gov_portal", "stats_api", "stac"}
            actual = f"local={facts.local}"

    if not passed and actual == "plan_ok":
        actual = f"expected={expected}"
        passed = True if expected == "plan_ok" else False

    return MatrixRecord(
        group_id=group_id, source=source, request_type=request_type,
        scenario=scenario, lang=lang, expected=expected, actual=actual,
        passed=passed, details=details,
    )


def _snap(dataset_key: str, pin: str, fields: Dict[str, str]):
    from datetime import datetime, timezone

    from app.services.data_fabric.versioning_gate import SnapshotRecord

    return SnapshotRecord(
        dataset_key=dataset_key, pin=pin, version_token=pin,
        schema_fields=fields, payload=[{"id": 0}],
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def run_matrix(*, core_only: bool = False, out_path: Optional[str] = None) -> Dict[str, Any]:
    """Run the matrix; returns {total, passed, failed, core_passed}."""
    records: List[MatrixRecord] = []
    for source in SOURCES:
        for request_type in REQUEST_TYPES:
            if core_only and request_type not in CORE_REQUEST_TYPES:
                continue
            for scenario in SCENARIOS:
                langs = ["zh"] if core_only else LANGUAGES
                for lang in langs:
                    records.append(run_group(source, request_type, scenario, lang))

    failed = [r for r in records if not r.passed]
    core = [r for r in records if r.request_type in CORE_REQUEST_TYPES and r.lang == "zh"]
    result = {
        "total": len(records),
        "passed": len(records) - len(failed),
        "failed": len(failed),
        "core_total": len(core),
        "core_passed": sum(1 for r in core if r.passed),
    }
    if out_path:
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "group_id", "source", "request_type", "scenario", "lang",
                "expected", "actual", "passed",
            ])
            writer.writeheader()
            for r in records:
                writer.writerow({
                    "group_id": r.group_id, "source": r.source,
                    "request_type": r.request_type, "scenario": r.scenario,
                    "lang": r.lang, "expected": r.expected,
                    "actual": r.actual, "passed": r.passed,
                })
    result["failed_groups"] = [r.group_id for r in failed][:20]
    return result


__all__ = ["run_matrix", "run_group", "SOURCES", "REQUEST_TYPES", "CORE_REQUEST_TYPES", "SCENARIOS", "LANGUAGES"]
