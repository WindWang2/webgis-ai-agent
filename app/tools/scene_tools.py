"""Scene tools — plan / set multiscale scene configuration (ADR-0201).

两个 agent 面：

- ``plan_map_scene``（只读）：把已核实证据（height 字段证据、elevation
  证据、几何、媒介）投影为确定性 SceneDecision + 可直接写入的 scene
  配置建议。**不写任何状态**。无证据时 fail-closed —— 绝不建议 3D。
- ``set_map_scene``（事务）：经 ``mapspec_store.set_scene``（SetSceneIntent
  全事务管线：锁/CAS/dedup/checkpoint/回滚）写入顶层 scene。presentation
  面 —— 不触碰 layers/sources/legend_spec（统计/分级/图例不漂移由构造
  保证）。

证据诚实边界：工具不替 agent 编造 ``has_*_evidence`` —— 传了什么就规划
什么；证据核实责任在调用方（如 extrusion 工具的 stats.valid、DEM ref
的注册回执）。
"""
import logging
from typing import Any, Dict, List, Optional

from app.tools.registry import ToolRegistry, tool
from app.services.mapspec_store import mapspec_store

logger = logging.getLogger(__name__)

_EVIDENCE_KEYS = (
    "is_compiled",
    "warnings",
    "checkpoint_id",
    "correction_hint",
    "message",
    "mutation_revision",
)


def register_scene_tools(registry: ToolRegistry):
    """注册多尺度场景工具面（ADR-0201）。"""

    @tool(registry, name="plan_map_scene",
          capabilities=["thematic_cartography"],
          description=(
              "规划多尺度场景档位（2d/2.5d/3d）。只读：基于已核实证据确定性"
              "决策是否使用地形/立体挤出、推荐相机档与垂直夸张，并给出可直接"
              "传给 set_map_scene 的 scene 配置。无高度/高程证据时绝不建议 3D"
              "（fail-closed）。"
          ),
          param_descriptions={
              "has_height_attribute_evidence": "已核实：要素存在数值型高度字段（如 extrusion 工具 stats.valid>0）",
              "has_elevation_evidence": "已核实：存在可用 DEM/raster-dem 源（如 ref:raster-* 已注册）",
              "vertical_extrusion_intent": "产品意图：需要立体挤出表达",
              "terrain_intent": "产品意图：需要地形/晕渲表达",
              "geometry_kinds": "数据几何类型列表（point/line/polygon/raster）",
              "medium": "输出媒介（interactive/print/export_pdf/export_svg…）",
              "purpose": "产品目的（exploration/analysis/communication/storytelling/comparison/monitoring）",
              "reduced_motion": "可访问性：减少动效（相机过渡归零，档位不变）",
              "exaggeration_request": "请求的垂直夸张系数（>1.5 会触发失真披露；上限 10）",
              "terrain_source": "决策含地形时绑定：spec 内 raster-dem 源 id（写入 suggested_scene.terrain.source；缺省时建议省略 terrain 并披露 —— 无源 terrain 会被 set_map_scene 拒绝）",
          },
          side_effect="cacheable_read",
          deterministic=True,
          latency_class="fast",
          memory_class="light",
          scale_class="small",
          tags=("scene", "3d", "地形", "挤出", "规划", "场景"),
          output_semantic_type="plan",
          failure_modes=("invalid_args",))
    async def plan_map_scene(
        has_height_attribute_evidence: bool = False,
        has_elevation_evidence: bool = False,
        vertical_extrusion_intent: bool = False,
        terrain_intent: bool = False,
        geometry_kinds: Optional[List[str]] = None,
        medium: str = "interactive",
        purpose: str = "analysis",
        reduced_motion: bool = False,
        exaggeration_request: Optional[float] = None,
        terrain_source: Optional[str] = None,
    ) -> dict:
        try:
            from app.lib.cartography.scene_planning import (
                SceneIntent,
                decision_to_scene_config,
                plan_scene,
            )

            allowed_geometry = {"point", "line", "polygon", "raster"}
            kinds = [k for k in (geometry_kinds or []) if k in allowed_geometry]

            intent = SceneIntent(
                purpose=purpose if purpose in {
                    "exploration", "analysis", "communication", "storytelling",
                    "comparison", "monitoring",
                } else "analysis",
                medium=medium if medium in {
                    "interactive", "screen", "print", "export_png",
                    "export_pdf", "export_svg",
                } else "interactive",
                motion="reduced" if reduced_motion else "full",
                geometry_kinds=kinds,  # type: ignore[arg-type]
                has_height_attribute_evidence=bool(has_height_attribute_evidence),
                has_elevation_evidence=bool(has_elevation_evidence),
                vertical_extrusion_intent=bool(vertical_extrusion_intent),
                terrain_intent=bool(terrain_intent),
                exaggeration_request=exaggeration_request,
            )
            decision = plan_scene(intent)
            suggested = decision_to_scene_config(decision)
            # review P1-3：terrain.source 绑定 —— Schema/SetSceneIntent 要求
            # 非空 source id；决策含地形但调用方未绑定源时，诚实省略 terrain
            # 并披露原因，绝不产出会被 set_map_scene 拒绝的半成品建议。
            terrain_source_note = None
            if decision.terrain:
                if terrain_source and isinstance(terrain_source, str):
                    suggested["terrain"]["source"] = terrain_source
                else:
                    suggested.pop("terrain", None)
                    terrain_source_note = (
                        "决策建议地形，但未提供 terrain_source（spec 内 raster-dem "
                        "源 id）；suggested_scene 已省略 terrain —— 请先注册 "
                        "raster-dem 源，再带 terrain_source 重新规划。"
                    )
            out = {
                "success": True,
                "decision": decision.model_dump(),
                "suggested_scene": suggested,
                "evidence_policy": (
                    "has_*_evidence 必须来自已核实的数据采样结论；"
                    "本工具不核实证据，只按输入确定性决策"
                    "（fail-closed：无证据不产出垂直表达）"
                ),
            }
            if terrain_source_note:
                out["terrain_source_note"] = terrain_source_note
            return out
        except Exception as e:  # noqa: BLE001 — 工具边界统一错误面
            logger.error("plan_map_scene failed: %s", e)
            return {"success": False, "error": str(e)}

    @tool(registry, name="set_map_scene",
          capabilities=["thematic_cartography"],
          description=(
              "设置地图场景（2d/2.5d/3d + 地形参数 + 相机建议档）。"
              "presentation 面事务写入：不改变数据图层/统计分级/图例（同一"
              "产品在 2D↔3D 间切换不重跑分析）。terrain.source 必须是 spec "
              "内已存在的 raster-dem 源 id；scene=None 清除场景回到平面。"
              "建议先用 plan_map_scene 得到 suggested_scene 再传入。"
          ),
          param_descriptions={
              "session_id": "会话 ID",
              "scene": "场景配置对象：mode 必填（2d/2.5d/3d）；"
                       "terrain{source,exaggeration,vertical_unit,elevation_ref}；"
                       "camera{pitch,bearing,transition_ms}；省略/None = 清除场景",
          },
          side_effect="state_mutation",
          deterministic=True,
          latency_class="fast",
          memory_class="light",
          scale_class="small",
          tags=("scene", "3d", "地形", "场景", "切换"),
          output_semantic_type="map_product",
          required_context=("map_state",),
          map_mutations=("theme",),
          failure_modes=("invalid_args", "revision_conflict"))
    async def set_map_scene(
        session_id: Optional[str] = None,
        scene: Optional[Dict[str, Any]] = None,
    ) -> dict:
        if not session_id:
            return {"success": False, "message": "Missing session_id", "command": "set_map_scene"}
        try:
            res = await mapspec_store.set_scene(session_id, scene)
            out: Dict[str, Any] = {
                "success": bool(res.get("success", False)),
                "command": "set_map_scene",
            }
            for key in _EVIDENCE_KEYS:
                if key in res and res[key] is not None:
                    out[key] = res[key]
            if res.get("mapspec") is not None:
                out["scene_applied"] = (res.get("mapspec") or {}).get("scene")
            return out
        except Exception as e:  # noqa: BLE001
            logger.error("set_map_scene failed: %s", e)
            return {"success": False, "error": str(e), "command": "set_map_scene"}
