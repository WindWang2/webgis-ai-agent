"""Lakehouse V6 Wave 10 — DataObject lineage / 可复现性证据。

覆盖面（ADR-0118）：
- manifest 血缘面：source_refs（输入边）+ producer（capability/algorithm，
  写时 redact）+ input_fingerprint（复用键）+ environment_fingerprint；
- 环境指纹：确定性（同环境同值）、有界（无路径/主机名/时间/凭据）；
- 复现键投影：同 (input_fingerprint, env, kind) 可判定可复现；
- cube 发布血缘：build_session_cube 的 manifest.source_refs = 输入栅格指纹。
"""
from __future__ import annotations

import json

import pytest

from app.services.durable_blob_store import FilesystemBlobStore
from app.services.lakehouse.data_object import (
    build_object_manifest,
    compute_object_reuse_fingerprint,
    lakehouse_environment_fingerprint,
    normalize_owner_scope,
    publish_data_object,
)


@pytest.fixture()
def blob_store(tmp_path):
    return FilesystemBlobStore(root=tmp_path / "blobs")


def test_manifest_carries_lineage_evidence():
    manifest = build_object_manifest(
        kind="vector_parquet",
        owner_scope=normalize_owner_scope(session_id="s1"),
        entries=[("data.parquet", "a" * 64, 3)],
        producer={"capability": "fabric.materialize",
                  "algorithm": "geoparquet_write_v6"},
        source_refs=["fingerprint:src1", "fingerprint:src2"],
        input_fingerprint="reuse:v3:abc",
    )
    assert manifest["source_refs"] == ["fingerprint:src1", "fingerprint:src2"]
    assert manifest["producer"]["algorithm"] == "geoparquet_write_v6"
    assert manifest["input_fingerprint"] == "reuse:v3:abc"
    assert len(manifest["environment_fingerprint"]) == 64
    raw = json.dumps(manifest, sort_keys=True)
    for banned in ("/tmp", "localhost", "secret", "token", "api_key"):
        assert banned not in raw


def test_environment_fingerprint_deterministic_and_bounded():
    a = lakehouse_environment_fingerprint()
    b = lakehouse_environment_fingerprint()
    assert a == b
    assert len(a) == 64


def test_environment_participates_in_identity(blob_store):
    """同内容在\"不同环境\"下 = 不同 DataObject id（可复现性语义）。

    用 monkeypatch 模拟 pyarrow 版本漂移 —— 环境指纹随之变化，身份分离。
    """
    import sys as _sys

    owner = normalize_owner_scope(session_id="s1")
    first = publish_data_object(
        {"d.bin": b"x"}, kind="vector_parquet", owner_scope=owner,
        store=blob_store,
    )
    fake = type(_sys)("fake_pa")
    fake.__version__ = "99.0.0"
    monkey = pytest.MonkeyPatch()
    monkey.setitem(_sys.modules, "pyarrow", fake)
    try:
        second = publish_data_object(
            {"d.bin": b"x"}, kind="vector_parquet", owner_scope=owner,
            store=blob_store,
        )
    finally:
        monkey.undo()
    # 字节 blob 共享（内容寻址去重不变），逻辑身份分离（环境参与身份）。
    assert first.data_object_id != second.data_object_id
    manifest_a = blob_store.get_blob(first.data_object_id)
    manifest_b = blob_store.get_blob(second.data_object_id)
    assert json.loads(manifest_a)["environment_fingerprint"] != \
        json.loads(manifest_b)["environment_fingerprint"]


def test_reproduction_key_projection():
    """复现键 = (input_fingerprint, environment, kind)：全部一致 ⇒ 可复现判定
    的比较面（与 run-level reproducibility verdicts 同语义族）。"""
    env = lakehouse_environment_fingerprint()
    common = dict(
        operation="cube.build", operation_version="v6",
        input_fingerprints={"0": "1" * 64},
        normalized_args={"times": ["t1", "t2"]},
        owner_scope={"session_id": "s"},
    )
    k1 = compute_object_reuse_fingerprint(**common)
    k2 = compute_object_reuse_fingerprint(**common)
    assert k1 == k2 and env  # 同输入同环境 → 同复用键


@pytest.mark.asyncio
async def test_cube_manifest_lineage(tmp_path, monkeypatch):
    """cube 生产血缘：source_refs = 输入栅格指纹（source → derived 边）。"""
    pytest.importorskip("zarr")
    pytest.importorskip("rasterio")
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    src = tmp_path / "data" / "src"
    src.mkdir(parents=True)
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    sources = []
    for i, v in enumerate((1.0, 2.0)):
        p = src / f"t{i}.tif"
        with rasterio.open(
            str(p), "w", driver="GTiff", width=64, height=48, count=1,
            dtype="float32", crs="EPSG:4326",
            transform=from_origin(0, 48, 1, 1), nodata=-9999.0,
        ) as dst:
            dst.write(np.full((48, 64), v, dtype="float32"), 1)
        sources.append({"time": f"2024-0{i + 1}", "source": str(p)})

    from app.services.lakehouse.cube_service import build_session_cube
    from app.services.lakehouse.data_object import resolve_data_object

    res = await build_session_cube("sess-lin", time_sources=sources, title="T")
    manifest = resolve_data_object(res["data_object_id"])
    assert len(manifest["source_refs"]) == 2
    assert all(r.startswith("fingerprint:") for r in manifest["source_refs"])
    assert manifest["producer"]["capability"] == "lakehouse.cube_build"
    assert manifest["input_fingerprint"].startswith("reuse:")
    assert manifest["owner_scope"] == {"session_id": "sess-lin"}
