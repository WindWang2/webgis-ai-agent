"""carto.scale.profile 语义检查测试（M5）—— 档案声明一致性 QA。"""
from __future__ import annotations

import pytest

from app.lib.cartography.multiscale.plan import (
    build_scale_profile,
    profile_with_artifact_refs,
)
from app.lib.cartography.multiscale.importance import derive_importance_model
from app.lib.cartography.multiscale.profile import SourceRevision, to_mapspec_layer_block
from app.lib.cartography.multiscale.semantic_checks_scale_profile import (
    check_scale_profile,
)
from app.lib.cartography.semantic_checks import CartographyReport

pytestmark = pytest.mark.cartography


def _roads(n: int = 60) -> dict:
    classes = ["高速公路", "国道", "省道", "县道", "乡道"]
    feats = []
    for i in range(n):
        feats.append({
            "type": "Feature", "id": f"r{i}",
            "properties": {"道路等级": classes[i % 5]},
            "geometry": {"type": "LineString", "coordinates": [
                [104.0 + i * 0.003 + k * 0.0002, 30.6 + (1e-5 if k % 2 else -1e-5)]
                for k in range(30)]},
        })
    return {"type": "FeatureCollection", "features": feats}


def _profile_block(layer_id: str = "roads", attach: bool = True) -> dict:
    dist = {"高速公路": 12, "国道": 12, "省道": 12, "县道": 12, "乡道": 12}
    result = build_scale_profile(
        layer_id=layer_id,
        feature_collection=_roads(),
        source=SourceRevision(ref="ref:geojson:t", feature_count=60,
                              geometry_family="line"),
        geometry_family="line",
        latitude_hint=30.6,
        importance=derive_importance_model(
            {"道路等级": {"type": "string"}}, {"道路等级": dist}, feature_count=60),
    )
    from app.lib.cartography.multiscale.profile import (
        representation_windows_of_profile,
    )
    if attach and result.artifacts:
        ref_map = {t: (f"ref:gen:{t}", f"{layer_id}__gen@{t}")
                   for t in result.artifacts}
        profile2 = profile_with_artifact_refs(result.profile, ref_map)
    else:
        profile2 = result.profile
    block = to_mapspec_layer_block(profile2)
    windows = representation_windows_of_profile(profile2)
    return block, windows


def _spec(block: dict, windows: list, sources_extra: dict | None = None) -> dict:
    sources = {"src-roads": {"type": "geojson", "inlineData": {
        "type": "FeatureCollection", "features": []}}}
    sources.update(sources_extra or {})
    gen_sources = {
        w["source"]: {"type": "geojson", "ref": 'ref:gen', "ref_id": "ref:gen"}
        for w in windows if w.get("mode") in ("generalized", "aggregate_grid")
    } if sources_extra is None else {}
    sources.update(gen_sources)
    layer = {"id": "roads", "source": "src-roads", "type": "line",
             "scale_profile": block}
    if any(w.get("mode") != "raw" for w in windows):
        layer["representation"] = {"windows": windows}
    return {"version": "1.8", "view": {"center": [104, 30], "zoom": 10},
            "sources": sources, "layers": [layer]}


def _run(mapspec: dict) -> CartographyReport:
    report = CartographyReport()
    layer = mapspec["layers"][0]
    check_scale_profile(report, mapspec, layer, "roads", "src-roads")
    return report


def _codes(report: CartographyReport) -> list:
    return [c.rule for c in report.checks if c.rule == "carto.scale.profile"]


class TestScaleProfileCheck:
    def test_no_declaration_no_check(self):
        report = _run({"version": "1.8", "sources": {}, "layers": [
            {"id": "x", "type": "line", "source": "s"}]})
        assert _codes(report) == []  # 未 apply 层零行为变化

    def test_valid_profile_passes(self):
        block, windows = _profile_block()
        report = _run(_spec(block, windows))
        results = [c for c in report.checks if c.rule == "carto.scale.profile"]
        assert results and results[0].status == "pass"

    def test_dangling_artifact_window_fails(self):
        block, windows = _profile_block()
        # 工件源从 spec.sources 抽走 → 悬空窗
        spec = _spec(block, windows, sources_extra={})
        for w in spec["layers"][0].get("representation", {}).get("windows", []):
            if w.get("source"):
                spec["sources"].pop(w["source"], None)
        report = _run(spec)
        results = [c for c in report.checks if c.rule == "carto.scale.profile"]
        assert results and results[0].status == "fail"
        assert "artifact_window_source_missing" in results[0].evidence.get(
            "problems", [])

    def test_bad_classification_basis_fails(self):
        block, windows = _profile_block()
        block["classification_basis"] = "generalized_artifact"  # 违反 D7
        report = _run(_spec(block, windows))
        results = [c for c in report.checks if c.rule == "carto.scale.profile"]
        assert results and results[0].status == "fail"

    def test_selection_without_evidence_fails(self):
        block, windows = _profile_block()
        for band in block["bands"]:
            band["selection"]["importance_evidence"] = "none"
            band["selection"]["kept_values"] = ["伪造主干"]
        report = _run(_spec(block, windows))
        results = [c for c in report.checks if c.rule == "carto.scale.profile"]
        assert results and results[0].status == "fail"
