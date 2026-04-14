"""图层管理工具 (Session Context Management)"""
import logging
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

class AliasLayerArgs(BaseModel):
    ref_id: str = Field(..., description="数据的引用 ID，例如 'ref:data-xxxx'")
    alias: str = Field(..., description="想要赋予的易读名称或别名，例如 '核心保护区'")

def register_layer_management_tools(registry: ToolRegistry):
    """注册会话图层管理工具"""

    @tool(registry, name="alias_layer",
           description="为当前会话中的数据引用（ref:xxx）设置一个语义化的别名。设置后，后续可以直呼其名（如：'核心保护区'）来引用该数据。",
           args_model=AliasLayerArgs)
    def alias_layer(ref_id: str, alias: str, session_id: Optional[str] = None) -> dict:
        """为引用的数据设置别名"""
        if not session_id:
            return {"error": "Missing session_id context"}
            
        session_data_manager.set_alias(session_id, ref_id, alias)
        return {
            "success": True, 
            "ref_id": ref_id, 
            "alias": alias, 
            "message": f"已成功为 {ref_id} 设置别名: {alias}。您现在可以在后续操作中直接使用该别名引用此图层。"
        }

    @tool(registry, name="inventory_layers",
           description="展示当前会话中所有的地理数据图层（包含系统生成的引用 ID 和您设置的别名）。")
    def inventory_layers(session_id: Optional[str] = None) -> dict:
        """列出所有图层"""
        if not session_id:
            return {"error": "Missing session_id context"}
            
        layers = session_data_manager.list_refs(session_id)
        inventory = []
        for ref_id, alias in layers.items():
            inventory.append({
                "ref_id": ref_id,
                "alias": alias or "(无别名)"
            })
            
        return {
            "success": True,
            "layers": inventory,
            "count": len(inventory)
        }

    @tool(registry, name="switch_base_layer",
           description="切换当前地图的底图图源。支持：'Carto 深色'、'OSM 地图'、'ESRI 影像'、'OpenTopoMap'、'高德影像'。")
    def switch_base_layer(name: str, session_id: Optional[str] = None) -> dict:
        """切换底图"""
        if not session_id:
            return {"error": "Missing session_id context"}
        
        # 汉化/规范化名称映射，确保 AI 即使说“卫星”或“satellite”，我们也存入标准的“ESRI 影像”
        CANONICAL_NAMES = ["Carto 深色", "OSM 地图", "ESRI 影像", "Carto 浅色", "ESRI 地形", "OpenTopoMap", "高德影像"]
        
        search_name = name.toLowerCase() if hasattr(name, 'toLowerCase') else str(name).lower()
        resolved_name = name # Default to original if no match
        
        # 1. 精确匹配
        matched = False
        for cname in CANONICAL_NAMES:
            if cname.lower() == search_name:
                resolved_name = cname
                matched = True
                break
        
        # 2. 模糊包含匹配
        if not matched:
            for cname in CANONICAL_NAMES:
                c_low = cname.lower()
                if search_name in c_low or c_low in search_name:
                    resolved_name = cname
                    matched = True
                    break
        
        # 3. 关键字兜底
        if not matched:
            if any(k in search_name for k in ["卫星", "影像", "satellite"]):
                resolved_name = "ESRI 影像"
            elif any(k in search_name for k in ["深色", "dark"]):
                resolved_name = "Carto 深色"
            elif any(k in search_name for k in ["地图", "osm", "street"]):
                resolved_name = "OSM 地图"

        session_data_manager.set_map_state(session_id, "base_layer", resolved_name)
        return {
            "success": True,
            "command": "BASE_LAYER_CHANGE",
            "params": {
                "name": resolved_name
            },
            "message": f"底图已成功切换为：{resolved_name}"
        }

    @tool(registry, name="set_layer_status",
           description="修改图层的显示状态（如可见性和透明度）。可以通过 ID (ref:xxx) 或别名引用图层。")
    def set_layer_status(layer_ref: str, visible: Optional[bool] = None, opacity: Optional[float] = None, session_id: Optional[str] = None) -> dict:
        """修改图层状态"""
        if not session_id:
            return {"error": "Missing session_id context"}
        
        # 尝试转换别名为 ID
        ref_id = session_data_manager._aliases.get(session_id, {}).get(layer_ref, layer_ref)
        
        return {
            "success": True,
            "command": "LAYER_VISIBILITY_UPDATE",
            "params": {
                "layer_id": ref_id,
                "visible": visible,
                "opacity": opacity
            },
            "message": f"已更新图层 {layer_ref} 的显示设置。"
        }

    @tool(registry, name="update_layer_appearance",
           description="修改图层的视觉样式（如颜色、线宽）。可以通过 ID (ref:xxx) 或别名引用图层。")
    def update_layer_appearance(layer_ref: str, color: Optional[str] = None, stroke_width: Optional[float] = None, session_id: Optional[str] = None) -> dict:
        """修改图层外观"""
        if not session_id:
            return {"error": "Missing session_id context"}
            
        ref_id = session_data_manager._aliases.get(session_id, {}).get(layer_ref, layer_ref)
        
        return {
            "success": True,
            "command": "LAYER_STYLE_UPDATE",
            "params": {
                "layer_id": ref_id,
                "style": {
                    "color": color,
                    "strokeWidth": stroke_width
                }
            },
            "message": f"已更新图层 {layer_ref} 的外观样式。"
        }
