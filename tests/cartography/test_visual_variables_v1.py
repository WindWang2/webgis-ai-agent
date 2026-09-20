"""Visual Variable Grammar 单元测试（C1，ADR-0204）。

覆盖：适配矩阵三分集完备、runtime 门槛（texture/orientation 不分配）、
standards DATA_SEMANTICS 单向投影、测量语义推断（词素边界安全 + 值证据
次序）、data_kind 推导单点、确定性。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.cartography.standards.rule import DATA_SEMANTICS  # noqa: E402
from app.lib.cartography.symbology import DataKind  # noqa: E402
from app.lib.cartography.visual_variables import (  # noqa: E402
    CHANNEL_FIT,
    CHANNEL_RUNTIME_STATUS,
    MEASUREMENT_KINDS,
    VISUAL_VARIABLES,
    allocatable,
    channel_fit,
    channel_fit_order,
    derive_data_kind,
    infer_measurement_kind,
    project_to_data_semantics,
)


class TestChannelFitMatrix:
    def test_declared_order_preserved(self):
        """评审修正回归：求解序必须保持矩阵声明序（制图学优先序），
        不得按变量名字典序重排——quantitative 的 preferred 首位是 position。"""
        order = channel_fit_order("quantitative")
        assert order["preferred"] == ("position", "size", "lightness")
        assert order["rejected"] == ("shape", "texture", "orientation")
        assert channel_fit_order("rate")["preferred"] == ("lightness", "position")

    def test_channel_fit_order_unknown_kind_fail_closed(self):
        with pytest.raises(ValueError):
            channel_fit_order("bogus")

    def test_partition_complete_for_every_measurement(self):
        for kind in MEASUREMENT_KINDS:
            covered: list[str] = []
            for level in ("preferred", "allowed", "rejected"):
                covered.extend(
                    fit.variable for fit in CHANNEL_FIT[kind].values()
                    if fit.level == level
                )
            assert sorted(covered) == sorted(VISUAL_VARIABLES), kind

    def test_stable_reason_codes(self):
        fit = channel_fit("nominal", "size")
        assert fit.level == "rejected"
        assert fit.reason_code == "GRAMMAR.CHAN.SIZE_NOMINAL_REJECTED"
        assert fit.reason  # 关键拒绝格有制图学理由

    def test_runtime_unsupported_channels_not_allocatable(self):
        # 任务书口径：texture 若 runtime 支持；现状不支持 → grammar 永不分配
        assert CHANNEL_RUNTIME_STATUS["texture"] == "unsupported"
        assert not allocatable("texture")
        assert not allocatable("orientation")  # partial → 不可分配
        assert allocatable("hue") and allocatable("opacity")

    def test_nominal_prefers_hue_shape_rejects_ordered_channels(self):
        assert channel_fit("nominal", "hue").level == "preferred"
        assert channel_fit("nominal", "shape").level == "preferred"
        for ordered in ("lightness", "saturation", "size", "opacity"):
            assert channel_fit("nominal", ordered).level == "rejected", ordered

    def test_signed_change_rejects_size_sign_loss(self):
        fit = channel_fit("signed_change", "size")
        assert fit.level == "rejected"
        assert "正负号" in fit.reason

    def test_uncertainty_prefers_opacity_texture(self):
        assert channel_fit("uncertainty", "opacity").level == "preferred"
        assert channel_fit("uncertainty", "hue").level == "rejected"

    def test_unknown_vocab_fail_closed(self):
        with pytest.raises(ValueError):
            channel_fit("bogus", "hue")
        with pytest.raises(ValueError):
            channel_fit("nominal", "bogus")


class TestProjectionToStandards:
    def test_all_projections_within_frozen_vocabulary(self):
        for kind in MEASUREMENT_KINDS:
            projected = project_to_data_semantics(kind)
            assert projected, kind
            for item in projected:
                assert item in DATA_SEMANTICS, (kind, item)

    def test_one_way_projection_shapes(self):
        assert project_to_data_semantics("nominal") == ("category",)
        assert project_to_data_semantics("rate") == ("rate",)
        assert project_to_data_semantics("uncertainty") == ("uncertainty",)
        with pytest.raises(ValueError):
            project_to_data_semantics("bogus")


class TestDeriveDataKind:
    @pytest.mark.parametrize(
        "kind,field,want",
        [
            ("nominal", "zone_type", "qualitative"),
            ("signed_change", "delta", "diverging"),
            ("temporal", "year", "sequential"),
            ("temporal", "month", "cyclic"),
            ("quantitative", "temperature", "sequential"),
            ("rate", "pop_density", "sequential"),
            ("ordinal", "risk_level", "sequential"),
        ],
    )
    def test_single_point_derivation(self, kind: str, field: str, want: DataKind):
        assert derive_data_kind(kind, field_name=field) == want

    def test_unknown_kind_fail_closed(self):
        with pytest.raises(ValueError):
            derive_data_kind("bogus")


class TestInferMeasurementKind:
    def test_signed_change_by_value_evidence(self):
        d = infer_measurement_kind(
            "net_migration", dtype="float",
            values=[-500.0] * 10 + [300.0] * 10)
        assert d.kind == "signed_change"
        assert d.data_kind == "diverging"
        assert "GRAMMAR.MEAS.VALUE_SIGNED" in d.reason_codes

    def test_boundary_safe_matching_migration_not_rate(self):
        # 回归：子串匹配曾把 migration 误判为 rate（"ratio" ⊂ "migration"）
        d = infer_measurement_kind(
            "net_migration", dtype="float",
            values=[-100.0] * 10 + [200.0] * 10)
        assert d.kind == "signed_change"

    def test_rate_by_name_token(self):
        d = infer_measurement_kind(
            "pop_density_per_km2", dtype="float", values=[5.0, 10.0, 8.0])
        assert d.kind == "rate"

    def test_rate_token_with_substantial_negatives_yields_signed(self):
        # 值证据（双符号 ≥5%）优先于一般词素：带符号的率是净变化量
        d = infer_measurement_kind(
            "growth_rate", dtype="float",
            values=[-0.3] * 5 + [0.1] * 15)
        assert d.kind == "signed_change"

    def test_minority_negatives_sentinel_suspect(self):
        d = infer_measurement_kind(
            "sensor_reading", dtype="float",
            values=[-1.0] + [20.0] * 39)
        assert d.kind == "quantitative"
        assert any(r["kind"] == "signed_change" for r in d.rejected)

    def test_nominal_by_dtype(self):
        d = infer_measurement_kind("landuse_type", dtype="string", values=[])
        assert d.kind == "nominal"
        assert d.data_kind == "qualitative"

    def test_ordinal_by_name_token(self):
        d = infer_measurement_kind("risk_level", dtype="int", values=[1, 2, 3])
        assert d.kind == "ordinal"

    def test_ordinal_by_low_cardinality_integer(self):
        d = infer_measurement_kind("zone_code", dtype="int", values=[1, 2, 3, 1, 2])
        assert d.kind == "ordinal"
        assert "GRAMMAR.MEAS.VALUE_LOW_CARDINAL_INTEGER" in d.reason_codes

    def test_ratio_by_count_token(self):
        d = infer_measurement_kind("building_count", dtype="int",
                                   values=[10, 20, 30, 40, 50, 60, 70, 80])
        assert d.kind == "ratio"

    def test_temporal_and_uncertainty_strong_names(self):
        assert infer_measurement_kind("year_built", dtype="int",
                                      values=[1990, 2000]).kind == "temporal"
        assert infer_measurement_kind("aqi_std", dtype="float",
                                      values=[1.0, 2.0]).kind == "uncertainty"

    def test_quantitative_fallback_is_zero_drift(self):
        d = infer_measurement_kind("median_income", dtype="float",
                                   values=[40000.0, 50000.0, 60000.0])
        assert d.kind == "quantitative"
        assert d.data_kind == "sequential"

    def test_insufficient_evidence_disclosed(self):
        d = infer_measurement_kind("some_field", dtype="float", values=[])
        assert d.kind == "quantitative"
        assert "GRAMMAR.MEAS.INSUFFICIENT_EVIDENCE" in d.reason_codes

    def test_explicit_pin_wins(self):
        d = infer_measurement_kind("median_income", dtype="float",
                                   values=[1.0], explicit="nominal")
        assert d.kind == "nominal"
        assert d.source == "explicit"
        assert d.reason_codes == ["GRAMMAR.MEAS.EXPLICIT"]
        with pytest.raises(ValueError):
            infer_measurement_kind("x", dtype="float", values=[1.0],
                                   explicit="bogus")

    def test_determinism(self):
        a = infer_measurement_kind("pop_density", dtype="float",
                                   values=[1.0, 2.0, -3.0, 4.0, -1.0, 2.0])
        b = infer_measurement_kind("pop_density", dtype="float",
                                   values=[1.0, 2.0, -3.0, 4.0, -1.0, 2.0])
        assert a.model_dump() == b.model_dump()
