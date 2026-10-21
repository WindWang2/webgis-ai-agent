"""#1716 回归 —— 栅格剖析「波段读取封顶」必须先于分配。

旧实现先按**全波段数** ``ds.read(out_shape=(ds.count, out_h, out_w))``
一次性分配，再只统计前 16 个波段 —— 封顶承诺（1000 波段 × 512² × 8B
≈ 2GB）与实际分配不符，名不符实。

修复后先定 ``bands_read``，再以 ``indexes`` 只读这些波段。本测试用
包装 ``rasterio.open`` 的方式记录真实 ``ds.read`` 实参，直接证明封顶
发生在分配之前。
"""
from __future__ import annotations

import numpy as np
import pytest

_BAND_CAP = 16


def _write_multiband(path: str, *, count: int, h: int = 64, w: int = 64) -> None:
    import rasterio
    from rasterio.transform import Affine

    data = np.arange(count * h * w, dtype="float32").reshape(count, h, w)
    transform = Affine.translation(0, 10) @ Affine.scale(10.0 / w, -10.0 / h)
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=count,
        dtype="float32", crs="EPSG:4326", transform=transform,
    ) as dst:
        dst.write(data)


class _RecordingReader:
    """转发到真实 dataset，只记录 ``read`` 的实参。"""

    def __init__(self, ds, seen):
        self._ds = ds
        self._seen = seen

    def __getattr__(self, name):
        return getattr(self._ds, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._ds.__exit__(*exc)

    def read(self, **kwargs):
        self._seen.update(kwargs)
        return self._ds.read(**kwargs)


@pytest.fixture()
def record_reads(monkeypatch):
    import rasterio

    seen: dict = {}
    real_open = rasterio.open

    def _fake_open(path, *args, **kwargs):
        return _RecordingReader(real_open(path, *args, **kwargs), seen)

    monkeypatch.setattr(rasterio, "open", _fake_open)
    return seen


def _profile(path):
    from app.services.data_profile.profiler import DatasetProfiler

    return DatasetProfiler._read_raster_profile(path, 512)


def test_band_read_is_capped_before_allocation(tmp_path, record_reads):
    path = str(tmp_path / "bands20.tif")
    _write_multiband(path, count=20)

    rp, diagnostics = _profile(path)

    # 封顶先于分配：真实 read 只拿到 _BAND_CAP 个 indexes
    assert record_reads["indexes"] == list(range(1, _BAND_CAP + 1)), \
        f"应按封顶后的波段数读，实际 indexes={record_reads.get('indexes')}"
    assert record_reads["out_shape"][0] == _BAND_CAP, \
        f"out_shape 首维必须等于封顶波段数，实际 {record_reads.get('out_shape')}"
    # 诚实披露
    assert f"band_stats_truncated_to_{_BAND_CAP}" in diagnostics
    assert rp.band_count == 20
    # 统计值仍是前几个波段的真实降采样统计（band_stats 模型层另有 :8 封顶）
    assert [b.band for b in rp.band_stats] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert rp.band_stats[0].min == 0.0 and rp.band_stats[0].max == 4095.0
    assert rp.band_stats[1].min == 4096.0 and rp.band_stats[1].max == 8191.0


def test_band_read_not_capped_when_within_cap(tmp_path, record_reads):
    path = str(tmp_path / "bands4.tif")
    _write_multiband(path, count=4, h=32, w=32)

    rp, diagnostics = _profile(path)

    assert record_reads["indexes"] == [1, 2, 3, 4]
    assert record_reads["out_shape"] == (4, 32, 32)
    assert not any("truncated" in d for d in diagnostics)
    assert rp.band_count == 4
    assert [b.band for b in rp.band_stats] == [1, 2, 3, 4]


def test_single_band_raster_unchanged(tmp_path, record_reads):
    path = str(tmp_path / "one.tif")
    _write_multiband(path, count=1, h=8, w=8)

    rp, diagnostics = _profile(path)

    assert record_reads["indexes"] == [1]
    assert record_reads["out_shape"] == (1, 8, 8)
    assert rp.band_count == 1 and len(rp.band_stats) == 1
    assert not diagnostics
