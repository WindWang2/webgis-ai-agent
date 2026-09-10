"""Lakehouse V7 Wave 17 — DR scrub（确定性采样 / 全量 / 损坏检测 / virtual 深度）。"""
from __future__ import annotations


import pytest

from app.services.lakehouse.data_object import (
    normalize_owner_scope,
    publish_data_object,
    resolve_data_object,
)
from app.services.lakehouse.dr import (
    DRVerifyError,
    _sample_chunk_ids,
    scrub_object,
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.durable_blob_store import reset_filesystem_blob_store
    from app.services.project_artifact_promotion import (
        reset_content_store_root_cache,
    )

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    reset_filesystem_blob_store()
    reset_content_store_root_cache()
    yield tmp_path
    reset_filesystem_blob_store()
    reset_content_store_root_cache()


def _object_with_chunks(env, n_chunks):
    files = {f"chunks/c{i:03d}.bin": bytes([i]) * 128 for i in range(n_chunks)}
    return publish_data_object(
        files, kind="zarr_cube",
        owner_scope=normalize_owner_scope(session_id="sess-scrub"),
    )


def test_sample_deterministic_and_bounded(env):
    obj = _object_with_chunks(env, 64)
    s1 = scrub_object(obj.data_object_id, mode="sample", sample_k=8)
    s2 = scrub_object(obj.data_object_id, mode="sample", sample_k=8)
    assert s1["state"] == "verified"
    assert s1["chunks_total"] == 64
    assert s1["chunks_checked"] == 8  # 采样有界（非全量）
    assert s1["chunks_checked"] == s2["chunks_checked"]
    full = scrub_object(obj.data_object_id, mode="full")
    assert full["chunks_checked"] == 64


def test_sample_selection_is_reproducible():
    ids = [f"{i:064x}" for i in range(100)]
    a = _sample_chunk_ids("m", ids, 10)
    b = _sample_chunk_ids("m", ids, 10)
    assert a == b
    assert len(a) == 10
    assert set(a) <= set(ids)
    assert _sample_chunk_ids("m", ids[:3], 10) == ids[:3]  # k >= n → 全集


def test_scrub_detects_missing_and_corrupt(env):
    from app.services.s3_blob_store import get_object_store

    obj = _object_with_chunks(env, 4)
    store = get_object_store()
    blobs = resolve_data_object(obj.data_object_id)["content_blobs"]
    manifest_blob_ids = [str(b["sha256"]) for b in blobs]
    # 模拟缺失：删一个内容 blob。
    store.delete_blob(manifest_blob_ids[0])
    report = scrub_object(obj.data_object_id, mode="full")
    assert report["state"] == "corrupt"
    assert manifest_blob_ids[0] in report["missing"]
    # 模拟损坏：同键覆写不同内容。
    store.put_blob(manifest_blob_ids[1], b"tampered" * 16, "binary")
    report2 = scrub_object(obj.data_object_id, mode="full")
    assert manifest_blob_ids[1] in report2["corrupt"]


def test_scrub_invalid_inputs(env):
    obj = _object_with_chunks(env, 2)
    with pytest.raises(DRVerifyError, match="mode"):
        scrub_object(obj.data_object_id, mode="quantum")
    with pytest.raises(DRVerifyError, match="invalid data object id"):
        scrub_object("nope", mode="sample")
    with pytest.raises(DRVerifyError, match="manifest not found"):
        scrub_object("f" * 64, mode="sample")


def test_scrub_virtual_reports_deep_state(env):
    from app.services.lakehouse.virtual_object import publish_virtual_object

    obj = _object_with_chunks(env, 2)
    v = publish_virtual_object(
        [obj.data_object_id], kind_label="view",
        owner_scope=normalize_owner_scope(session_id="sess-scrub"),
    )
    report = scrub_object(v["data_object_id"], mode="sample")
    assert report["state"] == "verified"
    # child 缺失 → 深度状态诚实缺席（绝不假绿）。
    from app.services.s3_blob_store import get_object_store

    get_object_store().delete_blob(obj.data_object_id)
    report2 = scrub_object(v["data_object_id"], mode="sample")
    assert report2["state"] == "virtual_children_missing"
