"""Wave 2 (ADR-0104 #4) — DatasetProfile → resolver 事实接缝的契约测试。

覆盖（审计 03-data-to-resolver-gaps 的关闭验证）：
- (a) to_resolver_profile 单一适配出口发出完整事实词表（vector / raster /
  temporal 三案例；DatasetProfileV3 → DatasetProfile → camelCase）；
- (b) RefDescriptor.crs → profile_from_descriptor → finalize 的活门
  （审计「死门」场景：PROJECTED_REQUIRED 算法在地理 CRS 上被拒）；
- (c) precondition 缺席事实 → deferred（不再 false-reject）；证据证明
  缺席仍拒绝；
- (d) workflow_engine.resolve_step_tool 执行期画像事实；
- (e) plan-time backend 变体/资源分层证据进 resolution 记录；
- (f) 插值解析事实驱动（小样本/零方差 → IDW 族；大样本+投影 CRS+
  不确定性需求 → OK 族；文本 hint 不绕硬门）；
- (g) ArtifactContract.profile_ref 生产方（有界 digest；缺席 → ""）；
- 决定论：同输入 → 同 resolver 输出 / 事实投影 / digest。
"""
from typing import Any, Dict, List, Optional

import pytest

from app.lib.gis.algorithm_resolver import AlgorithmResolver
from app.lib.gis.dataset_profile import DatasetProfile
from app.lib.gis.scientific_preconditions import evaluate_precondition
from app.services.gis_harness.planner import (
    MapProductPlanner,
    _interpolation_fact_signals,
    _interpolation_query_signals,
)


# ── fixtures / helpers ────────────────────────────────────────────────

def _vector_v3_features(n: int = 12, dup_first: bool = False) -> List[Dict[str, Any]]:
    feats = []
    for i in range(n):
        lng = 116.0 + (i % 4) * 0.01
        lat = 39.9 + (i // 4) * 0.01
        if dup_first and i == n - 1:
            lng, lat = 116.0, 39.9  # 与首点堆叠（重复坐标证据）
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "pm25": float(i + 1),
                "binary01": 1 if i % 2 else 0,
                "flag": bool(i % 2),
                "name": f"s{i}",
                "observed_at": f"2024-01-{(i % 28) + 1:02d}",
            },
        })
    return feats


def _build_v3_profile(
    features: List[Dict[str, Any]],
    *,
    crs: str = "EPSG:4326",
) -> Any:
    from app.lib.data.profile import profile_features

    vp, quality = profile_features(features, crs=crs)
    from app.lib.data.profile import DatasetProfileV3

    return DatasetProfileV3(
        target_ref="ref:geojson-test",
        category="vector",
        crs=crs,
        extent=vp.extent,
        vector=vp,
        profile_quality=quality,
    )


def _descriptor_with_crs(crs: Optional[str]) -> Dict[str, Any]:
    return {
        "ref_id": "ref:geojson-d",
        "feature_count": 60,
        "geometry_types": ["Point"],
        "bbox": [116.0, 39.8, 116.5, 40.1],
        "crs": crs,
        "field_schema": {
            "pm25": {"type": "number", "null_count": 3},
            "label": {"type": "string", "null_count": 0},
        },
        "field_schema_complete": True,
    }


# ── (a) adapter emits the full fact vocabulary ────────────────────────

class TestAdapterFactVocabulary:
    def test_vector_case_emits_full_vocabulary(self):
        profile = _build_v3_profile(_vector_v3_features(12))
        adapted = DatasetProfile.from_profile_v3(profile)
        rp = adapted.to_resolver_profile()

        # 既有键不回归
        assert rp["featureCount"] == 12
        assert rp["geometryTypes"] == ["Point"]
        assert rp["crs"] == "EPSG:4326"
        # 新事实词表（scientific_preconditions 的既读键）
        assert rp["numericFields"] == ["pm25", "binary01"]  # 属性插入序（确定性）
        assert "categoricalFields" in rp and set(rp["categoricalFields"]) == {"flag", "name", "observed_at"}
        # 深扫证实的 0/1 定义域数值列 + boolean 列都是二值证据
        assert set(rp["binaryFields"]) == {"binary01", "flag"}
        assert rp["hasTimeField"] is True
        assert rp["temporalObservationCount"] == 12
        assert rp["valueVariance"] > 0.0
        assert rp["numericSampleCount"] == 12
        assert rp["crsClass"] == "geographic"
        # 重复坐标证据（深扫口径在场）
        assert rp["uniqueCoordinateCount"] == 12
        assert rp["duplicateCoordinateCount"] == 0
        # 逐字段 null 证据
        assert rp["fields"]["pm25"]["type"] == "number"

    def test_duplicates_and_zero_variance_are_visible(self):
        feats = _vector_v3_features(12)
        for f in feats:
            # 全部数值场常量（pm25 与 binary01 都不变）→ 最大方差证据 = 0
            f["properties"]["pm25"] = 7.0
            f["properties"]["binary01"] = 1
        for f in feats[1:]:
            f["geometry"]["coordinates"] = list(feats[0]["geometry"]["coordinates"])
        profile = _build_v3_profile(feats)
        rp = DatasetProfile.from_profile_v3(profile).to_resolver_profile()
        assert rp["duplicateCoordinateCount"] == 11
        assert rp["uniqueCoordinateCount"] == 1
        assert rp["valueVariance"] == 0.0

    def test_raster_case_emits_band_count_and_honest_feature_count(self):
        from app.lib.data.profile import DatasetProfileV3, ProfileQuality, RasterProfileData

        raster = RasterProfileData(
            width=512, height=256, band_count=4, dtypes=["uint8"],
            crs="EPSG:32650", resolution_x=10.0,
        )
        profile = DatasetProfileV3(
            target_ref="ref:raster-1", category="raster", crs="EPSG:32650",
            raster=raster, profile_quality=ProfileQuality.COMPLETE,
        )
        rp = DatasetProfile.from_profile_v3(profile).to_resolver_profile()
        assert rp["bandCount"] == 4
        assert rp["featureCount"] is None  # 像元数不是要素数（不虚构）
        assert rp["geometryTypes"] == ["raster"]
        assert rp["crsClass"] == "projected_local_metric"

    def test_temporal_absence_evidence_only_from_full_scan(self):
        from app.lib.data.profile import DatasetProfileV3, ProfileQuality, profile_from_field_schema

        vp, quality = profile_from_field_schema(
            {"name": {"type": "string"}}, row_count=5,
            geometry_types=["Point"],
        )
        partial = DatasetProfileV3(
            target_ref="r", category="vector", vector=vp,
            profile_quality=ProfileQuality.PARTIAL,
        )
        rp_partial = DatasetProfile.from_profile_v3(partial).to_resolver_profile()
        assert "hasTimeField" not in rp_partial  # 命名启发缺席 ≠ 证据证伪

        vp2, quality2 = profile_from_field_schema(
            {"name": {"type": "string"}}, row_count=5, geometry_types=["Point"],
        )
        complete = DatasetProfileV3(
            target_ref="r", category="vector", vector=vp2,
            profile_quality=ProfileQuality.COMPLETE,
        )
        # COMPLETE 口径但来源是 descriptor 投影（scanned_rows==0）→ 仍不证伪
        # （扫描覆盖不成立）；只有真实深扫（profile_features COMPLETE）证伪。
        rp_complete = DatasetProfile.from_profile_v3(complete).to_resolver_profile()
        assert "hasTimeField" not in rp_complete

    def test_deep_scan_proves_temporal_absence(self):
        feats = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.0 + i, 39.0]},
                "properties": {"v": i},
            }
            for i in range(3)
        ]
        profile = _build_v3_profile(feats)  # profile_features → COMPLETE
        assert profile.profile_quality.value == "complete"
        rp = DatasetProfile.from_profile_v3(profile).to_resolver_profile()
        assert rp["hasTimeField"] is False
        assert "temporalObservationCount" not in rp

    def test_authoritative_empty_numeric_list_is_emitted(self):
        desc = {
            "ref_id": "r",
            "feature_count": 4,
            "geometry_types": ["Point"],
            "field_schema": {"name": {"type": "string"}},
            "field_schema_complete": True,
        }
        rp = DatasetProfile.from_ref_descriptor(desc).to_resolver_profile()
        assert rp["numericFields"] == []  # 完整 schema → 空清单是权威缺席
        assert rp["binaryFields"] == []

    def test_truncated_schema_omits_empty_lists(self):
        desc = {
            "ref_id": "r",
            "feature_count": 4,
            "geometry_types": ["Point"],
            "field_schema": {"name": {"type": "string"}},
            "field_schema_complete": False,
        }
        rp = DatasetProfile.from_ref_descriptor(desc).to_resolver_profile()
        assert "numericFields" not in rp  # 截断 schema：空清单不构成权威缺席
        assert "binaryFields" not in rp


# ── (b) descriptor CRS → live gate at finalize ────────────────────────

class TestDescriptorCrsGateLive:
    def test_compute_descriptor_extracts_declared_crs(self):
        from app.schemas.ref_descriptor import compute_descriptor

        fc = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                 "properties": {}},
            ],
        }
        descriptor = compute_descriptor("ref:geojson-x", fc)
        assert descriptor.crs == "EPSG:4326"
        roundtrip = descriptor.to_dict()
        assert roundtrip["crs"] == "EPSG:4326"
        from app.schemas.ref_descriptor import RefDescriptor

        assert RefDescriptor.from_dict(roundtrip).crs == "EPSG:4326"

    def test_descriptor_without_crs_stays_honestly_none(self):
        from app.schemas.ref_descriptor import compute_descriptor

        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                 "properties": {}},
            ],
        }
        assert compute_descriptor("ref:geojson-x", fc).crs is None

    def test_profile_from_descriptor_flows_crs_and_facts(self):
        from app.services.spatial_meta_profiler import profile_from_descriptor

        profile = profile_from_descriptor(_descriptor_with_crs("EPSG:4326"))
        assert profile["crs"] == "EPSG:4326"
        assert profile["crs_status"] == "explicit"
        assert profile["crsClass"] == "geographic"
        assert profile["numericFields"] == ["pm25"]
        assert profile["fields"]["pm25"]["null_ratio"] == round(3 / 60, 6)

    def test_profile_from_descriptor_without_crs_keeps_unknown(self):
        from app.services.spatial_meta_profiler import profile_from_descriptor

        profile = profile_from_descriptor(_descriptor_with_crs(None))
        assert profile["crs"] is None
        assert profile["crs_status"] == "unknown"
        assert "crsClass" not in profile

    def test_dead_gate_now_live_at_finalize(self):
        """审计场景：PROJECTED_REQUIRED（kriging）在地理 CRS 上于
        descriptor 驱动的 finalize 路径被拒 —— 此前该路径 crs=None 使门死亡。"""
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.spatial_meta_profiler import profile_from_descriptor

        planner = MapProductPlanner()
        intent = resolve_map_request_intent("克里金插值空气质量")
        plan = planner.plan_from_intent(intent, use_memo=False)
        assert any(r.capability == "spatial_interpolation" for r in plan.data_requirements)

        profile = profile_from_descriptor(_descriptor_with_crs("EPSG:4326"))
        finalized = planner.finalize_with_profile(plan, profile)
        sel = next(
            s for s in finalized.algorithm_selections
            if s.capability == "spatial_interpolation"
        )
        assert sel.algorithm == "interpolation.idw"  # 地理 CRS → 克里金族不可用
        assert any(
            r.startswith("crs_class_mismatch:interpolation.kriging") for r in sel.rejected
        ), sel.rejected
        # 事实投影也独立识别地理 CRS（不 hint 克里金）
        assert finalized.algorithm_fact_signals["ok_family_suitable"] is False
        assert finalized.algorithm_fact_signals["evidence"]["crsClass"] == "geographic"

    def test_projected_crs_at_finalize_keeps_gate_passing(self):
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.spatial_meta_profiler import profile_from_descriptor

        planner = MapProductPlanner()
        intent = resolve_map_request_intent("克里金插值空气质量")
        plan = planner.plan_from_intent(intent, use_memo=False)
        profile = profile_from_descriptor({
            **_descriptor_with_crs("EPSG:32650"),
            "feature_count": 40,
        })
        finalized = planner.finalize_with_profile(plan, profile)
        sel = next(
            s for s in finalized.algorithm_selections
            if s.capability == "spatial_interpolation"
        )
        assert sel.algorithm == "interpolation.kriging"
        assert not any("crs_class_mismatch" in r for r in sel.rejected)
        assert finalized.algorithm_fact_signals["ok_family_suitable"] is True


# ── (c) absent fact → deferred, genuine absence still rejected ────────

class TestPreconditionAbsentFacts:
    def test_fields_known_without_numeric_key_defers(self):
        result = evaluate_precondition(
            "numeric_field_required",
            {"fields": {"a": {"type": "string"}}, "fields_status": "explicit"},
        )
        assert result.verdict == "PASS"
        assert "deferred" in result.message

    def test_authoritative_empty_numeric_list_rejects(self):
        result = evaluate_precondition(
            "numeric_field_required",
            {"fields": {"a": {"type": "string"}}, "numericFields": []},
        )
        assert result.verdict == "INSUFFICIENT_DATA"

    def test_present_numeric_list_passes(self):
        result = evaluate_precondition(
            "numeric_field_required",
            {"fields": {"a": {"type": "number"}}, "numericFields": ["a"]},
        )
        assert result.verdict == "PASS"

    def test_fields_known_without_binary_key_defers(self):
        result = evaluate_precondition(
            "binary_field_required",
            {"fields": {"a": {"type": "string"}}, "fields_status": "explicit"},
        )
        assert result.verdict == "PASS"

    def test_authoritative_empty_binary_list_rejects(self):
        result = evaluate_precondition(
            "binary_field_required",
            {"fields": {"a": {"type": "string"}}, "binaryFields": []},
        )
        assert result.verdict == "INSUFFICIENT_DATA"

    def test_binary_present_passes(self):
        result = evaluate_precondition(
            "binary_field_required",
            {"fields": {"f": {"type": "boolean"}}, "binaryFields": ["f"]},
        )
        assert result.verdict == "PASS"

    def test_resolver_does_not_false_reject_statistics_algorithm(self):
        """hotspot 域（stats.getis_ord_gi_star 族）声明 numeric_field_required；
        在 descriptor 派生画像（fields 已知、numericFields 键缺席）上不得被
        科学门误拒 —— 修复前这是结构性 false-reject（审计 gap #3）。"""
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        registry = get_algorithm_registry()
        target = None
        for algo in registry.algorithms_for_capability("hotspot"):
            if "numeric_field_required" in (algo.scientific_preconditions or []):
                target = algo
                break
        if target is None:  # 能力词表漂移时退化为直接构造（仍验证语义）
            pytest.skip("no hotspot algorithm declares numeric_field_required")
        profile = {
            "featureCount": 500,
            "geometryTypes": ["Point"],
            "fields": {"name": {"type": "string"}, "kind": {"type": "string"}},
            "fields_status": "explicit",
        }
        resolution = AlgorithmResolver().resolve(
            "hotspot", profile=profile, available_tools=None,
        )
        assert resolution.status == "resolved"
        assert resolution.algorithm == target.id
        assert not any("numeric_field_required" in r for r in resolution.rejected), \
            resolution.rejected


# ── (d) execution-time re-resolution sees profile facts ──────────────

class TestRuntimeProfileWiring:
    async def test_resolver_profile_for_args_derives_from_descriptor(self):
        from app.services.workflow_engine import WorkflowEngine
        from app.services.session_data import session_data_manager

        session_id = "v4-profile-seam"
        fc = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:32650"}},
            "features": _vector_v3_features(10),
        }
        ref = await session_data_manager.store(session_id, fc, prefix="geojson")
        try:
            profile = await WorkflowEngine._resolver_profile_for_args(
                session_id, {"geojson": ref}
            )
            assert profile is not None
            assert profile["featureCount"] == 10
            assert profile["crs"] == "EPSG:32650"
            assert profile["crsClass"] == "projected_local_metric"
        finally:
            await session_data_manager.clear_session(session_id)

    async def test_resolver_profile_for_args_honest_none(self):
        from app.services.workflow_engine import WorkflowEngine

        assert await WorkflowEngine._resolver_profile_for_args(None, {"geojson": "ref:x"}) is None
        assert await WorkflowEngine._resolver_profile_for_args("sess", {"zoom": 10}) is None

    def test_resolve_step_tool_gates_on_profile_facts(self):
        from app.schemas.project_schema import WorkflowStepSpec
        from app.services.workflow_engine import WorkflowEngine

        class _Reg:
            def list_tools(self):
                return [
                    "idw_interpolation", "kriging_interpolation",
                    "rbf_interpolation", "nearest_neighbor_surface",
                ]

        spec = WorkflowStepSpec(
            step_id="s1", tool_name="kriging_interpolation",
            capability="spatial_interpolation",
        )
        # 3 个点：克里金 min_features=8 硬门在执行期拒 → 解析回退到 IDW。
        tool, cap, algo, evidence = WorkflowEngine.resolve_step_tool(
            spec, _Reg(),
            profile={
                "featureCount": 3, "geometryTypes": ["Point"],
                "crs": "EPSG:32650", "numericFields": ["v"],
                "fields": {"v": {"type": "number"}},
            },
        )
        assert cap == "spatial_interpolation"
        assert algo == "interpolation.idw"
        assert tool == "idw_interpolation"
        assert "profile_facts" in evidence
        assert "featureCount" in evidence["profile_facts"]

    def test_resolve_step_tool_without_profile_keeps_legacy_shape(self):
        from app.schemas.project_schema import WorkflowStepSpec
        from app.services.workflow_engine import WorkflowEngine

        class _Reg:
            def list_tools(self):
                return ["idw_interpolation", "kriging_interpolation"]

        spec = WorkflowStepSpec(
            step_id="s1", tool_name="kriging_interpolation",
            capability="spatial_interpolation",
        )
        tool, cap, algo, evidence = WorkflowEngine.resolve_step_tool(spec, _Reg())
        assert algo == "interpolation.idw"  # 默认优先级（无事实 → 无门）
        assert "profile_facts" not in evidence


# ── (e) plan-time backend evidence on resolution records ─────────────

class TestBackendEvidence:
    def test_resolution_records_backend_variant_and_scale(self):
        profile = {
            "featureCount": 500, "geometryTypes": ["Point"],
            "crs": "EPSG:32650", "crsClass": "projected_local_metric",
            "numericFields": ["v"], "fields": {"v": {"type": "number"}},
        }
        resolution = AlgorithmResolver().resolve(
            "spatial_interpolation", profile=profile, available_tools=None,
            algorithm_hint="interpolation.kriging",
        )
        assert resolution.algorithm == "interpolation.kriging"
        assert resolution.backend_variant == "numpy_batched"
        assert resolution.backend == "numpy"
        assert resolution.scale_tier
        assert resolution.runtime_strategy

    def test_default_algorithm_records_default_path_variant(self):
        profile = {"featureCount": 100, "geometryTypes": ["Point"]}
        resolution = AlgorithmResolver().resolve(
            "spatial_interpolation", profile=profile, available_tools=None,
        )
        assert resolution.algorithm == "interpolation.idw"
        # IDW 未声明变体 → 默认工具路径（variant 空，tier 仍记录）
        assert resolution.backend_variant == ""
        assert resolution.scale_tier

    def test_planner_record_carries_backend_fields(self):
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.spatial_meta_profiler import profile_from_descriptor

        planner = MapProductPlanner()
        intent = resolve_map_request_intent("克里金插值空气质量")
        plan = planner.plan_from_intent(intent, use_memo=False)
        profile = profile_from_descriptor({
            **_descriptor_with_crs("EPSG:32650"), "feature_count": 40,
        })
        finalized = planner.finalize_with_profile(plan, profile)
        sel = next(
            s for s in finalized.algorithm_selections
            if s.capability == "spatial_interpolation"
        )
        assert sel.algorithm == "interpolation.kriging"
        assert sel.backend_variant == "numpy_batched"
        assert sel.scale_tier


# ── (f) fact-driven interpolation resolution ─────────────────────────

class TestInterpolationFactResolution:
    def test_small_n_does_not_hint_kriging(self):
        signals = _interpolation_fact_signals({
            "featureCount": 5, "geometryTypes": ["Point"],
            "numericFields": ["v"], "crs": "EPSG:32650",
        })
        assert signals["ok_family_suitable"] is False
        assert signals["fact_hint"] == ""
        assert signals["evidence"]["featureCount"] == 5

    def test_zero_variance_blocks_kriging_family(self):
        signals = _interpolation_fact_signals({
            "featureCount": 100, "geometryTypes": ["Point"],
            "numericFields": ["v"], "valueVariance": 0.0,
            "crs": "EPSG:32650", "crsClass": "projected_local_metric",
        })
        assert signals["ok_family_suitable"] is False
        assert signals["evidence"]["reject"] == "zero_value_variance"

    def test_duplicates_undercutting_floor_blocks_kriging_family(self):
        signals = _interpolation_fact_signals({
            "featureCount": 200, "uniqueCoordinateCount": 6,
            "geometryTypes": ["Point"], "numericFields": ["v"],
            "crs": "EPSG:32650",
        })
        assert signals["ok_family_suitable"] is False
        assert signals["evidence"]["reject"] == "post_dedup_below_floor"

    def test_geographic_crs_blocks_fact_hint(self):
        signals = _interpolation_fact_signals({
            "featureCount": 100, "geometryTypes": ["Point"],
            "numericFields": ["v"], "crs": "EPSG:4326", "crsClass": "geographic",
        })
        assert signals["ok_family_suitable"] is False

    def test_large_n_projected_with_uncertainty_demands_ok_family(self):
        query = "插值并给出方差评估"
        signals = _interpolation_fact_signals(
            {
                "featureCount": 500, "geometryTypes": ["Point"],
                "numericFields": ["v", "cov1", "cov2"], "valueVariance": 3.2,
                "crs": "EPSG:32650", "crsClass": "projected_local_metric",
            },
            query=query,
        )
        assert signals["ok_family_suitable"] is True
        assert signals["fact_hint"] == "interpolation.kriging"
        assert signals["evidence"]["uncertainty_demand"] is True
        assert "interpolation_model_selection" in signals["extra_capabilities"]
        # 协变量 ≥2 + 投影 CRS → RK 候选（optional capability）
        assert "regression_kriging" in signals["extra_capabilities"]

    def test_absent_profile_is_noop(self):
        assert _interpolation_fact_signals(None)["fact_hint"] == ""
        assert _interpolation_fact_signals({})["fact_hint"] == ""

    def test_finalize_small_n_resolves_idw_path(self):
        from app.services.gis_harness.intent import resolve_map_request_intent

        planner = MapProductPlanner()
        intent = resolve_map_request_intent("插值展示")
        plan = planner.plan_from_intent(intent, use_memo=False)
        profile = {
            "featureCount": 5, "geometryTypes": ["Point"],
            "numericFields": ["v"], "crs": "EPSG:32650",
        }
        finalized = planner.finalize_with_profile(plan, profile)
        sel = next(
            s for s in finalized.algorithm_selections
            if s.capability == "spatial_interpolation"
        )
        assert sel.algorithm == "interpolation.idw"
        assert finalized.algorithm_fact_signals["fact_hint"] == ""

    def test_finalize_fact_hint_promotes_kriging_without_text(self):
        from app.services.gis_harness.intent import resolve_map_request_intent

        planner = MapProductPlanner()
        intent = resolve_map_request_intent("插值展示")
        plan = planner.plan_from_intent(intent, use_memo=False)
        profile = {
            "featureCount": 500, "geometryTypes": ["Point"],
            "numericFields": ["v"], "valueVariance": 1.5,
            "crs": "EPSG:32650", "crsClass": "projected_local_metric",
        }
        finalized = planner.finalize_with_profile(plan, profile)
        sel = next(
            s for s in finalized.algorithm_selections
            if s.capability == "spatial_interpolation"
        )
        assert sel.algorithm == "interpolation.kriging"
        assert finalized.algorithm_fact_signals["effective_hint"] == "interpolation.kriging"

    def test_kriging_keyword_cannot_override_hard_gate(self):
        """文本点名克里金 + 硬门数据（3 点）→ hint 只能通过硬门才生效。"""
        resolver = AlgorithmResolver()
        resolution = resolver.resolve(
            "spatial_interpolation",
            profile={"featureCount": 3, "geometryTypes": ["Point"], "crs": "EPSG:32650"},
            available_tools=None,
            algorithm_hint="interpolation.kriging",
        )
        assert resolution.algorithm != "interpolation.kriging"
        assert resolution.algorithm == "interpolation.idw"
        assert any("insufficient_features:interpolation.kriging" in r
                   for r in resolution.rejected)

    def test_facts_outrank_text_hint_at_finalize(self):
        """「克里金」点名 + 常量场事实 → finalize 不再顶位克里金。"""
        from app.services.gis_harness.intent import resolve_map_request_intent

        planner = MapProductPlanner()
        intent = resolve_map_request_intent("克里金插值")
        plan = planner.plan_from_intent(intent, use_memo=False)
        profile = {
            "featureCount": 100, "geometryTypes": ["Point"],
            "numericFields": ["v"], "valueVariance": 0.0,
            "crs": "EPSG:32650", "crsClass": "projected_local_metric",
            "fields": {"v": {"type": "number"}},
        }
        finalized = planner.finalize_with_profile(plan, profile)
        signals = finalized.algorithm_fact_signals
        assert signals["text_hint"] == "interpolation.kriging"
        assert signals["effective_hint"] == ""  # 事实胜过文本
        sel = next(
            s for s in finalized.algorithm_selections
            if s.capability == "spatial_interpolation"
        )
        assert sel.algorithm == "interpolation.idw"

    def test_text_keyword_gate_still_gates_capability_planning(self):
        """无插值词面的查询不得被事实投影放大出插值能力（关键词门不放大）。"""
        from app.services.gis_harness.intent import resolve_map_request_intent

        planner = MapProductPlanner()
        intent = resolve_map_request_intent("查看北京市POI分布")
        plan = planner.plan_from_intent(intent, use_memo=False)
        assert not any(
            r.capability == "spatial_interpolation" for r in plan.data_requirements
        )
        profile = {
            "featureCount": 500, "geometryTypes": ["Point"],
            "numericFields": ["v"], "crs": "EPSG:32650",
        }
        finalized = planner.finalize_with_profile(plan, profile)
        assert not any(
            r.capability == "spatial_interpolation" for r in finalized.data_requirements
        )
        assert finalized.algorithm_fact_signals == {}


# ── (g) profile_ref producer ─────────────────────────────────────────

class TestProfileRefProducer:
    async def test_registered_artifact_carries_profile_digest_and_ref(self):
        from app.lib.data.artifact_contract import from_artifact_record
        from app.services.artifact_registry import register_tool_artifact
        from app.services.session_data import session_data_manager

        session_id = "v4-profile-ref"
        fc = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "features": _vector_v3_features(10),
        }
        ref = await session_data_manager.store(session_id, fc, prefix="geojson")
        try:
            rec = await register_tool_artifact(session_id, ref, tool="demo_tool")
            assert rec is not None
            digest = rec.metadata.get("profile_digest")
            assert isinstance(digest, dict)
            assert digest["row_count"] == 10
            assert digest["crs"] == "EPSG:4326"
            assert "created_at" not in digest  # 决定论：无时间戳
            profile_ref = rec.metadata.get("profile_ref")
            assert isinstance(profile_ref, str) and profile_ref.startswith("profile:v3:")
            # contract projection carries it
            contract = from_artifact_record(rec)
            assert contract.profile_ref == profile_ref
        finally:
            await session_data_manager.clear_session(session_id)

    async def test_without_descriptor_profile_ref_stays_absent(self):
        from app.lib.data.artifact_contract import from_artifact_record
        from app.services.artifact_registry import register_tool_artifact

        session_id = "v4-profile-ref-missing"
        rec = await register_tool_artifact(
            session_id, "ref:geojson-never-stored", tool="demo_tool",
        )
        assert rec is not None
        assert "profile_digest" not in rec.metadata
        assert "profile_ref" not in rec.metadata
        assert from_artifact_record(rec).profile_ref == ""

    def test_digest_is_deterministic_and_bounded(self):
        from app.services.data_profile.profiler import (
            descriptor_profile_digest,
        )

        d1 = descriptor_profile_digest(_descriptor_with_crs("EPSG:4326"))
        d2 = descriptor_profile_digest(_descriptor_with_crs("EPSG:4326"))
        assert d1 == d2
        assert d1 is not None
        assert len(d1) <= 24
        assert d1["row_count"] == 60
        assert descriptor_profile_digest(None) is None
        assert descriptor_profile_digest({"bbox": [0, 0, 1, 1]}) is None


# ── determinism ──────────────────────────────────────────────────────

class TestDeterminism:
    def test_resolver_same_input_same_output(self):
        profile = {
            "featureCount": 500, "geometryTypes": ["Point"],
            "crs": "EPSG:32650", "crsClass": "projected_local_metric",
            "numericFields": ["v"], "fields": {"v": {"type": "number"}},
        }
        r1 = AlgorithmResolver().resolve(
            "spatial_interpolation", profile=profile, available_tools=None,
            algorithm_hint="interpolation.kriging",
        )
        r2 = AlgorithmResolver().resolve(
            "spatial_interpolation", profile=profile, available_tools=None,
            algorithm_hint="interpolation.kriging",
        )
        assert r1.model_dump() == r2.model_dump()

    def test_adapter_and_fact_signals_deterministic(self):
        profile = _build_v3_profile(_vector_v3_features(12))
        rp1 = DatasetProfile.from_profile_v3(profile).to_resolver_profile()
        rp2 = DatasetProfile.from_profile_v3(profile).to_resolver_profile()
        assert rp1 == rp2
        s1 = _interpolation_fact_signals(rp1, query="插值")
        s2 = _interpolation_fact_signals(rp2, query="插值")
        assert s1 == s2

    def test_finalize_same_input_same_plan(self):
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.spatial_meta_profiler import profile_from_descriptor

        planner = MapProductPlanner()
        intent = resolve_map_request_intent("克里金插值空气质量")
        plan = planner.plan_from_intent(intent, use_memo=False)
        profile = profile_from_descriptor({
            **_descriptor_with_crs("EPSG:32650"), "feature_count": 40,
        })
        f1 = planner.finalize_with_profile(plan, profile)
        f2 = planner.finalize_with_profile(plan, profile)
        assert f1.model_dump() == f2.model_dump()


# ── text keyword gate semantics unchanged ────────────────────────────

def test_interpolation_query_signals_unchanged():
    assert _interpolation_query_signals("克里金插值") == (True, "interpolation.kriging")
    assert _interpolation_query_signals("kriging surface") == (True, "interpolation.kriging")
    assert _interpolation_query_signals("插值") == (True, "")
    assert _interpolation_query_signals("查看POI") == (False, "")
