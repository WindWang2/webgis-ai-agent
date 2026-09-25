"""F07：ExecutionCatalog 投影 / 指纹 / 对账 契约测试。

纯函数面用合成条目；投影面用真实 registry（compile 一次复用）。
"""
from __future__ import annotations

import pytest

from app.lib.gis.execution_catalog import (
    CATALOG_KINDS,
    EXECUTION_CATALOG_VERSION,
    CatalogEntry,
    ExecutionCatalog,
    compile_execution_catalog,
    get_execution_catalog,
    reconcile_with_manifest,
    split_extension_namespace,
)


def _entry(kind, eid, **kw):
    return CatalogEntry(kind=kind, id=eid, **kw)


def _catalog(*entries) -> ExecutionCatalog:
    cat = ExecutionCatalog(catalog_version=EXECUTION_CATALOG_VERSION)
    for e in entries:
        cat.entries[e.key] = e
    from app.lib.gis.execution_catalog import canonical_payload_json
    import hashlib

    payload = {
        "catalog_version": cat.catalog_version,
        "entries": cat.entry_fingerprints(),
    }
    cat.generation_fingerprint = hashlib.sha256(
        canonical_payload_json(payload).encode("utf-8"),
        usedforsecurity=False).hexdigest()[:32]
    return cat


# ── 条目指纹 ──────────────────────────────────────────────────────────


def test_entry_fingerprint_is_content_sensitive_and_stable():
    a = _entry("tool", "t1", version="1.0", side_effect="pure")
    b = _entry("tool", "t1", version="1.0", side_effect="pure")
    c = _entry("tool", "t1", version="1.1", side_effect="pure")
    assert a.fingerprint == b.fingerprint
    assert a.fingerprint != c.fingerprint
    # 词表盐：catalog 版本变化 → 全部指纹变化
    from app.lib.gis.execution_catalog import canonical_payload_json
    payload = dict(a.fingerprint_payload())
    payload["catalog_version"] = EXECUTION_CATALOG_VERSION + 1
    import hashlib

    salted = hashlib.sha256(
        canonical_payload_json(payload).encode("utf-8"),
        usedforsecurity=False).hexdigest()[:32]
    assert salted != a.fingerprint


def test_entry_fingerprint_covers_semantic_fields():
    base = dict(version="1.0")
    a = _entry("algorithm", "a1", crs_class="PROJECTED_REQUIRED", **base)
    b = _entry("algorithm", "a1", crs_class="CRS_AGNOSTIC", **base)
    assert a.fingerprint != b.fingerprint
    c = _entry("algorithm", "a1", unit_requirements="meters", **base)
    d = _entry("algorithm", "a1", unit_requirements="degrees", **base)
    assert c.fingerprint != d.fingerprint
    e = _entry("tool", "t", capabilities=("cap_a",), **base)
    f = _entry("tool", "t", capabilities=("cap_b",), **base)
    assert e.fingerprint != f.fingerprint


# ── provider 归属（证据注入；不猜测）────────────────────────────────


def test_split_namespace_requires_known_namespace_evidence():
    # tool / recipe: {ns}_ 前缀
    assert split_extension_namespace("tool", "acme_foo", ["acme"]) == "acme"
    assert split_extension_namespace("recipe", "acme_bar", ["acme"]) == "acme"
    # 核心前缀不因形状相似而误判
    assert split_extension_namespace("tool", "webgis_map_intent", ["acme"]) is None
    assert split_extension_namespace("tool", "webgis_map_intent", []) is None
    # algorithm: {ns}. 前缀；核心点号 id 不受影响
    assert split_extension_namespace("algorithm", "acme.cluster", ["acme"]) == "acme"
    assert split_extension_namespace(
        "algorithm", "spatial.aggregate.admin", ["acme"]) is None
    # 最长 namespace 优先（防 a 遮蔽 a_b）
    assert split_extension_namespace("tool", "a_b_tool", ["a", "a_b"]) == "a_b"


def test_certification_projection_honesty():
    from app.lib.gis.execution_catalog import (
        _certification_projection,
        PROVIDER_CORE,
        PROVIDER_DYNAMIC,
    )

    core = _certification_projection(None, {})
    assert core["provider_kind"] == PROVIDER_CORE
    assert core["certified"] is True
    dyn = _certification_projection(None, {}, is_dynamic=True)
    assert dyn["provider_kind"] == PROVIDER_DYNAMIC
    unknown = _certification_projection("ghostns", {})
    assert unknown["provider_kind"] == "unknown"
    assert unknown["certified"] is False
    certified = _certification_projection(
        "acme", {"acme": {"state": "valid", "certified": True, "signed": True}})
    assert certified["certified"] is True
    stale = _certification_projection(
        "acme", {"acme": {"state": "stale", "certified": False, "signed": False}})
    assert certified["certified"] != stale["certified"]


# ── tool_candidates_for_capability（capability-first 解析视图）───────


def test_tool_candidates_ordered_by_algorithm_priority_and_filtered():
    cat = _catalog(
        _entry("capability", "cap_a"),
        _entry("algorithm", "alg_low", capabilities=("cap_a",),
               priority=80, status="native",
               detail={"tool_candidates": ["t_low"]}),
        _entry("algorithm", "alg_high", capabilities=("cap_a",),
               priority=10, status="native",
               detail={"tool_candidates": ["t_high", "t_low"]}),
        _entry("tool", "t_high", status="stable"),
        _entry("tool", "t_low", status="stable"),
        _entry("tool", "t_hidden", status="hidden"),
        _entry("algorithm", "alg_planned", capabilities=("cap_a",),
               priority=1, status="planned",
               detail={"tool_candidates": ["t_hidden"]}),
    )
    assert cat.tool_candidates_for_capability("cap_a") == ["t_high", "t_low"]
    # include_non_executable 展示 planned 链
    with_all = cat.tool_candidates_for_capability(
        "cap_a", include_non_executable=True)
    assert "t_hidden" in with_all


# ── 真实 registry 投影 ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_catalog():
    return compile_execution_catalog()


def test_real_catalog_width_and_shape(real_catalog):
    s = real_catalog.summary()
    assert s["counts"]["tool"] > 100
    assert s["counts"]["recipe"] > 50
    assert s["counts"]["algorithm"] > 50
    assert s["counts"]["capability"] > 50
    for kind in CATALOG_KINDS:
        assert real_catalog.entries_of_kind(kind)


def test_real_catalog_determinism(real_catalog):
    again = compile_execution_catalog()
    assert again.generation_fingerprint == real_catalog.generation_fingerprint
    assert again.entry_fingerprints() == real_catalog.entry_fingerprints()


def test_real_catalog_tool_candidates_match_algorithm_registry(real_catalog):
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    ar = get_algorithm_registry()
    checked = 0
    for algo_id in ar.all_ids:
        algo = ar.get(algo_id)
        if algo.runtime_status != "native" or not algo.capabilities:
            continue
        cap = algo.capabilities[0]
        tools = real_catalog.tool_candidates_for_capability(cap)
        for t in algo.tool_candidates:
            te = real_catalog.get("tool", t)
            if te is not None and te.status not in ("planned", "hidden"):
                assert t in tools, (
                    f"registered executable candidate {t} of {algo_id} "
                    f"missing from catalog chain of {cap}")
                checked += 1
    assert checked > 0


def test_singleton_get_execution_catalog():
    cat = get_execution_catalog()
    assert get_execution_catalog() is cat


# ── 对账（catalog ↔ runtime_manifest）───────────────────────────────


def test_reconcile_with_real_manifest_has_no_id_mismatch():
    from app.lib.gis.runtime_manifest import compile_runtime_manifest

    catalog = compile_execution_catalog()
    manifest = compile_runtime_manifest()
    issues = reconcile_with_manifest(catalog, manifest)
    id_mismatch = [i for i in issues if i.code == "catalog_manifest_id_mismatch"]
    # 同一批权威 registry 两面投影：不应存在单面条目（除已知的
    # hidden/planned 可见面差异 —— manifest 投影 metadata()/all_metadata()
    # 覆盖全部注册工具，理论上一致；若有差异必须在测试里显性暴露）。
    assert id_mismatch == [], f"unexpected: {[i.to_dict() for i in id_mismatch[:8]]}"


def test_reconcile_detects_field_divergence():
    catalog = _catalog(
        _entry("capability", "cap_a", version="1.0", status="native"),
        _entry("tool", "t1", version="2.0", status="stable",
               capabilities=("cap_a",)),
    )

    class FakeManifest:
        capabilities = {"cap_a": {"version": "1.0", "status": "native"}}
        algorithms = {}
        tools = {"t1": {"version": "9.9", "status": "stable",
                        "capabilities": ["cap_a"]}}
        recipes = {}

    issues = reconcile_with_manifest(catalog, FakeManifest())
    divergence = [i for i in issues if i.code == "catalog_manifest_field_divergence"]
    assert any(i.entry_id == "t1" and "version" in i.detail for i in divergence)


def test_reconcile_tolerates_non_manifest_shape():
    catalog = _catalog(_entry("capability", "cap_a"))
    issues = reconcile_with_manifest(catalog, object())
    assert len(issues) == 1
    assert issues[0].code == "catalog_manifest_id_mismatch"
