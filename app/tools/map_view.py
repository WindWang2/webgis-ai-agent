"""地图视图操作工具 (View / Camera Control)

提供 LLM 主动驱动地图视角的能力：飞行、缩放到包围盒、定位到图层、重置视图、调整 pitch/bearing。
所有工具不修改图层数据，只发出前端命令控制相机。
"""
import logging
from typing import Any, List, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)


# 中国大陆默认视图（北京天安门为中心，zoom 4 覆盖全国）
DEFAULT_VIEW = {
    "center": [104.0, 35.0],
    "zoom": 4,
    "bearing": 0,
    "pitch": 0,
}


def _extract_bbox_from_geojson(data: Any) -> Optional[List[float]]:
    """从 GeoJSON 中粗略提取 bbox: [west, south, east, north]"""
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("bbox"), list) and len(data["bbox"]) >= 4:
        return [float(x) for x in data["bbox"][:4]]

    bounds = [float("inf"), float("inf"), float("-inf"), float("-inf")]
    found = False

    def walk(node: Any):
        nonlocal found
        if isinstance(node, list) and node and isinstance(node[0], (int, float)) and len(node) >= 2:
            lng, lat = float(node[0]), float(node[1])
            bounds[0] = min(bounds[0], lng)
            bounds[1] = min(bounds[1], lat)
            bounds[2] = max(bounds[2], lng)
            bounds[3] = max(bounds[3], lat)
            found = True
            return
        if isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            if "coordinates" in node:
                walk(node["coordinates"])
            if node.get("type") == "FeatureCollection":
                for f in node.get("features", []) or []:
                    walk(f)
            elif node.get("type") == "Feature":
                geom = node.get("geometry")
                if geom:
                    walk(geom)

    walk(data)
    return bounds if found else None


class FlyToLocationArgs(BaseModel):
    longitude: float = Field(..., description="目标经度 (-180~180)")
    latitude: float = Field(..., description="目标纬度 (-90~90)")
    zoom: float = Field(12, ge=0, le=22, description="目标缩放级别 (0~22)，城市级一般 10-14")
    bearing: Optional[float] = Field(None, description="方位角 0-360，正北为 0；不填则保持当前方位")
    pitch: Optional[float] = Field(None, ge=0, le=85, description="俯仰角 0-85，0 为正俯视；不填则保持当前俯仰")


class ZoomToBBoxArgs(BaseModel):
    bbox: List[float] = Field(..., description="包围盒 [west, south, east, north]，4 个数字")
    padding: int = Field(50, ge=0, le=500, description="边距像素，默认 50")


class ZoomToLayerArgs(BaseModel):
    layer_ref: str = Field(..., description="图层引用：ref:xxx、别名 或 图层名称")
    padding: int = Field(60, ge=0, le=500, description="边距像素，默认 60")


class SetMapViewArgs(BaseModel):
    zoom: Optional[float] = Field(None, ge=0, le=22, description="目标缩放级别")
    bearing: Optional[float] = Field(None, description="方位角 0-360")
    pitch: Optional[float] = Field(None, ge=0, le=85, description="俯仰角 0-85")


def _resolve_layer_id(session_id: str, layer_ref: str) -> Optional[str]:
    """根据 ref/别名/名称 解析到画布上的 layer_id"""
    aliases = session_data_manager._aliases.get(session_id, {}) if hasattr(session_data_manager, "_aliases") else {}
    candidate = aliases.get(layer_ref, layer_ref)

    map_state = session_data_manager.get_map_state(session_id) or {}
    layers = map_state.get("layers", []) or []

    for l in layers:
        if l.get("id") == candidate or l.get("id") == layer_ref:
            return l.get("id")
    for l in layers:
        name = l.get("name", "") or ""
        if name == layer_ref or (layer_ref and layer_ref in name):
            return l.get("id")
    return candidate


def register_map_view_tools(registry: ToolRegistry):
    """注册地图视图操作工具"""

    @tool(
        registry,
        name="fly_to_location",
        description=(
            "把地图相机平滑飞行到指定经纬度。"
            "\n何时用：用户说『把地图移到 XX』『定位到 XX』『看一下 XX 周边』，已经知道目标坐标。"
            "\n何时不用：(1) 想看某一个图层的范围 — 用 zoom_to_layer；"
            "(2) 只想改俯仰/旋转角度 — 用 set_map_view；"
            "(3) 不知道目标坐标 — 先 geocode_address 拿到坐标再调本工具。"
            "\n关键约束：经纬度必须是十进制度数；zoom 推荐 10-14（城市级），16+ 街区级。"
        ),
        args_model=FlyToLocationArgs,
    )
    def fly_to_location(
        longitude: float,
        latitude: float,
        zoom: float = 12,
        bearing: Optional[float] = None,
        pitch: Optional[float] = None,
    ) -> dict:
        if not (-180 <= longitude <= 180) or not (-90 <= latitude <= 90):
            return {"error": f"坐标越界: ({longitude}, {latitude})"}

        params: dict[str, Any] = {
            "center": [longitude, latitude],
            "zoom": zoom,
        }
        if bearing is not None:
            params["bearing"] = bearing
        if pitch is not None:
            params["pitch"] = pitch

        return {
            "success": True,
            "command": "fly_to",
            "params": params,
            "message": f"地图已飞行至 ({longitude:.4f}, {latitude:.4f}) zoom={zoom}",
        }

    @tool(
        registry,
        name="zoom_to_bbox",
        description=(
            "把地图缩放到给定包围盒，让该区域完整出现在视口里。"
            "\n何时用：拿到了一个明确的 [west, south, east, north] bbox（来自分析结果、行政边界等），"
            "希望地图自适应这片区域。"
            "\n何时不用：只知道一个图层名 — 用 zoom_to_layer；只有一个点 — 用 fly_to_location。"
            "\n关键约束：bbox 必须是经度在前、纬度在后的 4 元素列表；west<east, south<north。"
        ),
        args_model=ZoomToBBoxArgs,
    )
    def zoom_to_bbox(bbox: List[float], padding: int = 50) -> dict:
        if not isinstance(bbox, list) or len(bbox) < 4:
            return {"error": "bbox 必须是长度为 4 的列表 [west, south, east, north]"}
        west, south, east, north = bbox[0], bbox[1], bbox[2], bbox[3]
        if west >= east or south >= north:
            return {"error": f"bbox 顺序错误: west({west}) 必须 < east({east}), south({south}) < north({north})"}
        return {
            "success": True,
            "command": "zoom_to_bbox",
            "params": {
                "bbox": [west, south, east, north],
                "padding": padding,
            },
            "message": f"已缩放到包围盒 [{west:.3f}, {south:.3f}, {east:.3f}, {north:.3f}]",
        }

    @tool(
        registry,
        name="zoom_to_layer",
        description=(
            "把地图视图自适应到指定图层的覆盖范围。"
            "\n何时用：用户说『放大到 XX 图层』『定位到核心保护区』『看一下分析结果的全貌』。"
            "\n何时不用：图层不在当前 session（如刚切换底图） — 改用 zoom_to_bbox 并显式给 bbox。"
            "\n关键约束：layer_ref 支持 ref:xxx 引用、用户起的别名、或图层名称；若 session 中存的数据有 bbox，会用它，"
            "否则从 features 实时计算。"
        ),
        args_model=ZoomToLayerArgs,
    )
    def zoom_to_layer(layer_ref: str, padding: int = 60, session_id: Optional[str] = None) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}

        # 1) 解析到 ref_id（如果是别名）
        aliases = session_data_manager._aliases.get(session_id, {}) if hasattr(session_data_manager, "_aliases") else {}
        ref_id = aliases.get(layer_ref, layer_ref)

        # 2) 尝试拉数据并算 bbox
        bbox: Optional[List[float]] = None
        data = session_data_manager.get(session_id, ref_id)
        if data is not None:
            payload = data.get("data") if isinstance(data, dict) and "data" in data else data
            bbox = _extract_bbox_from_geojson(payload)

        # 3) 兜底：看一下 map_state 是否记录了该图层的 extent
        if bbox is None:
            map_state = session_data_manager.get_map_state(session_id) or {}
            for l in (map_state.get("layers", []) or []):
                if l.get("id") in (ref_id, layer_ref) or l.get("name") == layer_ref:
                    cand = l.get("bbox") or l.get("extent")
                    if isinstance(cand, list) and len(cand) >= 4:
                        bbox = [float(x) for x in cand[:4]]
                        break

        if not bbox:
            return {"error": f"无法获取图层 {layer_ref} 的范围，请先确认图层存在或直接用 zoom_to_bbox 指定 bbox"}

        return {
            "success": True,
            "command": "zoom_to_bbox",
            "params": {
                "bbox": bbox,
                "padding": padding,
            },
            "resolved_layer_id": _resolve_layer_id(session_id, layer_ref),
            "message": f"已缩放到图层 {layer_ref} 的范围",
        }

    @tool(
        registry,
        name="reset_map_view",
        description=(
            "把地图视图复位到全国默认视角（中心约 (104, 35)，zoom 4，俯仰/方位归零）。"
            "\n何时用：用户说『回到初始视图』『重置地图』『缩到全国』。"
            "\n何时不用：只是想看某个特定地区 — 用 fly_to_location / zoom_to_layer。"
        ),
    )
    def reset_map_view() -> dict:
        return {
            "success": True,
            "command": "fly_to",
            "params": DEFAULT_VIEW.copy(),
            "message": "地图视图已重置为全国默认视角",
        }

    @tool(
        registry,
        name="set_map_view",
        description=(
            "在不移动地图中心的前提下，只调整 zoom / pitch / bearing。"
            "\n何时用：用户说『放大一点』『倾斜看 3D』『把北朝上』『顺时针转 30 度』。三个参数都是可选的，"
            "传一个就只改一个。"
            "\n何时不用：要同时改中心位置 — 用 fly_to_location。"
            "\n关键约束：pitch 范围 0-85；bearing 0-360（0=正北）。"
        ),
        args_model=SetMapViewArgs,
    )
    def set_map_view(
        zoom: Optional[float] = None,
        bearing: Optional[float] = None,
        pitch: Optional[float] = None,
    ) -> dict:
        if zoom is None and bearing is None and pitch is None:
            return {"error": "zoom / bearing / pitch 至少传一个"}
        params: dict[str, Any] = {}
        if zoom is not None:
            params["zoom"] = zoom
        if bearing is not None:
            params["bearing"] = bearing
        if pitch is not None:
            params["pitch"] = pitch
        return {
            "success": True,
            "command": "set_map_view",
            "params": params,
            "message": f"视图参数已更新: {params}",
        }
