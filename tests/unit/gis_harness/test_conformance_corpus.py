"""Conformance Corpus（C9）+ Anti-Claim（C10）+ Scenarios（C11）回归锁。

不变式：
- 语料规模 ≥20000（59 语义族 × 语言 × 12 scope × 9 句式），确定性生成；
- 语义身份不变量：同族所有表述同 task/recipe —— 全量运行挂 ``perf``
  标记（unfiltered 运行自跳过，#664），默认车道跑确定性分层抽样 + 领域
  切片（资源红线：20k 全量 ≈ 数分钟，不得进默认门）；
- anti-claim 反声明契约（分母/准则/受体/显著性语义分界）零失败；
- workflow 契约案例（义务联动/降级/阻断/verdict V2）零失败；
- 全部 V2 recipe（147）可完整编译（registry 覆盖烟测）；
- 7 个代表性端到端场景全绿。
"""
from __future__ import annotations

import pytest

from app.evaluation.anti_claim import (
    build_anti_claim_plan_cases,
    build_workflow_contract_cases,
    build_v2_recipe_coverage_sweep,
    run_workflow_contract_case,
)
from app.evaluation.conformance import (
    CONFORMANCE_FAMILIES,
    build_conformance_corpus,
)


@pytest.mark.asyncio
async def test_corpus_size_and_determinism():
    cases = build_conformance_corpus()
    assert len(cases) >= 20000, f"conformance corpus must stay >= 20000, got {len(cases)}"
    again = build_conformance_corpus()
    assert [c.id for c in cases] == [c.id for c in again]
    assert [c.query for c in cases] == [c.query for c in again]
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))


@pytest.mark.perf
@pytest.mark.asyncio
async def test_corpus_full_run_green():
    """全量语义一致性（≈20k 案例，数分钟级）：unfiltered 运行自跳过。

    显式运行：pytest -m perf tests/unit/gis_harness/test_conformance_corpus.py
    任一失败 = 产品语义回归（task/recipe/能力/警告码）。
    """
    from app.evaluation.runner import GISBenchmarkRunner

    cases = build_conformance_corpus()
    results = await GISBenchmarkRunner().run(cases)
    failed = [(r.case_id, r.failures) for r in results if not r.passed]
    assert failed == [], f"{len(failed)} semantic regressions: {failed[:5]}"


@pytest.mark.asyncio
async def test_corpus_stratified_sample_green():
    """默认车道：确定性分层抽样（每族语言 × scope × 句式各取代表）。

    抽样规则确定性（cases 已按 id 排序，步长切片零随机）——语义身份
    不变量的抽样面覆盖全部 59 族；全量契约由 perf 标记的完整运行锁定。
    """
    from app.evaluation.runner import GISBenchmarkRunner

    cases = build_conformance_corpus()
    sample = cases[::37]           # 20088 / 37 ≈ 543 案例切片
    assert len(sample) >= 400
    results = await GISBenchmarkRunner().run(sample)
    failed = [(r.case_id, r.failures) for r in results if not r.passed]
    assert failed == [], f"{len(failed)} regressions in sample: {failed[:5]}"


@pytest.mark.asyncio
async def test_corpus_domain_slice_green():
    """分片运行（CI 友好）：领域切片同样零失败（V3 扩容族所在片）。"""
    from app.evaluation.runner import GISBenchmarkRunner

    cases = build_conformance_corpus(
        domains=["terrain", "hydrology", "network", "statistics"])
    assert cases, "slice must exist"
    results = await GISBenchmarkRunner().run(cases)
    failed = [r.case_id for r in results if not r.passed]
    assert failed == []


def test_corpus_covers_all_pack_domains():
    """语义族必须覆盖全部 24 个领域包（知识库的 NL 契约面不分空白）。"""
    family_domains = {f.domain for f in CONFORMANCE_FAMILIES}
    from app.services.gis_harness.recipe_packs import PACK_MODULES

    missing = set(PACK_MODULES) - family_domains
    assert missing == set(), f"domains without conformance families: {sorted(missing)}"


@pytest.mark.asyncio
async def test_anti_claim_plan_cases_green():
    """C10 反声明（规划期）：无分母/无准则/无受体的披露必须随 plan 下行。"""
    from app.evaluation.runner import GISBenchmarkRunner

    cases = build_anti_claim_plan_cases()
    results = await GISBenchmarkRunner().run(cases)
    failed = [(r.case_id, r.failures) for r in results if not r.passed]
    assert failed == [], failed


def test_workflow_contract_cases_green():
    """C4/C6/C7 契约层：义务状态/降级/阻断/verdict V2 全部按契约发生。"""
    cases = build_workflow_contract_cases()
    assert len(cases) >= 10
    for case in cases:
        result = run_workflow_contract_case(case)
        assert result.passed, f"{result.case_id}: {result.failures}"


def test_verdict_v2_blocked_by_method_despite_perfect_render():
    """C10 红线锁：方法不成立时，完美渲染的合成面也不得 READY。"""
    from app.evaluation.anti_claim import run_workflow_contract_case
    from app.evaluation.anti_claim import build_workflow_contract_cases

    case = next(
        c for c in build_workflow_contract_cases()
        if c.case_id == "WC-kriging-blocked-no-field"
    )
    result = run_workflow_contract_case(case)
    assert result.passed, result.failures


def test_v2_recipe_compilation_coverage():
    """registry 覆盖：全部 V2 专业 recipe 必须能通过 12 阶段编译。"""
    from app.services.gis_harness.workflow_compiler import compile_workflow

    recipe_ids = build_v2_recipe_coverage_sweep()
    assert len(recipe_ids) >= 140, f"V2 recipes: {len(recipe_ids)}"
    for rid in recipe_ids:
        compilation = compile_workflow(
            f"执行 {rid} 专业工作流", recipe_id=rid,
            profile={"featureCount": 60, "geometryTypes": ["Point"],
                     "fields": {"value": {"type": "number"},
                                "population": {"type": "number"}}},
        )
        assert len(compilation.stages) == 15, f"{rid}: stages {len(compilation.stages)}"
        blocked = [s for s in compilation.stages if s.status == "blocked"]
        # 编译blocked 仅允许来自义务阻断（科学诚实），不允许编译器自身失败
        for s in blocked:
            assert s.reason_codes != ["PLAN_GRAPH_BUILD_FAILED"], (
                f"{rid}: compiler DAG failure"
            )


@pytest.mark.asyncio
async def test_scenarios_all_green():
    """C11：7 个代表性端到端场景（plan + execute + contract 三层）。"""
    from app.evaluation.scenarios import build_scenarios, run_scenario

    scenarios = build_scenarios()
    assert len(scenarios) >= 7
    for scenario in scenarios:
        result = await run_scenario(scenario)
        assert result.passed, f"{scenario.scenario_id}: {result.failures}"
