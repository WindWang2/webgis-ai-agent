"""Scale-aware LOD strategy tests (ADR-0201 M5).

Oracle anchors:
- LOD 纯函数：zoom → {label topRatio, symbol scale, terrain maxzoom, 抽稀预算}。
- 单调性：zoom 越高 → 标注越多、符号越大、terrain 分辨率越细。
- 落点是既有 spec 能力（MapSpecLayerLabel.zoomBands ADR-0154 语义 +
  thresholds.maxFeatures），不新造 spec 字段。
"""
from __future__ import annotations


from app.lib.cartography.scene_lod import (
    LOD_BANDS,
    build_label_zoom_bands,
    lod_for_zoom,
)


class TestLodTable:
    def test_bands_cover_zoom_domain(self):
        # LOD 分档覆盖 3..18 全域，单调衔接（band 边界连续）
        assert LOD_BANDS[0]["max_zoom"] == LOD_BANDS[1]["min_zoom"]
        assert LOD_BANDS[-1]["max_zoom"] >= 18

    def test_zoom3_is_sparse(self):
        lod = lod_for_zoom(3)
        assert lod["label_top_ratio"] <= 0.35
        assert lod["terrain_maxzoom"] <= 10

    def test_zoom16_is_dense(self):
        lod = lod_for_zoom(16)
        assert lod["label_top_ratio"] == 1.0
        assert lod["terrain_maxzoom"] >= 12

    def test_symbol_scale_monotonic_nondecreasing(self):
        scales = [lod_for_zoom(z)["symbol_scale"] for z in range(3, 19)]
        assert all(b >= a for a, b in zip(scales, scales[1:]))

    def test_label_top_ratio_monotonic_nondecreasing(self):
        ratios = [lod_for_zoom(z)["label_top_ratio"] for z in range(3, 19)]
        assert all(b >= a for a, b in zip(ratios, ratios[1:]))

    def test_terrain_maxzoom_monotonic_nondecreasing(self):
        mz = [lod_for_zoom(z)["terrain_maxzoom"] for z in range(3, 19)]
        assert all(b >= a for a, b in zip(mz, mz[1:]))

    def test_out_of_domain_clamps(self):
        assert lod_for_zoom(0)["label_top_ratio"] == lod_for_zoom(3)["label_top_ratio"]
        assert lod_for_zoom(25)["label_top_ratio"] == lod_for_zoom(18)["label_top_ratio"]

    def test_decimation_budget_bounded(self):
        for z in range(3, 19):
            lod = lod_for_zoom(z)
            assert 0 < lod["decimate_budget"] <= 5000  # 与 VIEWPORT_RENDER_BUDGET 同量级


class TestLabelZoomBands:
    def test_builds_bounded_bands(self):
        bands = build_label_zoom_bands()
        assert 1 <= len(bands) <= 4  # ADR-0154 ≤4 档契约
        for b in bands:
            assert set(b) <= {"minZoom", "maxZoom", "topRatio", "sizeRatio"}
            assert b["topRatio"] <= 1.0

    def test_bands_are_adjacent(self):
        bands = build_label_zoom_bands()
        for a, b in zip(bands, bands[1:]):
            assert a["maxZoom"] == b["minZoom"]

    def test_determinism(self):
        assert build_label_zoom_bands() == build_label_zoom_bands()
