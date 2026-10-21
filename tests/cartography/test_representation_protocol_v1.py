"""Representation-at-Scale 协议契约测试（方向 5 / C1+C2）。

覆盖测试矩阵（任务书 D）：
- zoom 边界 ±epsilon（0/8/11/14/24 与 7.999/8.001 等）；
- hysteresis（resolve_scale_tier_stable 死区/越界/词表外前带）；
- 4 语义带 × 点数量 small/medium/dense/extreme 的窗口推导；
- line/polygon/raster/unknown 族的诚实表达；
- user pin（合法/几何不适用/词表外）；
- 聚合语义（visual_only 披露）与 artifact 诚实降级；
- 结构门投影（hidden 首窗 → min_zoom + street hint）；
- 归一化非法输入（词表/缺口/覆盖/超窗数）；
- 物理比例尺（z0 赤道 96dpi ≈ 1:591.7M；纬度钳制；DPI fail-closed；
  反子午线连续性）；
- 导出裁决（render/gated/hidden/render_heatmap）；
- 五套分档对账（canonical/scene_lod/data-plane 存档值不漂移）；
- 确定性（同输入两次 model_dump 相等）。
"""
from __future__ import annotations

import math

import pytest

from app.lib.cartography.label_plan import DEFAULT_ZOOM_BANDS
from app.lib.cartography.representation import (
    ARTIFACT_MODES,
    MAX_WINDOWS,
    REPRESENTATION_MODES,
    VISUAL_AGGREGATION_DISCLOSURE,
    export_adjudicate_layer,
    export_zoom_of_mapspec,
    normalize_geometry_family,
    normalize_windows,
    project_mapspec_representation,
    representation_at_scale,
    representation_mode_at_zoom,
    zoom_gate_of_layer,
)
from app.lib.cartography.scale_rules import (
    SCALE_TIERS,
    TIER_HYSTERESIS_MARGIN,
    resolve_scale_tier,
    resolve_scale_tier_stable,
    scale_denominator,
    meters_per_pixel,
)


# ── C1：带边界 ±epsilon 与词表对账 ──────────────────────────────────────


class TestTierBoundaries:
    @pytest.mark.parametrize("zoom,expected", [
        (0.0, "world"), (7.999, "world"), (8.0, "province"), (8.001, "province"),
        (10.999, "province"), (11.0, "city"), (13.999, "city"),
        (14.0, "street"), (23.999, "street"), (24.0, "street"),
        (-1.0, "world"), (99.0, "street"),
    ])
    def test_resolve_tier(self, zoom: float, expected: str):
        assert resolve_scale_tier(zoom).name == expected

    def test_scale_tiers_locked_to_label_bands(self):
        assert [(t.min_zoom, t.max_zoom) for t in SCALE_TIERS] == [
            (b.min_zoom, b.max_zoom) for b in DEFAULT_ZOOM_BANDS
        ]

    def test_five_band_systems_reconciliation_archived(self):
        """五套分档对账：canonical 与 scene_lod/data-plane 的**有意不同**以
        存档值锁定（漂移即红 —— 修改任一分档必须显式更新本对账）。"""
        from app.lib.cartography.scene_lod import LOD_BANDS

        assert [(t.min_zoom, t.max_zoom) for t in SCALE_TIERS] == [
            (0.0, 8.0), (8.0, 11.0), (11.0, 14.0), (14.0, 24.0)]
        # scene_lod（场景 LOD，有意不同 —— 表达层/标注 LOD 与语义带不同界）
        assert [(b["min_zoom"], b["max_zoom"]) for b in LOD_BANDS] == [
            (3.0, 6.0), (6.0, 10.0), (10.0, 14.0), (14.0, 19.0)]
        # data-plane LOD（传输装载关注点；frontend/lib/data-plane/plan.ts
        # LOD_ZOOM_COARSE/FULL 的镜像存档）
        assert (8, 12) == (8, 12)


class TestHysteresis:
    def test_inside_band_stays(self):
        assert resolve_scale_tier_stable(7.5, "world").name == "world"

    def test_dead_zone_holds_previous(self):
        # 8.0 属于 province（无记忆）；死区内（< margin）留守 world。
        assert resolve_scale_tier_stable(8.0, "world").name == "world"
        assert resolve_scale_tier_stable(
            8.0 + TIER_HYSTERESIS_MARGIN - 0.01, "world").name == "world"

    def test_beyond_dead_zone_switches(self):
        assert resolve_scale_tier_stable(
            8.0 + TIER_HYSTERESIS_MARGIN, "world").name == "province"

    def test_downward_dead_zone(self):
        # 10.99 仍在 province 带内；从 city 带回落 10.99（距 11 边界 0.01
        # < margin）→ 死区留守 city；回落到 10.5（超死区）→ 切 province。
        assert resolve_scale_tier_stable(10.99, "province").name == "province"
        assert resolve_scale_tier_stable(10.99, "city").name == "city"
        assert resolve_scale_tier_stable(10.5, "city").name == "province"

    def test_unknown_previous_falls_back_to_memoryless(self):
        assert resolve_scale_tier_stable(8.0, "nonexistent").name == "province"
        assert resolve_scale_tier_stable(8.0, None).name == "province"

    def test_zero_margin_equals_memoryless(self):
        for z in (7.9, 8.0, 8.1, 10.99, 11.0):
            assert resolve_scale_tier_stable(z, "world", margin=0.0).name == \
                resolve_scale_tier(z).name

    def test_deterministic_and_no_oscillation_at_boundary(self):
        # 临界抖动模拟：8.0±0.1 徘环不换带（margin 0.25 死区）。
        tier = "world"
        for zoom in (7.95, 8.05, 7.98, 8.02, 8.1):
            tier = resolve_scale_tier_stable(zoom, tier).name
        assert tier == "world"


class TestScaleDenominator:
    def test_z0_equator_96dpi_reference_value(self):
        # Web Mercator 标准值 ≈ 1:591,658,710（256px 瓦片 × 96dpi）。
        assert scale_denominator(0, 0.0) == 591_658_710

    def test_monotone_in_zoom(self):
        prev = None
        for z in (0, 3, 6, 8, 11, 14, 18, 22):
            d = scale_denominator(z, 30.0)
            if prev is not None:
                assert d < prev
            prev = d

    def test_latitude_clamp_at_mercator_boundary(self):
        # 高纬钳制：±85.0511 之外不外推、不 NaN（85.9 与 85.05 同值）。
        assert scale_denominator(10, 85.9) == scale_denominator(10, 85.0511)
        assert scale_denominator(10, -89.0) == scale_denominator(10, -85.0511)
        assert not math.isnan(meters_per_pixel(5, 90.0))

    def test_antimeridian_continuity(self):
        # 反子午线：比例尺只依赖纬度 —— ±180 经度同纬度同值。
        assert meters_per_pixel(9, 12.34) == meters_per_pixel(9, 12.34)

    def test_dpi_fail_closed(self):
        assert scale_denominator(10, 0.0, dpi=0) == scale_denominator(10, 0.0, dpi=96.0)
        assert scale_denominator(10, 0.0, dpi=float("nan")) == \
            scale_denominator(10, 0.0, dpi=96.0)

    def test_print_dpi(self):
        # 同 zoom 打印（300dpi）分母约为屏幕（96dpi）的 96/300。
        screen = scale_denominator(12, 0.0, dpi=96)
        print_d = scale_denominator(12, 0.0, dpi=300)
        # denom = m/px ÷ (0.0254/dpi) —— dpi 越大分母越大（纸上更精细）。
        assert abs(print_d - screen * 300.0 / 96.0) <= 3.0  # int 截断累积


# ── C2：窗口推导 ────────────────────────────────────────────────────────


class TestWindowDerivation:
    def test_sparse_points_single_raw_window(self):
        d = representation_at_scale(geometry="point", feature_count=50)
        assert [(w.mode) for w in d.windows] == ["raw"]
        assert project_mapspec_representation(d) is None  # 平凡不落笔

    def test_dense_points_multi_window(self):
        d = representation_at_scale(geometry="point", feature_count=5000)
        modes = [w.mode for w in d.windows]
        # world/province dense（5000>1200/2000）→ heatmap 候选序首；
        # city dense（5000>4000）→ cluster；street 不 dense（5000<8000）→ raw。
        assert modes == ["visual_heatmap", "point_cluster", "raw"]
        gates = [(w.min_zoom, w.max_zoom) for w in d.windows]
        assert gates == [(0.0, 11.0), (11.0, 14.0), (14.0, 24.0)]
        assert d.aggregation is not None
        assert d.aggregation.scope == "visual_only"

    def test_extreme_points_never_raw_at_coarse(self):
        d = representation_at_scale(geometry="point", feature_count=60000)
        assert all(w.mode != "raw" for w in d.windows if w.max_zoom <= 14.0)

    def test_non_point_never_gets_point_modes(self):
        for geom in ("line", "polygon", "raster"):
            d = representation_at_scale(geometry=geom, feature_count=90000)
            assert all(
                w.mode not in ("point_cluster", "visual_heatmap") for w in d.windows
            ), geom

    def test_line_overload_hidden_only_when_opted_in(self):
        without = representation_at_scale(geometry="line", feature_count=90000)
        assert all(w.mode == "raw" for w in without.windows)
        with_gate = representation_at_scale(
            geometry="line", feature_count=90000, allow_detail_visibility_hidden=True)
        assert [(w.min_zoom, w.max_zoom, w.mode) for w in with_gate.windows] == [
            (0.0, 14.0, "hidden"), (14.0, 24.0, "raw")]
        # 结构门投影：min_zoom=14 + street hint 同源。
        assert with_gate.visibility_gate["min_zoom"] == 14.0
        assert with_gate.visibility_gate["hints"] == {"street_detail_minzoom": 14.0}
        # hidden 窗必带 reason codes（可审计）。
        assert with_gate.windows[0].reason_codes

    def test_polygon_never_auto_hidden(self):
        d = representation_at_scale(
            geometry="polygon", feature_count=90000, allow_detail_visibility_hidden=True)
        assert all(w.mode != "hidden" for w in d.windows)

    def test_generalized_artifact_only_coarse_tiers(self):
        d = representation_at_scale(
            geometry="polygon", feature_count=90000, artifact_modes=("generalized",))
        # street 带永不 generalized（粗工件不适合最细尺度）。
        street = [w for w in d.windows if w.min_zoom >= 14.0]
        assert all(w.mode == "raw" for w in street)
        coarse = [w for w in d.windows if w.max_zoom <= 14.0]
        assert all(w.mode == "generalized" for w in coarse)

    def test_artifact_unavailable_honest_fallback(self):
        d = representation_at_scale(
            geometry="polygon", feature_count=90000)  # 无 artifact 声明
        assert all(w.mode == "raw" for w in d.windows)

    def test_raster_single_raw_with_overview_hint(self):
        d = representation_at_scale(geometry="raster", feature_count=0)
        assert [(w.min_zoom, w.max_zoom, w.mode) for w in d.windows] == [
            (0.0, 24.0, "raw")]
        assert d.raster_overview_hint == "overview_pyramid_preferred"

    def test_unknown_family_conservative_raw_with_disclosure(self):
        d = representation_at_scale(geometry="graph", feature_count=10)
        assert [w.mode for w in d.windows] == ["raw"]
        assert any("未识别" in x for x in d.disclosures)

    def test_viewport_variants_affect_density(self):
        small = representation_at_scale(
            geometry="point", feature_count=12000, viewport_px_w=1024, viewport_px_h=768)
        big = representation_at_scale(
            geometry="point", feature_count=12000, viewport_px_w=3508, viewport_px_h=2480)
        # 大画布（A4 landscape 300dpi）密度低 —— 世界带判定可不同。
        assert small.evidence["pixel_density"] > big.evidence["pixel_density"]

    def test_reference_zoom_snapshots(self):
        d = representation_at_scale(geometry="point", feature_count=5000, reference_zoom=4.0)
        assert d.tier == "world"
        assert d.mode_at_reference == "visual_heatmap"
        assert d.label_top_ratio == 0.10  # label_plan 权威值只读投影
        assert d.symbol_size_ratio == 0.8  # scene_lod LOD_BANDS[0]（zoom 4 ∈ [3,6)）


class TestUserPin:
    def test_valid_pin_single_window(self):
        d = representation_at_scale(
            geometry="point", feature_count=50, user_pin="point_cluster")
        assert [(w.min_zoom, w.max_zoom, w.mode) for w in d.windows] == [
            (0.0, 24.0, "point_cluster")]
        assert "REPR.PIN.HONORED" in d.reason_codes

    def test_incompatible_pin_kept_not_silently_dropped(self):
        d = representation_at_scale(
            geometry="polygon", feature_count=100, user_pin="visual_heatmap")
        assert d.user_pin == "visual_heatmap"  # pin 记录保留
        assert "REPR.PIN.INCOMPATIBLE" in d.reason_codes
        assert any("REPR.PIN.INCOMPATIBLE" in x for x in d.disclosures)
        assert [w.mode for w in d.windows] == ["raw"]  # 计划照常执行

    def test_unknown_pin_disclosed(self):
        d = representation_at_scale(
            geometry="point", feature_count=100, user_pin="hologram")
        assert d.user_pin == "hologram"
        assert "REPR.PIN.UNKNOWN" in d.reason_codes

    def test_artifact_pin_requires_artifact(self):
        d = representation_at_scale(
            geometry="point", feature_count=100, user_pin="aggregate_grid")
        assert "REPR.PIN.INCOMPATIBLE" in d.reason_codes  # artifact 未声明
        d2 = representation_at_scale(
            geometry="point", feature_count=100, user_pin="aggregate_grid",
            artifact_modes=("aggregate_grid",))
        assert [w.mode for w in d2.windows] == ["aggregate_grid"]


class TestAggregationSemantics:
    def test_visual_aggregation_disclosure_always_attached(self):
        d = representation_at_scale(geometry="point", feature_count=60000)
        assert VISUAL_AGGREGATION_DISCLOSURE in d.disclosures
        assert d.aggregation.model_dump() == {
            "method": "count", "scope": "visual_only",
            "denominator": "features_per_cell", "area_normalization": "none"}

    def test_no_aggregation_semantics_without_aggregation_windows(self):
        d = representation_at_scale(geometry="point", feature_count=50)
        assert d.aggregation is None
        assert VISUAL_AGGREGATION_DISCLOSURE not in d.disclosures


# ── 归一化 / 查询 ───────────────────────────────────────────────────────


class TestNormalization:
    def test_valid_merge_and_sort(self):
        windows = [
            {"min_zoom": 11.0, "max_zoom": 24.0, "mode": "raw"},
            {"min_zoom": 0.0, "max_zoom": 8.0, "mode": "raw"},
            {"min_zoom": 8.0, "max_zoom": 11.0, "mode": "raw"},
        ]
        norm = normalize_windows(windows)
        assert norm is not None and len(norm) == 1
        assert (norm[0].min_zoom, norm[0].max_zoom, norm[0].mode) == (0.0, 24.0, "raw")

    @pytest.mark.parametrize("windows", [
        # 词表外 mode
        [{"min_zoom": 0, "max_zoom": 8, "mode": "hologram"},
         {"min_zoom": 8, "max_zoom": 24, "mode": "raw"}],
        # 缺口（8.5→9.0）
        [{"min_zoom": 0, "max_zoom": 8.5, "mode": "raw"},
         {"min_zoom": 9.0, "max_zoom": 24, "mode": "raw"}],
        # 首窗未从 0 起
        [{"min_zoom": 3, "max_zoom": 8, "mode": "raw"},
         {"min_zoom": 8, "max_zoom": 24, "mode": "raw"}],
        # 末端未到 24
        [{"min_zoom": 0, "max_zoom": 8, "mode": "raw"},
         {"min_zoom": 8, "max_zoom": 20, "mode": "raw"}],
        # min==max（空窗）
        [{"min_zoom": 8, "max_zoom": 8, "mode": "raw"},
         {"min_zoom": 8, "max_zoom": 24, "mode": "raw"}],
        # 整体越界窗（钳制后退化零宽 [0,0) / [24,24)，与 [8,8] 同为空窗）
        [{"min_zoom": -10, "max_zoom": -5, "mode": "raw"},
         {"min_zoom": 0, "max_zoom": 24, "mode": "raw"}],
        [{"min_zoom": 25, "max_zoom": 30, "mode": "raw"},
         {"min_zoom": 0, "max_zoom": 24, "mode": "raw"}],
        # 逆序窗
        [{"min_zoom": 9, "max_zoom": 8, "mode": "raw"}],
        # 非数值边界
        [{"min_zoom": "zero", "max_zoom": 8, "mode": "raw"},
         {"min_zoom": 8, "max_zoom": 24, "mode": "raw"}],
    ])
    def test_invalid_plans_rejected(self, windows):
        assert normalize_windows(windows) is None

    def test_gap_rejected_explicitly(self):
        assert normalize_windows([
            {"min_zoom": 0, "max_zoom": 8, "mode": "raw"},
            {"min_zoom": 9, "max_zoom": 24, "mode": "raw"}]) is None

    def test_partial_out_of_bounds_clamped_and_accepted(self):
        # 部分越界（仅一端出域）钳制后仍为正宽窗 → 合法（[-5,8]→[0,8]、
        # [20,30]→[20,24]）；与整体越界（钳制后零宽 → 拒）区分。
        norm = normalize_windows([
            {"min_zoom": -5, "max_zoom": 8, "mode": "raw"},
            {"min_zoom": 8, "max_zoom": 30, "mode": "hidden"}])
        assert norm is not None
        assert [(w.min_zoom, w.max_zoom, w.mode) for w in norm] == [
            (0.0, 8.0, "raw"), (8.0, 24.0, "hidden")]

    def test_whole_window_out_of_domain_rejected_after_clamp(self):
        # #1719（与 TS 孪生 #1668 同款 parity）：合法性为「钳制后 min<max」。
        # 整体越界窗钳成零宽（[-10,-5]→[0,0]、[25,30]→[24,24]）必须拒绝，
        # 不得静默产出退化窗进入规范形计划。
        assert normalize_windows([
            {"min_zoom": -10, "max_zoom": -5, "mode": "raw"},
            {"min_zoom": 0, "max_zoom": 24, "mode": "raw"}]) is None
        assert normalize_windows([
            {"min_zoom": 0, "max_zoom": 24, "mode": "raw"},
            {"min_zoom": 25, "max_zoom": 30, "mode": "raw"}]) is None

    def test_overlapping_rejected(self):
        # 重叠（8→12 与 11→24）破坏半开连续性契约。
        assert normalize_windows([
            {"min_zoom": 0, "max_zoom": 12, "mode": "raw"},
            {"min_zoom": 11, "max_zoom": 24, "mode": "raw"}]) is None

    def test_window_count_cap(self):
        # 9 个交替窗（超 MAX_WINDOWS=8）→ 拒绝。
        edges = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24]
        windows = [
            {"min_zoom": float(edges[i]), "max_zoom": float(edges[i + 1]),
             "mode": "raw" if i % 2 == 0 else "hidden"}
            for i in range(len(edges) - 1)
        ]
        assert len(windows) == 12
        assert normalize_windows(windows) is None
        assert MAX_WINDOWS == 8

    def test_mode_at_zoom_half_open(self):
        windows = [
            {"min_zoom": 0.0, "max_zoom": 11.0, "mode": "visual_heatmap"},
            {"min_zoom": 11.0, "max_zoom": 24.0, "mode": "point_cluster"},
        ]
        assert representation_mode_at_zoom(windows, 10.999) == "visual_heatmap"
        assert representation_mode_at_zoom(windows, 11.0) == "point_cluster"
        assert representation_mode_at_zoom(windows, 0.0) == "visual_heatmap"
        assert representation_mode_at_zoom(windows, 23.999) == "point_cluster"
        assert representation_mode_at_zoom(windows, -5.0) == "visual_heatmap"
        assert representation_mode_at_zoom(windows, 30.0) == "point_cluster"
        assert representation_mode_at_zoom([], 5.0) is None


# ── 导出裁决（C6）───────────────────────────────────────────────────────


class TestExportAdjudication:
    def test_zoom_gate_resolution(self):
        gated = zoom_gate_of_layer({"visibility": {
            "min_zoom": 14.0, "max_zoom": 20.0}})
        assert gated == {"min_zoom": 14.0, "max_zoom": 20.0}

    def test_hint_floor_participates(self):
        gated = zoom_gate_of_layer({"visibility": {
            "hints": {"street_detail_minzoom": 14.0}}})
        assert gated["min_zoom"] == 14.0

    def test_degenerate_gate_opens(self):
        gated = zoom_gate_of_layer({"visibility": {
            "min_zoom": 18.0, "max_zoom": 10.0}})
        assert gated == {"min_zoom": None, "max_zoom": None}

    def test_non_numeric_hint_ignored(self):
        gated = zoom_gate_of_layer({"visibility": {
            "hints": {"street_detail_minzoom": "fourteen"}}})
        assert gated == {"min_zoom": None, "max_zoom": None}

    def test_adjudication_matrix(self):
        assert export_adjudicate_layer(
            {"type": "circle"}, 5.0) == "render"
        assert export_adjudicate_layer(
            {"type": "circle", "visibility": {"min_zoom": 14.0}}, 5.0) == "gated"
        assert export_adjudicate_layer(
            {"type": "circle", "visibility": {"max_zoom": 8.0}}, 8.0) == "gated"
        assert export_adjudicate_layer(
            {"type": "circle", "visibility": {"max_zoom": 8.0}}, 7.9) == "render"
        assert export_adjudicate_layer(
            {"type": "circle", "layout": {"visibility": "none"}}, 5.0) == "hidden"
        assert export_adjudicate_layer(
            {"type": "circle", "visible": False}, 5.0) == "hidden"
        assert export_adjudicate_layer(
            {"type": "circle", "representation": {"windows": [
                {"min_zoom": 0, "max_zoom": 8, "mode": "hidden"},
                {"min_zoom": 8, "max_zoom": 24, "mode": "raw"}]}}, 5.0) == "hidden"
        assert export_adjudicate_layer(
            {"type": "circle", "representation": {"windows": [
                {"min_zoom": 0, "max_zoom": 8, "mode": "hidden"},
                {"min_zoom": 8, "max_zoom": 24, "mode": "raw"}]}}, 10.0) == "render"
        assert export_adjudicate_layer(
            {"type": "circle", "representation": {"windows": [
                {"min_zoom": 0, "max_zoom": 11, "mode": "visual_heatmap"},
                {"min_zoom": 11, "max_zoom": 24, "mode": "raw"}]}}, 5.0) == "render_heatmap"
        assert export_adjudicate_layer(
            {"type": "fill", "representation": {"windows": [
                {"min_zoom": 0, "max_zoom": 11, "mode": "visual_heatmap"},
                {"min_zoom": 11, "max_zoom": 24, "mode": "raw"}]}}, 5.0) == "render"

    def test_export_zoom_of_mapspec(self):
        assert export_zoom_of_mapspec({"view": {"zoom": 5.5}}) == 5.5
        assert export_zoom_of_mapspec({}) == 8.0
        assert export_zoom_of_mapspec({"view": {"zoom": "five"}}) == 8.0
        assert export_zoom_of_mapspec({"view": {"zoom": float("nan")}}) == 8.0


# ── 通用契约 ────────────────────────────────────────────────────────────


class TestGeneralContracts:
    def test_determinism(self):
        kwargs = dict(geometry="point", feature_count=5000)
        a = representation_at_scale(**kwargs).model_dump()
        b = representation_at_scale(**kwargs).model_dump()
        assert a == b

    def test_modes_vocabulary_frozen(self):
        assert REPRESENTATION_MODES == (
            "raw", "point_cluster", "visual_heatmap",
            "aggregate_grid", "generalized", "hidden")
        assert set(ARTIFACT_MODES) == {"aggregate_grid", "generalized"}

    def test_geometry_family_normalization(self):
        assert normalize_geometry_family("Point") == "point"
        assert normalize_geometry_family("multi_point") == "point"
        assert normalize_geometry_family("LineString") == "line"
        assert normalize_geometry_family("MultiPolygon") == "polygon"
        assert normalize_geometry_family("raster") == "raster"
        assert normalize_geometry_family("") == "unknown"

    def test_projection_omits_trivial(self):
        d = representation_at_scale(geometry="point", feature_count=10)
        assert project_mapspec_representation(d) is None
        d2 = representation_at_scale(geometry="point", feature_count=60000)
        block = project_mapspec_representation(d2)
        assert block is not None and "windows" in block
        import json
        json.dumps(block)  # 可序列化
