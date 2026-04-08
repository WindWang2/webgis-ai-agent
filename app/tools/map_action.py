"""地图操作工具 - 让 AI 可以控制前端地图展示"""
from typing import Optional
from app.tools.registry import ToolRegistry

# 内存存储地图状态（后续可持久化到数据库）
_map_views: dict[str, dict] = {}  # session_id -> {lng, lat, zoom, layers}
_map_layers: dict[str, list[dict]] = {}  # session_id -> [layer1, layer2, ...]


def register_map_action_tools(registry: ToolRegistry):
    """注册地图操作工具到 registry"""
    
    @registry.register(
        name="set_map_view",
        description="设置地图视图的中心点坐标和缩放级别。当用户想要定位到某个位置或调整地图视野时使用此工具",
        param_descriptions={
            "lng": "中心点经度 (longitude)",
            "lat": "中心点纬度 (latitude)", 
            "zoom": "缩放级别 (通常 1-18)",
            "session_id": "会话ID，用于保存地图状态"
        }
    )
    def set_map_view(lng: float, lat: float, zoom: int = 10, session_id: str = "default") -> dict:
        """设置地图视图"""
        _map_views[session_id] = {"lng": lng, "lat": lat, "zoom": zoom}
        return {
            "success": True,
            "action": "set_view",
            "data": {"lng": lng, "lat": lat, "zoom": zoom},
            "message": f"地图已移动到 ({lng}, {lat})，缩放级别 {zoom}"
        }
    
    @registry.register(
        name="add_map_layer",
        description="在地图上添加一个 GeoJSON 图层，当需要展示地理空间数据（如分析结果、POI、路径等）到地图上时使用",
        param_descriptions={
            "name": "图层名称",
            "geojson": "GeoJSON 格式的地理数据",
            "style": "图层样式（可选，如颜色、线宽等）",
            "session_id": "会话ID"
        }
    )
    def add_map_layer(name: str, geojson: dict, style: Optional[dict] = None, session_id: str = "default") -> dict:
        """添加地图图层"""
        if session_id not in _map_layers:
            _map_layers[session_id] = []
        
        layer = {
            "name": name,
            "geojson": geojson,
            "style": style or {"color": "#3388ff", "weight": 2}
        }
        _map_layers[session_id].append(layer)
        
        return {
            "success": True,
            "action": "add_layer",
            "data": layer,
            "message": f"图层 '{name}' 已添加到地图"
        }
    
    @registry.register(
        name="clear_map_layers",
        description="清除地图上所有自定义图层当用户想要重置地图或清除分析结果时使用",
        param_descriptions={
            "session_id": "会话ID"
        }
    )
    def clear_map_layers(session_id: str = "default") -> dict:
        """清除地图图层"""
        if session_id in _map_layers:
            count = len(_map_layers[session_id])
            _map_layers[session_id] = []
            return {
                "success": True,
                "action": "clear_layers",
                "data": {"count": count},
                "message": f"已清除 {count} 个图层"
            }
        return {
            "success": True,
            "action": "clear_layer",
            "data": {"count": 0},
            "message": "地图上没有图层需要清除"
        }
    
    @registry.register(
        name="get_map_state",
        description="获取当前地图的状态包括视图中心和已添加的图层列表",
        param_descriptions={
            "session_id": "会话ID"
        }
    )
    def get_map_state(session_id: str = "default") -> dict:
        """获取地图状态"""
        view = _map_views.get(session_id, {"lng": 116.4, "lat": 39.9, "zoom": 10})
        layers_list = _map_layers.get(session_id, [])
        
        return {
            "success": True,
            "action": "get_state",
            "data": {
                "view": view,
                "layers": layer_list
            },
            "message": f"地图视图: ({view['lng']}, {view['lat']}), 缩放 {view['zoom']}, 图层数: {len(layer_list)}"
        }


def get_session_layers(session_id: str = "default") -> list[dict]:
    """获取会话的所有图层（供前端使用）"""
    return _map_layers.get(session_id, [])


def get_session_view(session_id: str = "default") -> dict:
    """获取会话的视图（供前端使用）"""
    return _map_views.get(session_id, {"lng": 116.4, "lat": 39.9, "zoom": 10})