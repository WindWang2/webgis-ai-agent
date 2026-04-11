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
