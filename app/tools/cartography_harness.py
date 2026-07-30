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
