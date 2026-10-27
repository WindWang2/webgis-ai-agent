"""G3D01 basemap/elevation 治理画像 —— 目录不变量 + fail-closed 负例。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.lib.cartography.basemap_profiles import (
    BASEMAP_PROVIDER_PROFILES,
    ELEVATION_FALLBACK_ID,
    BasemapProviderProfile,
    CachePolicy,
    ElevationSourceProfile,
    ProfileValidationError,
    default_elevation_fallback,
    evaluate_basemap_availability,
    evaluate_elevation_availability,
    get_basemap_profile,
    get_elevation_profile,
    iter_llm_catalog,
)


class TestCatalogIntegrity:
    def test_catalog_importable_and_unique(self):
        ids = [p.provider_id for p in BASEMAP_PROVIDER_PROFILES]
        names = [p.name for p in BASEMAP_PROVIDER_PROFILES]
        assert len(ids) == len(set(ids)), "provider_id 必须唯一"
        assert len(names) == len(set(names)), "provider name 必须唯一"

    def test_every_profile_carries_license_and_attribution(self):
        """数据诚信红线：无 license/attribution 的 provider 不许进目录。"""
        for p in BASEMAP_PROVIDER_PROFILES:
            assert p.license.strip(), p.provider_id
            assert p.attribution.strip(), p.provider_id
            assert p.license_url or p.tos_url or p.license.startswith("由部署方"), p.provider_id

    def test_remote_urls_are_https(self):
        for p in BASEMAP_PROVIDER_PROFILES:
            if p.url.startswith("{env:"):
                continue
            assert p.url.startswith("https://"), p.provider_id

    def test_openfreemap_entries_present_and_restricted(self):
        ofm = [p for p in BASEMAP_PROVIDER_PROFILES if p.provider_id.startswith("openfreemap-")]
        assert len(ofm) == 3
        for p in ofm:
            assert p.attribution == "© OpenFreeMap © OpenMapTiles Data from OpenStreetMap"
            assert p.cache_policy.offline_bulk_download == "restricted"
            assert p.cache_policy.offline_note  # 限额必须显式记录
            assert p.requires_credentials is False  # 免 key

    def test_elevation_fallback_is_display_only(self):
        """AWS 公共 terrarium 混合源垂直基准不统一 → 绝不默认授予分析资格。"""
        fb = get_elevation_profile(ELEVATION_FALLBACK_ID)
        assert fb is not None
        assert fb.qualification == "display_only"
        assert fb.vertical_datum is None
        assert fb.attribution_detail  # 许可要求的多源署名清单必须在场
        assert fb.encoding == "terrarium"

    def test_synthetic_profile_is_display_only(self):
        syn = get_elevation_profile("synthetic-demo-dem")
        assert syn is not None
        assert syn.synthetic is True
        assert syn.qualification == "display_only"

    def test_llm_catalog_excludes_local_and_includes_openfreemap(self):
        catalog = iter_llm_catalog()
        names = [c["name"] for c in catalog]
        assert "本地底图" not in names
        assert any("OpenFreeMap" in n for n in names)


class TestFailClosedNegatives:
    def test_missing_license_rejected(self):
        with pytest.raises((ValidationError, ProfileValidationError)):
            BasemapProviderProfile(
                provider_id="x",
                name="X",
                kind="raster_xyz",
                map_style_type="raster",
                url="https://tiles.example/{z}/{x}/{y}.png",
                attribution="someone",
                license="",
                max_zoom=18,
                cache_policy=CachePolicy(),
                keywords=["x"],
            )

    def test_missing_attribution_rejected(self):
        with pytest.raises((ValidationError, ProfileValidationError)):
            BasemapProviderProfile(
                provider_id="x",
                name="X",
                kind="raster_xyz",
                map_style_type="raster",
                url="https://tiles.example/{z}/{x}/{y}.png",
                attribution="",
                license="MIT",
                max_zoom=18,
                cache_policy=CachePolicy(),
                keywords=["x"],
            )

    def test_http_url_rejected(self):
        with pytest.raises((ValidationError, ProfileValidationError)):
            BasemapProviderProfile(
                provider_id="x",
                name="X",
                kind="raster_xyz",
                map_style_type="raster",
                url="http://insecure.example/{z}/{x}/{y}.png",
                attribution="someone",
                license="MIT",
                max_zoom=18,
                cache_policy=CachePolicy(),
                keywords=["x"],
            )

    def test_partial_env_placeholder_does_not_bypass_https(self):
        """R1 P2-4：`{env:}` 只豁免整段注入模板；http+占位混合仍拒绝。"""
        with pytest.raises((ValidationError, ProfileValidationError)):
            BasemapProviderProfile(
                provider_id="x",
                name="X",
                kind="raster_xyz",
                map_style_type="raster",
                url="http://evil.example/{z}/{x}/{y}.png?k={env:SOME_TOKEN}",
                attribution="someone",
                license="MIT",
                max_zoom=18,
                cache_policy=CachePolicy(),
                keywords=["x"],
            )

    def test_https_with_inline_env_placeholder_accepted(self):
        """天地图形态：https 前缀 + token 占位 —— 合法。"""
        p = BasemapProviderProfile(
            provider_id="x",
            name="X",
            kind="raster_xyz",
            map_style_type="raster",
            url="https://tiles.example/{z}/{x}/{y}.png?k={env:SOME_TOKEN}",
            attribution="someone",
            license="MIT",
            max_zoom=18,
            requires_credentials=True,
            credentials_env="SOME_TOKEN",
            cache_policy=CachePolicy(),
            keywords=["x"],
        )
        assert p.url.startswith("https://")

    def test_whole_url_env_placeholder_accepted(self):
        """local-xyz 形态：整段 {env:VAR} 注入模板 —— 合法。"""
        p = BasemapProviderProfile(
            provider_id="x",
            name="X",
            kind="raster_xyz",
            map_style_type="raster",
            url="{env:NEXT_PUBLIC_LOCAL_BASEMAP_URL}",
            attribution="someone",
            license="MIT",
            max_zoom=22,
            env_gate="NEXT_PUBLIC_LOCAL_BASEMAP_URL",
            deployment="air_gapped_only",
            cache_policy=CachePolicy(),
            keywords=["x"],
        )
        assert p.url == "{env:NEXT_PUBLIC_LOCAL_BASEMAP_URL}"

    def test_credentials_without_env_name_rejected(self):
        with pytest.raises((ValidationError, ProfileValidationError)):
            BasemapProviderProfile(
                provider_id="x",
                name="X",
                kind="raster_xyz",
                map_style_type="raster",
                url="https://tiles.example/{z}/{x}/{y}.png?k=secret",
                attribution="someone",
                license="MIT",
                max_zoom=18,
                requires_credentials=True,
                credentials_env="sk-live-abc123",  # 内联 secret 形值 → 拒绝
                cache_policy=CachePolicy(),
                keywords=["x"],
            )

    def test_requires_credentials_without_env_rejected(self):
        with pytest.raises((ValidationError, ProfileValidationError)):
            BasemapProviderProfile(
                provider_id="x",
                name="X",
                kind="raster_xyz",
                map_style_type="raster",
                url="https://tiles.example/{z}/{x}/{y}.png",
                attribution="someone",
                license="MIT",
                max_zoom=18,
                requires_credentials=True,
                cache_policy=CachePolicy(),
                keywords=["x"],
            )

    def test_air_gapped_only_requires_env_gate(self):
        with pytest.raises((ValidationError, ProfileValidationError)):
            BasemapProviderProfile(
                provider_id="x",
                name="X",
                kind="raster_xyz",
                map_style_type="raster",
                url="https://tiles.example/{z}/{x}/{y}.png",
                attribution="someone",
                license="MIT",
                max_zoom=18,
                deployment="air_gapped_only",
                cache_policy=CachePolicy(),
                keywords=["x"],
            )

    def test_synthetic_elevation_cannot_be_analysis_grade(self):
        with pytest.raises((ValidationError, ProfileValidationError)):
            ElevationSourceProfile(
                source_id="bad-synthetic",
                kind="synthetic_local",
                encoding="terrarium",
                max_zoom=14,
                attribution="synthetic",
                license="mine",
                synthetic=True,
                qualification="analysis_grade",  # 红线：合成 ≠ 分析级
            )

    def test_remote_elevation_cannot_default_to_analysis_grade(self):
        with pytest.raises((ValidationError, ProfileValidationError)):
            ElevationSourceProfile(
                source_id="bad-remote",
                kind="remote_terrarium_xyz",
                encoding="terrarium",
                max_zoom=14,
                url="https://dem.example/{z}/{x}/{y}.png",
                attribution="someone",
                license="X",
                vertical_datum="EGM96",
                qualification="analysis_grade",  # 远端公共混合源不可默认分析级
            )

    def test_remote_elevation_requires_url(self):
        with pytest.raises((ValidationError, ProfileValidationError)):
            ElevationSourceProfile(
                source_id="bad-remote-2",
                kind="remote_terrarium_xyz",
                encoding="terrarium",
                max_zoom=14,
                attribution="someone",
                license="X",
                qualification="display_only",
            )

    def test_elevation_url_must_be_https(self):
        with pytest.raises((ValidationError, ProfileValidationError)):
            ElevationSourceProfile(
                source_id="bad-remote-3",
                kind="remote_terrarium_xyz",
                encoding="terrarium",
                max_zoom=14,
                url="http://dem.example/{z}/{x}/{y}.png",
                attribution="someone",
                license="X",
                qualification="display_only",
            )


class TestAvailabilityEvaluation:
    def test_tianditu_missing_token(self):
        p = get_basemap_profile("tianditu-vec")
        assert p is not None
        verdict = evaluate_basemap_availability(p, env={}, deployment="cloud")
        assert verdict.state == "unavailable"
        assert verdict.reason_code == "missing_credentials"

    def test_tianditu_with_token_available(self):
        p = get_basemap_profile("tianditu-vec")
        assert p is not None
        verdict = evaluate_basemap_availability(
            p, env={"NEXT_PUBLIC_TIANDITU_TOKEN": "tk-xxx"}, deployment="cloud"
        )
        assert verdict.state == "available"

    def test_local_xyz_missing_env(self):
        p = get_basemap_profile("local-xyz")
        assert p is not None
        verdict = evaluate_basemap_availability(p, env={}, deployment="air_gapped")
        assert verdict.state == "unavailable"
        assert verdict.reason_code == "missing_env_gate"

    def test_local_xyz_available_in_air_gapped(self):
        p = get_basemap_profile("local-xyz")
        assert p is not None
        verdict = evaluate_basemap_availability(
            p,
            env={"NEXT_PUBLIC_LOCAL_BASEMAP_URL": "http://tiles.intranet/{z}/{x}/{y}.png"},
            deployment="air_gapped",
        )
        assert verdict.state == "available"

    def test_remote_blocked_in_air_gapped(self):
        for pid in ("osm", "carto-light", "openfreemap-libre"):
            p = get_basemap_profile(pid)
            assert p is not None
            verdict = evaluate_basemap_availability(p, env={}, deployment="air_gapped")
            assert verdict.state == "unavailable", pid
            assert verdict.reason_code == "deployment_not_permitted", pid

    def test_elevation_fallback_blocked_in_air_gapped(self):
        fb = get_elevation_profile(ELEVATION_FALLBACK_ID)
        assert fb is not None
        verdict = evaluate_elevation_availability(fb, env={}, deployment="air_gapped")
        assert verdict.state == "unavailable"
        assert verdict.reason_code == "deployment_not_permitted"

    def test_reason_codes_in_closed_vocabulary(self):
        from app.lib.cartography.basemap_profiles import AVAILABILITY_REASONS

        assert "ok" in AVAILABILITY_REASONS
        assert "missing_env_gate" in AVAILABILITY_REASONS
        assert "missing_credentials" in AVAILABILITY_REASONS
        assert "deployment_not_permitted" in AVAILABILITY_REASONS


class TestDefaultElevationFallback:
    def test_cloud_returns_governed_aws_profile(self):
        fb = default_elevation_fallback(deployment="cloud")
        assert fb is not None
        assert fb.source_id == ELEVATION_FALLBACK_ID
        assert fb.url and fb.url.startswith("https://")

    def test_air_gapped_returns_none_not_ungoverned_url(self):
        """D6：air_gapped 无兜底 = None（调用方显式降级），绝不回退未治理 URL。"""
        assert default_elevation_fallback(deployment="air_gapped") is None
