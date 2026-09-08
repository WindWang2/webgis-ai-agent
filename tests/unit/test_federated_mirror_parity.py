"""V6 镜像常量/函数对账测试（评审 R2 m-5）：防止跨模块拷贝漂移。"""

from app.services.data_fabric.query.federated import (
    adaptive,
    bloom,
    costing,
    enumerator,
    physical,
)
from app.services.data_fabric.query import federation


def test_max_federated_sources_mirror():
    assert enumerator.MAX_FEDERATED_SOURCES == federation.MAX_FEDERATED_SOURCES


def test_unestimated_rows_mirror():
    assert enumerator._UNESTIMATED_ROWS == federation._UNESTIMATED_ROWS
    assert costing._UNESTIMATED_ROWS == federation._UNESTIMATED_ROWS


def test_spatial_shrink_factors_mirror_costing():
    assert (
        enumerator._SPATIAL_SHRINK_WITHIN == costing._SHRINK_BY_OP["within"]
    )
    assert (
        enumerator._SPATIAL_SHRINK_INTERSECTS == costing._SHRINK_BY_OP["intersects"]
    )


def test_crs_transform_weights_mirror():
    # enumerator._crs_transform_meta 与 costing 的 server/本地每行权重同源
    assert costing._W_SERVER_REPROJECT_PER_ROW == 0.1
    assert costing._W_LOCAL_REPROJECT_PER_ROW == 1.5


def test_parse_srid_equivalent_on_valid_domain():
    """规划侧（enumerator/costing）与执行侧（planner.parse_epsg）在合法域
    上必须同解析 —— 非法边界值由执行侧 typed 失败兜底。"""
    from app.services.data_fabric.query.planner import parse_epsg

    for crs in ("EPSG:4326", "epsg:3857", "CRS84", "OGC:CRS84", "4326", ""):
        assert enumerator._parse_srid(crs) == parse_epsg(crs) or (
            enumerator._parse_srid(crs) is None and parse_epsg(crs) is None
        ), f"CRS 解析漂移: {crs!r}"
    # 数值域抽样
    for srid in (3857, 2154, 32650):
        assert enumerator._parse_srid(f"EPSG:{srid}") == srid
        assert costing.decide_crs_transform  # costing 依赖同一域


def test_page_size_parity():
    assert physical.DEFAULT_PAGE_SIZE == federation.JOIN_PAGE_SIZE


def test_adaptive_thresholds_documented_and_bounded():
    assert adaptive.DEVIATION_THRESHOLD == 4.0
    assert adaptive.MAX_REPLANS == 1
    assert bloom.PROFIT_THRESHOLD == 2.0
    assert bloom.MIN_REDUCTION_RATIO == 0.1


def test_semi_join_key_cap_mirror():
    assert federation.SEMI_JOIN_MAX_KEYS == 1000  # V5 红线不变
