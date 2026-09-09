"""Lakehouse V7 Wave 8-9 — S3 生产化（multipart/流式/ETag/重试/孤儿清扫）。

覆盖面（ADR-0119 §4，评审 R0-13/14/15）：
- put_blob_stream：multipart 分块（峰值 O(part_size) —— tracemalloc 结构性
  证据）、digest 流式累计、零字节 typed 拒绝；
- 失败清理：upload_part 终败 → abort 无残留；重试恢复（瞬态故障）；
- put_blob_from_path：digest 进 create Metadata（complete 前在场）；
  put-if-absent 命中不重写；
- get_blob_stream：digest 尾部判定（不符 BlobDigestMismatch）；
- ETag：sidecar 记录 + remote_etag head 比对（仅记录值比对 —— multipart
  ETag 非 digest，R0-14）；
- iter_objects：分页有界枚举（GC/孤儿扫描 parity 面）；
- sweep：超龄 multipart abort + staging 残留清理（cap 有界）。
"""
from __future__ import annotations

import hashlib
import tracemalloc

import pytest

from app.services.durable_blob_store import BlobDigestMismatch, BlobKeyError
from app.services.s3_blob_store import (
    DEFAULT_ATTEMPTS,
    S3BlobStore,
)


class FakeS3V7:
    """S3 语义双：multipart + Metadata + ETag + 失败注入 + 时间旅行。"""

    def __init__(self, *, fail_times=None):
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict] = {}
        self.multipart: dict[str, list] = {}   # upload_id -> [(part_no, bytes)]
        self.uploads_open: dict[str, str] = {}  # upload_id -> key
        self.upload_counter = 0
        self.deleted: list[str] = []
        self.aborted: list[str] = []
        self.fail_times = dict(fail_times or {})  # op -> 剩余失败次数
        self.upload_started_dates: dict[str, object] = {}

    def _etag(self, data: bytes) -> str:
        return f'"{hashlib.md5(data).hexdigest()}"'

    def _guard(self, op: str):
        remaining = self.fail_times.get(op, 0)
        if remaining > 0:
            self.fail_times[op] = remaining - 1
            raise RuntimeError(f"injected failure: {op}")

    # ── 基础对象 API ──────────────────────────────────────────────────

    def put_object(self, Bucket, Key, Body, Metadata=None):
        self._guard("put_object")
        self.objects[Key] = bytes(Body)
        if Metadata:
            self.metadata[Key] = dict(Metadata)
        return {"ETag": self._etag(self.objects[Key])}

    def copy_object(self, Bucket, Key, CopySource):
        self._guard("copy_object")
        src = self.objects.get(CopySource["Key"])
        if src is None:
            raise RuntimeError("404")
        self.objects[Key] = src
        return {"ETag": self._etag(src)}

    def head_object(self, Bucket, Key):
        self._guard("head_object")
        if Key not in self.objects:
            raise RuntimeError("404")
        return {
            "ContentLength": len(self.objects[Key]),
            "ETag": self._etag(self.objects[Key]),
        }

    def get_object(self, Bucket, Key):
        self._guard("get_object")
        if Key not in self.objects:
            raise RuntimeError("404")
        body = self.objects[Key]

        class _B:
            def __init__(self, data):
                self._data = data
                self._pos = 0

            def read(self, n=-1):
                if n is None or n < 0:
                    out = self._data[self._pos:]
                    self._pos = len(self._data)
                    return out
                out = self._data[self._pos:self._pos + n]
                self._pos += len(out)
                return out

        return {"Body": _B(body)}

    def delete_object(self, Bucket, Key):
        self._guard("delete_object")
        self.deleted.append(Key)
        self.objects.pop(Key, None)
        self.metadata.pop(Key, None)

    def list_objects_v2(self, Bucket, Prefix="", MaxKeys=1000, **kw):
        self._guard("list_objects_v2")
        contents = [
            {"Key": k, "Size": len(v), "ETag": self._etag(v),
             "LastModified": self.upload_started_dates.get(k)}
            for k, v in sorted(self.objects.items())
            if k.startswith(Prefix)
        ][:MaxKeys]
        return {"Contents": contents, "IsTruncated": False}

    # ── multipart API ─────────────────────────────────────────────────

    def create_multipart_upload(self, Bucket, Key, Metadata=None):
        self._guard("create_multipart_upload")
        self.upload_counter += 1
        upload_id = f"upload-{self.upload_counter}"
        self.uploads_open[upload_id] = Key
        self.multipart[upload_id] = []
        if Metadata:
            self.metadata[Key] = dict(Metadata)
        return {"UploadId": upload_id}

    def upload_part(self, Bucket, Key, UploadId, PartNumber, Body):
        self._guard("upload_part")
        if UploadId not in self.uploads_open:
            raise RuntimeError("404 no such upload")
        self.multipart[UploadId].append((PartNumber, bytes(Body)))
        return {"ETag": self._etag(bytes(Body))}

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        self._guard("complete_multipart_upload")
        if UploadId not in self.uploads_open:
            raise RuntimeError("404 no such upload")
        parts = sorted(MultipartUpload["Parts"], key=lambda p: p["PartNumber"])
        data = b"".join(self.multipart[UploadId][p["PartNumber"] - 1][1]
                        for p in parts)
        self.objects[Key] = data
        del self.uploads_open[UploadId]
        return {"ETag": f'"multipart-{hashlib.md5(data).hexdigest()}"'}

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self._guard("abort_multipart_upload")
        self.aborted.append(UploadId)
        self.uploads_open.pop(UploadId, None)
        self.multipart.pop(UploadId, None)
        self.metadata.pop(Key, None)

    def list_multipart_uploads(self, Bucket, Prefix="", **kw):
        import datetime as dt

        now = dt.datetime.now(dt.timezone.utc)
        uploads = [
            {"Key": key, "UploadId": uid, "Started": self.upload_started_dates.get(uid, now)}
            for uid, key in sorted(self.uploads_open.items())
            if key.startswith(Prefix)
        ]
        return {"Uploads": uploads, "IsTruncated": False}


def _store(fake=None, prefix="", fail_times=None):
    fake = fake or FakeS3V7(fail_times=fail_times)
    return S3BlobStore("bkt", lambda: fake, prefix=prefix), fake


def test_put_blob_stream_multipart_roundtrip_and_dedup_meta():
    store, fake = _store()
    key = "a" * 64
    data = b"x" * (8 * 1024 * 1024) + b"y" * 1024  # 2 parts
    result = store.put_blob_stream(key, [data[:8 * 1024 * 1024], data[8 * 1024 * 1024:]])
    assert result.put_new is True
    assert store.get_blob(key) == data
    assert len(fake.multipart) == 1
    digest = hashlib.sha256(data).hexdigest()
    meta = store.read_meta(key)
    assert meta["sha256"] == digest
    assert meta["byte_size"] == len(data)


def test_put_blob_stream_zero_bytes_typed_and_no_object():
    store, fake = _store()
    key = "b" * 64
    with pytest.raises(BlobKeyError, match="zero bytes"):
        store.put_blob_stream(key, iter([]))
    assert fake.aborted, "empty stream must abort the multipart upload"
    assert store.exists(key) is False


def test_upload_part_transient_failure_retries_and_succeeds():
    fake = FakeS3V7(fail_times={"upload_part": 1})
    store, _ = _store(fake)
    key = "c" * 64
    store.put_blob_stream(key, iter([b"hello world"]))
    assert store.get_blob(key) == b"hello world"


def test_upload_part_permanent_failure_aborts_cleanly():
    fake = FakeS3V7(fail_times={"upload_part": 99})
    store, _ = _store(fake)
    key = "d" * 64
    with pytest.raises(RuntimeError, match="upload_part"):
        store.put_blob_stream(key, iter([b"doomed"]))
    assert fake.aborted
    assert store.exists(key) is False


def test_put_blob_from_path_streams_with_metadata_digest(tmp_path):
    store, fake = _store()
    key = "e" * 64
    payload = b"z" * (8 * 1024 * 1024) + b"tail"
    src = tmp_path / "big.bin"
    src.write_bytes(payload)
    result = store.put_blob_from_path(key, src)
    assert result.put_new is True
    assert store.get_blob(key) == payload
    # digest 在 create Metadata（complete 前在场 —— R0-13 验收）。
    assert fake.metadata[f"{key[:4]}/{key}.bin"].get("sha256") == \
        hashlib.sha256(payload).hexdigest()
    # 重发布 = 去重命中（不重写）。
    again = store.put_blob_from_path(key, src)
    assert again.put_new is False


def test_get_blob_stream_digest_verification():
    store, _fake = _store()
    key = "f" * 64
    store.put_blob(key, b"stream-me", "binary")
    good = hashlib.sha256(b"stream-me").hexdigest()
    assert b"".join(store.get_blob_stream(key, expected_sha256=good)) == b"stream-me"
    with pytest.raises(BlobDigestMismatch):
        list(store.get_blob_stream(key, expected_sha256="0" * 64))


def test_get_blob_stream_bounded_memory():
    store, _fake = _store()
    key = "7" * 64
    payload = bytes(range(256)) * 4096  # 1 MiB
    store.put_blob(key, payload, "binary")
    digest = hashlib.sha256()
    seen = 0
    tracemalloc.start()
    for chunk in store.get_blob_stream(key, chunk_size=64 * 1024):
        digest.update(chunk)
        seen += len(chunk)
        current, _peak = tracemalloc.get_traced_memory()
        # 消费侧不驻留（本测试也只累计哈希/计数）—— 峰值 O(chunk)。
        assert current < 512 * 1024
    tracemalloc.stop()
    assert seen == len(payload)
    assert digest.hexdigest() == hashlib.sha256(payload).hexdigest()


def test_etag_roundtrip_and_remote_etag():
    store, _fake = _store()
    key = "8" * 64
    store.put_blob(key, b"etag-me", "binary")
    meta = store.read_meta(key)
    remote = store.remote_etag(key)
    # sidecar 记录值 == 当前 head 值（未漂移）；记录值是版本 ETag
    # （非 digest —— R0-14：绝不与 sha256 比较）。
    assert meta.get("etag") == remote
    assert remote != hashlib.sha256(b"etag-me").hexdigest()


def test_iter_objects_bounded_and_relative():
    store, fake = _store(prefix="pfx")
    for i in range(5):
        store.put_blob(f"{i}" * 64, b"v", "json")
    items = list(store.iter_objects(limit=100))
    assert 5 <= len(items)  # blobs + meta sidecars
    assert all(not it["key"].startswith("pfx") for it in items)
    assert any(it["key"].endswith(".json.meta") for it in items)
    bounded = list(store.iter_objects(limit=3))
    assert len(bounded) == 3


def test_sweep_stale_multipart_and_staging():
    import datetime as dt

    fake = FakeS3V7()
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=48)
    store, _ = _store(fake)
    # 一个超龄 + 一个新鲜 multipart。
    fake.uploads_open["u-old"] = f"{'a' * 4}/{'a' * 64}.bin"
    fake.upload_started_dates["u-old"] = old
    fake.uploads_open["u-new"] = f"{'b' * 4}/{'b' * 64}.bin"
    report = store.sweep_stale_multipart_uploads(max_age_hours=24)
    assert report["aborted_count"] == 1
    assert "u-old" in fake.aborted and "u-new" not in fake.aborted
    assert report["kept"] == 1
    # staging 残留：超龄删、新鲜留。
    old_dto = old
    fake.objects["staging/dead/beef.bin"] = b"x"
    fake.upload_started_dates["staging/dead/beef.bin"] = old_dto
    fake.objects["staging/live/beef.bin"] = b"x"
    fake.upload_started_dates["staging/live/beef.bin"] = dt.datetime.now(dt.timezone.utc)
    sweep = store.sweep_stale_staging(max_age_hours=24)
    assert sweep["deleted"] == ["staging/dead/beef.bin"]


def test_default_part_parameters_are_bounded():
    assert DEFAULT_ATTEMPTS >= 2  # 瞬态可恢复
    from app.services.s3_blob_store import DEFAULT_PART_SIZE, MAX_PARTS

    assert DEFAULT_PART_SIZE >= 1024 * 1024
    assert MAX_PARTS <= 10_000
