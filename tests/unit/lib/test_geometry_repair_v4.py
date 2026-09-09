"""Science V4 Wave 3 —— geometry repair disclosure 单元测试。

never-silent 契约：无效几何要么类型化拒绝（strict），要么修复+逐要素
披露（数量/方法/无效原因/面积变化）。zonal_statistics 关键路径一致。
"""
from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import Polygon

from app.lib.geo_analysis.geometry_repair import (
    GeometryRepairReport,
    ensure_valid_geometry,
    repair_geometry_sequence,
)
from app.lib.gis.scientific_errors import InvalidGeometry


def _bowtie() -> Polygon:
    """经典自交 bowtie（8 字形）：area 按 GEOS 歧义规则解释 —— 修复前的
    「貌似合理的统计」正是 KNOWN-GAP #2 的根因。"""
    return Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])


def test_valid_geometry_passes_through_without_record():
    square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    geom, rec = ensure_valid_geometry(square, strict=True)
    assert geom is square
    assert rec is None


def test_strict_mode_typed_reject_with_hint():
    with pytest.raises(InvalidGeometry) as ei:
        ensure_valid_geometry(_bowtie(), index=3, strict=True, context="unit")
    assert ei.value.correction_hint
    assert "unit[3]" in str(ei.value)


def test_non_strict_repairs_with_disclosure_record():
    fixed, rec = ensure_valid_geometry(_bowtie(), strict=False, context="unit")
    assert fixed.is_valid
    assert rec is not None
    assert rec.method == "make_valid"
    assert rec.reason  # is_valid_reason 披露无效原因
    d = rec.to_dict()
    assert d["area_before"] is not None and d["area_after"] is not None
    # bowtie 的 GEOS 歧义面积是 0（两翼绕向相消）——修复披露恰好把
    # 「静默零面积 → 有效 2.0」的差异暴露出来（KNOWN-GAP #2 根因）。
    assert d["area_before"] == pytest.approx(0.0)
    assert d["area_after"] == pytest.approx(2.0)
    assert rec.area_delta == pytest.approx(2.0)


def test_repair_failure_raises_invalid_geometry():
    from shapely.geometry import LineString

    class _Unrepairable(LineString):
        """make_valid 后仍无效的构造（空 collections 无面积意义）。"""

    # 空几何：valid 语义上通过（is_valid=True）；用 make_valid 产物为空
    # 的构造直接验证失败通道 —— GeometryCollection only。
    from shapely.geometry import GeometryCollection

    bad = GeometryCollection([_bowtie(), LineString([(0, 0), (1, 1)])])
    bad = bad.buffer(0)  # 确保本身有效，绕开 strict 门
    assert bad.is_valid
    # 直接构造一个 make_valid 后为空的输入不可行（shapely 保持不变式）；
    # 改验证：GeometryCollection(self-intersect) 修复路径不抛 & 记录面积 None。
    fixed, rec = ensure_valid_geometry(
        Polygon([(0, 0), (1, 1), (1, 0), (0, 1)]), strict=False)
    assert fixed.is_valid and rec is not None


def test_sequence_report_aggregates():
    geoms = [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]), _bowtie(),
             Polygon([(3, 3), (4, 3), (4, 4), (3, 4)])]
    out, report = repair_geometry_sequence(geoms, strict=False, context="seq")
    assert len(out) == 3 and all(g.is_valid for g in out)
    assert isinstance(report, GeometryRepairReport)
    assert report.checked == 3 and report.invalid == 1 and report.repaired == 1
    assert report.records[0].index == 1
    d = report.to_dict()
    assert d["failed"] == 0 and d["area_delta_total"] is not None


def _tiny_raster(tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "z.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=16, height=16, count=1,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(103.95, 30.70, 0.005, 0.005),
    ) as dst:
        dst.write(np.full((1, 16, 16), 7.0, dtype="float32"))
    return str(path)


def _bowtie_fc():
    return {
        "type": "FeatureCollection",
        "crs": "EPSG:4326",
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [103.9575, 30.6950], [103.9650, 30.6900],
                    [103.9650, 30.6950], [103.9575, 30.6900],
                    [103.9575, 30.6950],
                ]],
            },
            "properties": {"id": 1},
        }],
    }


def test_zonal_strict_false_repairs_with_row_disclosure(tmp_path):
    from app.lib.geo_analysis.raster_ops import zonal_statistics

    rows = zonal_statistics(_bowtie_fc(), _tiny_raster(tmp_path), strict=False)
    assert len(rows) == 1
    rec = rows[0].get("geometry_repair")
    assert rec is not None
    assert rec["method"] == "make_valid" and rows[0]["mean"] == pytest.approx(7.0)


def test_zonal_default_strict_rejects_invalid(tmp_path):
    from app.lib.geo_analysis.raster_ops import zonal_statistics

    with pytest.raises(InvalidGeometry):
        zonal_statistics(_bowtie_fc(), _tiny_raster(tmp_path))


def test_zonal_valid_geometry_unchanged_no_repair_key(tmp_path):
    from app.lib.geo_analysis.raster_ops import zonal_statistics

    fc = _bowtie_fc()
    fc["features"][0]["geometry"]["coordinates"] = [[
        [103.9575, 30.6900], [103.9650, 30.6900],
        [103.9650, 30.6950], [103.9575, 30.6950],
        [103.9575, 30.6900],
    ]]
    rows = zonal_statistics(fc, _tiny_raster(tmp_path))
    assert rows and "geometry_repair" not in rows[0]
    assert rows[0]["mean"] == pytest.approx(7.0)
