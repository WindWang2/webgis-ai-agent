"""Lakehouse V6 Wave 9 — workspace durability for lakehouse refs.

覆盖面（ADR-0118 Workspace V5 durability）：
- fabric-parquet：binary lane（stat 预检 → 读 → sha256 → CAS put）+ 原子
  restore 写回同一磁盘位；
- cube：manifest lane（manifest-only 发布，零字节拷贝）+ verify 四态 +
  restore 从 BlobStore 逐 blob 校验物化（进程/会话死亡后 reopen）；
- 预算：超预算诚实 "budget" 跳过；
- 端到端：快照 save → 删除工作载荷（模拟会话死亡）→ restore(register)
  → 载荷复活 + digest 校验。
"""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.services.artifact_registry import (
    fabric_parquet_ref_exists,
    register_artifact,
    probe_ref,
)
from app.services.workspace.durability import (
    INTEGRITY_VERIFIED,
    materialize_ref_payload,
    read_back_payload,
    restore_cube_store,
    restore_fabric_parquet_file,
    verify_durable_pointer,
)
from app.services.workspace.snapshot import (
    get_workspace_snapshot_service,
    reset_workspace_snapshot_service,
)

pytest.importorskip("zarr")
pytest.importorskip("pyarrow")


@pytest.fixture(autouse=True)
def _reset():
    reset_workspace_snapshot_service()
    yield
    reset_workspace_snapshot_service()


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def _write_slice(path, value, h=48, w=64):
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=1,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(0, h, 1, 1), nodata=-9999.0,
    ) as dst:
        dst.write(np.full((h, w), value, dtype="float32"), 1)
    return str(path)


@pytest.mark.asyncio
async def test_fabric_parquet_durability_cycle(data_dir):
    from app.services.data_fabric.materialization_service import materialization_service
    from app.services.data_fabric.vector_carrier import features_to_arrow

    feats = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
         "properties": {"i": i}} for i in range(3)
    ]
    table = features_to_arrow(feats, crs="EPSG:4326")
    res = await materialization_service.materialize_geoparquet("sess-dur", table, "T")
    ref = res["ref"]
    await register_artifact("sess-dur", artifact_id=ref,
                            artifact_type="fabric_geoparquet")
    original_bytes = open(res["path"], "rb").read()

    # 物化 → 指针（binary lane）。
    pointer, reason = await materialize_ref_payload("sess-dur", ref)
    assert pointer is not None and reason is None
    assert pointer["content_type"] == "binary"
    assert verify_durable_pointer(pointer) == INTEGRITY_VERIFIED

    # 工作载荷死亡 → restore 原子写回，字节一致，ref 复活。
    import os

    os.unlink(res["path"])
    assert not fabric_parquet_ref_exists("sess-dur", ref)
    payload = read_back_payload(pointer)
    assert payload is not None and payload.data == original_bytes
    assert await restore_fabric_parquet_file("sess-dur", ref, payload.data)
    assert fabric_parquet_ref_exists("sess-dur", ref)
    assert open(res["path"], "rb").read() == original_bytes
    assert await probe_ref("sess-dur", ref) == {"kind": "fabric_geoparquet", "exists": True}


@pytest.mark.asyncio
async def test_cube_durability_cycle(data_dir, tmp_path):
    from app.services.lakehouse.cube_service import build_session_cube, read_session_cube_window

    src = data_dir / "src"
    src.mkdir(parents=True, exist_ok=True)
    time_sources = [
        {"time": "2024-01", "source": _write_slice(src / "t1.tif", 1.0)},
        {"time": "2024-02", "source": _write_slice(src / "t2.tif", 2.0)},
    ]
    res = await build_session_cube("sess-cd", time_sources=time_sources, title="T")
    ref = res["ref"]
    import shutil

    store = data_dir / "sess-cd" / "lakehouse-cubes" / res["cube_id"]

    # 物化 → manifest 指针（零字节拷贝 lane）。
    pointer, reason = await materialize_ref_payload("sess-cd", ref)
    assert pointer is not None and reason is None
    assert pointer["content_type"] == "lakehouse_manifest"
    assert verify_durable_pointer(pointer) == INTEGRITY_VERIFIED

    # 会话死亡（store 目录被清）→ manifest 读回 → 从 BlobStore 复原 store。
    manifest = read_back_payload(pointer)
    assert isinstance(manifest, dict) and manifest["kind"] == "zarr_cube"
    shutil.rmtree(store)
    assert await probe_ref("sess-cd", ref) is None
    assert await restore_cube_store(
        "sess-cd", ref, manifest,
        data_object_id=str(pointer["content_payload_sha256"]),
    )
    assert await probe_ref("sess-cd", ref) == {"kind": "lakehouse_cube", "exists": True}
    # 复原后窗口读值一致（逐 blob digest 校验通过后的字节）。
    out = await read_session_cube_window("sess-cd", ref, time=slice(1, 2))
    assert float(out["bands"]["b1"][0, 0, 0]) == 2.0


@pytest.mark.asyncio
async def test_budget_honest_skips(data_dir, tmp_path):
    from app.services.data_fabric.materialization_service import materialization_service
    from app.services.data_fabric.vector_carrier import features_to_arrow

    feats = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
         "properties": {"i": i}} for i in range(2)
    ]
    table = features_to_arrow(feats, crs="EPSG:4326")
    res = await materialization_service.materialize_geoparquet("sess-budget", table, "T")
    pointer, reason = await materialize_ref_payload(
        "sess-budget", res["ref"], budget_bytes=1
    )
    assert pointer is None and reason == "budget"


@pytest.mark.asyncio
async def test_snapshot_end_to_end_reopen(data_dir, tmp_path):
    """端到端：save 快照 → 工作载荷死亡（进程/会话重启模拟）→
    restore(register) → GeoParquet 载荷复活。"""
    from app.services.data_fabric.materialization_service import materialization_service
    from app.services.data_fabric.vector_carrier import features_to_arrow

    sid = "sess-e2e"
    feats = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
         "properties": {"i": i}} for i in range(2)
    ]
    table = features_to_arrow(feats, crs="EPSG:4326")
    res = await materialization_service.materialize_geoparquet(sid, table, "T")
    ref = res["ref"]
    await register_artifact(sid, artifact_id=ref, artifact_type="fabric_geoparquet",
                            producer_tool="materialize_geoparquet")
    original = open(res["path"], "rb").read()

    svc = get_workspace_snapshot_service()
    snap = await svc.save_snapshot(sid, label="pre-restart")
    assert snap is not None
    assert ref in {c.artifact_id for c in snap.artifact_contracts}

    # 会话死亡：工作载荷消失。
    import os

    os.unlink(res["path"])

    result = await svc.restore_snapshot(sid, snap.snapshot_id, mode="register")
    assert result.get("error") is None
    # 载荷复活（digest 校验写回同一磁盘位）。
    assert fabric_parquet_ref_exists(sid, ref), result
    assert open(res["path"], "rb").read() == original
    assert await probe_ref(sid, ref) == {"kind": "fabric_geoparquet", "exists": True}
