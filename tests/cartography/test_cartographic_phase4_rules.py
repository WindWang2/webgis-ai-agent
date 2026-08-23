"""Phase 4 quantitative cartographic rules (spec P4).

``carto.visualvar.overload`` (Bertin concurrent variables),
``carto.label.collision_est`` (bounded label-box estimate, no rendering) and
``carto.scale.svs`` (smallest visible size at the target scale). Same
contract as Phase 1: a rule emits only when its evidence domain exists, and
structural/data-shaping fixes stay advisory (never AUTO_SAFE).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.lib.cartography.quality_loop import review_and_repair_cartography
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics


_PROFILE = {
    "featureCount": 500,
    "bbox": [116.0, 39.8, 116.4, 40.0],
    "geometryTypes": ["Polygon"],
    "crs": "EPSG:4326",
    "crs_status": "explicit",
    "fields": {
        "name": {"type": "string", "sampleValues": ["北京市朝阳区", "海淀区"],
                 "null_count": 0},
        "pop": {"type": "number", "min": 0, "max": 9, "null_count": 0},
        "gdp": {"type": "number", "min": 0, "max": 9, "null_count": 0},
        "area": {"type": "number", "min": 0, "max": 9, "null_count": 0},
        "den": {"type": "number", "min": 0, "max": 9, "null_count": 0},
    },
}


def _spec(layer, *, zoom=10, profile=None, feature_count=None):
    profile = dict(profile or _PROFILE)
    if feature_count is not None:
        profile["featureCount"] = feature_count
    return {
        "version": "1.0",
        "view": {"center": [116.2, 39.9], "zoom": zoom},
        "layout": {"legend": {"visible": True}},
        "sources": {"s1": {"type": "geojson", "ref": "ref:geojson-x",
                           "profile": profile}},
        "layers": [layer],
    }


def _checks(mapspec, rule):
    report = evaluate_cartography_semantics(mapspec)
    return [c for c in report.to_dict()["checks"] if c["rule"] == rule]


def _dd(field):
    return {"method": "step", "field": field, "default": "#eeeeee",
            "stops": [[5, "#999999"]]}


# ─── carto.visualvar.overload ────────────────────────────────────────────

def test_four_concurrent_variables_fail_with_split_advice():
    layer = {"id": "l1", "source": "s1", "type": "fill", "paint": {
        "fill-color": _dd("pop"), "fill-opacity": _dd("gdp"),
        "fill-outline-color": _dd("area"), "fill-pattern": _dd("den")}}
    check = _checks(_spec(layer), "carto.visualvar.overload")[0]
    assert check["status"] == "fail"
    assert check["evidence"]["encoded_field_count"] == 4
    assert check["suggested_fix"]["operation"] == "split_layer"
    # Structural change — advisory only.
    assert check["repairability"] == "not_repairable"


def test_three_variables_warn_and_two_pass():
    warn_layer = {"id": "l1", "source": "s1", "type": "fill", "paint": {
        "fill-color": _dd("pop"), "fill-opacity": _dd("gdp"),
        "fill-outline-color": _dd("area")}}
    assert _checks(_spec(warn_layer), "carto.visualvar.overload")[0]["status"] == "warning"
    ok_layer = {"id": "l1", "source": "s1", "type": "fill", "paint": {
        "fill-color": _dd("pop"), "fill-opacity": _dd("gdp")}}
    assert _checks(_spec(ok_layer), "carto.visualvar.overload")[0]["status"] == "pass"


def test_redundant_channels_for_one_variable_are_not_overload():
    # Bertin: several channels encoding the SAME variable is redundancy
    # (legitimate reinforcement), not concurrent-variable overload.
    layer = {"id": "l1", "source": "s1", "type": "fill", "paint": {
        "fill-color": _dd("pop"), "fill-opacity": _dd("pop"),
        "fill-outline-color": _dd("pop")}}
    check = _checks(_spec(layer), "carto.visualvar.overload")[0]
    assert check["status"] == "pass"
    assert check["evidence"]["encoded_field_count"] == 1


def test_constant_paint_is_out_of_domain():
    layer = {"id": "l1", "source": "s1", "type": "fill",
             "paint": {"fill-color": "#abcdef"}}
    assert _checks(_spec(layer), "carto.visualvar.overload") == []


# ─── carto.label.collision_est ───────────────────────────────────────────

def _label_layer(size=14, field="{name}"):
    return {"id": "lab", "source": "s1", "type": "symbol",
            "layout": {"text-field": field, "text-size": size}}


def test_dense_cjk_labels_fail():
    check = _checks(_spec(_label_layer()), "carto.label.collision_est")[0]
    assert check["status"] == "fail"
    assert check["evidence"]["label_ink_ratio"] > 0.25
    assert check["evidence"]["model"] == "uniform_density_label_boxes_estimate"
    # CJK glyphs are ~1em wide, ASCII ~0.6em — the estimate uses real samples.
    assert check["evidence"]["avg_label_em_width"] > 3
    assert check["suggested_fix"]["operation"] == "thin_labels"


def test_sparse_labels_pass():
    check = _checks(
        _spec(_label_layer(size=10), feature_count=8),
        "carto.label.collision_est",
    )[0]
    assert check["status"] == "pass"


def test_label_rule_needs_a_label_layer_and_samples():
    # Non-label layer: out of domain.
    fill = {"id": "l1", "source": "s1", "type": "fill",
            "paint": {"fill-color": "#abcdef"}}
    assert _checks(_spec(fill), "carto.label.collision_est") == []
    # Label layer without text-field: out of domain.
    no_field = {"id": "lab", "source": "s1", "type": "symbol",
                "layout": {"text-size": 12}}
    assert _checks(_spec(no_field), "carto.label.collision_est") == []
    # Label field with no profile samples: unevaluable, not a fake pass.
    profile = {**_PROFILE, "fields": {
        **_PROFILE["fields"], "name": {"type": "string", "sampleValues": []}}}
    assert _checks(
        _spec(_label_layer(), profile=profile), "carto.label.collision_est"
    ) == []


def test_label_rule_needs_a_camera():
    spec = _spec(_label_layer())
    del spec["view"]
    assert _checks(spec, "carto.label.collision_est") == []


# ─── carto.scale.svs ─────────────────────────────────────────────────────

def _fill_layer():
    return {"id": "l1", "source": "s1", "type": "fill",
            "paint": {"fill-color": "#abcdef"}}


def test_polygons_below_smallest_visible_size_fail():
    check = _checks(_spec(_fill_layer(), zoom=4), "carto.scale.svs")[0]
    assert check["status"] == "fail"
    assert check["evidence"]["avg_feature_area_px"] < check["evidence"]["svs_area_px"]
    assert check["suggested_fix"]["operation"] == "generalize"
    assert check["suggested_fix"]["alternative"] == "switch_symbolization"
    assert check["repairability"] == "not_repairable"


def test_polygons_are_legible_when_zoomed_in():
    assert _checks(_spec(_fill_layer(), zoom=12), "carto.scale.svs")[0]["status"] == "pass"


def test_svs_warns_near_the_visibility_floor():
    # Tuned so the average area lands between SVS (2.25px²) and 4×SVS.
    check = _checks(
        _spec(_fill_layer(), zoom=10, feature_count=15_000), "carto.scale.svs",
    )[0]
    assert check["status"] == "warning"
    svs = check["evidence"]["svs_area_px"]
    assert svs < check["evidence"]["avg_feature_area_px"] < svs * 4
    assert check["suggested_fix"]["operation"] == "switch_symbolization"


def test_svs_domain_is_polygon_fills_only():
    # Point layer: minimum visible size comes from the symbol radius, not
    # from data — out of domain.
    point_profile = {**_PROFILE, "geometryTypes": ["Point"]}
    circle = {"id": "l1", "source": "s1", "type": "circle",
              "paint": {"circle-radius": 5}}
    assert _checks(_spec(circle, profile=point_profile), "carto.scale.svs") == []
    # Mixed geometry: the uniform-area model does not hold.
    mixed = {**_PROFILE, "geometryTypes": ["Polygon", "Point"]}
    assert _checks(_spec(_fill_layer(), profile=mixed), "carto.scale.svs") == []
    # No zoom: unevaluable.
    spec = _spec(_fill_layer())
    del spec["view"]
    assert _checks(spec, "carto.scale.svs") == []


# ─── loop integration: advisory rules must not be auto-repaired ──────────

def test_phase4_failures_are_never_auto_repaired():
    layer = {"id": "l1", "source": "s1", "type": "fill", "paint": {
        "fill-color": _dd("pop"), "fill-opacity": _dd("gdp"),
        "fill-outline-color": _dd("area"), "fill-pattern": _dd("den")}}
    result = review_and_repair_cartography(_spec(layer, zoom=4))
    assert result.status == "failed_unrepairable"
    assert result.repair_count == 0
    # The candidate is returned untouched — no silent layer splitting or
    # generalization of the user's data.
    assert result.mapspec["layers"][0]["paint"]["fill-pattern"] == _dd("den")
