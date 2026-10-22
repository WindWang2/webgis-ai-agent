"""G01/WP-B6 恢复链语义对账失败注入测试（_reconcile_descriptor_semantics）。

验收矩阵（端到端 valid/recompute 场景在 tests/data/
test_project_runtime_v1.py::test_descriptor_semantics_reconciled_on_resume）：
- 损坏 descriptor：迁移失败计数（不吞），有记录面锚 → unknown 披露；
- ref 无语义（老会话/淘汰）：无记录面锚 → 无对账（不虚构期望）；
- 记录面锚在但语义缺席 → unknown（不知道 ≠ 没变）；
- legacy source（无 descriptor_fingerprint）→ 零 check；
- 有界：>16 ref 只处理前 16；
- 空 ref_map / 空 session → 空结果（诚实缺席）。
"""
from types import SimpleNamespace

import pytest

from app.lib.gis.dataset_profile import DatasetProfile
from app.services.dataset_semantics import derive_descriptor
from app.services.dataset_semantics.store import (
    DatasetSemanticStore,
    reset_dataset_semantic_store,
)
from app.services.project_runtime.resume import (
    _reconcile_descriptor_semantics,
)


def _desc(count=5, key="ref:data-x"):
    p = DatasetProfile(
        source="ref_descriptor", feature_count=count,
        geometry_types=["Point"], crs="EPSG:4326",
        fields={"v": "number"}, numeric_fields=["v"],
        fields_status="explicit",
    )
    feats = [
        {"geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.6]},
         "properties": {"v": i}}
        for i in range(count)
    ]
    return derive_descriptor(p, dataset_key=key, features=feats)


def _snapshot(old_sid, doc=None):
    return SimpleNamespace(session_id=old_sid, mapspec_doc=doc or {})


def _doc_with_fp(ref, fp):
    return {"sources": {"poi": {
        "type": "geojson", "ref": ref, "ref_id": ref,
        "descriptor_fingerprint": fp,
    }}}


@pytest.fixture()
def sem_store(tmp_path, monkeypatch):
    import app.services.dataset_semantics.store as store_mod

    store = DatasetSemanticStore(base_dir=tmp_path)
    monkeypatch.setattr(store_mod, "_store", store)
    yield store
    reset_dataset_semantic_store()


async def test_migration_and_valid_verdict(sem_store, tmp_path):
    d = _desc(key="ref:old-1")
    await sem_store.put("sess-old", "ref:old-1", d)
    snap = _snapshot("sess-old", _doc_with_fp("ref:old-1", d.descriptor_fingerprint))
    out = await _reconcile_descriptor_semantics(
        snap, "sess-new", {"ref:old-1": "ref:new-1"})
    assert out["v"] == 1
    assert out["migrated"] == 1
    assert out["migration_failed"] == 0
    assert out["checks"][0]["verdict"] == "valid"
    assert out["stale_refs"] == []
    # 迁移后新 session 可读，指纹不变、指针改写。
    rec = await sem_store.get("sess-new", "ref:new-1")
    assert rec.ok
    assert rec.descriptor.descriptor_fingerprint == d.descriptor_fingerprint
    assert rec.descriptor.dataset_key == "ref:new-1"


async def test_recompute_verdict_on_semantic_drift(sem_store):
    v1 = _desc(count=5, key="ref:old-1")
    await sem_store.put("sess-old", "ref:old-1", v1)
    v2 = _desc(count=50, key="ref:old-1")  # 内容前进
    await sem_store.put("sess-old", "ref:old-1", v2)
    snap = _snapshot("sess-old", _doc_with_fp("ref:old-1", v1.descriptor_fingerprint))
    out = await _reconcile_descriptor_semantics(
        snap, "sess-new", {"ref:old-1": "ref:new-1"})
    assert out["checks"][0]["verdict"] == "recompute"
    assert out["stale_refs"] == ["ref:new-1"]
    assert "DESCRIPTOR_FINGERPRINT_MISMATCH" in out["checks"][0]["reason_codes"]


async def test_unknown_when_recorded_anchor_but_semantics_missing(sem_store):
    snap = _snapshot("sess-old", _doc_with_fp("ref:old-1", "dsd-v1:gone"))
    out = await _reconcile_descriptor_semantics(
        snap, "sess-new", {"ref:old-1": "ref:new-1"})
    assert out["migrated"] == 0
    assert out["checks"][0]["verdict"] == "unknown"
    assert out["unknown_refs"] == ["ref:new-1"]
    assert out["stale_refs"] == []


async def test_no_anchor_no_check(sem_store):
    d = _desc(key="ref:old-1")
    await sem_store.put("sess-old", "ref:old-1", d)
    snap = _snapshot("sess-old", {"sources": {"poi": {
        "type": "geojson", "ref": "ref:old-1", "ref_id": "ref:old-1"}}})
    out = await _reconcile_descriptor_semantics(
        snap, "sess-new", {"ref:old-1": "ref:new-1"})
    assert out["migrated"] == 1  # 迁移照做（身份连续）
    assert out["checks"] == []   # 无记录面锚 → 不对账（不虚构期望）


async def test_corrupt_descriptor_migration_failure_counted(
        sem_store, monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("store read failed")

    monkeypatch.setattr(DatasetSemanticStore, "get", _boom)
    snap = _snapshot("sess-old", _doc_with_fp("ref:old-1", "dsd-v1:x"))
    out = await _reconcile_descriptor_semantics(
        snap, "sess-new", {"ref:old-1": "ref:new-1"})
    assert out["migration_failed"] == 1
    assert out["checks"][0]["verdict"] == "unknown"


async def test_bounded_to_sixteen_refs(sem_store):
    d = _desc(key="ref:bulk")
    await sem_store.put("sess-old", "ref:bulk", d)
    ref_map = {f"ref:old-{i}": f"ref:new-{i}" for i in range(30)}
    snap = _snapshot("sess-old", {})
    out = await _reconcile_descriptor_semantics(snap, "sess-new", ref_map)
    assert out["migrated"] + out["migration_failed"] <= 16
    assert len(out["checks"]) == 0  # 无锚


async def test_empty_inputs_return_empty(sem_store):
    out = await _reconcile_descriptor_semantics(_snapshot(""), "s", {})
    assert out == {"v": 1, "migrated": 0, "migration_failed": 0,
                   "checks": [], "stale_refs": [], "unknown_refs": []}


async def test_receipt_has_reconciliation_field():
    from app.services.project_runtime.contract import ResumeReceipt

    r = ResumeReceipt(
        snapshot_id="prs-1", project_id="p", session_id="s",
        source_session_id="o", status="ok")
    assert r.descriptor_reconciliation == {}
    r2 = ResumeReceipt(
        snapshot_id="prs-2", project_id="p", session_id="s",
        source_session_id="o", status="ok",
        descriptor_reconciliation={"v": 1, "stale_refs": ["ref:x"]})
    assert r2.descriptor_reconciliation["stale_refs"] == ["ref:x"]
