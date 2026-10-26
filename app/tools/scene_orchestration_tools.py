"""Scene orchestration tools — G3D04 Agent 三维编排工具面（ADR-0199 之上）。

四个 agent 面（全部编译为既有通道，零第二真相）：

- ``get_3d_capabilities``（只读）：typed 3D 能力快照——available/
  unavailable + alternatives；缺能力时明确报告，绝不只在 prompt 里列假工具。
- ``apply_scene_representation``（事务）：用户偏好 × 证据 × 媒介 →
  2d/2.5d/3d 决策（核是既有 ``plan_scene``）→ 既有 ``SetSceneIntent``
  （mapspec_store.set_scene 全事务管线）。dry_run 只回决策不写状态。
- ``fly_camera``（事务）：相机动作 → 既有 ``SetViewIntent``
  （mapspec_store.set_view）；user-wins 守卫（Interaction IR viewport 偏离
  / IR scene_camera 锁）命中时 typed 抑制，绝不偷改用户镜头。
- ``run_3d_analysis``（分析）：capability 门 → 既有 ``viewshed_analysis``
  经 registry 真实 dispatch（单一执行真相）→ 证据 + 失效绑定记录
  （观察点/DEM/结果层 → staleness 裁决面）。

证据诚实边界：垂直证据只认 spec 内真实 raster-dem 源与 extrusion 采样
结论（scene3d.evidence）；无证据 → 诚实降级（偏好保留 + typed 注记），
绝不伪造高度/地形。
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

_LAYER_TYPE_TO_GEOMETRY = {
    "circle": "point", "symbol": "point", "heatmap": "point",
    "line": "line", "fill": "polygon", "fill-extrusion": "polygon",
    "raster": "raster", "hillshade": "raster",
}


def _geometry_kinds_of(mapspec: Optional[Dict[str, Any]]) -> List[str]:
    kinds = set()
    for layer in (mapspec or {}).get("layers") or []:
        if isinstance(layer, dict) and layer.get("type") in _LAYER_TYPE_TO_GEOMETRY:
            kinds.add(_LAYER_TYPE_TO_GEOMETRY[layer["type"]])
    return sorted(kinds)


async def _interaction_snapshot(session_id: str) -> Dict[str, Any]:
    from app.services.gis_harness.interaction_ir.projection import (
        interaction_snapshot_via_store,
    )
    from app.services.session_data import session_data_manager

    return await interaction_snapshot_via_store(session_data_manager, session_id)


def register_scene_orchestration_tools(registry: ToolRegistry):
    """注册 G3D04 场景编排工具面。"""

    @tool(registry, name="get_3d_capabilities",
          capabilities=["scene_representation", "terrain_scene_binding",
                        "scene_camera_action"],
          tier=2, domains=["mapspec"],
          description=(
              "探测当前会话的 3D 能力快照（typed）：scene_mode/terrain/"
              "extrusion/camera/viewshed_analysis 等各项 available 或 "
              "unavailable + reason + alternatives。证据只认 spec 内真实 "
              "raster-dem 源与 extrusion 采样结论；缺能力时返回替代路径，"
              "供决策与诚实降级使用（不是工具清单页）。"
          ),
          param_descriptions={
              "session_id": "会话 ID（读当前 MapSpec 作证据源）",
              "medium": "输出媒介（缺省 interactive；静态媒介影响 3D 档结论）",
          },
          side_effect="cacheable_read",
          network=False,
          deterministic=True,
          latency_class="fast",
          memory_class="light",
          scale_class="small",
          tags=("scene", "3d", "能力发现", "capability", "能力"),
          output_semantic_type="diagnostic",
          result_size_policy="inline_small",
          failure_modes=("invalid_args", "missing_data"),
          )
    async def get_3d_capabilities(
        session_id: Optional[str] = None,
        medium: str = "interactive",
    ) -> dict:
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "command": "get_3d_capabilities"}
        try:
            from app.services.gis_harness.scene3d.capabilities import (
                probe_scene3d_capabilities,
            )
            from app.services.gis_harness.scene3d.evidence import (
                verify_vertical_evidence,
            )

            mapspec = await mapspec_store.get_mapspec(session_id)
            evidence = verify_vertical_evidence(mapspec)
            snapshot = probe_scene3d_capabilities(
                evidence, geometry_kinds=_geometry_kinds_of(mapspec),
                medium=medium if medium else "interactive",
            )
            return {
                "success": True,
                "command": "get_3d_capabilities",
                "capabilities": [e.model_dump() for e in snapshot.entries],
                "dem_source_ids": snapshot.dem_source_ids,
                "evidence": {
                    "has_elevation": evidence.has_elevation,
                    "has_height_attribute": evidence.has_height,
                    "records": [r.model_dump() for r in evidence.to_records()],
                },
                "notes": snapshot.notes,
            }
        except Exception as e:  # noqa: BLE001 — 工具边界统一错误面
            logger.error("get_3d_capabilities failed: %s", e)
            return {"success": False, "error": str(e),
                    "command": "get_3d_capabilities"}

    @tool(registry, name="apply_scene_representation",
          capabilities=["scene_representation", "terrain_scene_binding"],
          tier=2, domains=["mapspec"],
          description=(
              "应用三维表达决策：用户显式偏好（dimension/terrain/extrusion/"
              "exaggeration）× 已核实垂直证据 × 输出媒介 → 2d/2.5d/3d 场景档"
              "（决策核是确定性 plan_scene），编译为既有 SetSceneIntent 事务"
              "写入。无证据时 fail-closed 诚实降级（degradations 返回 typed "
              "注记，用户偏好不撤回）。dry_run=True 只回决策不写状态。"
          ),
          param_descriptions={
              "session_id": "会话 ID",
              "user_dimension": "用户显式维度偏好（2d/2.5d/3d；空=未表达，零推断）",
              "terrain_requested": "用户显式地形表达（None=未表达）",
              "extrusion_requested": "用户显式立体挤出（None=未表达）",
              "exaggeration": "请求的垂直夸张系数（钳制上限 10）",
              "medium": "输出媒介（interactive/screen/print/export_*；静态媒介禁 3D）",
              "purpose": "产品目的（exploration/analysis/communication/storytelling/comparison/monitoring）",
              "reduced_motion": "减少动效（相机过渡归零）",
              "dem_source_id": "terrain 绑定的 spec 内 raster-dem 源 id（缺省取第一个已核实源）",
              "dry_run": "True 只返回决策与场景配置建议，不写 MapSpec",
          },
          side_effect="state_mutation",
          network=False,
          deterministic=True,
          latency_class="fast",
          memory_class="light",
          scale_class="small",
          tags=("scene", "3d", "representation", "降级", "场景"),
          output_semantic_type="map_product",
          result_size_policy="inline_small",
          required_context=("map_state",),
          map_mutations=("theme",),
          failure_modes=("invalid_args", "revision_conflict", "missing_data"),
          )
    async def apply_scene_representation(
        session_id: Optional[str] = None,
        user_dimension: str = "",
        terrain_requested: Optional[bool] = None,
        extrusion_requested: Optional[bool] = None,
        exaggeration: Optional[float] = None,
        medium: str = "interactive",
        purpose: str = "analysis",
        reduced_motion: bool = False,
        dem_source_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "command": "apply_scene_representation"}
        try:
            from app.services.gis_harness.scene3d.capabilities import (
                probe_scene3d_capabilities,
            )
            from app.services.gis_harness.scene3d.decision import (
                decide_representation,
            )
            from app.services.gis_harness.scene3d.evidence import (
                verify_vertical_evidence,
            )

            mapspec = await mapspec_store.get_mapspec(session_id)
            evidence = verify_vertical_evidence(mapspec)
            capabilities = probe_scene3d_capabilities(
                evidence, geometry_kinds=_geometry_kinds_of(mapspec), medium=medium)
            decision = decide_representation(
                user_dimension=user_dimension if user_dimension in ("2d", "2.5d", "3d") else "",
                terrain_requested=terrain_requested,
                extrusion_requested=extrusion_requested,
                exaggeration=exaggeration,
                medium=medium, purpose=purpose, reduced_motion=reduced_motion,
                geometry_kinds=_geometry_kinds_of(mapspec),
                evidence=evidence, capabilities=capabilities,
                dem_source_id=dem_source_id,
            )
            out: Dict[str, Any] = {
                "success": True,
                "command": "apply_scene_representation",
                "decision": decision.model_dump(exclude={"scene_config"}),
            }
            if dry_run:
                out["suggested_scene"] = decision.scene_config
                out["dry_run"] = True
                return out
            res = await mapspec_store.set_scene(session_id, decision.scene_config)
            out["applied"] = bool(res.get("success", False))
            for key in _EVIDENCE_KEYS:
                if key in res and res[key] is not None:
                    out[key] = res[key]
            if res.get("mapspec") is not None:
                out["scene_applied"] = (res.get("mapspec") or {}).get("scene")
            return out
        except Exception as e:  # noqa: BLE001
            logger.error("apply_scene_representation failed: %s", e)
            return {"success": False, "error": str(e),
                    "command": "apply_scene_representation"}

    @tool(registry, name="fly_camera",
          capabilities=["scene_camera_action"],
          tier=2, domains=["mapspec"],
          description=(
              "3D 相机动作（pitch/bearing/center/zoom，编译为既有 "
              "SetViewIntent 事务）。user-wins 守卫：用户 viewport 与 spec "
              "偏离（手动调镜头未落 spec）或 IR scene_camera 锁命中时，"
              "动作被 typed 抑制（status=user_wins_suppressed）——绝不偷改"
              "用户镜头；可据 suppressed_axes 只动未锁轴。"
          ),
          param_descriptions={
              "session_id": "会话 ID",
              "pitch": "目标俯仰角（0-85 度；缺省不改）",
              "bearing": "目标方位角（-180..180；缺省不改）",
              "center": "目标中心 [lng, lat]（缺省不改）",
              "zoom": "目标缩放级（缺省不改）",
          },
          side_effect="state_mutation",
          network=False,
          deterministic=True,
          latency_class="fast",
          memory_class="light",
          scale_class="small",
          tags=("scene", "3d", "camera", "相机", "视角", "user-wins"),
          output_semantic_type="map_product",
          result_size_policy="inline_small",
          required_context=("map_state",),
          map_mutations=("camera",),
          failure_modes=("invalid_args", "revision_conflict"),
          )
    async def fly_camera(
        session_id: Optional[str] = None,
        pitch: Optional[float] = None,
        bearing: Optional[float] = None,
        center: Optional[List[float]] = None,
        zoom: Optional[float] = None,
    ) -> dict:
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "command": "fly_camera"}
        try:
            from app.services.gis_harness.scene3d.camera import plan_camera

            requested: Dict[str, Any] = {}
            if pitch is not None:
                requested["pitch"] = min(85.0, max(0.0, float(pitch)))
            if bearing is not None:
                raw_bearing = float(bearing) % 360.0
                requested["bearing"] = raw_bearing - 360.0 if raw_bearing > 180.0 else raw_bearing
            if center:
                requested["center"] = [float(center[0]), float(center[1])]
            if zoom is not None:
                requested["zoom"] = max(0.0, min(24.0, float(zoom)))

            mapspec = await mapspec_store.get_mapspec(session_id)
            spec_view = (mapspec or {}).get("view") or {}
            snapshot = await _interaction_snapshot(session_id)
            camera_locks: List[str] = []
            user_camera: Dict[str, Any] = {}
            try:
                from app.services.gis_harness.requirement_ir.service import (
                    get_requirement_view,
                )
                from app.services.session_data import session_data_manager

                view = await get_requirement_view(session_data_manager, session_id)
                if isinstance(view, dict):
                    camera_locks = [
                        str(lock.get("value"))
                        for lock in view.get("locks") or []
                        if isinstance(lock, dict)
                        and lock.get("scope") == "scene_camera"
                        and lock.get("value") in ("pitch", "bearing")
                    ]
                    scene = view.get("scene")
                    if isinstance(scene, dict):
                        # 用户显式相机偏好（等值下发=执行用户选择）
                        if scene.get("camera_pitch") is not None:
                            user_camera["pitch"] = scene["camera_pitch"]
                        if scene.get("camera_bearing") is not None:
                            user_camera["bearing"] = scene["camera_bearing"]
            except Exception:  # noqa: BLE001 — IR 缺席不阻塞相机（守卫主面是交互快照）
                camera_locks = []

            plan = plan_camera(
                requested,
                interaction_snapshot=snapshot,
                spec_view=spec_view if isinstance(spec_view, dict) else {},
                camera_locks=camera_locks,
                user_camera=user_camera,
            )
            out: Dict[str, Any] = {
                "success": True,
                "command": "fly_camera",
                "status": plan.status,
                "reason_code": plan.reason_code,
                "detail": plan.detail,
            }
            if plan.suppressed_axes:
                out["suppressed_axes"] = plan.suppressed_axes
            if plan.status != "apply":
                out["applied"] = False
                return out
            res = await mapspec_store.set_view(
                session_id,
                center=plan.view.get("center"),
                zoom=plan.view.get("zoom"),
                pitch=plan.view.get("pitch"),
                bearing=plan.view.get("bearing"),
            )
            out["applied"] = bool(res.get("success", False))
            for key in _EVIDENCE_KEYS:
                if key in res and res[key] is not None:
                    out[key] = res[key]
            return out
        except Exception as e:  # noqa: BLE001
            logger.error("fly_camera failed: %s", e)
            return {"success": False, "error": str(e), "command": "fly_camera"}

    @tool(registry, name="run_3d_analysis",
          capabilities=["scene_representation"],
          tier=2, domains=["raster"],
          description=(
              "3D 分析编排（当前支持 viewshed）：先过 capability 门（DEM 证据 +"
              " viewshed_analysis 工具注册），不可用即 typed decline + "
              "alternatives；可用则经 registry 真实 dispatch viewshed_analysis"
              "（单一执行真相），返回科学证据 + MethodUseContract 摘要 + "
              "失效绑定（观察点/DEM 源/结果层，供 staleness 裁决与重算）。"
              "\n何时不用：非视域分析（line_of_sight/skyline 待科学面补齐——"
              "decline 会给 alternatives）。"
          ),
          param_descriptions={
              "session_id": "会话 ID",
              "analysis": "分析目标（当前仅 viewshed）",
              "raster_path": "DEM GeoTIFF 路径（data_dir 内；来自数据面步骤的真实 ref）",
              "observer_x": "观察点世界 x（栅格 CRS 单位）",
              "observer_y": "观察点世界 y（栅格 CRS 单位）",
              "observer_height": "观察者离地高度（米，默认 2）",
              "target_height": "目标离地高度（米，默认 0）",
              "max_distance": "最大视线距离（米，默认 5000）",
              "dem_source_id": "绑定的 spec 内 raster-dem 源 id（失效对账用，可选）",
              "result_layer_id": "结果挂载层 id（失效对账用，可选）",
              "observer_feature_id": "观察点要素 id（用户点选语义，失效对账用，可选）",
          },
          side_effect="deterministic_compute",
          network=False,
          deterministic=True,
          latency_class="medium",
          memory_class="heavy",
          scale_class="large",
          tags=("3d", "viewshed", "视域", "通视", "编排", "失效"),
          output_semantic_type="stats",
          result_size_policy="inline_small",
          crs_semantics="crs_agnostic",
          failure_modes=("invalid_args", "missing_data", "capability_unavailable", "memory"),
          )
    async def run_3d_analysis(
        session_id: Optional[str] = None,
        analysis: str = "viewshed",
        raster_path: str = "",
        observer_x: Optional[float] = None,
        observer_y: Optional[float] = None,
        observer_height: float = 2,
        target_height: float = 0,
        max_distance: float = 5000,
        dem_source_id: Optional[str] = None,
        result_layer_id: Optional[str] = None,
        observer_feature_id: Optional[str] = None,
    ) -> dict:
        command = "run_3d_analysis"
        if not session_id:
            return {"success": False, "message": "Missing session_id", "command": command}
        try:
            from app.services.gis_harness.scene3d.capabilities import (
                probe_scene3d_capabilities,
            )
            from app.services.gis_harness.scene3d.evidence import (
                verify_vertical_evidence,
            )

            if analysis != "viewshed":
                return {
                    "success": False,
                    "command": command,
                    "declined": True,
                    "reason_code": "SCENE_CAP_ALGORITHM_PENDING_G3D03",
                    "message": f"analysis={analysis!r} 暂不可用",
                    "alternatives": [
                        "viewshed（视域掩膜；语义差异须披露）"] if analysis in (
                        "line_of_sight", "skyline") else [],
                }

            mapspec = await mapspec_store.get_mapspec(session_id)
            evidence = verify_vertical_evidence(mapspec)
            capabilities = probe_scene3d_capabilities(
                evidence, geometry_kinds=_geometry_kinds_of(mapspec))
            entry = capabilities.entry("viewshed_analysis")
            if entry is None or entry.unavailable:
                return {
                    "success": False,
                    "command": command,
                    "declined": True,
                    "reason_code": entry.reason_code if entry else "SCENE_CAP_UNKNOWN",
                    "message": "viewshed 能力不可用（capability 门拒绝）",
                    "alternatives": entry.alternatives if entry else [],
                }
            if not raster_path:
                return {"success": False, "command": command,
                        "declined": True,
                        "reason_code": "SCENE_ANALYSIS_MISSING_RASTER_PATH",
                        "message": "缺 raster_path（DEM GeoTIFF 真实 ref 路径）——"
                                   "本工具不猜测数据路径",
                        "alternatives": []}
            if observer_x is None or observer_y is None:
                return {"success": False, "command": command,
                        "declined": True,
                        "reason_code": "SCENE_ANALYSIS_MISSING_OBSERVER",
                        "message": "缺观察点坐标（observer_x/observer_y）",
                        "alternatives": []}

            from app.agent_pi_bridge import try_get_tool_registry
            registry = try_get_tool_registry()
            if registry is None or not registry.has("viewshed_analysis"):
                return {"success": False, "command": command, "declined": True,
                        "reason_code": "SCENE_CAP_TOOL_NOT_REGISTERED",
                        "message": "viewshed_analysis 未注册", "alternatives": []}

            result = await registry.dispatch("viewshed_analysis", {
                "raster_path": raster_path,
                "observer_x": float(observer_x),
                "observer_y": float(observer_y),
                "observer_height": float(observer_height),
                "target_height": float(target_height),
                "max_distance": float(max_distance),
            }, session_id=session_id)
            if not isinstance(result, dict) or not result.get("success"):
                return {
                    "success": False,
                    "command": command,
                    "declined": False,
                    "message": "viewshed_analysis 执行失败（真实 dispatch）",
                    "execution": result if isinstance(result, dict) else None,
                }

            # MethodUseContract 摘要（科学适用性披露面；权威在 registries）
            method_digest: Dict[str, Any] = {}
            try:
                from app.lib.gis.method_use_contract import build_method_use_contract
                contract = build_method_use_contract("terrain.viewshed")
                method_digest = {
                    "algorithm_id": contract.algorithm_id,
                    "capabilities": contract.capabilities,
                    "scientific_preconditions": contract.scientific_preconditions,
                    "limitations": contract.limitations,
                    "assumptions": contract.assumptions,
                    "method_references": contract.method_references,
                    "findings": contract.findings,
                }
            except Exception as exc:  # noqa: BLE001 — 披露面失败不吞分析结果
                method_digest = {"error": str(exc)[:200]}

            binding = {
                "analysis_goal": "viewshed",
                "result_layer_id": result_layer_id or "",
                "observer_feature_id": observer_feature_id or "",
                "dem_source_id": dem_source_id
                or (evidence.dem_source_ids()[:1] or [""])[0],
                "observer_x": float(observer_x),
                "observer_y": float(observer_y),
                "staleness_schema": "scene3d_staleness.v1",
            }
            return {
                "success": True,
                "command": command,
                "analysis": "viewshed",
                "result": result,
                "method_use_contract": method_digest,
                "staleness_binding": binding,
            }
        except Exception as e:  # noqa: BLE001
            logger.error("run_3d_analysis failed: %s", e)
            return {"success": False, "error": str(e), "command": command}
