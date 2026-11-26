"""纸面目标比例尺综合工具（GOAL 04 WP5）—— Agent 可发现/可执行/可审计三面。

- ``webgis_inspect_paper_scale_generalization``（只读）：目标尺度证据面 ——
  等效 zoom、Mercator 纬度变化披露、几何族支持、重要性证据、策略提示；
- ``webgis_preview_paper_scale_generalization``（只读）：零突变完整编译
  预览（管线 + 工件统计 + QA 审计）；
- ``webgis_apply_paper_scale_generalization``（受控写）：preview/commit；
  commit = QA fail-closed 门 → 工件存**新 ref**（源数据不动）→ MapSpec
  顺序 CAS（UpsertSource + UpsertLayer：paper_plans 冻结 + 表达窗口
  精确裁剪插入）→ 回执环幂等。

自然语言场景（Harness 可直接编排）：
- 「出一张 1:50万 总览图，别丢掉县城和主干道路」→ apply
  denominator_text="1:50万" purpose="overview" kept_values=["县城","国道"...]
  （或由重要性证据自动裁决）+ hard constraint 由 inspect 面核对；
- 「改成 A4 横版出版图」→ page_text="A4横版"（重编译 + 冻结新计划）。
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class InspectArgs(BaseModel):
    layer: str = Field(..., max_length=128,
                       description="目标图层 id（或唯一后缀；inspect 只读）")
    denominator: Optional[int] = Field(
        None, description="目标比例尺分母（1:N 的 N，如 500000）")
    denominator_text: Optional[str] = Field(
        None, max_length=32, description="比例尺文本（如 1:50万；与 denominator 二选一）")
    page_text: Optional[str] = Field(
        None, max_length=32, description="页面文本（如 A4横版 / A3 portrait）")
    purpose: str = Field(
        "reference", max_length=32,
        description="地图用途：overview/reference/planning/navigation/"
                    "thematic_exhibition")
    dpi: Optional[float] = Field(None, gt=0, le=2400, description="输出 DPI")


class PreviewArgs(InspectArgs):
    kept_values: Optional[List[Any]] = Field(
        None, max_length=32,
        description="线族显式保留词表值（覆盖重要性秩；如 [\"国道\",\"省道\"]）")
    sum_fields: Optional[List[str]] = Field(
        None, max_length=8,
        description="计数/总量字段（消除/聚合 Σ守恒；如 [\"school_count\"]）")


class ApplyArgs(PreviewArgs):
    mode: str = Field("preview", description="preview=零突变预览；commit=提交")
    operation_id: Optional[str] = Field(
        None, max_length=64, description="幂等键；重试传同一 id 可幂等重放")
    expected_revision: Optional[int] = Field(
        None, ge=0, description="CAS 基线（可选；并发修改会使操作 superseded）")
    note: Optional[str] = Field(None, max_length=160, description="归因摘要")


def _args_to_kwargs(args: dict) -> dict:
    """工具入参 → 服务参数（分层透传；避免同名键漂移）。"""
    return {
        "denominator": args.get("denominator"),
        "denominator_text": args.get("denominator_text"),
        "page_text": args.get("page_text"),
        "purpose": args.get("purpose") or "reference",
        "dpi": args.get("dpi"),
        "kept_values": args.get("kept_values"),
        "sum_fields": args.get("sum_fields"),
        "keep_feature_ids": args.get("keep_feature_ids"),
        "keep_values": args.get("keep_values"),
        "keep_values_field": args.get("keep_values_field"),
        "keep_region": args.get("keep_region"),
    }


def register_paper_scale_tools(registry: ToolRegistry) -> None:
    """注册纸面比例尺综合三工具（只读×2 + 受控写×1）。"""

    @tool(
        registry,
        name="webgis_inspect_paper_scale_generalization",
        capabilities=["cartography_paper_scale_inspect"],
        domains=["mapspec", "cartography"],
        tier=2,
        args_model=InspectArgs,
        description=(
            "只读检查一个图层在**目标纸面比例尺**（如 1:50万）下的制图综合"
            "证据：等效屏幕 zoom、Web Mercator 纬度比例尺变化披露、几何族"
            "是否支持（点/线/面）、可信重要性字段、按用途的策略提示"
            "（取舍激进度/网络硬约束/专题守恒）。"
            "\n何时用：用户说出目标比例尺或纸面尺寸（『1:50万』『A4横版』）"
            "后的第一步；apply 前确认证据与硬约束。"
            "\n何时不用：屏幕缩放带表达用 webgis_inspect_map_scale_profile。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=("制图综合", "比例尺", "出版", "paper scale"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "target_not_found", "no_mapspec"),
    )
    async def webgis_inspect_paper_scale_generalization(
        layer: str,
        denominator: Optional[int] = None,
        denominator_text: Optional[str] = None,
        page_text: Optional[str] = None,
        purpose: str = "reference",
        dpi: Optional[float] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.paper_generalization import (
            PaperScaleError,
            inspect_paper_scale_generalization,
        )
        try:
            return await inspect_paper_scale_generalization(
                session_id, layer,
                denominator=denominator, denominator_text=denominator_text,
                page_text=page_text, purpose=purpose, dpi=dpi)
        except PaperScaleError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — 工具错误契约
            logger.warning("[inspect_paper_scale] failed", exc_info=True)
            return {"error": f"inspect failed: {exc}"}

    @tool(
        registry,
        name="webgis_preview_paper_scale_generalization",
        capabilities=["cartography_paper_scale_preview"],
        domains=["mapspec", "cartography"],
        tier=2,
        args_model=PreviewArgs,
        description=(
            "零突变预览一个图层的**目标比例尺出版综合计划**：消除/拓扑简化/"
            "网络取舍桥接/点聚合管线、综合前后要素数、独立 QA 审计行"
            "（几何/无缝/连通/守恒/最小尺寸/比例尺一致性）。不存 ref、不改 "
            "MapSpec。"
            "\n何时用：commit 前核对『1:50万会综合成什么样、丢多少要素、"
            "QA 是否全过』。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="heavy",
        scale_class="large",
        tags=("制图综合", "预览", "零突变", "paper scale"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "target_not_found", "no_mapspec"),
    )
    async def webgis_preview_paper_scale_generalization(
        layer: str,
        denominator: Optional[int] = None,
        denominator_text: Optional[str] = None,
        page_text: Optional[str] = None,
        purpose: str = "reference",
        dpi: Optional[float] = None,
        kept_values: Optional[List[Any]] = None,
        sum_fields: Optional[List[str]] = None,
        keep_feature_ids: Optional[List[str]] = None,
        keep_values: Optional[List[Any]] = None,
        keep_values_field: Optional[str] = None,
        keep_region: Optional[List[float]] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.paper_generalization import (
            PaperScaleError,
            preview_paper_scale_generalization,
        )
        kwargs = _args_to_kwargs(dict(
            denominator=denominator, denominator_text=denominator_text,
            page_text=page_text, purpose=purpose, dpi=dpi,
            kept_values=kept_values, sum_fields=sum_fields,
            keep_feature_ids=keep_feature_ids, keep_values=keep_values,
            keep_values_field=keep_values_field, keep_region=keep_region))
        try:
            return await preview_paper_scale_generalization(
                session_id, layer, **kwargs)
        except PaperScaleError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[preview_paper_scale] failed", exc_info=True)
            return {"error": f"preview failed: {exc}"}

    @tool(
        registry,
        name="webgis_apply_paper_scale_generalization",
        capabilities=["cartography_paper_scale_apply"],
        domains=["mapspec", "cartography"],
        tier=2,
        args_model=ApplyArgs,
        description=(
            "为一个图层执行并冻结**目标比例尺出版综合**（paper_scale.v1）："
            "真实几何综合管线（小面积消除→共享边无缝简化 / 等级取舍+连通性"
            "桥接 / 点聚合守恒）+ 独立 QA 门（fail 拒绝上版），工件存**新 "
            "ref**（原始数据与统计口径永不变），MapSpec 顺序 CAS 提交"
            "（paper_plans 冻结计划 + 表达窗口裁剪插入 —— 该 zoom 跨度内"
            "纸面工件优先）。"
            "\n何时用：用户要『1:50万 总览图，保留县城与主干道』『A4 横版"
            "出版图』这类**固定比例尺交付**；改比例尺/页面后重跑即冻结新"
            "计划（旧计划保留可回看）。"
            "\n何时不用：屏幕缩放带表达用 webgis_apply_scale_representation；"
            "只看计划用 mode=preview。"
            "\n关键语义：QA blocking>0 → 拒绝冻结（fail-closed）；commit "
            "幂等（同 operation_id 重放返回存证）；原始 ref/统计/图例不动。"
        ),
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="heavy",
        scale_class="large",
        tags=("制图综合", "出版", "受控提交", "paper scale",
              "generalization"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        required_context=("map_state",),
        map_mutations=("style_layer",),
        data_mutations=("session_state",),
        failure_modes=("invalid_args", "target_not_found", "superseded",
                       "no_mapspec", "qa_blocked"),
    )
    async def webgis_apply_paper_scale_generalization(
        layer: str,
        mode: str = "preview",
        denominator: Optional[int] = None,
        denominator_text: Optional[str] = None,
        page_text: Optional[str] = None,
        purpose: str = "reference",
        dpi: Optional[float] = None,
        kept_values: Optional[List[Any]] = None,
        sum_fields: Optional[List[str]] = None,
        keep_feature_ids: Optional[List[str]] = None,
        keep_values: Optional[List[Any]] = None,
        keep_values_field: Optional[str] = None,
        keep_region: Optional[List[float]] = None,
        operation_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
        note: Optional[str] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.paper_generalization import (
            PaperScaleError,
            apply_paper_scale_generalization,
        )
        kwargs = _args_to_kwargs(dict(
            denominator=denominator, denominator_text=denominator_text,
            page_text=page_text, purpose=purpose, dpi=dpi,
            kept_values=kept_values, sum_fields=sum_fields,
            keep_feature_ids=keep_feature_ids, keep_values=keep_values,
            keep_values_field=keep_values_field, keep_region=keep_region))
        try:
            receipt = await apply_paper_scale_generalization(
                session_id, layer, mode=mode, operation_id=operation_id,
                expected_revision=expected_revision, note=note, **kwargs)
        except PaperScaleError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[apply_paper_scale] failed", exc_info=True)
            return {"error": f"apply failed: {exc}"}
        result = dict(receipt)
        result["success"] = receipt.get("status") in ("preview", "committed")
        if not result["success"]:
            result["error"] = receipt.get("error") or str(receipt.get("status"))
        return result
