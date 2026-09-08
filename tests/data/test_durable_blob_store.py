"""BlobStore（Wave 1 durable artifact store）单元测试。

覆盖 brief 要求的面：
- put/get 往返（json + binary）；
- 去重：同内容第二次 put 返回 put_new=False（CAS put-if-absent）；
- digest 校验失败 → None（绝不静默顶替内容）；
- 原子性：注入失败时不留半截文件/临时件；
- 布局兼容：既有晋升库布局（<shard4>/<key>.json）的文件可读。
"""
import json
import os

import pytest

from app.services.durable_blob_store import (
    BlobKeyError,
    FilesystemBlobStore,
    PutResult,
    safe_blob_key,
    sha256_of_bytes,
)


@pytest.fixture
def store(tmp_path):
    return FilesystemBlobStore(tmp_path)


class TestPutGetRoundtrip:
    def test_json_roundtrip(self, store, tmp_path):
        data = json.dumps({"type": "FeatureCollection", "features": [1, 2]}).encode()
        result = store.put_blob("a" * 64, data, "json")
        assert isinstance(result, PutResult) and result.put_new is True
        # 布局：<root>/<key[:4]>/<key>.json —— 与晋升库同款
        assert result.location == f"{'a' * 4}/{'a' * 64}.json"
        assert (tmp_path / result.location).is_file()
        assert store.get_blob("a" * 64) == data

    def test_binary_roundtrip_with_sidecar(self, store, tmp_path):
        payload = b"\x89PNG\r\n\x1a\n-binary-bytes"
        result = store.put_blob("b" * 64, payload, "binary")
        assert result.put_new is True
        assert result.location == f"{'b' * 4}/{'b' * 64}.bin"
        assert store.get_blob("b" * 64) == payload
        meta = store.read_meta("b" * 64)
        assert meta == {"content_type": "binary", "byte_size": len(payload)}

    def test_put_json_convenience_uses_canonical_fingerprint(self, store):
        from app.lib.data.fingerprints import canonical_fingerprint

        payload = {"b": 2, "a": [1, 2]}
        key, digest = store.put_json(payload)
        assert key == digest == canonical_fingerprint(payload)
        raw = store.get_blob(key)
        assert raw is not None
        assert json.loads(raw.decode()) == {"a": [1, 2], "b": 2}  # sort_keys canonical


class TestDedup:
    def test_put_if_absent_skips_rewrite(self, store, tmp_path):
        key = "c" * 64
        data = b"same-bytes"
        first = store.put_blob(key, data, "json")
        assert first.put_new is True
        # 检测"真的没有重写"（CAS skip）：mtime_ns 与内容都原样保留。
        # （round-1 review MINOR：skip 路径现在先验 size/digest —— 内容一致
        # 时依旧跳过；故意篡改字节的场景见下一个测试。）
        path = tmp_path / first.location
        mtime_before = path.stat().st_mtime_ns
        second = store.put_blob(key, data, "json")
        assert second.put_new is False
        assert second.location == first.location
        assert path.stat().st_mtime_ns == mtime_before

    def test_put_blob_rewrites_corrupt_existing(self, store, tmp_path):
        """round-1 review MINOR：skip 路径信任前先验 —— 既有文件损坏/被
        外部覆写（digest 与键内容不符）时原子重写，绝不把坏字节当命中。"""
        key = "e" * 64
        data = b"trusted-bytes"
        first = store.put_blob(key, data, "json")
        assert first.put_new is True
        path = tmp_path / first.location
        path.write_bytes(b"corrupt-or-foreign-bytes")
        second = store.put_blob(key, data, "json")
        assert second.put_new is True, "mismatched existing content must be rewritten"
        assert path.read_bytes() == data
        # 修复后再 put：恢复 CAS skip 语义
        third = store.put_blob(key, data, "json")
        assert third.put_new is False


class TestDigestVerifiedRead:
    def test_digest_mismatch_returns_none(self, store):
        key = "d" * 64
        store.put_blob(key, b"trusted-bytes", "json")
        good = sha256_of_bytes(b"trusted-bytes")
        assert store.get_blob(key, expected_sha256=good) == b"trusted-bytes"
        assert store.get_blob(key, expected_sha256="0" * 64) is None

    def test_corrupted_content_detected(self, store, tmp_path):
        key = "e" * 64
        result = store.put_blob(key, b"original", "json")
        (tmp_path / result.location).write_bytes(b"tampered")
        assert store.get_blob(key, expected_sha256=sha256_of_bytes(b"original")) is None


class TestAtomicWrite:
    def test_no_partial_file_when_publish_fails(self, store, tmp_path, monkeypatch):
        key = "f" * 64
        target = tmp_path / key[:4] / f"{key}.json"
        real_replace = os.replace

        def _boom(src, dst):
            if str(dst) == str(target):
                raise OSError("ENOSPC simulated")
            return real_replace(src, dst)

        monkeypatch.setattr("app.services.durable_blob_store.os.replace", _boom)
        with pytest.raises(OSError):
            store.put_blob(key, b"payload", "json")
        assert not target.exists(), "no partial file may survive a failed publish"
        # tmp 已清理（同一 shard 目录下无残留临时件）
        shard_dir = tmp_path / key[:4]
        leftovers = [p for p in shard_dir.iterdir()] if shard_dir.is_dir() else []
        assert leftovers == []

    def test_unsafe_keys_rejected(self, store):
        for bad in ("", "../escape", "a/b", "a\\b", "..", "x" * 201):
            with pytest.raises(BlobKeyError):
                store.put_blob(bad, b"x", "json")
        with pytest.raises(BlobKeyError):
            safe_blob_key("ok/../nope")


class TestLayoutCompat:
    def test_existing_promotion_style_file_readable(self, store, tmp_path, monkeypatch):
        """既有晋升库写入的 <shard4>/<key>.json 文件必须原样可读（audit
        §7.1 布局兼容硬约束）。"""
        from app.services import project_artifact_promotion as pap

        monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
        key = "ab12" + "0" * 60
        payload = {"type": "FeatureCollection", "features": []}
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        legacy_rel = f"{key[:4]}/{key}.json"
        legacy_path = tmp_path / legacy_rel
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(blob, encoding="utf-8")

        assert store.exists(key) is True
        assert store.size(key) == len(blob.encode("utf-8"))
        raw = store.get_blob(key, expected_sha256=sha256_of_bytes(blob.encode("utf-8")))
        assert raw is not None and json.loads(raw) == payload
        # 晋升库的 read_content 同样可读该位置
        assert pap.read_content(legacy_rel) == payload

    def test_get_text_and_missing_key(self, store):
        store.put_json({"k": "v"})
        key, _ = store.put_json({"k2": "v2"})
        assert store.get_text(key) is not None
        assert store.get_blob("ff" + "0" * 62) is None
        assert store.exists("ff" + "0" * 62) is False
        assert store.size("ff" + "0" * 62) is None
        assert store.delete_blob("ff" + "0" * 62) is False

    def test_delete_blob_removes_all_forms(self, store, tmp_path):
        key = "9" * 64
        result = store.put_blob(key, b"bye", "binary")
        assert store.delete_blob(key) is True
        assert store.exists(key) is False
        assert not (tmp_path / result.location).exists()
        assert not (tmp_path / f"{key[:4]}/{key}.bin.meta").exists()
