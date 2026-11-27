"""ADR-0097 显式分析图回归锁。

不变式：分析图是 SessionPlan 章节 + MapSpec + 证据的纯派生投影 —
零持久化、零第二事实源、有界可序列化；删除任何缓存立即重建值不变。
"""
from __future__ import annotations




class _FakePlan:
    def __init__(self, chapter, *, superseded=False):
        self.session_id = "sess-test"
        self.envelope_id = "sp-test"
        self.user_goal = chapter.get("query", "")
        self.gis_chapter = chapter
        self.replaced = False
        self.superseded = superseded


def _chapter(query: str = "为成都新分校选址推荐最优位置") -> dict:
    from app.services.gis_harness.intent import resolve_map_request_intent
    from app.services.gis_harness.planner import MapProductPlanner

    plan = MapProductPlanner().plan_from_intent(
        resolve_map_request_intent(query), use_memo=False)
    return plan.model_dump()


def _build(chapter, **kw):
    from app.services.gis_harness.analysis_graph import build_analysis_graph

    return build_analysis_graph(_FakePlan(chapter), None, **kw)


class TestProjectionShape:
    def test_graph_has_goal_execution_product_layers(self):
        g = _build(_chapter())
        assert g["goal"]["kind"] == "goal"
        kinds = {n["kind"] for n in g["nodes"]}
        assert "requirement" in kinds or "analysis" in kinds
        assert "product" in kinds
        assert g["counts"]["execution"] >= 4
        assert g["counts"]["product"] >= 3

    def test_execution_nodes_carry_dependency_and_algorithm_evidence(self):
        g = _build(_chapter())
        exec_nodes = [n for n in g["nodes"] if n["kind"] in ("requirement", "analysis")]
        by_cap = {n["capability"]: n for n in exec_nodes}
        assert "mcda_evaluation" in by_cap
        mcda = by_cap["mcda_evaluation"]
        # artifact 类型推断：mcda 消费 poi/面/聚合表 → 依赖其生产者
        assert "poi_query" in mcda["depends_on"]
        assert mcda["algorithm"] == "decision.mcda.wsm"
        assert mcda["tool"] == "spatial_decision_v3"
        assert mcda["recompute_impact"] == "downstream"

    def test_goal_carries_methodology_warning_codes(self):
        g = _build(_chapter())
        codes = [w["code"] for w in g["goal"]["methodology_warnings"]]
        assert "SITE_SELECTION_CRITERIA_UNDECLARED" in codes

    def test_product_facets_carry_recompute_dims(self):
        g = _build(_chapter())
        facets = [n for n in g["nodes"] if n["kind"] == "product"]
        dims = {f["facet_kind"]: f["recompute_dims"] for f in facets}
        assert dims, "product facets must exist even before layers bind"
        # 每个 facet 必须声明失效维度（可解释的重算语义）
        assert all(v for v in dims.values())
        # 纯渲染表达（legend）不触发分析重算；统计/分析 facet 不受样式影响
        if "legend" in dims:
            assert "algorithm" not in dims["legend"]
        if "statistics" in dims:
            assert "style" not in dims["statistics"]
        # 有图层绑定时，图层 facet 的五维语义含 style（样式只重渲染）
        if "map_layer" in dims:
            assert "style" in dims["map_layer"]
            assert "algorithm" in dims["map_layer"]

    def test_next_action_present_for_pending_plan(self):
        g = _build(_chapter())
        assert g["next_action"] is not None
        assert g["next_action"]["mode"] in (
            "capability", "runtime_repair", "observation", "finalization")

    def test_deterministic_projection(self):
        chapter = _chapter()
        g1 = _build(chapter)
        g2 = _build(chapter)
        assert g1 == g2


class TestBoundsAndEmpty:
    def test_no_plan_returns_honest_empty_graph(self):
        from app.services.gis_harness.analysis_graph import build_analysis_graph

        g = build_analysis_graph(None)
        assert g["nodes"] == []
        assert g["goal"] is None
        assert g["notes"]

    async def test_session_loader_empty_for_unknown_session(self):
        from app.services.gis_harness.analysis_graph import (
            build_analysis_graph_for_session,
        )

        g = await build_analysis_graph_for_session("no-such-session")
        assert g["nodes"] == []

    def test_execution_nodes_bounded(self):
        # 构造一个超宽 chapter（能力重复行不现实，直接放大产品节点侧的
        # 上界由常量保证；这里验证 execution 上界常量存在且生效路径不炸）
        from app.services.gis_harness import analysis_graph as ag

        assert ag._MAX_EXECUTION_NODES <= 96
        assert ag._MAX_PRODUCT_NODES <= 64
        assert ag._MAX_WARNINGS <= 12

    def test_superseded_plan_flagged(self):
        chapter = _chapter()
        from app.services.gis_harness.analysis_graph import build_analysis_graph

        g = build_analysis_graph(_FakePlan(chapter, superseded=True), None)
        assert any("superseded" in n for n in g["notes"])

    def test_malformed_chapter_projection_never_raises(self):
        """畸形章节（行是非 dict）→ 执行/产品投影降级缺席，绝不抛给调用方
        （与 next_action / goal_graph 投影同纪律 —— 图是检视图，不是门槛）。"""
        chapter = _chapter()
        chapter["data_requirements"] = ["oops-not-a-dict"]
        chapter["analysis_steps"] = ["oops-not-a-dict"]
        g = _build(chapter)
        assert g["counts"]["execution"] == 0
        assert any("execution projection failed" in n for n in g["notes"])


class TestPlanInputs:
    def test_dict_plan_reads_goal_fields(self):
        """dict plan 的 user_goal/superseded/replaced 不得静默为空/False
        （label 已用同一字段；同节点内必须自洽）。"""
        from app.services.gis_harness.analysis_graph import build_analysis_graph

        g = build_analysis_graph({
            "user_goal": "dict goal",
            "superseded": True,
            "replaced": True,
            "gis_chapter": _chapter(),
        })
        assert g["goal"]["label"] == "dict goal"
        assert g["goal"]["query"] == "dict goal"
        assert g["goal"]["superseded"] is True
        assert g["goal"]["replaced"] is True
        assert any("superseded" in n for n in g["notes"])

    def test_facet_completion_computed_once(self, monkeypatch):
        import app.services.gis_harness.product_graph as pg

        original = pg.build_facet_completion
        calls = []

        def _counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(pg, "build_facet_completion", _counting)
        _build(_chapter())
        assert len(calls) == 1

    async def test_non_numeric_revision_keeps_mapspec(self, monkeypatch):
        """revision 非数字只降级修订号，不得把整个 MapSpec 置 None。"""
        import app.services.gis_harness.render_observation as ro
        import app.services.mapspec_store as msmod
        import app.services.session_plan as spmod
        from app.services.gis_harness import analysis_graph as ag

        captured = {}
        mapspec = {"revision": "not-a-number", "layers": [{"id": "l1"}]}

        def _fake_build(plan, spec, **kw):
            captured["mapspec"] = spec
            captured["current_revision"] = kw.get("current_revision")
            return {"nodes": []}

        class _Store:
            async def get_mapspec(self, session_id):
                return mapspec

        async def _none(_session_id):
            return None

        monkeypatch.setattr(ag, "build_analysis_graph", _fake_build)
        monkeypatch.setattr(msmod, "mapspec_store", _Store())
        monkeypatch.setattr(spmod, "load_session_plan", _none)
        monkeypatch.setattr(ro, "load_render_observation", _none)

        await ag.build_analysis_graph_for_session("s")
        assert captured["mapspec"] is mapspec
        assert captured["current_revision"] == 0


class TestRoute:
    def test_route_registered(self):
        from app.main import app

        # FastAPI 版本差异：app.routes 可能含 _IncludedRouter 包装（无 path）
        # —— 双路断言：展开具 path 的路由 + OpenAPI paths（权威）。
        paths = {
            r.path for r in app.routes
            if hasattr(r, "path") and isinstance(getattr(r, "path"), str)
        }
        try:
            paths |= set(app.openapi().get("paths", {}).keys())
        except Exception:  # noqa: BLE001 — openapi 构建失败退回路由表
            pass
        assert "/api/v1/sessions/{session_id}/analysis-graph" in paths

    async def test_handler_uses_conversation_id(self, monkeypatch):
        """回归：handler 曾读 ``conversation.session_id`` —— Conversation
        只有 PK ``id``（session_id 语义 = Conversation.id，守卫查询即
        ``Conversation.id == session_id``），属主校验通过的请求 100%
        AttributeError → 500。此处直接驱动 handler 本体。"""
        from app.api.routes import analysis_graph
        from app.models.db_model import Conversation

        conv = Conversation(id="sess-handler")
        assert not hasattr(conv, "session_id")
        seen: list[str] = []

        async def _fake_build(session_id: str) -> dict:
            seen.append(session_id)
            return {"nodes": [], "goal": None}

        monkeypatch.setattr(
            analysis_graph, "build_analysis_graph_for_session", _fake_build)
        result = await analysis_graph.get_analysis_graph(conv)
        assert seen == ["sess-handler"]
        assert result == {"nodes": [], "goal": None}


class TestInvariants:
    def test_projection_reads_only_no_mutation(self):
        """投影绝不写回章节（纯函数证据：输入深拷贝前后相等）。"""
        import copy

        chapter = _chapter()
        snapshot = copy.deepcopy(chapter)
        _build(chapter)
        assert chapter == snapshot
