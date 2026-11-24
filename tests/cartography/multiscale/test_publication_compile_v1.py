"""publication_compile 测试 —— 管线编排 / QA 接入 / 确定性。

场景取验收旅程 1-3 的缩小版：行政区（共享边）、路网（等级取舍）、
小学分布（点聚合）；契约走 paper_scale 真实换算（1:50万）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.lib.cartography.multiscale.importance import (
    ImportanceDerivation,
    ImportanceModel,
)
from app.lib.cartography.multiscale.paper_scale import (
    FrameViewport,
    PublicationScaleContract,
)
from app.lib.cartography.multiscale.publication_compile import (
    PaperCompileError,
    POINT_AGG_MIN_FEATURES,
    compile_paper_scale_plan,
    plan_with_artifact_ref,
)
from app.lib.cartography.multiscale.profile import SourceRevision


def _contract(purpose: str = "overview") -> PublicationScaleContract:
    return PublicationScaleContract(
        target_denominator=500_000, purpose=purpose,
        frame=FrameViewport(width_mm=260.0, height_mm=170.0,
                            scale_reference_lat=39.0,
                            data_bbox=[115.0, 38.0, 117.0, 40.0]),
    )


def _poly(fid: str, x0: float, y0: float, x1: float, y1: float,
          props: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    return {"type": "Feature", "id": fid,
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": dict(props or {})}


def _line(fid: str, coords: List[List[float]], cls: str) -> Dict[str, Any]:
    return {"type": "Feature", "id": fid,
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"道路等级": cls}}


def _pt(fid: str, lon: float, lat: float,
        props: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"type": "Feature", "id": fid,
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": dict(props or {})}


_SRC = SourceRevision(ref="ref:test", feature_count=0,
                      geometry_family="unknown")


class TestPolygonPipeline:
    def test_eliminate_then_topology_order(self):
        # 小面（<0.5km²）先消除，幸存面与共享边拓扑简化
        small = _poly("small", 1.0, 0.0, 1.004, 0.004)   # ≈0.2km²
        a = _poly("a", 0.0, 0.0, 1.0, 1.0)
        b = _poly("b", 1.004, 0.0, 2.0, 1.0)
        src = SourceRevision(ref="ref:test", feature_count=3,
                             geometry_family="polygon")
        out = compile_paper_scale_plan(
            layer_id="districts", feature_collection={
                "type": "FeatureCollection", "features": [a, small, b]},
            source=src, contract=_contract(), latitude_hint=39.0)
        steps = [p["step"] for p in out.plan["pipeline"]]
        assert steps == ["eliminate_small_polygons", "topology_simplify"], \
            "消除必须先于拓扑简化（邻接关系随消除改变）"
        assert out.plan["stats"]["original_count"] == 3
        assert out.plan["stats"]["artifact_count"] == 2
        assert out.plan["family"] == "polygon"
        assert out.plan["stats"]["classification_basis"] == "original_source"
        checks = {r["check"] for r in out.semantic_checks}
        assert "psc.polygon_seams" in checks
        assert out.plan["qa_summary"]["blocking"] == 0

    def test_hard_constraint_protects_small_district(self):
        small = _poly("precious", 1.0, 0.0, 1.004, 0.004)
        a = _poly("a", 0.0, 0.0, 1.0, 1.0)
        b = _poly("b", 1.004, 0.0, 2.0, 1.0)
        src = SourceRevision(ref="ref:test", feature_count=3,
                             geometry_family="polygon")
        contract = _contract()
        from app.lib.cartography.multiscale.paper_scale import HardConstraint
        contract.hard_constraints = [
            HardConstraint(kind="keep_feature_ids", feature_ids=["precious"])]
        out = compile_paper_scale_plan(
            layer_id="districts", feature_collection={
                "type": "FeatureCollection", "features": [a, small, b]},
            source=src, contract=contract, latitude_hint=39.0)
        assert out.plan["stats"]["artifact_count"] == 3, \
            "user-wins 硬约束要素不得被消除"

    def test_invalid_contract_rejected(self):
        bad = _contract()
        bad.target_denominator = 10
        with pytest.raises(PaperCompileError):
            compile_paper_scale_plan(
                layer_id="x", feature_collection={"type": "FeatureCollection",
                                                  "features": []},
                source=_SRC, contract=bad)


class TestLinePipeline:
    def test_road_class_selection_with_connectivity(self):
        feats = [
            _line("exp1", [[0, 0], [1, 0]], "高速公路"),
            _line("link", [[1, 0], [2, 0]], "乡道"),
            _line("exp2", [[2, 0], [3, 0]], "高速公路"),
        ]
        src = SourceRevision(ref="ref:test", feature_count=3,
                             geometry_family="line")
        model = ImportanceModel(
            field="道路等级", value_order=["高速公路", "国道", "省道", "乡道"],
            matched_vocabulary="road_class")
        out = compile_paper_scale_plan(
            layer_id="roads", feature_collection={
                "type": "FeatureCollection", "features": feats},
            source=src, contract=_contract(purpose="overview"),
            importance=ImportanceDerivation(selected=model),
            latitude_hint=39.0)
        step = out.plan["pipeline"][0]
        assert step["step"] == "network_select"
        # overview 秩 3 = 保留高速/国道/省道 → 乡道抑制 → 断网 → 桥接重加
        assert step["bridges_added"] >= 1
        assert step["fully_connected"] is True
        checks = {r["check"]: r for r in out.semantic_checks}
        assert checks["psc.network_connectivity"]["status"] == "pass"
        assert out.plan["qa_summary"]["blocking"] == 0

    def test_no_importance_evidence_no_selection(self):
        feats = [_line("r1", [[0, 0], [1, 0]], "乡道")]
        src = SourceRevision(ref="ref:test", feature_count=1,
                             geometry_family="line")
        out = compile_paper_scale_plan(
            layer_id="roads", feature_collection={
                "type": "FeatureCollection", "features": feats},
            source=src, contract=_contract())
        assert out.plan["pipeline"][0].get("skipped") == "no_importance_evidence"
        assert any("不取舍全保留" in d for d in out.disclosures)
        assert out.plan["stats"]["artifact_count"] == 1


class TestPointPipeline:
    def test_dense_points_aggregated_with_counts(self):
        n = POINT_AGG_MIN_FEATURES + 5
        feats = []
        for i in range(n):
            lon = 0.0005 * (i % 3)          # 前几格（cell≈0.009° @lat0）
            lat = 0.0005 * (i // 3)
            feats.append(_pt(f"s{i}", lon + 0.001, lat + 0.001,
                             {"school_count": 1}))
        far = _pt("far", 5.0, 5.0, {"school_count": 1})
        feats.append(far)
        src = SourceRevision(ref="ref:test", feature_count=len(feats),
                             geometry_family="point")
        out = compile_paper_scale_plan(
            layer_id="schools", feature_collection={
                "type": "FeatureCollection", "features": feats},
            source=src, contract=_contract(), sum_fields=["school_count"],
            latitude_hint=0.0)
        step = out.plan["pipeline"][0]
        assert step["step"] == "aggregate_points"
        assert step["aggregated"] >= POINT_AGG_MIN_FEATURES
        checks = {r["check"]: r for r in out.semantic_checks}
        assert checks["psc.sum_conservation"]["status"] == "pass"

    def test_sparse_points_passthrough(self):
        feats = [_pt("a", 0.001, 0.001), _pt("b", 0.5, 0.5)]
        src = SourceRevision(ref="ref:test", feature_count=2,
                             geometry_family="point")
        out = compile_paper_scale_plan(
            layer_id="poi", feature_collection={
                "type": "FeatureCollection", "features": feats},
            source=src, contract=_contract())
        assert out.plan["pipeline"][0].get("skipped") == "below_threshold"
        assert out.plan["stats"]["artifact_count"] == 2


class TestPlanBlock:
    def test_fingerprint_deterministic_and_content_sensitive(self):
        feats = [_poly("a", 0, 0, 1, 1), _poly("b", 1.004, 0, 2, 1)]
        src = SourceRevision(ref="ref:test", feature_count=2,
                             geometry_family="polygon")
        kwargs = dict(
            layer_id="d", feature_collection={
                "type": "FeatureCollection", "features": feats},
            source=src, contract=_contract(), latitude_hint=39.0)
        o1 = compile_paper_scale_plan(**kwargs)
        o2 = compile_paper_scale_plan(**kwargs)
        assert o1.fingerprint == o2.fingerprint
        assert o1.plan["fingerprint"] == o1.fingerprint
        diff = compile_paper_scale_plan(
            **{**kwargs, "contract": _contract(purpose="planning")})
        assert diff.fingerprint != o1.fingerprint

    def test_freeze_backfill(self):
        feats = [_poly("a", 0, 0, 1, 1), _poly("b", 1.004, 0, 2, 1)]
        src = SourceRevision(ref="ref:test", feature_count=2,
                             geometry_family="polygon")
        out = compile_paper_scale_plan(
            layer_id="d", feature_collection={
                "type": "FeatureCollection", "features": feats},
            source=src, contract=_contract(), latitude_hint=39.0)
        frozen = plan_with_artifact_ref(
            out.plan, artifact_ref="ref:gen1", artifact_source_id="d__paper",
            window={"min_zoom": 7.0, "max_zoom": 8.0, "mode": "generalized",
                    "source": "d__paper"})
        assert frozen["status"] == "frozen"
        assert frozen["artifact_ref"] == "ref:gen1"
        assert frozen["export_window"]["source"] == "d__paper"
        assert out.plan["status"] == "planned"   # 原计划不被修改

    def test_schema_version_pinned(self):
        out = compile_paper_scale_plan(
            layer_id="x", feature_collection={"type": "FeatureCollection",
                                              "features": []},
            source=SourceRevision(ref="ref:t", geometry_family="polygon"),
            contract=_contract())
        assert out.plan["schema_version"] == "paper_scale_plan.v1"
        assert out.plan["status"] == "planned"
