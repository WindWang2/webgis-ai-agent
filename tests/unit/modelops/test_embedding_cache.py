"""EmbeddingCache 单元测试（Platform 11 / WP-D）。

oracle 独立性：期望直接来自手算（键敏感性用不同输入构造、LRU 用显式
时钟、篡改用字节覆写），不复用被测实现产生期望。
"""
from __future__ import annotations


import numpy as np
import pytest

from app.lib.modelops.errors import ModelOpsError
from app.services.modelops.embedding_cache import (
    EmbeddingCache,
    build_embed_cache_key,
)

OWNER = {"session_id": "s1"}
OWNER2 = {"session_id": "s2"}


def _key(window=(0, 0, 32, 32), model_digest="m" * 64, asset="a" * 64,
         preprocess="p" * 64, semver="1.0.0"):
    return build_embed_cache_key(
        model_digest=model_digest,
        provider_semantic_version=semver,
        asset_sha256=asset,
        preprocess_digest=preprocess,
        grid={"width": 100, "height": 100, "crs": "EPSG:4326",
              "transform": (1.0, 0.0, 0.0, 0.0, -1.0, 0.0)},
        window=window,
        owner_scope=OWNER,
    )


def test_put_get_roundtrip_preserves_shape_dtype(tmp_path):
    cache = EmbeddingCache(tmp_path, max_entries=8)
    vec = np.linspace(0.0, 1.0, 16, dtype=np.float32).reshape(4, 4)
    assert cache.put(_key(), vec, owner_scope=OWNER, model_id="m", asset_sha="a" * 64)
    got = cache.get(_key(), owner_scope=OWNER)
    assert got is not None
    np.testing.assert_array_equal(got, vec)
    assert got.dtype == np.float32 and got.shape == (4, 4)
    stats = cache.stats()
    assert stats["entries"] == 1 and stats["hits"] == 1 and stats["misses"] == 0


def test_key_sensitivity_to_every_axis():
    base = dict(
        model_digest="m" * 64, provider_semantic_version="1.0.0",
        asset_sha256="a" * 64, preprocess_digest="p" * 64,
        grid={"width": 100, "height": 100, "crs": "EPSG:4326",
              "transform": (1.0, 0.0, 0.0, 0.0, -1.0, 0.0)},
        window=(0, 0, 32, 32), owner_scope=OWNER,
    )
    k0 = build_embed_cache_key(**base)
    assert k0 == build_embed_cache_key(**base)  # 确定性
    variants = [
        {**base, "model_digest": "n" * 64},
        {**base, "provider_semantic_version": "2.0.0"},
        {**base, "asset_sha256": "b" * 64},
        {**base, "preprocess_digest": "q" * 64},
        {**base, "grid": {**base["grid"], "width": 101}},
        {**base, "grid": {**base["grid"], "transform": (2.0, 0.0, 0.0, 0.0, -1.0, 0.0)}},
        {**base, "window": (1, 0, 32, 32)},
        {**base, "owner_scope": OWNER2},
    ]
    for variant in variants:
        assert build_embed_cache_key(**variant) != k0


def test_miss_on_absent_entry(tmp_path):
    cache = EmbeddingCache(tmp_path)
    assert cache.get(_key(), owner_scope=OWNER) is None
    assert cache.stats()["misses"] == 1


def test_digest_tamper_evicts_and_misses(tmp_path):
    cache = EmbeddingCache(tmp_path)
    key = _key()
    cache.put(key, np.ones((2, 2), dtype=np.float32), owner_scope=OWNER)
    # 篡改 .npy 内容（保持可解析）→ digest 失配 → 驱逐 + miss。
    npy = next(tmp_path.rglob(f"{key}.npy"))
    tampered = np.zeros((2, 2), dtype=np.float32)
    with open(npy, "wb") as fh:
        np.save(fh, tampered, allow_pickle=False)
    assert cache.get(key, owner_scope=OWNER) is None
    stats = cache.stats()
    assert stats["digest_failures"] == 1 and stats["entries"] == 0


def test_lru_entries_cap_eviction(tmp_path):
    clock = {"t": 0.0}
    cache = EmbeddingCache(tmp_path, max_entries=2, clock=lambda: clock["t"])
    k1, k2, k3 = _key((0, 0, 8, 8)), _key((0, 8, 8, 8)), _key((8, 0, 8, 8))
    vec = np.zeros(4, dtype=np.float32)
    cache.put(k1, vec, owner_scope=OWNER)
    clock["t"] = 1.0
    cache.put(k2, vec, owner_scope=OWNER)
    clock["t"] = 2.0
    cache.get(k1, owner_scope=OWNER)  # k1 更新 last_used → k2 最旧
    clock["t"] = 3.0
    cache.put(k3, vec, owner_scope=OWNER)  # 超界 → 驱逐 k2
    assert cache.get(k1, owner_scope=OWNER) is not None
    assert cache.get(k2, owner_scope=OWNER) is None
    assert cache.get(k3, owner_scope=OWNER) is not None
    assert cache.stats()["entries"] == 2


def test_bytes_cap_eviction(tmp_path):
    vec = np.ones((64,), dtype=np.float64)  # 512 B/条
    cache = EmbeddingCache(tmp_path, max_entries=10, max_bytes=1280)
    for i in range(4):
        cache.put(_key((i, 0, 8, 8)), vec, owner_scope=OWNER)
    stats = cache.stats()
    assert stats["entries"] <= 2  # 1280/512 = 2.5 → ≤2 条
    assert stats["bytes"] <= 1280


def test_per_entry_cap_refuses_oversized(tmp_path):
    cache = EmbeddingCache(tmp_path, max_entry_bytes=64)
    big = np.ones((1024,), dtype=np.float64)  # 8 KiB
    key = _key()
    assert cache.put(key, big, owner_scope=OWNER) is False
    assert cache.get(key, owner_scope=OWNER) is None
    assert cache.stats()["entries"] == 0


def test_partial_invalidation_by_model_and_asset(tmp_path):
    cache = EmbeddingCache(tmp_path, max_entries=16)
    vec = np.zeros(4, dtype=np.float32)
    ka = _key((0, 0, 8, 8), model_digest="m" * 64, asset="asset-a")
    kb = _key((0, 8, 8, 8), model_digest="m" * 64, asset="asset-b")
    kc = _key((8, 0, 8, 8), model_digest="upgraded" + "m" * 56, asset="asset-a")
    cache.put(ka, vec, owner_scope=OWNER, model_id="m1", model_fp="m" * 64, asset_sha="asset-a")
    cache.put(kb, vec, owner_scope=OWNER, model_id="m1", model_fp="m" * 64, asset_sha="asset-b")
    cache.put(kc, vec, owner_scope=OWNER, model_id="m2", model_fp="upgraded" + "m" * 56, asset_sha="asset-a")
    # 模型升级（model_id）：清 m1 的全部（ka/kb）。
    assert cache.invalidate(model_id="m1") == 2
    assert cache.get(kc, owner_scope=OWNER) is not None
    # 资产变更：清 asset-a 剩余（kc）。
    assert cache.invalidate(asset_sha="asset-a") == 1
    assert cache.stats()["entries"] == 0


def test_put_failure_leaves_no_tmp_residue(tmp_path, monkeypatch):
    cache = EmbeddingCache(tmp_path)
    import os as _os

    def failing_replace(src, dst):
        raise OSError("simulated failure")

    monkeypatch.setattr(_os, "replace", failing_replace)
    key = _key()
    assert cache.put(key, np.ones(4, dtype=np.float32), owner_scope=OWNER) is False
    residue = list(tmp_path.rglob("*.tmp*"))
    assert residue == [], [str(p) for p in residue]
    # 索引不含半写条目。
    monkeypatch.undo()
    assert cache.get(key, owner_scope=OWNER) is None


def test_owner_isolation_and_scope_validation(tmp_path):
    cache = EmbeddingCache(tmp_path)
    key = _key()
    cache.put(key, np.ones(4, dtype=np.float32), owner_scope=OWNER)
    assert cache.get(key, owner_scope=OWNER2) is None  # 跨 owner 永不命中
    with pytest.raises(ModelOpsError, match="invalid owner scope"):
        cache.get(key, owner_scope={"session_id": "../escape"})


def test_index_rescan_on_restart_and_orphan_cleanup(tmp_path):
    cache = EmbeddingCache(tmp_path, max_entries=8)
    key = _key()
    cache.put(key, np.arange(6, dtype=np.float32), owner_scope=OWNER)
    # 孤儿 .npy（无 sidecar）：重启扫描时清除。
    orphan_dir = tmp_path / "session_id-s1" / "zz"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    orphan = orphan_dir / ("z" * 64 + ".npy")
    orphan.write_bytes(b"garbage")
    cache2 = EmbeddingCache(tmp_path, max_entries=8)
    got = cache2.get(key, owner_scope=OWNER)
    assert got is not None
    np.testing.assert_array_equal(got, np.arange(6, dtype=np.float32))
    assert not orphan.exists()
    assert cache2.stats()["entries"] == 1


def test_sidecar_is_commit_marker(tmp_path):
    """sidecar 缺失 = 条目不可见（半写 .npy 永不产出污染向量）。"""
    cache = EmbeddingCache(tmp_path)
    key = _key()
    cache.put(key, np.ones(4, dtype=np.float32), owner_scope=OWNER)
    sidecar = next(tmp_path.rglob(f"{key}.json"))
    sidecar.unlink()
    cache2 = EmbeddingCache(tmp_path, max_entries=8)
    assert cache2.get(key, owner_scope=OWNER) is None
    meta_files = list(tmp_path.rglob(f"{key}.json"))
    assert meta_files == []


def test_concurrent_put_get(tmp_path):
    import threading

    cache = EmbeddingCache(tmp_path, max_entries=128)
    errors = []

    def worker(w: int) -> None:
        try:
            for i in range(20):
                key = _key((w, i, 8, 8))
                cache.put(key, np.full((4,), float(w), dtype=np.float32),
                          owner_scope=OWNER)
                got = cache.get(key, owner_scope=OWNER)
                assert got is None or got[0] == float(w)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert cache.stats()["entries"] == 80


def test_stats_shape_stable(tmp_path):
    cache = EmbeddingCache(tmp_path)
    stats = cache.stats()
    assert set(stats) == {
        "entries", "bytes", "max_entries", "max_bytes",
        "hits", "misses", "evictions", "digest_failures",
    }
