"""V6 空间成本契约测试（ADR-0118 W3）：空间选择性 + CRS 变换决策。"""

import pytest

from app.services.data_fabric.query.federated.costing import (
    decide_crs_transform,
    estimate_spatial_selectivity,
    geojson_bbox,
)
from app.services.data_fabric.query.federated.spatial_stats import (
    build_histogram,
    build_histogram_from_cells,
    histogram_to_meta,
)
from app.services.data_fabric.query.predicates import (
    BBox,
    DWithin,
    Intersects,
    Within,
)
from app.services.data_fabric.query.statistics import DatasetStatistics

_POINT = {"type": "Point", "coordinates": [5.0, 5.0]}
_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[4.0, 4.0], [6.0, 4.0], [6.0, 6.0], [4.0, 6.0], [4.0, 4.0]]],
}


def _stats_with_hist(basis="measured", grid=4) -> DatasetStatistics:
    if basis == "measured":
        hist = build_histogram_from_cells(
            counts=[625.0] * 16, extent=[0.0, 0.0, 10.0, 10.0], basis="measured"
        )
    else:
        hist = build_histogram(
            row_count=10_000, extent=[0.0, 0.0, 10.0, 10.0], grid_size=grid
        )
    return DatasetStatistics(
        dataset_fingerprint="ds1",
        row_count=10_000,
        extent=[0.0, 0.0, 10.0, 10.0],
        spatial_histogram=histogram_to_meta(hist),
    )


# ── GeoJSON 足迹 ────────────────────────────────────────────────────────────


def test_geojson_bbox_point_and_polygon():
    assert geojson_bbox(_POINT) == [5.0, 5.0, 5.0, 5.0]
    bb = geojson_bbox(_SQUARE)
    assert bb == [4.0, 4.0, 6.0, 6.0]


def test_geojson_bbox_invalid():
    assert geojson_bbox({"type": "Point"}) is None
    assert geojson_bbox(None) is None


# ── 空间选择性 ──────────────────────────────────────────────────────────────


def test_bbox_selectivity_measured_hist_is_statistics_basis():
    est = estimate_spatial_selectivity(
        BBox(bbox=[0.0, 0.0, 2.5, 2.5]), _stats_with_hist("measured")
    )
    assert est.basis == "statistics"
    assert est.value == pytest.approx(1.0 / 16.0)


def test_bbox_selectivity_uniform_hist_is_assumption_basis():
    est = estimate_spatial_selectivity(
        BBox(bbox=[0.0, 0.0, 2.5, 2.5]), _stats_with_hist("assumption")
    )
    assert est.basis == "assumption"
    assert est.value == pytest.approx(1.0 / 16.0)


def test_no_stats_falls_back_area_ratio_default_basis():
    est = estimate_spatial_selectivity(
        BBox(bbox=[0.0, 0.0, 1.0, 1.0]),
        DatasetStatistics(dataset_fingerprint="d", extent=[0.0, 0.0, 10.0, 10.0]),
    )
    assert est.basis == "default"
    assert est.value == pytest.approx(0.01)


def test_within_more_selective_than_intersects():
    intersects = estimate_spatial_selectivity(
        Intersects(geometry=_SQUARE), _stats_with_hist("measured")
    )
    within = estimate_spatial_selectivity(
        Within(geometry=_SQUARE), _stats_with_hist("measured")
    )
    assert within.value < intersects.value


def test_dwithin_distance_grows_selectivity():
    near = estimate_spatial_selectivity(
        DWithin(geometry=_POINT, distance=100.0), _stats_with_hist("measured")
    )
    far = estimate_spatial_selectivity(
        DWithin(geometry=_POINT, distance=100_000.0), _stats_with_hist("measured")
    )
    assert far.value > near.value


def test_dwithin_geographic_marks_assumption_detail():
    est = estimate_spatial_selectivity(
        DWithin(geometry=_POINT, distance=500.0), _stats_with_hist("measured")
    )
    assert est.detail.get("geographic_approximation") is True


def test_null_spatial_is_default_one():
    est = estimate_spatial_selectivity(None, _stats_with_hist())
    assert est.value == 1.0 and est.basis == "default"


# ── CRS 变换决策 ────────────────────────────────────────────────────────────


class _Caps:
    def __init__(self, server_reprojection=False, source_type="postgis",
                 output_crs_pushdown=False):
        self.server_reprojection = server_reprojection
        self.source_type = source_type
        self.output_crs_pushdown = output_crs_pushdown


def test_same_crs_no_transform():
    d = decide_crs_transform(
        left_crs_srid=4326,
        right_crs_srid=4326,
        join_kind="spatial_join",
        caps_left=_Caps(),
        caps_right=_Caps(),
        est_left_rows=100,
        est_right_rows=50,
    )
    assert d.placement == "none" and d.transform_side is None


def test_unknown_crs_honest_no_transform():
    d = decide_crs_transform(
        left_crs_srid=None,
        right_crs_srid=3857,
        join_kind="spatial_join",
        caps_left=_Caps(),
        caps_right=_Caps(server_reprojection=True),
        est_left_rows=100,
        est_right_rows=50,
    )
    assert d.placement == "none"
    assert d.correctness_note is not None


def test_server_placement_opt_in_only():
    # allow_server=False（当前生产默认）：server placement 不产 —— 需要跨
    # adapter 的 output.crs 管道（deferred），计划绝不声称执行不了的 placement
    d = decide_crs_transform(
        left_crs_srid=4326,
        right_crs_srid=3857,
        join_kind="spatial_join",
        caps_left=_Caps(),
        caps_right=_Caps(server_reprojection=True),
        est_left_rows=1000,
        est_right_rows=10,
    )
    assert d.placement == "local"
    assert d.transform_side == "right"


def test_server_placement_when_allowed():
    # allow_server=True：server 每行成本 ≈ 本地 1/15 → 大侧 server 胜过小侧本地
    # V7（ADR-0119 W8）：placement 还要求该侧声明 output_crs_pushdown 通道。
    d = decide_crs_transform(
        left_crs_srid=4326,
        right_crs_srid=3857,
        join_kind="spatial_join",
        caps_left=_Caps(),
        caps_right=_Caps(server_reprojection=True, output_crs_pushdown=True),
        est_left_rows=1000,
        est_right_rows=10,
        allow_server=True,
    )
    assert d.placement == "server"
    assert d.transform_side == "right"


def test_server_placement_requires_verified_channel():
    # V7 语义：仅 server_reprojection（服务器能变换）而无已验证扫描通道
    # （output_crs_pushdown）→ 本地变换（绝不声称执行不了的 placement）。
    d = decide_crs_transform(
        left_crs_srid=4326,
        right_crs_srid=3857,
        join_kind="spatial_join",
        caps_left=_Caps(),
        caps_right=_Caps(server_reprojection=True, output_crs_pushdown=False),
        est_left_rows=1000,
        est_right_rows=10,
        allow_server=True,
    )
    assert d.placement == "local"
    assert d.transform_side == "right"


def test_server_placement_side_feasibility_gate():
    # 右侧通道完整但非 scan-like（server_feasible[1]=False）→ 落左（本地）。
    d = decide_crs_transform(
        left_crs_srid=4326,
        right_crs_srid=3857,
        join_kind="spatial_join",
        caps_left=_Caps(server_reprojection=True, output_crs_pushdown=True),
        caps_right=_Caps(server_reprojection=True, output_crs_pushdown=True),
        est_left_rows=1000,
        est_right_rows=10,
        allow_server=True,
        server_feasible=(True, False),
    )
    assert d.placement == "local"


def test_local_transform_cheaper_side_wins():
    # 右侧更大且不可 server → 本地变换左侧（较小侧）
    d = decide_crs_transform(
        left_crs_srid=4326,
        right_crs_srid=3857,
        join_kind="spatial_join",
        caps_left=_Caps(server_reprojection=False),
        caps_right=_Caps(server_reprojection=False),
        est_left_rows=10,
        est_right_rows=100_000,
    )
    assert d.placement == "local"
    assert d.transform_side == "left"
    assert d.per_row_cost > 0


def test_none_estimated_rows_uses_placeholder():
    d = decide_crs_transform(
        left_crs_srid=4326,
        right_crs_srid=3857,
        join_kind="spatial_join",
        caps_left=_Caps(),
        caps_right=_Caps(server_reprojection=True),
        est_left_rows=None,
        est_right_rows=None,
    )
    # 双 None → 稳定取右侧（placeholder 相等时 tie-break right）
    assert d.transform_side == "right"


def test_attribute_join_still_needs_crs_alignment_for_geometry_carry():
    # 属性 join 本身不做几何比较，但几何列参与输出；CRS 不一致仅记录，不强算
    d = decide_crs_transform(
        left_crs_srid=4326,
        right_crs_srid=3857,
        join_kind="attribute_join",
        caps_left=_Caps(),
        caps_right=_Caps(),
        est_left_rows=10,
        est_right_rows=10,
    )
    assert d.placement == "none"
    assert d.correctness_note is not None
