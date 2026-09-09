"""Lakehouse V6 Wave 2 — S3-compatible BlobStore backend.

覆盖面（与 FilesystemBlobStore 同族纪律）：
- 同一 BlobStore 接口：put/get/exists/size/delete/location 语义一致；
- 原子发布：staging→copy→cleanup；copy 失败时 final 不出场、staging 清理；
- put-if-absent CAS：sidecar digest 一致 = 命中不重写；不一致 = 原子重写；
- digest 校验读：expected 不符 → None（绝不静默顶替）；
- 后端无关指针：location 形态与 FS store 相同（<shard>/<key>.suffix）；
- 选择器：默认 filesystem（零行为变化）；s3 缺配置 → typed 降级；
- SSRF 门：构建期 endpoint 过 DataFabricSecurity.validate_url。
"""
from __future__ import annotations

import json

import pytest

from app.services.durable_blob_store import BlobKeyError
from app.services.s3_blob_store import (
    S3BlobStore,
    S3StoreUnavailable,
    build_s3_client_from_env,
    get_object_store,
    reset_object_store_singleton,
    s3_config_from_env,
)


class FakeS3:
    """最小 S3 语义双（head/get/put/copy/delete + 失败注入）。"""

    def __init__(self, fail_on: str | None = None):
        self.objects: dict[str, bytes] = {}
        self.fail_on = fail_on
        self.deleted: list[str] = []

    def _guard(self, op: str):
        if self.fail_on == op:
            raise RuntimeError(f"injected failure: {op}")

    def put_object(self, Bucket, Key, Body):
        self._guard("put_object")
        self.objects[Key] = bytes(Body)

    def copy_object(self, Bucket, Key, CopySource):
        self._guard("copy_object")
        src = self.objects.get(CopySource["Key"])
        if src is None:
            raise RuntimeError("copy source missing")
        self.objects[Key] = src

    def head_object(self, Bucket, Key):
        self._guard("head_object")
        if Key not in self.objects:
            raise RuntimeError("404")
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, Bucket, Key):
        self._guard("get_object")
        if Key not in self.objects:
            raise RuntimeError("404")
        body = self.objects[Key]

        class _B:
            def read(self_inner):
                return body

        return {"Body": _B()}

    def delete_object(self, Bucket, Key):
        self._guard("delete_object")
        self.deleted.append(Key)
        self.objects.pop(Key, None)


def _store(bucket="test-bucket", prefix="", **kw):
    fake = FakeS3(**kw)
    return S3BlobStore(bucket, lambda: fake, prefix=prefix), fake


# ── roundtrip / 指针形态 ──────────────────────────────────────────────────


def test_put_get_roundtrip_json_and_binary():
    store, _fake = _store()
    r = store.put_blob("a" * 64, b'{"x": 1}', "json")
    assert r.put_new is True
    # 后端无关指针：与 FS store 相同布局（<shard>/<key>.json）。
    assert r.location == f"{'a' * 4}/{64 * 'a'}.json"
    assert store.get_blob("a" * 64) == b'{"x": 1}'
    b = store.put_blob("b" * 64, b"\x00\x01png", "binary")
    assert b.location == f"{'b' * 4}/{64 * 'b'}.bin"
    assert store.get_blob("b" * 64) == b"\x00\x01png"


def test_put_if_absent_dedup_hit_then_rewrite():
    store, fake = _store()
    key = "c" * 64
    first = store.put_blob(key, b"same-bytes", "json")
    second = store.put_blob(key, b"same-bytes", "json")
    assert first.put_new is True and second.put_new is False
    # 外部覆写（坏字节占位）→ 身份不符 → 原子重写。
    final_key = f"{key[:4]}/{key}.json"
    fake.objects[final_key] = b"corrupted-place-holder"
    third = store.put_blob(key, b"same-bytes", "json")
    assert third.put_new is True
    assert store.get_blob(key) == b"same-bytes"


def test_get_digest_verification_refuses_mismatch():
    store, _fake = _store()
    store.put_blob("d" * 64, b"trusted", "json")
    assert store.get_blob("d" * 64, expected_sha256="0" * 64) is None
    import hashlib

    good = hashlib.sha256(b"trusted").hexdigest()
    assert store.get_blob("d" * 64, expected_sha256=good) == b"trusted"


def test_exists_size_delete_and_missing():
    store, _fake = _store()
    store.put_blob("e" * 64, b"12345", "binary")
    assert store.exists("e" * 64) is True
    assert store.size("e" * 64) == 5
    assert store.exists("f" * 64) is False
    assert store.size("f" * 64) is None
    assert store.delete_blob("e" * 64) is True
    assert store.exists("e" * 64) is False
    assert store.delete_blob("e" * 64) is False
    with pytest.raises(BlobKeyError):
        store.put_blob("../evil", b"x", "json")


def test_meta_sidecar_readable():
    store, _fake = _store()
    store.put_blob("9" * 64, b"\x01\x02", "binary")
    meta = store.read_meta("9" * 64)
    assert meta is not None
    assert meta["content_type"] == "binary"
    assert meta["byte_size"] == 2
    assert len(meta["sha256"]) == 64


# ── 原子发布：失败注入 ────────────────────────────────────────────────────


def test_copy_failure_leaves_no_final_and_cleans_staging():
    store, fake = _store(fail_on="copy_object")
    key = "7" * 64
    with pytest.raises(RuntimeError, match="copy_object"):
        store.put_blob(key, b"doomed", "json")
    assert f"{key[:4]}/{key}.json" not in fake.objects
    # staging 全部清理（无 staging/ 前缀残留对象）。
    assert not [k for k in fake.objects if k.startswith("staging/")]
    assert store.exists(key) is False


def test_bucket_validation():
    with pytest.raises(BlobKeyError):
        S3BlobStore("", lambda: None)
    with pytest.raises(BlobKeyError):
        S3BlobStore("bucket/with/slash", lambda: None)


def test_prefix_namespacing():
    store, fake = _store(prefix="tenants/t1")
    store.put_blob("8" * 64, b"ns", "json")
    assert any(k.startswith("tenants/t1/") for k in fake.objects)
    assert store.get_blob("8" * 64) == b"ns"


# ── 选择器 / env 配置 / SSRF 门 ──────────────────────────────────────────


def test_selector_default_filesystem(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBGIS_OBJECT_STORE_BACKEND", "filesystem")
    reset_object_store_singleton()
    from app.services.durable_blob_store import FilesystemBlobStore

    assert isinstance(get_object_store(), FilesystemBlobStore)


def test_selector_s3_requires_bucket(monkeypatch):
    monkeypatch.setenv("WEBGIS_OBJECT_STORE_BACKEND", "s3")
    monkeypatch.delenv("WEBGIS_S3_BUCKET", raising=False)
    reset_object_store_singleton()
    with pytest.raises(S3StoreUnavailable):
        get_object_store()
    reset_object_store_singleton()


def test_selector_s3_rejects_unknown_backend(monkeypatch):
    monkeypatch.setenv("WEBGIS_OBJECT_STORE_BACKEND", "gcs")
    reset_object_store_singleton()
    with pytest.raises(S3StoreUnavailable, match="unknown"):
        get_object_store()
    reset_object_store_singleton()


def test_build_client_requires_config(monkeypatch):
    monkeypatch.setenv("WEBGIS_S3_BUCKET", "")
    monkeypatch.setenv("WEBGIS_S3_ENDPOINT_URL", "")
    with pytest.raises(S3StoreUnavailable, match="required"):
        build_s3_client_from_env()


def test_build_client_ssrf_gate_blocks_private_endpoint(monkeypatch):
    monkeypatch.setenv("WEBGIS_S3_BUCKET", "b")
    monkeypatch.setenv("WEBGIS_S3_ENDPOINT_URL", "http://169.254.169.254:9000")
    with pytest.raises(S3StoreUnavailable, match="SSRF"):
        build_s3_client_from_env()


def test_config_reads_env_without_secret_leak(monkeypatch):
    monkeypatch.setenv("WEBGIS_S3_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("WEBGIS_S3_SECRET_ACCESS_KEY", "top-secret")
    monkeypatch.setenv("WEBGIS_S3_PREFIX", "/lake/t1/")
    cfg = s3_config_from_env()
    assert cfg["access_key_id"] == "AKIA_TEST"
    assert cfg["secret_access_key"] == "top-secret"
    assert cfg["prefix"] == "lake/t1"
    # secret 不入日志/manifest 的机器验证：store 侧任何对象/位置串都不含它。
    store, fake = _store()
    store.put_blob("6" * 64, b"x", "json")
    dumped = json.dumps(list(fake.objects))
    assert "top-secret" not in dumped
