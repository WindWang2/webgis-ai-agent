"""Regression tests for the adversarial-review findings (review round 1).

Each test is labeled with its finding id from
review/CARTOGRAPHIC_STANDARDS_RULE_GRAPH_REVIEW.md:
- P0-1  unmeasurable palette → not_evaluated, never satisfied
- P1-1  RULE_CONFLICT obeys the profile severity cap (legacy gate stays allow)
- P1-2  label_density: unknown featureCount → not_evaluated, not not_applicable
- P2-1  count_vs_rate: typeless {} field meta → not_evaluated
- P2-2  strictness keys on explicit purpose only (medium-only stays non-strict)
- P2-4  time block with enabled=False is not a disclosure
"""
from __future__ import annotations



from app.lib.cartography.standards.pack import StandardsPack
from app.lib.cartography.standards.profile import resolve_profile
from app.lib.cartography.standards.qa import (
    evaluate_standards_qa,
    standards_precompile_gate,
)
from app.lib.cartography.standards.rule import CartographicRule, RuleApplicability
from tests.cartography.test_standards_evaluate_v1 import base_mapspec


def _obligation_status(report, kind: str) -> str:
    found = [o["status"] for o in report.obligations if o["kind"] == kind]
    assert len(found) == 1
    return found[0]


def _evaluate(spec, profile):
    return evaluate_standards_qa(spec, profile=profile)


class TestP0PaletteUnavailable:
    def test_unknown_palette_name_not_evaluated(self):
        spec = base_mapspec()
        spec["layers"][0]["legend_spec"]["palette"] = "Definitely_Not_A_Palette"
        report = _evaluate(spec, resolve_profile(purpose="publication", medium="print"))
        assert _obligation_status(report, "cvd_safe_palette") == "not_evaluated"
        assert _obligation_status(report, "print_legible_palette") == "not_evaluated"

    def test_unparseable_raw_colors_not_evaluated(self):
        spec = base_mapspec()
        legend = spec["layers"][0]["legend_spec"]
        del legend["palette"]
        legend["palette_colors"] = ["rgb(1,2)", "not-a-color", "#000000"]
        report = _evaluate(spec, resolve_profile(purpose="publication", medium="print"))
        assert _obligation_status(report, "cvd_safe_palette") == "not_evaluated"
        assert _obligation_status(report, "print_legible_palette") == "not_evaluated"

    def test_failing_layer_not_masked_by_unavailable_layer(self):
        spec = base_mapspec()
        spec["sources"]["s2"] = {"type": "geojson", "ref": "ref:dataset-2"}
        spec["layers"].append({
            "id": "l2", "source": "s2", "type": "fill",
            "legend_spec": {
                "type": "graduated", "field": "median_income",
                "palette": "Ghost_Palette", "method": "quantiles",
                "title": "t", "unit": "u",
            },
        })
        spec["layers"][0]["legend_spec"]["palette"] = "YlOrRd"
        status = _obligation_status(
            _evaluate(spec, resolve_profile(purpose="publication", medium="print")),
            "cvd_safe_palette")
        assert status == "violated"
        report = _evaluate(spec, resolve_profile(purpose="publication", medium="print"))
        violated_layers = {v.layer_id for v in report.violations if v.kind == "cvd_safe_palette"}
        assert violated_layers == {"l1"}


class TestP1ConflictSeverityCap:
    @staticmethod
    def _conflicting_pack():
        return StandardsPack.build(
            pack_id="conflicty", version="0.1.0",
            rules=(
                CartographicRule(
                    rule_id="T.A", kind="legend_present", severity="warning",
                    message="a",
                    applies_when=RuleApplicability(), conflicts_with=("T.B",)),
                CartographicRule(
                    rule_id="T.B", kind="thematic_profile_declared",
                    severity="warning", message="b", applies_when=RuleApplicability()),
            ),
        )

    @staticmethod
    def _conflict_map():
        spec = base_mapspec()
        del spec["cartographic_profile"]
        del spec["layers"][0]["legend_spec"]
        spec["layers"][0]["paint"] = {
            "fill-color": {"property": "median_income", "method": "step",
                           "stops": [[40000.0, "#ffeda0"], [80000.0, "#f03b20"]],
                           "default": "#ffffcc"},
        }
        spec["layout"]["legend"] = {"visible": False}
        spec["layout"]["components"] = [
            c for c in spec["layout"]["components"] if c["type"] != "legend"]
        return spec

    def test_inferred_profile_never_blocks_on_conflict(self):
        gate = standards_precompile_gate(
            self._conflict_map(), pack=self._conflicting_pack())
        assert gate["verdict"] == "allow"
        conflicts = [v for v in gate["report"]["violations"]
                     if v["rule_id"] == "RULE_CONFLICT"]
        assert conflicts and conflicts[0]["severity"] == "warning"

    def test_explicit_profile_escalates_conflict(self):
        gate = standards_precompile_gate(
            self._conflict_map(),
            profile=resolve_profile(purpose="publication", medium="print"),
            pack=self._conflicting_pack(),
        )
        blocking_ids = {v["rule_id"] for v in gate["blocking"]}
        assert "RULE_CONFLICT" in blocking_ids


class TestP1LabelDensityUnknownCount:
    def test_missing_feature_count_not_evaluated(self):
        spec = base_mapspec()
        spec["sources"]["s1"]["profile"].pop("featureCount")
        spec["sources"]["s1"]["profile"]["geometryTypes"] = ["Point"]
        spec["layers"][0] = {
            "id": "l-pts", "source": "s1", "type": "circle",
            "paint": {"circle-radius": 4},
        }
        report = _evaluate(spec, resolve_profile(purpose="analysis", medium="screen"))
        assert _obligation_status(report, "label_density_declared") == "not_evaluated"


class TestP2TypelessFieldMeta:
    def test_typeless_meta_not_evaluated(self):
        spec = base_mapspec()
        spec["layers"][0]["legend_spec"]["field"] = "population"
        spec["sources"]["s1"]["profile"]["fields"]["population"] = {}
        report = _evaluate(spec, resolve_profile(purpose="publication"))
        assert _obligation_status(report, "count_vs_rate") == "not_evaluated"


class TestP2StrictKeysOnPurpose:
    def test_medium_only_profile_is_non_strict(self):
        profile = resolve_profile(medium="print")
        assert profile.strict is False
        assert profile.medium == "print"

    def test_service_single_axis_gate_never_blocks_legacy(self):
        spec = base_mapspec()
        del spec["layout"]
        del spec["cartographic_profile"]
        gate = standards_precompile_gate(
            spec, profile=resolve_profile(medium="print"))
        assert gate["verdict"] == "allow"


class TestP2TimeEnabledFalse:
    def test_disabled_time_block_is_not_disclosure(self):
        spec = base_mapspec()
        spec["sources"]["s1"]["profile"]["hasTimeField"] = True
        spec["time"] = {"enabled": False}
        report = _evaluate(spec, resolve_profile(purpose="publication"))
        assert _obligation_status(report, "time_disclosure") == "violated"


class TestP2MirrorProbeAnchor:
    def test_probe_set_matches_declared_operations(self):
        from app.lib.cartography.standards.rule import QUALITY_LOOP_OPERATIONS

        # the behavioral probe table in test_standards_contract_v1 must cover
        # exactly the declared mirror — no phantom ops, none missing
        from tests.cartography.test_standards_contract_v1 import (
            _auto_safe_probes,
        )

        assert set(_auto_safe_probes()) == set(QUALITY_LOOP_OPERATIONS)
