"""
地图制图 FC 工具 - 提供样式设置和专题图制作能力
"""
import json
import logging
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

def _safe_parse_geojson(geojson: Any) -> dict | None:
    """从输入解析 GeoJSON (支持 dict 或 str)"""
    if isinstance(geojson, dict):
        return geojson
    if not isinstance(geojson, str):
        return None
    try:
        return json.loads(geojson)
    except:
        return None

class ApplyStyleArgs(BaseModel):
    geojson: Any = Field(..., description="输入 GeoJSON 或数据引用(ref:xxx)")
    color: str = Field("#3b82f6", description="颜色 (Hex, 例如 #ff0000)")
    opacity: float = Field(0.7, ge=0, le=1, description="不透明度 (0~1)")
    stroke_width: float = Field(2.0, description="边框宽度")

class ThematicMapArgs(BaseModel):
    geojson: Any = Field(..., description="输入 GeoJSON 或数据引用(ref:xxx)")
    field: str = Field(..., description="用于分类的数值字段名")
    method: str = Field("quantiles", description="分类方法: quantiles(分位数), equal_interval(等间距)")
    k: int = Field(5, ge=2, le=10, description="分类数量 (2-10)")
    palette: str = Field("YlOrRd", description="调色板: YlOrRd, Blues, Greens, Reds, Viridis, Magma")

def register_cartography_tools(registry: ToolRegistry):
    """注册制图工具"""

    @tool(registry, name="apply_layer_style",
           description="为地理图层设置统一的显示样式（颜色、透明度等）。",
           args_model=ApplyStyleArgs)
    def apply_layer_style(geojson: Any, color: str, opacity: float = 0.7, stroke_width: float = 2.0) -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            
            # 这里我们不修改原始要素，而是返回一个带有 style hint 的结果
            # 或者直接在 features 的 properties 中注入样式
            features = data.get("features", [])
            for f in features:
                if "properties" not in f: f["properties"] = {}
                f["properties"]["fill_color"] = color
                f["properties"]["opacity"] = opacity
                f["properties"]["stroke_width"] = stroke_width
            
            return {
                "geojson": data,
                "style_applied": {
                    "color": color,
                    "opacity": opacity,
                    "stroke_width": stroke_width
                }
            }
        except Exception as e:
            return {"error": str(e)}

    @tool(registry, name="create_thematic_map",
           description="根据指定字段制作分层设色专题图 (Choropleth Map)，自动计算颜色级别。",
           args_model=ThematicMapArgs)
    def create_thematic_map(geojson: Any, field: str, method: str = "quantiles", k: int = 5, palette: str = "YlOrRd") -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            
            from app.services.cartography_service import CartographyService
            result_geojson = CartographyService.apply_choropleth(
                geojson=data,
                field=field,
                method=method,
                k=k,
                palette=palette
            )
            
            return {
                "geojson": result_geojson,
                "metadata": result_geojson.get("metadata", {})
            }
        except Exception as e:
            logger.error(f"Error creating thematic map: {e}")
            return {"error": str(e)}
