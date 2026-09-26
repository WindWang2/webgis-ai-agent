"""Grammar golden corpus harness（F10 M6）.

驱动 ``golden_corpus.grammar_decisions`` 的声明式用例矩阵，逐案断言：
确定性（双解 model_dump 逐字节相等）、reason codes、表达选择、collapse
spec、图例配对、尺度带候选、user-wins 记账，以及关联 resolve_symbology
的色带族/center 证据——**不是只检查 palette 名字**。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.cartography.grammar_types import (  # noqa: E402
    GRAMMAR_VERSION,
    MAX_CATEGORICAL_CLASSES,
    REPRESENTATION_MODEL_IDS,
)
from app.lib.cartography.grammar_solver import (  # noqa: E402
    solve_grammar,
)
from app.lib.cartography.model_library import get_map_model  # noqa: E402
from app.lib.cartography.symbology import (  # noqa: E402
    symbology_decision_from_values,
)
from tests.cartography.golden_corpus.grammar_decisions import (  # noqa: E402
    GRAMMAR_CORPUS,
    MULTISCALE_CASES,
    build_request_for_multiscale,
)


def _assert_expectations(decision, expect: dict) -> None:
    if "data_kind" in expect:
        assert decision.data_kind == expect["data_kind"]
    if "measurement_kind" in expect:
        primary = next(b for b in decision.bindings if b.role == "primary")
        assert primary.measurement.kind == expect["measurement_kind"]
    if "representation_selected" in expect:
        assert decision.representation.selected == (
            expect["representation_selected"])
    for code in expect.get("reason_codes_contain", []):
        assert code in decision.reason_codes, (code, decision.reason_codes)
    for text in expect.get("disclosures_contain", []):
        assert any(text in d for d in decision.disclosures), (
            text, decision.disclosures)
    if "legend_form" in expect:
        assert decision.legend.form == expect["legend_form"]
    collapse = expect.get("collapse")
    if collapse:
        rep_collapse = decision.representation.collapse
        assert rep_collapse is not None
        for key, value in collapse.items():
            assert rep_collapse[key] == value, (key, rep_collapse)
    if expect.get("collapse_absent"):
        assert decision.representation.collapse is None
    if "scale_tier" in expect:
        assert decision.scale.tier == expect["scale_tier"]
    if "is_dense_points" in expect:
        assert decision.scale.is_dense_points is expect["is_dense_points"]
    for model_id, code in expect.get("rejected_representations", {}).items():
        match = next((r for r in decision.representation.rejected
                      if r.get("model_id") == model_id), None)
        assert match is not None, (model_id, decision.representation.rejected)
        assert match.get("reason_code") == code
    if "point_candidates_first" in expect:
        assert decision.scale.point_candidates[0] == (
            expect["point_candidates_first"])
    if "secondary_binding_measurement" in expect:
        secondary = [b for b in decision.bindings if b.role != "primary"]
        assert any(
            b.measurement.kind == expect["secondary_binding_measurement"]
            for b in secondary)
    if "user_wins_kinds" in expect:
        kinds = {w["kind"] for w in decision.user_wins}
        for kind in expect["user_wins_kinds"]:
            assert kind in kinds
    if "binding_variable" in expect:
        primary = next(b for b in decision.bindings if b.role == "primary")
        assert primary.variable == expect["binding_variable"]
    if expect.get("representations_in_registry_vocab"):
        assert decision.representation.selected in REPRESENTATION_MODEL_IDS


def _assert_symbology(spec: dict) -> None:
    payload = spec["input"]
    decision = symbology_decision_from_values(
        payload["values"],
        data_kind=payload.get("data_kind", "sequential"),
        measurement_kind=payload.get("measurement_kind"),
    )
    if spec.get("palette_family_diverging"):
        assert decision.palette in ("RdBu", "PuOr", "RdYlGn", "BrBG", "Spectral",
                                    "PiYG", "PRGn", "PuOr_diverging"), (
            decision.palette, decision.reasons)
        assert any("语义证据" in r or "measurement_kind" in r
                   for r in decision.reasons)
    else:
        assert decision.palette not in ("RdBu", "PuOr", "RdYlGn"), (
            decision.palette, decision.reasons)
    if "diverging_center" in spec:
        assert decision.diverging_center == spec["diverging_center"]


@pytest.mark.parametrize("case_id", sorted(GRAMMAR_CORPUS))
def test_grammar_corpus_case(case_id: str):
    case = GRAMMAR_CORPUS[case_id]
    decision = solve_grammar(case["request"])
    # 版本钉：全部决策工件带当前 grammar 版本。
    assert decision.grammar_version == GRAMMAR_VERSION
    _assert_expectations(decision, case["expect"])
    if "symbology" in case["expect"]:
        _assert_symbology(case["expect"]["symbology"])


@pytest.mark.parametrize("case_id", sorted(GRAMMAR_CORPUS))
def test_grammar_corpus_determinism(case_id: str):
    """同输入两次求解 model_dump() 逐字节相等（确定性契约）。"""
    case = GRAMMAR_CORPUS[case_id]
    a = solve_grammar(case["request"]).model_dump()
    b = solve_grammar(case["request"]).model_dump()
    assert a == b


@pytest.mark.parametrize("case_id", sorted(GRAMMAR_CORPUS))
def test_grammar_corpus_fingerprint_stable(case_id: str):
    case = GRAMMAR_CORPUS[case_id]
    a = solve_grammar(case["request"]).fingerprint
    b = solve_grammar(case["request"]).fingerprint
    assert a == b and len(a) == 32


class TestMultiscaleCorpus:
    @pytest.mark.parametrize("case", MULTISCALE_CASES,
                             ids=[c["tier"] for c in MULTISCALE_CASES])
    def test_tier_bands_and_dense_thresholds(self, case):
        dense = solve_grammar(
            build_request_for_multiscale(case["zoom"], case["dense_at"]))
        sparse = solve_grammar(
            build_request_for_multiscale(case["zoom"], case["sparse_at"]))
        assert dense.scale.tier == case["tier"]
        assert dense.scale.is_dense_points is True
        assert sparse.scale.is_dense_points is False
        assert "GRAMMAR.SCALE.DENSE_POINTS_AGGREGATE" in dense.reason_codes
        assert "GRAMMAR.SCALE.SPARSE_POINTS_RAW" in sparse.reason_codes

    def test_tier_candidates_differ_by_band(self):
        seen = {}
        for case in MULTISCALE_CASES:
            decision = solve_grammar(
                build_request_for_multiscale(case["zoom"], 9000))
            seen[case["tier"]] = tuple(decision.scale.point_candidates)
        # world 首选热力，street 只聚簇/原始——分带动作表真实生效。
        assert seen["world"][0] == "visual_heatmap"
        assert seen["street"] == ("point_cluster", "point_overlay")
        assert seen["world"] != seen["street"]


class TestCorpusInvariants:
    def test_case_ids_cover_direction_requirements(self):
        # 方向任务书要求的验收面在场。
        required = {
            "signed_change",          # signed
            "rate_normalized",        # rate vs count（2a/2b）
            "count_advisory",
            "nominal_many",           # nominal categories
            "dense_points",           # 密集点
            "low_n",                  # 低 N
            "bivariate_with_uncertainty",  # 双变量
        }
        missing = required - set(GRAMMAR_CORPUS)
        assert not missing, missing

    def test_collapsed_observed_classes_exceed_palette_capacity(self):
        case = GRAMMAR_CORPUS["nominal_many"]
        assert (case["expect"]["collapse"]["observed_classes"]
                > MAX_CATEGORICAL_CLASSES)

    def test_all_representation_ids_resolve_in_model_library(self):
        for case_id, case in GRAMMAR_CORPUS.items():
            selected = case["expect"].get("representation_selected")
            if selected:
                assert get_map_model(selected) is not None, case_id
