"""Benchmark Manifest / Registry（V2；GOAL 里程碑 1：统一清单）。

全语料登记簿：每个语料以 ``(prefix, builder, version_hash)`` 注册，
提供：

- ``corpus_manifest()``：名称 → {count, version_hash, group}（确定性序）；
- ``iter_all_cases()``：跨语料枚举全部 ``GISBenchmarkCase``；
- ``write_manifest(path)`` / ``load_manifest(path)``：机器清单落盘。

``version_hash`` = sha256(该语料全部案例的规范化 JSON 串) —— 期望表任何
内容漂移（不止 id/数量）都会改变哈希，给 CI 一个便宜的漂移信号。
注册表为模块级纯数据：导入即注册（无 I/O、无网络、确定性）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional

from app.evaluation.case import GISBenchmarkCase


@dataclass(frozen=True)
class CorpusRegistration:
    """一个语料的登记项。"""

    name: str
    prefix: str  # 案例 id 前缀（跨语料唯一性锚）
    builder: Callable[[], List[Any]]
    #: 案例是否为 GISBenchmarkCase（True → 进入 iter_all_cases）
    is_benchmark_case: bool = True
    #: 报告聚合的组名（非 case 语料由驱动器决定组名）
    group: str = ""


_REGISTRY: List[CorpusRegistration] = []


def register(corpus: CorpusRegistration) -> None:
    """登记一个语料（幂等：同名重复注册 = 覆盖）。"""
    global _REGISTRY
    _REGISTRY = [c for c in _REGISTRY if c.name != corpus.name]
    _REGISTRY.append(corpus)
    _REGISTRY.sort(key=lambda c: c.name)


def _all_registrations() -> List[CorpusRegistration]:
    _ensure_builtin_registrations()
    return list(_REGISTRY)


def _ensure_builtin_registrations() -> None:
    if _REGISTRY:
        return
    from app.evaluation import (
        anti_claim,
        case_matrix,
        conformance,
        evidence_corpus,
        failure_corpus,
        goal_satisfaction_corpus,
        hard_negative_corpus,
        methodology_corpus,
        quality_corpus,
        reliability_corpus,
        retrieval_eval_corpus,
        runtime_corpus,
        scenario_corpus,
        security_corpus,
        skill_policy_corpus,
    )
    from app.evaluation.cartography_axes_corpus import (
        build_cartography_axes_corpus,
    )
    from app.evaluation.mission_corpus import build_mission_corpus

    register(CorpusRegistration(
        name="anti_claim_plan", prefix="AC-",
        builder=anti_claim.build_anti_claim_plan_cases, group="anti-claim"))
    register(CorpusRegistration(
        name="workflow_contract", prefix="WC-",
        builder=anti_claim.build_workflow_contract_cases,
        is_benchmark_case=False, group="anti-claim"))
    register(CorpusRegistration(
        name="golden_matrix", prefix="GIS-",
        builder=case_matrix.build_matrix_cases, group="form"))
    register(CorpusRegistration(
        name="conformance", prefix="CF-",
        builder=lambda: conformance.build_conformance_corpus(),
        group="conformance"))
    register(CorpusRegistration(
        name="evidence_grounding", prefix="EV-",
        builder=evidence_corpus.build_evidence_corpus,
        group="benchmark-evidence"))
    register(CorpusRegistration(
        name="failure_taxonomy", prefix="FL-",
        builder=failure_corpus.build_failure_corpus,
        is_benchmark_case=False, group="reliability"))
    register(CorpusRegistration(
        name="goal_satisfaction", prefix="GS-",
        builder=goal_satisfaction_corpus.build_goal_satisfaction_cases,
        is_benchmark_case=False, group="goal"))
    register(CorpusRegistration(
        name="hard_negative", prefix="HN-",
        builder=hard_negative_corpus.build_hard_negative_corpus,
        group="hard-negative"))
    register(CorpusRegistration(
        name="methodology", prefix="MT-",
        builder=lambda: list(methodology_corpus.corpus_cases()),
        is_benchmark_case=False, group="semantics"))
    register(CorpusRegistration(
        name="mission_scenario", prefix="MSN-",
        builder=build_mission_corpus,
        is_benchmark_case=False, group="benchmark-mission"))
    register(CorpusRegistration(
        name="cartography_axes", prefix="CARTX-",
        builder=build_cartography_axes_corpus,
        is_benchmark_case=False, group="cartography-axes"))
    register(CorpusRegistration(
        name="quality_scenario", prefix="QS-",
        builder=quality_corpus.build_quality_scenario_corpus,
        group="quality"))
    register(CorpusRegistration(
        name="reliability", prefix="RL-",
        builder=reliability_corpus.build_reliability_corpus,
        is_benchmark_case=False, group="reliability"))
    register(CorpusRegistration(
        name="retrieval_eval", prefix="RT-",
        builder=retrieval_eval_corpus.build_retrieval_eval_corpus,
        is_benchmark_case=False, group="retrieval"))
    register(CorpusRegistration(
        name="runtime_situation", prefix="RUNTIME-",
        builder=runtime_corpus.build_runtime_corpus,
        is_benchmark_case=False, group="runtime"))
    register(CorpusRegistration(
        name="security_injection", prefix="SEC-",
        builder=security_corpus.build_security_corpus,
        group="benchmark-security"))
    register(CorpusRegistration(
        name="skill_policy", prefix="SP-",
        builder=skill_policy_corpus.build_skill_policy_corpus,
        group="benchmark-policy"))
    register(CorpusRegistration(
        name="scenario", prefix="SC-",
        builder=lambda: scenario_corpus.build_scenario_corpus(
            registry_validate=False),
        is_benchmark_case=False, group="scenario"))


def corpus_version_hash(cases: List[Any]) -> str:
    """语料内容指纹（sha256；期望表任何内容漂移都会改变哈希）。"""
    digest = hashlib.sha256(usedforsecurity=False)
    for case in sorted(cases, key=lambda c: str(getattr(c, "id", "")
                                                or getattr(c, "case_id", "")
                                                or getattr(c, "scenario_id", ""))):
        if isinstance(case, GISBenchmarkCase):
            blob = case.model_dump_json()
        elif hasattr(case, "model_dump_json"):
            blob = case.model_dump_json()
        else:
            try:
                blob = json.dumps(case, ensure_ascii=False, sort_keys=True,
                                  default=repr)
            except TypeError:
                blob = repr(case)
        digest.update(blob.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:32]


def corpus_manifest() -> Dict[str, Dict[str, Any]]:
    """名称 → {count, version_hash, prefix, group}（确定性序；构建即守卫）。"""
    manifest: Dict[str, Dict[str, Any]] = {}
    seen_prefixes: Dict[str, str] = {}
    for reg in _all_registrations():
        cases = reg.builder()
        # 跨语料 id 唯一性只对 GISBenchmarkCase 语料强制 —— 专用管线语料
        # （mission/failure/…）的 id 字段语义各异，由各自构建期守卫负责。
        if reg.is_benchmark_case:
            ids = [
                str(getattr(c, "id", "") or getattr(c, "case_id", "")
                    or getattr(c, "scenario_id", ""))
                for c in cases
            ]
            if len(ids) != len(set(ids)):
                raise AssertionError(f"corpus {reg.name} has duplicate ids")
            for cid in ids:
                owner = seen_prefixes.setdefault(reg.prefix, reg.name)
                if owner != reg.name:
                    raise AssertionError(
                        f"id prefix {reg.prefix!r} claimed by both {owner} "
                        f"and {reg.name}"
                    )
        manifest[reg.name] = {
            "count": len(cases),
            "version_hash": corpus_version_hash(cases),
            "prefix": reg.prefix,
            "group": reg.group,
        }
    return manifest


def iter_all_cases() -> Iterator[GISBenchmarkCase]:
    """跨语料枚举全部 GISBenchmarkCase（确定性序：语料名 → 案例 id）。"""
    for reg in _all_registrations():
        if not reg.is_benchmark_case:
            continue
        cases = [c for c in reg.builder() if isinstance(c, GISBenchmarkCase)]
        yield from sorted(cases, key=lambda c: c.id)


def write_manifest(path: Any, manifest: Optional[Dict[str, Any]] = None) -> None:
    """机器清单落盘（确定性 JSON；供基线 diff / CI 漂移检查）。"""
    blob = manifest if manifest is not None else corpus_manifest()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(blob, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def load_manifest(path: Any) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
