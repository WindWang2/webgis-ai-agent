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
        got_patterns = {w.get("pattern") for w in (plan.methodology_warnings or [])}
        got_codes = set()
        for w in (plan.methodology_warnings or []):
            for code in (w.get("warning_codes") or []):
                got_codes.add(str(code))
            if w.get("code"):
                got_codes.add(str(w.get("code")))
        honesty_checked = bool(
            case.expected_methodology_warnings or case.forbidden_methodology_warnings
            or case.expected_warning_codes or case.forbidden_warning_codes
        )
        honesty_ok = (
            set(case.expected_methodology_warnings) <= got_patterns
            and not (set(case.forbidden_methodology_warnings) & got_patterns)
        ) if honesty_checked else None
        evidence = {
            "task": intent.task,
            "warning_codes": sorted(got_codes),
            "recipe_id": plan.recipe_id,
            "template_id": plan.template_id,
            "resolved_capabilities": resolved,
            "algorithms": algorithms,
            "tool_calls_planned": len([r for r in plan.data_requirements if r.resolved_tool]),
            "methodology_warnings": [
                w.get("pattern") for w in (plan.methodology_warnings or [])
            ],
            "methodology_honesty_ok": honesty_ok,
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

        if case.expected_tasks:
            if intent.task not in case.expected_tasks:
                failures.append(
                    f"task: expected one of {case.expected_tasks}, got {intent.task}"
                )
        elif case.expected_task and intent.task != case.expected_task:
            failures.append(f"task: expected {case.expected_task}, got {intent.task}")
        # Methodology honesty: the plan must carry the expected pattern-level
        # warnings (e.g. equity without a denominator) — and honest-disclosure
        # cases must NOT silently pass when the warning is missing.
        if case.expected_methodology_warnings or case.forbidden_methodology_warnings:
            got_patterns = {w.get("pattern") for w in (plan.methodology_warnings or [])}
            missing_warn = sorted(set(case.expected_methodology_warnings) - got_patterns)
            if missing_warn:
                failures.append(
                    f"methodology warnings missing: {missing_warn} (got {sorted(got_patterns)})"
                )
            forbidden_warn = sorted(
                set(case.forbidden_methodology_warnings) & got_patterns
            )
            if forbidden_warn:
                failures.append(
                    f"methodology noise: {forbidden_warn} warned without semantic basis"
                )
            # Workflow V2（C10）：稳定警告码断言（比 pattern 更细的反声明锚）。
            missing_codes = sorted(set(case.expected_warning_codes) - got_codes)
            if missing_codes:
                failures.append(f"warning codes missing: {missing_codes}")
            noise_codes = sorted(set(case.forbidden_warning_codes) & got_codes)
            if noise_codes:
                failures.append(
                    f"forbidden warning codes present: {noise_codes}"
                )
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
        if case.expected_recipes:
            if plan.recipe_id not in case.expected_recipes:
                failures.append(
                    f"recipe: expected one of {case.expected_recipes}, got {plan.recipe_id}"
                )
        elif case.expected_recipe and plan.recipe_id != case.expected_recipe:
            failures.append(f"recipe: expected {case.expected_recipe}, got {plan.recipe_id}")
        # ── V2 scope 绑定契约（wrong-AOI 硬负例的诚实面）─────────────────
        # known=False：解析器不得虚构 scope（错误 AOI 必须留空，禁止静默
        # 绑定默认城市）；known=True：必须解析出非空 name。
        scope_binding_correct = None
        if case.expected_scope is not None:
            got_name = intent.scope.name or ""
            got_level = str(intent.scope.level or "")
            evidence["scope"] = {"name": got_name, "level": got_level}
            scope_failures: List[str] = []
            known = bool(got_name)
            if case.expected_scope.known is not None and known != case.expected_scope.known:
                scope_failures.append(
                    f"scope known: expected {case.expected_scope.known}, got {known} "
                    f"(name={got_name!r}, level={got_level!r})"
                )
            if case.expected_scope.name is not None and got_name != case.expected_scope.name:
                scope_failures.append(
                    f"scope name: expected {case.expected_scope.name!r}, got {got_name!r}"
                )
            if case.expected_scope.level is not None and got_level != case.expected_scope.level:
                scope_failures.append(
                    f"scope level: expected {case.expected_scope.level!r}, got {got_level!r}"
                )
            failures.extend(scope_failures)
            scope_binding_correct = not scope_failures
        if case.max_tool_calls is not None:
            planned_calls = evidence["tool_calls_planned"]
            if planned_calls > case.max_tool_calls:
                failures.append(
                    f"tool_calls: planned {planned_calls} > max {case.max_tool_calls}"
                )
        # ── 质量场景 DSL 契约（Wave 2+3，全部 opt-in）────────────────────
        # resolved 工具名（去重、保序）；描述符查询可能对未注册名 raise，
        # 逐一兜底（未知名 = 该工具类别未知，诚实跳过，不虚构失败）。
        resolved_tools: List[str] = []
        for r in plan.data_requirements:
            if r.resolved_tool and r.resolved_tool not in resolved_tools:
                resolved_tools.append(r.resolved_tool)
        if any((
            case.expected_tool_classes,
            case.expected_export_formats,
            case.max_context_schema_bytes is not None,
            case.forbid_network_tools,
            case.trace_requirements,
            case.allowed_tools is not None,
        )):
            registry = self._ensure_registry()
            got_classes: set = set()
            context_schema_bytes = 0
            unknown_schema_tools: List[str] = []
            for name in resolved_tools:
                try:
                    desc = registry.descriptor(name)
                except Exception:  # noqa: BLE001 — 未注册工具按未知处理
                    continue
                if desc.output_semantic_type:
                    got_classes.add(desc.output_semantic_type)
                if case.forbid_network_tools and desc.network:
                    failures.append(
                        f"network tool forbidden but resolved: {name}"
                    )
                if case.max_context_schema_bytes is not None:
                    size = registry.schema_size(name)
                    if size is None:
                        unknown_schema_tools.append(name)
                    else:
                        context_schema_bytes += size
            if case.expected_tool_classes:
                missing_classes = sorted(
                    set(case.expected_tool_classes) - got_classes)
                if missing_classes:
                    failures.append(
                        f"tool classes: expected {case.expected_tool_classes} "
                        f"but resolved tools cover {sorted(got_classes)}"
                    )
            if case.expected_export_formats:
                missing_exports = sorted(
                    set(case.expected_export_formats) - set(plan.exports or []))
                if missing_exports:
                    failures.append(
                        f"export formats: expected {case.expected_export_formats} "
                        f"in plan.exports {sorted(plan.exports or [])}"
                    )
            if case.max_context_schema_bytes is not None:
                # 仅声明预算时记入 evidence；未知 schema 的工具如实留痕。
                evidence["context_schema_bytes"] = context_schema_bytes
                if unknown_schema_tools:
                    evidence["context_schema_unknown_tools"] = unknown_schema_tools
                if context_schema_bytes > case.max_context_schema_bytes:
                    failures.append(
                        f"context schema: {context_schema_bytes} bytes "
                        f"> budget {case.max_context_schema_bytes}"
                    )
            if case.trace_requirements:
                evidence["trace_requirements"] = list(case.trace_requirements)
            if case.allowed_tools is not None:
                # V2 第一类可接受工具名备选集：resolved 工具必须全部落入
                # （exact-match，比 allowed_algorithms 前缀集更细）。
                outside = sorted(set(resolved_tools) - set(case.allowed_tools))
                if outside:
                    failures.append(
                        f"tools outside allowed set {case.allowed_tools}: {outside}"
                    )
                evidence["allowed_tools_ok"] = not outside
        facet_failures = self._check_facet_contract(plan, case.expected_product_facets)
        failures.extend(facet_failures)

        metrics = {
            "task_correct": intent.task == case.expected_task if case.expected_task else None,
            "capability_precision": round(precision, 4),
            "capability_recall": round(recall, 4),
            "algorithm_correct": not forbidden_hits and (case.allowed_algorithms is None or ok),
            # None = case declares no honesty contract (not checked ≠ passed)
            "methodology_honesty_ok": honesty_ok,
            # ── V3 semantic planning metrics（Goal §十二）────────────────
            # 本体任务匹配（top-1 == 期望本体任务；None = 未声明契约）
            "ontology_top1_correct": None,
            # recipe 选择命中（exact 或集合成员；None = 无 recipe 契约）
            "recipe_selection_correct": (
                (plan.recipe_id in case.expected_recipes)
                if case.expected_recipes
                else (plan.recipe_id == case.expected_recipe)
                if case.expected_recipe else None
            ),
            # 过度分析（false-positive professional）：声明 forbidden 警告码
            # 的描述性请求不得携带专业义务噪声（None = 未声明）
            "no_false_professional_analysis": (
                not (set(case.forbidden_warning_codes) & got_codes)
                if case.forbidden_warning_codes else None
            ),
            # 不必要工具数（resolved 超出期望能力的部分；报告面指标）
            "unnecessary_tool_count": max(
                0, len(resolved_set - expected_set - set(case.optional_capabilities))),
            # 数据资格状态 / 回退层 / 规划确定性（opt-in 契约；None = 未声明）
            "qualification_states_correct": None,
            "fallback_tier_correct": None,
            "planning_deterministic": None,
            # ── V2 指标（opt-in 契约；None = 未声明）─────────────────────
            "scope_binding_correct": scope_binding_correct,
            "allowed_tools_ok": (
                not any(f.startswith("tools outside allowed set") for f in failures)
                if case.allowed_tools is not None else None
            ),
        }
        v3_failures, v3_metrics = self._run_v3_contract_tier(case, intent, plan)
        failures.extend(v3_failures)
        metrics.update(v3_metrics)
        pol_failures, pol_metrics = self._run_policy_tier(case)
        failures.extend(pol_failures)
        metrics.update(pol_metrics)
        ev_failures, ev_metrics = self._run_evidence_tier(case)
        failures.extend(ev_failures)
        metrics.update(ev_metrics)
        sec_failures, sec_metrics = self._run_security_tier(case)
        failures.extend(sec_failures)
        metrics.update(sec_metrics)
        evidence["metrics"] = metrics
        return evidence, failures

    def _run_v3_contract_tier(
        self, case: GISBenchmarkCase, intent: Any, plan: Any
    ) -> Tuple[List[str], Dict[str, Any]]:
        """V3 语义契约层（全部 opt-in，零声明零开销）。

        - 本体匹配：intent 的本体 top-1 对齐 expected_ontology_task；
        - 数据资格/回退：带 qualification_profile 走 compile_workflow 复评
          （qualify_data 四态 + fallback_v3 层裁决）；
        - 规划确定性：双跑 plan tier 对齐（check_determinism）。
        """
        failures: List[str] = []
        metrics: Dict[str, Any] = {
            "ontology_top1_correct": None,
            "qualification_states_correct": None,
            "fallback_tier_correct": None,
            "planning_deterministic": None,
        }

        if case.expected_ontology_task:
            from app.services.gis_harness.gis_ontology import match_task_ontology

            matches = match_task_ontology(intent, limit=1)
            top1 = matches[0].task_id if matches else ""
            metrics["ontology_top1_correct"] = top1 == case.expected_ontology_task
            if top1 != case.expected_ontology_task:
                failures.append(
                    f"ontology: expected top-1 {case.expected_ontology_task}, got {top1!r}"
                )

        if case.qualification_profile is not None:
            from app.services.gis_harness.workflow_compiler import compile_workflow

            compilation = compile_workflow(
                case.query, profile=case.qualification_profile)
            quals = {q.get("role"): q.get("state")
                     for q in compilation.data_qualifications}
            for role, expected_state in case.expected_qualification.items():
                got = quals.get(role)
                if got != expected_state:
                    failures.append(
                        f"qualification[{role}]: expected {expected_state}, got {got}"
                    )
            metrics["qualification_states_correct"] = (
                all(quals.get(r) == s
                    for r, s in case.expected_qualification.items())
                if case.expected_qualification else None
            )
            if case.expected_fallback_tier:
                got_tier = (compilation.fallback_resolution or {}).get("tier")
                metrics["fallback_tier_correct"] = got_tier == case.expected_fallback_tier
                if got_tier != case.expected_fallback_tier:
                    failures.append(
                        f"fallback tier: expected {case.expected_fallback_tier}, got {got_tier}"
                    )

        if case.check_determinism:
            from app.services.gis_harness.planner import MapProductPlanner
            from app.services.gis_harness.intent import resolve_map_request_intent

            planner2 = MapProductPlanner()
            intent2 = resolve_map_request_intent(case.query)
            plan2 = planner2.plan_from_intent(
                intent2, available_tools=self._tool_names() or None, use_memo=False)
            dump1 = plan.model_dump(mode="json")
            dump2 = plan2.model_dump(mode="json")
            metrics["planning_deterministic"] = dump1 == dump2
            if dump1 != dump2:
                failures.append("planning determinism: double run diverged")
        return failures, metrics

    # ── V2 opt-in tiers（同 _run_v3_contract_tier 规约：零声明零行为，
    #    指标 None = 未声明；失败语义 = 产品语义回归）────────────────────

    def _resolve_policy_inputs(self):
        """Policy tier 共享输入（惰性导入；library 为静态审定资产）。"""
        from app.services.gis_harness.skills.loader import get_skill_library
        from app.services.gis_harness.skills.policy import SkillPolicy

        library = get_skill_library()
        return library, SkillPolicy(library.resolver)

    def _run_policy_tier(
        self, case: GISBenchmarkCase
    ) -> Tuple[List[str], Dict[str, Any]]:
        """SkillPolicy 契约层：SelectionFacts → resolve → 决策子集断言。"""
        failures: List[str] = []
        metrics: Dict[str, Any] = {
            "policy_mode_correct": None,
            "policy_deterministic": None,
        }
        exp = case.policy_expectation
        if exp is None:
            return failures, metrics
        import os

        from app.services.gis_harness.skills.policy import (
            SKILL_POLICY_ENV,
            SkillPolicy,
        )
        from app.services.gis_harness.skills.situation import SelectionFacts

        facts = SelectionFacts(**dict(exp.facts))
        library, _default_policy = self._resolve_policy_inputs()
        shadow_resolver = None
        if exp.shadow_induced:
            from app.evaluation.fixtures import demo_induced_skill
            from app.services.gis_harness.skills.resolver import SkillResolver

            shadow_resolver = SkillResolver(
                [demo_induced_skill()], capability_exists=lambda _c: True,
            )
        policy = SkillPolicy(
            library.resolver,
            shadow_resolver=shadow_resolver,
            quarantine_ids=tuple(exp.quarantine_ids) if exp.quarantine_ids else None,
        )
        kill_saved = os.environ.get(SKILL_POLICY_ENV)
        try:
            if exp.disable_policy:
                os.environ[SKILL_POLICY_ENV] = "0"
            decision = policy.resolve(
                facts,
                prefer_execute=exp.prefer_execute,
                allow_shadow=exp.allow_shadow,
            )
        finally:
            if exp.disable_policy:
                if kill_saved is None:
                    os.environ.pop(SKILL_POLICY_ENV, None)
                else:
                    os.environ[SKILL_POLICY_ENV] = kill_saved
        if exp.expected_mode is not None and decision.mode != exp.expected_mode:
            failures.append(
                f"policy mode: expected {exp.expected_mode}, got {decision.mode} "
                f"(reasons={decision.reasons[:4]}, fallback={decision.fallback_reason!r})"
            )
        if (
            exp.expected_trust_tier is not None
            and decision.trust_tier != exp.expected_trust_tier
        ):
            failures.append(
                f"policy trust tier: expected {exp.expected_trust_tier}, "
                f"got {decision.trust_tier}"
            )
        if (
            exp.expected_selected_skill is not None
            and decision.selected_skill != exp.expected_selected_skill
        ):
            failures.append(
                f"policy skill: expected {exp.expected_selected_skill!r}, "
                f"got {decision.selected_skill!r}"
            )
        if (
            exp.expected_shadow_candidate is not None
            and decision.shadow_candidate != exp.expected_shadow_candidate
        ):
            failures.append(
                f"policy shadow candidate: expected {exp.expected_shadow_candidate!r}, "
                f"got {decision.shadow_candidate!r}"
            )
        bad_modes = sorted(set(exp.forbidden_modes) & {decision.mode})
        if bad_modes:
            failures.append(f"policy forbidden modes present: {bad_modes}")
        declared_contract = bool(
            exp.expected_mode or exp.expected_trust_tier
            or exp.expected_selected_skill or exp.expected_shadow_candidate
            or exp.forbidden_modes
        )
        metrics["policy_mode_correct"] = not failures if declared_contract else None
        if exp.check_determinism:
            decision2 = policy.resolve(
                facts,
                prefer_execute=exp.prefer_execute,
                allow_shadow=exp.allow_shadow,
            )
            metrics["policy_deterministic"] = (
                decision.to_bounded_dict() == decision2.to_bounded_dict()
            )
            if not metrics["policy_deterministic"]:
                failures.append("policy determinism: double resolve diverged")
        return failures, metrics

    def _run_evidence_tier(
        self, case: GISBenchmarkCase
    ) -> Tuple[List[str], Dict[str, Any]]:
        """证据契约层：对每个 ExpectedEvidence 构建确定性场景，断言生产
        verify_claim 的裁决与声明一致（EvidenceGrounding 指标）。"""
        failures: List[str] = []
        metrics: Dict[str, Any] = {
            "evidence_grounding_correct": None,
            "evidence_positive_proof_ok": None,
        }
        if not case.expected_evidence:
            return failures, metrics
        from app.services.gis_harness.evidence_claim.claims import (
            claim_from_statistic,
            narrative_claim_unverified,
        )
        from app.services.gis_harness.evidence_claim.contracts import (
            Claim,
            ClaimStatus,
            ClaimType,
            EvidenceFreshness,
            EvidenceKind,
            EvidenceNode,
            Scope,
        )
        from app.services.gis_harness.evidence_claim.freshness import (
            invalidate_affected_claims,
            mark_evidence_stale,
        )
        from app.services.gis_harness.evidence_claim.contradiction import (
            detect_contradictions,
        )
        from app.services.gis_harness.evidence_claim.store import ClaimStore
        from app.services.gis_harness.evidence_claim.verify import verify_claim

        grounded = 0
        positive_ok = 0
        positive_declared = 0
        scenario_expected_status = {
            "supported": "supported",
            "unsupported": "unsupported",
            "missing_evidence": "unsupported",
            "cross_tenant": "unsupported",
            "stale": "stale",
            "stale_propagation": "stale",
            "contradicted": "contradicted",
        }
        for i, ev in enumerate(case.expected_evidence):
            store = ClaimStore()
            expected_status = scenario_expected_status[ev.scenario]
            label = f"expected_evidence[{i}] ({ev.claim_type}/{ev.scenario})"
            claim = self._build_evidence_scenario(
                ev, store, claim_from_statistic=claim_from_statistic,
                narrative_claim_unverified=narrative_claim_unverified,
                claim_cls=Claim, claim_type_cls=ClaimType,
                evidence_node_cls=EvidenceNode, evidence_kind_cls=EvidenceKind,
                evidence_freshness_cls=EvidenceFreshness, scope_cls=Scope,
                claim_status_cls=ClaimStatus,
            )
            if ev.scenario == "contradicted":
                detect_contradictions(store)
                # 矛盾标记写回 store 内 claim —— store 是事实源，裁决前重取
                # （verify_claim 信任传入对象的 status/contradicting refs）。
                claim = store.get_claim(claim.claim_id) or claim
            elif ev.scenario == "stale_propagation":
                invalidate_affected_claims(store, "ds:bench:v1")
            elif ev.scenario == "stale":
                for eid in claim.supporting_evidence_refs:
                    mark_evidence_stale(store, eid, reason="benchmark_scenario")
            result = verify_claim(claim, store, expected_tenant_id="tenant-a")
            if result.status.value == expected_status:
                grounded += 1
            else:
                failures.append(
                    f"{label}: expected status {expected_status}, "
                    f"got {result.status.value} (reasons={result.reasons[:4]})"
                )
            if ev.require_positive_proof:
                positive_declared += 1
                if result.positive_proof:
                    positive_ok += 1
                else:
                    failures.append(f"{label}: positive proof missing")
        total = len(case.expected_evidence)
        metrics["evidence_grounding_correct"] = grounded == total
        metrics["evidence_positive_proof_ok"] = (
            positive_ok == positive_declared if positive_declared else None
        )
        if grounded != total:
            failures.append(
                f"evidence grounding: {grounded}/{total} verdicts match declarations"
            )
        return failures, metrics

    @staticmethod
    def _build_evidence_scenario(
        ev, store,
        *, claim_from_statistic, narrative_claim_unverified, claim_cls,
        claim_type_cls, evidence_node_cls, evidence_kind_cls,
        evidence_freshness_cls, scope_cls, claim_status_cls,
    ):
        """确定性证据场景构建（公开 API；正例场景 = 完整正证明四件套）。

        scenario 词表：supported / unsupported / stale / contradicted /
        cross_tenant / missing_evidence / stale_propagation。
        """
        scenario = ev.scenario
        if scenario == "unsupported":
            return narrative_claim_unverified(
                ev.subject or "描述性结论未经数值证据",
                tenant_id="tenant-a", session_id="bench", store=store,
            )
        if scenario == "missing_evidence":
            claim = claim_from_statistic(
                subject=ev.subject or "青羊区",
                claim_type=ev.claim_type, value=42.0, unit="所",
                method="admin_aggregation",
                statistic_evidence_id="stat:bench-missing",
                tenant_id="tenant-a", session_id="bench",
            )
            store.upsert_claim(claim)
            return claim
        if scenario == "cross_tenant":
            claim = claim_from_statistic(
                subject=ev.subject or "青羊区",
                claim_type=ev.claim_type, value=42.0, unit="所",
                method="admin_aggregation",
                statistic_evidence_id="stat:bench-xtenant",
                tenant_id="tenant-b", session_id="bench",
            )
            store.upsert_evidence(evidence_node_cls(
                evidence_id="stat:bench-xtenant",
                kind=evidence_kind_cls.STATISTIC, ref="ref:bench-xtenant",
                producer="admin_aggregation",
                freshness=evidence_freshness_cls.FRESH,
                tenant_id="tenant-b", session_id="bench",
                metadata={"stat_type": ev.claim_type, "unit": "所"},
            ))
            store.upsert_claim(claim)
            return claim
        if scenario == "contradicted":
            # 同轴同方法同区域两个 highest（不同 subject）→ 生产管线判硬矛盾
            # （Scope.overlaps：区域不同 = scoped_divergence，非硬矛盾）。
            claims = []
            for k, (subj, val) in enumerate(
                [(ev.subject or "青羊区", 42.0), ("金牛区", 57.0)]
            ):
                stat_id = f"stat:bench-c{k}"
                store.upsert_evidence(evidence_node_cls(
                    evidence_id=stat_id,
                    kind=evidence_kind_cls.STATISTIC, ref=f"ref:bench-c{k}",
                    producer="admin_aggregation",
                    freshness=evidence_freshness_cls.FRESH,
                    tenant_id="tenant-a", session_id="bench",
                    metadata={"stat_type": ev.claim_type},
                ))
                c = claim_from_statistic(
                    subject=subj, claim_type=ev.claim_type, value=val,
                    comparator="highest", method="admin_aggregation",
                    statistic_evidence_id=stat_id,
                    spatial_scope=scope_cls(
                        spatial_name="成都市", spatial_level="city"),
                    tenant_id="tenant-a", session_id="bench",
                )
                store.upsert_claim(c)
                claims.append(c)
            return claims[0]
        if scenario == "stale_propagation":
            # 数据集版本更新 → 后代 claim 必须判 stale（不急重算）。
            store.upsert_evidence(evidence_node_cls(
                evidence_id="ds:bench:v1",
                kind=evidence_kind_cls.DATASET_VERSION, ref="ref:bench-ds",
                version="v1", freshness=evidence_freshness_cls.FRESH,
                tenant_id="tenant-a", session_id="bench",
            ))
            store.upsert_evidence(evidence_node_cls(
                evidence_id="stat:bench-sp",
                kind=evidence_kind_cls.STATISTIC, ref="ref:bench-ds",
                producer="admin_aggregation",
                freshness=evidence_freshness_cls.FRESH,
                tenant_id="tenant-a", session_id="bench",
                metadata={"stat_type": ev.claim_type},
            ))
            claim = claim_from_statistic(
                subject=ev.subject or "青羊区",
                claim_type=ev.claim_type, value=42.0, unit="所",
                method="admin_aggregation",
                statistic_evidence_id="stat:bench-sp",
                dataset_version_evidence_id="ds:bench:v1",
                tenant_id="tenant-a", session_id="bench",
            )
            store.upsert_claim(claim)
            return claim
        # supported / stale：完整正证明四件套（stat + dataset + method +
        # uncertainty），stale 场景随后由调用方将证据标记为过期。
        store.upsert_evidence(evidence_node_cls(
            evidence_id="stat:bench-ok",
            kind=evidence_kind_cls.STATISTIC, ref="ref:bench-stat",
            producer="admin_aggregation",
            freshness=evidence_freshness_cls.FRESH,
            tenant_id="tenant-a", session_id="bench",
            metadata={"stat_type": ev.claim_type, "unit": "所"},
        ))
        store.upsert_evidence(evidence_node_cls(
            evidence_id="ds:bench:v1",
            kind=evidence_kind_cls.DATASET_VERSION, ref="ref:bench-ds",
            version="v1", freshness=evidence_freshness_cls.FRESH,
            tenant_id="tenant-a", session_id="bench",
        ))
        store.upsert_evidence(evidence_node_cls(
            evidence_id="unc:bench-1",
            kind=evidence_kind_cls.UNCERTAINTY, ref="ref:bench-unc",
            freshness=evidence_freshness_cls.FRESH,
            tenant_id="tenant-a", session_id="bench",
        ))
        claim = claim_from_statistic(
            subject=ev.subject or "青羊区",
            claim_type=ev.claim_type, value=42.0, unit="所",
            method="admin_aggregation",
            statistic_evidence_id="stat:bench-ok",
            dataset_version_evidence_id="ds:bench:v1",
            uncertainty_ref="unc:bench-1",
            tenant_id="tenant-a", session_id="bench",
        )
        store.upsert_claim(claim)
        return claim

    def _run_security_tier(
        self, case: GISBenchmarkCase
    ) -> Tuple[List[str], Dict[str, Any]]:
        """注入 contained 契约层：payload 与 benign twin 的能力面对比。

        注入不得扩大能力面（算法集 ⊆ twin）、payload 任务解析不得逃逸
        出合法 GIS 语义（空任务 = 逃逸失败），network 工具由 plan-tier
        forbid_network_tools 与本层描述符复查双保险。
        """
        failures: List[str] = []
        metrics: Dict[str, Any] = {"injection_contained": None}
        exp = case.security_expectation
        if exp is None:
            return failures, metrics
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner import MapProductPlanner

        planner = MapProductPlanner()
        tools = self._tool_names() or None
        twin_intent = resolve_map_request_intent(exp.benign_twin)
        twin_plan = planner.plan_from_intent(
            twin_intent, available_tools=tools, use_memo=False)
        twin_algos = {
            r.algorithm for r in twin_plan.algorithm_selections if r.algorithm
        }
        payload_intent = resolve_map_request_intent(case.query)
        payload_plan = planner.plan_from_intent(
            payload_intent, available_tools=tools, use_memo=False)
        payload_algos = {
            r.algorithm for r in payload_plan.algorithm_selections if r.algorithm
        }
        escalation = sorted(payload_algos - twin_algos)
        if escalation:
            failures.append(
                f"injection escalation: algorithms beyond benign twin {sorted(twin_algos)}: "
                f"{escalation}"
            )
        if not payload_intent.task:
            failures.append(
                "injection escape: payload task resolution empty (semantic escape)"
            )
        if exp.forbid_network_tools:
            registry = self._ensure_registry()
            for name in {r.resolved_tool for r in payload_plan.data_requirements
                         if r.resolved_tool}:
                try:
                    desc = registry.descriptor(name)
                except Exception:  # noqa: BLE001 — 未注册工具按未知处理
                    continue
                if getattr(desc, "network", False):
                    failures.append(
                        f"injection containment broken: network tool resolved: {name}"
                    )
        metrics["injection_contained"] = not failures
        return failures, metrics

    def _run_turns(
        self, case: GISBenchmarkCase
    ) -> Tuple[List[str], Dict[str, Any], Dict[str, Any]]:
        """多轮执行（opt-in：case.turns 非空）。

        顶层 query 即轮 1；``case.turns[i]`` 依序为轮 i+2。轮间共享确定性
        绑定表（scope ← 首个解析成功的 intent.scope.name；subject ← 首个
        非空 subject.category）；后续轮 query 中的 ``{scope}`` / ``{subject}``
        占位符由绑定表替换 —— 指代消解的声明是绑定规则本身，被评测的是
        前序轮真实解析出的前件（前序轮未解析出前件 → 绑定失败即败）。

        返回 (failures, turn_metrics, evidence)。
        """
        failures: List[str] = []
        turn_metrics: Dict[str, Any] = {
            "turns_correct": None,
            "coreference_binding_ok": None,
        }
        evidence: Dict[str, Any] = {"turns": []}
        if not case.turns:
            return failures, turn_metrics, evidence

        from app.services.gis_harness.intent import resolve_map_request_intent

        bindings: Dict[str, str] = {}
        carry_declared = False
        coref_ok = True
        turns_all_ok = True

        # 轮 1 = 顶层 query（断言由 run_case 的 plan tier 负责），此处仅
        # 从其 intent 播种绑定表；case.turns[i] 依序为轮 2..N。
        first_intent = resolve_map_request_intent(case.query)
        first_scope = first_intent.scope.name or ""
        if first_scope:
            bindings["scope"] = first_scope
        first_subject = first_intent.subject.category or ""
        if first_subject:
            bindings["subject"] = first_subject
        last_scope = first_scope
        evidence["turns"].append({
            "turn": 1, "query": case.query, "task": first_intent.task,
            "scope": first_scope, "ok": None,  # 由主 plan tier 判定
        })

        for idx, turn in enumerate(case.turns):
            turn_no = idx + 2
            spec = turn.model_dump()
            raw_query = str(spec.get("query") or "")
            query = raw_query
            for key in ("scope", "subject"):
                token = "{" + key + "}"
                if token in query:
                    bound = bindings.get(key, "")
                    if not bound:
                        failures.append(
                            f"turn {turn_no}: unresolved coreference placeholder "
                            f"{token} (bindings={bindings})"
                        )
                        turns_all_ok = False
                        coref_ok = False
                        query = query.replace(token, "")
                    else:
                        query = query.replace(token, bound)
            intent = resolve_map_request_intent(query)
            scope_name = intent.scope.name or ""
            binding = spec.get("expected_scope_binding")
            if binding is not None:
                carry_declared = True
                if binding == "carry":
                    if not scope_name or scope_name != last_scope:
                        failures.append(
                            f"turn {turn_no}: expected scope carry {last_scope!r}, "
                            f"got {scope_name!r} (query={query!r})"
                        )
                        coref_ok = False
                elif binding == "new":
                    if scope_name and scope_name == last_scope:
                        failures.append(
                            f"turn {turn_no}: expected scope re-bind, still {scope_name!r}"
                        )
                        coref_ok = False
            if scope_name:
                # 最后写入优先：后续 {scope} 指代最近的活跃范围（换绑后
                # 指代随新前件走）—— 与会话语义一致。
                bindings["scope"] = scope_name
                last_scope = scope_name
            subject_category = intent.subject.category or ""
            if subject_category and not bindings.get("subject"):
                bindings["subject"] = subject_category

            # 轮级断言复用 plan tier：子案例只带轮级期望（case 级期望只
            # 约束轮 1，避免后续轮被 case 级契约误判）。
            sub_case = GISBenchmarkCase(
                id=f"{case.id}#t{turn_no}",
                name=f"{case.name} · turn {turn_no}",
                group=case.group,
                query=query,
                plan_only=True,
                expected_task=spec.get("expected_task"),
                expected_tasks=list(spec.get("expected_tasks") or []),
                expected_recipe=spec.get("expected_recipe"),
                expected_recipes=list(spec.get("expected_recipes") or []),
                expected_warning_codes=list(spec.get("expected_warning_codes") or []),
                forbidden_warning_codes=list(spec.get("forbidden_warning_codes") or []),
            )
            turn_failures: List[str]
            turn_evidence: Dict[str, Any]
            turn_evidence, turn_failures = self._run_plan_tier(sub_case)
            turn_evidence.pop("metrics", None)
            if turn_failures:
                turns_all_ok = False
                failures.extend(f"turn {turn_no}: {f}" for f in turn_failures)
            evidence["turns"].append({
                "turn": turn_no,
                "query": query,
                "task": intent.task,
                "scope": scope_name,
                "ok": not turn_failures,
            })
        if carry_declared:
            turn_metrics["coreference_binding_ok"] = coref_ok
        turn_metrics["turns_correct"] = turns_all_ok
        evidence["bindings"] = bindings
        return failures, turn_metrics, evidence

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

            turn_metrics: Dict[str, Any] = {}
            if case.turns:
                turn_failures, turn_metrics, turn_evidence = self._run_turns(case)
                result.plan_evidence["turns"] = turn_evidence
                result.failures.extend(turn_failures)

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
            metrics.update(turn_metrics)
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
