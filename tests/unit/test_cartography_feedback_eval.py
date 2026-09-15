"""Hermetic unit tests for unified cartography feedback evaluation.

Covers:
- template/codegen evaluator (schema / compile / composition / reuse)
- unified three-axis feedback builder
- verdict injection projection of visual + template scores
"""
from __future__ import annotations

import json

from app.lib.cartography.verdict_summary import render_verdict_for_llm, should_inject_verdict
from app.lib.harness.cartography_feedback import (
    attach_unified_feedback,
    build_unified_feedback,
)
from app.lib.harness.evidence import CartographicReviewEvidence
from app.lib.harness.template_codegen_evaluator import (
    CHECK_COMPILE,
    CHECK_COMPOSITION,
    CHECK_REUSE,
    CHECK_SCHEMA,
    evaluate_template_codegen,
)


FP = "carto-sha256:feedback-eval-test"


def _minimal_mapspec(**overrides):
    spec = {
        "version": "1.0",
        "view": {"center": [116.4, 39.9], "zoom": 10},
        "sources": {
            "demo": {
                "type": "geojson",
                "data": {"type": "FeatureCollection", "features": []},
            }
        },
        "layers": [
            {
                "id": "demo-fill",
                "type": "fill",
                "source": "demo",
                "paint": {"fill-color": "#088"},
            }
        ],
        "layout": {"components": []},
    }
    spec.update(overrides)
    return spec


class TestTemplateCodegenEvaluator:
    def test_missing_mapspec_is_not_evaluated(self):
        report = evaluate_template_codegen(None)
        assert report.status == "not_evaluated"
        assert report.score is None
        assert report.evaluated is False
        assert report.reason == "missing_mapspec"

    def test_schema_fail_is_honest_fail(self):
        bad = {"version": "1.0", "layers": [{"id": 1}]}  # id must be str
        report = evaluate_template_codegen(bad, is_compiled=True)
        assert report.status == "fail"
        assert report.evaluated is True
        assert report.checks[CHECK_SCHEMA] == "fail"
        assert report.score is not None and report.score < 1.0

    def test_compile_false_fails(self):
        report = evaluate_template_codegen(_minimal_mapspec(), is_compiled=False)
        assert report.checks[CHECK_COMPILE] == "fail"
        assert report.status == "fail"

    def test_compile_missing_is_not_evaluated_subcheck(self):
        report = evaluate_template_codegen(_minimal_mapspec())
        assert report.checks[CHECK_COMPILE] == "not_evaluated"
        # No fail elsewhere with empty components → overall not_evaluated
        assert report.status == "not_evaluated"
        assert report.score is None

    def test_unknown_template_id_fails_reuse(self):
        spec = _minimal_mapspec()
        spec["layout"] = {
            "components": [
                {
                    "id": "n1",
                    "type": "north_arrow",
                    "enabled": True,
                    "position": "top-right",
                    "templateId": "does-not-exist/variant",
                }
            ]
        }
        report = evaluate_template_codegen(spec, is_compiled=True)
        assert report.checks[CHECK_REUSE] == "fail"
        assert report.status == "fail"

    def test_registered_template_reuse_passes_with_compile(self):
        from app.lib.cartography.component_templates import get_component_template_registry

        reg = get_component_template_registry()
        known = reg.all_ids[0]
        tmpl = reg.get(known)
        spec = _minimal_mapspec()
        spec["layout"] = {
            "components": [
                {
                    "id": "c1",
                    "type": tmpl.component_type,
                    "enabled": True,
                    "position": "top-right",
                    "templateId": known,
                    "variant": tmpl.variant,
                }
            ]
        }
        report = evaluate_template_codegen(
            spec,
            composition_template_id="",
            is_compiled=True,
        )
        assert report.checks[CHECK_SCHEMA] == "pass"
        assert report.checks[CHECK_COMPILE] == "pass"
        assert report.checks[CHECK_REUSE] == "pass"
        # composition may be NE (no template id) → overall NE (incomplete)
        assert report.checks[CHECK_COMPOSITION] in ("pass", "not_evaluated")
        if report.checks[CHECK_COMPOSITION] == "pass":
            assert report.status == "pass"
            assert report.score == 1.0
        else:
            assert report.status == "not_evaluated"

    def test_unknown_composition_template_fails(self):
        spec = _minimal_mapspec()
        spec["layout"] = {
            "components": [
                {
                    "id": "scale",
                    "type": "scale_bar",
                    "enabled": True,
                    "position": "bottom-right",
                }
            ]
        }
        report = evaluate_template_codegen(
            spec,
            composition_template_id="composition.does_not_exist",
            is_compiled=True,
        )
        assert report.checks[CHECK_COMPOSITION] == "fail"
        assert report.status == "fail"


class TestUnifiedFeedback:
    def test_three_axes_present(self):
        evidence = CartographicReviewEvidence(
            session_id="s1",
            desired_status="pass",
            status="passed",
            mapspec_fingerprint=FP,
        )
        evidence.visual_evidence.append({
            "source": "visual_judge",
            "status": "evaluated",
            "error_count": 0,
            "warning_count": 0,
            "critiques": [],
        })
        payload = attach_unified_feedback(
            evidence,
            _minimal_mapspec(),
            is_compiled=True,
        )
        assert set(payload["axes"]) == {"visual", "template_codegen", "gis_semantics"}
        assert payload["scores"]["visual"] == 1.0
        assert payload["scores"]["gis_semantics"] == 1.0
        assert evidence.feedback["version"] == 1
        # Check rows appended
        rules = [c["rule"] for c in evidence.checks]
        assert "TEMPLATE_CODEGEN_FITNESS" in rules
        assert "CARTOGRAPHY_FEEDBACK_AXES" in rules

    def test_visual_errors_fail_visual_axis(self):
        evidence = CartographicReviewEvidence(session_id="s1", desired_status="pass")
        evidence.visual_evidence.append({
            "source": "visual_judge",
            "status": "evaluated",
            "error_count": 2,
            "warning_count": 1,
            "critiques": [
                {"dimension": "readability", "severity": "error", "suggestion": "fix labels"},
            ],
        })
        fb = build_unified_feedback(evidence, None)
        assert fb.visual.status == "fail"
        assert fb.visual.score is not None and fb.visual.score < 1.0

    def test_missing_visual_is_not_evaluated(self):
        evidence = CartographicReviewEvidence(session_id="s1", desired_status="not_evaluated")
        fb = build_unified_feedback(evidence, None)
        assert fb.visual.status == "not_evaluated"
        assert fb.visual.score is None
        assert fb.gis_semantics.status == "not_evaluated"
        assert fb.template_codegen.status == "not_evaluated"
        assert fb.overall_status == "not_evaluated"

    def test_gis_semantics_fail_from_desired_status(self):
        evidence = CartographicReviewEvidence(
            session_id="s1",
            desired_status="fail",
            desired_review={
                "checks": [
                    {"rule": "SOURCE_LAYER_REF", "status": "fail", "message": "missing"},
                ]
            },
        )
        fb = build_unified_feedback(evidence, _minimal_mapspec(), is_compiled=True)
        assert fb.gis_semantics.status == "fail"
        assert fb.gis_semantics.score == 0.0


class TestVerdictInjectionFeedback:
    def _review(self, feedback=None, status="failed_repairable"):
        cartography = {
            "status": status,
            "termination_reason": "desired_quality_failed",
            "mapspec_fingerprint": FP,
            "desired_status": "fail",
            "runtime_status": "not_evaluated",
            "checks": [],
            "repair_attempts": [],
            "visual_evidence": [],
        }
        if feedback is not None:
            cartography["feedback"] = feedback
        return {
            "session_id": "s1",
            "cartography": cartography,
            "gate": {},
            "overall_passed": False,
        }

    def test_feedback_scores_projected_on_fail(self):
        feedback = {
            "version": 1,
            "overall_status": "fail",
            "overall_reason": "failed_axes:template_codegen",
            "scores": {"visual": 1.0, "template_codegen": 0.0, "gis_semantics": 0.0},
            "axes": {
                "visual": {
                    "status": "pass", "score": 1.0, "evaluated": True, "reason": "ok",
                },
                "template_codegen": {
                    "status": "fail", "score": 0.0, "evaluated": True,
                    "reason": "template_codegen_failed",
                },
                "gis_semantics": {
                    "status": "fail", "score": 0.0, "evaluated": True,
                    "reason": "desired_semantics_failed",
                },
            },
        }
        block = render_verdict_for_llm(self._review(feedback))
        assert block.startswith("[CARTOGRAPHY_VERDICT]")
        assert '"template_codegen"' in block
        assert '"visual"' in block
        # Parse the JSON body between marker lines
        lines = block.splitlines()
        body = json.loads(lines[1])
        assert body["feedback"]["scores"]["visual"] == 1.0
        assert body["feedback"]["scores"]["template_codegen"] == 0.0
        assert body["feedback"]["axes"]["template_codegen"]["status"] == "fail"

    def test_feedback_scores_projected_on_pass_token(self):
        """Agent must still see visual + template scores when overall token is pass."""
        feedback = {
            "version": 1,
            "overall_status": "pass",
            "overall_reason": "all_axes_passed",
            "scores": {"visual": 1.0, "template_codegen": 1.0, "gis_semantics": 1.0},
            "axes": {
                "visual": {
                    "status": "pass", "score": 1.0, "evaluated": True, "reason": "ok",
                },
                "template_codegen": {
                    "status": "pass", "score": 1.0, "evaluated": True, "reason": "ok",
                },
                "gis_semantics": {
                    "status": "pass", "score": 1.0, "evaluated": True, "reason": "ok",
                },
            },
        }
        review = self._review(feedback, status="passed")
        review["overall_passed"] = True
        assert should_inject_verdict(review, FP) is True
        block = render_verdict_for_llm(review)
        assert '"verdict": "pass"' in block
        body = json.loads(block.splitlines()[1])
        assert body["feedback"]["scores"]["visual"] == 1.0
        assert body["feedback"]["scores"]["template_codegen"] == 1.0

    def test_no_feedback_key_when_absent(self):
        block = render_verdict_for_llm(self._review(None))
        body = json.loads(block.splitlines()[1])
        assert "feedback" not in body


    def test_pass_token_forced_fail_when_feedback_overall_fail(self):
        """#1325: L4 pass + feedback.overall_status=fail must not inject pass."""
        feedback = {
            "version": 1,
            "overall_status": "fail",
            "overall_reason": "failed_axes:template_codegen",
            "scores": {"visual": 1.0, "template_codegen": 0.0, "gis_semantics": 1.0},
            "axes": {
                "visual": {
                    "status": "pass", "score": 1.0, "evaluated": True, "reason": "ok",
                },
                "template_codegen": {
                    "status": "fail", "score": 0.0, "evaluated": True,
                    "reason": "template_codegen_failed",
                },
                "gis_semantics": {
                    "status": "pass", "score": 1.0, "evaluated": True, "reason": "ok",
                },
            },
        }
        review = self._review(feedback, status="passed")
        review["overall_passed"] = True
        review["cartography"]["checks"] = [
            {
                "rule": "TEMPLATE_CODEGEN_FITNESS",
                "status": "fail",
                "message": "Template/codegen axis: fail (template_codegen_failed).",
            }
        ]
        assert should_inject_verdict(review, FP) is True
        block = render_verdict_for_llm(review)
        assert '"verdict": "pass"' not in block
        assert '"verdict": "fail"' in block
        assert "No corrective action needed." not in block
        assert "Plan a corrective webgis_* action." in block
        body = json.loads(block.splitlines()[1])
        assert body["verdict"] == "fail"
        assert body["feedback"]["overall_status"] == "fail"
        failed_rules = {c["rule"] for c in body.get("failed_checks", [])}
        assert "TEMPLATE_CODEGEN_FITNESS" in failed_rules
