"""webgis_* Canonical Cartography Tools for MapSpec Harness."""
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.services.mapspec_store import mapspec_store

logger = logging.getLogger(__name__)


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


def register_cartography_harness_tools(registry: ToolRegistry) -> None:
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
    return {
        "success": True,
        "mapspec": res["mapspec"],
        "summary": "MapSpec project initialized",
    }

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
    return {
        "success": True,
        "view": res["mapspec"]["view"],
        "summary": f"View updated to {res['mapspec']['view']}",
    }

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
    profile = await mapspec_store.source_profile(session_id, source_id, geojson_data)
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
    return {
        "success": True,
        "layer_id": layer.get("id"),
        "mapspec": res["mapspec"],
        "summary": f"Layer '{layer.get('id')}' upserted into MapSpec",
    }

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
    return {
        "success": True,
        "removed_id": layer_id,
        "summary": f"Layer '{layer_id}' removed from MapSpec",
    }
