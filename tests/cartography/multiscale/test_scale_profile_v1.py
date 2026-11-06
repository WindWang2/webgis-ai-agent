"""ScaleRepresentationProfile.v1 契约测试（M1）—— 先行测试（红）。

覆盖：
- 双基准比例尺换算（display 512px vs computation 256px）与 frontend
  meters-per-pixel.ts 常数一致性；
- 带结构合法性（[0,24) 连续覆盖、displayed≤original、generalized 必带工件）；
- band_at_zoom 查询语义；
- MapSpec 落笔投影（representation windows + window.source 覆盖 + scale_profile 声明）；
- 指纹确定性；user override 访问语义。
"""
from __future__ import annotations

import json
import math

import pytest

from app.lib.cartography.multiscale.profile import (
    EARTH_CIRCUMFERENCE_M,
    SCALE_REPRESENTATION_PROFILE_VERSION,
    band_at_zoom,
    display_meters_per_pixel,
    display_scale_denominator,
    profile_fingerprint,
    representation_windows_of_profile,
    to_mapspec_layer_block,
    validate_profile,
)
from app.lib.cartography.multiscale.profile import (
    ScaleRepresentationProfile,
)


def _minimal_profile(**overrides) -> ScaleRepresentationProfile:
    """4 带 raw-only 最小合法档案（street 带带 importance 选择证据）。"""
    base = dict(
        schema_version=SCALE_REPRESENTATION_PROFILE_VERSION,
        profile_id="msp:test:1",
        layer_id="schools",
        source={
            "ref": "ref:geojson:abc123",
            "content_revision": 3,
            "data_fingerprint": "sha256:deadbeef",
            "feature_count": 12000,
            "geometry_family": "point",
        },
        bands=[
            {
                "scale": {
                    "min_zoom": 0.0, "max_zoom": 8.0, "tier": "world",
                    "latitude": 30.5,
                },
                "representation_mode": "raw",
                "geometry": {"strategy": "none"},
                "selection": {"importance_evidence": "none"},
                "decluttering": {
                    "mode": "point_cluster",
                    "threshold_feature_count": 5000,
                },
                "stats": {
                    "original_feature_count": 12000,
                    "displayed_feature_count": 12000,
                    "suppressed_feature_count": 0,
                },
            },
            {
                "scale": {
                    "min_zoom": 8.0, "max_zoom": 11.0, "tier": "province",
                    "latitude": 30.5,
                },
                "representation_mode": "raw",
                "geometry": {"strategy": "none"},
                "selection": {"importance_evidence": "none"},
                "decluttering": {"mode": "none"},
                "stats": {
                    "original_feature_count": 12000,
                    "displayed_feature_count": 12000,
                    "suppressed_feature_count": 0,
                },
            },
            {
                "scale": {
                    "min_zoom": 11.0, "max_zoom": 14.0, "tier": "city",
                    "latitude": 30.5,
                },
                "representation_mode": "raw",
                "geometry": {"strategy": "none"},
                "selection": {"importance_evidence": "none"},
                "decluttering": {"mode": "none"},
                "stats": {
                    "original_feature_count": 12000,
                    "displayed_feature_count": 12000,
                    "suppressed_feature_count": 0,
                },
            },
            {
                "scale": {
                    "min_zoom": 14.0, "max_zoom": 24.0, "tier": "street",
                    "latitude": 30.5,
                },
                "representation_mode": "raw",
                "geometry": {"strategy": "none"},
                "selection": {"importance_evidence": "none"},
                "decluttering": {"mode": "none"},
                "stats": {
                    "original_feature_count": 12000,
                    "displayed_feature_count": 12000,
                    "suppressed_feature_count": 0,
                },
            },
        ],
        user_overrides=[],
        semantic_checks=[
            {"check": "bands_cover_full_domain", "status": "pass"},
        ],
        disclosures=["本档案仅声明表达；统计口径以 original_source 为准"],
    )
    base.update(overrides)
    return ScaleRepresentationProfile.model_validate(base)


# ── 比例尺换算（双基准显式分口径）─────────────────────────────────────────


class TestScaleConversion:
    def test_display_basis_matches_frontend_512px_constant(self):
        # frontend/lib/map-kit/meters-per-pixel.ts 同公式（512 * 2^z）
        z, lat = 10.0, 30.5
        expected = EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat)) / (512 * 2 ** z)
        assert display_meters_per_pixel(z, lat) == pytest.approx(expected, rel=1e-12)

    def test_display_vs_computation_basis_exact_2x(self):
        # 同 zoom：512 基准的 m/px 与 256 基准（scale_rules）的 2 倍关系。
        # 两基准用不同小数字面量表示同一物理常数（40075016.686/512 vs
        # 156543.03392804097/2），相对差 ~1e-10 —— 契约只锁 2 倍关系到
        # 物理常数精度（1e-9），不锁字面量逐位相等。
        from app.lib.cartography.scale_rules import meters_per_pixel as tile_mpp

        for z in (0.0, 5.5, 11.0, 23.0):
            assert display_meters_per_pixel(z, 0.0) == pytest.approx(
                tile_mpp(z, 0.0) / 2.0, rel=1e-9
            )

    def test_display_scale_denominator_z8_equator_96dpi(self):
        # 512 基准 z8 赤道 96dpi ≈ 1:1,155,000（对 256 基准 1:2,310,000）。
        d = display_scale_denominator(8.0, 0.0, dpi=96.0)
        assert d == pytest.approx(1_155_000, rel=2e-3)

    def test_denominator_fail_closed_on_bad_dpi(self):
        assert display_scale_denominator(8.0, 0.0, dpi=-3) == (
            display_scale_denominator(8.0, 0.0, dpi=96.0)
        )


# ── 带结构合法性 ──────────────────────────────────────────────────────────


class TestProfileValidation:
    def test_minimal_profile_valid(self):
        assert validate_profile(_minimal_profile()) == []

    def test_gap_band_rejected(self):
        p = _minimal_profile()
        p.bands[1].scale.min_zoom = 8.5  # 缺口 [8.0,8.5)
        errs = validate_profile(p)
        assert any("contiguous" in e or "gap" in e.lower() for e in errs)

    def test_domain_must_be_covered(self):
        p = _minimal_profile()
        p.bands[0].scale.min_zoom = 3.0
        errs = validate_profile(p)
        assert any("0" in e and "cover" in e.lower() for e in errs)

    def test_displayed_exceeding_original_rejected(self):
        p = _minimal_profile()
        p.bands[0].stats.displayed_feature_count = 999_999
        errs = validate_profile(p)
        assert any("displayed" in e and "original" in e for e in errs)

    def test_generalized_without_artifact_rejected(self):
        p = _minimal_profile()
        p.bands[0].representation_mode = "generalized"
        errs = validate_profile(p)
        assert any("artifact" in e for e in errs)

    def test_unknown_tier_name_rejected(self):
        p = _minimal_profile()
        p.bands[0].scale.tier = "galaxy"
        errs = validate_profile(p)
        assert any("tier" in e for e in errs)

    def test_band_scale_range_carries_both_bases(self):
        b = _minimal_profile().bands[0]
        assert b.scale.display_scale_basis == "maplibre_512px"
        assert b.scale.computation_basis == "tile_256px"
        # 换算值与带端点一致（band 端 zoom 的换算快照，不重复推导自由数值）
        assert b.scale.display_denominator_at_min == display_scale_denominator(
            0.0, b.scale.latitude
        )
        assert b.scale.computation_m_per_px_at_min == pytest.approx(
            display_meters_per_pixel(0.0, b.scale.latitude) * 2.0, rel=1e-9
        )


# ── 查询语义 ──────────────────────────────────────────────────────────────


class TestBandQuery:
    def test_band_at_zoom_half_open(self):
        p = _minimal_profile()
        assert band_at_zoom(p, 7.999).scale.tier == "world"
        assert band_at_zoom(p, 8.0).scale.tier == "province"
        assert band_at_zoom(p, 23.99).scale.tier == "street"

    def test_band_at_zoom_clamped_out_of_domain(self):
        p = _minimal_profile()
        assert band_at_zoom(p, -3).scale.tier == "world"
        assert band_at_zoom(p, 99).scale.tier == "street"


# ── MapSpec 落笔投影 ─────────────────────────────────────────────────────


class TestMapSpecProjection:
    def test_windows_derived_with_source_override(self):
        p = _minimal_profile()
        p.bands[0].representation_mode = "generalized"
        p.bands[0].artifact_source_id = "schools__gen@0-8"
        p.bands[0].artifact_ref = "ref:gen:xyz"
        windows = representation_windows_of_profile(p)
        # 相邻同模式带合并（v1.6 语义）：generalized + 3 个 raw 带 → 2 窗
        assert [w["mode"] for w in windows] == ["generalized", "raw"]
        assert windows[0]["source"] == "schools__gen@0-8"
        assert windows[1]["min_zoom"] == 8.0 and windows[1]["max_zoom"] == 24.0
        # raw 带无源覆盖（主层 source 语义）
        assert "source" not in windows[1]

    def test_to_mapspec_layer_block_shape(self):
        p = _minimal_profile()
        block = to_mapspec_layer_block(p)
        assert block["schema_version"] == SCALE_REPRESENTATION_PROFILE_VERSION
        assert block["classification_basis"] == "original_source"
        assert block["layer_id"] == "schools"
        assert block["source"]["ref"] == "ref:geojson:abc123"
        assert len(block["bands"]) == 4
        # 有界：声明可 json 序列化（进 MapSpec 开放 dict 面）
        json.dumps(block, ensure_ascii=False)


# ── 指纹与确定性 ──────────────────────────────────────────────────────────


class TestFingerprintAndDeterminism:
    def test_fingerprint_deterministic(self):
        a = profile_fingerprint(_minimal_profile())
        b = profile_fingerprint(_minimal_profile())
        assert a == b and len(a) >= 8

    def test_fingerprint_changes_with_policy(self):
        p = _minimal_profile()
        before = profile_fingerprint(p)
        p.bands[0].decluttering.mode = "none"
        assert profile_fingerprint(p) != before

    def test_fingerprint_ignores_profile_id(self):
        p1 = _minimal_profile()
        p2 = _minimal_profile()
        p2.profile_id = "msp:test:other"
        assert profile_fingerprint(p1) == profile_fingerprint(p2)

    def test_roundtrip_json(self):
        p = _minimal_profile()
        p2 = ScaleRepresentationProfile.model_validate(json.loads(p.model_dump_json()))
        assert profile_fingerprint(p2) == profile_fingerprint(p)
