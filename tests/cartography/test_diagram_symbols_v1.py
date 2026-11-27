"""地理锚定统计图符 WP1 —— 合同/几何 oracle/资格/图例 单测（正+负+确定性）。

科学语义断言（不 mock、纯数值 oracle）：
- 组成比例：角度 ∝ share、12 点钟起顺时针、Σ角度=360、零值零角度有会计；
- 总量面积编码：radius ∝ √(total/v_max)，低于 r_min clamp 并披露；
- 条形：线性长度、0 基线、正上负下、stacked 累计 / grouped 并排；
- 分段比例：等角槽位、radius ∝ √v、扇区面积 ∝ v、跨要素同值同半径（公共尺度）；
- 资格：缺失会计、block 拒绝、非数值/NaN/混合量纲/无身份 typed 拒绝。
"""
from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.diagram_symbols import (  # noqa: E402
    DiagramSymbolError,
    build_glyph_geometry,
    build_glyph_legend,
    compute_glyph_scale,
    diagram_block_fingerprint,
    geometry_digest,
    glyph_bound_radius,
    normalize_size_policy,
    qualify_features,
)


# ── contracts：尺寸政策与公共尺度 ──────────────────────────────────────


class TestSizePolicy:
    def test_composition_defaults(self):
        sp = normalize_size_policy("composition_pie", None)
        assert sp["r_max_px"] == 32.0
        donut = normalize_size_policy("composition_donut", {})
        assert donut["inner_radius_ratio"] == 0.55

    def test_donut_ratio_rejected_out_of_range(self):
        with pytest.raises(DiagramSymbolError) as ei:
            normalize_size_policy("composition_donut", {"inner_radius_ratio": 1.0})
        assert ei.value.code == "INNER_RADIUS_OUT_OF_RANGE"

    def test_non_numeric_rejected(self):
        with pytest.raises(DiagramSymbolError) as ei:
            normalize_size_policy("composition_pie", {"r_max_px": "big"})
        assert ei.value.code == "INVALID_SIZE_POLICY"

    def test_r_max_capped(self):
        with pytest.raises(DiagramSymbolError) as ei:
            normalize_size_policy("composition_pie", {"r_max_px": 1000.0})
        assert ei.value.code == "INVALID_SIZE_POLICY"

    def test_bars_defaults(self):
        sp = normalize_size_policy("series_bars", None)
        assert sp == {"h_max_px": 40.0, "bar_w_px": 8.0, "bar_gap_px": 1.0}


class TestGlyphScale:
    def test_composition_common_scale(self):
        scale = compute_glyph_scale(
            "composition_pie", [[10, 20], [30, 40]], {"r_max_px": 32.0}
        )
        assert scale["v_ref"] == 40
        assert scale["r_max_px"] == 32.0

    def test_composition_area_encoding_carries_r_min(self):
        sp = {"r_max_px": 32.0, "total_area_encoding": True, "r_min_px": 4.0}
        scale = compute_glyph_scale("composition_pie", [[10, 20]], sp)
        assert scale["total_area_encoding"] == 1.0
        assert scale["r_min_px"] == 4.0

    def test_bars_linear_scale(self):
        scale = compute_glyph_scale("series_bars", [[5, -10], [20, 0]], normalize_size_policy("series_bars", None))
        assert scale["v_abs_max"] == 20
        assert scale["px_per_unit"] == pytest.approx(40.0 / 20.0)

    def test_segmented_v_ref_is_global_max(self):
        scale = compute_glyph_scale(
            "segmented_proportional", [[1, 2], [3, 4]], {"r_max_px": 20.0}
        )
        assert scale["v_ref"] == 4

    def test_extreme_range_rejected(self):
        with pytest.raises(DiagramSymbolError) as ei:
            compute_glyph_scale("composition_pie", [[1, 1_000_000]], {"r_max_px": 32.0})
        assert ei.value.code == "EXTREME_VALUE_RANGE"

    def test_zero_total_rejected(self):
        with pytest.raises(DiagramSymbolError) as ei:
            compute_glyph_scale("composition_pie", [[0, 0]], {"r_max_px": 32.0})
        assert ei.value.code == "ZERO_TOTAL"

    def test_bars_all_zero_rejected(self):
        with pytest.raises(DiagramSymbolError) as ei:
            compute_glyph_scale("series_bars", [[0, 0]], normalize_size_policy("series_bars", None))
        assert ei.value.code == "ZERO_TOTAL"


class TestCategorySpec:
    def test_length_mismatch_rejected(self):
        with pytest.raises(DiagramSymbolError) as ei:
            from app.lib.cartography.diagram_symbols import validate_category_spec
            validate_category_spec(["a", "b"], ["f1"], kind="composition_pie")
        assert ei.value.code == "FIELD_NOT_FOUND"

    def test_composition_needs_two_categories(self):
        from app.lib.cartography.diagram_symbols import validate_category_spec
        with pytest.raises(DiagramSymbolError) as ei:
            validate_category_spec(["a"], ["f1"], kind="composition_pie")
        assert ei.value.code == "TOO_FEW_CATEGORIES"

    def test_duplicate_category(self):
        from app.lib.cartography.diagram_symbols import validate_category_spec
        with pytest.raises(DiagramSymbolError) as ei:
            validate_category_spec(["a", "a"], ["f1", "f2"], kind="series_bars")
        assert ei.value.code == "DUPLICATE_CATEGORY"

    def test_pair_output_preserves_order(self):
        from app.lib.cartography.diagram_symbols import validate_category_spec
        pairs = validate_category_spec(["b", "a"], ["f_b", "f_a"], kind="series_bars")
        assert pairs == [("b", "f_b"), ("a", "f_a")]


# ── geometry oracle：科学语义 ──────────────────────────────────────────


class TestCompositionGeometry:
    def test_angles_sum_and_share(self):
        values = [("小学", 30), ("中学", 20), ("其他", 10)]
        glyph = build_glyph_geometry("composition_pie", values, {"r_max_px": 32.0}, {"r_max_px": 32.0})
        assert glyph["total"] == 60
        spans = [s["end_angle_deg"] - s["start_angle_deg"] for s in glyph["slices"]]
        assert sum(spans) == pytest.approx(360.0, abs=0.01)
        assert glyph["slices"][0]["share"] == pytest.approx(0.5, abs=1e-6)
        assert glyph["slices"][0]["start_angle_deg"] == 0.0  # 12 点钟起
        # 顺时针首尾相接：slice i end == slice i+1 start
        for a, b in zip(glyph["slices"], glyph["slices"][1:]):
            assert a["end_angle_deg"] == pytest.approx(b["start_angle_deg"], abs=1e-3)

    def test_zero_value_slice_zero_width_counted(self):
        values = [("a", 10), ("b", 0), ("c", 10)]
        glyph = build_glyph_geometry("composition_pie", values, {"r_max_px": 32.0}, {"r_max_px": 32.0})
        assert glyph["zero_slices"] == 1
        zero = glyph["slices"][1]
        assert zero["end_angle_deg"] == pytest.approx(zero["start_angle_deg"], abs=1e-9)

    def test_donut_inner_radius(self):
        sp = {"r_max_px": 40.0, "inner_radius_ratio": 0.5}
        glyph = build_glyph_geometry(
            "composition_donut", [("a", 1), ("b", 3)], {"r_max_px": 40.0}, sp
        )
        assert glyph["inner_radius_px"] == pytest.approx(20.0, abs=1e-3)

    def test_area_encoding_radius_sqrt_law(self):
        sp = {"r_max_px": 40.0, "total_area_encoding": True, "r_min_px": 4.0}
        scale = compute_glyph_scale("composition_pie", [[30, 70]], sp)
        g_big = build_glyph_geometry("composition_pie", [("a", 30), ("b", 70)], scale, sp)
        g_quarter = build_glyph_geometry("composition_pie", [("a", 7.5), ("b", 17.5)], scale, sp)
        # total 100 vs 25：半径比 = √(100/25) = 2
        assert g_big["radius_px"] == pytest.approx(g_quarter["radius_px"] * 2.0, rel=1e-3)
        # 面积比 = 4
        area_big = math.pi * g_big["radius_px"] ** 2
        area_small = math.pi * g_quarter["radius_px"] ** 2
        assert area_big / area_small == pytest.approx(4.0, rel=1e-3)
        assert "radius_clamped_to_min" not in g_quarter

    def test_area_encoding_clamped_below_r_min(self):
        sp = {"r_max_px": 40.0, "total_area_encoding": True, "r_min_px": 10.0}
        scale = compute_glyph_scale("composition_pie", [[30, 70]], sp)
        g_tiny = build_glyph_geometry("composition_pie", [("a", 0.3), ("b", 0.7)], scale, sp)
        # total=1 → r=4 < r_min=10 → clamp 且披露
        assert g_tiny["radius_px"] == 10.0
        assert g_tiny["radius_clamped_to_min"] is True

    def test_negative_rejected(self):
        with pytest.raises(DiagramSymbolError) as ei:
            build_glyph_geometry("composition_pie", [("a", -5), ("b", 10)], {"r_max_px": 32.0})
        assert ei.value.code == "NEGATIVE_VALUE"

    def test_sum_zero_rejected(self):
        with pytest.raises(DiagramSymbolError) as ei:
            build_glyph_geometry("composition_pie", [("a", 0), ("b", 0)], {"r_max_px": 32.0})
        assert ei.value.code == "ZERO_TOTAL"

    def test_non_finite_rejected(self):
        with pytest.raises(DiagramSymbolError) as ei:
            build_glyph_geometry("composition_pie", [("a", float("nan")), ("b", 1)], {"r_max_px": 32.0})
        assert ei.value.code == "NON_FINITE_VALUE"


class TestSeriesBarsGeometry:
    SP = {"h_max_px": 40.0, "bar_w_px": 8.0, "bar_gap_px": 2.0}

    def _scale(self):
        return compute_glyph_scale("series_bars", [[10, -20], [40, 0]], dict(self.SP))

    def test_stacked_positive_up_negative_down(self):
        scale = self._scale()
        glyph = build_glyph_geometry(
            "series_bars", [("公立", 10), ("民办", -20)], scale, dict(self.SP), layout="stacked"
        )
        pos_seg, neg_seg = glyph["segments"]
        # 正段：0 → +length（向上）；负段：−length → 0（基线之下）
        assert pos_seg["y0"] == 0.0 and pos_seg["y1"] == pytest.approx(10 * scale["px_per_unit"])
        assert neg_seg["y1"] == 0.0 and neg_seg["y0"] == pytest.approx(-20 * scale["px_per_unit"])
        assert glyph["extent_px"]["top"] == pytest.approx(pos_seg["y1"])
        assert glyph["extent_px"]["bottom"] == pytest.approx(neg_seg["y0"])
        assert glyph["baseline_y"] == 0.0

    def test_stacked_cumulative_same_sign(self):
        scale = self._scale()
        glyph = build_glyph_geometry(
            "series_bars", [("a", 10), ("b", 5)], scale, dict(self.SP), layout="stacked"
        )
        s0, s1 = glyph["segments"]
        assert s1["y0"] == pytest.approx(s0["y1"], abs=1e-3)  # 首尾相接

    def test_grouped_side_by_side(self):
        scale = self._scale()
        glyph = build_glyph_geometry(
            "series_bars", [("a", 10), ("b", -20)], scale, dict(self.SP), layout="grouped"
        )
        s0, s1 = glyph["segments"]
        # 并排：条间净距 = bar_gap_px（相邻不重叠）
        assert s1["x0"] - s0["x1"] == pytest.approx(self.SP["bar_gap_px"], abs=1e-3)
        assert s0["x0"] < 0 < s1["x1"]
        # 每段独立从基线起
        assert s0["y0"] == 0.0 and s1["y1"] == 0.0

    def test_zero_value_counted(self):
        scale = self._scale()
        glyph = build_glyph_geometry(
            "series_bars", [("a", 10), ("b", 0)], scale, dict(self.SP), layout="stacked"
        )
        assert glyph["zero_values"] == 1

    def test_unknown_layout_rejected(self):
        with pytest.raises(DiagramSymbolError) as ei:
            build_glyph_geometry(
                "series_bars", [("a", 1)], self._scale(), dict(self.SP), layout="polar"
            )
        assert ei.value.code == "UNKNOWN_LAYOUT"


class TestSegmentedGeometry:
    def test_equal_slots_and_radius_law(self):
        sp = {"r_max_px": 30.0}
        scale = compute_glyph_scale("segmented_proportional", [[10, 40]], sp)
        glyph = build_glyph_geometry("segmented_proportional", [("a", 10), ("b", 40)], scale, sp)
        assert glyph["slot_deg"] == pytest.approx(180.0)
        s0, s1 = glyph["segments"]
        assert s0["radius_px"] == pytest.approx(30.0 * math.sqrt(10 / 40), rel=1e-3)
        assert s1["radius_px"] == pytest.approx(30.0, rel=1e-3)

    def test_sector_area_proportional_to_value(self):
        sp = {"r_max_px": 30.0}
        scale = compute_glyph_scale("segmented_proportional", [[10, 40]], sp)
        glyph = build_glyph_geometry("segmented_proportional", [("a", 10), ("b", 40)], scale, sp)
        a0, a1 = glyph["segments"][0]["area_px2"], glyph["segments"][1]["area_px2"]
        assert a1 / a0 == pytest.approx(4.0, rel=1e-3)  # 等角 → 面积比=值比

    def test_cross_feature_common_scale_same_value_same_radius(self):
        sp = {"r_max_px": 30.0}
        scale = compute_glyph_scale("segmented_proportional", [[10, 40], [40, 5]], sp)
        g1 = build_glyph_geometry("segmented_proportional", [("a", 10), ("b", 40)], scale, sp)
        g2 = build_glyph_geometry("segmented_proportional", [("a", 40), ("b", 5)], scale, sp)
        assert g1["segments"][1]["radius_px"] == pytest.approx(g2["segments"][0]["radius_px"], abs=1e-3)

    def test_negative_rejected(self):
        with pytest.raises(DiagramSymbolError) as ei:
            build_glyph_geometry(
                "segmented_proportional", [("a", -1), ("b", 4)],
                {"v_ref": 4, "r_max_px": 30.0}, {"r_max_px": 30.0},
            )
        assert ei.value.code == "NEGATIVE_VALUE"


class TestBoundRadiusAndDigest:
    def test_bound_radius_pie(self):
        glyph = build_glyph_geometry("composition_pie", [("a", 1), ("b", 1)], {"r_max_px": 24.0}, {"r_max_px": 24.0})
        assert glyph_bound_radius(glyph) == pytest.approx(24.0)

    def test_bound_radius_bars_is_half_diagonal(self):
        # grouped n=2，bar_w=8，gap=0：总宽 16，半宽 8；幅度 top=40 bottom=-30
        sp = {"h_max_px": 40.0, "bar_w_px": 8.0, "bar_gap_px": 0.0}
        scale = compute_glyph_scale("series_bars", [[40, -30]], sp)
        glyph = build_glyph_geometry("series_bars", [("a", 40), ("b", -30)], scale, sp, layout="grouped")
        r = glyph_bound_radius(glyph)
        assert r == pytest.approx(math.hypot(8.0, 40.0), rel=1e-3)

    def test_digest_deterministic(self):
        sp = {"r_max_px": 32.0}
        scale = compute_glyph_scale("composition_pie", [[1, 2]], sp)
        g1 = build_glyph_geometry("composition_pie", [("a", 1), ("b", 2)], scale, sp)
        g2 = build_glyph_geometry("composition_pie", [("a", 1), ("b", 2)], scale, sp)
        assert geometry_digest([g1]) == geometry_digest([g2])
        assert diagram_block_fingerprint({"schema_version": "diagram_symbol.v1", "kind": "composition_pie"}) == \
            diagram_block_fingerprint({"schema_version": "diagram_symbol.v1", "kind": "composition_pie"})

    def test_geometry_deterministic_byte_level(self):
        from app.lib.cartography.diagram_symbols import canonical_json
        sp = {"r_max_px": 32.0, "total_area_encoding": True, "r_min_px": 2.0}
        scale = compute_glyph_scale("composition_pie", [[1, 2], [3, 4]], sp)
        g1 = build_glyph_geometry("composition_pie", [("a", 1), ("b", 2)], scale, sp)
        g2 = build_glyph_geometry("composition_pie", [("a", 1), ("b", 2)], scale, sp)
        assert canonical_json(g1) == canonical_json(g2)


# ── qualification：资格与会计 ──────────────────────────────────────────


def _fc(props_list, with_ids=True):
    return [
        ({"id": str(i), "properties": p} if with_ids else {"properties": p})
        for i, p in enumerate(props_list)
    ]


class TestQualifyFeatures:
    def test_happy_path_with_missing_accounting(self):
        fc = _fc([
            {"primary": 10, "middle": 20},
            {"primary": 5},               # middle 缺失 → omit + 会计
            {"primary": None, "middle": 8},
        ])
        out = qualify_features(fc, kind="composition_pie", categories=["小学", "中学"],
                               fields=["primary", "middle"])
        assert len(out["values"]) == 3
        assert out["values"][1] == [("小学", 5.0)]
        assert out["values"][2] == [("中学", 8.0)]
        assert out["evidence"]["missing_by_category"]["中学"] == 1
        assert out["evidence"]["missing_by_category"]["小学"] == 1
        assert out["evidence"]["missing_total"] == 2
        assert out["evidence"]["glyphs"] == 3

    def test_id_from_properties_fallback(self):
        fc = [{"properties": {"fid": "abc", "v1": 1, "v2": 2}}]
        out = qualify_features(fc, kind="series_bars", categories=["a", "b"],
                               fields=["v1", "v2"], id_field="fid")
        assert out["glyph_feature_ids"] == ["abc"]

    def test_missing_id_rejected(self):
        fc = [{"properties": {"v1": 1, "v2": 2}}]
        with pytest.raises(DiagramSymbolError) as ei:
            qualify_features(fc, kind="series_bars", categories=["a", "b"], fields=["v1", "v2"])
        assert ei.value.code == "MISSING_FEATURE_ID"

    def test_block_policy_refuses_missing(self):
        fc = _fc([{"v1": 1, "v2": None}])
        with pytest.raises(DiagramSymbolError) as ei:
            qualify_features(fc, kind="composition_pie", categories=["a", "b"],
                             fields=["v1", "v2"], missing_policy="block")
        assert ei.value.code == "MISSING_VALUE_BLOCKED"

    def test_non_numeric_rejected_not_coerced(self):
        fc = _fc([{"v1": "3", "v2": 2}])
        with pytest.raises(DiagramSymbolError) as ei:
            qualify_features(fc, kind="composition_pie", categories=["a", "b"],
                             fields=["v1", "v2"])
        assert ei.value.code == "FIELD_NOT_NUMERIC"

    def test_nan_rejected(self):
        fc = _fc([{"v1": float("nan"), "v2": 2}])
        with pytest.raises(DiagramSymbolError) as ei:
            qualify_features(fc, kind="composition_pie", categories=["a", "b"],
                             fields=["v1", "v2"])
        assert ei.value.code == "NON_FINITE_VALUE"

    def test_absent_field_rejected(self):
        fc = _fc([{"v1": 1, "v2": 2}])
        with pytest.raises(DiagramSymbolError) as ei:
            qualify_features(fc, kind="composition_pie", categories=["a", "b"],
                             fields=["v1", "not_there"])
        assert ei.value.code == "FIELD_NOT_FOUND"

    def test_mixed_units_rejected(self):
        fc = _fc([{"v1": 1, "v2": 2}])
        with pytest.raises(DiagramSymbolError) as ei:
            qualify_features(fc, kind="series_bars", categories=["a", "b"],
                             fields=["v1", "v2"], units={"v1": "所", "v2": "ha"})
        assert ei.value.code == "MIXED_UNITS"

    def test_all_missing_rejected(self):
        fc = _fc([{"v1": None, "v2": None}])
        with pytest.raises(DiagramSymbolError) as ei:
            qualify_features(fc, kind="composition_pie", categories=["a", "b"],
                             fields=["v1", "v2"])
        assert ei.value.code == "ZERO_TOTAL"

    def test_unknown_missing_policy(self):
        fc = _fc([{"v1": 1, "v2": 2}])
        with pytest.raises(DiagramSymbolError) as ei:
            qualify_features(fc, kind="series_bars", categories=["a", "b"],
                             fields=["v1", "v2"], missing_policy="zero_fill")
        assert ei.value.code == "UNKNOWN_MISSING_POLICY"

    def test_empty_features_glyph_accounting(self):
        fc = _fc([{"v1": None, "v2": 2}, {"v1": 1, "v2": 2}])
        out = qualify_features(fc, kind="composition_pie", categories=["a", "b"],
                               fields=["v1", "v2"])
        assert out["evidence"]["glyphs"] == 2
        assert out["evidence"]["missing_by_category"]["a"] == 1


# ── legend：与几何同源 ─────────────────────────────────────────────────


class TestGlyphLegend:
    def test_area_law_reference_radii_follow_sqrt(self):
        sp = {"r_max_px": 40.0, "total_area_encoding": True, "r_min_px": 2.0}
        scale = compute_glyph_scale("composition_pie", [[25, 75]], sp)
        legend = build_glyph_legend("composition_pie", ["a", "b"], scale, sp)
        refs = legend["size_reference"]["references"]
        assert refs[0]["radius_px"] == pytest.approx(20.0, rel=1e-3)   # √0.25 = 0.5 → 40·0.5
        assert refs[2]["radius_px"] == pytest.approx(40.0, rel=1e-3)

    def test_constant_radius_note_without_encoding(self):
        sp = {"r_max_px": 32.0}
        scale = compute_glyph_scale("composition_pie", [[1, 2]], sp)
        legend = build_glyph_legend("composition_pie", ["a", "b"], scale, sp)
        assert legend["size_reference"]["mode"] == "constant_radius"
        assert any("不编码总量" in n for n in legend["notes_zh"])

    def test_bars_linear_reference(self):
        sp = {"h_max_px": 40.0, "bar_w_px": 8.0, "bar_gap_px": 1.0}
        scale = compute_glyph_scale("series_bars", [[10, -20]], sp)
        legend = build_glyph_legend("series_bars", ["a", "b"], scale, sp, unit="所", layout="stacked")
        assert legend["size_reference"]["px_per_unit"] == pytest.approx(2.0)
        assert legend["unit"] == "所"

    def test_segmented_notes_not_wind_rose(self):
        sp = {"r_max_px": 30.0}
        scale = compute_glyph_scale("segmented_proportional", [[1, 2]], sp)
        legend = build_glyph_legend("segmented_proportional", ["a", "b"], scale, sp)
        assert legend["size_reference"]["slot_deg"] == pytest.approx(180.0)
        assert any("风玫瑰" in n for n in legend["notes_zh"])

    def test_palette_cycle_note_beyond_nine(self):
        sp = {"r_max_px": 32.0}
        scale = compute_glyph_scale("composition_pie", [[1, 2]], sp)
        cats = [f"c{i}" for i in range(12)]
        legend = build_glyph_legend("composition_pie", cats, scale, sp)
        assert legend["categories"][11]["color_slot"] == 11
        assert any("循环" in n for n in legend["notes_zh"])

    def test_error_payload_shape(self):
        err = DiagramSymbolError("NEGATIVE_VALUE", "x", field="f", correction_hint="h")
        payload = err.payload()
        assert payload == {"success": False, "code": "NEGATIVE_VALUE", "message": "x",
                           "field": "f", "correction_hint": "h"}
