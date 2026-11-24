"""paper_scale.v1 契约测试 —— 单位换算 oracle / 投影诚实 / 硬约束 / 校验。

换算 oracle 全部用独立公式手工推导（不是被测函数自证）：
- 1:500,000 @300dpi → 500 m/mm、42.333… m/px；
- 等效 zoom 用 display_meters_per_pixel 反解后回代验证；
- Mercator 纬度变形 cos(60°)=0.5 → 分母翻倍（独立三角学事实）。
"""
from __future__ import annotations

import math

import pytest

from app.lib.cartography.multiscale.paper_scale import (
    AUTO_TOLERANCE_FACTOR,
    MAX_DENOMINATOR,
    PublicationScaleContract,
    PageSpec,
    FrameViewport,
    ErrorBudget,
    HardConstraint,
    contract_fingerprint,
    equivalent_display_zoom,
    frame_ground_extent_m,
    ground_m_per_mm,
    ground_m_per_pixel,
    keep_region_of,
    mandatory_ids_of,
    mandatory_values_of,
    minimum_mappable_area_m2,
    minimum_mappable_length_m,
    parse_denominator_text,
    parse_page_text,
    resolve_tolerance_m,
    scale_denominator_at_latitude,
    scale_variation_report,
    strategy_hints_of,
    validate_contract,
)
from app.lib.cartography.multiscale.profile import display_meters_per_pixel


def _contract(**kw) -> PublicationScaleContract:
    base = dict(
        target_denominator=500_000,
        purpose="overview",
        frame=FrameViewport(
            width_mm=260.0, height_mm=170.0, scale_reference_lat=30.0,
            data_bbox=[115.0, 29.0, 117.0, 31.5],
        ),
    )
    base.update(kw)
    return PublicationScaleContract(**base)


class TestUnitConversions:
    def test_ground_m_per_mm_oracle(self):
        # 1:500,000 → 纸面 1mm = 地面 500m（独立算术：500000/1000）
        assert ground_m_per_mm(500_000) == pytest.approx(500.0)
        assert ground_m_per_mm(1_000_000) == pytest.approx(1000.0)
        assert math.isnan(ground_m_per_mm(50))          # 越下界 fail-closed
        assert math.isnan(ground_m_per_mm(MAX_DENOMINATOR * 10))

    def test_ground_m_per_pixel_oracle(self):
        # N·0.0254/dpi：500000×0.0254/300 = 42.3333… m/px
        assert ground_m_per_pixel(500_000, 300.0) == pytest.approx(
            500_000 * 0.0254 / 300.0)
        assert math.isnan(ground_m_per_pixel(500_000, 5.0))  # DPI 越域

    def test_equivalent_display_zoom_roundtrip(self):
        # 反解 oracle：zoom 使 display m/px(512 基准) == 纸面 m/px
        for denom, lat in ((500_000, 30.0), (50_000, 0.0), (2_000_000, 45.0)):
            z = equivalent_display_zoom(denom, lat, dpi=300.0)
            assert z is not None
            assert display_meters_per_pixel(z, lat) == pytest.approx(
                ground_m_per_pixel(denom, 300.0), rel=1e-9)

    def test_scale_denominator_at_latitude_cos_fact(self):
        # cos(60°)=0.5 → 实际分母翻倍（独立三角学 oracle）
        assert scale_denominator_at_latitude(500_000, 0.0) == 500_000
        assert scale_denominator_at_latitude(500_000, 60.0) == 1_000_000
        # 85° 仍在 Web Mercator 域内（85.0511° 钳制）
        assert scale_denominator_at_latitude(500_000, 85.0) == pytest.approx(
            500_000 / math.cos(math.radians(85.0)), rel=0.001)
        # 89° 超出 Mercator 域 → fail-honest None（不投影不存在处的比例尺）
        assert scale_denominator_at_latitude(500_000, 89.0) is None
        assert scale_denominator_at_latitude(500_000, 95.0) is None
        assert scale_denominator_at_latitude(0, 0.0) is None


class TestScaleVariationHonesty:
    def test_web_mercator_variation_reported(self):
        c = _contract()  # bbox 29..31.5N 锚定 30N
        rep = scale_variation_report(c)
        assert rep["evaluated"] is True
        assert rep["denominator_at_reference"] == 500_000
        assert rep["denominator_at_north_edge"] is not None
        assert rep["variation_ratio"] is not None
        # 1.5° 纬差 cos 变化 < 5% → 不强制披露
        assert rep["disclosure_required"] is False

    def test_high_latitude_span_requires_disclosure(self):
        c = _contract(frame=FrameViewport(
            width_mm=260.0, height_mm=170.0, scale_reference_lat=55.0,
            data_bbox=[30.0, 30.0, 31.0, 62.0],
        ))
        rep = scale_variation_report(c)
        # 30..62°N cos(30°)/cos(62°) ≈ 1.7 变化 → 必须强制披露
        assert rep["disclosure_required"] is True
        assert rep["variation_ratio"] > 0.3

    def test_non_mercator_not_evaluated(self):
        c = _contract(projection="epsg:4326")
        rep = scale_variation_report(c)
        assert rep["evaluated"] is False
        assert rep["disclosure_required"] is False


class TestDerivedBudgets:
    def test_auto_tolerance_sub_visible(self):
        # auto = 0.5 × 0.2mm × 500 m/mm = 50m（低于纸面可辨阈值）
        c = _contract()
        assert resolve_tolerance_m(c) == pytest.approx(
            AUTO_TOLERANCE_FACTOR * 0.2 * 500.0)
        # 显式容差覆盖 auto
        c2 = _contract(error_budget=ErrorBudget(tolerance_m=12.5))
        assert resolve_tolerance_m(c2) == 12.5

    def test_minimum_mappable_oracles(self):
        c = _contract()
        # 2.0 mm² × (500 m/mm)² = 500,000 m²
        assert minimum_mappable_area_m2(c) == pytest.approx(500_000.0)
        # 0.3 mm × 500 = 150 m
        assert minimum_mappable_length_m(c) == pytest.approx(150.0)

    def test_frame_ground_extent(self):
        c = _contract()
        assert frame_ground_extent_m(c) == pytest.approx(
            260.0 * 170.0 * 500.0 * 500.0)

    def test_strategy_hints_by_purpose(self):
        nav = strategy_hints_of(_contract(purpose="navigation"))
        assert nav["network_connectivity_hard"] is True
        assert nav["network_connectivity_hard"] is not None
        th = strategy_hints_of(_contract(purpose="thematic_exhibition"))
        assert th["thematic_conservation_hard"] is True
        ov = strategy_hints_of(_contract(purpose="overview"))
        assert ov["selection_aggressiveness"] == "high"
        assert ov["network_connectivity_hard"] is False


class TestHardConstraints:
    def test_views_union_and_dedup(self):
        c = _contract(hard_constraints=[
            HardConstraint(kind="keep_feature_ids", feature_ids=["a", "b", "a"]),
            HardConstraint(kind="keep_values", importance_field="道路等级",
                           values=["国道", "省道", "国道"]),
            HardConstraint(kind="keep_region", region_bbox=[1.0, 2.0, 3.0, 4.0]),
            HardConstraint(kind="keep_region", region_bbox=[2.0, 1.0, 5.0, 3.0]),
        ])
        assert mandatory_ids_of(c) == ["a", "b"]
        assert mandatory_values_of(c) == {"道路等级": ["国道", "省道"]}
        # 多区域取外接并集
        assert keep_region_of(c) == [1.0, 1.0, 5.0, 4.0]
        assert keep_region_of(_contract()) is None


class TestValidation:
    def test_valid_contract_passes(self):
        assert validate_contract(_contract()) == []

    def test_rejects_out_of_domain(self):
        assert any("denominator_out_of_range" in e
                   for e in validate_contract(_contract(target_denominator=10)))
        assert any("unknown_purpose" in e
                   for e in validate_contract(_contract(purpose="art")))
        assert any("unknown_projection" in e
                   for e in validate_contract(_contract(projection="epsg:9999")))
        bad_frame = _contract(frame=FrameViewport(
            width_mm=900.0, height_mm=900.0))
        assert any("frame_exceeds_page" in e for e in validate_contract(bad_frame))

    def test_rejects_bad_constraints(self):
        cases = [
            HardConstraint(kind="keep_feature_ids", feature_ids=[]),
            HardConstraint(kind="keep_values", values=[]),
            HardConstraint(kind="keep_values", values=["国道"], importance_field=None),
            HardConstraint(kind="keep_region"),
            HardConstraint(kind="explode", feature_ids=["x"]),
        ]
        for hc in cases:
            errs = validate_contract(_contract(hard_constraints=[hc]))
            assert errs, f"constraint must be rejected: {hc.kind}"

    def test_rejects_bad_bbox_and_budget(self):
        assert any("data_bbox_empty" in e for e in validate_contract(
            _contract(frame=FrameViewport(data_bbox=[2.0, 1.0, 1.0, 2.0]))))
        assert any("tolerance_negative" in e for e in validate_contract(
            _contract(error_budget=ErrorBudget(tolerance_m=-1.0))))
        assert any("area_error_ratio_out_of_range" in e for e in validate_contract(
            _contract(error_budget=ErrorBudget(max_area_error_ratio=1.5))))


class TestParsers:
    def test_denominator_text(self):
        assert parse_denominator_text("1:50万") == 500_000
        assert parse_denominator_text("50万") == 500_000
        assert parse_denominator_text("1:500000") == 500_000
        assert parse_denominator_text("1:1,000,000") == 1_000_000
        assert parse_denominator_text("1:100万") == 1_000_000
        assert parse_denominator_text("1亿") == 100_000_000
        assert parse_denominator_text("5亿") is None          # 越上界
        assert parse_denominator_text("abc") is None
        assert parse_denominator_text("1:-5万") is None
        assert parse_denominator_text("") is None

    def test_page_text(self):
        assert parse_page_text("A4横版") == ("A4", "landscape")
        assert parse_page_text("a3 portrait") == ("A3", "portrait")
        assert parse_page_text("A2") == ("A2", "landscape")   # ISO 缺省向
        assert parse_page_text("A5") is None                   # 词表外
        assert parse_page_text("B1横版") is None               # 非 A 系


class TestFingerprintAndPageSpec:
    def test_fingerprint_content_identity(self):
        a = _contract()
        b = _contract()
        b.contract_id = "other-id"
        b.generated_by = "someone"
        assert contract_fingerprint(a) == contract_fingerprint(b)
        c = _contract(target_denominator=1_000_000)
        assert contract_fingerprint(a) != contract_fingerprint(c)

    def test_pagespec_iso_derivation(self):
        p = PageSpec(format_id="A4", orientation="portrait")
        assert (p.width_mm, p.height_mm) == (210.0, 297.0)
        p2 = PageSpec(format_id="A4", orientation="landscape")
        assert (p2.width_mm, p2.height_mm) == (297.0, 210.0)
        # DPI 越域 fail-closed 回缺省
        p3 = PageSpec(dpi=99999.0)
        assert p3.dpi == 300.0

    def test_schema_version_pinned(self):
        c = _contract()
        assert c.schema_version == "paper_scale.v1"
        assert validate_contract(c) == []
