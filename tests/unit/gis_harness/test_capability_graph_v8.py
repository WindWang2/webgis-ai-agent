"""Harness V8 统一能力运行时测试（ADR-0137）。

覆盖：
- V8.1 图结构：指纹缓存零重建（§28 结构证明）、词表封闭、validate。
- V8.2 Model 一等实体：capability → model 候选查询。
- V8.3 资格引擎：eligible/ineligible/degraded + 可解释 reasons。
- V8.4 ExecutionEstimate：basis 诚实披露（不把猜测伪装成测量）。
- V8.6 候选规划：资格过滤 + 成本排序 + 确定性 tie-break。
- §29 场景切片：Case 1（学校分布多候选）/ Case 2（建筑提取模型链）/
  Case 4（失败反馈罚分改变排序）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.capability_graph import (
    GRAPH_KINDS,
    build_capability_graph,
    GRAPH_RELATIONS,
    get_capability_graph,
    graph_build_count_for_tests,
    reset_capability_graph,
    validate_graph,
)
from app.services.gis_harness.candidate_planner_v8 import (
    plan_candidates_v8,
    reliability_penalty_v8,
)
from app.services.gis_harness.qualification_v8 import (
    QualificationContext,
    QualificationStatus,
    estimate_for_node,
    qualify_model_for_input,
    qualify_node,
)


@pytest.fixture(scope="module", autouse=True)
def _seeded_modelops():
    """种子模型注册（图 build 前执行；幂等）。"""
    from app.services.modelops.config import ModelOpsSettings
    from app.services.modelops.providers.base import ProviderRegistry
    from app.services.modelops.registry import ModelRegistryStore
    from app.services.modelops.seeds import seed_providers, seed_registry

    pr = ProviderRegistry()
    seed_providers(pr)
    store = ModelRegistryStore(ModelOpsSettings.load())
    seed_registry(store, pr)
    reset_capability_graph()
    yield
    reset_capability_graph()


class TestGraphStructure:
    """V8.1：图结构 + 缓存纪律。"""

    def test_graph_builds_with_all_core_kinds(self) -> None:
        g = get_capability_graph()
        kinds = {n.kind for n in g._nodes.values()}
        assert {"capability", "algorithm", "tool", "artifact_type"} <= kinds
        assert g.node_count > 300
        assert g.edge_count > 500

    def test_same_fingerprint_zero_rebuild(self) -> None:
        """§28 结构证明：registry 指纹不变 → 零重建（同实例）。"""
        g1 = get_capability_graph()
        before = graph_build_count_for_tests()
        g2 = get_capability_graph()
        assert g2 is g1
        assert graph_build_count_for_tests() == before

    def test_vocabularies_closed(self) -> None:
        g = get_capability_graph()
        for node in g._nodes.values():
            assert node.kind in GRAPH_KINDS
        for edge in g._edges:
            assert edge.relation in GRAPH_RELATIONS

    def test_validate_graph_no_errors(self) -> None:
        issues = validate_graph(get_capability_graph())
        errs = [i.to_dict() for i in issues if i.severity == "error"]
        assert errs == [], errs


class TestModelFirstClassEntity:
    """V8.2：Model 是可检索的能力实体（#1212 验收锚点）。"""

    def test_models_for_image_segmentation(self) -> None:
        g = get_capability_graph()
        models = g.models_for_capability("model_image_segmentation")
        ids = [m.id for m in models]
        assert any(i.endswith("tiny-landcover-seg@1.0.0") for i in ids)
        assert any(i.endswith("tiny-promptable-seg@1.0.0") for i in ids)

    def test_model_node_carries_compatibility_extras(self) -> None:
        g = get_capability_graph()
        models = g.models_for_capability("model_image_segmentation")
        m = next(x for x in models if "tiny-landcover-seg" in x.id)
        assert m.extras["input_bands"] >= 1
        assert m.extras["task_types"]
        assert "min_m_per_px" in m.extras  # resolution range 投影在场

    def test_statistical_vs_model_vocabulary_split(self) -> None:
        """B-10 拆分：统计分割与模型分割是不同 capability。"""
        g = get_capability_graph()
        assert g.has("capability", "model_image_segmentation")
        assert g.has("capability", "image_segmentation")  # k-means 统计法仍在
        assert g.node("capability:model_image_segmentation") is not None


class TestQualification:
    """V8.3：统一资格判断（禁 bool，结论可解释）。"""

    def _model_node(self, model_prefix: str):
        g = get_capability_graph()
        for m in g.nodes_by_kind("model"):
            if model_prefix in m.id:
                return m
        raise AssertionError(f"model {model_prefix} not in graph")

    def test_bands_mismatch_ineligible_with_reason(self) -> None:
        node = self._model_node("tiny-landcover-seg")
        ctx = QualificationContext(raster_bands=1, resolution_m_per_px=10.0)
        result = qualify_model_for_input(node, ctx)
        assert result.status == QualificationStatus.INELIGIBLE
        bands = [r for r in result.reasons if r.check == "raster_bands"]
        assert bands and "bands" in bands[0].observed

    def test_compatible_input_eligible(self) -> None:
        node = self._model_node("tiny-landcover-seg")
        ctx = QualificationContext(
            raster_bands=int(node.extras["input_bands"]),
            resolution_m_per_px=1.0,
        )
        result = qualify_model_for_input(node, ctx)
        assert result.status == QualificationStatus.ELIGIBLE, result.to_dict()

    def test_geographic_crs_resolution_unknown_degraded(self) -> None:
        """地理 CRS（度）→ resolution unknown（degraded，不误判 incompatible）。"""
        node = self._model_node("tiny-landcover-seg")
        ctx = QualificationContext(
            raster_bands=int(node.extras["input_bands"]),
            resolution_m_per_px=0.0,  # 0 = 未知（R1-C3 口径）
        )
        result = qualify_model_for_input(node, ctx)
        assert result.status == QualificationStatus.DEGRADED
        unknown = [r for r in result.reasons if r.check == "resolution_unknown"]
        assert unknown  # 度量未知如实披露

    def test_latency_constraint_on_tool(self) -> None:
        g = get_capability_graph()
        slow_tool = next(
            (n for n in g.nodes_by_kind("tool")
             if str(n.extras.get("latency_class", "")) == "slow"), None)
        if slow_tool is None:
            pytest.skip("no slow tool in live registry")
        ctx = QualificationContext(max_latency_class="fast")
        result = qualify_node(slow_tool, ctx)
        assert result.status == QualificationStatus.INELIGIBLE
        assert any(r.check == "latency_constraint" for r in result.reasons)


class TestExecutionEstimate:
    """V8.4：basis 诚实披露。"""

    def test_every_dimension_has_basis(self) -> None:
        g = get_capability_graph()
        tool = g.nodes_by_kind("tool")[0]
        est = estimate_for_node(tool)
        for dim in ("cpu", "memory", "latency_class", "gpu"):
            assert est.basis.get(dim) in ("measured", "declared", "estimated", "unknown")
        assert 0.0 <= est.confidence <= 1.0

    def test_model_estimate_gpu_declared(self) -> None:
        g = get_capability_graph()
        models = g.nodes_by_kind("model")
        if not models:
            pytest.skip("no models registered")
        est = estimate_for_node(models[0])
        assert est.basis["latency_class"] == "declared"


class TestCandidatePlanner:
    """V8.6：资格过滤 + 成本排序 + 确定性。"""

    def test_case1_school_distribution_multi_candidates(self) -> None:
        """§29 Case 1：点分布意图 → 多可视化候选（不硬编码「学校=热力图」）。"""
        ctx = QualificationContext(
            geometry_kinds=["Point"], feature_count=200, max_latency_class="")
        plan = plan_candidates_v8("kde_density", ctx)
        assert plan.candidates, plan.to_dict()
        tool_ids = [c.id for c in plan.candidates if c.kind == "tool"]
        assert {"kde_surface", "kde_contours", "heatmap_data"} & set(tool_ids)

    def test_case2_building_extraction_model_chain(self) -> None:
        """§29 Case 2：遥感建筑提取 → 模型候选（兼容性过滤参与裁决）。"""
        ctx = QualificationContext(raster_bands=3, resolution_m_per_px=1.0)
        plan = plan_candidates_v8("model_image_segmentation", ctx)
        model_ids = [c.id for c in plan.candidates if c.kind == "model"]
        assert any(i.endswith("tiny-landcover-seg@1.0.0") for i in model_ids)

    def test_case2_bands_incompatible_excluded_with_reason(self) -> None:
        ctx = QualificationContext(raster_bands=1, resolution_m_per_px=1.0)
        plan = plan_candidates_v8("model_image_segmentation", ctx)
        excluded_ids = [e["id"] for e in plan.excluded]
        assert any(i.endswith("tiny-landcover-seg@1.0.0") for i in excluded_ids)
        entry = next(e for e in plan.excluded
                     if e["id"].endswith("tiny-landcover-seg@1.0.0"))
        assert entry["qualification"]["status"] == "ineligible"
        assert entry["qualification"]["reasons"]

    def test_case4_failure_feedback_penalizes_ranking(self) -> None:
        """§29 Case 4：模型失败 → 可靠性罚分改变候选排序。"""
        from app.services.gis_harness.recovery_ledger import (
            get_recovery_ledger,
            reset_recovery_ledger_for_tests,
        )

        reset_recovery_ledger_for_tests()
        ledger = get_recovery_ledger()
        try:
            # 模拟最优候选（fast 工具）连续失败
            for _ in range(3):
                ledger.record_failure(
                    "sess-v8-test", "tool:heatmap_data", "timeout")
            assert reliability_penalty_v8("tool:heatmap_data", "sess-v8-test") > 0.5
            ctx = QualificationContext()
            plan = plan_candidates_v8(
                "kde_density", ctx, session_id="sess-v8-test")
            penalized = [c for c in plan.candidates if c.id == "heatmap_data"]
            if penalized:
                assert penalized[0].reliability_penalty > 0.5
                # 有罚分者不得排在零罚分者之前（同分按 id 亦在后）
                top = plan.candidates[0]
                assert top.reliability_penalty <= penalized[0].reliability_penalty
        finally:
            reset_recovery_ledger_for_tests()

    def test_deterministic_ordering(self) -> None:
        ctx = QualificationContext(raster_bands=3, resolution_m_per_px=1.0)
        p1 = plan_candidates_v8("model_image_segmentation", ctx)
        p2 = plan_candidates_v8("model_image_segmentation", ctx)
        assert [c.to_dict() for c in p1.candidates] == \
               [c.to_dict() for c in p2.candidates]

    def test_unknown_capability_excluded_honestly(self) -> None:
        plan = plan_candidates_v8(
            "no_such_capability", QualificationContext())
        assert plan.candidates == []
        assert plan.excluded and \
            plan.excluded[0]["reason"] == "capability_not_in_graph"


class TestReviewAFixes:
    """Review A（phase-c）MAJOR 修复的回归锁定。"""

    def test_ra1_models_query_excludes_tools(self) -> None:
        """models_for_capability 只返回 model 节点（RA-1）。"""
        g = get_capability_graph()
        # image_segmentation 有 13 个 tool 直连 implements —— 模型面必须为空
        # 或仅含 model kind。
        models = g.models_for_capability("image_segmentation")
        assert all(m.kind == "model" for m in models), \
            [m.key for m in models if m.kind != "model"]

    def test_ra2_no_duplicate_candidates(self) -> None:
        """plan_candidates_v8 候选无重复（kind:id 唯一）（RA-2）。"""
        ctx = QualificationContext(raster_bands=3, resolution_m_per_px=1.0)
        for cap in ("model_image_segmentation", "image_segmentation",
                    "kde_density"):
            plan = plan_candidates_v8(cap, ctx)
            keys = [f"{c.kind}:{c.id}" for c in plan.candidates]
            assert len(keys) == len(set(keys)), (cap, keys)

    def test_ra3_cross_scope_model_no_false_duplicate(self, tmp_path) -> None:
        """跨 owner scope 同名模型不触发 duplicate_identity（RA-3）。"""
        from app.lib.gis.capability_registry import reset_capability_registry
        from app.services.modelops.config import ModelOpsSettings
        from app.services.modelops.providers.base import ProviderRegistry
        from app.services.modelops.registry import ModelRegistryStore
        from app.services.modelops.seeds import seed_descriptors, seed_providers

        reset_capability_graph()
        settings = ModelOpsSettings(registry_dir=tmp_path)
        # 图构建读 ModelOpsSettings.load() 默认目录 —— 指向 tmp 注册表
        monkeypatch_or_skip = None
        from app.services.modelops.config import ModelOpsSettings as _MS
        orig_load = _MS.load
        _MS.load = staticmethod(lambda *a, **k: settings)
        pr = ProviderRegistry()
        seed_providers(pr)
        store = ModelRegistryStore(settings)
        desc = next(d for d in seed_descriptors()
                    if d.model_id == "tiny-landcover-seg")
        store.register(desc, owner_scope={"session_id": "scope-a"},
                       registered_by="test")
        store2 = ModelRegistryStore(settings)
        store2.register(desc, owner_scope={"session_id": "scope-b"},
                        registered_by="test")
        try:
            g = build_capability_graph()
            errs = [i for i in validate_graph(g)
                    if i.code == "duplicate_identity"]
            assert errs == [], [e.to_dict() for e in errs]
            # 两个 scope 各自的节点都在
            ids = [n.id for n in g.nodes_by_kind("model")
                   if "tiny-landcover-seg" in n.id]
            assert len(ids) == 2, ids
        finally:
            _MS.load = orig_load
            reset_capability_graph()
            reset_capability_registry()

    def test_ra4_absent_context_is_unknown_not_eligible(self) -> None:
        """缺席上下文 → UNKNOWN + 结构化 reason（RA-4）。"""
        g = get_capability_graph()
        models = [m for m in g.models_for_capability("model_image_segmentation")
                  if "tiny-landcover-seg" in m.id]
        assert models
        result = qualify_model_for_input(
            models[0], QualificationContext())  # 全缺席
        assert result.status == QualificationStatus.UNKNOWN, result.to_dict()
        checks = {r.check for r in result.reasons}
        assert "raster_bands_unknown" in checks or "resolution_unknown" in checks
