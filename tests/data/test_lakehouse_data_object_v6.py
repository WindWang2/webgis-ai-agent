"""Lakehouse V6 Wave 1 — durable DataObject identity + session content hash.

覆盖面（ADR-0118 身份契约）：
- manifest 确定性：同 (kind, owner, 内容, 参数) ⇒ 同 DataObject id；
- 内容寻址默认参与身份：republish 全 CAS 命中（deduped），blob 零重写；
- owner 隔离：跨 owner 同内容 = 不同逻辑对象；越权物化/解析被拒；
- 完整性四态：verified / manifest_missing / blob_missing / digest_mismatch；
- 物化 round-trip：blob → 工作目录逐文件 digest 校验 + 原子发布 + 路径遏制；
- 有界：manifest 尺寸闸 / blob 数闸 / unsafe member path 拒绝；
- 复用键：compute_reuse_fingerprint 口径 + owner 参与键；
- session RefDescriptor.content_hash 默认参与身份（WEBGIS_REF_CONTENT_HASH
  显式关闭才回退恒 None）。
"""
from __future__ import annotations

import json

import pytest

from app.services.durable_blob_store import FilesystemBlobStore
from app.services.lakehouse.data_object import (
    DataObjectError,
    DataObjectTooLargeError,
    build_object_manifest,
    compute_content_root,
    compute_object_reuse_fingerprint,
    is_data_object_id,
    materialize_data_object,
    normalize_owner_scope,
    owner_scope_allows,
    publish_data_object,
    resolve_data_object,
    verify_data_object,
)


@pytest.fixture()
def blob_store(tmp_path):
    return FilesystemBlobStore(root=tmp_path / "blobs")


def _publish_pair(store, owner):
    return publish_data_object(
        {
            "data.parquet": b"parquet-bytes-v1",
            "meta.json": b'{"bbox": [0, 0, 1, 1]}',
        },
        kind="vector_parquet",
        owner_scope=owner,
        payload={"feature_count": 2, "crs": "EPSG:4326"},
        producer={"capability": "fabric.materialize", "args": {"api_token": "sekret"}},
        source_refs=["ref:data-fabric-abc"],
        store=store,
    )


# ── 身份确定性 / dedup ────────────────────────────────────────────────────


def test_identity_is_deterministic_and_content_addressed(blob_store):
    owner = normalize_owner_scope(session_id="s1")
    a = _publish_pair(blob_store, owner)
    b = _publish_pair(blob_store, owner)
    assert is_data_object_id(a.data_object_id)
    assert a.data_object_id == b.data_object_id
    assert a.manifest_location == b.manifest_location
    # 同内容重发布 = CAS 命中，无重写。
    assert b.deduped is True


def test_identity_changes_with_content_and_params(blob_store):
    owner = normalize_owner_scope(session_id="s1")
    a = _publish_pair(blob_store, owner)
    b = publish_data_object(
        {"data.parquet": b"parquet-bytes-v2"},
        kind="vector_parquet",
        owner_scope=owner,
        payload={"feature_count": 2, "crs": "EPSG:4326"},
        store=blob_store,
    )
    c = publish_data_object(
        {"data.parquet": b"parquet-bytes-v1"},
        kind="vector_parquet",
        owner_scope=owner,
        payload={"feature_count": 3, "crs": "EPSG:4326"},
        store=blob_store,
    )
    assert len({a.data_object_id, b.data_object_id, c.data_object_id}) == 3


def test_owner_scope_participates_in_identity(blob_store):
    s1 = normalize_owner_scope(session_id="s1")
    s2 = normalize_owner_scope(session_id="s2")
    a = _publish_pair(blob_store, s1)
    b = _publish_pair(blob_store, s2)
    # 同内容跨 owner = 两个不同逻辑对象（字节 blob 共享由 CAS 保证）。
    assert a.data_object_id != b.data_object_id


def test_manifest_is_deterministic_and_secret_free(blob_store):
    owner = normalize_owner_scope(session_id="s1")
    manifest = build_object_manifest(
        kind="vector_parquet",
        owner_scope=owner,
        entries=[("data.parquet", "a" * 64, 10)],
        producer={"args": {"password": "hunter2", "limit": 5}},
    )
    raw = json.dumps(manifest, sort_keys=True)
    assert "hunter2" not in raw
    assert "[REDACTED]" in raw
    assert "created_at" not in raw and "timestamp" not in raw


# ── resolve / verify / materialize ───────────────────────────────────────


def test_resolve_roundtrip_is_digest_verified(blob_store):
    identity = _publish_pair(blob_store, normalize_owner_scope(session_id="s1"))
    manifest = resolve_data_object(identity.data_object_id, store=blob_store)
    assert manifest is not None
    assert manifest["kind"] == "vector_parquet"
    assert manifest["producer"]["args"]["api_token"] == "[REDACTED]"
    assert resolve_data_object("z" * 64, store=blob_store) is None
    assert resolve_data_object("not-an-id", store=blob_store) is None


def test_verify_states(blob_store):
    identity = _publish_pair(blob_store, normalize_owner_scope(session_id="s1"))
    assert verify_data_object(identity.data_object_id, store=blob_store) == "verified"
    assert verify_data_object("f" * 64, store=blob_store) == "manifest_missing"
    # 篡改一个 blob → digest_mismatch（绝不静默顶替）。
    manifest = resolve_data_object(identity.data_object_id, store=blob_store)
    blob_key = manifest["content_blobs"][0]["sha256"]
    blob_store.put_blob(blob_key, b"tampered!!", "binary")
    assert verify_data_object(identity.data_object_id, store=blob_store) == (
        "digest_mismatch"
    )
    # 删掉一个 blob → blob_missing。
    blob_store.delete_blob(blob_key)
    assert verify_data_object(identity.data_object_id, store=blob_store) == (
        "blob_missing"
    )


def test_materialize_roundtrip_with_owner_and_containment(blob_store, tmp_path):
    identity = _publish_pair(blob_store, normalize_owner_scope(session_id="s1"))
    target = tmp_path / "restore"
    written = materialize_data_object(
        identity.data_object_id,
        target,
        owner_session_id="s1",
        store=blob_store,
    )
    assert sorted(written) == ["data.parquet", "meta.json"]
    assert (target / "data.parquet").read_bytes() == b"parquet-bytes-v1"
    # 越权物化 → typed 拒绝。
    with pytest.raises(DataObjectError, match="different owner"):
        materialize_data_object(
            identity.data_object_id,
            tmp_path / "elsewhere",
            owner_session_id="s2",
            store=blob_store,
        )


# ── 有界闸（oversized / unsafe）─────────────────────────────────────────


def test_oversized_manifest_payload_rejected(blob_store):
    with pytest.raises(DataObjectTooLargeError):
        build_object_manifest(
            kind="vector_parquet",
            owner_scope=normalize_owner_scope(session_id="s1"),
            entries=[("d.bin", "a" * 64, 1)],
            payload={"blob": "x" * (70 * 1024)},
        )


def test_unsafe_member_paths_rejected(blob_store):
    for bad in ("/etc/passwd", "../escape", "a\\b", ""):
        with pytest.raises(DataObjectError):
            publish_data_object(
                {bad: b"x"},
                kind="vector_parquet",
                owner_scope=normalize_owner_scope(session_id="s1"),
                store=blob_store,
            )


def test_owner_scope_validation():
    with pytest.raises(DataObjectError):
        normalize_owner_scope()
    with pytest.raises(DataObjectError):
        normalize_owner_scope(session_id="s", project_id="p")
    with pytest.raises(DataObjectError):
        normalize_owner_scope(session_id="../evil")
    assert normalize_owner_scope(project_id="p1") == {"project_id": "p1"}


def test_owner_scope_allows_matrix():
    m = build_object_manifest(
        kind="cog_raster",
        owner_scope=normalize_owner_scope(project_id="p1"),
        entries=[("cog.tif", "b" * 64, 4)],
    )
    assert owner_scope_allows(m, project_id="p1")
    assert not owner_scope_allows(m, project_id="p2")
    assert not owner_scope_allows(m, session_id="p1")


# ── 复用键（derived artifact dependency hash）───────────────────────────


def test_reuse_fingerprint_stable_and_owner_scoped():
    common = dict(
        operation="cube.build",
        operation_version="v6",
        input_fingerprints={"src": "1" * 64},
        normalized_args={"window": 512},
    )
    a = compute_object_reuse_fingerprint(**common, owner_scope={"session_id": "s1"})
    b = compute_object_reuse_fingerprint(**common, owner_scope={"session_id": "s1"})
    c = compute_object_reuse_fingerprint(**common, owner_scope={"session_id": "s2"})
    d = compute_object_reuse_fingerprint(
        **{**common, "normalized_args": {"window": 256}},
        owner_scope={"session_id": "s1"},
    )
    assert a == b
    assert a != c  # 跨 owner 复用判定隔离
    assert a != d  # 参数参与键


def test_content_root_order_and_bytes():
    e1 = [("b.bin", "2" * 64, 2), ("a.bin", "1" * 64, 1)]
    root, total = compute_content_root(e1)
    # 顺序无关（内部排序）。
    root2, total2 = compute_content_root(list(reversed(e1)))
    assert root == root2 and total == 3
    with pytest.raises(DataObjectError):
        compute_content_root([])


# ── session RefDescriptor.content_hash 默认参与身份 ─────────────────────


def test_session_content_hash_default_on(monkeypatch):
    monkeypatch.delenv("WEBGIS_REF_CONTENT_HASH", raising=False)
    from app.schemas.ref_descriptor import _content_hash_enabled, _opt_in_content_hash

    assert _content_hash_enabled() is True
    digest = _opt_in_content_hash({"type": "FeatureCollection", "features": []})
    assert isinstance(digest, str) and len(digest) == 64
    # 同内容同摘要。
    again = _opt_in_content_hash({"features": [], "type": "FeatureCollection"})
    assert digest == again


def test_session_content_hash_opt_out(monkeypatch):
    monkeypatch.setenv("WEBGIS_REF_CONTENT_HASH", "0")
    from app.schemas.ref_descriptor import _content_hash_enabled, _opt_in_content_hash

    assert _content_hash_enabled() is False
    assert _opt_in_content_hash({"a": 1}) is None


def test_session_content_hash_over_budget_keeps_none(monkeypatch):
    monkeypatch.delenv("WEBGIS_REF_CONTENT_HASH", raising=False)
    from app.schemas.ref_descriptor import _opt_in_content_hash

    big = {"blob": "x" * (1024 * 1024 + 1)}
    assert _opt_in_content_hash(big) is None
