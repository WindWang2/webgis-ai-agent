"""F07：目录级 conformance 纯函数校验器契约测试（合成 catalog）。

负例驱动：每个错误码都有「注入缺陷 → 精确命中」用例；真实 registry 的
基线行为（0 fatal）由 test_real_catalog_has_no_fatal 锁定。
"""
from __future__ import annotations

import hashlib

import pytest

from app.lib.gis.execution_catalog import (
    EXECUTION_CATALOG_VERSION,
    CatalogEntry,
    ExecutionCatalog,
    compile_execution_catalog,
)
from app.lib.gis.execution_catalog_conformance import (
    CODE_CAPABILITY_DANGLING,
    CODE_DEPRECATED_NO_SUCCESSOR,
    CODE_DEPRECATED_PROVIDER_ONLY,
    CODE_EXTENSION_CONTRACT_INCOMPLETE,
    CODE_FALLBACK_DANGLING,
    CODE_OUTPUT_CONTRACT_MISMATCH,
    CODE_RECIPE_CAPABILITY_UNREACHABLE,
    CODE_UNIT_GEOMETRY_MISMATCH,
    MAX_FINDINGS,
    facet_gaps,
    validate_execution_catalog_conformance,
)


def _entry(kind, eid, **kw):
    return CatalogEntry(kind=kind, id=eid, **kw)


def _catalog(*entries) -> ExecutionCatalog:
    cat = ExecutionCatalog(catalog_version=EXECUTION_CATALOG_VERSION)
    for e in entries:
        cat.entries[e.key] = e
    return cat


def _codes(cat, *args, **kw):
    return [i.code for i in validate_execution_catalog_conformance(cat, *args, **kw)]


def test_healthy_chain_has_no_catalog_issues():
    cat = _catalog(
        _entry("capability", "cap_a", status="native"),
        _entry("algorithm", "alg_a", capabilities=("cap_a",), status="native",
               priority=10, detail={"tool_candidates": ["t_a"]}),
        _entry("tool", "t_a", capabilities=("cap_a",), status="stable",
               side_effect="pure",
               detail={"network": False, "result_size_policy": "bounded",
                       "tier": 2}),
    )
    issues = validate_execution_catalog_conformance(cat)
    catalog_codes = {i.code for i in issues
                     if i.code.startswith("catalog_")}
    assert catalog_codes == set(), f"unexpected: {catalog_codes}"


def test_dangling_capability_is_fatal_at_every_layer():
    base_tool = _entry("tool", "t_ok", status="stable", side_effect="pure",
                       detail={"network": False, "result_size_policy": "bounded",
                               "tier": 2})
    cat = _catalog(
        base_tool,
        _entry("algorithm", "alg_bad", capabilities=("ghost_cap",),
               status="native", detail={"tool_candidates": ["t_ok"]}),
        _entry("tool", "t_bad", capabilities=("ghost_cap",), status="stable"),
        _entry("recipe", "r_bad", capabilities=("ghost_cap",)),
    )
    issues = validate_execution_catalog_conformance(
        cat, chain_capability_conformance=False)
    hits = [i for i in issues if i.code == CODE_CAPABILITY_DANGLING]
    assert {i.kind for i in hits} == {"algorithm", "tool", "recipe"}
    assert all(i.severity == "fatal" for i in hits)


def test_dangling_fallback_and_successor_are_fatal():
    cat = _catalog(
        _entry("capability", "cap_a"),
        _entry("algorithm", "alg_a", capabilities=("cap_a",),
               fallback_targets=("ghost_alg",)),
        _entry("tool", "t_dep", status="deprecated", superseded_by="ghost_tool"),
        _entry("recipe", "r_a", fallback_targets=("ghost_recipe",)),
    )
    issues = validate_execution_catalog_conformance(
        cat, chain_capability_conformance=False)
    codes = [i.code for i in issues]
    assert CODE_FALLBACK_DANGLING in codes
    assert "catalog_deprecation_target_dangling" in codes
    fatal = [i for i in issues if i.severity == "fatal"]
    assert fatal, "dangling references must be fatal"


def test_native_algorithm_with_unregistered_tools_is_warning():
    cat = _catalog(
        _entry("capability", "cap_a"),
        _entry("algorithm", "alg_a", capabilities=("cap_a",), status="native",
               detail={"tool_candidates": ["missing_tool"]}),
    )
    issues = validate_execution_catalog_conformance(
        cat, chain_capability_conformance=False)
    hits = [i for i in issues
            if i.code == "catalog_algorithm_tool_missing"]
    assert hits and hits[0].severity == "warning"


def test_recipe_unreachable_capability_is_warning():
    cat = _catalog(
        _entry("capability", "cap_orphan", status="native"),
        # 无任何算法实现 → 不可达
        _entry("recipe", "r_a", capabilities=("cap_orphan",)),
    )
    issues = validate_execution_catalog_conformance(
        cat, chain_capability_conformance=False)
    hits = [i for i in issues if i.code == CODE_RECIPE_CAPABILITY_UNREACHABLE]
    assert len(hits) == 1
    assert (hits[0].kind, hits[0].entry_id, hits[0].peer) == (
        "recipe", "r_a", "cap_orphan")


def test_output_contract_undeclared_only_when_no_candidate_declares():
    from app.lib.gis.artifacts import get_artifact_type_registry

    reg = get_artifact_type_registry()
    out_type = reg.all_ids[0] if hasattr(reg, "all_ids") else ""
    if not out_type:
        pytest.skip("artifact registry empty")
    declaring = _entry("tool", "t_decl", status="stable",
                       output_semantic_types=("geojson_fc",))
    silent = _entry("tool", "t_silent", status="stable")
    cat_with = _catalog(
        _entry("capability", "cap_a"),
        _entry("algorithm", "alg_a", capabilities=("cap_a",), status="native",
               output_semantic_types=(out_type,),
               detail={"tool_candidates": ["t_decl"]}),
        declaring,
    )
    cat_without = _catalog(
        _entry("capability", "cap_a"),
        _entry("algorithm", "alg_a", capabilities=("cap_a",), status="native",
               output_semantic_types=(out_type,),
               detail={"tool_candidates": ["t_silent"]}),
        silent,
    )
    assert [i for i in validate_execution_catalog_conformance(
        cat_with, chain_capability_conformance=False)
        if i.code == CODE_OUTPUT_CONTRACT_MISMATCH] == []
    hits = [i for i in validate_execution_catalog_conformance(
        cat_without, chain_capability_conformance=False)
        if i.code == CODE_OUTPUT_CONTRACT_MISMATCH]
    assert len(hits) == 1


def test_unit_family_conflict_detected_only_when_both_declared():
    cat_conflict = _catalog(
        _entry("capability", "cap_a"),
        _entry("algorithm", "alg_a", capabilities=("cap_a",), status="native",
               unit_requirements="meters",
               detail={"tool_candidates": ["t_deg"]}),
        _entry("tool", "t_deg", status="stable", unit_semantics="degrees"),
    )
    cat_no_conflict = _catalog(
        _entry("capability", "cap_a"),
        _entry("algorithm", "alg_a", capabilities=("cap_a",), status="native",
               unit_requirements="meters",
               detail={"tool_candidates": ["t_m"]}),
        _entry("tool", "t_m", status="stable", unit_semantics="meters"),
    )
    cat_undeclared = _catalog(
        _entry("capability", "cap_a"),
        _entry("algorithm", "alg_a", capabilities=("cap_a",), status="native",
               unit_requirements="meters",
               detail={"tool_candidates": ["t_x"]}),
        _entry("tool", "t_x", status="stable"),  # unit_semantics 缺席 → 不判
    )
    def _hits(cat):
        return [i for i in validate_execution_catalog_conformance(
            cat, chain_capability_conformance=False)
            if i.code == CODE_UNIT_GEOMETRY_MISMATCH]
    assert len(_hits(cat_conflict)) == 1
    assert _hits(cat_no_conflict) == []
    assert _hits(cat_undeclared) == []


def test_crs_conflict_projected_required_vs_geography_bound_tool():
    cat = _catalog(
        _entry("capability", "cap_a"),
        _entry("algorithm", "alg_a", capabilities=("cap_a",), status="native",
               crs_class="PROJECTED_REQUIRED",
               detail={"tool_candidates": ["t_wgs"]}),
        _entry("tool", "t_wgs", status="stable", crs_semantics="wgs84"),
    )
    issues = validate_execution_catalog_conformance(
        cat, chain_capability_conformance=False)
    assert any(i.code == CODE_UNIT_GEOMETRY_MISMATCH for i in issues)


def test_deprecated_without_successor_warns():
    cat = _catalog(
        _entry("capability", "cap_a"),
        _entry("algorithm", "alg_old", capabilities=("cap_a",),
               status="native", deprecated=True,
               detail={"tool_candidates": ["t_x"],
                       "scientific_status": "DEPRECATED"}),
        _entry("tool", "t_dep", status="deprecated"),  # 无 superseded_by
    )
    issues = validate_execution_catalog_conformance(
        cat, chain_capability_conformance=False)
    hits = [i for i in issues if i.code == CODE_DEPRECATED_NO_SUCCESSOR]
    assert {i.kind for i in hits} == {"algorithm", "tool"}


def test_capability_served_only_by_deprecated_algorithms_warns():
    cat = _catalog(
        _entry("capability", "cap_a"),
        _entry("algorithm", "alg_old", capabilities=("cap_a",),
               status="native", deprecated=True,
               detail={"tool_candidates": ["t_x"],
                       "scientific_status": "DEPRECATED"}),
        _entry("tool", "t_x", status="stable"),
    )
    issues = validate_execution_catalog_conformance(
        cat, chain_capability_conformance=False)
    assert any(i.code == CODE_DEPRECATED_PROVIDER_ONLY for i in issues)


# ── 扩展认证契约（7 facet）────────────────────────────────────────────


def test_facet_gaps_apply_only_to_extension_entries():
    core = _entry("tool", "t_core", certification={"provider_kind": "core"})
    assert facet_gaps(core) == []
    ext = _entry(
        "tool", "t_ext", name="Acme Tool",
        certification={"provider_kind": "extension", "namespace": "acme"})
    gaps = facet_gaps(ext)
    assert "schema" in gaps            # 未声明 output_semantic_type
    assert "side_effects" in gaps      # 未声明 side_effect
    assert "metadata" not in gaps      # name/version 已投影
    anon = _entry("tool", "t_anon",
                  certification={"provider_kind": "extension",
                                 "namespace": "acme"})
    assert "metadata" in facet_gaps(anon)  # 无 name → 身份元数据缺口


def test_extension_contract_incomplete_warning_emitted():
    cat = _catalog(
        _entry("tool", "t_ext", certification={"provider_kind": "extension",
                                               "namespace": "acme"}),
        _entry("tool", "t_core", certification={"provider_kind": "core"}),
    )
    issues = validate_execution_catalog_conformance(
        cat, chain_capability_conformance=False)
    hits = [i for i in issues if i.code == CODE_EXTENSION_CONTRACT_INCOMPLETE]
    assert len(hits) == 1 and hits[0].entry_id == "t_ext"


# ── 确定性与有界 ──────────────────────────────────────────────────────


def test_validation_is_deterministic_and_bounded():
    entries = []
    for i in range(600):
        entries.append(_entry("tool", f"t_{i:04d}",
                              capabilities=(f"ghost_{i}",), status="stable"))
    cat = _catalog(*entries)
    a = validate_execution_catalog_conformance(cat)
    b = validate_execution_catalog_conformance(cat)
    assert [i.to_dict() for i in a] == [i.to_dict() for i in b]
    assert len(a) <= MAX_FINDINGS


def test_real_catalog_has_no_fatal_on_clean_baseline():
    """与启动 strict 闸同基线：干净 registry 上 catalog 级引用必须零 fatal。"""
    catalog = compile_execution_catalog()
    issues = validate_execution_catalog_conformance(catalog)
    fatal = [i for i in issues if i.severity == "fatal"]
    assert fatal == [], f"unexpected fatal: {[i.to_dict() for i in fatal[:8]]}"
