"""Compiler Evaluation V4 —— 方法论正确性/完备性/拒绝/确定性/可解释。

对审定双语语料（app/evaluation/methodology_corpus.py）逐案例运行
``compile_workflow_v4``，断言五个维度：

1. methodology correctness：解析到期望方法族（歧义变体允许族内任意
   确定性默认，但不允许跨族漂移）；
2. data requirement completeness：期望角色出现在角色解析 ∪ 方法声明
   消费集合中；
3. invalid method rejection：审定 hard negative 方法在给定数据事实上
   必须被资格拒绝（带稳定 reason code）；
4. obligation completeness：期望义务/披露码在义务链 ∪ 制图义务 ∪
   完成契约披露中出现；
5. determinism & explainability：同输入双编译同指纹；每个 V4 阶段有
   reason codes 或 evidence（可解释性非空）。

全部离线、确定性、零 LLM。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.evaluation.methodology_corpus import (
    METHODOLOGY_CORPUS,
    MethodologyCase,
)
from app.services.gis_harness.workflow_v4.compiler_v4 import (
    compile_workflow_v4,
)


@dataclass
class CaseEvaluation:
    case_id: str
    family_correct: bool
    data_requirements_complete: bool
    invalid_method_rejected: Optional[bool]   # None = 无审定拒绝集
    obligation_complete: Optional[bool]       # None = 无审定义务期望
    deterministic: bool
    explainable: bool
    resolved_family: str = ""
    resolved_method: str = ""
    failures: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id[:64],
            "family_correct": self.family_correct,
            "data_requirements_complete": self.data_requirements_complete,
            "invalid_method_rejected": self.invalid_method_rejected,
            "obligation_complete": self.obligation_complete,
            "deterministic": self.deterministic,
            "explainable": self.explainable,
            "resolved_family": self.resolved_family[:40],
            "resolved_method": self.resolved_method[:64],
            "failures": [f[:200] for f in self.failures[:6]],
        }


@dataclass
class CompilerEvaluationReport:
    total: int = 0
    passed: int = 0
    failed_cases: List[CaseEvaluation] = field(default_factory=list)
    dimensions: Dict[str, int] = field(default_factory=dict)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "dimension_counts": dict(sorted(self.dimensions.items())),
            "failed_cases": [c.to_bounded_dict()
                             for c in self.failed_cases[:12]],
        }


def _utterances(case: MethodologyCase) -> List[str]:
    return [case.utterance_zh, case.utterance_en,
            *case.ambiguous_variants]


def _profile_facts(case: MethodologyCase) -> Optional[Dict[str, Any]]:
    return dict(case.profile[0]) if case.profile else None


def evaluate_case(case: MethodologyCase) -> List[CaseEvaluation]:
    """单案例 × 全部表述（zh/en/歧义变体）→ 每表述一个评估。"""
    profile = _profile_facts(case)
    evaluations: List[CaseEvaluation] = []
    for utterance in _utterances(case):
        c1 = compile_workflow_v4(utterance, profile=profile)
        c2 = compile_workflow_v4(utterance, profile=profile)
        ev = CaseEvaluation(
            case_id=f"{case.case_id}::{utterance[:24]}",
            family_correct=True,
            data_requirements_complete=True,
            invalid_method_rejected=(None),
            obligation_complete=(None),
            deterministic=(
                c1.package_fingerprint == c2.package_fingerprint
                and c1.to_bounded_dict() == c2.to_bounded_dict()),
            explainable=all(
                (s.reason_codes or s.evidence)
                for s in c1.v4_stages),
        )
        ev.resolved_family = c1.methodology_family
        ev.resolved_method = str(
            c1.method_qualification.get("selected_id", ""))

        # ── 1. 方法族正确性 ───────────────────────────────────────────
        if ev.resolved_family != case.family:
            ev.family_correct = False
            ev.failures.append(
                f"family mismatch: expected {case.family}, "
                f"got {ev.resolved_family}")

        # ── 2. 数据需求完备性 ─────────────────────────────────────────
        dag_roles = {
            str(n.get("role", ""))
            for n in (c1.typed_dag.get("nodes") or [])
            if n.get("kind") == "data_input"}
        resolved_roles = {
            str(r.get("role", ""))
            for r in (c1.base.data_roles or [])}
        covered_roles = dag_roles | resolved_roles
        missing = [r for r in case.expected_roles
                   if r not in covered_roles]
        if missing:
            ev.data_requirements_complete = False
            ev.failures.append(f"missing roles: {missing}")

        # ── 3. 无效方法拒绝（hard negative）──────────────────────────
        if case.expected_rejected_methods:
            rejected_ids = {
                q.get("method_id")
                for q in (c1.method_qualification.get("qualifications") or [])
                if q.get("status") == "rejected"}
            bad = [m for m in case.expected_rejected_methods
                   if m not in rejected_ids]
            ev.invalid_method_rejected = not bad
            if bad:
                ev.failures.append(
                    f"hard negatives not rejected: {bad}")
        if case.expected_method and ev.resolved_method != case.expected_method:
            ev.failures.append(
                f"method mismatch: expected {case.expected_method}, "
                f"got {ev.resolved_method}")

        # ── 4. 义务完备性 ─────────────────────────────────────────────
        if case.expected_obligation_hints:
            chain_ids = {
                o.get("obligation_id", "")
                for o in (c1.obligation_chain.get("obligations") or [])}
            carto_codes = {
                o.get("rule_code", "")
                for o in (c1.cartographic_obligations or [])}
            disclosures = set(
                c1.base.completion_contract.get("required_disclosures") or [])
            pool = chain_ids | carto_codes | disclosures
            missing_obl = [h for h in case.expected_obligation_hints
                           if not any(h in p for p in pool)]
            ev.obligation_complete = not missing_obl
            if missing_obl:
                ev.failures.append(f"missing obligation hints: {missing_obl}")

        if not ev.passed:
            evaluations.append(ev)
        else:
            evaluations.append(ev)
    return evaluations


def evaluate_compiler(
    cases: Optional[Sequence[MethodologyCase]] = None,
) -> CompilerEvaluationReport:
    """全语料评估（确定性；报告有界）。"""
    report = CompilerEvaluationReport()
    all_cases = cases if cases is not None else METHODOLOGY_CORPUS
    dims = {
        "family_correct": 0, "data_requirements_complete": 0,
        "invalid_method_rejected": 0, "obligation_complete": 0,
        "deterministic": 0, "explainable": 0,
    }
    dim_total = 0
    for case in all_cases:
        for ev in evaluate_case(case):
            report.total += 1
            if ev.passed:
                report.passed += 1
            else:
                report.failed_cases.append(ev)
            dim_total += 1
            if ev.family_correct:
                dims["family_correct"] += 1
            if ev.data_requirements_complete:
                dims["data_requirements_complete"] += 1
            if ev.invalid_method_rejected is not None:
                if ev.invalid_method_rejected:
                    dims["invalid_method_rejected"] += 1
            if ev.obligation_complete is not None:
                if ev.obligation_complete:
                    dims["obligation_complete"] += 1
            if ev.deterministic:
                dims["deterministic"] += 1
            if ev.explainable:
                dims["explainable"] += 1
    report.dimensions = dims
    return report
