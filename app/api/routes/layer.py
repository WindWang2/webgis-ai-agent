"""
图层数据 API

- GET /layers/data/{ref_id}: Fetch-on-Demand 端点，Agent 执行链路的一部分。
- GET /layer-types: 图层类型枚举。
- 图层 CRUD 已移除 — Agent 通过工具链自动创建和管理图层（"Agent is Everything"）。
- 空间分析任务端点已移除 — Agent 通过 tool calling 驱动分析，不再走 REST CRUD。
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.services.session_data import session_data_manager
from app.core.auth import get_current_user_optional

router = APIRouter()


@router.get("/layers/data/{ref_id}", tags=["图层数据"])
async def get_session_layer_data(
    ref_id: str,
    session_id: str = Query(..., min_length=8, max_length=128, description="会话 ID"),
    _user: dict = Depends(get_current_user_optional),
):
    """通过引用 ID 获取会话缓存中的大数据对象（如分析产生的 GeoJSON）。"""
    if not ref_id or len(ref_id) > 128 or any(c.isspace() for c in ref_id):
        raise HTTPException(status_code=400, detail="非法 ref_id")
    data = await session_data_manager.get(session_id, ref_id)
    if not data:
        raise HTTPException(status_code=404, detail="数据已过期或不存在")
    return data


@router.get("/layer-types", tags=["元数据"])
def get_layer_types():
    """获取支持的图层类型列表"""
    return {
        "layer_types": [
            {"type": "vector", "description": "矢量图层", "formats": ["shapefile", "geojson", "gpx", "kml"]},
            {"type": "raster", "description": "栅格图层", "formats": ["tiff", "jpg", "png", "dem"]},
            {"type": "tile", "description": "瓦片图层", "formats": ["xyz", "wmts", "tms"]}
        ],
        "analysis_types": [
            {"type": "buffer", "description": "缓冲区分析"},
            {"type": "clip", "description": "裁剪分析"},
            {"type": "intersect", "description": "相交分析"},
            {"type": "dissolve", "description": "融合分析"},
            {"type": "union", "description": "联合分析"},
            {"type": "spatial_join", "description": "空间连接"}
        ]
    }
