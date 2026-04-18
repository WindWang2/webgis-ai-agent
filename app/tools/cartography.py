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
    group: str = Field("analysis", description="图层组: analysis(分析), base(底图), reference(参考)")

class ThematicMapArgs(BaseModel):
    geojson: Any = Field(..., description="输入 GeoJSON 或数据引用(ref:xxx)")
    field: str = Field(..., description="用于分类的数值字段名")
    method: str = Field("quantiles", description="分类方法: quantiles(分位数), equal_interval(等间距)")
    k: int = Field(5, ge=2, le=10, description="分类数量 (2-10)")
    palette: str = Field("YlOrRd", description="调色板: YlOrRd, Blues, Greens, Reds, Viridis, Magma")
    group: str = Field("analysis", description="图层组: analysis(分析), base(底图), reference(参考)")

class ExportMapArgs(BaseModel):
    title: str = Field(..., description="制图主标题 (如: '2026年朝阳区绿地分布监测图')")
    subtitle: str = Field(default="", description="制图副标题")
    include_legend: bool = Field(default=True, description="是否在导出图中附带图例")
    dark_mode: bool = Field(default=True, description="强制使用暗色现代高斯模糊底纹")

def register_cartography_tools(registry: ToolRegistry):
    """注册制图工具"""

    @tool(registry, name="apply_layer_style",
           description="为地理图层设置统一的显示样式（颜色、透明度等）。",
           args_model=ApplyStyleArgs)
    def apply_layer_style(geojson: Any, color: str, opacity: float = 0.7, stroke_width: float = 2.0, group: str = "analysis") -> dict:
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
                "group": group,
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
    def create_thematic_map(geojson: Any, field: str, method: str = "quantiles", k: int = 5, palette: str = "YlOrRd", group: str = "analysis") -> dict:
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
                "group": group,
                "metadata": result_geojson.get("metadata", {})
            }
        except Exception as e:
            logger.error(f"Error creating thematic map: {e}")
            return {"error": str(e)}

    @tool(registry, name="export_thematic_map",
           description="当用户请求导出精美地图、制图排版、保存当前地图视图为图片时调用。该工具会指挥前端抽取当前地图画面并合成带标题的高质量图片。",
           args_model=ExportMapArgs)
    def export_thematic_map(title: str, subtitle: str = "", include_legend: bool = True, dark_mode: bool = True) -> dict:
        # 该工具直接触发一个隐藏的同步 command，前端据此完成截图、上传及后续系统回调
        return {
            "status": "export_task_created",
            "command": "export_map",
            "params": {
                "title": title,
                "subtitle": subtitle,
                "include_legend": include_legend,
                "dark_mode": dark_mode
            },
            "system_message": ("已将导出任务发送至前端！前端合成排版需要两到三秒时间，合成完成后将自动通过"
                               " `[系统通知]` 回传带有下载安全链接的高清截图。请直接告知用户你正在制图排版合成...")
        }
