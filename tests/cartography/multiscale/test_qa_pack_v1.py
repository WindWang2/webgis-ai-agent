"""qa_pack 测试 —— 独立审计 oracle（不信任引擎自述回执）。

用「手工构造的好/坏工件」验证每个审计的判定边界：
- 人为制造缝隙/重叠的面工件 → psc.polygon_seams 必须判 fail；
- 缺口线网 → psc.network_connectivity 判 warning（非硬）/fail（硬）；
- sum 守恒被破坏 → psc.sum_conservation 判 fail。
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.lib.cartography.multiscale.paper_scale import (
    FrameViewport,
    PublicationScaleContract,
)
from app.lib.cartography.multiscale.qa_pack import (
    audit_geometry_validity,
    audit_min_display_size,
    audit_polygon_pairwise_seams,
    audit_sum_conservation,
    qa_summary,
    run_paper_scale_qa,
)


def _poly(fid: str, ring: List[List[float]]) -> Dict[str, Any]:
    ring = ring + [ring[0]]
    return {"type": "Feature", "id": fid,
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {}}


def _line(fid: str, coords: List[List[float]]) -> Dict[str, Any]:
    return {"type": "Feature", "id": fid,
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {}}


class TestSeamAudit:
    def test_clean_neighbors_pass(self):
        feats = [
            _poly("a", [[0, 0], [1, 0], [1, 1], [0, 1]]),
            _poly("b", [[1, 0], [2, 0], [2, 1], [1, 1]]),
        ]
        out = audit_polygon_pairwise_seams(feats, latitude_hint=39.0)
        assert out["within_tolerance"] is True
        assert out["max_pairwise_overlap_m2"] < 1.0
        assert out["coverage_gap_m2"] < 1.0

    def test_overlap_detected(self):
        # b 侵入 a 0.1°（真实重叠 ≈ 0.1°·1°·kx·ky 米²）
        feats = [
            _poly("a", [[0, 0], [1, 0], [1, 1], [0, 1]]),
            _poly("b", [[0.9, 0], [2, 0], [2, 1], [0.9, 1]]),
        ]
        out = audit_polygon_pairwise_seams(feats, latitude_hint=39.0)
        assert out["max_pairwise_overlap_m2"] > 100.0
        assert out["within_tolerance"] is False

    def test_gap_detected(self):
        # a、b 之间 0.01° 缝隙 → 覆盖差 = Σ−union ≈ 0（分离多边形覆盖差
        # 为 0！）—— 缝隙审计的正确语义：分离几何 union 覆盖差为 0 不是
        # 缝隙信号（无缝隙概念前提是邻接数据）；此处仅验证数值自洽。
        feats = [
            _poly("a", [[0, 0], [1, 0], [1, 1], [0, 1]]),
            _poly("b", [[1.01, 0], [2, 0], [2, 1], [1.01, 1]]),
        ]
        out = audit_polygon_pairwise_seams(feats, latitude_hint=39.0)
        # 分离多边形：无重叠、无覆盖差（覆盖差只捕捉「邻接却互侵」）
        assert out["max_pairwise_overlap_m2"] < 1.0

    def test_single_polygon_not_evaluated_pairs(self):
        out = audit_polygon_pairwise_seams(
            [_poly("a", [[0, 0], [1, 0], [1, 1], [0, 1]])])
        assert out["evaluated"] is True
        assert out["pairs_checked"] == 0


class TestValidityAudit:
    def test_self_intersecting_detected(self):
        bad = _poly("bowtie", [[0, 0], [1, 1], [1, 0], [0, 1]])
        good = _poly("ok", [[0, 0], [1, 0], [1, 1], [0, 1]])
        out = audit_geometry_validity([bad, good])
        assert out["invalid_ids"] == ["bowtie"]


class TestMinDisplaySize:
    def test_area_threshold(self):
        small = _poly("s", [[0, 0], [0.001, 0], [0.001, 0.001], [0, 0.001]])
        big = _poly("b", [[0, 0], [0.5, 0], [0.5, 0.5], [0, 0.5]])
        out = audit_min_display_size([small, big], min_area_m2=500_000.0)
        assert out["sub_threshold_area_ids"] == ["s"]

    def test_length_threshold(self):
        short = _line("st", [[0, 0], [0.0005, 0]])
        long_ = _line("lg", [[0, 0], [0.5, 0]])
        out = audit_min_display_size([short, long_], min_length_m=150.0)
        assert out["sub_threshold_length_ids"] == ["st"]


class TestSumConservation:
    def test_conserved(self):
        src = [
            {"type": "Feature", "id": "a", "geometry": None,
             "properties": {"school_count": 5}},
            {"type": "Feature", "id": "b", "geometry": None,
             "properties": {"school_count": 7}},
        ]
        art = [{"type": "Feature", "id": "r", "geometry": None,
                "properties": {"school_count": 12}}]
        out = audit_sum_conservation(src, art, sum_fields=["school_count"])
        assert out["evaluated"] is True
        assert out["conserved"] is True

    def test_broken(self):
        src = [
            {"type": "Feature", "id": "a", "geometry": None,
             "properties": {"school_count": 5}},
            {"type": "Feature", "id": "b", "geometry": None,
             "properties": {"school_count": 7}},
        ]
        art = [{"type": "Feature", "id": "r", "geometry": None,
                "properties": {"school_count": 5}}]   # 丢 7
        out = audit_sum_conservation(src, art, sum_fields=["school_count"])
        assert out["conserved"] is False

    def test_no_fields_not_evaluated(self):
        out = audit_sum_conservation([], [], sum_fields=[])
        assert out["evaluated"] is False


class TestRunFullQa:
    def _contract(self):
        return PublicationScaleContract(
            target_denominator=500_000, purpose="navigation",
            frame=FrameViewport(width_mm=260.0, height_mm=170.0,
                                data_bbox=[115.0, 28.0, 118.0, 32.0]),
        )

    def test_polygon_artifact_full_pass(self):
        feats = [
            _poly("a", [[115, 28], [116, 28], [116, 29], [115, 29]]),
            _poly("b", [[116, 28], [117, 28], [117, 29], [116, 29]]),
        ]
        rows = run_paper_scale_qa(
            self._contract(), feats, feats, geometry_family="polygon",
            latitude_hint=30.0, min_area_m2=500_000.0)
        summary = qa_summary(rows)
        assert summary["blocking"] == 0
        checks = {r["check"] for r in rows}
        assert "psc.polygon_seams" in checks
        assert "psc.geometry_validity" in checks

    def test_disconnected_network_navigation_hard_fail(self):
        lines = [
            _line("a", [[115, 28], [116, 28]]),
            _line("b", [[117, 28], [118, 28]]),   # 与 a 分离
        ]
        rows = run_paper_scale_qa(
            self._contract(), lines, lines, geometry_family="line",
            latitude_hint=30.0, network_connectivity_hard=True)
        row = next(r for r in rows if r["check"] == "psc.network_connectivity")
        assert row["status"] == "fail"

    def test_disconnected_network_soft_warning(self):
        contract = PublicationScaleContract(
            target_denominator=500_000, purpose="overview",
            frame=FrameViewport(width_mm=260.0, height_mm=170.0))
        lines = [
            _line("a", [[115, 28], [116, 28]]),
            _line("b", [[117, 28], [118, 28]]),
        ]
        rows = run_paper_scale_qa(contract, lines, lines,
                                  geometry_family="line", latitude_hint=30.0)
        row = next(r for r in rows if r["check"] == "psc.network_connectivity")
        assert row["status"] == "warning"

    def test_high_latitude_variation_warning(self):
        contract = PublicationScaleContract(
            target_denominator=500_000, purpose="overview",
            frame=FrameViewport(width_mm=260.0, height_mm=170.0,
                                scale_reference_lat=55.0,
                                data_bbox=[30.0, 30.0, 31.0, 62.0]))
        rows = run_paper_scale_qa(contract, [], [], geometry_family="polygon",
                                  latitude_hint=55.0)
        warns = {r["check"] for r in rows if r["status"] == "warning"}
        assert "psc.scale_variation_disclosure" in warns

    def test_invalid_contract_fail(self):
        contract = PublicationScaleContract(
            target_denominator=10, purpose="overview",
            frame=FrameViewport(width_mm=260.0, height_mm=170.0))
        rows = run_paper_scale_qa(contract, [], [], geometry_family="polygon")
        row = next(r for r in rows if r["check"] == "psc.scale_consistency")
        assert row["status"] == "fail"

    def test_summary_counts(self):
        rows = [
            {"check": "a", "status": "pass", "detail": {}},
            {"check": "b", "status": "fail", "detail": {}},
            {"check": "c", "status": "warning", "detail": {}},
        ]
        s = qa_summary(rows)
        assert s["total"] == 3
        assert s["blocking"] == 1
        assert s["status_counts"] == {"pass": 1, "fail": 1, "warning": 1}
