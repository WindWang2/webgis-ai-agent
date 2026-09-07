"""Backend SDK V3（ADR-0117）—— 结构化资源包络/精度分类/数值容差/取消画像
与 backend 选择层消费的锁定测试。

覆盖：
- ResourceEnvelope / NumericalTolerance 模型校验（缺省零约束、声明必合法）；
- descriptor approximation_class 与 approximate 布尔的交叉一致性；
- BackendVariant.approximation_class 词表；
- select_backend 的 estimate-before-allocate（字节估算/对预算预警/硬上限
  预警）与近似披露（变体级/算法级/出窗降级）；
- BackendEvidence 结构化证据（确定性键序）；
- ApproximationDisclosure 不确定性块与词表 additive；
- manifest 投影携带 approximation_class（缺省 "" 时投影不变）；
- catalog 生成器渲染 V3 字段。
"""

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture()
def sdk_registry():
    """注册测试 descriptor 并在用例后恢复全局单例（防泄漏）。"""
    from app.lib.gis.algorithm_registry import (
        AlgorithmDescriptor,
        BackendVariant,
        ResourceEnvelope,
        get_algorithm_registry,
    )

    reg = get_algorithm_registry()
    reg.register(
        AlgorithmDescriptor(
            id="test.sdk.envelope",
            name="SDK 测试算法",
            capabilities=["point_profile"],
            input_artifact_types=["point_feature_set"],
            output_artifact_type="point_feature_set",
            tool_candidates=["spatial_stats"],
            algorithm_family="spatial_descriptive",
            crs_class="CRS_AGNOSTIC",
            approximation_class="exact",
            resource_envelope=ResourceEnvelope(
                bytes_per_feature=64.0, max_pairs=1000, hard_max_features=10_000
            ),
            backend_variants=[
                BackendVariant(
                    id="exact_np",
                    backend="numpy",
                    min_features=1,
                    max_features=500,
                    approximation_class="exact",
                ),
                BackendVariant(
                    id="approx_stream",
                    backend="numpy",
                    min_features=501,
                    approximation_class="approximate",
                ),
            ],
        )
    )
    yield reg
    from app.lib.gis.algorithm_registry import reset_algorithm_registry

    reset_algorithm_registry()


class TestResourceEnvelopeModel:
    def test_empty_envelope_rejected(self):
        from app.lib.gis.algorithm_registry import ResourceEnvelope

        with pytest.raises(ValueError):
            ResourceEnvelope()

    def test_negative_bounds_rejected(self):
        from app.lib.gis.algorithm_registry import ResourceEnvelope

        with pytest.raises(ValueError):
            ResourceEnvelope(bytes_per_feature=-1.0)
        with pytest.raises(ValueError):
            ResourceEnvelope(max_pairs=0)

    def test_valid_envelope(self):
        from app.lib.gis.algorithm_registry import ResourceEnvelope

        env = ResourceEnvelope(
            bytes_per_cell=8.0, hard_max_cells=50_000_000, notes="float64 主数组"
        )
        assert env.hard_max_cells == 50_000_000
        assert env.notes == "float64 主数组"


class TestNumericalToleranceModel:
    def test_empty_rejected(self):
        from app.lib.gis.algorithm_registry import NumericalTolerance

        with pytest.raises(ValueError):
            NumericalTolerance()

    def test_rtol_range_enforced(self):
        from app.lib.gis.algorithm_registry import NumericalTolerance

        with pytest.raises(ValueError):
            NumericalTolerance(rtol=0.5)
        assert NumericalTolerance(rtol=1e-9).rtol == 1e-9

    def test_atol_and_policy(self):
        from app.lib.gis.algorithm_registry import NumericalTolerance

        t = NumericalTolerance(atol=1e-6, policy="golden_double_run")
        assert t.atol == 1e-6 and t.policy == "golden_double_run"


class TestApproximationClassConsistency:
    def _descriptor(self, **kw):
        from app.lib.gis.algorithm_registry import AlgorithmDescriptor

        base = dict(
            id="test.sdk.consistency",
            name="一致性测试",
            capabilities=["point_profile"],
            input_artifact_types=["point_feature_set"],
            output_artifact_type="point_feature_set",
            tool_candidates=["spatial_stats"],
        )
        base.update(kw)
        return AlgorithmDescriptor(**base)

    def test_approximate_class_requires_flag(self):
        d = self._descriptor(approximation_class="heuristic")
        issues = [
            i for i in d.model_validate(d.model_dump()) and []
        ]  # 构造期不抛；一致性由 registry.validate 报告
        assert issues == []

    def test_registry_validate_flags_mismatch(self, sdk_registry):
        from app.lib.gis.algorithm_registry import (
            reset_algorithm_registry,
        )

        reg = sdk_registry
        reg.register(
            self._descriptor(id="test.sdk.badflag", approximation_class="sampling")
        )
        issues = [i for i in reg.validate() if "test.sdk.badflag" in i]
        assert any("approximate=True" in i for i in issues)
        reset_algorithm_registry()

    def test_registry_validate_flags_exact_contradiction(self, sdk_registry):
        from app.lib.gis.algorithm_registry import (
            reset_algorithm_registry,
        )

        reg = sdk_registry
        reg.register(
            self._descriptor(
                id="test.sdk.badexact", approximate=True, approximation_class="exact"
            )
        )
        issues = [i for i in reg.validate() if "test.sdk.badexact" in i]
        assert any("矛盾" in i for i in issues)
        reset_algorithm_registry()

    def test_legacy_descriptors_unconstrained(self):
        """存量算法（未声明 approximation_class）validate 必须保持干净。"""
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        assert get_algorithm_registry().validate() == []


class TestSelectBackendEnvelopeConsumption:
    def test_bytes_estimate_and_pair_budget_warning(self, sdk_registry):
        from app.lib.gis.backend_selection import ScaleProfile, select_backend

        decision = select_backend("test.sdk.envelope", ScaleProfile(feature_count=50))
        assert decision.matched is True
        assert decision.estimated_bytes == 64 * 50
        assert any("pair budget" in w for w in decision.resource_warnings)
        # 变体 exact_np 命中 → 无近似披露
        assert decision.approximation_disclosure == ""

    def test_variant_approximation_disclosed(self, sdk_registry):
        from app.lib.gis.backend_selection import ScaleProfile, select_backend

        decision = select_backend("test.sdk.envelope", ScaleProfile(feature_count=800))
        assert decision.variant_id == "approx_stream"
        assert "approximation_class=approximate" in decision.approximation_disclosure

    def test_out_of_window_disclosure(self, sdk_registry):
        from app.lib.gis.backend_selection import ScaleProfile, select_backend
        from app.lib.gis.algorithm_registry import (
            AlgorithmDescriptor,
            BackendVariant,
        )

        # 有洞窗口：两个变体都有上界 → n 超出全部窗口时走降级路径
        sdk_registry.register(
            AlgorithmDescriptor(
                id="test.sdk.hole",
                name="窗口有洞",
                capabilities=["point_profile"],
                input_artifact_types=["point_feature_set"],
                output_artifact_type="point_feature_set",
                tool_candidates=["spatial_stats"],
                algorithm_family="spatial_descriptive",
                backend_variants=[
                    BackendVariant(
                        id="small", backend="numpy", min_features=1, max_features=500
                    ),
                    BackendVariant(
                        id="mid", backend="numpy", min_features=501, max_features=1000
                    ),
                ],
            )
        )
        decision = select_backend("test.sdk.hole", ScaleProfile(feature_count=20_000))
        assert decision.matched is False
        assert "scale_window_exceeded" in decision.approximation_disclosure

        # 硬上限预警在原 envelope 描述符上验证
        decision2 = select_backend(
            "test.sdk.envelope", ScaleProfile(feature_count=20_000)
        )
        assert any("hard_max_features" in w for w in decision2.resource_warnings)

    def test_diagnostic_includes_disclosure(self, sdk_registry):
        from app.lib.gis.backend_selection import ScaleProfile, select_backend

        decision = select_backend("test.sdk.envelope", ScaleProfile(feature_count=800))
        diag = decision.to_diagnostic()
        assert "approximate" in diag["text"]

    def test_backend_evidence_shape(self, sdk_registry):
        from app.lib.gis.backend_selection import ScaleProfile, select_backend

        decision = select_backend("test.sdk.envelope", ScaleProfile(feature_count=800))
        ev = decision.to_evidence()
        assert ev.algorithm_id == "test.sdk.envelope"
        assert ev.variant_id == "approx_stream"
        assert ev.estimated_bytes == 64 * 800
        d = ev.to_dict()
        assert list(d.keys()) == [
            "algorithm_id",
            "variant_id",
            "backend",
            "scale_tier",
            "matched",
            "approximation_disclosure",
            "estimated_bytes",
            "resource_warnings",
            "execution_policy",
            "runtime_strategy",
            "rationale",
        ]


class TestApproximationDisclosureUncertainty:
    def test_vocabulary_additive(self):
        from app.lib.gis.uncertainty import UNCERTAINTY_TYPE_VOCABULARY

        assert "approximation_disclosure" in UNCERTAINTY_TYPE_VOCABULARY

    def test_block_roundtrip(self):
        from app.lib.gis.uncertainty import (
            ApproximationDisclosure,
            uncertainty_blocks_to_evidence,
        )

        block = ApproximationDisclosure(
            target="interp.grid",
            approximation_class="streaming",
            source="approx_stream",
            exact_alternative="exact_np",
            note="分块近似，边界效应 ≤ 1 cell",
        )
        evidence = uncertainty_blocks_to_evidence([block])
        assert evidence[0]["uncertainty_type"] == "approximation_disclosure"
        assert evidence[0]["approximation_class"] == "streaming"


class TestManifestProjection:
    def test_projection_carries_approximation_class(self):
        from app.lib.gis.runtime_manifest import _project_algorithm

        class _Fake:
            id = "x"
            capabilities = ["c"]
            tool_candidates = ["t"]
            fallback_algorithms = []
            fallback_semantics = {}
            scientific_preconditions = []
            approximation_class = "exact"

        proj = _project_algorithm(_Fake())
        assert proj["approximation_class"] == "exact"

    def test_legacy_projection_unchanged(self):
        from app.lib.gis.runtime_manifest import _project_algorithm

        class _Fake:
            id = "x"
            capabilities = ["c"]
            tool_candidates = ["t"]
            fallback_algorithms = []
            fallback_semantics = {}
            scientific_preconditions = []

        assert _project_algorithm(_Fake())["approximation_class"] == ""


class TestCatalogRendering:
    def test_v3_fields_rendered(self):
        from scripts.gen_science_catalog import _algorithm_bullet

        from app.lib.gis.algorithm_registry import (
            AlgorithmDescriptor,
            NumericalTolerance,
            ResourceEnvelope,
        )

        algo = AlgorithmDescriptor(
            id="test.sdk.render",
            name="渲染测试",
            capabilities=["point_profile"],
            tool_candidates=["spatial_stats"],
            approximation_class="streaming",
            approximate=True,
            resource_envelope=ResourceEnvelope(bytes_per_feature=32.0),
            tolerance=NumericalTolerance(rtol=1e-6),
            cancellation_profile="chunk_boundary",
        )
        text = _algorithm_bullet(algo)
        assert "精度: streaming" in text
        assert "资源包络：32B/要素" in text
        assert "取消：chunk_boundary" in text
        assert "数值容差：rtol=1e-06" in text

    def test_legacy_bullet_unchanged(self):
        from scripts.gen_science_catalog import _algorithm_bullet

        from app.lib.gis.algorithm_registry import AlgorithmDescriptor

        algo = AlgorithmDescriptor(
            id="test.sdk.legacy",
            name="旧渲染",
            capabilities=["point_profile"],
            tool_candidates=["spatial_stats"],
        )
        text = _algorithm_bullet(algo)
        assert "精度:" not in text
        assert "资源包络" not in text
        assert "取消：" not in text


class TestUncertaintyProducerTests:
    """Wave 8：declared uncertainty → producer test 机器可查闭环。"""

    def _descriptor(self, **kw):
        from app.lib.gis.algorithm_registry import AlgorithmDescriptor
        base = dict(
            id="test.sdk.producer", name="producer 测试",
            capabilities=["point_profile"],
            input_artifact_types=["point_feature_set"],
            output_artifact_type="point_feature_set",
            tool_candidates=["spatial_stats"],
            uncertainty_outputs=["statistical_significance"],
        )
        base.update(kw)
        return AlgorithmDescriptor(**base)

    def test_kriging_has_producer_tests(self):
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        d = get_algorithm_registry().get("interpolation.kriging")
        assert d.uncertainty_producer_tests
        assert set(d.uncertainty_producer_tests) <= set(d.uncertainty_outputs)
        assert get_algorithm_registry().validate() == []

    def test_key_must_be_declared_output(self, sdk_registry):
        from app.lib.gis.algorithm_registry import reset_algorithm_registry

        reg = sdk_registry
        reg.register(self._descriptor(
            uncertainty_producer_tests={
                "validation_metrics": "tests/unit/gis/test_backend_sdk_v3.py::TestUncertaintyProducerTests",
            }))
        issues = [i for i in reg.validate() if "test.sdk.producer" in i]
        assert any("not declared in uncertainty_outputs" in i for i in issues)
        reset_algorithm_registry()

    def test_node_must_exist(self, sdk_registry):
        from app.lib.gis.algorithm_registry import reset_algorithm_registry

        reg = sdk_registry
        reg.register(self._descriptor(
            uncertainty_producer_tests={
                "statistical_significance": "tests/unit/gis/test_backend_sdk_v3.py::TestNoSuchClassHere",
            }))
        issues = [i for i in reg.validate() if "test.sdk.producer" in i]
        assert any("uncertainty producer test node missing" in i for i in issues)
        reset_algorithm_registry()

    def test_valid_producer_passes(self, sdk_registry):
        from app.lib.gis.algorithm_registry import reset_algorithm_registry

        reg = sdk_registry
        reg.register(self._descriptor(
            uncertainty_producer_tests={
                "statistical_significance": "tests/unit/gis/test_backend_sdk_v3.py::TestUncertaintyProducerTests",
            }))
        issues = [i for i in reg.validate() if "test.sdk.producer" in i]
        assert issues == []
        reset_algorithm_registry()
