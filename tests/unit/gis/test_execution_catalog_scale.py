"""F07：宽度 / 性能预算 —— 数百 registry 条目下有界、可缓存、确定性。

合成目录（400 tools / 300 algorithms / 300 recipes / 150 capabilities）上
验证：conformance 校验、discovery 查询、staleness diff 的耗时与产出规模
均有界；并发查询无共享可变态。
"""
from __future__ import annotations

import time

import pytest

from app.lib.gis.execution_catalog import (
    EXECUTION_CATALOG_VERSION,
    CatalogEntry,
    ExecutionCatalog,
    compile_execution_catalog,
    reconcile_with_manifest,
)
from app.lib.gis.execution_catalog_conformance import (
    MAX_FINDINGS,
    validate_execution_catalog_conformance,
)
from app.lib.gis.execution_catalog_discovery import (
    MAX_LIMIT,
    DiscoveryQuery,
    discover,
)
from app.lib.gis.execution_catalog_staleness import (
    catalog_snapshot_ref,
    diff_snapshots,
    explain_staleness,
)

N_CAPS = 150
N_ALGOS = 300
N_TOOLS = 400
N_RECIPES = 300


def _synthetic_catalog() -> ExecutionCatalog:
    cat = ExecutionCatalog(catalog_version=EXECUTION_CATALOG_VERSION)
    for c in range(N_CAPS):
        e = CatalogEntry(kind="capability", id=f"cap_{c:03d}")
        cat.entries[e.key] = e
    for a in range(N_ALGOS):
        cap = f"cap_{a % N_CAPS:03d}"
        e = CatalogEntry(
            kind="algorithm", id=f"alg_{a:03d}", capabilities=(cap,),
            status="native", priority=a % 100,
            detail={"tool_candidates": [
                f"t_{a:03d}", f"t_{(a + 1) % N_TOOLS:03d}"]})
        cat.entries[e.key] = e
    for t in range(N_TOOLS):
        e = CatalogEntry(
            kind="tool", id=f"t_{t:03d}", status="stable",
            side_effect="pure",
            capabilities=(f"cap_{t % N_CAPS:03d}",),
            resource_class={"latency": "fast", "memory": "light",
                            "scale": "small"},
            detail={"network": False, "result_size_policy": "bounded",
                    "tier": 2})
        cat.entries[e.key] = e
    for r in range(N_RECIPES):
        e = CatalogEntry(
            kind="recipe", id=f"r_{r:03d}",
            capabilities=(f"cap_{r % N_CAPS:03d}",))
        cat.entries[e.key] = e
    from app.lib.gis.execution_catalog import canonical_payload_json
    import hashlib

    cat.generation_fingerprint = hashlib.sha256(
        canonical_payload_json(
            {"catalog_version": cat.catalog_version,
             "entries": cat.entry_fingerprints()}).encode("utf-8"),
        usedforsecurity=False).hexdigest()[:32]
    return cat


@pytest.fixture(scope="module")
def big_catalog():
    return _synthetic_catalog()


def test_synthetic_width_is_as_declared(big_catalog):
    counts = big_catalog.counts()
    assert counts == {"capability": N_CAPS, "algorithm": N_ALGOS,
                      "tool": N_TOOLS, "recipe": N_RECIPES}


def test_conformance_scales_linearly_and_bounded(big_catalog):
    start = time.perf_counter()
    issues = validate_execution_catalog_conformance(big_catalog)
    elapsed = time.perf_counter() - start
    # 1150 合成条目（健康链）：校验必须有界耗时（预算 8s ≫ 预期 ~1s）
    assert elapsed < 8.0, f"conformance took {elapsed:.2f}s"
    assert len(issues) <= MAX_FINDINGS


def test_discovery_query_budget_and_hard_limit(big_catalog):
    caps = tuple(f"cap_{c:03d}" for c in range(8))
    start = time.perf_counter()
    result = discover(big_catalog, DiscoveryQuery(capabilities=caps))
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"discovery took {elapsed:.3f}s"
    assert len(result.candidates) <= MAX_LIMIT
    assert result.truncated is True  # 8 caps × 2 algos × 2 tools = 32 > 16


def test_staleness_full_snapshot_budget(big_catalog):
    snap = catalog_snapshot_ref(big_catalog)
    start = time.perf_counter()
    res = explain_staleness(snap, big_catalog)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"full snapshot explain took {elapsed:.2f}s"
    assert res["stale"] is False
    # 变更 20 条 → diff 有界归因
    mutated = _synthetic_catalog()
    keys = sorted(mutated.entries.keys())[:20]
    for k in keys:
        old = mutated.entries[k]
        mutated.entries[k] = CatalogEntry(
            kind=old.kind, id=old.id, version="9.9")
    start = time.perf_counter()
    res2 = explain_staleness(snap, mutated)
    elapsed2 = time.perf_counter() - start
    assert elapsed2 < 2.0, f"mutated explain took {elapsed2:.2f}s"
    assert res2["stale"] is True
    assert res2["summary"]["changed_total"] == 20


def test_generation_fingerprint_stable_across_synthetic_rebuild():
    assert _synthetic_catalog().generation_fingerprint == \
        _synthetic_catalog().generation_fingerprint


def test_concurrent_discovery_reads_are_isolated(big_catalog):
    import concurrent.futures

    queries = [
        DiscoveryQuery(capabilities=(f"cap_{c % N_CAPS:03d}",))
        for c in range(16)
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(
            lambda q: discover(big_catalog, q).to_dict(), queries))
    for i, (q, res) in enumerate(zip(queries, results)):
        assert res == discover(big_catalog, q).to_dict(), (
            f"concurrent read {i} diverged from serial result")


def test_real_compile_time_budget():
    start = time.perf_counter()
    catalog = compile_execution_catalog()
    elapsed = time.perf_counter() - start
    assert catalog.counts()["tool"] > 100
    assert elapsed < 60.0, f"real compile took {elapsed:.1f}s"


def test_reconcile_budget_on_synthetic(big_catalog):
    class FakeManifest:
        capabilities = {f"cap_{c:03d}": {"version": "1.0", "status": "native"}
                        for c in range(N_CAPS)}
        algorithms = {}
        tools = {}
        recipes = {}

    issues = reconcile_with_manifest(big_catalog, FakeManifest())
    # 300 algos + 400 tools + 300 recipes 全部缺席 → 大量 mismatch（有界披露）
    assert len(issues) <= 128
    assert all(i.code == "catalog_manifest_id_mismatch" for i in issues)
