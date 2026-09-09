"""V6 空间统计契约测试（ADR-0118 W2）：网格直方图/几何复杂度/CRS/收割。"""

import pytest

from app.services.data_fabric.query.federated.spatial_stats import (
    SpatialGridHistogram,
    attach_spatial_stats,
    build_histogram,
    build_histogram_from_cells,
    estimate_bbox_selectivity,
    geometry_complexity,
    histogram_to_meta,
)
from app.services.data_fabric.query.statistics import (
    DatasetStatistics,
    statistics_from_descriptor,
)


class _Desc:
    """descriptor 测试替身（duck-typed）。"""

    def __init__(self, meta, id="ds1", source_type="geoparquet"):
        self.id = id
        self.source_type = source_type
        self.feature_count = meta.get("row_count")
        self.bbox = meta.get("bbox")
        self.metadata = meta


# ── 直方图构建 ──────────────────────────────────────────────────────────────


def test_uniform_histogram_deterministic():
    h1 = build_histogram(row_count=10_000, extent=[0.0, 0.0, 10.0, 10.0], grid_size=4)
    h2 = build_histogram(row_count=10_000, extent=[0.0, 0.0, 10.0, 10.0], grid_size=4)
    assert h1 == h2
    assert len(h1.counts) == 16
    assert h1.basis == "assumption"
    assert h1.total_rows == 10_000


def test_cells_histogram_measured_basis():
    h = build_histogram_from_cells(
        counts=[100.0] * 16, extent=[0.0, 0.0, 10.0, 10.0], basis="measured"
    )
    assert h.basis == "measured"
    assert sum(h.counts) == pytest.approx(1600.0)


def test_invalid_extent_rejected():
    with pytest.raises(ValueError):
        build_histogram(row_count=100, extent=[5.0, 0.0, 1.0, 10.0])
    with pytest.raises(ValueError):
        build_histogram(row_count=100, extent=[0.0, 0.0, 1.0])


# ── bbox 选择率 ─────────────────────────────────────────────────────────────


def test_bbox_selectivity_full_and_zero():
    h = build_histogram(row_count=1000, extent=[0.0, 0.0, 10.0, 10.0], grid_size=4)
    assert estimate_bbox_selectivity(h, [0.0, 0.0, 10.0, 10.0]) == pytest.approx(1.0)
    assert estimate_bbox_selectivity(h, [20.0, 20.0, 30.0, 30.0]) == pytest.approx(0.0)


def test_bbox_selectivity_partial_quarter():
    h = build_histogram(row_count=1000, extent=[0.0, 0.0, 10.0, 10.0], grid_size=4)
    # 查询恰好覆盖一个格（2.5×2.5 的 1/16 面积）
    sel = estimate_bbox_selectivity(h, [0.0, 0.0, 2.5, 2.5])
    assert sel == pytest.approx(1.0 / 16.0)


def test_bbox_selectivity_none_without_hist():
    assert estimate_bbox_selectivity(None, [0.0, 0.0, 1.0, 1.0]) is None


def test_bbox_selectivity_clamped_to_unit():
    h = build_histogram(row_count=10, extent=[0.0, 0.0, 1.0, 1.0], grid_size=2)
    assert estimate_bbox_selectivity(
        h, [-100.0, -100.0, 100.0, 100.0]
    ) == pytest.approx(1.0)


# ── 几何复杂度 ──────────────────────────────────────────────────────────────


def test_geometry_complexity_defaults_by_type():
    stats = DatasetStatistics(dataset_fingerprint="ds1", geometry_type="Point")
    assert geometry_complexity(stats) == 1.0
    stats2 = DatasetStatistics(dataset_fingerprint="ds1", geometry_type="Polygon")
    assert geometry_complexity(stats2) > 1.0


def test_geometry_complexity_measured_wins():
    stats = DatasetStatistics(
        dataset_fingerprint="ds1", geometry_type="Point", avg_vertices=42.0
    )
    assert geometry_complexity(stats) == 42.0


def test_geometry_complexity_unknown_type_default():
    stats = DatasetStatistics(dataset_fingerprint="ds1")
    assert geometry_complexity(stats) > 0


# ── descriptor 收割 ─────────────────────────────────────────────────────────


def test_attach_from_meta_measured_cells():
    meta = {
        "row_count": 800,
        "bbox": [0.0, 0.0, 8.0, 8.0],
        "spatial_histogram": {
            "grid_size": 2,
            "counts": [500.0, 100.0, 150.0, 50.0],
            "basis": "measured",
        },
        "avg_vertices": 12.5,
        "srs": "EPSG:4326",
    }
    stats = DatasetStatistics(dataset_fingerprint="ds1")
    updated = attach_spatial_stats(stats, _Desc(meta))
    assert updated.spatial_histogram is not None
    assert updated.spatial_histogram["grid_size"] == 2
    assert updated.spatial_histogram["basis"] == "measured"
    assert updated.avg_vertices == 12.5
    assert updated.crs == "EPSG:4326"


def test_attach_synthesizes_uniform_when_no_hist():
    meta = {"row_count": 400, "bbox": [0.0, 0.0, 4.0, 4.0]}
    stats = DatasetStatistics(dataset_fingerprint="ds1")
    updated = attach_spatial_stats(stats, _Desc(meta))
    assert updated.spatial_histogram is not None
    assert updated.spatial_histogram["basis"] == "assumption"
    cells = updated.spatial_histogram["counts"]
    assert len(cells) == 64  # 默认 8×8
    assert all(c == pytest.approx(400 / 64) for c in cells)


def test_attach_noop_without_extent():
    stats = DatasetStatistics(dataset_fingerprint="ds1")
    updated = attach_spatial_stats(stats, _Desc({"row_count": 10}))
    assert updated.spatial_histogram is None


def test_histogram_meta_roundtrip():
    h = build_histogram(row_count=100, extent=[0.0, 0.0, 2.0, 2.0], grid_size=2)
    meta = histogram_to_meta(h)
    h2 = SpatialGridHistogram.from_meta(meta)
    assert h2 is not None and h2 == h


def test_statistics_from_descriptor_harvests_crs_and_vertices():
    meta = {
        "row_count": 100,
        "bbox": [0.0, 0.0, 1.0, 1.0],
        "srs": "EPSG:3857",
        "avg_vertices": 9.0,
    }
    stats = statistics_from_descriptor(_Desc(meta))
    assert stats is not None
    assert stats.crs == "EPSG:3857"
    assert stats.avg_vertices == 9.0
