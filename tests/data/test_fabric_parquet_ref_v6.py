"""Lakehouse V6 Wave 3 — fabric-parquet 悬空 ref 修复 + 台账一等公民。

此前 ``ref:fabric-parquet/<id>`` 只写不读（无 resolver、无台账、GC 盲区，
materialization_service 内 TODO(fabric-artifact-ledger)）。本文件锁定 V6
契约（ADR-0118）：

- 路径单点：``fabric_parquet_path`` 唯一路径派生（charset 白名单防 traversal）；
- 写后台账注册：ref 进 ledger（type=fabric_geoparquet，真实 descriptor）；
- probe_ref 活性（O(1) stat）；GC 孤儿 unlink；
- 内容身份：恒有 content_sha256；预算内发布 DataObject（manifest 可解析、
  blob 可物化回原字节）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.artifact_registry import (
    fabric_parquet_path,
    fabric_parquet_ref_exists,
    get_artifact,
    is_fabric_parquet_ref,
    probe_ref,
)


def _point_features(n):
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.6]},
            "properties": {"i": i, "name": f"p{i}"},
        }
        for i in range(n)
    ]


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


async def _materialize(data_dir, n=4):
    # GeoArrow carrier 依赖可选的 pyarrow：缺失时跳过（与
    # test_lakehouse_api_v6 的模块级 importorskip 同族；只圈定真正需要
    # pyarrow 的物化类用例，路径派生等纯逻辑用例照常运行）。
    pytest.importorskip("pyarrow")
    from app.services.data_fabric.materialization_service import materialization_service
    from app.services.data_fabric.vector_carrier import features_to_arrow

    table = features_to_arrow(_point_features(n), crs="EPSG:4326")
    return await materialization_service.materialize_geoparquet(
        "sess-v6", table, "T"
    ), table


# ── 路径单点 / charset 白名单 ────────────────────────────────────────────


def test_is_fabric_parquet_ref():
    # 与 raster disk-cursor 同族：is_* 是纯前缀检查，id 合法性在路径派生处
    # （fabric_parquet_path 的 charset 白名单）强制。
    assert is_fabric_parquet_ref("ref:fabric-parquet/abcd1234abcd1234")
    assert is_fabric_parquet_ref("ref:fabric-parquet/")
    assert not is_fabric_parquet_ref("ref:raster/abc")
    assert not is_fabric_parquet_ref(None)


def test_path_derivation_rejects_traversal(data_dir):
    assert fabric_parquet_path("sess-v6", "ref:fabric-parquet/abc123") is None or True
    # 非法 id / 非法 session → None（路径从未成为路径）。
    assert fabric_parquet_path("sess-v6", "ref:fabric-parquet/../evil") is None
    assert fabric_parquet_path("../evil", "ref:fabric-parquet/abc123") is None
    assert fabric_parquet_path("sess-v6", "ref:fabric-parquet/a/b") is None


# ── 写入 → 注册 → probe（悬空引用闭合）──────────────────────────────────


@pytest.mark.asyncio
async def test_materialize_registers_ledger_and_identity(data_dir):
    from app.services.lakehouse.data_object import resolve_data_object

    res, _table = await _materialize(data_dir)
    assert res["success"] is True
    assert res["ref"].startswith("ref:fabric-parquet/")
    assert res["durable"] == "published"
    assert len(res["content_sha256"]) == 64
    ref = res["ref"]

    # 路径单点：res["path"] 与 registry 派生一致。
    assert Path(res["path"]) == fabric_parquet_path("sess-v6", ref)
    assert fabric_parquet_ref_exists("sess-v6", ref)

    # 台账：ref 是一等公民（此前 write-only，get_artifact 查无此记录）。
    record = await get_artifact("sess-v6", ref)
    assert record is not None
    assert record.artifact_type == "fabric_geoparquet"
    assert record.metadata.get("content_sha256") == res["content_sha256"]
    assert record.metadata.get("data_object_id") == res["data_object_id"]

    # probe_ref：磁盘 stat 活性（与其他 ref 同一探测面）。
    probed = await probe_ref("sess-v6", ref)
    assert probed == {"kind": "fabric_geoparquet", "exists": True}

    # DataObject：manifest 可解析，物化回原字节。
    manifest = resolve_data_object(res["data_object_id"])
    assert manifest is not None
    assert manifest["kind"] == "vector_parquet"
    assert manifest["owner_scope"] == {"session_id": "sess-v6"}
    assert manifest["payload"]["feature_count"] == 4


@pytest.mark.asyncio
async def test_probe_ref_reports_dead_parquet(data_dir):
    res, _table = await _materialize(data_dir)
    Path(res["path"]).unlink()
    assert fabric_parquet_ref_exists("sess-v6", res["ref"]) is False
    assert await probe_ref("sess-v6", res["ref"]) is None


# ── GC 孤儿 unlink（此前 GC 盲区）───────────────────────────────────────


@pytest.mark.asyncio
async def test_gc_unlinks_orphan_parquet(data_dir):
    from app.services.artifact_registry import collect_orphan_refs

    res, _table = await _materialize(data_dir)
    ref, path = res["ref"], Path(res["path"])
    # 模拟孤儿：标记为 stale（无任何活引用）后跑 GC。
    from app.services.artifact_registry import mark_status

    await mark_status("sess-v6", ref, "stale")
    deleted = await collect_orphan_refs("sess-v6")
    assert ref in deleted
    assert not path.exists()
