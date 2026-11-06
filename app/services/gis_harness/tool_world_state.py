"""Independently testable webgis_world_state handler and explicit registration."""
from __future__ import annotations
from app.tools.registry import tool

from app.services.gis_harness.tools import (
    ToolRegistry,
    Optional,
)


async def webgis_world_state(session_id: Optional[str] = None) -> dict:
    if not session_id:
        return {"success": False, "message": "Missing session_id"}
    from app.services.gis_world_state import build_world_state

    try:
        snapshot = await build_world_state(session_id)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "message": f"world state unavailable: {e}"}
    return {
        "success": True,
        **snapshot,
        "summary": (
            f"revision={snapshot.get('revision')}，{snapshot.get('layer_count_total')} 层，"
            f"{len(snapshot.get('components') or [])} 组件，"
            f"用户隐藏 {len((snapshot.get('interaction') or {}).get('user_hidden_layers') or [])} 层"
        ),
    }



def register_world_state(registry: ToolRegistry) -> None:
    tool(
        registry,
        tier=2, domains=["mapspec"], name="webgis_world_state",
        capabilities=['workspace_state_inspection'],
        description=(
            "读取当前会话的 GIS 世界状态快照（只读、有界、无数据负载）。"
            "一次返回：revision、视口、底图、图层列表（id/类型/可见性/不透明度/"
            "角色/制图意图）、数据源摘要（ref 与要素计数，不含几何）、制图组件、"
            "用户交互决策（user_hidden_layers——用户手动隐藏的层，Agent 不得覆盖）、"
            "制图质量评审摘要、最近观察与 provenance 决策链。"
            "\n『地图现在什么状态？』『哪些层是用户手动藏的？』『这个层为什么看不见？』"
            "——先读世界状态再决策/再解释。"
        ),
        side_effect="pure",
        deterministic=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("世界状态", "快照", "图层清单", "视口", "底图", "用户隐藏", "只读"),
        output_semantic_type="text",
        result_size_policy="bounded",
    )(webgis_world_state)
