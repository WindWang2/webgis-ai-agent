"""F07：有界 capability-first discovery 契约测试（合成 catalog + 真实目录）。"""
from __future__ import annotations

import time

import pytest

from app.lib.gis.execution_catalog import (
    EXECUTION_CATALOG_VERSION,
    CatalogEntry,
    ExecutionCatalog,
    compile_execution_catalog,
)
from app.lib.gis.execution_catalog_discovery import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    DiscoveryQuery,
    discover,
)


def _entry(kind, eid, **kw):
    return CatalogEntry(kind=kind, id=eid, **kw)


def _catalog(*entries) -> ExecutionCatalog:
    cat = ExecutionCatalog(catalog_version=EXECUTION_CATALOG_VERSION)
    for e in entries:
        cat.entries[e.key] = e
    return cat


def _chain(cap="cap_a", alg="alg_a", tools=("t_a",), priority=50,
           algo_crs_class="", algo_envelope=None, tool_overrides=None):
    """一条健康链（可注入算法/工具声明覆盖；绝不事后改 frozen 条目）。"""
    entries = [_entry("capability", cap)]
    algo_kw = {}
    if algo_crs_class:
        algo_kw["crs_class"] = algo_crs_class
    if algo_envelope:
        algo_kw["resource_envelope"] = algo_envelope
    entries.append(_entry("algorithm", alg, capabilities=(cap,),
                          status="native", priority=priority,
                          detail={"tool_candidates": list(tools)}, **algo_kw))
    for t in tools:
        kw = dict(status="stable", side_effect="pure",
                  detail={"network": False, "result_size_policy": "bounded",
                          "tier": 2})
        if tool_overrides and t in tool_overrides:
            overrides = dict(tool_overrides[t])
            detail_overrides = overrides.pop("detail", {})
            kw.update(overrides)
            kw["detail"] = {**kw["detail"], **detail_overrides}
        entries.append(_entry("tool", t, **kw))
    return entries


def test_query_validation():
    with pytest.raises(ValueError):
        DiscoveryQuery(capabilities=())
    with pytest.raises(ValueError):
        DiscoveryQuery(capabilities=tuple(f"c{i}" for i in range(9)))
    with pytest.raises(ValueError):
        DiscoveryQuery(capabilities=("c",), limit=0)
    with pytest.raises(ValueError):
        DiscoveryQuery(capabilities=("c",), limit=MAX_LIMIT + 1)


def test_happy_path_returns_ordered_chain_with_evidence():
    cat = _catalog(*_chain(alg="alg_low", tools=("t_slow",), priority=80),
                   *_chain(alg="alg_high", tools=("t_fast",), priority=10))
    result = discover(cat, DiscoveryQuery(capabilities=("cap_a",)))
    assert [c.tool for c in result.candidates] == ["t_fast", "t_slow"]
    first = result.candidates[0]
    assert first.reasons and any(
        r.startswith("algorithm_priority:10") for r in first.reasons)
    assert first.evidence["algorithm_priority"] == 10
    assert not result.truncated


def test_offline_filter_excludes_network_tools_with_reason_counts():
    entries = _chain(tools=("t_local",))
    entries += _chain(alg="alg_net", tools=("t_remote",), priority=20,
                      tool_overrides={"t_remote": {"detail": {"network": True}}})
    cat = _catalog(*entries)
    result = discover(cat, DiscoveryQuery(
        capabilities=("cap_a",), offline_required=True))
    assert [c.tool for c in result.candidates] == ["t_local"]
    assert result.excluded.get("excluded_offline_violation") == 1


def test_destructive_and_tier3_excluded_unless_allowed():
    entries = _chain(tools=("t_destructive",),
                     tool_overrides={"t_destructive": {
                         "side_effect": "destructive"}})
    cat = _catalog(*entries)
    blocked = discover(cat, DiscoveryQuery(capabilities=("cap_a",)))
    assert blocked.candidates == []
    assert blocked.excluded.get("excluded_destructive") == 1
    allowed = discover(cat, DiscoveryQuery(
        capabilities=("cap_a",), allow_destructive=True))
    assert [c.tool for c in allowed.candidates] == ["t_destructive"]


def test_resource_envelope_hard_cap_excludes_oversized_data():
    entries = _chain(alg="alg_capped", tools=("t_x",),
                     algo_envelope={"hard_max_features": 1000})
    cat = _catalog(*entries)
    ok = discover(cat, DiscoveryQuery(
        capabilities=("cap_a",), approx_features=500))
    assert len(ok.candidates) == 1
    over = discover(cat, DiscoveryQuery(
        capabilities=("cap_a",), approx_features=5000))
    assert over.candidates == []
    assert over.excluded.get("excluded_resource_envelope") == 1


def test_geographic_crs_data_excludes_projected_required_algorithms():
    entries = _chain(alg="alg_proj", tools=("t_x",),
                     algo_crs_class="PROJECTED_REQUIRED")
    cat = _catalog(*entries)
    result = discover(cat, DiscoveryQuery(
        capabilities=("cap_a",), crs_class="geographic"))
    assert result.candidates == []
    assert result.excluded.get("projected_crs_required") == 1


def test_penalties_are_explicit_in_reasons_and_ordering():
    entries = _chain(alg="alg_plain", tools=("t_plain",), priority=50)
    entries += _chain(alg="alg_depr", tools=("t_dep",), priority=50,
                      tool_overrides={"t_dep": {
                          "status": "deprecated",
                          "superseded_by": "t_plain"}})
    cat = _catalog(*entries)
    result = discover(cat, DiscoveryQuery(capabilities=("cap_a",)))
    dep = next(c for c in result.candidates if c.tool == "t_dep")
    plain = next(c for c in result.candidates if c.tool == "t_plain")
    assert "provider_deprecated" in dep.reasons
    assert dep.score > plain.score
    assert result.candidates[0].tool == "t_plain"


def test_geometry_verdict_three_states_honest():
    """P1-3：几何判定三态 —— 匹配/失配/未声明；无查询几何不产生理由。"""
    entries = _chain(cap="cap_point", alg="alg_p", tools=("t_p",))
    entries[0] = _entry("capability", "cap_point",
                        geometry_requirements=("point",))
    entries += _chain(cap="cap_poly", alg="alg_q", tools=("t_q",))
    entries.append(_entry("capability", "cap_poly",
                          geometry_requirements=("polygon",)))
    entries += _chain(cap="cap_any", alg="alg_r", tools=("t_r",))
    entries.append(_entry("capability", "cap_any"))  # 未声明几何
    cat = _catalog(*entries)
    # 匹配 → matched 理由，无罚分
    matched = discover(cat, DiscoveryQuery(
        capabilities=("cap_point",), geometry="point"))
    assert "geometry_requirements_matched" in matched.candidates[0].reasons
    # 失配 → mismatch 理由 + 软罚（不排除）
    mismatch = discover(cat, DiscoveryQuery(
        capabilities=("cap_point",), geometry="polygon"))
    assert "geometry_requirements_mismatch" in mismatch.candidates[0].reasons
    assert mismatch.candidates[0].score > matched.candidates[0].score
    # 未声明 → undeclared 理由（诚实披露），无罚分
    undeclared = discover(cat, DiscoveryQuery(
        capabilities=("cap_any",), geometry="point"))
    assert "geometry_requirements_undeclared" in undeclared.candidates[0].reasons
    # 查询未带几何 → 不产生任何几何理由
    none = discover(cat, DiscoveryQuery(capabilities=("cap_point",)))
    assert not any(r.startswith("geometry_requirements")
                   for c in none.candidates for r in c.reasons)


def test_offline_unknown_network_disclosed_not_silent():
    """P2-6：offline_required 下 network=None 的工具放行但披露未知。"""
    entries = _chain(tools=("t_undeclared",),
                     tool_overrides={"t_undeclared": {
                         "detail": {"network": None}}})
    cat = _catalog(*entries)
    result = discover(cat, DiscoveryQuery(
        capabilities=("cap_a",), offline_required=True))
    assert [c.tool for c in result.candidates] == ["t_undeclared"]
    assert "offline_network_unknown" in result.candidates[0].reasons
    assert result.candidates[0].evidence["network"] is None


def test_raster_data_uses_cells_envelope():
    """P2-6：栅格查询按 hard_max_cells 判。"""
    entries = _chain(alg="alg_raster", tools=("t_r",),
                     algo_envelope={"hard_max_features": 100,
                                    "hard_max_cells": 1_000_000})
    cat = _catalog(*entries)
    # 栅格口径：5 万像元 < 1M cells 上限 → 放行（features 上限 100 会误杀）
    ok = discover(cat, DiscoveryQuery(
        capabilities=("cap_a",), geometry="raster", approx_features=50_000))
    assert len(ok.candidates) == 1
    over = discover(cat, DiscoveryQuery(
        capabilities=("cap_a",), geometry="raster", approx_features=2_000_000))
    assert over.candidates == []
    # 非栅格口径仍按 features
    vec_over = discover(cat, DiscoveryQuery(
        capabilities=("cap_a",), approx_features=50_000))
    assert vec_over.candidates == []


def test_dedup_across_algorithms_and_capabilities():
    cat = _catalog(
        *_chain(cap="cap_a", alg="alg_a", tools=("shared", "t_a")),
        *_chain(cap="cap_b", alg="alg_b", tools=("shared",), priority=60),
    )
    result = discover(cat, DiscoveryQuery(capabilities=("cap_a", "cap_b")))
    pairs = [(c.capability, c.tool) for c in result.candidates]
    assert len(pairs) == len(set(pairs))
    shared_rows = [p for p in pairs if p[1] == "shared"]
    assert ("cap_a", "shared") in shared_rows


def test_limit_bounded_and_truncation_disclosed():
    tools = tuple(f"t_{i:03d}" for i in range(40))
    cat = _catalog(*_chain(alg="alg_a", tools=tools))
    result = discover(cat, DiscoveryQuery(capabilities=("cap_a",), limit=5))
    assert len(result.candidates) == 5
    assert result.truncated is True
    assert result.excluded.get("truncated_over_limit") == 35
    hard = discover(cat, DiscoveryQuery(
        capabilities=("cap_a",), limit=MAX_LIMIT))
    assert len(hard.candidates) == MAX_LIMIT
    # 纯函数 API 拒绝超限；LLM 工具面负责先钳制（见 catalog_discover）。
    with pytest.raises(ValueError):
        DiscoveryQuery(capabilities=("cap_a",), limit=MAX_LIMIT + 1)


def test_unknown_capability_disclosed_not_raised():
    cat = _catalog(*_chain())
    result = discover(cat, DiscoveryQuery(capabilities=("nope",)))
    assert result.candidates == []
    assert result.excluded.get("capability_not_in_catalog") == 1


def test_deterministic_ordering():
    cat = _catalog(*_chain(alg="alg_x", tools=("t1", "t2", "t3")))
    a = discover(cat, DiscoveryQuery(capabilities=("cap_a",)))
    b = discover(cat, DiscoveryQuery(capabilities=("cap_a",)))
    assert [c.to_dict() for c in a.candidates] == [
        c.to_dict() for c in b.candidates]
    same_priority = discover(
        _catalog(*_chain(alg="alg_a", tools=("t1",), priority=10),
                 *_chain(alg="alg_b", tools=("t2",), priority=10)),
        DiscoveryQuery(capabilities=("cap_a",)))
    assert [c.algorithm for c in same_priority.candidates] == ["alg_a", "alg_b"]


# ── 真实目录冒烟 + 预算 ───────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_catalog():
    return compile_execution_catalog()


def test_real_discovery_admin_aggregation(real_catalog):
    result = discover(real_catalog, DiscoveryQuery(
        capabilities=("admin_aggregation",)))
    assert result.candidates, "admin_aggregation must have executable chain"
    first = result.candidates[0]
    assert first.capability == "admin_aggregation"
    assert first.algorithm and first.tool


def test_real_discovery_latency_budget(real_catalog):
    queries = [
        DiscoveryQuery(capabilities=("admin_aggregation", "poi_distribution")),
        DiscoveryQuery(capabilities=("raster_change_detection",),
                       offline_required=True),
        DiscoveryQuery(capabilities=("admin_boundary_query", "density_estimation",
                                     "spatial_interpolation")),
    ]
    start = time.perf_counter()
    for q in queries:
        discover(real_catalog, q)
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0, f"3 discovery queries took {elapsed:.2f}s"


def test_real_discovery_offline_never_returns_network_tool(real_catalog):
    result = discover(real_catalog, DiscoveryQuery(
        capabilities=tuple(
            e.id for e in real_catalog.entries_of_kind("capability")[:8]),
        offline_required=True,
    ))
    for c in result.candidates:
        tool = real_catalog.get("tool", c.tool)
        assert tool is not None and tool.detail.get("network") is not True
