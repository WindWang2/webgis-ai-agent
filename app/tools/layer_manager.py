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


class ReorderLayerArgs(BaseModel):
    layer_ref: str = Field(..., description="图层引用 (ref:xxx) / 别名 / 名称")
    position: str = Field(
        "top",
        description="目标层级：top(置顶) / bottom(置底) / up(上移一层) / down(下移一层) / before(置于 before_ref 之下)",
    )
    before_ref: Optional[str] = Field(None, description="仅当 position=before 时使用：要插入到哪个图层之下")


class RemoveLayerArgs(BaseModel):
    layer_ref: str = Field(..., description="图层引用 (ref:xxx) / 别名 / 名称")

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
        from app.core.base_layers import get_base_layer_names
        CANONICAL_NAMES = get_base_layer_names()
        
        search_name = str(name).lower()
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
           description="修改图层的显示状态（如可见性和透明度）。可以通过 ID (ref:xxx)、别名或图层名称引用图层。")
    def set_layer_status(layer_ref: str, visible: Optional[bool] = None, opacity: Optional[float] = None, session_id: Optional[str] = None) -> dict:
        """修改图层状态"""
        if not session_id:
            return {"error": "Missing session_id context"}
        
        # 1. 尝试从当前 Session 别名查找
        ref_id = session_data_manager.resolve_alias(session_id, layer_ref)
        
        # 2. 如果没找到（或者是 legacy 图层），尝试从地图实时状态中模糊查找
        map_state = session_data_manager.get_map_state(session_id)
        layers = map_state.get("layers", [])
        
        # 精确 ID 匹配优先
        found_id = None
        for l in layers:
            if l.get("id") == ref_id or l.get("id") == layer_ref:
                found_id = l.get("id")
                break
        
        # 名称模糊匹配作为兜底
        if not found_id:
            for l in layers:
                if l.get("name") == layer_ref or layer_ref in l.get("name", ""):
                    found_id = l.get("id")
                    break
        
        id_to_use = found_id or ref_id
        
        return {
            "success": True,
            "command": "LAYER_VISIBILITY_UPDATE",
            "params": {
                "layer_id": id_to_use,
                "visible": visible,
                "opacity": opacity
            },
            "message": f"已向地图发送指令：更新图层 {layer_ref} (目标 ID: {id_to_use}) 的显示设置。"
        }

    @tool(registry, name="update_layer_appearance",
           description="修改图层的视觉样式（如颜色、线宽）。可以通过 ID (ref:xxx)、别名或图层名称引用图层。")
    def update_layer_appearance(layer_ref: str, color: Optional[str] = None, stroke_width: Optional[float] = None, session_id: Optional[str] = None) -> dict:
        """修改图层外观"""
        if not session_id:
            return {"error": "Missing session_id context"}
            
        # 逻辑同上
        ref_id = session_data_manager.resolve_alias(session_id, layer_ref)
        map_state = session_data_manager.get_map_state(session_id)
        layers = map_state.get("layers", [])
        
        found_id = None
        for l in layers:
            if l.get("id") == ref_id or l.get("id") == layer_ref:
                found_id = l.get("id")
                break
        if not found_id:
            for l in layers:
                if l.get("name") == layer_ref or layer_ref in l.get("name", ""):
                    found_id = l.get("id")
                    break
        
        id_to_use = found_id or ref_id
        
        return {
            "success": True,
            "command": "LAYER_STYLE_UPDATE",
            "params": {
                "layer_id": id_to_use,
                "style": {
                    "color": color,
                    "strokeWidth": stroke_width
                }
            },
            "message": f"已向地图发送指令：更新图层 {layer_ref} (目标 ID: {id_to_use}) 的外观样式。"
        }

    @tool(registry, name="reorder_layer",
           description=(
               "调整图层在地图上的 Z 顺序 (上下叠放层级)。"
               "\n何时用：用户说『把分析结果放到最上面』『底图盖住了热力图』『让这个图层置顶』。"
               "\n何时不用：仅想改可见性 — 用 set_layer_status；仅想改颜色 — 用 update_layer_appearance。"
               "\n关键约束：position 支持 top/bottom/up/down/before；before 时必须提供 before_ref。"
           ),
           args_model=ReorderLayerArgs)
    def reorder_layer(layer_ref: str, position: str = "top", before_ref: Optional[str] = None, session_id: Optional[str] = None) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}

        # /review P1-6: empty layer_ref would resolve to the empty string and
        # the frontend's prefix-match handler (`id.startsWith('custom-' + layer_id)`)
        # would match ALL custom layers in one shot. The existence check below
        # catches any layer_ref that doesn't resolve to a session-owned id —
        # including '', 'ref:', and any other unspecific value.
        if not layer_ref:
            return {"error": "layer_ref 不能为空"}

        pos = (position or "top").lower().strip()
        if pos not in {"top", "bottom", "up", "down", "before"}:
            return {"error": f"Invalid position '{position}', must be one of: top/bottom/up/down/before"}
        if pos == "before" and not before_ref:
            return {"error": "position=before 时必须提供 before_ref"}

        ref_id = session_data_manager.resolve_alias(session_id, layer_ref)

        # /review P1-6: verify the resolved id is session-owned. Accept either
        # source (the session's data store OR the frontend-echoed active layers)
        # since legitimate flow can have the ref registered before the frontend
        # echoes it back. The point is to refuse a free-form LLM ref that wasn't
        # registered by THIS session.
        map_state = session_data_manager.get_map_state(session_id) or {}
        active_layers = map_state.get("layers", []) or []
        session_refs = session_data_manager.list_refs(session_id) or {}
        if ref_id not in session_refs and not any(l.get("id") == ref_id for l in active_layers):
            return {"error": f"layer_ref {layer_ref!r} 未在当前会话的图层 / 数据引用中找到对应的 id"}

        before_id = None
        if before_ref:
            before_id = session_data_manager.resolve_alias(session_id, before_ref)
            if before_id not in session_refs and not any(l.get("id") == before_id for l in active_layers):
                return {"error": f"before_ref {before_ref!r} 未在当前会话的图层 / 数据引用中找到对应的 id"}

        return {
            "success": True,
            "command": "REORDER_LAYER",
            "params": {
                "layer_id": ref_id,
                "position": pos,
                "before_id": before_id,
            },
            "message": f"已向地图发送指令：调整图层 {layer_ref} 的 Z 顺序 -> {pos}",
        }

    @tool(registry, name="remove_layer",
           description=(
               "从地图上移除指定图层 (同时释放其 source)。"
               "\n何时用：用户说『把 XX 删掉』『关掉这个图层』『清掉分析结果』，且确实不再需要该数据。"
               "\n何时不用：只是临时隐藏 — 用 set_layer_status visible=false；想换样式 — 用 update_layer_appearance。"
               "\n关键约束：删除是不可逆操作；ref_id 来自 session 数据存储，删除画布上的图层不会清掉 session 数据本身。"
           ),
           args_model=RemoveLayerArgs)
    def remove_layer(layer_ref: str, session_id: Optional[str] = None) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}

        # /review P1-6: empty layer_ref prefix-matches everything on the
        # frontend. The existence check below catches all other unresolved refs.
        if not layer_ref:
            return {"error": "layer_ref 不能为空"}

        ref_id = session_data_manager.resolve_alias(session_id, layer_ref)
        map_state = session_data_manager.get_map_state(session_id)
        layers = map_state.get("layers", [])
        found_id = None
        for l in layers:
            if l.get("id") == ref_id or l.get("id") == layer_ref:
                found_id = l.get("id")
                break
        if not found_id:
            for l in layers:
                if l.get("name") == layer_ref or (layer_ref and layer_ref in (l.get("name") or "")):
                    found_id = l.get("id")
                    break

        # /review P1-6: if neither the active-layers loop above NOR the
        # session's ref store knows this ref, refuse the command. Otherwise an
        # LLM-emitted unknown ref passes through to the frontend's prefix-match
        # handler and wipes whatever matches.
        session_refs = session_data_manager.list_refs(session_id) or {}
        if not found_id and ref_id not in session_refs:
            return {"error": f"layer_ref {layer_ref!r} 未在当前会话的图层 / 数据引用中找到对应的 id"}

        target = found_id or ref_id
        return {
            "success": True,
            "command": "REMOVE_LAYER",
            "params": {"layer_id": target},
            "message": f"已向地图发送指令：移除图层 {layer_ref} (目标 ID: {target})",
        }

    @tool(registry, name="apply_layer_filter",
           description=(
               "实时图层过滤：按属性条件动态隐藏/显示现有图层的要素。"
               "✅ 用于：快速筛选可见要素（如『只看人口>1000的区域』），不产生新图层。"
               "\n❌ 不要用于：需要导出新要素集或做链式分析 — 用 attribute_filter。"
           ),
           param_descriptions={
               "layer_ref": "图层引用 (ref:xxx) 或名称",
               "expression": "过滤表达式，例如 'pop > 1000' 或 MapLibre/Mapbox GL 风格表达式。设为 null 或空字符串可清除过滤。",
           })
    def apply_layer_filter(layer_ref: str, expression: Any, session_id: Optional[str] = None) -> dict:
        """应用实时图层过滤"""
        if not session_id:
            return {"error": "Missing session_id context"}
        
        # Resolve ref/alias
        ref_id = session_data_manager.resolve_alias(session_id, layer_ref)
        
        # Find canonical ID if it exists in state
        map_state = session_data_manager.get_map_state(session_id)
        layers = map_state.get("layers", [])
        found_id = None
        for l in layers:
            if l.get("id") == ref_id or l.get("id") == layer_ref:
                found_id = l.get("id")
                break
        
        id_to_use = found_id or ref_id
        
        return {
            "success": True,
            "command": "APPLY_LAYER_FILTER",
            "params": {
                "layer_id": id_to_use,
                "filter": expression
            },
            "summary": f"Applied instant filter to layer {layer_ref} with expression: {expression}"
        }
