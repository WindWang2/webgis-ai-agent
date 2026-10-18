"""ModelOps 产物写盘原子性（#1634）：tmp + os.replace，失败零残留。

``write_raster_output`` / ``write_geojson_output`` 旧实现直接写 run 输出
目录终态路径 —— 推理进程在写盘中被 kill 会留下半成品孤儿文件（digest 验真
在 publish 层能拦住损坏内容进 CAS，但 run 目录的孤儿无人回收）。
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from app.services.modelops.artifacts import (
    write_geojson_output,
    write_raster_output,
)


class _FakeSrc:
    """最小 template 鸭子类型（crs/transform/shape；无 .dataset 属性）。"""

    def __init__(self, transform):
        self.crs = "EPSG:4326"
        self.transform = transform


def test_write_geojson_output_publishes_atomically(tmp_path):
    path = tmp_path / "detections.geojson"
    out = write_geojson_output(path, {"type": "FeatureCollection", "features": []})
    assert out == path and path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["type"] == "FeatureCollection"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_write_raster_output_publishes_atomically(tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "classes.tif"
    out = write_raster_output(
        path,
        arrays=[np.zeros((4, 4), dtype=np.uint8)],
        band_names=["classes"],
        template=_FakeSrc(from_origin(0, 4, 1, 1)),
    )
    assert out == path and path.exists()
    with rasterio.open(path) as ds:
        assert ds.count == 1 and ds.width == 4 and ds.height == 4
        assert ds.descriptions[0] == "classes"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_write_raster_output_cleans_tmp_on_failure(tmp_path, monkeypatch):
    """写盘失败 → tmp 清理 + 异常透传；终态路径绝不出现半成品。"""
    import rasterio
    from rasterio.transform import from_origin

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(rasterio, "open", _boom)
    path = tmp_path / "boom.tif"
    with pytest.raises(OSError, match="disk full"):
        write_raster_output(
            path,
            arrays=[np.zeros((4, 4), dtype=np.uint8)],
            band_names=["classes"],
            template=_FakeSrc(from_origin(0, 4, 1, 1)),
        )
    assert not path.exists()
    assert list(tmp_path.glob(".*.tmp")) == []
