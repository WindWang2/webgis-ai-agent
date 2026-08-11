"""webgis_* Canonical Cartography Tools for MapSpec Harness."""
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.services.mapspec_store import mapspec_store
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

# HARNESS-V3 / BE-3: adapter 结果中的证据字段，必须透传到工具结果——
# harness MapSpecValidity 阶梯读 is_compiled；被拒绝的 mutation 需要
# message + correction_hint 让 LLM 自愈（此前包装层硬编码 success:True，
# 丢弃全部证据，导致生产证据链断裂）。
_EVIDENCE_KEYS = ("is_compiled", "warnings", "checkpoint_id", "correction_hint", "message")


def _forward_evidence(res: Dict[str, Any], out: Dict[str, Any]) -> Dict[str, Any]:
  """透传 adapter 证据字段，并从 adapter 结果推导 success（不再硬编码 True）。"""
  out["success"] = bool(res.get("success", False))
  for key in _EVIDENCE_KEYS:
    if key in res and res[key] is not None:
      out[key] = res[key]
  return out


class WebgisProjectInitArgs(BaseModel):
  view: Optional[Dict[str, Any]] = Field(default=None, description="初始视图，如 {'center': [120.1, 30.2], 'zoom': 10}")
  thresholds: Optional[Dict[str, Any]] = Field(default=None, description="运行阈值，如 {'maxFeatures': 50000}")


class WebgisViewSetArgs(BaseModel):
  center: Optional[List[float]] = Field(default=None, description="中心点经纬度 [lng, lat]")
  zoom: Optional[float] = Field(default=None, description="缩放级别 (1~20)")
  pitch: Optional[float] = Field(default=None, description="倾角 (0~85)")
  bearing: Optional[float] = Field(default=None, description="旋转角 (-180~180)")


class WebgisStateGetArgs(BaseModel):
  pass


class WebgisSourceProfileArgs(BaseModel):
  source_id: str = Field(..., description="数据源唯一标识符，如 'earthquakes'")
  geojson_data: Any = Field(..., description="GeoJSON 数据对象、数据引用 ref:xxx 或 URL/文件路径")


class WebgisLayerUpsertArgs(BaseModel):
  layer: Dict[str, Any] = Field(..., description="图层规范对象 (包含 id, source, type, paint, layout, label)")
  source_data: Optional[Any] = Field(default=None, description="可选的 GeoJSON 数据对象或引用 ref:xxx，供自动分析与视图计算")


class WebgisLayerRemoveArgs(BaseModel):
  layer_id: str = Field(..., description="要移除的图层 ID")


class WebgisLayoutSetArgs(BaseModel):
  legend: Optional[Dict[str, Any]] = Field(default=None, description="图例配置，如 {'title': '标题', 'position': 'top-right', 'visible': True}")
  controls: Optional[List[Dict[str, Any]]] = Field(default=None, description="地图控件配置列表")
  margins: Optional[Dict[str, Any]] = Field(default=None, description="边距配置")


class WebgisValidateArgs(BaseModel):
  pass


class WebgisCompileMaplibreArgs(BaseModel):
  pass


class WebgisCheckpointArgs(BaseModel):
  checkpoint_id: Optional[str] = Field(default=None, description="可选的快照名称 ID")


class WebgisRollbackArgs(BaseModel):
  checkpoint_id: str = Field(..., description="要回滚到的快照 ID")


class WebgisRuntimeValidateArgs(BaseModel):
  pass


def register_mapspec_cartography_tools(registry: ToolRegistry) -> None:
  """注册 MapSpec Harness 规范化 webgis_* 工具。"""

  @tool(
      registry,
      name="webgis_project_init",
      description="初始化当前会话的 MapSpec 制图 Intent 文档与基线配置。",
      args_model=WebgisProjectInitArgs,
      tier=1,
  )
  async def webgis_project_init(
      view: Optional[dict] = None,
      thresholds: Optional[dict] = None,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    res = await mapspec_store.init_project(session_id, view, thresholds)
    out: Dict[str, Any] = {"mapspec": res.get("mapspec")}
    if res.get("success"):
      out["summary"] = "MapSpec project initialized"
    return _forward_evidence(res, out)

  @tool(
      registry,
      name="webgis_state_get",
      description="读取当前会话的 MapSpec 制图 Intent 文档与 MapMeta Profile。",
      args_model=WebgisStateGetArgs,
      tier=1,
  )
  async def webgis_state_get(session_id: Optional[str] = None) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    mapspec = await mapspec_store.get_mapspec(session_id)
    if not mapspec:
      res = await mapspec_store.init_project(session_id)
      mapspec = res["mapspec"]
    return {
        "success": True,
        "mapspec": mapspec,
        "summary": "MapSpec state retrieved",
    }

  @tool(
      registry,
      name="webgis_view_set",
      description="设置或覆盖地图视图参数 (center, zoom, pitch, bearing)，并同步重新编译 runtime map_state。",
      args_model=WebgisViewSetArgs,
      tier=1,
  )
  async def webgis_view_set(
      center: Optional[List[float]] = None,
      zoom: Optional[float] = None,
      pitch: Optional[float] = None,
      bearing: Optional[float] = None,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    res = await mapspec_store.set_view(session_id, center, zoom, pitch, bearing)
    view = (res.get("mapspec") or {}).get("view", {})
    out: Dict[str, Any] = {"view": view}
    if res.get("success"):
      # Round-2 P2：LLM 可见文本绝不声称相机已移动 —— 工具层只完成了 mapspec.view
      # 写入 + 下发 fly_to 指令，前端是否真正落定相机由 ACK 证据闭环判定
      # （后端 _is_verifiable_ack 从 actual 重算收敛）。此前 "View updated to …"
      # 在没有任何实时 ACK 前就向 LLM 宣称更新完成，属于"假成功"自我表扬。
      out["summary"] = "视图指令已下发，等待前端执行"
      # HARNESS-V3 / BE-3: 此前只写 mapspec.view，实时相机从不动。这里在调用方
      # 确实传了视图参数时，同步 runtime map_state.viewport（无 seq 的服务端真相
      # 写入，同 ws_service 的 viewport 契约），并下发 fly_to 命令让前端相机移动。
      fly_params: Dict[str, Any] = {}
      if center is not None:
        fly_params["center"] = center
      if zoom is not None:
        fly_params["zoom"] = zoom
      if pitch is not None:
        fly_params["pitch"] = pitch
      if bearing is not None:
        fly_params["bearing"] = bearing
      if fly_params:
        current = (await session_data_manager.get_map_state(session_id)).get("viewport") or {}
        merged = {**current, **fly_params}
        await session_data_manager.set_map_state(session_id, "viewport", merged)
        out["command"] = "fly_to"
        # P2 fix: 下发合并后的完整视口（当前视口 ∪ 本次参数）。此前只透传
        # fly_params —— 部分参数（如仅 pitch）会产出 center-less 的 fly_to，
        # 前端按缺 center 判 invalid_params，相机永不移动、viewport 失步。
        out["params"] = merged
    return _forward_evidence(res, out)

  @tool(
      registry,
      name="webgis_source_profile",
      description="剖析 GeoJSON 数据源生成 Spatial Meta Profile (BBOX, 建议视图, 字段统计, 数值分布)。",
      args_model=WebgisSourceProfileArgs,
      tier=1,
  )
  async def webgis_source_profile(
      source_id: str,
      geojson_data: Any,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    # source_profile adapter 不经引擎（无锁/校验），失败路径是 profiling 抛错；
    # 捕获后返回 success:False + correction_hint，让 LLM 自愈而非拿到异常。
    try:
      profile = await mapspec_store.source_profile(session_id, source_id, geojson_data)
    except Exception as e:
      logger.warning(f"[webgis_source_profile] profile failed for '{source_id}': {e}")
      return {
          "success": False,
          "source_id": source_id,
          "message": f"Source profile failed: {e}",
          "correction_hint": "请检查 geojson_data 是否为合法 GeoJSON、ref:xxx 引用或可读 URL/路径后重试。",
      }
    return {
        "success": True,
        "source_id": source_id,
        "profile": profile,
        "summary": f"Profile generated for source '{source_id}'",
    }

  @tool(
      registry,
      name="webgis_layer_upsert",
      description="创建或更新 MapSpec 图层规范，自动剖析数据源并设置建议视角，且同步编译发布到 runtime map_state。",
      args_model=WebgisLayerUpsertArgs,
      tier=1,
  )
  async def webgis_layer_upsert(
      layer: Dict[str, Any],
      source_data: Optional[Any] = None,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    res = await mapspec_store.layer_upsert(session_id, layer, source_data)
    out: Dict[str, Any] = {
        "layer_id": layer.get("id"),
        "mapspec": res.get("mapspec"),
    }
    if res.get("success"):
      out["summary"] = f"Layer '{layer.get('id')}' upserted into MapSpec"
    return _forward_evidence(res, out)

  @tool(
      registry,
      name="webgis_layer_remove",
      description="从 MapSpec 中移除指定图层并同步从 runtime map_state 擦除。",
      args_model=WebgisLayerRemoveArgs,
      tier=1,
  )
  async def webgis_layer_remove(
      layer_id: str,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    res = await mapspec_store.layer_remove(session_id, layer_id)
    out: Dict[str, Any] = {"removed_id": layer_id}
    if res.get("success"):
      out["summary"] = f"Layer '{layer_id}' removed from MapSpec"
    return _forward_evidence(res, out)

  @tool(
      registry,
      name="webgis_layout_set",
      description="设置 MapSpec 版面配置 (图例位置、控件、边距)。",
      args_model=WebgisLayoutSetArgs,
      tier=1,
  )
  async def webgis_layout_set(
      legend: Optional[Dict[str, Any]] = None,
      controls: Optional[List[Dict[str, Any]]] = None,
      margins: Optional[Dict[str, Any]] = None,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    res = await mapspec_store.layout_set(session_id, legend, controls, margins)
    out: Dict[str, Any] = {"layout": res.get("layout", {})}
    if res.get("success"):
      out["summary"] = "MapSpec layout updated"
    return _forward_evidence(res, out)

  @tool(
      registry,
      name="webgis_validate",
      description="在编译前检验 MapSpec 规范性 (CRS, 字段存在性, stops 严格单调性, view 合理性)。",
      args_model=WebgisValidateArgs,
      tier=1,
  )
  async def webgis_validate(session_id: Optional[str] = None) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    return await mapspec_store.validate_mapspec(session_id)

  @tool(
      registry,
      name="webgis_compile_maplibre",
      description="执行 MapSpec 编译，产出 style.json, index.html 与 compile-report.json。",
      args_model=WebgisCompileMaplibreArgs,
      tier=1,
  )
  async def webgis_compile_maplibre(session_id: Optional[str] = None) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    return await mapspec_store.compile_mapspec_cli(session_id)

  @tool(
      registry,
      name="webgis_checkpoint",
      description="创建 MapSpec 快照并具象化落地所引用的全部 ref_id 数据载荷。",
      args_model=WebgisCheckpointArgs,
      tier=1,
  )
  async def webgis_checkpoint(
      checkpoint_id: Optional[str] = None,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    return await mapspec_store.checkpoint(session_id, checkpoint_id)

  @tool(
      registry,
      name="webgis_rollback",
      description="回滚 MapSpec 与 runtime map_state 到指定的快照点。",
      args_model=WebgisRollbackArgs,
      tier=1,
  )
  async def webgis_rollback(
      checkpoint_id: str,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    return await mapspec_store.rollback(session_id, checkpoint_id)

  @tool(
      registry,
      name="webgis_runtime_validate",
      description="重新编译当前 MapSpec 并在 Headless 环境下进行运行时验收与 5-维度评分 (80% max)。",
      args_model=WebgisRuntimeValidateArgs,
      tier=1,
  )
  async def webgis_runtime_validate(session_id: Optional[str] = None) -> dict:
    from app.services.runtime_validator import runtime_validator
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    return await runtime_validator.validate_runtime(session_id)
