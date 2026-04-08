"""
地图服务路由 - 地图 CRUD 和地图状态管理
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import uuid
from app.tools.map_action import get_session_view, get_session_layers, _map_views, _map_layers
router = APIRouter(prefix="/map", tags=["地图"])

# 内存地图存储
_maps: dict[str, dict] = {}

class MapExtent(BaseModel):
    """地图范围"""
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    crs: str = "EPSG:4326"

class MapStyle(BaseModel):
    """地图样式"""
    color: str = "#33aaff"
    weight: int = 2
    fill_color: Optional[str] = None
    fill_opacity: float = 0.5

class MapLayerInput(BaseModel):
    """图层输入"""
    name: str
    geojson: dict
    style: Optional[MapStyle] = None

class MapInfo(BaseModel):
    """地图信息"""
    id: str
    name: str
    description: Optional[str] = None
    extent: Optional[MapExtent] = None
    center: Optional[dict] = None  # {lng, lat}
    zoom: int = 10
    created_at: datetime

class MapViewRequest(BaseModel):
    """地图视图请求"""
    lng: float = Field(..., ge=-180, le=180)
    lat: float = Field(..., ge=-90, le=90)
    zoom: int = Field(default=10, ge=1, le=18)
    session_id: str = "default"

class MapLayerRequest(BaseModel):
    """添加图层请求"""
    name: str
    geojson: dict
    style: Optional[dict] = None
    session_id: str = "default"

@router.get("/maps")
def list_maps(limit: int = 100, offset: int = 0):
    """
    获取地图列表
    
    返回所有保存的地图配置
    """
    map_list = list(_maps.values())[offset:offset+limit]
    return {
        "total": len(_maps),
        "limit": limit,
        "offset": offset,
        "maps": map_list
    }

@router.get("/maps/{map_id}")
def get_map(map_id: str):
    """
    获取地图详情
    
    Args:
        map_id: 地图 ID
    
    Returns:
        地图详细信息
    """
    if map_id not in _maps:
        raise HTTPException(status_code=404, detail=f"地图 {map_id} 不存在")
    return _maps[map_id]

@router.post("/maps")
def create_map(name: str, description: Optional[str] = None, extent: Optional[dict] = None):
    """
    创建新地图
    
    Args:
        name: 地图名称
        description: 地图描述
        extent: 地图范围 {xmin, ymin, xmax, ymax}
    
    Returns:
        创建的地图信息
    """
    map_id = f"map_{uuid.uuid4().hex[:8]}"
    now = datetime.utcnow()
    
    new_map = {
        "id": map_id,
        "name": name,
        "description": description,
        "extent": MapExtent(**extent) if extent else None,
        "center": {"lng": 116.4, "lat": 39.9},  # 默认北京
        "zoom": 10,
        "created_at": now,
        "updated_at": now
    }
    _maps[map_id] = new_map
    return new_map

@router.patch("/maps/{map_id}")
def update_map(map_id: str, name: Optional[str] = None, description: Optional[str] = None, center: Optional[dict] = None, zoom: Optional[int] = None):
    """
    更新地图信息
    """
    if map_id not in _maps:
        raise HTTPException(status_code=404, detail=f"地图 {map_id} 不存在")
    
    m = _maps[map_id]
    if name is not None:
        m["name"] = name
    if description is not None:
        m["description"] = description
    if center is not None:
        m["center"] = center
    if zoom is not None:
        m["zoom"] = zoom
    m["updated_at"] = datetime.utcnow()
    
    return m

@router.delete("/maps/{map_id}")
def delete_map(map_id: str):
    """
    删除地图
    """
    if map_id not in _maps:
        raise HTTPException(status_code=404, detail=f"地图 {map_id} 不存在")
    del _maps[map_id]
    return {"status": "ok", "message": f"地图 {map_id} 已删除"}

# ===== 地图视图控制 =====
@router.get("/view")
def get_map_view(session_id: str = "default"):
    """
    获取会话的地图视图
    
    返回当前地图的中心点、缩放级别和图层
    """
    view = get_session_view(session_id)
    layers = get_session_layers(session_id)
    
    return {
        "session_id": session_id,
        "view": view,
        "layers": layer,
        "timestamp": datetime.utcnow().isoformat()
    }

@router.post("/view")
def set_map_view(req: MapViewRequest):
    """
    设置地图视图
    """
    from app.tools.map_action import _map_views
    _map_views[req.session_id] = {
        "lng": req.lng,
        "lat": req.lat,
        "zoom": req.zoom
    }
    
    return {
        "success": True,
        "session_id": req.session_id,
        "view": {"lng": req.lng, "lat": req.lat, "zoom": req.zoom}
    }

# ===== 地图图层操作 =====
@router.get("/layers")
def get_map_layers(session_id: str = "default"):
    """
    获取会话的地图图层列表
    """
    layer = get_session_layers(session_id)
    return {
        "session_id": session_id,
        "layer": layer,
        "count": len(layer)
    }

@router.post("/layers")
def add_map_layer(req: MapLayerRequest):
    """
    添加地图图层
    """
    from app.tools.map_action import _map_layers
    
    if req.session_id not in _map_layers:
        _map_layers[req.session_id] = []
    
    layer = {
        "name": req.name,
        "geojson": req.geojson,
        "style": req.style or {"color": "#33aaff", "weight": 2}
    }
    _map_layers[req.session_id].append(layer)
    
    return {
        "success": True,
        "session_id": req.session_id,
        "layer": layer,
        "total_count": len(_map_layers[req.session_id])
    }

@router.delete("/layers")
def clear_map_layer(session_id: str = "default"):
    """
    清除地图图层
    """
    from app.tools.map_action import _map_layers
    
    count = 0
    if session_id in _map_layers:
        count = len(_map_layers[session_id])
        _map_layers[session_id] = []
    
    return {
        "success": True,
        "session_id": session_id,
        "cleared_count": count
    }

@router.delete("/layers/{layer_name}")
def delete_map_layer(layer_name: str, session_id: str = "default"):
    """
    删除指定名称的图层
    """
    from app.tools.map_action import _map_layers
    
    if session_id in _map_layers:
        _map_layers[session_id] = [l for l in _map_layers[session_id] if l.get("name") != layer_name]
    
    return {
        "success": True,
        "session_id": session_id,
        "layer_name": layer_name
    }