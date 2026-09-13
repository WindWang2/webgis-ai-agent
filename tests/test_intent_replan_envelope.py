"""意图差异最小失效 —— session_plan envelope 级回归（方向 5 E5-E6）。

直接驱动 ``apply_tool_result``（Pi bridge 工具结果回写的同一 seam），
覆盖任务书场景 2/3 的计划侧语义：

- supersede（成都小学→成都高中）：行政边界完成事实跨 envelope 携带，
  主体链重开；
- replace（同 goal 局部改参）：携带行不 void，仅变更行失效；
- ``GIS_INTENT_DIFF_REPLAN=0``：回到 master 全量失效行为。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.session_data import session_data_manager
from app.services.session_plan import (
    apply_tool_result,
    load_session_plan,
)


def _chapter(scope: str, subject: str, *, agg_params: str = "district",
             poi_ref: str = "", boundary_ref: str = "") -> dict:
    def _req(cap, tool, params, ref):
        return {
            "capability": cap, "purpose": "", "optional": False,
            "resolved_tool": tool, "resolved_algorithm": "a1",
            "params": params, "depends_on": [],
            "status": "available" if ref else "pending",
            "bound_ref": ref,
        }

    agg_ref = "ref:agg-1" if poi_ref and boundary_ref else ""
    return {
        "plan_id": f"plan-{scope}-{subject}".replace("市", ""),
        "query": f"{scope}{subject}分布情况",
        "recipe_id": "poi_stats",
        "intent": {
            "scope": {"name": scope, "level": "city"},
            "subject": {"type": "poi", "category": subject},
            "task": "distribution_overview", "measure": "count",
            "time": "",
        },
        "data_requirements": [
            _req("adm_boundary_fetch", "get_local_admin_boundary",
                 {"scope": scope}, boundary_ref or f"ref:bd-{scope}"),
            _req("poi_query", "query_local_poi",
                 {"category": subject, "scope": scope}, poi_ref),
        ],
        "analysis_steps": [
            {
                "capability": "district_stats", "purpose": "", "optional": False,
                "resolved_tool": "local_stats", "resolved_algorithm": "a1",
                "params": {"by": agg_params},
                "depends_on": ["adm_boundary_fetch", "poi_query"],
                "status": "done" if agg_ref else "pending",
                "bound_ref": agg_ref,
            },
        ],
    }


@pytest.fixture
async def sid():
    session_id = f"sess-eg-replan-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)


async def test_supersede_carries_boundary_completion(sid):
    """场景3：成都小学→成都高中 —— 边界携带，主体链最小失效。"""
    ch1 = _chapter("成都市", "小学", poi_ref="ref:poi-1",
                   boundary_ref="ref:bd-1")
    events = await apply_tool_result(sid, "webgis_map_intent",
                                     {"plan": ch1}, success=True)
    assert any(e.event == "session_plan_updated" for e in events)

    # 主体数据 + 统计在旧 envelope 内完成（工具结果 → 行状态）
    await apply_tool_result(sid, "query_local_poi", {"ok": True},
                            success=True, geojson_ref="ref:poi-1")
    await apply_tool_result(sid, "local_stats", {"ok": True},
                            success=True, geojson_ref="ref:agg-1")
    plan1 = await load_session_plan(sid)
    assert plan1 is not None
    by_cap = {r.capability: r.status for r in plan1.progress}
    assert by_cap["poi_query"] == "complete"
    assert by_cap["district_stats"] == "complete"

    # follow-up：换高中 → goal_key 变化 → supersede + 携带裁决
    ch2 = _chapter("成都市", "高中", poi_ref="", boundary_ref="")
    events2 = await apply_tool_result(sid, "webgis_map_intent",
                                      {"plan": ch2}, success=True)
    assert any(e.event == "session_plan_superseded" for e in events2)
    plan2 = await load_session_plan(sid)
    assert plan2 is not None and plan2.superseded is False
    by_cap2 = {r.capability: r.status for r in plan2.progress}
    # 行政边界：签名未变 + scope 未变 → 完成事实跨 envelope 存续
    assert by_cap2["adm_boundary_fetch"] == "complete"
    boundary_row = next(r for r in plan2.gis_chapter["data_requirements"]
                        if r["capability"] == "adm_boundary_fetch")
    assert boundary_row["bound_ref"] == "ref:bd-1"
    assert boundary_row["status"] == "available"
    # 主体链：重开（pending）
    assert by_cap2["poi_query"] == "pending"
    assert by_cap2["district_stats"] == "pending"


async def test_replace_keeps_carried_and_voids_changed(sid):
    """场景2 变体：同 goal 局部改参 —— 携带行不 void，变更行失效。"""
    ch1 = _chapter("成都市", "小学", poi_ref="ref:poi-1",
                   boundary_ref="ref:bd-1")
    await apply_tool_result(sid, "webgis_map_intent", {"plan": ch1},
                            success=True)
    await apply_tool_result(sid, "get_local_admin_boundary", {"ok": True},
                            success=True, geojson_ref="ref:bd-1")
    await apply_tool_result(sid, "query_local_poi", {"ok": True},
                            success=True, geojson_ref="ref:poi-1")
    await apply_tool_result(sid, "local_stats", {"ok": True},
                            success=True, geojson_ref="ref:agg-1")
    plan1 = await load_session_plan(sid)
    assert all(r.status == "complete" for r in plan1.progress)

    # 同 goal（scope/subject 未变）但统计参数变更 → replace 分支。
    # 新 chapter 行是 planner 产出的全新 pending 行（无 ref）——携带 ref
    # 由 apply_intent_diff_to_chapter 写入。
    ch2 = _chapter("成都市", "小学", agg_params="grid")
    events = await apply_tool_result(sid, "webgis_map_intent",
                                     {"plan": ch2}, success=True)
    # 无 superseded（同 goal replace）
    assert not any(e.event == "session_plan_superseded" for e in events)
    plan2 = await load_session_plan(sid)
    by_cap2 = {r.capability: r.status for r in plan2.progress}
    # 携带行不 void
    assert by_cap2["adm_boundary_fetch"] == "complete"
    assert by_cap2["poi_query"] == "complete"
    # 变更行失效（voided = 完成事实作废、可重做）
    assert by_cap2["district_stats"] == "voided"
    agg_row = next(r for r in plan2.gis_chapter["analysis_steps"]
                   if r["capability"] == "district_stats")
    assert agg_row["status"] == "pending"


async def test_kill_switch_restores_full_invalidation(sid, monkeypatch):
    """GIS_INTENT_DIFF_REPLAN=0 → master 行为（全 void）。"""
    monkeypatch.setenv("GIS_INTENT_DIFF_REPLAN", "0")
    ch1 = _chapter("成都市", "小学", poi_ref="ref:poi-1",
                   boundary_ref="ref:bd-1")
    await apply_tool_result(sid, "webgis_map_intent", {"plan": ch1},
                            success=True)
    await apply_tool_result(sid, "query_local_poi", {"ok": True},
                            success=True, geojson_ref="ref:poi-1")
    await apply_tool_result(sid, "local_stats", {"ok": True},
                            success=True, geojson_ref="ref:agg-1")
    ch2 = _chapter("成都市", "高中")
    await apply_tool_result(sid, "webgis_map_intent", {"plan": ch2},
                            success=True)
    plan2 = await load_session_plan(sid)
    by_cap2 = {r.capability: r.status for r in plan2.progress}
    # 开关关停：无携带 —— 一切 pending（现状全量语义）
    assert all(status == "pending" for status in by_cap2.values())
