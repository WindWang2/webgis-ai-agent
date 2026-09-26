"""F07：staleness 快照 / 精确解释 契约测试。"""
from __future__ import annotations

import pytest

from app.lib.gis.execution_catalog import (
    EXECUTION_CATALOG_VERSION,
    CatalogEntry,
    ExecutionCatalog,
    compile_execution_catalog,
)
from app.lib.gis.execution_catalog_staleness import (
    MAX_CHANGED_ENTRIES,
    REASON_ENTRY_CONTENT_CHANGED,
    REASON_ENTRY_REMOVED,
    REASON_SNAPSHOT_CORRUPT,
    REASON_SNAPSHOT_SCHEMA_UNKNOWN,
    catalog_snapshot_ref,
    diff_snapshots,
    explain_staleness,
    supersession_map,
    validate_snapshot_ref,
)


def _entry(kind, eid, **kw):
    return CatalogEntry(kind=kind, id=eid, **kw)


def _catalog(*entries) -> ExecutionCatalog:
    cat = ExecutionCatalog(catalog_version=EXECUTION_CATALOG_VERSION)
    for e in entries:
        cat.entries[e.key] = e
    from app.lib.gis.execution_catalog import canonical_payload_json
    import hashlib

    cat.generation_fingerprint = hashlib.sha256(
        canonical_payload_json(
            {"catalog_version": cat.catalog_version,
             "entries": cat.entry_fingerprints()}).encode("utf-8"),
        usedforsecurity=False).hexdigest()[:32]
    return cat


def _healthy_chain(entries_added=()):
    return [
        _entry("capability", "cap_a"),
        _entry("algorithm", "alg_a", capabilities=("cap_a",),
               status="native", detail={"tool_candidates": ["t_a"]}),
        _entry("tool", "t_a", status="stable"),
        *entries_added,
    ]


def test_full_snapshot_same_catalog_not_stale():
    cat = _catalog(*_healthy_chain())
    snap = catalog_snapshot_ref(cat)
    res = explain_staleness(snap, cat)
    assert res["stale"] is False and res["reasons"] == []


def test_partial_snapshot_ignores_unrelated_changes():
    cat = _catalog(*_healthy_chain(entries_added=(
        _entry("tool", "t_unrelated", status="stable"),)))
    snap = catalog_snapshot_ref(cat, entry_keys=["tool:t_a"])
    assert snap["scope"] == "partial"
    res = explain_staleness(snap, cat)
    assert res["stale"] is False, res["reasons"]


def test_partial_snapshot_detects_exactly_the_changed_entry():
    cat_before = _catalog(*_healthy_chain(entries_added=(
        _entry("recipe", "r_x", status="native"),)))
    snap = catalog_snapshot_ref(
        cat_before, entry_keys=["tool:t_a", "recipe:r_x"])
    # 演进后的目录：t_a 内容变化 + r_x 消失 + 无关新条目
    cat_after = _catalog(*_healthy_chain(entries_added=(
        _entry("tool", "t_a", status="deprecated", superseded_by="t_new"),
        _entry("tool", "t_new", status="stable"),
        _entry("tool", "t_unrelated", status="stable"),
    )))
    res = explain_staleness(snap, cat_after)
    assert res["stale"] is True
    assert REASON_ENTRY_CONTENT_CHANGED in res["reasons"]
    assert REASON_ENTRY_REMOVED in res["reasons"]
    assert "entry_added" not in [r for r in res["reasons"]]
    assert res["diff"]["changed"] == ["tool:t_a"]
    assert res["diff"]["removed"] == ["recipe:r_x"]
    assert res["diff"]["added"] == []
    # 消费者归因：t_a 的直接消费者是引用它的算法
    assert res["affected_consumers"].get("tool:t_a") == ["algorithm:alg_a"]


def test_full_snapshot_reports_added_and_generation():
    cat_before = _catalog(*_healthy_chain())
    snap = catalog_snapshot_ref(cat_before)
    cat_after = _catalog(*_healthy_chain(entries_added=(
        _entry("tool", "t_new", status="stable"),)))
    res = explain_staleness(snap, cat_after)
    assert res["stale"] is True
    assert "entry_added" in res["reasons"]
    assert "catalog_generation_changed" in res["reasons"]
    assert res["summary"]["changed_total"] == 1


def test_corrupt_and_unknown_snapshots_not_judged_stale():
    """与 manifest.is_stale_plan 同诚实规则：不可判读 → 不判 stale，
    只披露（review P1-2）。"""
    cat = _catalog(*_healthy_chain())
    corrupt = explain_staleness(
        {"schema_version": 1, "generation_fingerprint": "short"}, cat)
    assert corrupt["stale"] is False
    assert corrupt["reasons"] == [REASON_SNAPSHOT_CORRUPT]
    assert corrupt["diff"] == {}
    assert "not judged stale" in corrupt["summary"]["note"]
    unknown = explain_staleness(
        {"schema_version": 99, "generation_fingerprint": "a" * 32}, cat)
    assert unknown["stale"] is False
    assert unknown["reasons"] == [REASON_SNAPSHOT_SCHEMA_UNKNOWN]
    assert unknown["summary"]["note"]


def test_validate_snapshot_ref_shape_rules():
    assert validate_snapshot_ref({"schema_version": 1,
                                  "generation_fingerprint": "a" * 32,
                                  "entry_fingerprints": {}}) is None
    assert validate_snapshot_ref("not-a-mapping") == REASON_SNAPSHOT_CORRUPT
    assert validate_snapshot_ref({"schema_version": 1,
                                  "generation_fingerprint": "a" * 32,
                                  "entry_fingerprints": [1, 2]}) == \
        REASON_SNAPSHOT_CORRUPT


def test_manifest_generation_change_is_reported_even_without_entry_diff():
    cat = _catalog(*_healthy_chain())
    snap = catalog_snapshot_ref(cat, manifest_fingerprint="a" * 64)
    res = explain_staleness(snap, cat, current_manifest_fingerprint="b" * 64)
    assert res["stale"] is True
    assert "manifest_generation_changed" in res["reasons"]


def test_diff_snapshots_and_bounding():
    old = {f"tool:t{i}": "x" for i in range(MAX_CHANGED_ENTRIES + 10)}
    new = dict(old)
    new["tool:zzz"] = "x"
    for i in range(MAX_CHANGED_ENTRIES + 5):
        new[f"tool:t{i}"] = "y"
    diff = diff_snapshots(old, new)
    assert diff.added == ["tool:zzz"]
    assert diff.changed[0] == "tool:t0"
    assert len(diff.changed) == MAX_CHANGED_ENTRIES + 5
    bounded = diff.bounded()
    assert len(bounded.changed) == MAX_CHANGED_ENTRIES
    assert diff.truncated_total() == 6


def test_supersession_map_requires_known_successor_truthfully():
    cat = _catalog(
        _entry("tool", "t_old", status="deprecated", superseded_by="t_new"),
        _entry("tool", "t_new", status="stable"),
        _entry("algorithm", "alg_old", deprecated=True,
               fallback_targets=("ghost_alg",),
               detail={"scientific_status": "DEPRECATED"}),
    )
    sup = supersession_map(cat)
    assert sup["tool:t_old"]["successor"] == "t_new"
    assert sup["tool:t_old"]["successor_known"] is True
    assert sup["algorithm:alg_old"]["successor"] == "ghost_alg"
    assert sup["algorithm:alg_old"]["successor_known"] is False


def test_real_catalog_snapshot_roundtrip():
    catalog = compile_execution_catalog()
    snap = catalog_snapshot_ref(
        catalog, entry_keys=["tool:spatial_aggregate"])
    res = explain_staleness(snap, catalog)
    assert res["stale"] is False
    # 全量快照往返
    full = catalog_snapshot_ref(catalog)
    assert explain_staleness(full, catalog)["stale"] is False
