"""GIS Harness agent 工具面（Pi / ChatEngine 共用，经 ToolDispatchService 执行）。

三个工具对应 Harness 的三个职责入口：

- ``webgis_map_intent``     —— Intent 解析 + Recipe 候选 + 计划骨架（纯确定性，
                              LLM hint 显式合并、显式记录）；
- ``webgis_map_product``    —— 数据/图层到位后的产品组装：eligibility 复检
                              → 角色绑定 → 缺失图层授权 → 组件/版面落 MapSpec；
- ``webgis_component_update``—— 组件局部突变（「换一个指南针」「色条竖向」），
                              只动 layout.components，绝不触发数据重查/重分析。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.tools_component import (  # noqa: F401 — re-export（导入方兼容）
    _component_lifecycle_action,
    _forward_evidence,
    _ref_probe,
)
from app.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from app.services.gis_harness.planner import AlgorithmSelectionRecord

logger = logging.getLogger(__name__)


class MapIntentArgs(BaseModel):
    query: str = Field(..., max_length=2000, description="用户原始 GIS 请求文本（如『成都小学的分布情况』）")
    task_hint: Optional[str] = Field(
        None, description="[可选] LLM 语义提示：任务类型。合法值见返回 intent.task 的类型族")
    scope_hint: Optional[str] = Field(None, description="[可选] 范围提示（如 成都市）")
    subject_hint: Optional[str] = Field(None, description="[可选] 主体提示（如 小学）")
    geometry_hint: Optional[str] = Field(
        None, description="[可选] 期望几何 point/line/polygon/raster")


class MapProductArgs(BaseModel):
    query: str = Field(..., max_length=2000, description="用户原始请求文本（驱动 intent/recipe/计划）")
    session_id: Optional[str] = Field(None, description="会话 ID")
    layer_ids: List[str] = Field(
        default_factory=list,
        description="已存在于 MapSpec 的图层 id（来自先前工具 step_result.layer_id）。"
                    "按图层类型确定性绑定角色：heatmap→primary、circle→point_overlay、"
                    "fill→admin_choropleth、raster→raster_surface")
    primary_ref: Optional[str] = Field(
        None, description="主数据 ref（ref:geojson-xxx）。缺失的规划图层会从该 ref 授权补齐")
    overlay_refs: List[str] = Field(
        default_factory=list,
        description="辅助数据 ref 列表（如行政区边界面 ref），补充 reference 角色")
    title: Optional[str] = Field(None, description="产品标题（缺省由 intent 派生）")
    template_id: Optional[str] = Field(None, description="产品模板 id（缺省按 recipe 自动匹配）")
    palette: str = Field("classic", description="热力配色 classic/magma/viridis/thermal")
    radius_px: Optional[int] = Field(
        None, ge=4, le=80, description="视觉热力半径（像素）。显式像素语义")
    recipe_id: Optional[str] = Field(
        None, description="沿用 webgis_map_intent 推荐的 recipe id（计划连续性："
                          "两阶段共用同一份计划，避免 hint 纠偏被丢失）")
    task_hint: Optional[str] = Field(
        None, description="[可选] 与 webgis_map_intent 相同的任务 hint（重放合并）")


from app.services.gis_harness.tools_component_update import (
    ComponentUpdateArgs as ComponentUpdateArgs,
    register_component_update_tool,
)


class ProductEditArgs(BaseModel):
    """webgis_product_edit：产品语义编辑（spec 图上先验证后提交）。"""

    session_id: Optional[str] = Field(None, description="会话 ID")
    op: str = Field(..., max_length=40, description=(
        "编辑操作：remove_view / add_view / set_view_filter / toggle_view / "
        "replace_component / toggle_component / set_caption / set_delivery"))
    target: str = Field("", max_length=120, description=(
        "操作目标：view_id（如 v-chart / v-map）或组件族类型"
        "（toggle_component 时 component_type 也可放 payload）"))
    payload: Optional[Dict[str, Any]] = Field(None, description=(
        "操作载荷（按 op）：add_view.view={view_id,kind,...}；"
        "set_view_filter.filter={...}；replace_component.chart_kind=bar；"
        "toggle_component.component_type=chart_panel + enabled=false；"
        "set_caption.text=...；set_delivery.delivery={targets,aspect,audience}"))
    reason: str = Field("", max_length=200, description="编辑原因（进 override 账，可追溯）")


def _manifest_stale(recorded: str) -> bool:
    """#1084: 计划记录的 registry 指纹是否落后于当前编译（诚实披露）。

    v3(audit A1)：此前调用不存在的 ``manifest.stale(...)``（真实 API 是
    ``is_stale_plan``），AttributeError 被 broad except 洗成 ``False`` ——
    指纹披露恒假，stale 信号被 API 漂移静默吞掉。stale/version mismatch
    是 correctness signal：不做异常兜底，manifest 访问失败让它显式暴露
    （``get_runtime_manifest`` 内部对各 registry 已有容错，走到异常即真
    故障）。
    """
    from app.lib.gis.runtime_manifest import get_runtime_manifest
    return get_runtime_manifest().is_stale_plan(recorded or None)


# #1076(D-8): turn 级记忆 —— intent→product 链此前每个 harness 工具调用
# 各查一次 DB（一条链 2 次往返）。缓存键为 (project_id, turn_id)：同 turn
# 内的记忆漂移不可见（recipe 验证事实本 turn 内不会变），跨 turn 失效。
_verified_recipes_cache: dict[tuple, tuple] = {}
_VERIFIED_CACHE_MAX = 64


async def _project_verified_recipes() -> set:
    """当前 turn 的项目已验证 recipe id 集合（无项目上下文 → 空集）。

    读账本是一次有界索引查询（recipe_outcome 事实通常个位数），且仅当
    RuntimeContext 携带 project_id 时发生——匿名/无项目会话零开销。
    账本不可用时返回空集（记忆是增值信号，绝不阻断推荐）。
    """
    from app.lib.runtime.context import current_runtime_context

    ctx = current_runtime_context()
    project_id = getattr(ctx, "project_id", None)
    if not project_id:
        return set()
    turn_id = getattr(ctx, "turn_id", None) or ""
    cache_key = (str(project_id), str(turn_id))
    cached = _verified_recipes_cache.get(cache_key)
    if cached is not None:
        return set(cached)

    def _read() -> set:
        from app.core.database import SessionLocal
        from app.services.cartography.project_memory import get_verified_recipe_ids

        with SessionLocal() as db:
            return get_verified_recipe_ids(db, project_id)

    try:
        import asyncio as _asyncio

        verified = await _asyncio.to_thread(_read)
    except Exception:  # noqa: BLE001 — 记忆缺失退化为无加成
        return set()
    _verified_recipes_cache[cache_key] = tuple(sorted(verified))
    while len(_verified_recipes_cache) > _VERIFIED_CACHE_MAX:
        _verified_recipes_cache.pop(next(iter(_verified_recipes_cache)))
    return verified


def _product_composition_identity(
    plan: Any,
    component_dicts: List[Dict[str, Any]],
    existing_spec: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """H02-G3：产品组合身份块（layout.composition）的有界投影。

    模板缺席/未注册 → None（layout_set 的 composition=None 即"不触碰"）。
    用户显式契约身份（contract_id 非空）在场 → None：agent 再制图不得
    覆盖用户的组合契约（user-wins）。``applied_revision=0`` 与契约工具的
    ``int(expected_revision or 0)`` 同口径（产品路径未钉 revision）。
    """
    compo_id = str(
        (getattr(plan, "template_selection", None) or {}).get(
            "composition_template_id")
        or ""
    )
    if not compo_id:
        return None
    try:
        from app.lib.cartography.component_abi import (
            COMPONENT_ABI_VERSION,
            versions_projection,
        )
        from app.lib.cartography.composition_contract import CompositionIdentity
        from app.lib.cartography.composition_templates import (
            get_composition_template_registry,
        )

        tpl = get_composition_template_registry().get(compo_id)
        if tpl is None:
            return None
        existing = ((existing_spec or {}).get("layout") or {}).get("composition")
        if (
            isinstance(existing, dict)
            and str(existing.get("contract_id") or "")
        ):
            return None
        # G04：资产模板（builtin；DB 资产不进全局注册表）→ 强版本引用
        # （id+revision），取代弱 schema_version 语义。
        asset_id = ""
        asset_revision = 0
        template_version = str(getattr(tpl, "schema_version", 1))
        if tpl.id.startswith("asset."):
            registry_ref = get_composition_template_registry()
            revision = registry_ref.asset_revision_of(tpl.id)
            if revision is not None:
                asset_id = tpl.id
                asset_revision = int(revision)
                template_version = f"asset-r{asset_revision}"
        identity = CompositionIdentity(
            template_id=tpl.id,
            template_version=template_version,
            component_abi_version=COMPONENT_ABI_VERSION,
            component_versions=versions_projection(
                [str(c.get("type") or "") for c in component_dicts]),
            asset_id=asset_id,
            asset_revision=asset_revision,
        )
        return identity.to_bounded_dict()
    except Exception:  # noqa: BLE001 — 身份是增值披露，缺席不阻断组装
        return None


def bounded_selection_evidence(record: AlgorithmSelectionRecord) -> dict:
    """G02：selection → LLM 面证据的有界投影。

    空值字段（""/[]/None）不入证据 —— 科学 evidence 字段 additive 后
    空语法占位会使大载荷产品（G4 150k POI 的 24KB descriptor 预算，
    test_benchmark_harness 钉扎）无谓超限；数值 0 是合法证据值（如
    cost_score=0.0）不剥（review P3-2）。非空证据照常全量转录，
    record 本体不受影响（tests/unit/gis/test_g02_gate_chain.py 钉扎）。
    """
    return {k: v for k, v in record.model_dump().items() if v or v == 0}


def register_gis_harness_tools(registry: ToolRegistry) -> None:
    """注册 GIS Harness 工具（tier 1：意图解析廉价且恒可用）。"""

    from app.services.gis_harness.tool_map_intent import register_map_intent
    register_map_intent(registry)

    from app.services.gis_harness.tool_map_product import register_map_product
    register_map_product(registry)

    from app.services.gis_harness.tool_map_design_select import register_map_design_select
    register_map_design_select(registry)

    from app.services.gis_harness.tool_product_edit import register_product_edit
    register_product_edit(registry)

    register_component_update_tool(registry)

    from app.services.gis_harness.tool_component_catalog import register_component_catalog
    register_component_catalog(registry)


    from app.services.gis_harness.tool_world_state import register_world_state
    register_world_state(registry)
