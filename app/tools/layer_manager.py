"""图层管理工具 (Session Context Management)"""
import logging
from typing import Any, List, Optional
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


class FinalizeDisplayArgs(BaseModel):
    show_refs: List[str] = Field(
        ...,
        description=(
            "本轮最终要展示的图层引用列表（ref:xxx / 别名 / 名称）。"
            "未列出的分析图层将全部隐藏"
        ),
    )


async def resolve_layer_ref(
    session_id: str,
    layer_ref: str,
) -> tuple[Optional[str], Optional[dict]]:
    """Resolve a layer_ref (ref:xxx / alias / name) to a canonical layer id.

    The one resolver for the 5 layer-mutation tools. Centralizes the alias →
    id → active-layer → name-substring resolution AND the /review P1-6
    existence gate: a resolved id must be session-owned (registered in the
    session's ref store OR echoed in the frontend's active-layers state), or
    the command is refused. Without this gate, an unresolved LLM ref passes
    through to the frontend's prefix-match handler (renderer.ts:285
    `l.id.startsWith(id + '-')`) and matches unintended layers.

    Returns ``(resolved_id, error_dict)``. On success ``error_dict`` is None
    and ``resolved_id`` is the canonical id to send to the frontend. On failure
    ``resolved_id`` is None and ``error_dict`` is the ``{"error": ...}`` dict
    the tool should return.
    """
    # /review P1-6: empty layer_ref prefix-matches everything on the frontend.
    if not layer_ref:
        return None, {"error": "layer_ref 不能为空"}

    ref_id = await session_data_manager.resolve_alias(session_id, layer_ref)
    map_state = await session_data_manager.get_map_state(session_id) or {}
    active_layers = map_state.get("layers", []) or []
    session_refs = await session_data_manager.list_refs(session_id) or {}

    # 1. Exact id match against active layers (by resolved ref_id OR raw layer_ref)
    found_id = None
    for layer in active_layers:
        if layer.get("id") == ref_id or layer.get("id") == layer_ref:
            found_id = layer.get("id")
            break

    # 2. Name-substring fallback (UX: "reference a layer by its name")
    if not found_id and layer_ref:
        for layer in active_layers:
            if layer.get("name") == layer_ref or layer_ref in (layer.get("name") or ""):
                found_id = layer.get("id")
                break

    resolved = found_id or ref_id

    # 3. /review P1-6 existence gate: refuse any ref not session-owned.
    # Accept either source (session ref store OR frontend-echoed active layers)
    # since legitimate flow can have the ref registered before the frontend
    # echoes it back. The point is to refuse a free-form LLM ref that wasn't
    # registered by THIS session.
    if resolved not in session_refs and not any(layer.get("id") == resolved for layer in active_layers):
        return None, {"error": f"layer_ref {layer_ref!r} 未在当前会话的图层 / 数据引用中找到对应的 id"}

    return resolved, None


def register_layer_management_tools(registry: ToolRegistry):
    """注册会话图层管理工具"""

    @tool(
           registry,
           name="finalize_display",
           tier=2,
           # audit4 #978: 域必须是 DOMAIN_KEYWORDS 的键。此前误标 "cartography"
           # （词表中不存在的域）→ tier-2 永不命中，SYSTEM_PROMPT 强制的每轮
           # 收尾钩子在 legacy 引擎整链不可达。"mapspec" 与「显示/隐藏/图层」
           # 激活词对齐，追问收口场景必然命中。
           domains=["mapspec"],
           description=(
               "【每轮分析收尾必调】最终图层显示管理钩子：确定本轮要展示的图层集合。"
               "显示列出的图层，隐藏当前会话中其余所有分析图层（原始 POI 点、边界、"
               "缓冲区等中间层一律让位）。"
               "\n何时用：一轮空间分析的工具全部执行完、即将给出最终结论之前 —— "
               "由你判断哪些图层与最终成图直接相关，一次性收口显示状态。"
               "\n关键约束：show_refs 只包含最终成图需要的图层；宁可少列不要多列，"
               "中间过程层（点云、边界、缓冲、裁剪残料）不要出现在最终地图上。"
           ),
           args_model=FinalizeDisplayArgs,
    )
    async def finalize_display(show_refs: List[str], session_id: Optional[str] = None) -> dict:
        """收尾显示管理：展示 show_refs，隐藏其余分析图层。

        Goal C（终态确认）：结果携带 ``mapspec_fingerprint`` —— 使 finalize
        进入 cartographic gate（Pi has_cartographic_generation / legacy
        tool_pipeline 同源），前端 ack 的 confirmed/store_updated 证据因此
        参与收敛判定，不再游离在门禁之外。展示集同时落服务端 desired state
        （MapSpec layout.visibility，经生命周期引擎事务提交）——即便前端
        错过命令，期望态也已持久。
        """
        if not session_id:
            return {"error": "Missing session_id context"}
        if not show_refs:
            return {"error": "show_refs 不能为空 —— 至少列出最终成图的一个图层"}

        resolved: List[str] = []
        for ref in show_refs:
            id_to_use, err = await resolve_layer_ref(session_id, ref)
            if err:
                return err
            if id_to_use and id_to_use not in resolved:
                resolved.append(id_to_use)

        # 服务端 desired 持久化：展示集 visible=true（只写 MapSpec 已有层；
        # HUD-only 层由前端命令的 pending presentation + durability 提交
        # 收敛——隐藏集的裁决权也在前端：group/pin/boundary 语境只有它有）。
        from app.lib.cartography.quality_loop import cartographic_fingerprint
        from app.services.mapspec.lifecycle_engine import (
            MapSpecLifecycleEngine,
            PatchLayerPresentationIntent,
        )
        from app.services.mapspec.store import mapspec_store_instance

        engine = MapSpecLifecycleEngine()
        spec = await mapspec_store_instance.get_mapspec(session_id) or {}
        spec_layer_ids = {
            str(layer.get("id")) for layer in spec.get("layers", []) if isinstance(layer, dict)
        }
        durability_patched: List[str] = []
        # user_hidden 拒绝集：用户 durable 隐藏的层不因收口被 agent 翻回可见
        #（G6 不变量——user interaction wins 的服务端强制，GISWorldState 守卫）。
        user_guard_refusals: List[str] = []
        from app.services.gis_world_state import apply_gis_mutation
        for layer_id in resolved:
            if layer_id not in spec_layer_ids:
                continue
            pres = await apply_gis_mutation(
                session_id,
                PatchLayerPresentationIntent(layer_id=layer_id, visible=True),
                origin="agent",
                actor="finalize_display",
                engine=engine,
            )
            if not pres.is_error and not pres.superseded:
                durability_patched.append(layer_id)
            elif pres.is_error and "不覆盖用户显式操作" in (pres.error_msg or ""):
                user_guard_refusals.append(layer_id)

        final_spec = await mapspec_store_instance.get_mapspec(session_id) or {}
        fingerprint = cartographic_fingerprint(final_spec) if final_spec else ""

        # review P2-3/P1（409 风暴根因）：服务端 desired patch 推进了
        # mutation_revision，但结果不带 revision/mapspec → 前端游标必然
        # 过期 → finalize 突发提交全数 409。回传两项让 SSE 消费端
        # （use-sse-stream 读 data.mutation_revision/data.mapspec）在命令
        # 执行前收敛游标。
        from app.services.session_data import session_data_manager as _sdm
        _state = await _sdm.get_map_state(session_id)
        _revision = _state.get("_cartographic_mutation_revision", 0)

        return {
            "success": True,
            "command": "FINALIZE_DISPLAY",
            "params": {"show_layer_ids": resolved},
            # 门禁证据：fingerprint 存在 → dispatch 进入 cartographic gate，
            # 前端 ack（confirmed/store_updated + visible/hidden/unresolved
            # layer ids）参与收敛判定。
            "mapspec_fingerprint": fingerprint,
            "mutation_revision": _revision if isinstance(_revision, int) else 0,
            "mapspec": final_spec or None,
            "final_display": {
                "show_layer_ids": resolved,
                "desired_state_patched": durability_patched,
                "user_hidden_respected": user_guard_refusals,
                "verification": "frontend_runtime",
            },
            "message": (
                f"最终展示集已收口：显示 {len(resolved)} 个图层"
                f"（{', '.join(resolved[:3])}{'…' if len(resolved) > 3 else ''}），"
                f"其余分析图层已隐藏"
            ),
        }

    @tool(registry, name="alias_layer",
           description="为当前会话中的数据引用（ref:xxx）设置一个语义化的别名。设置后，后续可以直呼其名（如：'核心保护区'）来引用该数据。",
           args_model=AliasLayerArgs)
    async def alias_layer(ref_id: str, alias: str, session_id: Optional[str] = None) -> dict:
        """为引用的数据设置别名"""
        if not session_id:
            return {"error": "Missing session_id context"}

        await session_data_manager.set_alias(session_id, ref_id, alias)
        return {
            "success": True, 
            "ref_id": ref_id, 
            "alias": alias, 
            "message": f"已成功为 {ref_id} 设置别名: {alias}。您现在可以在后续操作中直接使用该别名引用此图层。"
        }

    @tool(registry, name="inventory_layers",
           description="展示当前会话中所有的地理数据图层（包含系统生成的引用 ID 和您设置的别名）。")
    async def inventory_layers(session_id: Optional[str] = None) -> dict:
        """列出所有图层"""
        if not session_id:
            return {"error": "Missing session_id context"}

        layers = await session_data_manager.list_refs(session_id)
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
    async def switch_base_layer(name: str, session_id: Optional[str] = None) -> dict:
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

        await session_data_manager.set_map_state(session_id, "base_layer", resolved_name)
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
    async def set_layer_status(layer_ref: str, visible: Optional[bool] = None, opacity: Optional[float] = None, session_id: Optional[str] = None) -> dict:
        """修改图层状态"""
        if not session_id:
            return {"error": "Missing session_id context"}

        id_to_use, err = await resolve_layer_ref(session_id, layer_ref)
        if err:
            return err

        # #609: 未传的 Optional 字段必须从 params 省略（而非序列化为 JSON null）。
        # JSON null 曾被前端 `!== undefined` 判存在性、把 null 当 falsy → 图层被
        # 隐藏 + 后验证假收敛。省略键 = 前端看到"该属性未被请求"。display_layer
        # 早已如此（只发 visible+name，不带 opacity）。
        params: dict = {"layer_id": id_to_use}
        if visible is not None:
            params["visible"] = visible
        if opacity is not None:
            params["opacity"] = opacity

        return {
            "success": True,
            "command": "LAYER_VISIBILITY_UPDATE",
            "params": params,
            "message": f"已向地图发送指令：更新图层 {layer_ref} (目标 ID: {id_to_use}) 的显示设置。"
        }

    @tool(registry, name="update_layer_appearance",
           description="修改图层的视觉样式（如颜色、线宽、描边色、点大小、虚线样式等）。可以通过 ID (ref:xxx)、别名或图层名称引用图层。")
    async def update_layer_appearance(
        layer_ref: str,
        color: Optional[str] = None,
        stroke_width: Optional[float] = None,
        stroke_color: Optional[str] = None,
        point_size: Optional[float] = None,
        dash_array: Optional[str] = None,
        fill: Optional[bool] = None,
        render_type: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """修改图层外观"""
        if not session_id:
            return {"error": "Missing session_id context"}

        id_to_use, err = await resolve_layer_ref(session_id, layer_ref)
        if err:
            return err

        style: dict = {}
        if color is not None:
            style["color"] = color
        if stroke_width is not None:
            style["strokeWidth"] = stroke_width
        if stroke_color is not None:
            style["strokeColor"] = stroke_color
        if point_size is not None:
            style["pointSize"] = point_size
        if dash_array is not None:
            style["dashArray"] = dash_array
        if fill is not None:
            style["fill"] = fill
        if render_type is not None:
            style["renderType"] = render_type

        return {
            "success": True,
            "command": "LAYER_STYLE_UPDATE",
            "params": {
                "layer_id": id_to_use,
                "style": style,
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
    async def reorder_layer(layer_ref: str, position: str = "top", before_ref: Optional[str] = None, session_id: Optional[str] = None) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}

        pos = (position or "top").lower().strip()
        if pos not in {"top", "bottom", "up", "down", "before"}:
            return {"error": f"Invalid position '{position}', must be one of: top/bottom/up/down/before"}
        if pos == "before" and not before_ref:
            return {"error": "position=before 时必须提供 before_ref"}

        ref_id, err = await resolve_layer_ref(session_id, layer_ref)
        if err:
            return err

        before_id = None
        if before_ref:
            before_id, before_err = await resolve_layer_ref(session_id, before_ref)
            if before_err:
                return before_err

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
               "\n若当前 session 已建立 MapSpec（曾用过 webgis_* 工具），改用 webgis_layer_remove——"
               "它同步更新 desired MapSpec 并携带 runtime 指令，避免 desired 与运行时地图分叉。"
               "\n关键约束：删除是不可逆操作；ref_id 来自 session 数据存储，删除画布上的图层不会清掉 session 数据本身。"
           ),
           args_model=RemoveLayerArgs)
    async def remove_layer(layer_ref: str, session_id: Optional[str] = None) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}

        target, err = await resolve_layer_ref(session_id, layer_ref)
        if err:
            return err

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
    async def apply_layer_filter(layer_ref: str, expression: Any, session_id: Optional[str] = None) -> dict:
        """应用实时图层过滤"""
        if not session_id:
            return {"error": "Missing session_id context"}

        id_to_use, err = await resolve_layer_ref(session_id, layer_ref)
        if err:
            return err

        return {
            "success": True,
            "command": "APPLY_LAYER_FILTER",
            "params": {
                "layer_id": id_to_use,
                "filter": expression
            },
            "summary": f"Applied instant filter to layer {layer_ref} with expression: {expression}"
        }

    @tool(registry, name="display_layer",
           description=(
               "将已加载但隐藏的数据图层显示到地图上，并赋予有意义的名称。"
               "✅ 必须在任务结束前调用：只显示与任务目标直接相关的最终结果图层。"
               "✅ 同一个 ref_id 只需调用一次。"
               "\n❌ 不要用于：展示中间过渡数据（边界查询、原始 POI 点、缓冲区辅助层等）。"
           ),
           param_descriptions={
               "ref_id": "工具返回的 ref_id，如 'geojson-abc123'",
               "name": "显示在图层面板的名称，应简洁描述分析内容，如'锦江区大学分布'",
               "color": "（可选）图层颜色，16进制如 '#e11d48'",
           })
    def display_layer(ref_id: str, name: str, color: Optional[str] = None, session_id: Optional[str] = None) -> dict:
        """显示隐藏的结果图层"""
        if not session_id:
            return {"error": "Missing session_id context"}

        params: dict = {"layer_id": ref_id, "visible": True, "name": name}
        if color is not None:
            params["color"] = color

        return {
            "success": True,
            "command": "LAYER_VISIBILITY_UPDATE",
            "params": params,
            "summary": f"图层 {ref_id} 已显示，名称：{name}",
        }
