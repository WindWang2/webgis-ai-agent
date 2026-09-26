"""SemanticInputs 统一推导契约测试（F10 M7，design D1）.

锁定：11→8 冻结投影完备性、判定优先级（explicit > dataset contract >
值证据）、带符号率升级、uncertainty 无色族、保守降级、确定性。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.cartography.semantic_inputs import (  # noqa: E402
    MEASUREMENT_KIND_TO_GRAMMAR,
    SEMANTIC_INPUTS_VERSION,
    SemanticInputs,
    derive_semantic_inputs,
    project_measurement_kind,
)
from app.lib.cartography.visual_variables import (  # noqa: E402
    MEASUREMENT_KINDS,
)
from app.lib.gis.measurement import (  # noqa: E402
    MeasurementKind,
    derive_field_semantics,
)

SIGNED_VALUES = [-500.0, -300.0, -100.0, -50.0, 10.0, 80.0,
                 200.0, 400.0, 600.0, 900.0, -800.0, -200.0]


class TestProjection:
    def test_projection_covers_full_11_vocab(self):
        assert set(MEASUREMENT_KIND_TO_GRAMMAR) == {
            k.value for k in MeasurementKind}

    def test_projection_values_in_grammar_vocab(self):
        for projected in MEASUREMENT_KIND_TO_GRAMMAR.values():
            assert projected in MEASUREMENT_KINDS

    def test_projection_key_semantics(self):
        assert MEASUREMENT_KIND_TO_GRAMMAR["count"] == "ratio"
        assert MEASUREMENT_KIND_TO_GRAMMAR["absolute_quantity"] == "ratio"
        assert MEASUREMENT_KIND_TO_GRAMMAR["density"] == "rate"
        assert MEASUREMENT_KIND_TO_GRAMMAR["percentage"] == "rate"
        assert MEASUREMENT_KIND_TO_GRAMMAR["index"] == "quantitative"
        assert MEASUREMENT_KIND_TO_GRAMMAR["category"] == "nominal"
        assert MEASUREMENT_KIND_TO_GRAMMAR["signed_change"] == "signed_change"
        assert MEASUREMENT_KIND_TO_GRAMMAR["uncertainty"] == "uncertainty"

    def test_unknown_kind_fail_closed(self):
        with pytest.raises(ValueError):
            project_measurement_kind("definitely_not_a_kind")


class TestPriority:
    def test_explicit_pin_wins(self):
        out = derive_semantic_inputs(
            "population", value_samples=[1.0, 2.0],
            explicit_measurement="signed_change")
        assert out.source == "explicit"
        assert out.measurement_kind == "signed_change"
        assert out.data_kind == "diverging"
        assert out.reason_codes == ["GRAMMAR.MEAS.EXPLICIT"]

    def test_explicit_invalid_vocab_fail_closed(self):
        with pytest.raises(ValueError):
            derive_semantic_inputs("f", explicit_measurement="bogus")

    def test_dataset_contract_priority(self):
        # 画像判 count（#1488 11 词表），值证据低基数整数本会判 ordinal——
        # dataset contract 优先（count → ratio → sequential）。
        fs = derive_field_semantics(
            "n_poi", ["count_measure"],
            value_samples=[1, 2, 3, 4, 5])
        assert fs.measurement_kind == MeasurementKind.COUNT.value
        out = derive_semantic_inputs(
            "n_poi", value_samples=[1, 2, 3, 4, 5], dtype="int",
            profile_semantics=fs)
        assert out.source == "dataset_contract"
        assert out.measurement_kind == "ratio"
        assert out.data_kind == "sequential"
        assert out.contract_measurement_kind == "count"

    def test_value_evidence_fills_when_contract_empty(self):
        fs = derive_field_semantics("some_field", [], value_samples=[])
        assert fs.measurement_kind == ""
        out = derive_semantic_inputs(
            "some_field", value_samples=[1, 2, 3], dtype="int",
            profile_semantics=fs)
        assert out.source in ("evidence", "fallback")

    def test_roles_derive_contract_in_place(self):
        # 未传 profile_semantics 但给 roles → 就地派生一次画像再投影。
        out = derive_semantic_inputs(
            "n_school", roles=["count_measure"],
            value_samples=[3, 7, 12])
        assert out.source == "dataset_contract"
        assert out.measurement_kind == "ratio"


class TestSignedSemantics:
    def test_signed_value_evidence_diverging(self):
        out = derive_semantic_inputs(
            "balance", value_samples=SIGNED_VALUES, dtype="float")
        assert out.measurement_kind == "signed_change"
        assert out.data_kind == "diverging"
        # 值证据 signed_change 回填 ADR-0207 词表（resolver 置 diverging center）
        assert out.contract_measurement_kind == "signed_change"

    def test_signed_rate_escalation(self):
        # 画像判 rate（#1488 名称证据；率名不得含 signed 词素——含了会由
        # #1488 直接判 signed_change）+ 样本双符号（负侧 ≥5%）→ 适配层
        # 升级 signed_change。纯值路径（无画像）由 #1480 值证据先行。
        vals = [-0.10, 0.20, 0.30, -0.40, 0.10, -0.05,
                0.25, 0.05, -0.30, 0.15, -0.02, 0.08]
        fs = derive_field_semantics("success_rate", [], value_samples=vals)
        assert fs.measurement_kind == MeasurementKind.RATE.value
        out = derive_semantic_inputs("success_rate", value_samples=vals,
                                     dtype="float", profile_semantics=fs)
        assert out.measurement_kind == "signed_change"
        assert out.data_kind == "diverging"
        assert "GRAMMAR.MEAS.SIGNED_RATE_ESCALATION" in out.reason_codes
        assert out.disclosures

    def test_minority_negative_not_signed(self):
        # 负值占比 < 5%：疑哨兵值，不升级。
        vals = [10.0] * 30 + [-1.0]
        out = derive_semantic_inputs("population", value_samples=vals,
                                     dtype="float")
        assert out.measurement_kind != "signed_change"


class TestHonestSemantics:
    def test_uncertainty_no_color_family(self):
        out = derive_semantic_inputs("std_error",
                                     value_samples=[0.1, 0.2, 0.3],
                                     dtype="float")
        assert out.measurement_kind == "uncertainty"
        assert out.data_kind is None
        assert "GRAMMAR.MEAS.UNCERTAINTY_NO_COLOR_FAMILY" in out.reason_codes

    def test_dtype_nominal(self):
        out = derive_semantic_inputs("landuse", value_samples=["urban"],
                                     dtype="string")
        assert out.measurement_kind == "nominal"
        assert out.data_kind == "qualitative"

    def test_insufficient_evidence_check(self):
        out = derive_semantic_inputs("unnamed_metric", value_samples=[],
                                     dtype="number")
        assert out.source == "fallback"
        assert any(
            c["code"] == "GRAMMAR.MEAS.INSUFFICIENT_EVIDENCE"
            for c in out.checks)

    def test_cyclic_temporal_path_preserved(self):
        # temporal + 周期词素 → cyclic（derive_data_kind 单点路径保持）。
        out = derive_semantic_inputs("month", value_samples=[1.0, 2.0, 3.0],
                                     dtype="float")
        assert out.measurement_kind == "temporal"
        assert out.data_kind == "cyclic"


class TestDeterminismAndBounds:
    def test_deterministic(self):
        a = derive_semantic_inputs("x", value_samples=SIGNED_VALUES,
                                   dtype="float")
        b = derive_semantic_inputs("x", value_samples=SIGNED_VALUES,
                                   dtype="float")
        assert a.to_bounded_dict() == b.to_bounded_dict()

    def test_bounded_dict_shape(self):
        out = derive_semantic_inputs("x", value_samples=[1.0])
        d = out.to_bounded_dict()
        assert d["version"] == SEMANTIC_INPUTS_VERSION
        for key in ("field", "measurement_kind", "data_kind", "source",
                    "confidence", "contract_measurement_kind"):
            assert key in d
        assert len(d["evidence"]) <= 6
        assert len(d["checks"]) <= 6

    def test_serialize_roundtrip_via_json(self):
        import json
        out = derive_semantic_inputs("x", value_samples=SIGNED_VALUES,
                                     dtype="float")
        d = json.loads(json.dumps(out.to_bounded_dict(), ensure_ascii=False))
        assert SemanticInputs.model_validate({
            "field": d["field"], "measurement_kind": d["measurement_kind"],
            "data_kind": d["data_kind"], "source": d["source"],
        }).measurement_kind == "signed_change"
