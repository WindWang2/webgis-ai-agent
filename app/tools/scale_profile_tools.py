"""多尺度表达档案工具（GOAL 04 M7）—— Agent 可发现/可执行/可审计三面。

- ``webgis_inspect_map_scale_profile``（只读）：图层尺度档案证据面 ——
  源时点/重要性字段证据（真实值分布）/带级取舍预览/既有档案；
- ``webgis_preview_scale_representation``（只读）：零突变完整计划预览；
- ``webgis_apply_scale_representation``（受控写）：preview/commit；
  commit 工件存**新 ref**（源数据不动）→ MapSpec 单事务 CAS 落笔
  （representation windows + layer.scale_profile）→ provenance + 回执。

自然语言场景（Harness 可直接编排）：
- 「放大后显示所有学校点」→ apply overrides=[{kind:pin_mode,value:raw}]；
- 「缩小省级道路图并突出主要干线」→ apply（默认 importance 取舍，
  world/province 带保留词表序前 K）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class InspectArgs(BaseModel):
    layer: str = Field(..., max_length=128,
                       description="目标图层 id（或唯一后缀；inspect 只读）")


class PreviewArgs(BaseModel):
    layer: str = Field(..., max_length=128, description="目标图层 id")
    keep_ranks: Optional[Dict[str, int]] = Field(
        None, description="各尺度带保留的重要性秩数覆盖，如 {\"world\":3,\"city\":6}")
    overrides: Optional[List[Dict[str, Any]]] = Field(
        None, max_length=8,
        description="用户覆盖列表：[{kind:pin_mode|mandatory_feature|"
                    "simplify_tolerance|band_keep_values, value, detail?}]")


class ApplyArgs(PreviewArgs):
    mode: str = Field("preview", description="preview=零突变预览；commit=提交")
    operation_id: Optional[str] = Field(
        None, max_length=64, description="幂等键；重试传同一 id 可幂等重放")
    expected_revision: Optional[int] = Field(
        None, ge=0, description="CAS 基线（可选；并发修改会使操作 superseded）")
    note: Optional[str] = Field(None, max_length=160, description="归因摘要")


def register_scale_profile_tools(registry: ToolRegistry) -> None:
    """注册多尺度表达档案三工具（只读×2 + 受控写×1）。"""

    @tool(
        registry,
        name="webgis_inspect_map_scale_profile",
        capabilities=["cartography_scale_inspect"],
        domains=["mapspec", "cartography"],
        tier=1,
        args_model=InspectArgs,
        description=(
            "只读检查一个地图图层的多尺度表达证据：源数据时点（ref/revision）、"
            "可信重要性字段（需要真实值分布证据 —— 道路等级/行政级别词表）、"
            "各尺度带的取舍预览、既有 ScaleRepresentationProfile。"
            "\n何时用：用户问『这层在不同缩放级别怎么显示』『为什么缩小后有些"
            "道路不见了』，或 apply 前想确认字段证据。"
            "\n何时不用：要看图层数值统计用 layer_aggregate；要改表达用 "
            "webgis_apply_scale_representation。"
        ),
        side_effect="cacheable_read",
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=("多尺度", "尺度档案", "证据", "scale profile"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "target_not_found", "no_mapspec"),
    )
    async def webgis_inspect_map_scale_profile(
        layer: str, session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.scale_profile import (
            ScaleProfileError,
            inspect_map_scale_profile,
        )
        try:
            return await inspect_map_scale_profile(session_id, layer)
        except ScaleProfileError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — 工具错误契约
            logger.warning("[inspect_map_scale_profile] failed", exc_info=True)
            return {"error": f"inspect failed: {exc}"}

    @tool(
        registry,
        name="webgis_preview_scale_representation",
        capabilities=["cartography_scale_preview"],
        domains=["mapspec", "cartography"],
        tier=1,
        args_model=PreviewArgs,
        description=(
            "零突变预览一个图层的完整多尺度表达计划：四尺度带（world/province/"
            "city/street）的表达模式、每带显示/原始要素数、几何综合策略与容差、"
            "披露与语义检查。不存任何 ref、不改 MapSpec。"
            "\n何时用：apply 前核对计划，或回答『综合后会丢多少要素』。"
        ),
        side_effect="cacheable_read",
        deterministic=True,
        latency_class="medium",
        memory_class="heavy",
        scale_class="large",
        tags=("多尺度", "预览", "零突变", "scale preview"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "target_not_found", "no_mapspec"),
    )
    async def webgis_preview_scale_representation(
        layer: str,
        keep_ranks: Optional[Dict[str, int]] = None,
        overrides: Optional[List[Dict[str, Any]]] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.scale_profile import (
            ScaleProfileError,
            preview_scale_representation,
        )
        try:
            return await preview_scale_representation(
                session_id, layer, keep_ranks=keep_ranks, overrides=overrides)
        except ScaleProfileError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[preview_scale_representation] failed", exc_info=True)
            return {"error": f"preview failed: {exc}"}

    @tool(
        registry,
        name="webgis_apply_scale_representation",
        capabilities=["cartography_scale_apply"],
        domains=["mapspec", "cartography"],
        tier=2,
        args_model=ApplyArgs,
        description=(
            "为一个图层落笔多尺度表达档案（ScaleRepresentationProfile.v1，"
            "MapSpec v1.9）：按道路等级/行政级别等可信字段做语义取舍 + 拓扑"
            "安全几何综合，产物存为**新 ref**（原始数据永不修改），MapSpec 经"
            "单事务 CAS 提交（representation windows + layer.scale_profile），"
            "统计/图例口径恒 original_source。"
            "\n何时用：用户要『省域道路图缩小后突出主要干线』『不同缩放自动换"
            "合适表达』『放大后显示所有学校点』（后者传 overrides="
            "[{kind:pin_mode,value:raw}]）。"
            "\n何时不用：只看计划用 preview；一般图层显隐/样式走 map_operation。"
            "\n关键语义：mode=preview 零突变（推荐先预览）；commit 幂等"
            "（同 operation_id 重放返回存证）；原始 ref/统计/图例不被改动。"
        ),
        side_effect="state_mutation",
        deterministic=False,
        latency_class="slow",
        memory_class="heavy",
        scale_class="large",
        tags=("多尺度", "地图综合", "受控提交", "scale apply", "generalization"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        required_context=("map_state",),
        map_mutations=("style_layer",),
        data_mutations=("session_state",),
        failure_modes=("invalid_args", "target_not_found", "superseded",
                       "no_mapspec", "profile_invalid"),
    )
    async def webgis_apply_scale_representation(
        layer: str,
        mode: str = "preview",
        keep_ranks: Optional[Dict[str, int]] = None,
        overrides: Optional[List[Dict[str, Any]]] = None,
        operation_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
        note: Optional[str] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.scale_profile import (
            ScaleProfileError,
            apply_scale_representation,
        )
        try:
            receipt = await apply_scale_representation(
                session_id, layer, mode=mode, keep_ranks=keep_ranks,
                overrides=overrides, operation_id=operation_id,
                expected_revision=expected_revision, note=note)
        except ScaleProfileError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[apply_scale_representation] failed", exc_info=True)
            return {"error": f"apply failed: {exc}"}
        result = dict(receipt)
        result["success"] = receipt.get("status") in ("preview", "committed")
        if not result["success"]:
            result["error"] = receipt.get("error") or str(receipt.get("status"))
        return result
