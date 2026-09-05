"""Spatial Science Platform VNext 契约骨架测试（ADR-0099）。

覆盖：参数契约 / 科学前置条件 / CRS 安全 / 不确定性类型 / 科学错误分类 /
方法出处 / descriptor VNext 校验 / resolver 科学门 / manifest v3 指纹 /
算法-工具参数一致性门。
"""
import pytest

from app.lib.gis.algorithm_registry import (
    AlgorithmDescriptor,
    AlgorithmRegistry,
    get_algorithm_registry,
)
from app.lib.gis.algorithm_resolver import AlgorithmResolver
from app.lib.gis.crs_safety import (
    classify_crs,
    crs_class_allows,
    recommend_metric_crs,
)
from app.lib.gis.method_references import METHOD_REFERENCES, reference_exists
from app.lib.gis.parameter_contracts import (
    ParameterContract,
    ParameterSpec,
    apply_contract,
    get_parameter_contract_registry,
    validate_parameters,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    IllConditionedSystem,
    InsufficientSamples,
    ResourceScaleMismatch,
    ScientificError,
)
from app.lib.gis.scientific_preconditions import (
    combine_verdicts,
    evaluate_precondition,
    precondition_exists,
)
from app.lib.gis.scientific_evidence import (
    FallbackRecord,
    build_evidence,
)
from app.lib.gis.uncertainty import (
    StatisticalSignificance,
    ValidationMetrics,
    uncertainty_blocks_to_evidence,
)

pytestmark = pytest.mark.unit


# ── 参数契约 ─────────────────────────────────────────────────────────
class TestParameterContracts:
    def test_builtin_contracts_registered(self):
        reg = get_parameter_contract_registry()
        for cid in ("buffer_analysis", "idw_interpolation", "kriging_interpolation"):
            assert reg.has(cid), cid
        assert reg.validate() == []

    def test_descriptor_refs_resolve(self):
        """parameter_contract_ref 悬空 = 死契约 —— 内建种子必须全部解析。"""
        reg = get_parameter_contract_registry()
        for aid in get_algorithm_registry().all_ids:
            algo = get_algorithm_registry().get(aid)
            if algo.parameter_contract_ref:
                assert reg.has(algo.parameter_contract_ref), aid

    def test_validate_fills_defaults_and_flags(self):
        contract = get_parameter_contract_registry().get("idw_interpolation")
        result = validate_parameters(contract, {"value_field": "pm25", "power": 99})
        assert result.issues and "maximum" in result.issues[0]
        ok = validate_parameters(contract, {"value_field": "pm25"})
        assert ok.issues == []
        assert ok.normalized["power"] == 2
        assert ok.normalized["resolution"] == 8
        assert "resolution" in ok.applied_defaults
        assert "power" in ok.applied_defaults

    def test_apply_contract_raises_machine_readable(self):
        with pytest.raises(ValueError, match="parameter_contract_violation"):
            apply_contract("buffer_analysis", {})   # distance required

    def test_apply_contract_normalizes(self):
        out = apply_contract("buffer_analysis", {"distance": 500, "unit": "km"})
        assert out == {"distance": 500.0, "unit": "km"}

    def test_spec_validators(self):
        with pytest.raises(Exception):
            ParameterSpec(name="bad name", type="number")
        with pytest.raises(Exception):
            ParameterSpec(name="x", type="number", unit="furlongs")
        with pytest.raises(Exception):
            ParameterSpec(name="x", type="enum", enum_values=[])
        with pytest.raises(Exception):
            ParameterSpec(name="x", type="number", required=True, default=1)
        with pytest.raises(Exception):
            ParameterSpec(name="x", type="integer", minimum=10, maximum=1)

    def test_duplicate_contract_rejected(self):
        reg = get_parameter_contract_registry()
        with pytest.raises(ValueError, match="duplicate"):
            reg.register(reg.get("buffer_analysis"))


# ── CRS 安全 ─────────────────────────────────────────────────────────
class TestCRSSafety:
    def test_classify_common(self):
        assert classify_crs("EPSG:4326") == "geographic"
        assert classify_crs("EPSG:4490") == "geographic"
        assert classify_crs("EPSG:3857") == "projected"
        assert classify_crs("EPSG:32650") == "projected_local_metric"
        assert classify_crs("EPSG:32717") == "projected_local_metric"
        assert classify_crs("EPSG:3413") == "projected_local_metric"
        assert classify_crs("") == "unknown"
        assert classify_crs(None) == "unknown"
        assert classify_crs("not a crs ??") == "unknown"

    def test_classify_cached_and_repeatable(self):
        assert classify_crs("EPSG:4326") == classify_crs("EPSG:4326")

    def test_recommend_metric(self):
        assert recommend_metric_crs([116.3, 39.9, 116.5, 40.0]) == "EPSG:32650"
        assert recommend_metric_crs([20.0, -33.0, 21.0, -33.5]) == "EPSG:32734"
        assert recommend_metric_crs([0.0, 89.0, 1.0, 89.5]) == "EPSG:3413"
        assert recommend_metric_crs([0.0, -89.0, 1.0, -89.5]) == "EPSG:3031"
        assert recommend_metric_crs(None) == ""

    def test_crs_class_allows_matrix(self):
        # 度数数据 vs 投影要求 → 拒
        assert not crs_class_allows("PROJECTED_REQUIRED", "geographic")
        assert not crs_class_allows("LOCAL_METRIC_REQUIRED", "geographic")
        # 3857 是投影但不是局部度量
        assert crs_class_allows("PROJECTED_REQUIRED", "projected")
        assert not crs_class_allows("LOCAL_METRIC_REQUIRED", "projected")
        assert crs_class_allows("LOCAL_METRIC_REQUIRED", "projected_local_metric")
        # 地理 OK / GEODESIC / AGNOSTIC 全放行；unknown 永远放行
        for cls in ("CRS_AGNOSTIC", "GEOGRAPHIC_OK", "GEODESIC", "RASTER_GRID"):
            assert crs_class_allows(cls, "geographic")
        assert crs_class_allows("PROJECTED_REQUIRED", "unknown")


# ── 科学前置条件 ─────────────────────────────────────────────────────
class TestPreconditions:
    def test_registry_ids_resolve(self):
        for pid in ("numeric_field_required", "projected_crs_required",
                    "min_temporal_observations:8", "min_temporal_observations:12",
                    "raster_band_required:2", "band_semantics_required",
                    "point_support_required", "min_numeric_samples:20",
                    "nonzero_variance_required", "positive_weights_required"):
            assert precondition_exists(pid), pid
        assert not precondition_exists("no_such_precondition")
        assert not precondition_exists("min_temporal_observations:0")

    def test_projected_crs_verdicts(self):
        geo = evaluate_precondition(
            "projected_crs_required", {"crs": "EPSG:4326", "bbox": [116, 39, 117, 40]})
        assert geo.verdict == "REQUIRES_TRANSFORM"
        assert "EPSG:32650" in geo.transform_hint
        utm = evaluate_precondition(
            "projected_crs_required", {"crs": "EPSG:32650"})
        assert utm.verdict == "PASS"
        unknown = evaluate_precondition("projected_crs_required", {})
        assert unknown.verdict == "PASS"   # 未知 ≠ 不满足

    def test_temporal_soft_warning_band(self):
        few = evaluate_precondition(
            "min_temporal_observations:8", {"temporalObservationCount": 5})
        assert few.verdict == "PASS_WITH_WARNINGS"
        two = evaluate_precondition(
            "min_temporal_observations:8", {"temporalObservationCount": 2})
        assert two.verdict == "INSUFFICIENT_DATA"
        enough = evaluate_precondition(
            "min_temporal_observations:8", {"temporalObservationCount": 12})
        assert enough.verdict == "PASS"

    def test_point_support(self):
        ok = evaluate_precondition(
            "point_support_required", {"geometryTypes": ["Point"]})
        assert ok.verdict == "PASS"
        bad = evaluate_precondition(
            "point_support_required", {"geometryTypes": ["Polygon"]})
        assert bad.verdict == "INVALID_METHOD"

    def test_band_semantics(self):
        empty = evaluate_precondition("band_semantics_required", {"bandSemantics": ["", ""]})
        assert empty.verdict == "REQUIRES_TRANSFORM"
        good = evaluate_precondition(
            "band_semantics_required", {"bandSemantics": ["red", "nir"]})
        assert good.verdict == "PASS"

    def test_combine_worst_wins(self):
        from app.lib.gis.scientific_preconditions import PreconditionResult
        rs = [
            PreconditionResult("a", "PASS"),
            PreconditionResult("b", "PASS_WITH_WARNINGS"),
            PreconditionResult("c", "INSUFFICIENT_DATA"),
        ]
        assert combine_verdicts(rs) == "INSUFFICIENT_DATA"
        assert combine_verdicts(rs[:2]) == "PASS_WITH_WARNINGS"


# ── 不确定性 / 证据 / 错误 ───────────────────────────────────────────
class TestUncertaintyAndEvidence:
    def test_significance_validation(self):
        with pytest.raises(Exception):
            StatisticalSignificance(target="x", p_value=1.5)
        sig = StatisticalSignificance(
            target="morans_i", statistic_name="Moran's I",
            statistic_value=0.42345678, p_value=0.01, method="permutation",
            permutations=999, multiple_testing="")
        ev = sig.to_evidence()
        assert ev["statistic_value"] == 0.423457   # 6 位收敛
        assert ev["p_value"] == 0.01

    def test_blocks_bounded_dedup(self):
        v1 = ValidationMetrics(target="surface", rmse=1.23456789)
        v2 = ValidationMetrics(target="surface", rmse=9.9)   # 同 target 去重
        out = uncertainty_blocks_to_evidence([v1, v2])
        assert len(out) == 1
        assert out[0]["rmse"] == 1.234568

    def test_evidence_builder(self):
        algo = get_algorithm_registry().get("interpolation.kriging")
        ev = build_evidence(
            algo, tool="kriging_interpolation",
            parameters_applied={"variogram_model": "spherical", "neighbors": 12},
            input_facts={"feature_count": 42, "crs": "EPSG:32650"},
            uncertainty=[],
            validation=ValidationMetrics(target="surface", rmse=0.5),
            fallback=FallbackRecord(
                occurred=True, from_element="interpolation.kriging",
                to_element="interpolation.idw", semantics="approximation"),
            seed=42,
        )
        assert ev["algorithm"] == "interpolation.kriging"
        assert ev["inputs"]["crs_class"] == "projected_local_metric"
        assert ev["fallback"]["semantics"] == "approximation"
        assert ev["validation"]["rmse"] == 0.5
        assert ev["reproducibility"]["seed"] == 42

    def test_scientific_errors_are_valueerrors_with_codes(self):
        for exc_cls, code in [
            (InsufficientSamples, "INSUFFICIENT_SAMPLES"),
            (DegenerateData, "DEGENERATE_DATA"),
            (IllConditionedSystem, "ILL_CONDITIONED_SYSTEM"),
        ]:
            exc = exc_cls("detail")
            assert isinstance(exc, ValueError)      # dispatch 映射兼容
            assert isinstance(exc, ScientificError)
            assert exc.scientific_code == code
            assert exc.to_dict()["scientific_code"] == code
        rsm = ResourceScaleMismatch("too big", estimated="8GB", limit="4GB")
        assert rsm.to_dict()["estimated"] == "8GB"


# ── 方法出处 ─────────────────────────────────────────────────────────
class TestMethodReferences:
    def test_core_references_present(self):
        for rid in ("moran1950", "geary1954", "getis_ord1992", "anselin1995",
                    "matheron1963", "shepard1968", "horn1981", "tarboton1997",
                    "mann1945", "sen1968", "ripley1976", "rouse1974",
                    "dijkstra1959", "hwang_yoon1981"):
            assert reference_exists(rid), rid
            assert len(METHOD_REFERENCES[rid].citation) > 20

    def test_unknown_reference(self):
        assert not reference_exists("definitely_not_a_paper_2099")


# ── descriptor VNext 校验 ────────────────────────────────────────────
class TestDescriptorVNextValidation:
    def _registry(self, *algos):
        reg = AlgorithmRegistry()
        for a in algos:
            reg.register(a)
        return reg

    def test_bad_method_reference_flagged(self):
        reg = self._registry(AlgorithmDescriptor(
            id="x.bad_ref", name="x", capabilities=["poi_query"],
            method_references=["nope2099"]))
        assert any("method reference" in i for i in reg.validate())

    def test_bad_precondition_flagged(self):
        reg = self._registry(AlgorithmDescriptor(
            id="x.bad_pre", name="x", capabilities=["poi_query"],
            scientific_preconditions=["made_up_condition"]))
        assert any("scientific precondition" in i for i in reg.validate())

    def test_bad_uncertainty_output_flagged(self):
        reg = self._registry(AlgorithmDescriptor(
            id="x.bad_unc", name="x", capabilities=["poi_query"],
            uncertainty_outputs=["vibes"]))
        assert any("uncertainty output" in i for i in reg.validate())

    def test_fallback_semantics_rules(self):
        # 缺声明
        reg = self._registry(AlgorithmDescriptor(
            id="x.fb1", name="x", capabilities=["poi_query"],
            fallback_algorithms=["profile.spatial.stats"]))
        assert any("缺科学等价性声明" in i for i in reg.validate())
        # not_allowed 却可自动回退 → 矛盾
        reg2 = self._registry(AlgorithmDescriptor(
            id="x.fb2", name="x", capabilities=["poi_query"],
            fallback_algorithms=["profile.spatial.stats"],
            fallback_semantics={"profile.spatial.stats": "not_allowed"}))
        assert any("not_allowed" in i for i in reg2.validate())
        # 键不在 fallback 列表
        reg3 = self._registry(AlgorithmDescriptor(
            id="x.fb3", name="x", capabilities=["poi_query"],
            fallback_semantics={"profile.spatial.stats": "equivalent"}))
        assert any("不在 fallback_algorithms" in i for i in reg3.validate())

    def test_seed_policy_consistency(self):
        bad = AlgorithmDescriptor(
            id="x.seed", name="x", capabilities=["poi_query"],
            deterministic=False, random_seed_policy="deterministic")
        reg = self._registry(bad)
        assert any("种子策略" in i for i in reg.validate())
        good = AlgorithmDescriptor(
            id="x.seed2", name="x", capabilities=["poi_query"],
            deterministic=False, random_seed_policy="none")
        reg2 = self._registry(good)
        assert not any("种子策略" in i for i in reg2.validate())

    def test_production_requirements(self):
        # PRODUCTION 无契约/出处/测试 → 三连 issue
        reg = self._registry(AlgorithmDescriptor(
            id="x.prod", name="x", capabilities=["poi_query"],
            tool_candidates=["spatial_stats"],
            scientific_status="PRODUCTION"))
        issues = reg.validate()
        assert any("PRODUCTION 需要参数契约" in i for i in issues)
        assert any("PRODUCTION 需要方法出处" in i for i in issues)
        assert any("PRODUCTION 需要 conformance tests" in i for i in issues)

    def test_deprecated_needs_fallback(self):
        reg = self._registry(AlgorithmDescriptor(
            id="x.dep", name="x", capabilities=["poi_query"],
            scientific_status="DEPRECATED"))
        assert any("DEPRECATED" in i for i in reg.validate())

    def test_seed_vnext_fields_clean(self):
        """内建 52+ 算法在新校验下零 issue（fallback 语义已补齐）。"""
        reg = get_algorithm_registry()
        issues = reg.validate()
        assert issues == [], issues

    def test_conformance_test_file_existence_checked(self):
        reg = self._registry(AlgorithmDescriptor(
            id="x.conf", name="x", capabilities=["poi_query"],
            conformance_tests=["tests/unit/gis/no_such_file_xyz.py::test_a"]))
        assert any("conformance test file missing" in i for i in reg.validate())


# ── resolver 科学门 ──────────────────────────────────────────────────
class TestResolverScientificGates:
    def _resolver(self, *algos):
        reg = AlgorithmRegistry()
        for a in algos:
            reg.register(a)
        return AlgorithmResolver(algorithms=reg)

    def test_crs_class_rejection_with_transform_hint(self):
        resolver = self._resolver(AlgorithmDescriptor(
            id="x.metric_only", name="x", capabilities=["poi_query"],
            tool_candidates=["spatial_stats"], crs_class="PROJECTED_REQUIRED"))
        res = resolver.resolve(
            "poi_query",
            profile={"geometryTypes": ["Point"], "crs": "EPSG:4326",
                     "bbox": [116.3, 39.9, 116.4, 39.95]},
            available_tools={"spatial_stats"})
        assert res.status == "unavailable"
        assert any("crs_class_mismatch" in r for r in res.rejected)
        assert res.required_transformations == ["reproject to EPSG:32650"]

    def test_crs_class_pass_with_metric(self):
        resolver = self._resolver(AlgorithmDescriptor(
            id="x.metric_ok", name="x", capabilities=["poi_query"],
            tool_candidates=["spatial_stats"], crs_class="PROJECTED_REQUIRED"))
        res = resolver.resolve(
            "poi_query",
            profile={"geometryTypes": ["Point"], "crs": "EPSG:32650"},
            available_tools={"spatial_stats"})
        assert res.status == "resolved"

    def test_precondition_warning_nonblocking(self):
        resolver = self._resolver(AlgorithmDescriptor(
            id="x.trend", name="x", capabilities=["poi_query"],
            tool_candidates=["temporal_trend"],
            scientific_preconditions=["min_temporal_observations:8"]))
        res = resolver.resolve(
            "poi_query",
            profile={"temporalObservationCount": 5},
            available_tools={"temporal_trend"})
        assert res.status == "resolved"
        assert res.scientific_warnings and "证据不足" in res.scientific_warnings[0]

    def test_precondition_rejection_insufficient(self):
        resolver = self._resolver(AlgorithmDescriptor(
            id="x.trend2", name="x", capabilities=["poi_query"],
            tool_candidates=["temporal_trend"],
            scientific_preconditions=["min_temporal_observations:8"]))
        res = resolver.resolve(
            "poi_query",
            profile={"temporalObservationCount": 2},
            available_tools={"temporal_trend"})
        assert res.status == "unavailable"
        assert any("scientific_precondition" in r for r in res.rejected)

    def test_fallback_semantics_in_trail(self):
        """算法级 fallback 的 trail 必须携带科学等价性分类。"""
        resolver = self._resolver(
            AlgorithmDescriptor(
                id="x.primary", name="x", capabilities=["poi_query"],
                tool_candidates=["primary_tool"], min_features=100,
                fallback_algorithms=["x.secondary"],
                fallback_semantics={"x.secondary": "approximation"}),
            AlgorithmDescriptor(
                id="x.secondary", name="y", capabilities=["point_profile"],
                tool_candidates=["secondary_tool"]),
        )
        res = resolver.resolve(
            "poi_query",
            profile={"geometryTypes": ["Point"], "featureCount": 5},
            available_tools={"primary_tool", "secondary_tool"})
        assert res.status == "resolved"
        assert res.algorithm == "x.secondary"
        assert res.fallback_trail[0].semantics == "approximation"

    def test_undeclared_fields_keep_behavior(self):
        """不声明新字段的算法：行为逐位不变（回归保护）。"""
        resolver = self._resolver(AlgorithmDescriptor(
            id="x.plain", name="x", capabilities=["poi_query"],
            tool_candidates=["spatial_stats"]))
        res = resolver.resolve(
            "poi_query",
            profile={"geometryTypes": ["Point"], "crs": "EPSG:4326",
                     "featureCount": 10},
            available_tools={"spatial_stats"})
        assert res.status == "resolved"
        assert res.scientific_warnings == []
        assert res.required_transformations == []


# ── manifest v3 指纹 ─────────────────────────────────────────────────
class TestManifestV3:
    def test_projection_carries_science_fields(self):
        from app.lib.gis.runtime_manifest import (
            MANIFEST_VERSION, get_runtime_manifest,
        )
        assert MANIFEST_VERSION == 3
        manifest = get_runtime_manifest()
        kriging = manifest.algorithms.get("interpolation.kriging")
        assert kriging is not None
        assert "crs_class" in kriging
        assert "parameter_contract_ref" in kriging
        assert "fallback_semantics" in kriging
        assert "parameter_contracts" in {
            k for k in manifest.__dict__
        } or manifest.parameter_contracts
        assert manifest.parameter_contracts.get("kriging_interpolation", {}).get(
            "version") == 2  # v2: method enum [ordinary, universal]
        assert "variogram_model" in manifest.parameter_contracts[
            "kriging_interpolation"]["parameters"]
        assert "method" in manifest.parameter_contracts[
            "kriging_interpolation"]["parameters"]

    def test_fingerprint_stable_and_sensitive(self):
        from app.lib.gis.runtime_manifest import (
            compile_runtime_manifest as _compile,
            get_runtime_manifest,
        )
        m1 = get_runtime_manifest()
        assert len(m1.fingerprint) == 64
        assert get_runtime_manifest().fingerprint == m1.fingerprint
        # 同一进程内重复编译指纹一致（确定性）
        fresh = _compile()
        assert fresh.fingerprint == m1.fingerprint


# ── 算法/工具参数一致性门（§43 parity）────────────────────────────────
class TestParameterParityGate:
    def test_real_registry_clean(self):
        from app.services.gis_harness.registry_validation import (
            validate_algorithm_tool_parameter_parity,
        )
        issues = validate_algorithm_tool_parameter_parity()
        assert issues == [], issues

    def test_detects_contract_tool_mismatch(self):
        from app.services.gis_harness.registry_validation import (
            validate_algorithm_tool_parameter_parity,
        )
        contracts = get_parameter_contract_registry()
        broken = ParameterContract(
            id="broken_parity_test",
            parameters=[ParameterSpec(
                name="definitely_not_in_any_schema", type="number",
                required=True)],
        )
        contracts.register(broken)
        reg = get_algorithm_registry()
        reg.register(AlgorithmDescriptor(
            id="parity.probe", name="parity", capabilities=["poi_query"],
            tool_candidates=["spatial_stats"],
            parameter_contract_ref="broken_parity_test"))
        try:
            issues = validate_algorithm_tool_parameter_parity()
            assert any("definitely_not_in_any_schema" in i for i in issues)
        finally:
            del contracts._by_id["broken_parity_test"]
            del reg._by_id["parity.probe"]
            reg._by_capability.get("poi_query", []).remove("parity.probe")
            reg._tool_to_capability_cache = None
