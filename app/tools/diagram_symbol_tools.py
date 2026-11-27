"""地理锚定统计图符 Agent 工具族（GOAL-20261126-04 WP4）。

三工具（只读×1 + 计划×1 + 受控写×1），与 G33 thematic_authoring 三分法
同构；UI 面板经同一 service（diagram_symbol_authoring）走同一 CAS 链 ——
双入口单一写面。回执有界：几何样本 ≤16、evidence 有界，不整包返回。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class DiagramSymbolInspectArgs(BaseModel):
    session_id: str = Field(..., description="会话 id")
    layer: str = Field(..., description="目标图层 id（或唯一后缀）")


class DiagramSymbolPlanArgs(BaseModel):
    session_id: str = Field(..., description="会话 id")
    layer: str = Field(..., description="目标图层 id（或唯一后缀）")
    block: Dict[str, Any] = Field(
        ..., description=(
            "diagram_symbol.v1 块：{kind: composition_pie|composition_donut|"
            "series_bars|segmented_proportional, categories: [str], fields: [str], "
            "unit?, missing_policy?: omit|block, size_policy?: {...}, palette_id?, "
            "id_field?, layout?: stacked|grouped}"))
    layout_policy: Dict[str, Any] = Field(
        default_factory=dict,
        description="布局预算：{max_glyphs?, collision_padding_px?, "
                    "hide_below_radius_px?, min_zoom_floor?, displacement_enabled?, "
                    "reference_zoom?}（缺省走确定性默认）")


class DiagramSymbolApplyArgs(BaseModel):
    session_id: str = Field(..., description="会话 id")
    layer: str = Field(..., description="目标图层 id（或唯一后缀）")
    block: Dict[str, Any] = Field(
        ..., description="diagram_symbol.v1 块（同 plan；null=解绑恢复既有表达）")
    mode: str = Field("preview", description="preview（零突变）| commit（单笔 CAS）")
    expected_revision: int = Field(
        None, description="CAS 基线（origin=user 的 commit 必填；agent 建议携带）")
    note: str = Field("", description="变更说明（进 provenance）")


def register_diagram_symbol_tools(registry: ToolRegistry) -> None:
    """注册图符三工具（tier-2 制图域；读/预览/受控写）。"""
    from app.services.diagram_symbol_authoring import (
        apply_diagram_symbol,
        inspect_diagram_symbol,
        plan_diagram_symbol,
    )

    async def _guard(coro):
        try:
            return await coro
        except Exception:  # noqa: BLE001 - 工具错误契约，不泄漏栈
            logger.warning("[diagram_symbol] tool failed", exc_info=True)
            return {
                "success": False,
                "code": "DIAGRAM_TOOL_FAILED",
                "message": "diagram symbol operation failed",
                "correction_hint": "检查图层 id、块词表与 MapSpec revision",
            }

    def _status_payload(result: Dict[str, Any]) -> Dict[str, Any]:
        """service status dict → 工具回执（success 语义显式，拒绝带 diagnostics）。"""
        status = result.get("status")
        ok = status in ("ok", "plan", "preview", "committed")
        payload: Dict[str, Any] = {"success": ok, **result}
        return payload

    @tool(
        registry,
        name="webgis_diagram_symbol_inspect",
        capabilities=["map_design"],
        tier=2,
        domains=["mapspec", "cartography"],
        description=(
            "只读检查图层是否具备地理锚定统计图符资格：源类型（geojson "
            "inline/ref 可用，vector 瓦片不可用）、要素数量/几何构成、"
            "可用数值字段、现有 diagram_symbol 块。"
            "\n何时用：用户要求『在每个区县上画饼图/条形图/组成图符』『地图上"
            "直接显示各类别构成』前，先检查数据资格，再 plan。"
        ),
        args_model=DiagramSymbolInspectArgs,
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="light",
        tags=("图符", "饼图", "条形图", "资格", "inspect"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        map_mutations=(),
    )
    async def _inspect(args: DiagramSymbolInspectArgs) -> Dict[str, Any]:
        return _status_payload(await _guard(
            inspect_diagram_symbol(args.session_id, args.layer)))

    @tool(
        registry,
        name="webgis_diagram_symbol_plan",
        capabilities=["map_design"],
        tier=2,
        domains=["mapspec", "cartography"],
        description=(
            "为图层计划地理锚定统计图符（零突变）：返回数据资格会计、全图"
            "公共尺度、布局计划（避让/引线/省略计数）、图例与块指纹。"
            "plan 不落任何 mutation —— 确认证据后才 apply。"
            "\n何时用：inspect 通过后，生成候选证据给用户确认组成/条形/分段"
            "图符的类别与尺度；或对比不同 size_policy 的效果。"
        ),
        args_model=DiagramSymbolPlanArgs,
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        tags=("图符", "计划", "布局", "图例", "plan"),
        output_semantic_type="json",
        result_size_policy="bounded",
        map_mutations=(),
    )
    async def _plan(args: DiagramSymbolPlanArgs) -> Dict[str, Any]:
        return _status_payload(await _guard(plan_diagram_symbol(
            args.session_id, args.layer, args.block,
            layout_policy=args.layout_policy or None)))

    @tool(
        registry,
        name="webgis_diagram_symbol_apply",
        capabilities=["map_design"],
        tier=2,
        domains=["mapspec", "cartography"],
        description=(
            "把 diagram_symbol 块挂到图层（mode=preview 零突变 / commit 单笔 "
            "CAS）；commit 前先 plan 确认资格与布局证据；CAS 失败返回 "
            "superseded（携带最新 revision 重试）。block=null 解绑恢复。"
            "\n何时用：用户确认图符方案后提交；UI 面板与 Agent 走同一服务。"
        ),
        args_model=DiagramSymbolApplyArgs,
        side_effect="state_mutation",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        tags=("图符", "应用", "CAS", "提交", "apply"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        map_mutations=("style_layer", "component"),
        contract_version=2,
    )
    async def _apply(args: DiagramSymbolApplyArgs) -> Dict[str, Any]:
        block = args.block if isinstance(args.block, dict) and args.block else None
        return _status_payload(await _guard(apply_diagram_symbol(
            args.session_id, args.layer, block,
            mode=args.mode,
            expected_revision=args.expected_revision,
            note=args.note,
            origin="agent",
            actor="webgis_diagram_symbol_apply",
        )))


__all__ = ["register_diagram_symbol_tools"]
