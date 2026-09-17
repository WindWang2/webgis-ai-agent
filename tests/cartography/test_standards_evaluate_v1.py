"""Deterministic standards evaluation — per-kind checkers (v1, ADR-0200).

Each kind proves both directions: an intentional-violation fixture is caught,
a clean fixture is not. Evidence-missing paths land on ``not_evaluated``,
vacuous preconditions on ``not_applicable`` — never fake passes.
"""
from __future__ import annotations

from typing import Any, Dict


from app.lib.cartography.context_matrix import evaluate_cell
from app.lib.cartography.standards.profile import resolve_profile

FIVE_COLORS = ["#ffffcc", "#ffeda0", "#feb24c", "#f03b20", "#bd0026"]  # noqa: F841 — 违规夹具示例色


def base_mapspec() -> Dict[str, Any]:
    return {
        "version": "1.0",
        "cartographic_profile": "thematic_map",
        "sources": {"s1": {
            "type": "geojson", "ref": "ref:dataset-1",
            "profile": {
                "crs": "EPSG:4326", "crs_status": "explicit",
                "bbox": [-125.0, 24.0, -66.0, 50.0],
                "featureCount": 3100,
                "geometryTypes": ["Polygon", "MultiPolygon"],
                "fields": {
                    "median_income": {"type": "number"},
                    "state_name": {"type": "string"},
                },
            },
        }},
        "layers": [{
            "id": "l1", "source": "s1", "type": "fill",
            "legend_spec": {
                "type": "graduated", "field": "median_income",
                "palette": "Viridis", "method": "quantiles",
                "title": "家庭收入中位数", "unit": "美元",
                "min": 20000.0, "max": 120000.0,
                "breaks": [40000.0, 60000.0, 80000.0, 100000.0],
            },
        }],
        "layout": {
            "legend": {"visible": True, "title": "家庭收入中位数"},
            "components": [
                {"id": "c-title", "type": "title", "enabled": True},
                {"id": "c-scale", "type": "scale_bar", "enabled": True},
                {"id": "c-north", "type": "north_arrow", "enabled": True},
                {"id": "c-attr", "type": "attribution", "enabled": True,
                 "options": {"text": "数据来源：美国人口普查局 ACS 2022"}},
                {"id": "c-legend", "type": "legend", "enabled": True},
                {"id": "c-grat", "type": "graticule", "enabled": True},
            ],
        },
    }


def evaluate_kind(mapspec, kind: str, *, purpose="publication", medium="print"):
    """Run the whole core pack and return (status, violations) for one kind."""
    from app.lib.cartography.standards.qa import evaluate_standards_qa

    profile = resolve_profile(purpose=purpose, medium=medium, audience="public")
    report = evaluate_standards_qa(mapspec, profile=profile)
    found = [
        (o["status"], o.get("reason", {})) for o in report.obligations
        if o["kind"] == kind
    ]
    assert len(found) == 1, f"kind {kind} not uniquely evaluated: {found}"
    status, reason = found[0]
    violations = [v.to_dict() for v in report.violations if v.kind == kind]
    return status, violations, reason, report


class TestRequiredComponents:
    def test_clean_map_satisfies(self):
        status, violations, _, _ = evaluate_kind(base_mapspec(), "required_components")
        assert status == "satisfied" and violations == []

    def test_missing_layout_flags_all_baselines(self):
        spec = base_mapspec()
        del spec["layout"]
        status, violations, _, _ = evaluate_kind(spec, "required_components")
        assert status == "violated"
        missing_types = {v["evidence"]["component_type"] for v in violations}
        assert {"title", "scale_bar", "north_arrow", "attribution"} <= missing_types
        for v in violations:
            assert v["fix_hint"]["route"] == "component_autofill"

    def test_placeholder_attribution_flagged(self):
        spec = base_mapspec()
        spec["layout"]["components"][3]["options"]["text"] = "数据来源：—（待补充）"
        status, violations, _, _ = evaluate_kind(spec, "required_components")
        # required_components still satisfied (component present); the
        # placeholder is source_disclosure's business
        assert status == "satisfied"


class TestSourceDisclosure:
    def test_placeholder_is_violation(self):
        spec = base_mapspec()
        spec["layout"]["components"][3]["options"]["text"] = "数据来源：—（待补充）"
        status, violations, _, _ = evaluate_kind(spec, "source_disclosure")
        assert status == "violated" and len(violations) == 1

    def test_real_source_satisfies(self):
        status, violations, _, _ = evaluate_kind(base_mapspec(), "source_disclosure")
        assert status == "satisfied" and violations == []

    def test_exploration_purpose_not_applicable(self):
        status, _, _, _ = evaluate_kind(
            base_mapspec(), "source_disclosure", purpose="exploration")
        assert status == "not_applicable"


class TestLegendPresent:
    def test_legend_spec_satisfies(self):
        status, _, _, _ = evaluate_kind(base_mapspec(), "legend_present")
        assert status == "satisfied"

    def test_thematic_without_any_legend_violates(self):
        spec = base_mapspec()
        del spec["layers"][0]["legend_spec"]
        spec["layers"][0]["paint"] = {
            "fill-color": {
                "property": "median_income", "method": "step",
                "stops": [[40000.0, "#ffeda0"], [80000.0, "#f03b20"]],
                "default": "#ffffcc",
            },
        }
        spec["layout"]["legend"] = {"visible": False}
        spec["layout"]["components"] = [
            c for c in spec["layout"]["components"] if c["type"] not in (
                "legend", "categorical_legend", "continuous_colorbar")
        ]
        status, violations, _, _ = evaluate_kind(spec, "legend_present")
        assert status == "violated" and len(violations) == 1

    def test_no_thematic_layer_not_applicable(self):
        spec = base_mapspec()
        spec["layers"][0] = {
            "id": "l-base", "source": "s1", "type": "line",
            "paint": {"line-color": "#888", "line-width": 1},
        }
        status, _, _, _ = evaluate_kind(spec, "legend_present")
        assert status == "not_applicable"


class TestLegendUnitDisclosure:
    def test_title_and_unit_satisfy(self):
        status, _, _, _ = evaluate_kind(base_mapspec(), "legend_unit_disclosure")
        assert status == "satisfied"

    def test_missing_unit_violates(self):
        spec = base_mapspec()
        del spec["layers"][0]["legend_spec"]["unit"]
        status, violations, _, _ = evaluate_kind(spec, "legend_unit_disclosure")
        assert status == "violated" and len(violations) == 1
        assert violations[0]["evidence"]["has_unit"] is False

    def test_categorical_legend_not_applicable(self):
        spec = base_mapspec()
        spec["layers"][0]["legend_spec"] = {
            "type": "categorical", "field": "state_name",
            "palette": "Set2", "title": "州", "unit": "—",
        }
        status, _, _, _ = evaluate_kind(spec, "legend_unit_disclosure")
        assert status == "not_applicable"


class TestCountVsRate:
    def test_count_field_violates(self):
        spec = base_mapspec()
        spec["layers"][0]["legend_spec"]["field"] = "population"
        spec["sources"]["s1"]["profile"]["fields"]["population"] = {"type": "number"}
        status, violations, _, _ = evaluate_kind(spec, "count_vs_rate")
        assert status == "violated" and len(violations) == 1
        assert "population" in violations[0]["message"]

    def test_rate_field_satisfies(self):
        spec = base_mapspec()
        spec["layers"][0]["legend_spec"]["field"] = "population_density"
        spec["sources"]["s1"]["profile"]["fields"]["population_density"] = {"type": "number"}
        status, violations, _, _ = evaluate_kind(spec, "count_vs_rate")
        assert status == "satisfied" and violations == []

    def test_missing_profile_not_evaluated(self):
        spec = base_mapspec()
        spec["layers"][0]["legend_spec"]["field"] = "population"
        del spec["sources"]["s1"]["profile"]
        status, _, reason, _ = evaluate_kind(spec, "count_vs_rate")
        assert status == "not_evaluated"

    def test_one_layer_missing_evidence_hides_no_other_violation(self):
        # l2's source lacks profile evidence; l1 is an evaluable count field.
        # The missing evidence must not mask l1's violation (fail-open hole),
        # and the report stays honest about the unevaluated layer.
        spec = base_mapspec()
        spec["sources"]["s2"] = {"type": "geojson", "ref": "ref:dataset-2"}
        spec["layers"].append({
            "id": "l2", "source": "s2", "type": "fill",
            "legend_spec": {
                "type": "graduated", "field": "total_cases",
                "palette": "Viridis", "method": "quantiles",
                "title": "病例总数", "unit": "例",
            },
        })
        spec["sources"]["s1"]["profile"]["fields"]["population"] = {"type": "number"}
        spec["layers"][0]["legend_spec"]["field"] = "population"
        status, violations, reason, _ = evaluate_kind(spec, "count_vs_rate")
        assert status == "violated"
        assert {v["layer_id"] for v in violations} == {"l1"}

    def test_two_evaluable_count_fields_both_violate(self):
        spec = base_mapspec()
        spec["sources"]["s1"]["profile"]["fields"]["population"] = {"type": "number"}
        spec["sources"]["s2"] = {
            "type": "geojson", "ref": "ref:dataset-2",
            "profile": {"fields": {"total_cases": {"type": "number"}}},
        }
        spec["layers"].append({
            "id": "l2", "source": "s2", "type": "fill",
            "legend_spec": {
                "type": "graduated", "field": "total_cases",
                "palette": "Viridis", "method": "quantiles",
                "title": "病例总数", "unit": "例",
            },
        })
        spec["layers"][0]["legend_spec"]["field"] = "population"
        status, violations, _, _ = evaluate_kind(spec, "count_vs_rate")
        assert status == "violated"
        assert {v["layer_id"] for v in violations} == {"l1", "l2"}


class TestCvdSafePalette:
    def test_verdict_matches_context_matrix_source_of_truth(self):
        spec = base_mapspec()
        palette = spec["layers"][0]["legend_spec"]["palette"]
        cells = [
            evaluate_cell(palette, f"cvd_{kind}", k=5)
            for kind in ("deuteranopia", "protanopia", "tritanopia")
        ]
        status, violations, _, report = evaluate_kind(spec, "cvd_safe_palette")
        any_fail = any(c["verdict"] == "fail" for c in cells)
        assert status == ("violated" if any_fail else "satisfied")
        if any_fail:
            assert violations, "matrix-failing palette must produce violation"
            assert violations[0]["evidence"]["engine"].startswith("context_matrix")

    def test_failing_palette_produces_violation(self):
        spec = base_mapspec()
        spec["layers"][0]["legend_spec"]["palette"] = "YlOrRd"
        cell = evaluate_cell("YlOrRd", "cvd_deuteranopia", k=5)
        status, violations, _, _ = evaluate_kind(spec, "cvd_safe_palette")
        if cell["verdict"] == "fail":
            assert status == "violated" and len(violations) == 1
            failing = violations[0]["evidence"]["cells"]
            assert any(c["verdict"] == "fail" for c in failing)
        else:
            assert status == "satisfied"

    def test_palette_without_evidence_not_evaluated(self):
        spec = base_mapspec()
        spec["layers"][0]["legend_spec"] = {"type": "graduated", "field": "median_income"}
        status, _, _, _ = evaluate_kind(spec, "cvd_safe_palette")
        assert status == "not_evaluated"


class TestPrintLegiblePalette:
    def test_only_binds_on_print_medium(self):
        status_screen, _, _, _ = evaluate_kind(
            base_mapspec(), "print_legible_palette", medium="screen")
        assert status_screen == "not_applicable"
        status_print, _, _, _ = evaluate_kind(
            base_mapspec(), "print_legible_palette", medium="print")
        assert status_print in ("satisfied", "violated", "not_evaluated")


class TestClassificationDeclared:
    def test_missing_method_violates(self):
        spec = base_mapspec()
        del spec["layers"][0]["legend_spec"]["method"]
        status, violations, _, _ = evaluate_kind(spec, "classification_declared")
        assert status == "violated" and len(violations) == 1

    def test_declared_method_satisfies(self):
        status, _, _, _ = evaluate_kind(base_mapspec(), "classification_declared")
        assert status == "satisfied"


class TestLabelDensity:
    def test_dense_point_layer_without_budget_violates(self):
        spec = base_mapspec()
        spec["sources"]["s1"]["profile"]["geometryTypes"] = ["Point"]
        spec["sources"]["s1"]["profile"]["featureCount"] = 5000
        spec["layers"][0] = {
            "id": "l-pts", "source": "s1", "type": "circle",
            "paint": {"circle-radius": 4},
        }
        status, violations, _, _ = evaluate_kind(
            spec, "label_density_declared", medium="screen")
        assert status == "violated" and len(violations) == 1
        assert violations[0]["evidence"]["feature_count"] == 5000

    def test_topn_declared_satisfies(self):
        spec = base_mapspec()
        spec["sources"]["s1"]["profile"]["geometryTypes"] = ["Point"]
        spec["sources"]["s1"]["profile"]["featureCount"] = 5000
        spec["layers"][0] = {
            "id": "l-pts", "source": "s1", "type": "circle",
            "paint": {"circle-radius": 4},
            "label": {"field": "state_name", "mode": "top_n", "topN": 40},
        }
        status, _, _, _ = evaluate_kind(spec, "label_density_declared", medium="screen")
        assert status == "satisfied"

    def test_sparse_layer_not_applicable(self):
        status, _, _, _ = evaluate_kind(
            base_mapspec(), "label_density_declared", medium="screen")
        assert status == "not_applicable"


class TestTimeDisclosure:
    def test_temporal_without_time_block_violates(self):
        spec = base_mapspec()
        spec["sources"]["s1"]["profile"]["hasTimeField"] = True
        status, violations, _, _ = evaluate_kind(spec, "time_disclosure")
        assert status == "violated" and len(violations) == 1
        assert "mapspec://time" in violations[0]["evidence"]["evidence_refs"]

    def test_time_block_satisfies(self):
        spec = base_mapspec()
        spec["sources"]["s1"]["profile"]["hasTimeField"] = True
        spec["time"] = {"start": "2022-01-01", "end": "2022-12-31"}
        status, _, _, _ = evaluate_kind(spec, "time_disclosure")
        assert status == "satisfied"

    def test_atemporal_not_applicable(self):
        status, _, _, _ = evaluate_kind(base_mapspec(), "time_disclosure")
        assert status == "not_applicable"


class TestUncertaintyDisclosure:
    def test_uncertainty_without_panel_violates(self):
        spec = base_mapspec()
        spec["sources"]["s1"]["profile"]["fields"]["margin_of_error"] = {"type": "number"}
        status, violations, _, _ = evaluate_kind(spec, "uncertainty_disclosure")
        assert status == "violated" and len(violations) == 1
        assert violations[0]["fix_hint"]["route"] == "component_autofill"

    def test_panel_satisfies(self):
        spec = base_mapspec()
        spec["sources"]["s1"]["profile"]["fields"]["margin_of_error"] = {"type": "number"}
        spec["layout"]["components"].append(
            {"id": "c-unc", "type": "uncertainty_panel", "enabled": True})
        status, _, _, _ = evaluate_kind(spec, "uncertainty_disclosure")
        assert status == "satisfied"


class TestThematicProfileDeclared:
    def test_undeclared_violates(self):
        spec = base_mapspec()
        del spec["cartographic_profile"]
        status, violations, _, _ = evaluate_kind(spec, "thematic_profile_declared")
        assert status == "violated" and len(violations) == 1

    def test_declared_satisfies(self):
        status, _, _, _ = evaluate_kind(base_mapspec(), "thematic_profile_declared")
        assert status == "satisfied"


class TestDependencyCascade:
    def test_failed_legend_blocks_dependents_as_not_evaluated(self):
        spec = base_mapspec()
        del spec["layers"][0]["legend_spec"]
        spec["layers"][0]["paint"] = {
            "fill-color": {
                "property": "median_income", "method": "step",
                "stops": [[40000.0, "#ffeda0"], [80000.0, "#f03b20"]],
                "default": "#ffffcc",
            },
        }
        spec["layout"]["legend"] = {"visible": False}
        spec["layout"]["components"] = [
            c for c in spec["layout"]["components"] if c["type"] not in (
                "legend", "categorical_legend", "continuous_colorbar")
        ]
        from app.lib.cartography.standards.qa import evaluate_standards_qa
        profile = resolve_profile(purpose="publication", medium="print")
        report = evaluate_standards_qa(spec, profile=profile)
        by_kind = {o["kind"]: o for o in report.obligations}
        assert by_kind["legend_present"]["status"] == "violated"
        for dependent in ("legend_unit_disclosure", "cvd_safe_palette",
                          "print_legible_palette", "classification_declared",
                          "count_vs_rate"):
            assert by_kind[dependent]["status"] == "not_evaluated", dependent
            assert "CORE.LEGEND_PRESENT" in str(
                by_kind[dependent].get("reason")), dependent
