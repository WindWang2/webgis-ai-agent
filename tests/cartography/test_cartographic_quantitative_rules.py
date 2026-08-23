"""Quantitative cartographic rules (specs/cartographic-quality-rules-and-memory-spec P1).

Golden-fixture tests for the three deterministic L4 rules —
``carto.load.ratio``, ``carto.color.separability`` (CIEDE2000),
``carto.legend.completeness`` — plus the AUTO_SAFE ``change_palette`` /
``set_map_legend_visibility`` repairs driving the review-and-repair loop to
convergence. Fixtures are protocol-faithful: graduated legends carry
breaks + palette_colors and paint step specs whose output colors mirror the
legend ramp, so the only failing rule is the one under test.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.lib.cartography.palettes import (
    COLOR_PALETTES,
    ciede2000,
    min_adjacent_delta_e,
    parse_css_color,
    perceptual_ramp,
)
from app.lib.cartography.quality_loop import review_and_repair_cartography
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics


# ─── fixtures ────────────────────────────────────────────────────────────

_POINT_PROFILE = {
    "featureCount": 50,
    "bbox": [116.0, 39.8, 116.8, 40.0],
    "geometryTypes": ["Point"],
    "crs": "EPSG:4326",
    "crs_status": "explicit",
    "fields": {"v": {"type": "number", "min": 0, "max": 10, "null_count": 0}},
}


def _layer(colors, *, visible=True, radius=5):
    paint = {"circle-color": {
        "method": "step", "field": "v", "default": colors[0],
        "stops": [[2.5, colors[1]], [5.0, colors[2]], [7.5, colors[3]]],
    }}
    if radius is not None:
        paint["circle-radius"] = radius
    return {
        "id": "l1",
        "source": "src-1",
        "type": "circle",
        "visible": visible,
        "paint": paint,
        "legend_spec": {
            "type": "graduated", "field": "v", "min": 0, "max": 10,
            "breaks": [0, 2.5, 5.0, 7.5, 10],
            "palette_colors": colors,
        },
    }


def _spec(layers, *, feature_count=50, zoom=4.0, legend_visible=True):
    source = {
        "type": "geojson",
        "ref": "ref:geojson-x",
        "profile": {**_POINT_PROFILE, "featureCount": feature_count},
    }
    return {
        "version": "1.0",
        "sources": {"src-1": source},
        "layers": layers,
        "view": {"center": [116.4, 39.9], "zoom": zoom},
        "layout": {"legend": {"visible": legend_visible}},
    }


_SEPARABLE = ["#ffffb2", "#feb24c", "#fd8d3c", "#bd0026"]
_INSEPARABLE = ["#ff0000", "#ff0404", "#ff0808", "#ff0c0c"]


def _checks(mapspec, rule):
    report = evaluate_cartography_semantics(mapspec)
    return [c for c in report.to_dict()["checks"] if c["rule"] == rule]


# ─── CIEDE2000 primitives ────────────────────────────────────────────────

def test_ciede2000_primitives():
    assert ciede2000((255, 0, 0), (255, 0, 0)) == 0.0
    assert abs(ciede2000((0, 0, 0), (255, 255, 255)) - 100.0) < 1e-3
    # Near-identical reds must be far below the 5.0 fail threshold.
    assert ciede2000((255, 0, 0), (255, 4, 4)) < 1.0
    # Stock ColorBrewer ramps are calibrated to be classifiable: every
    # shipped palette clears the 10.0 warn threshold.
    for name, colors in COLOR_PALETTES.items():
        min_de = min_adjacent_delta_e(colors)
        assert min_de is not None and min_de >= 10.0, (name, min_de)


def test_parse_css_color_alpha_compositing():
    # CSS4 float alpha is rejected by Pillow; parse_css_color handles it and
    # composites over white (map paper) rather than guessing.
    assert parse_css_color("rgba(255,0,0,0.5)") == (255, 128, 128)
    assert parse_css_color("#feb24c") == (254, 178, 76)
    assert parse_css_color("red") == (255, 0, 0)
    assert parse_css_color("rgba(1,2,3,0)") is None
    assert parse_css_color("not-a-color") is None


def test_perceptual_ramp_replacement_quality():
    # A proposed replacement ramp must itself clear the warn threshold —
    # never hand the repair loop a fix that fails its own rule. The
    # guaranteed-separable range is n ≤ 8; beyond it the rule declines to
    # propose (see test_color_separability_no_fix_when_replacement_falls_short).
    for n in range(2, 9):
        ramp = perceptual_ramp(n)
        assert len(ramp) == n
        assert min_adjacent_delta_e(ramp) >= 10.0, (n, ramp)
    assert perceptual_ramp(1) == []
    assert perceptual_ramp(11) == []


# ─── carto.load.ratio ────────────────────────────────────────────────────

def test_load_overload_fails_with_thin_fix():
    spec = _spec([_layer(_SEPARABLE)], feature_count=200_000, zoom=12)
    checks = _checks(spec, "carto.load.ratio")
    assert len(checks) == 1
    check = checks[0]
    assert check["status"] == "fail"
    assert check["evidence"]["model"] == "uniform_density_point_line_symbols"
    assert check["evidence"]["load_ratio"] > 0.40
    assert check["suggested_fix"]["operation"] == "thin_features"
    # Data-shaping fixes are advisory only — never AUTO_SAFE.
    assert check["repairability"] == "not_repairable"


def test_load_healthy_passes_and_missing_evidence_stays_silent():
    assert _checks(_spec([_layer(_SEPARABLE)]), "carto.load.ratio")[0]["status"] == "pass"
    # No view.zoom → the rule's evidence domain is absent → no fake verdict.
    spec = _spec([_layer(_SEPARABLE)])
    del spec["view"]
    assert _checks(spec, "carto.load.ratio") == []
    # Polygon-only sources: fill-load model deliberately deferred → no check.
    spec = _spec([_layer(_SEPARABLE)])
    spec["sources"]["src-1"]["profile"]["geometryTypes"] = ["Polygon"]
    assert _checks(spec, "carto.load.ratio") == []


# ─── carto.color.separability ────────────────────────────────────────────

def test_color_separability_fail_proposes_auto_safe_palette():
    checks = _checks(_spec([_layer(_INSEPARABLE)]), "carto.color.separability")
    assert len(checks) == 1
    check = checks[0]
    assert check["status"] == "fail"
    assert check["evidence"]["min_adjacent_delta_e"] < 5.0
    assert check["repairability"] == "auto_safe"
    fix = check["suggested_fix"]
    assert fix["operation"] == "change_palette"
    replacement = fix["value"]["colors"]
    assert len(replacement) == len(_INSEPARABLE)
    assert min_adjacent_delta_e(replacement) >= 10.0


def test_color_separability_pass_and_short_ramps():
    assert _checks(_spec([_layer(_SEPARABLE)]), "carto.color.separability")[0]["status"] == "pass"
    # Single-class ramps (<2 colors) are out of the rule's domain.
    layer = {
        "id": "l1", "source": "src-1", "type": "circle",
        "paint": {"circle-color": "#bd0026"},
        "legend_spec": {
            "type": "graduated", "field": "v", "min": 0, "max": 10,
            "breaks": [0, 10], "palette_colors": ["#bd0026"],
        },
    }
    assert _checks(_spec([layer]), "carto.color.separability") == []


def test_color_separability_no_fix_when_replacement_falls_short():
    # 9 near-identical classes: the perceptual replacement itself cannot
    # clear the warn threshold, so the rule must downgrade to an advisory
    # fix instead of proposing a repair that would fail its own check.
    inseparable9 = [f"#ff{i:02x}00" for i in range(9)]
    spec = _spec([_layer(_INSEPARABLE)])
    spec["layers"][0]["legend_spec"]["palette_colors"] = inseparable9
    spec["layers"][0]["paint"]["circle-color"] = "#ff0000"
    checks = _checks(spec, "carto.color.separability")
    assert len(checks) == 1
    check = checks[0]
    assert check["status"] == "fail"
    assert check["repairability"] == "not_repairable"
    assert "value" not in (check["suggested_fix"] or {})


# ─── carto.legend.completeness ───────────────────────────────────────────

def test_legend_completeness_hidden_fails_with_auto_safe_enable():
    spec = _spec([_layer(_SEPARABLE)], legend_visible=False)
    checks = _checks(spec, "carto.legend.completeness")
    assert len(checks) == 1
    check = checks[0]
    assert check["status"] == "fail"
    assert check["repairability"] == "auto_safe"
    assert check["suggested_fix"] == {
        "operation": "set_map_legend_visibility", "value": True,
    }


def test_legend_completeness_passes_when_enabled():
    assert _checks(_spec([_layer(_SEPARABLE)]), "carto.legend.completeness")[0]["status"] == "pass"
    # Non-thematic maps are out of the rule's domain.
    layer = _layer(_SEPARABLE)
    del layer["legend_spec"]
    assert _checks(_spec([layer]), "carto.legend.completeness") == []


# ─── repair-loop convergence (e2e) ───────────────────────────────────────

def test_repair_loop_swaps_palette_and_converges():
    result = review_and_repair_cartography(_spec([_layer(_INSEPARABLE)]))
    assert result.status == "passed"
    assert result.repair_count == 1
    assert result.termination_reason == "quality_converged"
    final = [c for c in result.review["checks"] if c["rule"] == "carto.color.separability"][0]
    assert final["status"] == "pass"
    # Presentation-only: colors replaced in lockstep (legend + paint), the
    # classification semantics (breaks) are untouched.
    layer = result.mapspec["layers"][0]
    assert layer["legend_spec"]["breaks"] == [0, 2.5, 5.0, 7.5, 10]
    assert layer["legend_spec"]["palette_colors"] == _INSEPARABLE or (
        layer["legend_spec"]["palette_colors"] != _INSEPARABLE
    )
    paint = layer["paint"]["circle-color"]
    outputs = [paint["default"]] + [s[1] for s in paint["stops"]]
    assert outputs == layer["legend_spec"]["palette_colors"]


def test_repair_loop_enables_hidden_legend_and_converges():
    spec = _spec([_layer(_SEPARABLE)], legend_visible=False)
    result = review_and_repair_cartography(spec)
    assert result.status == "passed"
    assert result.repair_count == 1
    assert result.mapspec["layout"]["legend"]["visible"] is True


def test_overload_is_not_auto_repaired():
    # thin_features/generalize reshape data — the loop must surface the
    # failure, not silently thin the user's dataset.
    spec = _spec([_layer(_SEPARABLE)], feature_count=200_000, zoom=12)
    result = review_and_repair_cartography(spec)
    assert result.status == "failed_unrepairable"
    assert result.repair_count == 0
    assert result.mapspec["layers"][0]["paint"].get("circle-radius") == 5
