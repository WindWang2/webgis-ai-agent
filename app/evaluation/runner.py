"""Benchmark runner (ADR-0092 B2/B3/B4).

Deterministic-first evaluation: every verdict comes from schema assertions,
planner evidence, tool traces, MapSpec state, or numeric goldens — never from
an LLM judge. Metrics per case follow B3; unknown measurements are recorded
as ``None`` rather than fabricated.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.evaluation.case import GISBenchmarkCase, NumericAssertion
from app.evaluation.fixtures import FIXTURE_BUILDERS, ndvi_pair

logger = logging.getLogger(__name__)


class CaseResult(BaseModel):
    case_id: str
    group: str
    name: str
    status: str = "pass"  # pass | fail | skipped
    passed: bool = False
    metrics: Dict[str, Any] = Field(default_factory=dict)
    plan_evidence: Dict[str, Any] = Field(default_factory=dict)
    failures: List[str] = Field(default_factory=list)
    skipped_reason: str = ""
    elapsed_ms: int = 0


def _get_path(doc: Any, path: str) -> Any:
    cur = doc
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
    return cur


def _reduce(value: Any, agg: str) -> Any:
    if value is None:
        # Absent collection = zero items (an upper-bound assertion like
        # "no inline features" must PASS when the key is absent).
        return 0 if agg == "len" else None
    if agg == "value":
        return value
    if agg == "len":
        return len(value) if hasattr(value, "__len__") else 0
    if agg == "first":
        if isinstance(value, list) and value:
            return value[0]
        return None
    if agg == "sum":
        if isinstance(value, list):
            total = 0.0
            for item in value:
                if isinstance(item, dict):
                    nums = [
                        v for v in item.values() if isinstance(v, (int, float))
                    ]
                    if nums:
                        total += float(nums[0])
                elif isinstance(item, (int, float)):
                    total += float(item)
            return total
        return None
    if agg == "mean":
        if isinstance(value, list) and value:
            vals = [v for v in value if isinstance(v, (int, float))]
            return sum(vals) / len(vals) if vals else None
        return None
    return None


def _check_op(actual: Any, op: str, expected: float, tol: float) -> bool:
    if actual is None:
        return False
    try:
        a = float(actual)
    except (TypeError, ValueError):
        return False
    if op == "==":
        return abs(a - expected) <= tol
    if op == "approx":
        return abs(a - expected) <= tol
    if op == ">":
        return a > expected
    if op == ">=":
        return a >= expected
    if op == "<":
        return a < expected
    if op == "<=":
        return a <= expected
    return False


def _check_numeric(
    assertion: NumericAssertion,
    *,
    step_results: List[Dict[str, Any]],
    mapspec: Optional[Dict[str, Any]],
    fixture_docs: Dict[str, Any],
    quantities: Dict[str, Any],
    step_blobs: Optional[List[str]] = None,
) -> bool:
    if assertion.source == "step_result_bytes":
        # LLM-facing boundedness: the serialized tool result must stay under
        # a byte budget (large-data contract — descriptors, not payloads).
        if not step_results:
            return False
        idx = assertion.step if assertion.step is not None else len(step_results) - 1
        if idx < 0 or idx >= len(step_results):
            return False
        if step_blobs is not None and idx < len(step_blobs):
            size = len(step_blobs[idx])  # reuse the dispatch-time serialization
        else:
            import json as _json

            size = len(_json.dumps(step_results[idx], ensure_ascii=False, default=str))
        return _check_op(size, assertion.op, assertion.value, assertion.tol)
    if assertion.source == "quantity":
        actual = quantities.get(assertion.quantity or "")
        return _check_op(actual, assertion.op, assertion.value, assertion.tol)
    if assertion.source == "fixture":
        alias = assertion.path.split(".")[0] if "." in assertion.path else assertion.path
        doc = fixture_docs.get(alias)
        rest = assertion.path[len(alias) + 1:] if "." in assertion.path else ""
        return _check_op(
            _reduce(_get_path(doc, rest), assertion.agg),
            assertion.op, assertion.value, assertion.tol,
        )
    if assertion.source == "mapspec":
        if mapspec is None:
            return False
        return _check_op(
            _reduce(_get_path(mapspec, assertion.path), assertion.agg),
            assertion.op, assertion.value, assertion.tol,
        )
    # step_result
    if not step_results:
        return False
    idx = assertion.step if assertion.step is not None else len(step_results) - 1
    if idx < 0 or idx >= len(step_results):
        return False
    return _check_op(
        _reduce(_get_path(step_results[idx], assertion.path), assertion.agg),
        assertion.op, assertion.value, assertion.tol,
    )


class GISBenchmarkRunner:
    """Executes benchmark cases deterministically (offline, no LLM)."""

    def __init__(self, registry=None):
        self._registry = registry
        self._planner = None

    def _ensure_registry(self):
        if self._registry is None:
            from app.tools import init_tools
            from app.tools.registry import ToolRegistry

            reg = ToolRegistry()
            init_tools(reg)
            self._registry = reg
        return self._registry

    def _tool_names(self) -> set:
        try:
            return set(self._ensure_registry().list_tools())
        except Exception:  # noqa: BLE001
            return set()

    # ── plan tier ─────────────────────────────────────────────────────

    def _run_plan_tier(self, case: GISBenchmarkCase) -> Tuple[Dict[str, Any], List[str]]:
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner import MapProductPlanner

        planner = MapProductPlanner()
        intent = resolve_map_request_intent(case.query)
        plan = planner.plan_from_intent(
            intent,
            available_tools=self._tool_names() or None,
            use_memo=False,
        )
        resolved = [
            r.capability for r in plan.algorithm_selections if r.status == "resolved"
        ]
        algorithms = [
            r.algorithm for r in plan.algorithm_selections if r.algorithm
        ]
        evidence = {
            "task": intent.task,
            "recipe_id": plan.recipe_id,
            "template_id": plan.template_id,
            "resolved_capabilities": resolved,
            "algorithms": algorithms,
            "tool_calls_planned": len([r for r in plan.data_requirements if r.resolved_tool]),
        }
        failures: List[str] = []

        expected_caps = list(case.expected_capabilities)
        resolved_set = set(resolved)
        expected_set = set(expected_caps)
        hits = resolved_set & expected_set
        precision = len(hits) / len(resolved_set) if resolved_set else 0.0
        recall = len(hits) / len(expected_set) if expected_set else 1.0
        # optional capabilities don't count against precision
        optional_resolved = resolved_set & set(case.optional_capabilities)
        if optional_resolved:
            precision = min(1.0, (len(hits)) / max(1, len(resolved_set) - len(optional_resolved)))

        if case.expected_task and intent.task != case.expected_task:
            failures.append(f"task: expected {case.expected_task}, got {intent.task}")
        missing_caps = sorted(expected_set - resolved_set)
        if missing_caps:
            failures.append(f"capabilities unresolved: {missing_caps}")
        forbidden_hits = sorted(set(algorithms) & set(case.forbidden_algorithms))
        if forbidden_hits:
            failures.append(f"forbidden algorithms selected: {forbidden_hits}")
        if case.allowed_algorithms is not None:
            ok = algorithms and all(
                any(a.startswith(p) for p in case.allowed_algorithms)
                for a in algorithms
            )
            if not ok:
                failures.append(
                    f"algorithms outside allowed set {case.allowed_algorithms}: {algorithms}"
                )
        if case.expected_recipe and plan.recipe_id != case.expected_recipe:
            failures.append(f"recipe: expected {case.expected_recipe}, got {plan.recipe_id}")
        if case.max_tool_calls is not None:
            planned_calls = evidence["tool_calls_planned"]
            if planned_calls > case.max_tool_calls:
                failures.append(
                    f"tool_calls: planned {planned_calls} > max {case.max_tool_calls}"
                )
        facet_failures = self._check_facet_contract(plan, case.expected_product_facets)
        failures.extend(facet_failures)

        metrics = {
            "task_correct": intent.task == case.expected_task if case.expected_task else None,
            "capability_precision": round(precision, 4),
            "capability_recall": round(recall, 4),
            "algorithm_correct": not forbidden_hits and (case.allowed_algorithms is None or ok),
        }
        evidence["metrics"] = metrics
        return evidence, failures

    def _check_facet_contract(
        self, plan: Any, expected_facets: List[str]
    ) -> List[str]:
        if not expected_facets:
            return []
        from app.services.gis_harness.product_facets import derive_facet_contract

        # derive_facet_contract consumes the gis_chapter (dict) projection.
        # A draft-stage plan carries no composition_template_id yet — resolve
        # it from the product template so legend-slot signals match the
        # finalize-stage contract.
        chapter = plan.model_dump()
        template_selection = chapter.setdefault("template_selection", {})
        if isinstance(template_selection, dict) and not template_selection.get(
            "composition_template_id"
        ) and chapter.get("template_id"):
            try:
                from app.services.gis_harness.template_catalog import (
                    get_template_catalog,
                )

                tmpl = get_template_catalog().get_product_template(
                    chapter["template_id"]
                )
                cid = getattr(tmpl, "composition_template_id", "") if tmpl else ""
                if cid:
                    template_selection["composition_template_id"] = cid
            except Exception:  # noqa: BLE001 — 投影失败退化为部分契约
                pass
        contract = derive_facet_contract(chapter)
        failures: List[str] = []
        for facet in expected_facets:
            if facet == "chart":
                if not contract.chart_required:
                    failures.append("facet: chart_required false")
            elif facet == "legend":
                if not contract.legend_required:
                    failures.append("facet: legend_required false")
            elif facet in ("title", "north_arrow", "scale_bar", "attribution"):
                if facet not in contract.required_component_types:
                    failures.append(f"facet: component {facet} not required")
            elif facet not in ("map", "map_layer", "statistics"):
                failures.append(f"facet: unknown expectation {facet}")
        return failures

    # ── execute tier ──────────────────────────────────────────────────

    async def _run_execute_tier(
        self, case: GISBenchmarkCase, session_id: str
    ) -> Dict[str, Any]:
        """Scripted deterministic execution. Returns an evidence dict with
        failures appended by the caller."""
        reg = self._ensure_registry()
        registered = self._tool_names()
        evidence: Dict[str, Any] = {
            "tool_calls": 0,
            "retry_count": 0,
            "reused_artifact_count": 0,
            "results": [],
            "failures": [],
            "quantities": {},
        }
        if any(step.tool not in registered for step in case.script):
            missing = sorted({s.tool for s in case.script} - registered)
            evidence["skipped"] = f"tools not registered: {missing}"
            return evidence

        # Materialize fixtures (bounded: large fixtures are NOT inlined into
        # any LLM-facing payload — they go straight into the session store).
        fixture_docs: Dict[str, Any] = {}
        from app.services.session_data import session_data_manager

        for alias in case.fixture_aliases:
            if alias == "ndvi_pair":
                continue  # raster golden: materialized via quantities, not the session store
            builder = FIXTURE_BUILDERS.get(alias)
            if builder is None:
                evidence["failures"].append(f"fixture: unknown builder {alias}")
                continue
            doc = builder()
            fixture_docs[alias] = doc
            ref = await session_data_manager.store(session_id, doc, prefix="bench")
            await session_data_manager.set_alias(session_id, ref, alias)

        # Dispatch script.
        seen_calls: Dict[Tuple[str, str], int] = {}
        results: List[Dict[str, Any]] = []
        _last_step_blobs: List[str] = []
        for i, step in enumerate(case.script):
            args = json.loads(json.dumps(step.args))  # deep copy
            for key, val in list(args.items()):
                if isinstance(val, str) and val.startswith("fixture:"):
                    args[key] = val.split(":", 1)[1]
            call_key = (step.tool, json.dumps(args, sort_keys=True, default=str))
            seen_calls[call_key] = seen_calls.get(call_key, 0) + 1
            try:
                res = await reg.dispatch(step.tool, args, session_id=session_id)
            except Exception as e:  # noqa: BLE001 — failure semantics are evidence
                res = {"success": False, "error": str(e)[:300]}
            results.append(res if isinstance(res, dict) else {"value": res})
            evidence["tool_calls"] += 1
            # Serialize once per step: the same blob backs the bounded preview
            # and the (later) step_result_bytes assertion — no double dump of
            # large results.
            blob = json.dumps(res, ensure_ascii=False, default=str)
            _last_step_blobs.append(blob)
            got = blob[:200]
            if step.expect_error_contains:
                blob = got
                if step.expect_error_contains not in blob:
                    evidence["failures"].append(
                        f"step {i} ({step.tool}): expected error containing "
                        f"'{step.expect_error_contains}', got: {got}"
                    )
            else:
                failed = isinstance(res, dict) and (
                    res.get("success") is False
                    or (isinstance(res.get("error"), str) and res.get("error"))
                    or str(res.get("type") or "") == "error"
                )
                if failed:
                    evidence["failures"].append(
                        f"step {i} ({step.tool}) failed: {got}"
                    )
        evidence["results"] = results
        evidence["retry_count"] = sum(c - 1 for c in seen_calls.values() if c > 1)

        # MapSpec-derived assertions.
        mapspec = None
        try:
            from app.services.mapspec.store import mapspec_store_instance

            mapspec = await mapspec_store_instance.get_mapspec(session_id)
        except Exception:  # noqa: BLE001
            mapspec = None
        evidence["mapspec_present"] = bool(mapspec)
        evidence["mapspec"] = mapspec

        if case.component_assertions:
            types: set = set()
            if isinstance(mapspec, dict):
                layout = mapspec.get("layout") or {}
                types = {
                    str(c.get("type") or "")
                    for c in (layout.get("components") or [])
                    if isinstance(c, dict)
                }
            for ctype in case.component_assertions:
                if ctype not in types:
                    evidence["failures"].append(
                        f"component missing in MapSpec: {ctype} (have {sorted(types)})"
                    )
        if case.expected_artifact_types:
            session_types = await self._session_artifact_types(session_id)
            for atype in case.expected_artifact_types:
                if atype not in session_types:
                    evidence["failures"].append(
                        f"artifact type missing: {atype} (have {sorted(session_types)})"
                    )
        if case.expected_interaction_semantics:
            evidence["failures"].extend(
                self._check_interaction_semantics(case, results)
            )
        # Numeric assertions (fixtures/quantities available here).
        quantities = self._compute_quantities(case)
        evidence["quantities"] = quantities
        for assertion in case.numeric_assertions:
            if not _check_numeric(
                assertion,
                step_results=results,
                mapspec=mapspec,
                fixture_docs=fixture_docs,
                quantities=quantities,
                step_blobs=_last_step_blobs,
            ):
                evidence["failures"].append(
                    f"numeric assertion failed: {assertion.label or assertion.model_dump()}"
                )
        return evidence

    def _compute_quantities(self, case: GISBenchmarkCase) -> Dict[str, Any]:
        """Deterministic named quantities (offline raster goldens)."""
        quantities: Dict[str, Any] = {}
        if any(a == "ndvi_pair" for a in case.fixture_aliases):
            import numpy as np

            from app.services.rs.band_math import (
                compute_index_array,
                compute_raster_stats,
            )

            red_grid, nir_grid, expected = ndvi_pair()
            ndvi = compute_index_array(
                "ndvi", red=np.array(red_grid), nir=np.array(nir_grid)
            )
            stats = compute_raster_stats(ndvi)
            quantities["ndvi_mean"] = stats.get("mean")
            quantities["ndvi_expected_mean"] = expected
        return quantities

    async def _session_artifact_types(self, session_id: str) -> set:
        try:
            from app.services.artifact_registry import list_artifacts

            records = await list_artifacts(session_id)
            return {
                str(r.get("artifact_type") or "")
                for r in records
                if r.get("artifact_type")
            }
        except Exception:  # noqa: BLE001
            return set()

    def _check_interaction_semantics(
        self, case: GISBenchmarkCase, results: List[Dict[str, Any]]
    ) -> List[str]:
        """Deterministic interaction-contract probes (pure functions)."""
        failures: List[str] = []
        for semantics in case.expected_interaction_semantics:
            if semantics == "user-wins":
                failures.extend(self._probe_user_wins())
            elif semantics == "artifact-expired-no-remount":
                failures.extend(self._probe_expired_no_remount())
            else:
                failures.append(f"unknown interaction semantics: {semantics}")
        return failures

    def _probe_user_wins(self) -> List[str]:
        """G8: a user-hidden layer must never be force-restored by repair."""
        from app.services.gis_harness.runtime_repair import classify_runtime_repairs

        chapter = {
            "map_layers": [
                {"role": "primary", "layer_id": "lyr_u", "source_capability": "poi_query"}
            ],
        }
        mapspec = {
            "layers": [{
                "id": "lyr_u", "source": "src_u", "type": "circle",
                "visible": False,
                "cartographic_intent": {"presentation_owner": "user"},
            }],
            "sources": {"src_u": {"type": "geojson", "ref": "ref:user-1"}},
        }
        observation = {
            "source": "frontend_runtime",
            "mapspec_revision": 1,
            "layers": [{"id": "lyr_u", "runtime_layer_count": 1, "visible": False}],
        }
        plan = classify_runtime_repairs(
            chapter, mapspec, observation=observation, current_revision=1
        )
        failures: List[str] = []
        if plan.visibility_restores:
            failures.append(
                f"user-wins violated: visibility_restore planned for {plan.visibility_restores}"
            )
        if "lyr_u" not in plan.user_owned:
            failures.append("user-wins: hidden layer not disclosed as user-owned")
        return failures

    def _probe_expired_no_remount(self) -> List[str]:
        """G9: expired artifact → execution debt (rerun producer), never a
        remount of the dead ref."""
        from app.services.gis_harness.runtime_repair import classify_runtime_repairs

        chapter = {
            "map_layers": [
                {"role": "primary", "layer_id": "lyr_e", "source_capability": "poi_query"}
            ],
        }
        mapspec = {
            "layers": [{
                "id": "lyr_e", "source": "src_e", "type": "circle", "visible": True,
            }],
            "sources": {"src_e": {"type": "geojson", "ref": "ref:expired-1"}},
        }
        observation = {
            "source": "frontend_runtime",
            "mapspec_revision": 1,
            "layers": [],
        }
        plan = classify_runtime_repairs(
            chapter, mapspec,
            descriptors={"ref:expired-1": None},
            observation=observation, current_revision=1,
        )
        failures: List[str] = []
        if not plan.execution_debts:
            failures.append("expired artifact: no execution debt raised")
        if plan.reassert_layers:
            failures.append(
                f"expired artifact remount attempted: {plan.reassert_layers}"
            )
        return failures

    # ── entry points ──────────────────────────────────────────────────

    async def run_case(self, case: GISBenchmarkCase) -> CaseResult:
        started = time.monotonic()
        result = CaseResult(
            case_id=case.id, group=case.group, name=case.name,
        )
        try:
            plan_evidence, failures = self._run_plan_tier(case)
            result.plan_evidence = plan_evidence
            result.failures.extend(failures)

            exec_evidence: Optional[Dict[str, Any]] = None
            # Execute tier runs when the case ships a script, interaction
            # semantics probes, or fixture/quantity numeric goldens.
            needs_exec = (
                case.script
                or case.expected_interaction_semantics
                or case.fixture_aliases
            )
            if not case.plan_only and needs_exec:
                session_id = f"bench-{uuid.uuid4().hex[:10]}"
                try:
                    exec_evidence = await self._run_execute_tier(case, session_id)
                finally:
                    try:
                        from app.services.session_data import session_data_manager

                        await session_data_manager.clear_session(session_id)
                    except Exception:  # noqa: BLE001
                        pass
                if exec_evidence.get("skipped"):
                    result.status = "skipped"
                    result.skipped_reason = exec_evidence["skipped"]
                    result.metrics = {
                        **plan_evidence.get("metrics", {}),
                        "tool_call_count": 0,
                    }
                    result.elapsed_ms = int((time.monotonic() - started) * 1000)
                    return result
                result.failures.extend(exec_evidence.get("failures") or [])

            # Assemble B3 metrics.
            metrics: Dict[str, Any] = dict(plan_evidence.get("metrics") or {})
            metrics["numerical_correct"] = None
            metrics["artifact_contract_valid"] = None
            metrics["map_product_complete"] = None
            metrics["render_verified"] = None  # not measured offline (honest)
            metrics["tool_call_count"] = 0
            metrics["retry_count"] = 0
            metrics["reused_artifact_count"] = 0
            if exec_evidence is not None:
                metrics["tool_call_count"] = exec_evidence.get("tool_calls", 0)
                metrics["retry_count"] = exec_evidence.get("retry_count", 0)
                had_numeric = bool(case.numeric_assertions)
                had_contract = bool(
                    case.expected_artifact_types or case.component_assertions
                )
                if had_numeric:
                    metrics["numerical_correct"] = not any(
                        f.startswith("numeric") for f in exec_evidence.get("failures", [])
                    )
                if had_contract:
                    metrics["artifact_contract_valid"] = not any(
                        f.startswith(("component", "artifact")) for f in exec_evidence.get("failures", [])
                    )
                metrics["map_product_complete"] = bool(exec_evidence.get("mapspec_present"))
            result.metrics = metrics

            if exec_evidence is not None and not exec_evidence.get("mapspec_present") \
                    and (case.component_assertions or case.expected_artifact_types):
                result.failures.append("mapspec missing after script (map product incomplete)")
            result.passed = not result.failures
            result.status = "pass" if result.passed else "fail"
        except Exception as e:  # noqa: BLE001 — a broken case is a failed case
            logger.exception("[gis-benchmark] case %s crashed", case.id)
            result.failures.append(f"runner error: {e}")
            result.passed = False
            result.status = "fail"
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result

    async def run(
        self,
        cases: List[GISBenchmarkCase],
    ) -> List[CaseResult]:
        results: List[CaseResult] = []
        for case in cases:
            results.append(await self.run_case(case))
        return results
