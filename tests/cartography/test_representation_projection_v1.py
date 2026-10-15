"""Converter 落笔面 + QA 面测试（方向 5 / C3 + C7）。"""
from __future__ import annotations

import json

from app.lib.cartography.quality_loop import (
    representation_at_scale_report,
    review_cartography,
)
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics
from app.services.analysis_cartography_converter import convert_analysis_to_mapspec_layer


def _points(n: int) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point",
                                         "coordinates": [104 + i * 0.001, 30.6]},
         "properties": {"v": float(i)}}
        for i in range(n)
    ]}


def _analysis(n: int, **extra) -> dict:
    base = {"algorithm": "poi_query", "geojson": _points(n),
            "legend_spec": None}
    base.update(extra)
    return base


class TestConverterProjection:
    def test_dense_points_get_windows(self):
        layer, _, warnings = convert_analysis_to_mapspec_layer(_analysis(30000))
        rep = layer.get("representation")
        assert rep is not None
        modes = [w["mode"] for w in rep["windows"]]
        assert "visual_heatmap" in modes
        # 视觉聚合披露随层存续（QA/审计消费）。
        assert any("视觉聚合" in d for d in layer.get("representation_disclosures", []))

    def test_sparse_points_no_block(self):
        layer, _, _ = convert_analysis_to_mapspec_layer(_analysis(100))
        assert "representation" not in layer

    def test_explicit_cluster_hint_is_user_pin(self):
        layer, _, _ = convert_analysis_to_mapspec_layer(
            _analysis(30000, type_hint="point_cluster"))
        windows = layer["representation"]["windows"]
        assert [w["mode"] for w in windows] == ["point_cluster"]

    def test_authored_representation_not_overwritten(self):
        authored = {"windows": [
            {"min_zoom": 0, "max_zoom": 24, "mode": "raw",
             "reason_codes": ["AUTHORED"]}]}
        layer, _, _ = convert_analysis_to_mapspec_layer(
            _analysis(30000), layer={"id": "authored", "representation": authored})
        assert layer["representation"] == authored  # user-wins

    def test_projection_failure_never_blocks(self):
        # 病理输入：converter 绝不因表达计划崩（best-effort 契约）。
        layer, _, _ = convert_analysis_to_mapspec_layer(
            {"algorithm": "x", "geojson": None})
        assert isinstance(layer, dict)

    def test_result_json_serializable(self):
        layer, _, _ = convert_analysis_to_mapspec_layer(_analysis(30000))
        json.dumps(layer)


class TestSemanticCheck:
    def _spec_with(self, windows, **layer_extra) -> dict:
        return {
            "version": "1.6", "view": {"center": [104, 30], "zoom": 5.0},
            "sources": {"S": {"type": "geojson", "inlineData": _points(3)}},
            "layers": [{"id": "L", "source": "S", "type": "circle",
                        "representation": {"windows": windows}, **layer_extra}],
        }

    def test_valid_plan_passes(self):
        windows = [
            {"min_zoom": 0, "max_zoom": 11, "mode": "visual_heatmap",
             "reason_codes": ["REPR.WINDOW.WORLD.VISUAL_HEATMAP"]},
            {"min_zoom": 11, "max_zoom": 24, "mode": "point_cluster",
             "reason_codes": ["REPR.WINDOW.CITY.POINT_CLUSTER"]},
        ]
        spec = self._spec_with(windows, representation_disclosures=[
            "cluster/heatmap/grid 为视觉聚合（scope=visual_only）——簇计数/热度不是分析结果口径，分析请走分析通道"])
        report = evaluate_cartography_semantics(spec)
        check = next(c for c in report.checks if c.rule == "carto.scale.representation")
        assert check.status == "pass"
        assert check.evidence["active_mode_at_view_zoom"] == "visual_heatmap"

    def test_non_normalizable_plan_fails(self):
        spec = self._spec_with([
            {"min_zoom": 0, "max_zoom": 8, "mode": "raw"},
            {"min_zoom": 9, "max_zoom": 24, "mode": "raw"}])
        report = evaluate_cartography_semantics(spec)
        check = next(c for c in report.checks if c.rule == "carto.scale.representation")
        assert check.status == "fail"

    def test_hidden_window_without_reason_fails(self):
        spec = self._spec_with([
            {"min_zoom": 0, "max_zoom": 8, "mode": "hidden"},
            {"min_zoom": 8, "max_zoom": 24, "mode": "raw",
             "reason_codes": ["X"]}])
        report = evaluate_cartography_semantics(spec)
        check = next(c for c in report.checks if c.rule == "carto.scale.representation")
        assert check.status == "fail"
        assert "hidden_window_without_reason" in check.evidence["problems"]

    def test_aggregation_without_disclosure_fails(self):
        spec = self._spec_with([
            {"min_zoom": 0, "max_zoom": 11, "mode": "visual_heatmap",
             "reason_codes": ["X"]},
            {"min_zoom": 11, "max_zoom": 24, "mode": "raw"}])
        report = evaluate_cartography_semantics(spec)
        check = next(c for c in report.checks if c.rule == "carto.scale.representation")
        assert check.status == "fail"
        assert "aggregation_without_visual_scope_disclosure" in check.evidence["problems"]

    def test_point_mode_on_non_point_layer_fails(self):
        spec = {
            "version": "1.6", "view": {"center": [104, 30], "zoom": 5.0},
            "sources": {"S": {"type": "geojson", "inlineData": _points(3)}},
            "layers": [{"id": "F", "source": "S", "type": "fill",
                        "representation": {"windows": [
                            {"min_zoom": 0, "max_zoom": 11,
                             "mode": "visual_heatmap", "reason_codes": ["X"]},
                            {"min_zoom": 11, "max_zoom": 24, "mode": "raw"}]}}],
        }
        report = evaluate_cartography_semantics(spec)
        check = next(c for c in report.checks if c.rule == "carto.scale.representation")
        assert "point_mode_on_non_point_layer" in check.evidence["problems"]

    def test_gate_divergence_fails(self):
        spec = self._spec_with([
            {"min_zoom": 0, "max_zoom": 14, "mode": "hidden",
             "reason_codes": ["X"]},
            {"min_zoom": 14, "max_zoom": 24, "mode": "raw"}],
            visibility={"min_zoom": 12.0})
        report = evaluate_cartography_semantics(spec)
        check = next(c for c in report.checks if c.rule == "carto.scale.representation")
        assert "visibility_gate_diverges_from_windows" in check.evidence["problems"]

    def test_no_declaration_not_evaluated(self):
        spec = {
            "version": "1.5", "view": {"center": [104, 30], "zoom": 5.0},
            "sources": {"S": {"type": "geojson", "inlineData": _points(3)}},
            "layers": [{"id": "L", "source": "S", "type": "circle"}],
        }
        report = evaluate_cartography_semantics(spec)
        checks = [c for c in report.checks if c.rule == "carto.scale.representation"]
        assert checks == []  # 无声明零行为变化（不新增 not_evaluated 噪声）


class TestQualityLoopReport:
    def test_report_present_for_declared_spec(self):
        spec = {
            "version": "1.6", "view": {"center": [104, 30], "zoom": 5.0},
            "sources": {"S": {"type": "geojson", "inlineData": _points(3)}},
            "layers": [{
                "id": "L", "source": "S", "type": "circle",
                "representation": {"windows": [
                    {"min_zoom": 0, "max_zoom": 11, "mode": "visual_heatmap",
                     "reason_codes": ["REPR.WINDOW.WORLD.VISUAL_HEATMAP"]},
                    {"min_zoom": 11, "max_zoom": 24, "mode": "point_cluster",
                     "reason_codes": ["REPR.WINDOW.CITY.POINT_CLUSTER"]}]},
                "representation_disclosures": ["视觉聚合 visual_only"]}],
        }
        result = review_cartography(spec)
        assert result.representation_report is not None
        row = result.representation_report["layers"][0]
        assert row["layer_id"] == "L"
        assert row["tier"] == "world"
        assert row["active_mode"] == "visual_heatmap"
        assert row["aggregation_scope"] == "visual_only"
        # 报告是只读披露：不影响 review status 判定。
        assert result.representation_report["zoom"] == 5.0

    def test_report_none_for_plain_spec(self):
        spec = {
            "version": "1.5", "view": {"zoom": 5.0},
            "sources": {"S": {"type": "geojson", "inlineData": _points(3)}},
            "layers": [{"id": "L", "source": "S", "type": "circle"}],
        }
        assert representation_at_scale_report(spec) is None
