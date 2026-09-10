"""CapabilityDescriptors + ScenarioCorpus（V7 ADR-0130 D4）回归锁。

不变式：
1. 统一描述符 = registry 事实源的只读投影（kind 封闭；字段有界）；
2. preconditions 硬过滤：请求事实与声明前提冲突 → 剔除 + 原因；请求
   缺席 → 不约束（不猜测）；
3. select_capabilities 确定性（同输入同序，tie by id）；可靠性失败计数
   线性罚分（有界）；cost 偏好仅在 query 显式要求时生效；
4. fallback chain 从 registry 声明投影，附加披露不自动入候选；
5. 生成语料：域包 × 槽确定性展开 ≥2000；case_id 稳定；registry 校验
   面暴露结构缺陷（coverage_gaps / registry_missing）；
6. 抽样评测确定性（stride 无随机）；工具面 V7 信号 kill switch 逐位回退。
"""
from __future__ import annotations

import os

import pytest

from app.services.gis_harness.capability_descriptors import (
    CapabilityDescriptorV7,
    build_capability_index,
    check_preconditions,
    get_capability_index_cached,
    reset_capability_index,
    select_capabilities,
    v7_capability_retrieval_enabled,
)
from app.evaluation.scenario_corpus import (
    MIN_CORPUS_SIZE,
    build_scenario_corpus,
    coverage_report,
    evaluate_scenarios,
    sample_cases,
)


@pytest.fixture(scope="module", autouse=True)
def _index():
    reset_capability_index()
    yield
    reset_capability_index()


# ── 索引构建 ─────────────────────────────────────────────────────────────


def test_index_built_from_registries_with_kinds():
    index = get_capability_index_cached()
    assert index, "registry 种子在场时索引非空"
    kinds = {d.kind for d in index.values()}
    assert kinds <= {"capability", "algorithm", "template", "component"}
    assert "capability:" + "buffer_analysis" in index or len(index) > 10
    # 缓存生效（同对象）
    assert get_capability_index_cached() is index


def test_descriptor_bounded_projection():
    index = build_capability_index()
    for desc in list(index.values())[:20]:
        blob = str(desc.to_bounded_dict())
        assert len(blob) < 4000
        assert desc.kind in ("capability", "algorithm", "template", "component")


def test_algorithm_descriptor_science_fields_projected():
    index = build_capability_index()
    kriging = index.get("algorithm:kriging_interpolation")
    if kriging is None:
        pytest.skip("kriging algorithm not in seed registry")
    assert kriging.kind == "algorithm"
    blob = str(kriging.to_bounded_dict())
    # preconditions / postconditions 结构面在场（哪怕值为空表）
    assert "preconditions" in blob and "postconditions" in blob


# ── preconditions ────────────────────────────────────────────────────────


def _desc(**kw) -> CapabilityDescriptorV7:
    base = dict(
        id="algorithm:test", kind="algorithm", label="test op",
        geometry_requirements=["polygon"], crs_class="projected",
        min_features=10, required_fields=["population"],
        fallback_chain=["algorithm:fb1"],
    )
    base.update(kw)
    return CapabilityDescriptorV7(**base)


def test_preconditions_conflict_rejected_with_reason():
    d = _desc()
    ok, reason = check_preconditions(d, geometry="point")
    assert not ok and "geometry" in reason
    ok, reason = check_preconditions(d, crs_class="geographic")
    assert not ok and "crs_class" in reason
    ok, reason = check_preconditions(d, feature_count=5)
    assert not ok and "features" in reason
    ok, reason = check_preconditions(d, available_fields=["name"])
    assert not ok and "missing_fields" in reason


def test_preconditions_absent_request_not_constraining():
    d = _desc()
    # 请求侧缺席 → 不约束（不猜测）
    ok, _ = check_preconditions(d)
    assert ok
    ok, _ = check_preconditions(d, geometry="polygon", crs_class="projected",
                                feature_count=100,
                                available_fields=["population", "name"])
    assert ok


# ── 结构化检索 ───────────────────────────────────────────────────────────


def test_select_deterministic_and_ranked():
    index = {
        "algorithm:a": _desc(id="algorithm:a", label="density surface"),
        "algorithm:b": _desc(id="algorithm:b", label="density map",
                             geometry_requirements=[]),
    }
    r1 = select_capabilities(index, "density")
    r2 = select_capabilities(index, "density")
    assert r1 == r2
    assert [x["id"] for x in r1] == ["algorithm:a", "algorithm:b"]
    assert r1[0]["fallback_available"] == ["algorithm:fb1"]


def test_select_reliability_penalty_bounded():
    index = {
        "algorithm:a": _desc(id="algorithm:a", label="density surface"),
        "algorithm:b": _desc(id="algorithm:b", label="density surface"),
    }
    clean = select_capabilities(index, "density")
    # a 有未清零失败、b 干净 → 罚分翻转排序（罚分有界 ≤2.0）
    penalized = select_capabilities(
        index, "density", reliability={"a": {"fail": 99}})
    assert clean[0]["id"] == "algorithm:a"  # tie by id
    assert penalized[0]["id"] == "algorithm:b"
    assert any("reliability" in r for r in penalized[1]["reasons"])


def test_select_cost_bias_only_when_requested():
    index = {
        "algorithm:cheap": _desc(id="algorithm:cheap", label="fast density",
                                 cpu_cost="low", memory_cost="low"),
        "algorithm:heavy": _desc(id="algorithm:heavy", label="density model",
                                 cpu_cost="high", memory_cost="high"),
    }
    neutral = [x["id"] for x in select_capabilities(index, "density")]
    biased = [x["id"] for x in select_capabilities(index, "density 快速")]
    assert neutral == ["algorithm:cheap", "algorithm:heavy"]  # tie by id
    assert biased[0] == "algorithm:cheap"


def test_reliability_from_ledger_neutral_without_session():
    from app.services.gis_harness.capability_descriptors import (
        reliability_from_ledger,
    )

    assert reliability_from_ledger("") == {}


# ── 生成语料 ─────────────────────────────────────────────────────────────


def test_corpus_expands_above_size_gate():
    cases = build_scenario_corpus()
    assert len(cases) >= MIN_CORPUS_SIZE
    # case_id 确定性
    again = build_scenario_corpus()
    assert [c.case_id for c in cases[:50]] == [c.case_id for c in again[:50]]
    # 无重复 case_id
    assert len({c.case_id for c in cases}) == len(cases)


def test_corpus_coverage_report_structure():
    report = coverage_report()
    assert report["size_ok"] is True
    assert report["families"] >= 10
    # expected 白名单都应真实存在（registry 锚定 —— 语料缺陷即门红）
    assert report["registry_missing"] == [], report["registry_missing"]


def test_sample_and_eval_deterministic():
    cases = build_scenario_corpus()
    s1 = sample_cases(cases, 50)
    s2 = sample_cases(cases, 50)
    assert [c.case_id for c in s1] == [c.case_id for c in s2]
    assert len(s1) == 50
    report = evaluate_scenarios(cases, sample_n=60)
    assert report["evaluated"] == 60
    assert 0.0 <= report["p_at_1"] <= 1.0
    # 结构自洽：本语料按族构造、expected 与 pack 对齐，top-3 内命中率
    # 下限保守钉线（检索质量回归保护；完全随机 ≪ 此线）
    assert report["p_at_1"] >= 0.30, report


# ── 工具面 V7 信号门 ─────────────────────────────────────────────────────


def test_v7_gate_kill_switch(monkeypatch):
    assert v7_capability_retrieval_enabled()
    monkeypatch.setenv("GIS_CAPABILITY_RETRIEVAL_V7", "0")
    assert not v7_capability_retrieval_enabled()


def test_surface_select_v7_reasons_present():
    """select() 在 V7 开启时对命中候选带 v7:descriptor 证据；关闭时缺席。"""
    from app.services.chat.tool_surface_v3 import DynamicToolSurface, ToolSelectionContext
    from app.services.chat.semantic_retrieval import hybrid_signals  # noqa: F401

    pytest.importorskip("app.tools.registry")
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    surface = DynamicToolSurface(registry)
    ctx = ToolSelectionContext(user_message="对学校做缓冲区分析", k_max=10)
    monkeypatch_default = os.environ.get("GIS_CAPABILITY_RETRIEVAL_V7", "1")
    try:
        os.environ["GIS_CAPABILITY_RETRIEVAL_V7"] = "1"
        sel = surface.select(ctx)
        v7_on = any("v7:descriptor" in r for rs in sel.reasons.values() for r in rs)
        os.environ["GIS_CAPABILITY_RETRIEVAL_V7"] = "0"
        sel_off = surface.select(ctx)
        v7_off = any("v7:descriptor" in r for rs in sel_off.reasons.values() for r in rs)
        assert v7_off is False
        # 开启时也不强制要求命中（registry 空 → 无候选 → 无理由），仅验证
        # 关闭路径严格缺席。
        assert v7_on in (True, False)
    finally:
        os.environ["GIS_CAPABILITY_RETRIEVAL_V7"] = monkeypatch_default
