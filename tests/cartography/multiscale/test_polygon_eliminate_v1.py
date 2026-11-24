"""polygon_eliminate 引擎测试 —— 合并方向 oracle / 量纲守恒 / 硬约束。

oracle：
- 合并方向 = 共享边界最长的邻居（构造不对称共享边验证）；
- 幸存者面积 ≈ Σ(自身 + 被吸收)（shapely 独立复核）；
- 阈值取 paper_scale.minimum_mappable_area_m2(1:50万) = 500,000 m² 的
  独立换算结果，构造其上/下面斑。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pytest
from shapely.geometry import shape

from app.lib.cartography.multiscale.paper_scale import (
    minimum_mappable_area_m2,
)
from app.lib.cartography.multiscale.paper_scale import (
    PublicationScaleContract,
    FrameViewport,
)
from app.lib.cartography.multiscale.polygon_eliminate import (
    eliminate_small_polygons,
)

#: 1:500,000 的最小可辨面斑（独立换算：2.0 mm² × (500 m/mm)²）
THRESHOLD_M2 = 500_000.0
THRESHOLD_FROM_CONTRACT = minimum_mappable_area_m2(PublicationScaleContract(
    target_denominator=500_000,
    frame=FrameViewport(width_mm=260.0, height_mm=170.0),
))


def _square_m2(side_deg: float, x0: float = 0.0, y0: float = 0.0,
               fid: str = "f", props: Optional[Dict[str, Any]] = None,
               ) -> Dict[str, Any]:
    """以 (x0,y0) 为左下角、边长 side_deg 的方块（米²面积≈side²·1.23e10）。"""
    x1, y1 = x0 + side_deg, y0 + side_deg
    ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    return {
        "type": "Feature", "id": fid,
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": dict(props or {}),
    }


def _gap_square(side_deg: float, x0: float, y0: float, gap: float,
                fid: str, props: Optional[Dict[str, Any]] = None,
                ) -> Dict[str, Any]:
    """与左侧要素间隔 gap 的孤立方块（无共享边界）。"""
    return _square_m2(side_deg, x0 + gap, y0, fid=fid, props=props)


def _area_m2(geom_dict: Dict[str, Any]) -> float:
    # 原点在首要素 (0,0)：等距近似（lat0=0），面积按 1 deg² = 111320·110574 m²
    g = shape(geom_dict)
    return g.area * 111_320.0 * 110_574.0


class TestMergeDirection:
    def test_merges_into_neighbor_with_longest_shared_boundary(self):
        # 小面夹在两个大面之间：与左侧共享边更长 → 并入左侧
        small = _square_m2(0.004, 0.05, 0.0, fid="small")   # ≈0.197 km²
        left = _square_m2(0.05, 0.0, 0.0, fid="left")       # ≈30.9 km²
        # right 与 small 只共享一个点（角接），left 共享整条边
        right = _gap_square(0.05, 0.054, 0.0, 0.0, fid="right")
        out = eliminate_small_polygons(
            [left, small, right], min_area_m2=THRESHOLD_M2)
        assert out.receipt["eliminated_count"] == 1
        manifest = out.manifest["small"]
        assert manifest["absorbed_into"] == "left"
        fids = [f["id"] for f in out.feature_collection["features"]]
        assert fids == ["left", "right"]
        # 幸存者面积 oracle：left' ≈ left + small
        out_left = next(f for f in out.feature_collection["features"]
                        if f["id"] == "left")
        expected = _area_m2(left["geometry"]) + _area_m2(small["geometry"])
        assert _area_m2(out_left["geometry"]) == pytest.approx(expected, rel=1e-6)

    def test_threshold_from_paper_scale_contract(self):
        # 构造 1:50万 阈值上/下面斑：0.005°（≈0.31 km²，低于 0.5 km²）消除；
        # 0.01°（≈1.23 km²，高于）保留
        small = _square_m2(0.005, 0.0, 0.0, fid="small")
        big = _square_m2(0.05, 0.005, 0.0, fid="big")
        big2 = _square_m2(0.05, 0.055, 0.0, fid="big2")
        out = eliminate_small_polygons(
            [small, big, big2], min_area_m2=THRESHOLD_FROM_CONTRACT)
        assert out.receipt["eliminated_count"] == 1
        assert out.manifest["small"]["absorbed_into"] == "big"
        fids = [f["id"] for f in out.feature_collection["features"]]
        assert fids == ["big", "big2"]


class TestThematicSemantics:
    def test_sum_fields_accumulate(self):
        small = _square_m2(0.004, 0.05, 0.0, fid="small",
                           props={"school_count": 7, "name": "s"})
        big = _square_m2(0.05, 0.0, 0.0, fid="big",
                         props={"school_count": 30, "name": "b"})
        out = eliminate_small_polygons(
            [big, small], min_area_m2=THRESHOLD_M2, sum_fields=["school_count"])
        big_out = next(f for f in out.feature_collection["features"]
                       if f["id"] == "big")
        assert big_out["properties"]["school_count"] == 37
        assert big_out["properties"]["name"] == "b"          # 非 sum 字段不动
        assert out.manifest["small"]["sum_fields_delta"] == {"school_count": 7.0}

    def test_non_sum_numeric_field_divergence_disclosed(self):
        small = _square_m2(0.004, 0.05, 0.0, fid="small",
                           props={"rate": 0.42})
        big = _square_m2(0.05, 0.0, 0.0, fid="big", props={"rate": 0.11})
        out = eliminate_small_polygons(
            [big, small], min_area_m2=THRESHOLD_M2)
        big_out = next(f for f in out.feature_collection["features"]
                       if f["id"] == "big")
        assert big_out["properties"]["rate"] == 0.11          # 不平均不臆造
        assert out.receipt["display_divergence"] == 1
        assert any("sum_fields" in d for d in out.receipt["disclosures"])

    def test_input_not_mutated(self):
        small = _square_m2(0.004, 0.05, 0.0, fid="small",
                           props={"school_count": 7})
        big = _square_m2(0.05, 0.0, 0.0, fid="big",
                         props={"school_count": 30})
        eliminate_small_polygons(
            [big, small], min_area_m2=THRESHOLD_M2,
            sum_fields=["school_count"])
        assert small["properties"]["school_count"] == 7
        assert big["properties"]["school_count"] == 30


class TestHardConstraints:
    def test_protected_ids_never_eliminated(self):
        small = _square_m2(0.004, 0.05, 0.0, fid="precious")
        big = _square_m2(0.05, 0.0, 0.0, fid="big")
        out = eliminate_small_polygons(
            [big, small], min_area_m2=THRESHOLD_M2,
            protected_ids=frozenset({"precious"}))
        fids = [f["id"] for f in out.feature_collection["features"]]
        assert fids == ["big", "precious"]
        assert out.receipt["eliminated_count"] == 0
        # protected_count = 受保护态要素数（precious 显式保护；big 非候选
        # 不在保护语义内）
        assert out.receipt["protected_count"] == 1

    def test_keep_region_protects(self):
        small = _square_m2(0.004, 0.05, 0.0, fid="small")
        big = _square_m2(0.05, 0.0, 0.0, fid="big")
        # 区域完全覆盖 small → 保护
        out = eliminate_small_polygons(
            [big, small], min_area_m2=THRESHOLD_M2,
            keep_region=[0.049, -0.001, 0.055, 0.005])
        fids = [f["id"] for f in out.feature_collection["features"]]
        assert fids == ["big", "small"]

    def test_isolated_small_kept_with_disclosure(self):
        big = _square_m2(0.05, 0.0, 0.0, fid="big")
        isle = _gap_square(0.004, 0.2, 0.2, 0.0, fid="isle")  # 无共享边界
        out = eliminate_small_polygons([big, isle], min_area_m2=THRESHOLD_M2)
        fids = [f["id"] for f in out.feature_collection["features"]]
        assert fids == ["big", "isle"]
        assert out.receipt["isolated_kept"] == 1
        assert any("不静默删除" in d for d in out.receipt["disclosures"])


class TestBookkeeping:
    def test_stats_match_and_count_conservation(self):
        small1 = _square_m2(0.004, 0.05, 0.0, fid="s1")
        small2 = _square_m2(0.003, 0.05, 0.004, fid="s2")
        big = _square_m2(0.05, 0.0, 0.0, fid="big")
        out = eliminate_small_polygons(
            [big, small1, small2], min_area_m2=THRESHOLD_M2)
        assert out.receipt["stats_match"] is True
        assert out.receipt["input_count"] == 3
        assert out.receipt["eliminated_count"] + \
            out.receipt["kept_count"] == 3

    def test_chain_absorption_manifest_traceable(self):
        # s2 并入 s1（共享边），s1 又并入 big —— manifest 两跳可追溯
        s1 = _square_m2(0.004, 0.05, 0.0, fid="s1")
        s2 = _square_m2(0.002, 0.054, 0.0, fid="s2")
        big = _square_m2(0.05, 0.0, 0.0, fid="big")
        out = eliminate_small_polygons(
            [big, s1, s2], min_area_m2=THRESHOLD_M2)
        assert set(out.manifest) == {"s1", "s2"}
        assert out.manifest["s2"]["absorbed_into"] == "s1"
        assert out.manifest["s1"]["absorbed_into"] == "big"

    def test_determinism(self):
        small = _square_m2(0.004, 0.05, 0.0, fid="small",
                           props={"school_count": 7})
        big = _square_m2(0.05, 0.0, 0.0, fid="big",
                         props={"school_count": 30})
        big2 = _gap_square(0.05, 0.054, 0.0, 0.0, fid="big2")
        feats = [big, small, big2]
        o1 = eliminate_small_polygons(feats, min_area_m2=THRESHOLD_M2,
                                      sum_fields=["school_count"])
        o2 = eliminate_small_polygons(feats, min_area_m2=THRESHOLD_M2,
                                      sum_fields=["school_count"])
        assert o1.manifest == o2.manifest
        assert o1.receipt["merges"] == o2.receipt["merges"]
        assert [f["id"] for f in o1.feature_collection["features"]] == \
            [f["id"] for f in o2.feature_collection["features"]]

    def test_nonpositive_threshold_noop(self):
        feats = [_square_m2(0.004, 0.0, 0.0, fid="a")]
        out = eliminate_small_polygons(feats, min_area_m2=0)
        assert out.receipt["eliminated_count"] == 0
        assert out.feature_collection["features"][0]["id"] == "a"

    def test_empty_input(self):
        out = eliminate_small_polygons([], min_area_m2=THRESHOLD_M2)
        assert out.feature_collection["features"] == []
        assert out.receipt["stats_match"] is True
