"""带计划构建器 + 统计口径披露测试（M4）—— 先行测试。"""
from __future__ import annotations

import json


from app.lib.cartography.multiscale.importance import derive_importance_model
from app.lib.cartography.multiscale.plan import (
    build_scale_profile,
    profile_with_artifact_refs,
)
from app.lib.cartography.multiscale.profile import (
    SourceRevision,
    UserScaleOverride,
    validate_profile,
)


def _roads_fc(n_per_class: dict) -> dict:
    feats = []
    for cls, n in n_per_class.items():
        for i in range(n):
            feats.append({
                "type": "Feature", "id": f"{cls}-{i}",
                "properties": {"道路等级": cls},
                "geometry": {"type": "LineString", "coordinates": [
                    [104.0 + i * 0.001 + k * 0.0001, 30.0 + (0.00001 if k % 2 else -0.00001)]
                    for k in range(30)]},
            })
    return {"type": "FeatureCollection", "features": feats}


ROAD_DIST = {"高速公路": 40, "国道": 120, "省道": 300, "县道": 800, "乡道": 2000}
ROAD_SCHEMA = {"道路等级": {"type": "string"}, "name": {"type": "string"}}


def _build(**kw):
    fc = kw.pop("fc", _roads_fc(ROAD_DIST))
    imp = kw.pop("importance", derive_importance_model(
        ROAD_SCHEMA, {"道路等级": ROAD_DIST}, feature_count=3260))
    return build_scale_profile(
        layer_id="roads",
        feature_collection=fc,
        source=SourceRevision(
            ref="ref:geojson:roads", content_revision=2,
            data_fingerprint="sha256:aa", feature_count=len(fc["features"]),
            geometry_family="line"),
        geometry_family="line",
        latitude_hint=30.0,
        importance=imp,
        **kw,
    )


class TestBuildScaleProfileRoads:
    def test_bands_full_domain_with_semantic_selection(self):
        result = _build()
        # 构建期工件 ref 未回填：中间态口径复验；回填后严格口径也应通过
        assert validate_profile(result.profile, require_artifact_refs=False) == []
        ref_map = {
            b.scale.tier: (f"ref:gen:{b.scale.tier}", f"roads__gen@{b.scale.tier}")
            for b in result.profile.bands
            if b.representation_mode == "generalized"
        }
        profile = profile_with_artifact_refs(result.profile, ref_map)
        errs = validate_profile(profile)
        assert errs == [], errs
        tiers = [b.scale.tier for b in profile.bands]
        assert tiers == ["world", "province", "city", "street"]
        # 粗带做语义取舍：world 只保留高速/国道/省道
        world = profile.bands[0]
        assert world.representation_mode == "generalized"
        assert world.selection.kept_values == ["高速公路", "国道", "省道"]
        assert world.stats.original_feature_count == 3260
        assert world.stats.displayed_feature_count == 40 + 120 + 300
        # street 带零取舍零简化 → raw（平凡带不落工件）
        street = profile.bands[3]
        assert street.representation_mode == "raw"
        assert street.stats.displayed_feature_count == 3260

    def test_line_geometry_simplification_at_coarse_bands(self):
        result = _build()
        world = result.profile.bands[0]
        # 线族粗带几何策略 = bounded_simplify，容差来自带基准分辨率
        assert world.geometry.strategy == "bounded_simplify"
        assert world.geometry.tolerance > 0
        assert world.geometry.tolerance_units == "meters_ground"

    def test_artifacts_produced_only_for_generalized_bands(self):
        result = _build()
        assert set(result.artifacts.keys()) == {
            b.scale.tier for b in result.profile.bands
            if b.representation_mode == "generalized"}
        for tier, gen in result.artifacts.items():
            assert gen.receipt["stats_match"] is True
            assert gen.receipt["input_count"] == 3260

    def test_semantic_checks_recorded(self):
        result = _build()
        checks = {c.check: c.status for c in result.profile.semantic_checks}
        assert checks.get("bands_cover_full_domain") == "pass"
        assert checks.get("displayed_not_exceeding_original") == "pass"
        assert checks.get("artifact_stats_match") == "pass"
        assert checks.get("importance_evidence_valid") == "pass"

    def test_profile_valid_without_importance(self):
        # 无可信字段：不做**语义取舍**（不伪造主干）；纯几何综合仍可发生
        # （几何简化不虚构语义 —— suppressed 恒 0，selection 证据恒 none）
        fc = _roads_fc({"国道": 100})
        result = build_scale_profile(
            layer_id="roads2",
            feature_collection=fc,
            source=SourceRevision(
                ref="ref:geojson:x", feature_count=100, geometry_family="line"),
            geometry_family="line",
            latitude_hint=30.0,
            importance=derive_importance_model(
                {"name": {"type": "string"}}, {}, feature_count=100),
        )
        profile = result.profile
        for b in profile.bands:
            assert b.selection.importance_evidence == "none"
            assert b.selection.kept_values == []
            assert b.stats.suppressed_feature_count == 0
            assert b.stats.displayed_feature_count == 100
        assert any("no_trusted_importance_field" in d
                   for d in profile.disclosures)
        assert validate_profile(profile, require_artifact_refs=False) == []

    def test_user_pin_mode_forces_raw(self):
        # 用户 pin raw（如「放大后显示所有学校点」的 street 语义整程版）
        result = _build(user_overrides=[UserScaleOverride(
            kind="pin_mode", value="raw")])
        profile = result.profile
        assert all(b.representation_mode == "raw" for b in profile.bands)
        assert result.artifacts == {}
        assert any(o.kind == "pin_mode" for o in profile.user_overrides)


class TestPolygonChoropleth:
    def test_admin_polygons_shared_boundary_protection(self):
        # 3×3 相邻网格面（共享边）—— 粗带应走共享边界保护
        feats = []
        d = 0.02
        for gx in range(3):
            for gy in range(3):
                lon, lat = 104.0 + gx * d, 30.0 + gy * d
                feats.append({
                    "type": "Feature", "id": f"cell-{gx}-{gy}",
                    "properties": {"区县": f"区{gx}{gy}", "pop": 1000 + gx * 10 + gy},
                    "geometry": {"type": "Polygon", "coordinates": [[
                        [lon, lat], [lon + d, lat], [lon + d, lat + d],
                        [lon, lat + d], [lon, lat]]]},
                })
        result = build_scale_profile(
            layer_id="choro",
            feature_collection={"type": "FeatureCollection", "features": feats},
            source=SourceRevision(
                ref="ref:geojson:choro", feature_count=9, geometry_family="polygon"),
            geometry_family="polygon",
            latitude_hint=30.5,
            importance=derive_importance_model({}, {}, feature_count=9),
        )
        world = result.profile.bands[0]
        assert world.geometry.strategy in (
            "shared_boundary_protect", "safe_no_generalization")
        if world.representation_mode == "generalized":
            gen = result.artifacts["world"]
            assert gen.receipt["anchors_retained"] == gen.receipt["anchors_total"]
            # 9 面全保留（无重要性取舍）
            assert len(gen.feature_collection["features"]) == 9


class TestPointDensity:
    def test_points_no_geometry_generalization_decluttering_only(self):
        feats = [
            {"type": "Feature", "id": f"s{i}", "properties": {"name": f"校{i}"},
             "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.001, 30.6]}}
            for i in range(8000)
        ]
        result = build_scale_profile(
            layer_id="schools",
            feature_collection={"type": "FeatureCollection", "features": feats},
            source=SourceRevision(
                ref="ref:geojson:schools", feature_count=8000,
                geometry_family="point"),
            geometry_family="point",
            latitude_hint=30.6,
            importance=derive_importance_model({}, {}, feature_count=8000),
        )
        for b in result.profile.bands:
            assert b.geometry.strategy == "none"  # 点不改几何
        # 密集点粗带去密策略激活（阈值与既有 symbol-law 5000 同源）
        world = result.profile.bands[0]
        assert world.decluttering.mode in ("point_cluster", "visual_heatmap")
        assert world.stats.displayed_feature_count == 8000  # 原始口径不变


class TestArtifactRefAttachment:
    def test_attach_refs_and_revalidate(self):
        result = _build()
        ref_map = {
            b.scale.tier: (f"ref:gen:{b.scale.tier}", f"roads__gen@{b.scale.tier}")
            for b in result.profile.bands
            if b.representation_mode == "generalized"
        }
        profile2 = profile_with_artifact_refs(result.profile, ref_map)
        assert validate_profile(profile2) == []
        for b in profile2.bands:
            if b.representation_mode == "generalized":
                assert b.artifact_ref == f"ref:gen:{b.scale.tier}"
                assert b.artifact_source_id == f"roads__gen@{b.scale.tier}"
        # 原 profile 不被修改（非破坏）
        for b in result.profile.bands:
            if b.representation_mode == "generalized":
                assert b.artifact_ref is None


class TestDeterminism:
    def test_same_input_same_profile(self):
        r1 = _build()
        r2 = _build()
        assert json.dumps(r1.profile.model_dump(), sort_keys=True) == json.dumps(
            r2.profile.model_dump(), sort_keys=True)
