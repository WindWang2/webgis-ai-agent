"""Standards QA — gate semantics, back-compat, determinism, pi_card (v1)."""
from __future__ import annotations

import copy
from typing import Any, Dict

import pytest

from app.lib.cartography.standards.packs import get_core_pack
from app.lib.cartography.standards.profile import ProfileSpecError, resolve_profile
from app.lib.cartography.standards.qa import (
    evaluate_standards_qa,
    standards_precompile_gate,
)
from tests.cartography.test_standards_evaluate_v1 import base_mapspec


LEGACY_MAP: Dict[str, Any] = {
    "version": "1.0",
    "sources": {"s1": {"type": "geojson", "url": "https://example.test/data.geojson"}},
    "layers": [{"id": "l1", "source": "s1", "type": "fill",
                "paint": {"fill-color": "#345", "fill-opacity": 0.8}}],
}


class TestDeterminism:
    def test_same_input_byte_identical_report(self):
        spec = base_mapspec()
        profile = resolve_profile(purpose="publication", medium="print")
        first = evaluate_standards_qa(copy.deepcopy(spec), profile=profile).to_dict()
        second = evaluate_standards_qa(copy.deepcopy(spec), profile=profile).to_dict()
        assert first == second

    def test_report_is_json_clean(self):
        import json

        spec = base_mapspec()
        profile = resolve_profile(purpose="publication", medium="print")
        payload = json.dumps(
            evaluate_standards_qa(spec, profile=profile).to_dict(),
            ensure_ascii=False, sort_keys=True, allow_nan=False,
        )
        assert "standards_qa" in payload


class TestGateSemantics:
    def test_explicit_strict_blocks_on_error_obligations(self):
        spec = base_mapspec()
        del spec["layout"]  # required components missing → error obligation
        gate = standards_precompile_gate(spec, profile=resolve_profile(
            purpose="publication", medium="print"))
        assert gate["verdict"] == "block"
        assert gate["blocking"], "error-severity violations must be listed"
        assert all(v["severity"] == "error" for v in gate["blocking"])

    def test_inferred_profile_caps_errors_to_warning(self):
        spec = base_mapspec()
        del spec["layout"]
        del spec["cartographic_profile"]  # force inferred (non-strict)
        gate = standards_precompile_gate(spec)
        assert gate["verdict"] == "allow"
        error_violations = [
            v for v in gate["report"]["violations"] if v["severity"] == "error"]
        assert error_violations == []
        warnings = [
            v for v in gate["report"]["violations"] if v["severity"] == "warning"]
        assert warnings, "capped obligations stay visible as warnings"

    def test_strict_requires_explicit_profile(self):
        with pytest.raises(ProfileSpecError):
            standards_precompile_gate(base_mapspec(), strict=True)

    def test_disabled_is_explicit_and_empty(self):
        report = evaluate_standards_qa(base_mapspec(), enabled=False)
        assert report.status == "disabled"
        assert report.obligations == () and report.violations == ()
        gate = standards_precompile_gate(base_mapspec(), enabled=False)
        assert gate["verdict"] == "disabled"


class TestBackwardCompat:
    def test_legacy_map_gates_allow_and_never_errors(self):
        gate = standards_precompile_gate(copy.deepcopy(LEGACY_MAP))
        assert gate["verdict"] == "allow"
        assert all(
            v["severity"] != "error" for v in gate["report"]["violations"])

    def test_legacy_map_profile_is_inferred(self):
        report = evaluate_standards_qa(copy.deepcopy(LEGACY_MAP))
        assert report.profile.source == "inferred"
        assert report.profile.strict is False

    def test_feature_off_matches_pass_through(self):
        enabled = evaluate_standards_qa(base_mapspec(), enabled=True)
        disabled = evaluate_standards_qa(base_mapspec(), enabled=False)
        assert disabled.status == "disabled"
        assert enabled.status != "disabled"


class TestReportShape:
    def test_obligations_are_explainable_records(self):
        report = evaluate_standards_qa(
            base_mapspec(), profile=resolve_profile(purpose="publication"))
        for obligation in report.obligations:
            assert {"rule_id", "kind", "severity", "status", "message"} <= set(obligation)
            assert obligation["status"] in (
                "satisfied", "violated", "not_evaluated", "not_applicable")

    def test_status_ladder(self):
        clean = evaluate_standards_qa(
            base_mapspec(), profile=resolve_profile(purpose="publication"))
        assert clean.status == "pass"
        broken = base_mapspec()
        broken["layers"][0]["legend_spec"]["field"] = "population"
        broken["sources"]["s1"]["profile"]["fields"]["population"] = {"type": "number"}
        report = evaluate_standards_qa(
            broken, profile=resolve_profile(purpose="publication"))
        assert report.status == "violations"

    def test_violations_carry_rule_ids_and_evidence_refs(self):
        spec = base_mapspec()
        del spec["layout"]
        report = evaluate_standards_qa(
            spec, profile=resolve_profile(purpose="publication", medium="print"))
        assert report.violations
        for violation in report.violations:
            assert violation.rule_id.startswith("CORE.")
            refs = violation.evidence.get("evidence_refs")
            assert isinstance(refs, list) and refs

    def test_pack_identity_pinned_in_report(self):
        report = evaluate_standards_qa(base_mapspec())
        assert report.pack.pack_id == "core"
        assert report.pack.fingerprint == get_core_pack().fingerprint
        assert report.to_dict()["pack"]["fingerprint"].startswith("stdpack-sha256:")


class TestPiCard:
    def test_card_is_bounded(self):
        spec = base_mapspec()
        del spec["layout"]
        report = evaluate_standards_qa(
            spec, profile=resolve_profile(purpose="publication", medium="print"))
        card = report.pi_card
        assert card["card"] == "standards_qa"
        assert len(card["top_violations"]) <= card["bounds"]["top_violations"]
        for entry in card["top_violations"]:
            assert len(entry["message"]) <= card["bounds"]["message_chars"]
        assert set(card["counters"]) == {
            "satisfied", "violated", "not_evaluated", "not_applicable"}


class TestCustomPackResolution:
    def test_explicit_pack_overrides_registry(self):
        from app.lib.cartography.standards.pack import StandardsPack
        from app.lib.cartography.standards.rule import CartographicRule

        minimal = StandardsPack.build(
            pack_id="minimal", version="0.1.0",
            rules=(CartographicRule(
                rule_id="MIN.ONLY", kind="legend_present", severity="warning",
                message="图例检查", applies_when=__import__(
                    "app.lib.cartography.standards.rule",
                    fromlist=["RuleApplicability"]).RuleApplicability(),
            ),),
        )
        report = evaluate_standards_qa(base_mapspec(), pack=minimal)
        assert [o["rule_id"] for o in report.obligations] == ["MIN.ONLY"]
        assert report.pack.fingerprint == minimal.fingerprint

    def test_unknown_pack_version_fails_closed(self):
        from app.lib.cartography.standards.pack import StandardsPackError

        with pytest.raises(StandardsPackError):
            evaluate_standards_qa(base_mapspec(), pack_id="core", pack_version="9.9.9")
