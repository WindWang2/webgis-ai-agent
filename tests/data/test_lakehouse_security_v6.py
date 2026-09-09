"""Lakehouse V6 Wave 12 — 安全回归（集中语义面）。

控制点分散在各 wave（owner 域、路径白名单、SSRF 门、redaction、尺寸闸、
oversized 闸），本文件把它们作为安全语义集中锁定：

1. 跨 owner 的读/物化/复用判定全部拒绝（不泄漏存在性）；
2. 路径派生边界拒绝 traversal/分隔符（parquet/cube/manifest member）；
3. 远端对象存储 endpoint 过既有 SSRF 门（内网/云元数据拒绝）；
4. manifest 身份记录 secret-free（producer args 写时 redact）；
5. 尺寸闸在写任何字节之前拒绝（oversized metadata / object）。
"""
from __future__ import annotations

import json

import pytest

from app.services.durable_blob_store import FilesystemBlobStore
from app.services.lakehouse.data_object import (
    DataObjectError,
    DataObjectTooLargeError,
    build_object_manifest,
    materialize_data_object,
    normalize_owner_scope,
    owner_scope_allows,
    publish_data_object,
    resolve_data_object,
)


@pytest.fixture()
def blob_store(tmp_path):
    return FilesystemBlobStore(root=tmp_path / "blobs")


# 1. owner 隔离（读/物化/复用判定）─────────────────────────────────────────


def test_cross_owner_reads_and_materialization_denied(blob_store, tmp_path):
    owner = normalize_owner_scope(session_id="sess-owner")
    identity = publish_data_object(
        {"d.bin": b"data"}, kind="vector_parquet", owner_scope=owner,
        store=blob_store,
    )
    # manifest 可被任何知道 id 的人 resolve（BlobStore 层无 ACL —— 与
    # 晋升内容库一致），但一切**使用**都强制 owner 校验：
    assert owner_scope_allows(resolve_data_object(
        identity.data_object_id, store=blob_store), session_id="sess-owner")
    assert not owner_scope_allows(resolve_data_object(
        identity.data_object_id, store=blob_store), session_id="sess-other")
    with pytest.raises(DataObjectError, match="different owner"):
        materialize_data_object(
            identity.data_object_id, tmp_path / "steal",
            owner_session_id="sess-other", store=blob_store,
        )
    with pytest.raises(DataObjectError, match="different owner"):
        materialize_data_object(
            identity.data_object_id, tmp_path / "steal",
            owner_project_id="proj-other", store=blob_store,
        )


# 2. 路径边界（traversal / 分隔符 / 控制字符）─────────────────────────────


@pytest.mark.parametrize("bad", [
    "../etc/passwd", "a/b/../../c", "..", "a\\b", "\x00evil", "/abs/path",
])
def test_hostile_member_paths_rejected(blob_store, bad):
    owner = normalize_owner_scope(session_id="s")
    # publish 侧：member path 白名单在边界拒绝（写入前）。
    with pytest.raises(DataObjectError):
        publish_data_object({bad: b"x"}, kind="vector_parquet",
                            owner_scope=owner, store=blob_store)
    # 身份层（build_object_manifest）不是路径校验器 —— 恶意路径的安全边界
    # 在"物化时的遏制"（见 test_materialize_containment_against_forged_manifest）。


def test_materialize_containment_against_forged_manifest(blob_store, tmp_path):
    """外来/伪造 manifest 携带越界 member path → 物化路径遏制拒绝。"""
    from app.lib.data.fingerprints import canonical_dumps

    owner = normalize_owner_scope(session_id="s")
    identity = publish_data_object(
        {"ok.bin": b"payload"}, kind="vector_parquet", owner_scope=owner,
        store=blob_store,
    )
    manifest = resolve_data_object(identity.data_object_id, store=blob_store)
    manifest["content_blobs"] = [{
        "path": "../escape",
        "sha256": manifest["content_blobs"][0]["sha256"],
        "byte_size": manifest["content_blobs"][0]["byte_size"],
    }]
    blob = canonical_dumps(manifest).encode("utf-8")
    from app.services.durable_blob_store import sha256_of_bytes

    forged_id = sha256_of_bytes(blob)
    blob_store.put_blob(forged_id, blob, "json")
    with pytest.raises(DataObjectError):
        materialize_data_object(
            forged_id, tmp_path / "out", owner_session_id="s",
            store=blob_store,
        )
    assert not (tmp_path / "escape").exists()


# 3. SSRF 门（对象存储 endpoint）──────────────────────────────────────────


@pytest.mark.parametrize("endpoint", [
    "http://169.254.169.254:9000",          # 云元数据
    "http://127.0.0.1:9000",                # loopback
    "http://10.0.0.5:9000",                 # RFC1918
])
def test_s3_endpoints_ssrf_gated(monkeypatch, endpoint):
    monkeypatch.setenv("WEBGIS_S3_BUCKET", "b")
    monkeypatch.setenv("WEBGIS_S3_ENDPOINT_URL", endpoint)
    from app.services.s3_blob_store import S3StoreUnavailable, build_s3_client_from_env

    with pytest.raises(S3StoreUnavailable, match="SSRF"):
        build_s3_client_from_env()


# 4. manifest secret-free（写时 redact）───────────────────────────────────


def test_manifest_redacts_producer_secrets():
    manifest = build_object_manifest(
        kind="cog_raster",
        owner_scope=normalize_owner_scope(project_id="p"),
        entries=[("t.tif", "b" * 64, 1)],
        producer={"args": {
            "api_token": "sk-abc123", "password": "hunter2",
            "aws_secret_access_key": "AKIAEXAMPLE", "nested": {"s3_token": "t"},
            "limit": 5,
        }},
    )
    raw = json.dumps(manifest, sort_keys=True)
    for secret in ("sk-abc123", "hunter2", "AKIAEXAMPLE"):
        assert secret not in raw
    assert "[REDACTED]" in raw
    assert manifest["producer"]["args"]["limit"] == 5  # 非敏感参数保留


# 5. 尺寸闸（写前拒绝）────────────────────────────────────────────────────


def test_size_gates_refuse_before_write(blob_store):
    owner = normalize_owner_scope(session_id="s")
    with pytest.raises(DataObjectTooLargeError):
        publish_data_object(
            {"big.bin": b"x" * 128}, kind="vector_parquet", owner_scope=owner,
            max_total_bytes=64, store=blob_store,
        )
    with pytest.raises(DataObjectTooLargeError):
        build_object_manifest(
            kind="vector_parquet", owner_scope=owner,
            entries=[("d.bin", "a" * 64, 1)],
            payload={"blob": "x" * (70 * 1024)},
        )
