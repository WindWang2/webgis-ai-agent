"""Scale-aware Cartography Rules 测试（C3，ADR-0204 D5）。

覆盖：与 label_plan.DEFAULT_ZOOM_BANDS 分界契约、tier 解析与夹取、
密集点分带阈值（国家/省/市/街区等价面）、非点几何诚实缺省、密度信号
单点复用、表达 id 可解析、确定性。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.cartography.label_plan import DEFAULT_ZOOM_BANDS  # noqa: E402
from app.lib.cartography.model_library import get_map_model  # noqa: E402
from app.lib.cartography.scale_rules import (  # noqa: E402
    POINT_REPRESENTATION_IDS,
    SCALE_TIERS,
    resolve_scale_tier,
    scale_actions,
)


class TestBoundaryContract:
    def test_tier_boundaries_equal_label_zoom_bands(self):
        """跨模块契约：grammar 尺度带与标注 zoom 带同界（同一 zoom 心智
        模型覆盖标注与表达两层）——漂移即红。"""
        label_bounds = [(b.min_zoom, b.max_zoom) for b in DEFAULT_ZOOM_BANDS]
        tier_bounds = [(t.min_zoom, t.max_zoom) for t in SCALE_TIERS]
        assert tier_bounds == label_bounds

    def test_named_tiers_cover_country_to_street(self):
        assert [t.name for t in SCALE_TIERS] == [
            "world", "province", "city", "street"]


class TestTierResolution:
    @pytest.mark.parametrize(
        "zoom,want",
        [(0.0, "world"), (5.0, "world"), (8.0, "province"),
         (11.0, "city"), (14.0, "street"), (23.9, "street")],
    )
    def test_resolution(self, zoom: float, want: str):
        assert resolve_scale_tier(zoom).name == want

    def test_out_of_range_clamps(self):
        assert resolve_scale_tier(-3.0).name == "world"
        assert resolve_scale_tier(99.0).name == "street"


class TestDensePointActions:
    def test_world_band_dense(self):
        d = scale_actions(zoom=6.0, feature_count=5000, geometry="point")
        assert d.tier == "world" and d.is_dense_points
        assert d.point_candidates[0] == "visual_heatmap"
        assert "GRAMMAR.SCALE.DENSE_POINTS_AGGREGATE" in d.reason_codes
        assert d.scale_too_small_for_raw_points

    def test_world_band_sparse(self):
        d = scale_actions(zoom=6.0, feature_count=800, geometry="point")
        assert not d.is_dense_points
        assert d.point_candidates == []
        assert "GRAMMAR.SCALE.SPARSE_POINTS_RAW" in d.reason_codes

    def test_street_band_higher_threshold(self):
        # 街区带阈值更高：同样的 n 在低带密集、高带不密集
        assert scale_actions(zoom=6.0, feature_count=3000,
                             geometry="point").is_dense_points
        assert not scale_actions(zoom=16.0, feature_count=3000,
                                 geometry="point").is_dense_points
        assert scale_actions(zoom=16.0, feature_count=9000,
                             geometry="point").is_dense_points
        # 街区带密集点优先聚簇/原始（热力语义在近地面失真）
        d = scale_actions(zoom=16.0, feature_count=9000, geometry="point")
        assert d.point_candidates[0] == "point_cluster"

    def test_density_signal_via_pixel_density(self):
        # 视口极小 → 像素密度爆表 → 密集（复用 symbology_v2 单点信号）
        d = scale_actions(zoom=6.0, feature_count=1200, geometry="point",
                          viewport_px_w=200, viewport_px_h=200)
        assert d.is_dense_points
        assert d.feature_density is not None and d.feature_density > 8.0

    def test_polygon_gets_no_point_candidates(self):
        d = scale_actions(zoom=6.0, feature_count=5000, geometry="polygon")
        assert d.point_candidates == []
        assert not d.is_dense_points
        assert "GRAMMAR.SCALE.NON_POINT_NO_POINT_CANDIDATES" in d.reason_codes

    def test_visibility_and_generalization_hints_present(self):
        d = scale_actions(zoom=5.0, feature_count=100, geometry="polygon")
        assert "street_detail_minzoom" in d.visibility_hints
        assert d.boundary_detail == "generalized"
        assert d.aggregation_hint.startswith("admin")


class TestVocabularyAlignment:
    @pytest.mark.parametrize("model_id", POINT_REPRESENTATION_IDS)
    def test_point_candidate_ids_resolvable(self, model_id: str):
        assert get_map_model(model_id) is not None, model_id


class TestDeterminism:
    def test_same_input_same_dump(self):
        a = scale_actions(zoom=6.0, feature_count=5000, geometry="point")
        b = scale_actions(zoom=6.0, feature_count=5000, geometry="point")
        assert a.model_dump() == b.model_dump()
