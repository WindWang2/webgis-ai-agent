"""自主产品闭环 — 对抗场景矩阵（ADR-0088 §13）集成测试。

场景 D（chart 欠账 + statistics 存活 → 只补产物）走完整 SessionPlan 投影；
其余场景（A/B/C/E/F/G/H）见 test_runtime_repair.py，P1 动作层见
test_action_intent.py。本文件锁定**端到端投影契约**：Pi 在 turn 上下文里
看到的最小欠账 + 可复用输入。
"""
import uuid

import pytest

from app.services.session_data import session_data_manager
from app.services.session_plan import SessionPlan, format_session_plan_projection


@pytest.fixture
async def clean_session():
    sid = f"closure-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


def _chapter():
    return {
        "plan_id": "p1",
        "query": "成都小学统计",
        "recipe_id": "poi_stats",
        "data_requirements": [
            {"capability": "poi_query", "status": "available",
             "bound_ref": "ref:geojson-poi"},
            {"capability": "category_breakdown", "status": "done",
             "bound_ref": "ref:stats-1"},
        ],
        "analysis_steps": [],
        "map_layers": [
            {"role": "primary", "layer_id": "poi-main", "enabled": True,
             "source_capability": "poi_query"},
        ],
        "template_selection": {"export_profile": {"chart": True}},
    }


async def test_scenario_d_chart_missing_stats_reusable_reuse_disclosed(
    clean_session,
):
    """Scenario D：chart 缺失 + statistics artifact 存活。

    预期：`produce_chart` + `(reuse: ref:stats-1)` —— 只补产物；
    POI 查询 / 聚合 / 统计链不重跑（执行债为空：Ready none / 无 failed）。
    """
    plan = SessionPlan(
        session_id=clean_session,
        envelope_id="env-d",
        user_goal="成都小学统计",
        gis_chapter=_chapter(),
    )
    spec = {
        "layers": [{"id": "poi-main", "source": "s-poi"}],
        "sources": {"s-poi": {"type": "geojson", "ref_id": "ref:geojson-poi"}},
        "layout": {"components": []},
    }
    text = format_session_plan_projection(plan, spec)
    assert "chart 0/1" in text and "chart owed" in text
    assert "[Next GIS Action] chart:produce_chart (reuse: ref:stats-1)" in text
    # 上游分析无执行债（全部终态 → 无 ready/failed 披露）
    assert "Ready: none" in text
    assert "Failed" not in text


async def test_scenario_e_missing_runtime_layer_projects_runtime_repair(
    clean_session,
):
    """Scenario E（backend 侧）：style reload 后 runtime 层缺席、spec 在场。

    血缘确认 source ref 存活 → 统一动作仍是 runtime repair 债（不是执行
    债）；前端 registry replay / reassert 通道负责重挂载。
    """
    plan = SessionPlan(
        session_id=clean_session,
        envelope_id="env-e",
        user_goal="成都小学分布",
        gis_chapter=_chapter(),
    )
    # map_product 块记录 render issues（fresh observation 说层缺席）
    plan.gis_chapter["map_product"] = {
        "status": "needs_repair",
        "render_status": "issues",
        "issues": [
            {"code": "render_layer_missing", "severity": "error",
             "target": "poi-main", "detail": "planned result layer not mounted"}
        ],
    }
    spec = {
        "layers": [{"id": "poi-main", "source": "s-poi"}],
        "sources": {"s-poi": {"type": "geojson", "ref_id": "ref:geojson-poi"}},
        "layout": {"components": []},
    }
    text = format_session_plan_projection(plan, spec)
    # 血缘默认 liveness unknown（无 descriptor 快照）→ 不升级为执行债，
    # 保持 runtime repair 建议（死 ref 升级路径见 test_action_intent Scenario B）
    assert "runtime_repair" in text
