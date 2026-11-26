"""多源 Agent 工具与 HTTP 路由测试（WP4）。

- Pi 工具：plan_preview / drill 真实 dispatch（会话 ref 种子 → 传播 →
  指标 → 钻取改写重放；层级未声明 typed 阻塞）；
- HTTP：/linked-exploration/plan-preview 与 /drill 路由（require_owned_session
  依赖覆写 + typed 错误映射）—— 补 G46 遗留的 HTTP 层零覆盖缺口（本域
  新增路由先有防护网）。

oracle：计数/分组手算（学校×人口 fixture 与集成旅程一致）。
"""
from __future__ import annotations

import uuid

import pytest

from app.tools.linked_exploration_tools import (
    DrillArgs,
    PlanPreviewArgs,
    register_linked_exploration_tools,
)
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio


def _schools_fc():
    rows = [
        ("武侯区", "public"), ("武侯区", "public"), ("武侯区", "private"),
        ("锦江区", "public"),
        ("青羊区", "public"), ("青羊区", "private"),
    ]
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "properties": {"_wfid": f"s{i}", "district": d, "type": k},
         "geometry": {"type": "Point", "coordinates": [104.0 + i, 30.6]}}
        for i, (d, k) in enumerate(rows)]}


def _population_fc():
    rows = [("武侯区", 1_000_000), ("锦江区", 500_000),
            ("青羊区", 300_000), ("港区", 100_000)]
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "properties": {"_wfid": f"p{i}", "district": d, "pop": n},
         "geometry": None}
        for i, (d, n) in enumerate(rows)]}


@pytest.fixture
def tools():
    registry = ToolRegistry()
    register_linked_exploration_tools(registry)
    return registry


async def _seed_pair(session_id: str, tools):
    from app.services.session_data import session_data_manager

    school_ref = await session_data_manager.store(
        session_id, _schools_fc(), prefix="ms4sch")
    pop_ref = await session_data_manager.store(
        session_id, _population_fc(), prefix="ms4pop")
    school_inspect = await tools.dispatch(
        "webgis_exploration_inspect", {"ref": school_ref},
        session_id=session_id)
    pop_inspect = await tools.dispatch(
        "webgis_exploration_inspect", {"ref": pop_ref},
        session_id=session_id)
    assert school_inspect["success"] and pop_inspect["success"]
    return (school_ref, school_inspect["anchor"],
            pop_ref, pop_inspect["anchor"])


def _plan(school_anchor, pop_anchor):
    return {
        "schema_version": 3,
        "sources": [
            {"source_id": "school", "anchor": school_anchor, "role": "primary",
             "hierarchy": {"parent_field": "district",
                           "level_note": "学校点→区县"}},
            {"source_id": "pop", "anchor": pop_anchor, "role": "context"},
        ],
        "links": [{"link_id": "l1", "kind": "attribute",
                   "left_source_id": "school", "right_source_id": "pop",
                   "keys": [{"left_field": "district",
                             "right_field": "district"}],
                   "cardinality": "many_to_one",
                   "propagate": "left_to_right"}],
        "metrics": [{"metric_id": "m1", "op": "rate", "source_id": "school",
                     "denominator_source_id": "pop",
                     "denominator_field": "pop", "link_id": "l1",
                     "scale": 10000, "unit": "所/万人"}],
        "charts": [],
        "conditions": [],
        "base_scope_note": "",
    }


class TestPlanPreviewTool:
    async def test_dispatch_full_journey(self, tools):
        session_id = "ms4-preview-" + uuid.uuid4().hex
        try:
            school_ref, a_sch, pop_ref, a_pop = await _seed_pair(
                session_id, tools)
            result = await tools.dispatch(
                "webgis_exploration_plan_preview",
                {"plan": _plan(a_sch, a_pop)}, session_id=session_id)
            assert result["success"] is True, result
            assert result["schema_version"] == 3
            assert result["primary_source_id"] == "school"
            # 手算：无条件 + left_to_right 传播但上游未过滤 → 不限制
            assert result["per_source"]["school"]["match_count"] == 6
            assert result["per_source"]["pop"]["match_count"] == 4
            # 指标：每万人（oracle 0.03 / 0.02 / 2/30万×1万 / 港区 0）
            m = result["metrics"][0]
            by_key = {g["key"]: g for g in m["groups"]}
            assert by_key["武侯区"]["value"] == pytest.approx(0.03)
            assert by_key["港区"]["available"] is True
            assert by_key["港区"]["value"] == 0.0
            # 键入参 schema 边界
            PlanPreviewArgs(plan={"a": 1})
        finally:
            from app.services.session_data import session_data_manager
            await session_data_manager.clear_session(session_id)

    async def test_typed_rejection_invalid_plan(self, tools):
        session_id = "ms4-preview-bad-" + uuid.uuid4().hex
        result = await tools.dispatch(
            "webgis_exploration_plan_preview",
            {"plan": {"schema_version": 3}}, session_id=session_id)
        assert result["success"] is False
        assert result["code"] == "INVALID_PLAN"

    async def test_propagation_with_condition(self, tools):
        session_id = "ms4-prop-" + uuid.uuid4().hex
        try:
            _school_ref, a_sch, _pop_ref, a_pop = await _seed_pair(
                session_id, tools)
            plan = _plan(a_sch, a_pop)
            plan["charts"] = [{"chart_id": "ch-type", "kind": "category",
                               "field": "type", "source_id": "school"}]
            plan["conditions"] = [{
                "kind": "category", "condition_id": "cond-ch-type",
                "chart_id": "ch-type", "field": "type", "values": ["public"]}]
            result = await tools.dispatch(
                "webgis_exploration_plan_preview",
                {"plan": plan}, session_id=session_id)
            assert result["success"] is True, result
            # 手算：public → {武侯,锦江,青羊} → 人口 3 行（港区排除）
            assert result["per_source"]["school"]["match_count"] == 4
            assert result["per_source"]["pop"]["match_count"] == 3
            props = result["per_source"]["pop"]["propagations"]
            assert props[0]["key_count"] == 3
        finally:
            from app.services.session_data import session_data_manager
            await session_data_manager.clear_session(session_id)


class TestDrillTool:
    async def test_drill_down_and_up(self, tools):
        session_id = "ms4-drill-" + uuid.uuid4().hex
        try:
            _s, a_sch, _p, a_pop = await _seed_pair(session_id, tools)
            plan = _plan(a_sch, a_pop)
            down = await tools.dispatch(
                "webgis_exploration_drill",
                {"plan": plan, "source_id": "school", "direction": "down",
                 "parent_value": "武侯区"}, session_id=session_id)
            assert down["success"] is True, down
            assert down["drill"]["parent_field"] == "district"
            # 钻入武侯区 → 学校 3 所；人口侧经传播 → 只有武侯区 1 行
            assert down["preview_brief"]["per_source"]["school"][
                "match_count"] == 3
            assert down["preview_brief"]["per_source"]["pop"][
                "match_count"] == 1
            groups = {g["key"]: g["count"] for g in down["drill"]["groups"]}
            assert groups.get("武侯区") == 3
            # 上卷：移除钻取条件 → 回全量
            up = await tools.dispatch(
                "webgis_exploration_drill",
                {"plan": down["plan"], "source_id": "school",
                 "direction": "up"}, session_id=session_id)
            assert up["success"] is True
            assert up["preview_brief"]["per_source"]["school"][
                "match_count"] == 6
        finally:
            from app.services.session_data import session_data_manager
            await session_data_manager.clear_session(session_id)

    async def test_hierarchy_not_declared_honest_block(self, tools):
        session_id = "ms4-drill-noh-" + uuid.uuid4().hex
        try:
            _s, a_sch, _p, a_pop = await _seed_pair(session_id, tools)
            plan = _plan(a_sch, a_pop)
            del plan["sources"][0]["hierarchy"]   # 未声明层级
            result = await tools.dispatch(
                "webgis_exploration_drill",
                {"plan": plan, "source_id": "school", "direction": "down",
                 "parent_value": "武侯区"}, session_id=session_id)
            assert result["success"] is False
            assert result["code"] == "HIERARCHY_NOT_DECLARED"
            assert "不按名称猜测" in result["message"]
        finally:
            from app.services.session_data import session_data_manager
            await session_data_manager.clear_session(session_id)


# ─── HTTP 路由（require_owned_session 覆写；typed 错误映射） ─────────────

def _client(conv_id: str = "ms4-conv"):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models.db_model import Conversation
    from app.services.auth_history_bridge import require_owned_session

    conv = Conversation(id=conv_id, user_id="ms4-user", title="t")
    app.dependency_overrides[require_owned_session] = lambda: conv
    client = TestClient(app)
    return client, app


class TestHttpRoutes:
    def test_plan_preview_route(self):
        import asyncio

        from app.services.session_data import session_data_manager

        # 路由的会话 id = conv.id（查询参数仅作鉴权）——种子须用同一 id
        conv_id = "ms4-http-" + uuid.uuid4().hex
        loop = asyncio.new_event_loop()
        try:
            school_ref = loop.run_until_complete(session_data_manager.store(
                conv_id, _schools_fc(), prefix="ms4hs"))
            pop_ref = loop.run_until_complete(session_data_manager.store(
                conv_id, _population_fc(), prefix="ms4hp"))
            client, app = _client(conv_id)
            try:
                ins = client.post(
                    f"/api/v1/linked-exploration/inspect?session_id={conv_id}",
                    json={"ref": school_ref})
                assert ins.status_code == 200, ins.text
                a_sch = ins.json()["anchor"]
                ins2 = client.post(
                    f"/api/v1/linked-exploration/inspect?session_id={conv_id}",
                    json={"ref": pop_ref})
                assert ins2.status_code == 200, ins2.text
                a_pop = ins2.json()["anchor"]
                r = client.post(
                    f"/api/v1/linked-exploration/plan-preview"
                    f"?session_id={conv_id}",
                    json={"plan": _plan(a_sch, a_pop)})
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["per_source"]["pop"]["match_count"] == 4
            finally:
                app.dependency_overrides.clear()
        finally:
            loop.run_until_complete(
                session_data_manager.clear_session(conv_id))
            loop.close()

    def test_plan_preview_invalid_plan_maps_422(self):
        client, app = _client()
        try:
            r = client.post(
                "/api/v1/linked-exploration/plan-preview?session_id=ms4-conv",
                json={"plan": {"schema_version": 3}})
            assert r.status_code == 422
            # 全局异常处理器把 typed payload 包进 data 字段
            assert r.json()["data"]["code"] == "INVALID_PLAN"
        finally:
            app.dependency_overrides.clear()

    def test_drill_route_and_typed_block(self):
        import asyncio

        from app.services.session_data import session_data_manager

        conv_id = "ms4-http-d-" + uuid.uuid4().hex
        loop = asyncio.new_event_loop()
        try:
            school_ref = loop.run_until_complete(session_data_manager.store(
                conv_id, _schools_fc(), prefix="ms4hd"))
            client, app = _client(conv_id)
            try:
                ins = client.post(
                    f"/api/v1/linked-exploration/inspect?session_id={conv_id}",
                    json={"ref": school_ref})
                assert ins.status_code == 200, ins.text
                anchor = ins.json()["anchor"]
                plan = {
                    "schema_version": 3,
                    "sources": [{"source_id": "school",
                                 "anchor": anchor, "role": "primary"}],
                    "links": [], "metrics": [], "charts": [],
                    "conditions": [], "base_scope_note": "",
                }
                r = client.post(
                    f"/api/v1/linked-exploration/drill?session_id={conv_id}",
                    json={"plan": plan, "source_id": "school",
                          "direction": "down", "parent_value": "武侯区"})
                assert r.status_code == 422
                assert r.json()["data"]["code"] == "HIERARCHY_NOT_DECLARED"
            finally:
                app.dependency_overrides.clear()
        finally:
            loop.run_until_complete(
                session_data_manager.clear_session(conv_id))
            loop.close()
