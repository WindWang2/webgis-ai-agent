"""Capability Resolution V1（ADR-0181）—— resolve_capabilities 门面测试。

覆盖：
- 基本解析：goal → per-capability 决策 + provider 排序 + 确定性；
- Situation 三面（offline/auth_tier/budget）的资格效果；
- 无隐藏降级：ineligible 能力必须携带 fallback 替代与 make_available；
- 冲突披露：incompatible_with 双能力进同一 goal；
- 只读查询协议（describe/list）；
- 生产接线：select_candidates 第 12 层（situation=None 逐位一致）+
  plan_from_intent/finalize_with_profile 的证据面。
"""
from __future__ import annotations

import json

import pytest

from app.services.gis_harness.capability_graph import get_capability_graph
from app.services.gis_harness.capability_resolution import (
    GoalRequirements,
    build_situation,
    capability_status,
    describe_capability,
    list_capabilities,
    resolve_capabilities,
)
from app.services.gis_harness.qualification_v8 import QualificationContext


def _neutral() -> QualificationContext:
    return build_situation()


def _goal(*caps: str, optional: tuple = ()) -> GoalRequirements:
    return GoalRequirements(
        capability_ids=list(caps), optional_ids=list(optional),
        task_hint="test")


# ── 基本解析 ──────────────────────────────────────────────────────────────


class TestResolveBasics:
    def test_known_capability_resolves_with_providers(self):
        res = resolve_capabilities(_goal("poi_query"), _neutral())
        assert len(res.decisions) == 1
        d = res.decisions[0]
        assert d.required and d.status == "eligible"
        assert d.providers, "poi_query 必须有 provider"
        best = d.providers[0]
        assert best.kind == "tool"
        factors = best.factors
        assert "latency_rank" in factors

    def test_unknown_capability_honest(self):
        res = resolve_capabilities(_goal("no_such_capability_xyz"), _neutral())
        d = res.decisions[0]
        assert d.status == "unknown"
        assert d.why == "capability_not_in_graph"

    def test_determinism_same_input_same_output(self):
        a = resolve_capabilities(
            _goal("poi_query", "admin_aggregation", optional=("kde_density",)),
            _neutral())
        b = resolve_capabilities(
            _goal("poi_query", "admin_aggregation", optional=("kde_density",)),
            _neutral())
        assert json.dumps(a.to_dict(), sort_keys=True) == \
            json.dumps(b.to_dict(), sort_keys=True)

    def test_serializable_and_bounded(self):
        res = resolve_capabilities(
            _goal("poi_query", "kde_density", "admin_aggregation"),
            _neutral())
        blob = json.dumps(res.to_dict())
        assert len(blob) < 32 * 1024
        ctx = res.to_bounded_context(512)
        assert len(ctx.encode("utf-8")) <= 512

    def test_optional_capability_marked(self):
        res = resolve_capabilities(_goal("poi_query", optional=("kde_density",)),
                                   _neutral())
        by_id = {d.capability_id: d for d in res.decisions}
        assert by_id["poi_query"].required
        assert not by_id["kde_density"].required


# ── Situation 三面 ────────────────────────────────────────────────────────


def _find_offline_flip_capability() -> str:
    """找一个『中性 eligible、离线 ineligible』的能力（数据驱动，确定性）。"""
    graph = get_capability_graph()
    for node in sorted(graph.nodes_by_kind("capability"), key=lambda n: n.id):
        neutral, _, _ = capability_status(node.id, _neutral())
        if neutral != "eligible":
            continue
        off, _, _ = capability_status(node.id, build_situation(offline=True))
        if off == "ineligible":
            return node.id
    return ""


class TestSituationFaces:
    def test_offline_rejects_network_provider(self):
        cap = _find_offline_flip_capability()
        assert cap, "需要至少一个纯联网能力（网络声明面）"
        off = build_situation(offline=True)
        status, ranked, rejected = capability_status(cap, off)
        assert status == "ineligible"
        assert rejected
        for cand in rejected:
            checks = {r.check for r in cand.qualification.reasons}
            assert "offline_network_required" in checks

    def test_auth_tier_gate(self):
        # 机制级断言（合成 provider 节点）：security_tier 超出授权层 →
        # auth_tier_insufficient 失格；授权足够时不因 auth 失格。
        from app.services.gis_harness.capability_graph import GraphNode
        from app.services.gis_harness.qualification_v8 import qualify_node

        node = GraphNode("probe_heavy_tool", "tool", "test", extras={
            "tier": 3, "security_tier": 3, "cost": "heavy",
            "network": True, "side_effect": "state_mutation",
        })
        strict = build_situation(auth_tier=2)
        result = qualify_node(node, strict)
        checks = {r.check for r in result.reasons}
        assert result.status == "ineligible"
        assert "auth_tier_insufficient" in checks
        # 授权足够：auth 不再出现（tier>=3 的 confirm_required 降级保留）
        relaxed = build_situation(auth_tier=3)
        result2 = qualify_node(node, relaxed)
        assert "auth_tier_insufficient" not in {
            r.check for r in result2.reasons}

    def test_budget_gate(self):
        from app.services.gis_harness.capability_graph import GraphNode
        from app.services.gis_harness.qualification_v8 import qualify_node

        node = GraphNode("probe_heavy_tool", "tool", "test", extras={
            "tier": 1, "cost": "heavy", "network": False,
        })
        tight = build_situation(budget_cost_class="light")
        result = qualify_node(node, tight)
        assert result.status == "ineligible"
        assert "budget_exceeded" in {r.check for r in result.reasons}
        within = build_situation(budget_cost_class="heavy")
        result2 = qualify_node(node, within)
        assert "budget_exceeded" not in {r.check for r in result2.reasons}

    def test_offline_gate_synthetic(self):
        from app.services.gis_harness.capability_graph import GraphNode
        from app.services.gis_harness.qualification_v8 import qualify_node

        node = GraphNode("probe_online_tool", "tool", "test", extras={
            "tier": 1, "network": True,
        })
        off = build_situation(offline=True)
        result = qualify_node(node, off)
        assert result.status == "ineligible"
        assert "offline_network_required" in {r.check for r in result.reasons}

    def test_no_hidden_degradation(self):
        """失格能力必须披露 fallback 替代或 make_available 提示。"""
        cap = _find_offline_flip_capability()
        assert cap
        res = resolve_capabilities(_goal(cap), build_situation(offline=True))
        d = res.decisions[0]
        assert d.status == "ineligible"
        assert d.degraded_alternatives or d.make_available or d.missing


# ── 冲突披露 ──────────────────────────────────────────────────────────────


def _find_conflicting_pair() -> tuple:
    registry = get_capability_graph()
    from app.lib.gis.capability_registry import get_capability_registry
    caps = get_capability_registry()
    for cap_id in caps.all_ids:
        d = caps.get(cap_id)
        for other in (d.incompatible_with or []):
            if caps.has(other):
                return cap_id, other
    return ()


class TestConflicts:
    def test_incompatible_pair_disclosed(self):
        pair = _find_conflicting_pair()
        if not pair:
            pytest.skip("capability registry 无 incompatible_with 声明")
        res = resolve_capabilities(_goal(*pair), _neutral())
        assert res.conflicts, "互斥双能力必须披露冲突"


# ── 只读查询协议（方向 4）─────────────────────────────────────────────────


class TestQueryProtocol:
    def test_describe_capability_shape(self):
        d = describe_capability("poi_query", situation=_neutral())
        assert d is not None
        for key in ("id", "domain", "providers", "fallbacks", "conflicts",
                    "situation_status", "best_provider"):
            assert key in d
        assert describe_capability("no_such_capability_xyz") is None

    def test_list_capabilities_domain_filter(self):
        raster = list_capabilities(domain="raster")
        assert raster
        assert all(r["domain"] == "raster" for r in raster)

    def test_list_capabilities_query_and_limit(self):
        hits = list_capabilities(query="kriging", limit=5)
        assert len(hits) <= 5
        assert any("kriging" in h["id"] for h in hits)


# ── 生产接线 ──────────────────────────────────────────────────────────────


class TestProductionWiring:
    def test_select_candidates_identity_without_situation(self):
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.recipes import get_recipe_registry

        reg = get_recipe_registry()
        intent = resolve_map_request_intent("看看成都的大学分布，做一张图")
        baseline = [r.id for r in reg.select_candidates(intent)]
        explicit_none = [r.id for r in reg.select_candidates(
            intent, situation=None)]
        assert baseline == explicit_none

    def test_select_candidates_offline_penalizes_network_only_recipe(self):
        """第 12 层语义（合成 registry 钉死）：必需能力失格的候选后置，
        situation=None 逐位一致。"""
        cap = _find_offline_flip_capability()
        assert cap
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.recipes import (
            CartographyRecipe,
            RecipeRegistry,
        )

        reg = RecipeRegistry()
        reg.load_builtins()
        base_recipe = reg.get("poi_distribution_overview")
        assert base_recipe is not None
        safe = base_recipe.model_copy(deep=True)
        safe.id = "cg_v1_safe_probe"
        safe.name = "CG1 Safe Probe"
        safe.priority = base_recipe.priority
        victim = base_recipe.model_copy(deep=True)
        victim.id = "cg_v1_victim_probe"
        victim.name = "CG1 Victim Probe"
        victim.preferred_analysis = [cap]
        victim.priority = base_recipe.priority
        reg.register(safe)
        reg.register(victim)
        intent = resolve_map_request_intent("看看成都的大学分布，做一张图")
        baseline = [r.id for r in reg.select_candidates(intent, limit=20)]
        assert "cg_v1_safe_probe" in baseline
        offline = [r.id for r in reg.select_candidates(
            intent, limit=20, situation=build_situation(offline=True))]
        assert offline.index("cg_v1_victim_probe") > \
            offline.index("cg_v1_safe_probe"), \
            "必需能力失格的候选必须后置"
        # situation 缺省：victim 回到与 safe 同分（priority/id 决定）
        plain = [r.id for r in reg.select_candidates(intent, limit=20)]
        assert plain.index("cg_v1_victim_probe") == baseline.index("cg_v1_victim_probe")

    def test_plan_from_intent_attaches_evidence(self):
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner_runtime import get_planner_runtime

        planner = get_planner_runtime()
        intent = resolve_map_request_intent("看看成都的大学分布，做一张图")
        plan = planner.plan_from_intent(
            intent, use_memo=False,
            situation=build_situation(task_hint=str(intent.task)))
        ev = plan.capability_evidence
        assert ev, "situation 提供时必须附着能力证据"
        assert ev["goal"]["capability_ids"]
        assert "status_summary" in ev
        # 不带 situation：行为与历史一致（证据为空）
        plan_plain = planner.plan_from_intent(intent, use_memo=False)
        assert plan_plain.capability_evidence == {}

    def test_kill_switch_disables_wiring(self, monkeypatch):
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner_runtime import get_planner_runtime

        monkeypatch.setenv("GIS_CAPABILITY_PLANNING_V1", "0")
        planner = get_planner_runtime()
        intent = resolve_map_request_intent("看看成都的大学分布，做一张图")
        plan = planner.plan_from_intent(
            intent, use_memo=False,
            situation=build_situation(task_hint=str(intent.task)))
        assert plan.capability_evidence == {}

    def test_finalize_refreshes_evidence_with_profile(self):
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner_runtime import get_planner_runtime

        planner = get_planner_runtime()
        intent = resolve_map_request_intent("看看成都的大学分布，做一张图")
        draft = planner.plan_from_intent(
            intent, use_memo=False,
            situation=build_situation(task_hint=str(intent.task)))
        profile = {
            "geometry": "Point", "featureCount": 500,
            "fields": {"name": {}, "category": {}}, "crs": "EPSG:4326",
        }
        finalized = planner.finalize_with_profile(
            draft, profile,
            situation=build_situation(task_hint=str(intent.task)),
        )
        assert finalized.capability_evidence
        assert finalized.capability_evidence["situation"].get("feature_count") == 500

    def test_finalize_warns_on_required_ineligible(self):
        cap = _find_offline_flip_capability()
        if not cap:
            pytest.skip("无纯联网能力可构造失格场景")
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner_runtime import get_planner_runtime
        from app.services.gis_harness.recipes import get_recipe_registry

        reg = get_recipe_registry()
        target = ""
        for rid in reg.all_ids:
            r = reg.get(rid)
            if r and cap in (r.preferred_analysis or []):
                target = rid
                break
        assert target
        planner = get_planner_runtime()
        intent = resolve_map_request_intent("看看成都的大学分布，做一张图")
        draft = planner.plan_from_intent(
            intent, recipe_id=target, use_memo=False,
            situation=build_situation(offline=True))
        finalized = planner.finalize_with_profile(
            draft, {"geometry": "Point", "featureCount": 500,
                    "fields": {"name": {}}},
            situation=build_situation(offline=True))
        codes = {w.get("code") for w in finalized.methodology_warnings}
        assert f"CAPABILITY_INELIGIBLE_{cap.upper()}" in codes


# ── Review 修复回归（独立 review P1/P2/P3，2026-09-13）────────────────────


class TestReviewFixes:
    def test_bounded_context_truncates_under_pressure(self):
        """review P1：溢出截断路径必须工作（返回 ≤ 预算且不抛异常）。"""
        caps = [c.id for c in get_capability_graph()
                .nodes_by_kind("capability")][:12]
        res = resolve_capabilities(_goal(*caps), _neutral())
        ctx = res.to_bounded_context(256)
        assert len(ctx.encode("utf-8")) <= 256
        assert ctx, "截断后仍应有内容"

    def test_kill_switch_restores_selection_baseline(self):
        """review P2：kill switch 下即便显式传 situation，选择与证据
        都必须逐位等于无 situation 基线。"""
        import json as _json

        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner_runtime import get_planner_runtime

        reg_provider = get_planner_runtime()
        intent = resolve_map_request_intent("看看成都的大学分布，做一张图")
        baseline = [r.id for r in reg_provider.recipes.select_candidates(intent)]
        baseline_dump = _json.dumps(
            reg_provider.plan_from_intent(
                intent, use_memo=False).model_dump(),
            ensure_ascii=False, sort_keys=True)
        monkeyenv = {"GIS_CAPABILITY_PLANNING_V1": "0"}
        import os
        old = os.environ.get("GIS_CAPABILITY_PLANNING_V1")
        os.environ["GIS_CAPABILITY_PLANNING_V1"] = "0"
        try:
            switched = [r.id for r in reg_provider.recipes.select_candidates(
                intent, situation=build_situation(offline=True))]
            switched_plan = _json.dumps(
                reg_provider.plan_from_intent(
                    intent, use_memo=False,
                    situation=build_situation(offline=True)).model_dump(),
                ensure_ascii=False, sort_keys=True)
        finally:
            if old is None:
                os.environ.pop("GIS_CAPABILITY_PLANNING_V1", None)
            else:
                os.environ["GIS_CAPABILITY_PLANNING_V1"] = old
        assert switched == baseline
        assert switched_plan == baseline_dump

    def test_build_situation_preserves_zero_facts(self):
        """review P2：0/False 是观察事实，base 复制不得静默丢弃。"""
        base = QualificationContext(
            feature_count=0, resolution_m_per_px=0.0, crs_is_geographic=False,
            map_layer_count=0,
        )
        sit = build_situation(base=base)
        assert sit.feature_count == 0
        assert sit.resolution_m_per_px == 0.0
        assert sit.crs_is_geographic is False
        assert sit.map_layer_count == 0

    def test_deprecated_provider_penalized(self):
        """review P3：弃用 provider 排序因子生效（合成图）。"""
        from app.services.gis_harness.capability_graph import (
            CapabilityGraph,
            GraphEdge,
            GraphNode,
        )
        from app.services.gis_harness.capability_resolution import (
            _provider_candidates,
        )

        nodes = [
            GraphNode("cap_dep", "capability", "test"),
            GraphNode("tool_canonical", "tool", "test",
                      extras={"tier": 1, "status": "stable",
                              "latency_class": "medium"}),
            GraphNode("tool_old", "tool", "test",
                      extras={"tier": 1, "status": "deprecated",
                              "latency_class": "medium",
                              "deprecation_of": "tool_canonical"}),
        ]
        edges = [
            GraphEdge("tool:tool_canonical", "implements",
                      "capability:cap_dep"),
            GraphEdge("tool:tool_old", "implements", "capability:cap_dep"),
        ]
        g = CapabilityGraph(
            {n.key: n for n in nodes}, edges, "test", [])
        ranked, _ = _provider_candidates("cap_dep", _neutral(), g)
        by_id = {c.id: c for c in ranked}
        assert "deprecated_penalty" in by_id["tool_old"].factors
        assert by_id["tool_old"].score > by_id["tool_canonical"].score

    def test_goal_level_conflict_fold_with_scratch_registry(self):
        """review P3：goal 级冲突折叠（scratch registry 注入声明）。"""
        from app.lib.gis.capability_registry import (
            CapabilityDescriptor,
            get_capability_registry,
            reset_capability_registry,
        )

        reg = get_capability_registry()
        probe_a = "cg_v1_conflict_a"
        probe_b = "cg_v1_conflict_b"
        assert not reg.has(probe_a) and not reg.has(probe_b)
        try:
            reg.register(CapabilityDescriptor(
                id=probe_a, name="A", incompatible_with=[probe_b]))
            reg.register(CapabilityDescriptor(id=probe_b, name="B"))
            res = resolve_capabilities(_goal(probe_a, probe_b), _neutral())
            assert any(
                c["capabilities"] == f"{probe_a}|{probe_b}"
                or c["capabilities"] == f"{probe_b}|{probe_a}"
                for c in res.conflicts)
        finally:
            reset_capability_registry()
