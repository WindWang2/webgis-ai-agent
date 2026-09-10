"""GeoCompute V8 — artifact exchange + checkpoint spill（Phase E）。

覆盖：内容寻址 put-if-absent / zlib 压缩 / digest 校验读 / 瞬态重试 /
TTL 清扫 / NodeResultStore spill stub 重hydration（含失败退化）。
exchange 停用（无 root）→ V7 语义逐字节保留。
"""

from __future__ import annotations

import json
import zlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.geocompute.cluster.exchange import (
    ArtifactExchange,
    SpillHandle,
    get_exchange,
    reset_exchange_for_tests,
    spill_threshold_bytes,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_exchange_for_tests()
    yield
    reset_exchange_for_tests()


@pytest.fixture()
def ex(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'v8-ex.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    exchange = ArtifactExchange(root=str(tmp_path / "blobs"), factory=factory,
                                ttl_s=3600.0)
    yield exchange, factory
    eng.dispose()


class TestExchangeCore:
    def test_put_get_roundtrip_raw_and_zlib(self, ex):
        exchange, _ = ex
        data = json.dumps({"features": [{"a": i} for i in range(100)]}).encode()
        handle = exchange.put_bytes(data, kind="spill")
        assert handle.codec == "zlib"  # 可压缩载荷默认 zlib
        assert handle.size_bytes == len(data)
        assert exchange.get_bytes(handle) == data

    def test_put_if_absent_content_addressed(self, ex):
        """同内容两次 put → 同一键（全局去重），存储只有一份字节。"""
        exchange, factory = ex
        data = b"hello geocompute v8"
        h1 = exchange.put_bytes(data, run_id="r1", kind="spill")
        h2 = exchange.put_bytes(data, run_id="r2", kind="spill")
        assert h1.key == h2.key
        from app.models.db_model import GeoComputeArtifact

        with factory() as db:
            rows = db.query(GeoComputeArtifact).all()
            assert len(rows) == 1  # 内容寻址去重（元数据行唯一）
            assert rows[0].size_bytes == len(data)

    def test_oversize_payload_stays_raw(self, ex, monkeypatch):
        import app.services.geocompute.cluster.exchange as ex_mod

        monkeypatch.setattr(ex_mod, "MAX_COMPRESS_BYTES", 10)
        exchange, _ = ex
        data = b"12345678901234567890"  # > 10B 钳制
        handle = exchange.put_bytes(data)
        assert handle.codec == "raw"
        assert exchange.get_bytes(handle) == data

    def test_get_missing_fails_typed(self, ex):
        exchange, _ = ex
        handle = SpillHandle(key="0" * 64, size_bytes=3, codec="raw")
        with pytest.raises(RuntimeError, match="unreadable"):
            exchange.get_bytes(handle)

    def test_size_mismatch_fails(self, ex):
        exchange, _ = ex
        data = b"abc"
        handle = exchange.put_bytes(data)
        bad = SpillHandle(key=handle.key, size_bytes=99, codec="raw")
        with pytest.raises(RuntimeError, match="size mismatch|unreadable"):
            exchange.get_bytes(bad)

    def test_cleanup_expired(self, ex):
        exchange, factory = ex
        data = b"transient payload"
        handle = exchange.put_bytes(data)
        # 强制过期（expires_at < now）
        from datetime import datetime, timezone

        from app.models.db_model import GeoComputeArtifact

        past = datetime.now(timezone.utc).replace(tzinfo=None)
        with factory() as db:
            import sqlalchemy as sa

            db.execute(
                sa.update(GeoComputeArtifact)
                .where(GeoComputeArtifact.artifact_key == handle.key)
                .values(expires_at=past - __import__("datetime").timedelta(seconds=10))
            )
            db.commit()
        removed = exchange.cleanup_expired()
        assert removed == 1
        # blob 字节已删：读回失败（类型化）
        with pytest.raises(RuntimeError):
            exchange.get_bytes(handle)


class TestDisabledExchange:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("WEBGIS_EXCHANGE_ROOT", raising=False)
        exchange = ArtifactExchange()
        assert exchange.enabled is False
        assert exchange.cleanup_expired() == 0
        with pytest.raises(RuntimeError, match="not enabled"):
            exchange.put_bytes(b"x")

    def test_singleton_respects_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WEBGIS_EXCHANGE_ROOT", str(tmp_path / "x"))
        assert get_exchange().enabled is True
        monkeypatch.delenv("WEBGIS_EXCHANGE_ROOT", raising=False)
        reset_exchange_for_tests()
        assert get_exchange().enabled is False

    def test_threshold_env(self, monkeypatch):
        monkeypatch.setenv("WEBGIS_EXCHANGE_SPILL_BYTES", "1048576")
        assert spill_threshold_bytes() == 1048576
        monkeypatch.setenv("WEBGIS_EXCHANGE_SPILL_BYTES", "0")
        assert spill_threshold_bytes() == 0


# ═══════════════════════ NodeResultStore spill ══════════════════════


class TestNodeResultStoreSpill:
    def _store(self, tmp_path, max_bytes):
        from app.services.geocompute.executor import NodeResultStore

        exchange = ArtifactExchange(root=str(tmp_path / "blobs"))
        return NodeResultStore(max_entries=8, max_bytes=max_bytes,
                               exchange=exchange)

    def _payload(self, n):
        return {"features": [{"v": "x" * 40, "i": i} for i in range(n)],
                "metadata": {"k": 1}}

    def test_oversize_spilled_and_rehydrated(self, tmp_path):
        store = self._store(tmp_path, max_bytes=1000)
        big = self._payload(200)
        key = "k-big"
        store.put(key, big)
        with store._lock:
            entry = store._entries.get(key)
        assert entry is not None and entry.get("__spilled__")
        # stub 不携带载荷本体
        assert "features" not in entry
        got = store.get(key)
        assert got is not None
        assert got["features"] == big["features"]
        assert got["metadata"] == {"k": 1}

    def test_small_payload_stays_inline(self, tmp_path):
        store = self._store(tmp_path, max_bytes=1_000_000)
        store.put("k", self._payload(3))
        with store._lock:
            entry = store._entries["k"]
        assert "__spilled__" not in entry

    def test_spill_disabled_drops_oversize(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WEBGIS_EXCHANGE_ROOT", raising=False)
        from app.services.geocompute.executor import NodeResultStore

        store = NodeResultStore(max_entries=8, max_bytes=1000)
        store.put("k", self._payload(200))
        assert store.get("k") is None  # V7 语义：超预算直接丢弃

    def test_spill_blob_corruption_degrades_to_miss(self, tmp_path):
        """blob 字节被删（TTL 清扫竞态）→ rehydrate 失败 → 复用 miss
        （诚实重算），绝不返回半份载荷。"""
        store = self._store(tmp_path, max_bytes=1000)
        store.put("k", self._payload(200))
        stub = store.get  # noqa: F841
        with store._lock:
            entry = store._entries["k"]
        stub_info = entry["__spilled__"]
        # 直接删 blob（绕过 exchange 元数据）
        store._exchange._store.delete_blob(stub_info["key"])
        assert store.get("k") is None
