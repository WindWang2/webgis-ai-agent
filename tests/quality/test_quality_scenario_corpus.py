"""Quality Scenario Corpus（Wave 2+3）回归锁。

不变式：
- 语料规模：参考场景（reference）= **去重后的 (family_id, data_state) 契约
  组合**（conformance 族 × 12 数据状态，冻结表逐对断言 fallback tier /
  qualification 角色状态图）+ 制图 / agent 新族参考场景；≥500。
  总量（含参考场景的确定性表述扩展）≥5000；
- 确定性：两次构建 id 与查询完全一致；构建期反垃圾护栏（id 唯一、
  (family, state) 查询唯一、每族 profile 变体 ≤ 64）；
- 全量回放零失败：按 scenario_kind 前缀分片（basemap / science /
  cartography / data / agent），每片实测 ≪ 60s 测试超时；
- 新 DSL 字段（Wave 2+3）：语料覆盖（≥1 生成案例携带）+ 逐字段「必须
  触发」测试（手构案例对真实 registry 断言失败信息）。
"""
from __future__ import annotations

import time

import pytest

from app.evaluation.case import GISBenchmarkCase
from app.evaluation.conformance import CONFORMANCE_FAMILIES
from app.evaluation.quality_corpus import (
    AGENT_FAMILIES,
    CARTOGRAPHY_FAMILIES,
    DATA_STATE_PROFILES,
    build_quality_scenario_corpus,
    quality_corpus_reference_count,
)

#: scenario_kind 前缀 → 测试分片（全语料不相交划分）。
SCENARIO_PREFIXES = ("basemap_", "science_", "cartography_", "data_", "agent_")


def _slice_by_prefix(cases, prefix):
    return [c for c in cases if (c.scenario_kind or "").startswith(prefix)]


def test_reference_and_total_counts():
    """reference = 去重 (family_id, data_state) 契约对；定义见模块 docstring。"""
    cases = build_quality_scenario_corpus()
    ref_pairs = set()
    for c in cases:
        if c.qualification_profile is not None:
            # 参考场景 id 形如 QS-<family_id>-<state_id>（state_id 无连字符）
            family_id, state_id = c.id[len("QS-"):].rsplit("-", 1)
            ref_pairs.add((family_id, state_id))
    ref_pairs |= {(f.family_id, "nominal") for f in (*CARTOGRAPHY_FAMILIES, *AGENT_FAMILIES)}
    assert len(ref_pairs) >= 500, f"reference pairs: {len(ref_pairs)}"
    assert len(cases) >= 5000, f"total cases: {len(cases)}"
    assert quality_corpus_reference_count() == (
        len(CONFORMANCE_FAMILIES) * len(DATA_STATE_PROFILES)
        + len(CARTOGRAPHY_FAMILIES) + len(AGENT_FAMILIES)
    )
    # id 唯一 + 分片前缀完备（不相交划分）
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))
    kinds = {c.scenario_kind for c in cases}
    assert all(any(k.startswith(p) for p in SCENARIO_PREFIXES) for k in kinds)


def test_build_determinism_and_anti_garbage():
    """两次构建完全一致；反垃圾护栏成立。"""
    first = build_quality_scenario_corpus()
    second = build_quality_scenario_corpus()
    assert [c.id for c in first] == [c.id for c in second]
    assert [c.query for c in first] == [c.query for c in second]
    # 每族 profile 变体 ≤ 64
    assert len(DATA_STATE_PROFILES) <= 64
    # (family, state) 参考查询唯一
    ref_queries = [
        c.query for c in first if c.qualification_profile is not None
    ]
    assert len(ref_queries) == len(set(ref_queries))


@pytest.mark.parametrize("prefix", ["basemap_", "science_"])
@pytest.mark.asyncio
async def test_slice_geometry_states_green(prefix):
    """几何态分片（basemap / science）全量回放零失败。"""
    from app.evaluation.runner import GISBenchmarkRunner

    cases = [c for c in build_quality_scenario_corpus()
             if (c.scenario_kind or "").startswith(prefix)]
    assert cases, f"slice {prefix} must exist"
    started = time.monotonic()
    results = await GISBenchmarkRunner().run(cases)
    elapsed = time.monotonic() - started
    failed = [(r.case_id, r.failures) for r in results if not r.passed]
    assert failed == [], f"{len(failed)} failures: {failed[:5]}"
    assert elapsed < 45, f"slice {prefix} took {elapsed:.1f}s (budget 45s)"


@pytest.mark.asyncio
async def test_slice_data_states_green():
    """数据状态分片（data_*，59 族 × 8 语义数据状态）全量回放零失败。"""
    from app.evaluation.runner import GISBenchmarkRunner

    cases = _slice_by_prefix(build_quality_scenario_corpus(), "data_")
    assert len(cases) >= 59 * 8, f"data slice: {len(cases)}"
    started = time.monotonic()
    results = await GISBenchmarkRunner().run(cases)
    elapsed = time.monotonic() - started
    failed = [(r.case_id, r.failures) for r in results if not r.passed]
    assert failed == [], f"{len(failed)} failures: {failed[:5]}"
    assert elapsed < 45, f"data slice took {elapsed:.1f}s (budget 45s)"


@pytest.mark.asyncio
async def test_slice_cartography_and_agent_green():
    """制图 / agent 分片全量回放零失败（agent 为 spec-labeled，见模块说明）。"""
    from app.evaluation.runner import GISBenchmarkRunner

    cases = (
        _slice_by_prefix(build_quality_scenario_corpus(), "cartography_")
        + _slice_by_prefix(build_quality_scenario_corpus(), "agent_")
    )
    assert len(cases) >= len(CARTOGRAPHY_FAMILIES) + len(AGENT_FAMILIES)
    results = await GISBenchmarkRunner().run(cases)
    failed = [(r.case_id, r.failures) for r in results if not r.passed]
    assert failed == [], failed


def test_dsl_field_coverage_in_corpus():
    """每个新 DSL 字段至少被 1 个生成案例携带。"""
    cases = build_quality_scenario_corpus()

    def any_case(pred):
        return any(pred(c) for c in cases)

    assert any_case(lambda c: bool(c.scenario_kind))
    assert any_case(lambda c: bool(c.expected_tool_classes))
    assert any_case(lambda c: bool(c.expected_export_formats))
    assert any_case(lambda c: c.max_context_schema_bytes is not None)
    assert any_case(lambda c: c.forbid_network_tools)
    assert any_case(lambda c: bool(c.trace_requirements))
    assert any_case(lambda c: c.qualification_profile is not None)
    assert any_case(lambda c: bool(c.expected_qualification))
    assert any_case(lambda c: c.expected_fallback_tier is not None)
    # 工具类别词表必须来自真实 descriptor（防虚构词表）
    from app.evaluation.runner import GISBenchmarkRunner

    reg = GISBenchmarkRunner()._ensure_registry()
    vocabulary = {
        d.output_semantic_type for d in
        (reg.descriptor(t) for t in reg.list_tools())
        if d.output_semantic_type
    }
    used = {cls for c in cases for cls in c.expected_tool_classes}
    assert used <= vocabulary, f"fictional tool classes: {used - vocabulary}"


# ── 逐字段「必须触发」测试（对真实 registry；负向契约）─────────────────────

def _run_one(case: GISBenchmarkCase):
    from app.evaluation.runner import GISBenchmarkRunner

    import asyncio

    return asyncio.get_event_loop().run_until_complete(
        GISBenchmarkRunner().run_case(case))


@pytest.mark.asyncio
async def test_field_fires_max_context_schema_bytes():
    """预算=1 必须触发 context schema 失败（真实 schema 字节数 > 0）。"""
    from app.evaluation.runner import GISBenchmarkRunner

    case = GISBenchmarkCase(
        id="fire-schema-budget", name="t", group="quality-data",
        query="医院的分布情况", plan_only=True, max_context_schema_bytes=1,
    )
    result = await GISBenchmarkRunner().run_case(case)
    assert not result.passed
    assert any("context schema" in f for f in result.failures), result.failures
    assert result.plan_evidence.get("context_schema_bytes", 0) > 1


@pytest.mark.asyncio
async def test_field_fires_forbid_network_tools():
    """离线契约遇到 network 工具必须触发失败（query_local_poi network=True）。"""
    from app.evaluation.runner import GISBenchmarkRunner

    case = GISBenchmarkCase(
        id="fire-forbid-network", name="t", group="quality-data",
        query="医院的分布情况", plan_only=True, forbid_network_tools=True,
    )
    result = await GISBenchmarkRunner().run_case(case)
    assert not result.passed
    assert any("network tool forbidden" in f for f in result.failures), result.failures


@pytest.mark.asyncio
async def test_field_fires_expected_tool_classes():
    """虚构工具类别必须触发覆盖缺口失败。"""
    from app.evaluation.runner import GISBenchmarkRunner

    case = GISBenchmarkCase(
        id="fire-tool-classes", name="t", group="quality-data",
        query="医院的分布情况", plan_only=True,
        expected_tool_classes=["point_cloud"],
    )
    result = await GISBenchmarkRunner().run_case(case)
    assert not result.passed
    assert any("tool classes" in f for f in result.failures), result.failures


@pytest.mark.asyncio
async def test_field_fires_expected_export_formats():
    """计划不提供的导出格式必须触发子集断言失败。"""
    from app.evaluation.runner import GISBenchmarkRunner

    case = GISBenchmarkCase(
        id="fire-export-formats", name="t", group="quality-cartography",
        query="医院的分布情况", plan_only=True,
        expected_export_formats=["csv"],
    )
    result = await GISBenchmarkRunner().run_case(case)
    assert not result.passed
    assert any("export formats" in f for f in result.failures), result.failures


@pytest.mark.asyncio
async def test_field_records_trace_requirements():
    """trace 需求原样落入 plan evidence（后续 trace wave 的消费面）。"""
    from app.evaluation.runner import GISBenchmarkRunner

    case = GISBenchmarkCase(
        id="record-trace-reqs", name="t", group="quality-data",
        query="医院的分布情况", plan_only=True,
        trace_requirements=["plan.task", "plan.resolved_tools"],
    )
    result = await GISBenchmarkRunner().run_case(case)
    assert result.passed, result.failures
    assert result.plan_evidence.get("trace_requirements") == [
        "plan.task", "plan.resolved_tools"]
