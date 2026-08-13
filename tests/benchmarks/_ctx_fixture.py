"""Shared synthetic-session fixture for the planning-v3 context benchmark.

Imported by both ``bench_planning_v3.py`` (v3 worktree) and
``_master_context_baseline.py`` (executed with cwd = the pristine master
checkout, module resolved via PYTHONPATH). The fixture deliberately imports
only APIs that are byte-identical between the two worktrees, so the assembled
contexts are directly comparable:

- ``app.services.session_data.session_data_manager`` (memory backend when
  ``USE_REDIS=false``)
- ``app.services.chat.plan_orchestrator`` (``Plan``/``PlanStep``/``plan_orchestrator``)
- ``app.services.chat.prompt.SYSTEM_PROMPT``

The fixture mirrors swarm-report C §5's synthetic session: 2 layers (5-feature
GeoJSON schema inference), 3 tool events, a 4-step plan with 2 steps done,
plus 4 user/assistant turns.
"""
from __future__ import annotations

from app.services.session_data import session_data_manager
from app.services.chat.plan_orchestrator import Plan, PlanStep, plan_orchestrator
from app.services.chat.prompt import SYSTEM_PROMPT

SESSION_ID = "bench-v3-session"


def make_geojson(geom_type: str, n: int = 5) -> dict:
    """Small realistic GeoJSON FeatureCollection (5 features)."""
    feats = []
    for i in range(n):
        if geom_type == "Point":
            coords = [104.0 + i * 0.01, 30.6 + i * 0.005]
        else:
            coords = [[104.0 + j * 0.01, 30.6 + j * 0.005] for j in range(4)]
        feats.append({
            "type": "Feature",
            "properties": {"name": f"f{i}", "value": i},
            "geometry": {"type": geom_type, "coordinates": coords},
        })
    return {"type": "FeatureCollection", "features": feats}


async def build_fixture(
    session_id: str = SESSION_ID,
    n_turns: int = 4,
    long_msgs: bool = False,
):
    """Populate the memory session store + orchestrator plan, return messages.

    ``n_turns`` selects how many user/assistant pairs the message list holds;
    ``long_msgs`` uses 150-250 char realistic messages (used for the
    2-turn dedup measurement where short fixture messages would understate
    the block bytes).
    """
    await session_data_manager.clear_session(session_id)

    r1 = await session_data_manager.store(session_id, make_geojson("Point"), prefix="geojson")
    r2 = await session_data_manager.store(session_id, make_geojson("LineString"), prefix="geojson")
    await session_data_manager.set_alias(session_id, r1, "医院_成都")
    await session_data_manager.set_alias(session_id, r2, "道路_成都")

    await session_data_manager.set_map_state(session_id, "viewport", {
        "center": [104.06, 30.67], "zoom": 12.0, "bearing": 0, "pitch": 0,
        "bounds": [103.9, 30.5, 104.2, 30.8],
    })
    await session_data_manager.set_map_state(session_id, "base_layer", "OSM 地图")
    await session_data_manager.set_map_state(session_id, "layers", [
        {"id": r1, "name": "医院_成都", "type": "geojson", "visible": True, "featureCount": 5},
        {"id": r2, "name": "道路_成都", "type": "geojson", "visible": True, "featureCount": 5},
    ])
    await session_data_manager.set_map_state(session_id, "selected_feature", None)

    await session_data_manager.append_event(session_id, "tool_executed", {
        "tool": "search_poi", "status": "completed", "ref": r1,
        "feature_count": 5, "command": "search_poi",
    })
    await session_data_manager.append_event(session_id, "tool_executed", {
        "tool": "spatial_filter", "status": "completed", "ref": r1,
        "feature_count": 5, "command": "filter",
    })
    await session_data_manager.append_event(session_id, "tool_executed", {
        "tool": "create_heatmap", "status": "completed", "ref": r2,
        "feature_count": 5, "command": "heatmap",
    })

    plan = Plan(
        intent="分析成都市医院分布并生成热力图",
        domains=["statistics", "core"],
        steps=[
            PlanStep(n=1, goal="搜索成都市医院 POI 数据", tool_family="core", done=True),
            PlanStep(n=2, goal="对医院点做空间过滤", tool_family="core", done=True),
            PlanStep(n=3, goal="生成医院分布热力图", tool_family="statistics", done=False),
            PlanStep(n=4, goal="叠加底图并返回结果", tool_family="core", done=False),
        ],
    )
    plan_orchestrator.set_plan(session_id, plan)

    if long_msgs:
        user_msgs = [
            ("帮我分析一下成都市各区县医院的分布情况，看看有没有明显聚集的区域，"
             "如果聚集比较明显，我希望能看到每个聚集点的范围和大致数量，"
             "顺便把结果和道路网叠加起来看"),
            ("对刚才的医院点数据做一次热点分析，用核密度估计，"
             "带宽选 800 米，输出热力图层，同时给我列出聚集最明显的三个区域"),
        ]
        asst_msgs = [
            ("好的，我先搜索成都市的医院 POI 数据。已获取 215 个医院点，"
             "保存在 ref:geojson-xxxx 中。接下来我会对数据做空间过滤，"
             "去掉明显不属于主城区的点位，然后生成分布热力图。"),
            ("热点分析已完成，结果已保存为 ref:geojson-xxxx。聚集最明显的三个区域是："
             "春熙路商圈（约 42 个点）、华西坝（约 38 个点）、火车南站（约 31 个点）。"
             "如果需要，我可以进一步计算这些区域的面积和重叠情况。"),
        ]
    else:
        user_msgs = [
            "帮我分析成都市医院的分布情况，看看有没有集中的区域",
            "对医院点做热点分析，结果保存到 " + r1,
            "把热力图叠加到底图上显示",
            "把这些图层都显示出来",
        ]
        asst_msgs = [
            "好的，我先搜索成都的医院 POI 数据。",
            "热点分析完成，结果已保存为 " + r1 + "。",
            "已叠加热力图到底图。",
            "好的，图层已全部显示：" + r1 + "（医院）、" + r2 + "（道路）。",
        ]

    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(skill_list="（暂无预置技能）")}]
    for i in range(n_turns):
        messages.append({"role": "user", "content": user_msgs[i % len(user_msgs)]})
        messages.append({"role": "assistant", "content": asst_msgs[i % len(asst_msgs)]})
    return messages


def block_breakdown(messages: list[dict]) -> dict:
    """Per-block char counts of an assembled message list (v3 / master shapes)."""
    blocks = []
    total = 0
    for m in messages:
        c = str(m.get("content", ""))
        total += len(c)
        blocks.append(len(c))
    return {"blocks": blocks, "total_chars": total, "n_msgs": len(messages)}


def plan_block_chars(messages: list[dict]) -> int:
    """Chars of the [执行计划] block (second system message when a plan exists)."""
    for m in messages:
        if m.get("role") == "system" and str(m.get("content", "")).startswith("[执行计划]"):
            return len(str(m.get("content", "")))
    return 0


def last_analysis_chars(messages: list[dict]) -> int:
    """Chars of the [最近对话上下文] block, or 0 when absent."""
    for m in messages:
        if m.get("role") == "system" and str(m.get("content", "")).startswith("[最近对话上下文]"):
            return len(str(m.get("content", "")))
    return 0
