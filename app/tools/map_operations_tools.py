"""地图表现层操作事务工具（map_operation.v1，Track A M2/M4）。

``webgis_map_operation``：把「修改当前地图」的多项表现层请求收拢为一次
受控操作 —— preview（零突变 before/after）或 commit（既有 mutation
门面 CAS 链 + 单回执）。效果词表与执行语义见
``app/services/gis_harness/map_operations``（ADR-0278）。

与既有细粒度工具的关系：本工具不替代它们（单动作仍可直接调
set_layer_status/update_layer_appearance/fly_to_location 等）；它解决的是
**多动作一次提交 + 统一回执 + 指代解析 + 敏感人环** 这层组合语义。
所有效果最终经 ``apply_gis_mutation``/``apply_gis_mutation_batch`` 公共
门面落盘 —— MapSpec 仍是唯一 desired state，无第二状态仓库。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class WebgisMapOperationArgs(BaseModel):
    """webgis_map_operation 的 LLM 参数模型。

    显式 args_model：本模块使用 future annotations，签名推导的字符串
    注解在 create_model 动态命名空间不可解析（与 finalize_display 的
    FinalizeDisplayArgs 同款处理）。
    """

    effects: List[Dict[str, Any]] = Field(
        ..., min_length=1, max_length=8,
        description="效果列表（≤8）；kind 词表与约束见工具描述",
    )
    mode: str = Field(
        "preview",
        description="preview=零突变预览（推荐先预览）；commit=提交（含敏感效果时停在待确认）",
    )
    operation_id: Optional[str] = Field(
        None, max_length=64,
        description="操作幂等键（UUID）；重试时传同一个 id 可幂等重放",
    )
    expected_revision: Optional[int] = Field(
        None, ge=0, description="CAS 基线（可选；并发修改会使操作 superseded）",
    )
    note: Optional[str] = Field(None, max_length=160, description="归因摘要（可选）")


def register_map_operation_tools(registry: ToolRegistry):
    """注册地图操作事务工具。"""

    @tool(
        registry,
        name="webgis_map_operation",
        capabilities=["layer_display_control", "map_viewport_control"],
        domains=["mapspec"],
        tier=2,
        args_model=WebgisMapOperationArgs,
        description=(
            "把一次自然语言的多个地图表现修改（隐藏/显示图层、透明度、样式尺寸、"
            "层序、缩放定位、组件位置、移除图层）打包成一个受控操作。"
            "\n何时用：用户一次说了多项地图修改（如『隐藏热力图，学校点放大一点，"
            "缩放到武侯区，图例别动』）；或需要 before/after 预览、操作回执时。"
            "\n何时不用：单项简单修改直接用对应细粒度工具；数据分析/制图生成不属本工具。"
            "\n关键语义：mode=preview 零突变；commit 走 MapSpec CAS（revision 前进）；"
            "含 remove_layer 时 commit 只会停在待用户确认，不会直接删。"
            "效果 kind 词表：set_layer_visibility/set_layer_opacity/set_layer_paint/"
            "set_layer_symbol(point_size|line_width|label_size|icon_size，value 或 scale)/"
            "move_layer(position=top|bottom|up|down)/set_viewport(center+zoom 或 bbox)/"
            "set_component/remove_layer(需用户确认)。"
            "目标指代 target_kind：layer_ref（默认，传别名/名称/id）、"
            "focused_layer（当前焦点层）、last_result（上一个结果层）。"
            "多候选歧义时返回 clarification 候选，不修改地图。"
        ),
        side_effect="state_mutation",
        deterministic=False,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=("地图操作", "多动作", "预览", "受控提交", "map operation"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        required_context=("map_state",),
        map_mutations=("style_layer", "remove_layer", "camera", "component"),
        data_mutations=("session_state",),
        failure_modes=("invalid_args", "target_not_found", "superseded"),
    )
    async def webgis_map_operation(
        effects: List[Dict[str, Any]],
        mode: str = "preview",
        operation_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
        note: Optional[str] = None,
        session_id: str = "",
    ) -> dict:
        """受控地图表现层操作（preview 零突变 / commit CAS 提交）。

        effects 每项形如 {"kind": "set_layer_visibility", "target": "热力图",
        "visible": false}；详细词表见工具描述。重试务必复用同一
        operation_id（幂等重放返回存证回执）。
        """
        if not session_id:
            return {"error": "Missing session_id context"}

        from app.services.gis_harness.map_operations import (
            MapOperationRequest,
            commit_map_operation,
            preview_map_operation,
        )
        from app.services.gis_harness.map_operations.service import mint_operation_id

        try:
            request = MapOperationRequest(
                operation_id=operation_id or mint_operation_id(),
                mode=mode,
                effects=effects,  # type: ignore[arg-type]
                expected_revision=expected_revision,
                note=note,
            )
        except ValueError as exc:
            return {"error": f"invalid map operation request: {exc}"}

        try:
            if mode == "commit":
                receipt = await commit_map_operation(session_id, request)
            else:
                receipt = await preview_map_operation(session_id, request)
        except Exception as exc:  # noqa: BLE001 — 工具错误契约
            logger.warning("[webgis_map_operation] failed", exc_info=True)
            return {"error": f"map operation failed: {exc}"}

        # 前端/LLM 双面载荷：receipt（commit 时含 commands/mapspec）+ 摘要
        result: Dict[str, Any] = dict(receipt)
        # 独立评审 C-1："failed"（全部效果未生效）不是成功的中止态 ——
        # 与 noop/rejected（世界已满足/被拒）不同，它要求 LLM 修正重试。
        result["success"] = receipt.get("status") in (
            "preview", "committed", "noop", "partial", "superseded", "rejected",
            "confirmation_required", "clarification_needed",
        )
        if not result["success"]:
            result["error"] = receipt.get("message") or str(receipt.get("status"))
        return result
