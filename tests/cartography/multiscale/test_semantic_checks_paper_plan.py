"""carto.paper_scale_plan 检查测试 —— 跨面一致/悬空工件/零行为变化。"""
from __future__ import annotations

from typing import Any, Dict

from app.lib.cartography.multiscale.semantic_checks_paper_plan import (
    check_paper_scale_plan,
)
from app.lib.cartography.semantic_checks import CartographyReport


def _plan(fingerprint: str = "psp-x", **over: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "schema_version": "paper_scale_plan.v1",
        "layer_id": "districts",
        "status": "frozen",
        "artifact_ref": "ref:pgen1",
        "artifact_source_id": "districts__paper@500000",
        "export_window": {"min_zoom": 6.5, "max_zoom": 7.5,
                          "mode": "generalized",
                          "source": "districts__paper@500000"},
        "stats": {"classification_basis": "original_source",
                  "original_count": 3, "artifact_count": 2},
        "fingerprint": fingerprint,
        "family": "polygon",
        "contract": {"target_denominator": 500_000},
    }
    base.update(over)
    return base


def _mapspec(plans: Dict[str, Any], sources: Dict[str, Any] | None = None,
             windows: list | None = None) -> Dict[str, Any]:
    return {
        "sources": sources if sources is not None else {
            "districts__paper@500000": {"type": "geojson", "ref": "ref:pgen1"},
        },
        "layers": [{
            "id": "districts", "type": "fill",
            "scale_profile": {"paper_plans": plans},
            "representation": {"windows": windows if windows is not None else [
                {"min_zoom": 0, "max_zoom": 6.5, "mode": "raw"},
                {"min_zoom": 6.5, "max_zoom": 7.5, "mode": "generalized",
                 "source": "districts__paper@500000"},
                {"min_zoom": 7.5, "max_zoom": 24, "mode": "raw"},
            ]},
        }],
    }


def _run(mapspec: Dict[str, Any]):
    report = CartographyReport()
    layer = mapspec["layers"][0]
    check_paper_scale_plan(report, mapspec, layer, "districts", None)
    return report


class TestNoOpPaths:
    def test_no_plans_no_check(self):
        report = CartographyReport()
        layer = {"id": "x", "scale_profile": {}}
        check_paper_scale_plan(report, {"sources": {}}, layer, "x", None)
        assert len(report.checks) == 0

    def test_planned_status_accepted(self):
        plan = _plan(status="planned", artifact_ref=None,
                     artifact_source_id=None, export_window=None)
        report = _run(_mapspec({"psp-x": plan}, sources={}))
        assert report.checks[0].status == "pass"


class TestFrozenConsistency:
    def test_frozen_consistent_pass(self):
        report = _run(_mapspec({"psp-x": _plan()}))
        assert report.checks[0].status == "pass"
        assert report.checks[0].rule == "carto.paper_scale_plan"

    def test_dangling_artifact_source_fails(self):
        report = _run(_mapspec({"psp-x": _plan()}, sources={}))
        assert report.checks[0].status == "fail"
        assert any("artifact_source_absent" in p
                   for p in report.checks[0].evidence["problems"])

    def test_window_source_mismatch_fails(self):
        plan = _plan(export_window={"min_zoom": 6.5, "max_zoom": 7.5,
                                    "mode": "generalized", "source": "other"})
        report = _run(_mapspec({"psp-x": plan}))
        assert report.checks[0].status == "fail"
        assert any("window_source_mismatch" in p
                   for p in report.checks[0].evidence["problems"])

    def test_window_mode_not_artifact_fails(self):
        plan = _plan(export_window={"min_zoom": 6.5, "max_zoom": 7.5,
                                    "mode": "point_cluster", "source":
                                    "districts__paper@500000"})
        report = _run(_mapspec({"psp-x": plan}))
        assert report.checks[0].status == "fail"

    def test_window_bounds_invalid_fails(self):
        plan = _plan(export_window={"min_zoom": 7.5, "max_zoom": 6.5,
                                    "mode": "generalized",
                                    "source": "districts__paper@500000"})
        report = _run(_mapspec({"psp-x": plan}))
        assert report.checks[0].status == "fail"

    def test_window_not_in_representation_fails(self):
        report = _run(_mapspec({"psp-x": _plan()}, windows=[
            {"min_zoom": 0, "max_zoom": 24, "mode": "raw"}]))
        assert report.checks[0].status == "fail"
        assert any("window_not_in_representation" in p
                   for p in report.checks[0].evidence["problems"])

    def test_classification_basis_not_original_fails(self):
        plan = _plan(stats={"classification_basis": "display_copy"})
        report = _run(_mapspec({"psp-x": plan}))
        assert report.checks[0].status == "fail"

    def test_unknown_schema_version_fails(self):
        report = _run(_mapspec({"psp-x": _plan(schema_version="paper_scale_plan.v9")}))
        assert report.checks[0].status == "fail"
