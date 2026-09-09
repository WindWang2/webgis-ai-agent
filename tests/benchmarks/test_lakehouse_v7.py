"""Spatial Lakehouse V7 perf harness (nightly-only, ADR-0119).

风格契约（test_lakehouse_v6.py 同款）：synthetic fixtures、确定性种子、
有界内存；**结构性证据优先**（chunk 触达计数 / 分片组合身份 /
GC 元数据级触达），墙钟只做宽比带。nightly only 的理由与 V6 相同
（可选 geo 栈 + 与 PR 预算的隔离 —— 见
tests/test_ci_perf_coverage_contract.py::NIGHTLY_ONLY_PERF_FILES）。

结构断言：
- 10k+ chunks 经分片组合发布（§15 大元数据生产路径）—— 峰值内存有界、
  组合身份确定性、sample scrub 触达 ∝ K；
- GC plan 只触 manifest 元数据（chunk 内容零读 —— 计数器证明）。
"""
from __future__ import annotations

import tracemalloc

import pytest

pytestmark = pytest.mark.perf


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


def test_10k_chunk_sharded_composition_bounded(env):
    """10k chunks → 25 shard × 400 条 + virtual 组合（结构性有界）。"""
    from app.services.lakehouse.data_object import normalize_owner_scope
    from app.services.lakehouse.dr import scrub_object
    from app.services.lakehouse.virtual_object import (
        publish_sharded_object,
    )

    n = 10_000
    files = {f"chunks/c{i:05d}.bin": bytes([i % 256]) * 64 for i in range(n)}
    tracemalloc.start()
    result = publish_sharded_object(
        files,
        owner_scope=normalize_owner_scope(session_id="sess-perf7"),
    )
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    # 结构性：分片数 = ceil(10_000/400) = 25；组合身份确定。
    assert len(result["shards"]) == 25
    assert len(result["composite"]["data_object_id"]) == 64
    # 组合体深度校验：每个 shard 的 digest 校验（采样触达 ∝ K 而非总量
    # —— deep verify 走 verify_data_object 逐 shard，触及 25 shard manifest
    # + 各自全 blob（shard 内 digest 校验是完整性的最小充分证据））。
    report = scrub_object(result["composite"]["data_object_id"], mode="sample")
    assert report["state"] == "verified"
    # 峰值内存有界：分片发布不驻留全部 10k 条目结构两遍以上。
    assert peak < 128 * 1024 * 1024
    # 确定性：同内容重发布 = 组合 id 命中（CAS 身份）。
    again = publish_sharded_object(
        files, owner_scope=normalize_owner_scope(session_id="sess-perf7"),
    )
    assert again["composite"]["data_object_id"] == \
        result["composite"]["data_object_id"]


def test_gc_plan_is_metadata_only(env):
    """GC plan 绝不读 chunk 内容（只触 manifest 元数据 —— 计数器证明）。"""
    import app.services.s3_blob_store as s3_mod
    from app.services.lakehouse.data_object import (
        normalize_owner_scope,
        publish_data_object,
    )
    from app.services.lakehouse.lakehouse_gc import plan_gc

    chunk_bytes = 256 * 1024  # 大 chunk：plan 若读内容会显著放大
    files = {
        f"chunks/c{i}.bin": bytes([i % 256]) * chunk_bytes for i in range(8)
    }
    publish_data_object(
        files, kind="zarr_cube",
        owner_scope=normalize_owner_scope(session_id="sess-perf7"),
    )

    live_store = s3_mod.get_object_store()
    real_get = live_store.get_blob
    reads: list = []

    def counting_get(key, expected_sha256=None):
        reads.append(str(key))
        return real_get(key, expected_sha256=expected_sha256)

    live_store.get_blob = counting_get
    try:
        plan = plan_gc(grace_hours=0.0)
    finally:
        live_store.get_blob = real_get
    # 结构性：plan 只读 manifest 元数据（扫描 + 候选重读 = 2 次），
    # 8 个 256KB chunk 的内容读取次数为 0 —— GC 成本 ∝ manifest 数，
    # 与 chunk 字节无关（§13：GC 不是 O(all bytes)）。
    assert plan["scanned_manifests"] == 1
    assert len(reads) == 2
    plan2 = plan_gc(grace_hours=0.0)
    assert plan2["token"] == plan["token"]  # 确定性
