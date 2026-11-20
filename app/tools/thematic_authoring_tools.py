"""G33 专题表达作者工具（WP09）——inspect / preview / apply 三件套。

与 UI 面板同一领域服务（thematic_authoring.service）、同一预览构建器、
同一单笔 CAS 提交语义。工具回执**有界**：不放完整 MapSpec / 全要素 /
全量类表进上下文，只返回统计摘要、类表（≤12 类 / 类别 top 截断）与
授权 refs。widget 自由文本转述的断点必须经同一服务校验——不直接写 paint。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

_MAX_TOOL_CLASSES = 12
_MAX_TOOL_FIELDS = 40
_MAX_TOOL_TOP_CATEGORIES = 20


class InspectArgs(BaseModel):
    layer_id: str = Field(..., min_length=1, max_length=200, description="目标矢量专题层 id")
    field: Optional[str] = Field(
        None, max_length=128,
        description="要查看分布的字段；缺省 = 层当前专题字段")


class PlanArgs(BaseModel):
    layer_id: str = Field(..., min_length=1, max_length=200)
    plan: Dict[str, Any] = Field(
        ...,
        description=(
            "typed 作者计划（schema_version=1）：mode ∈ graduated_auto|"
            "graduated_manual|categorical|continuous|divergent|revert_auto；"
            "断点 breaks 必须严格递增（服务端不自动排序）；类别表 categories "
            "[{key,color,label}] 顺序即呈现顺序，key=源值不改数据"
        ))


class ApplyArgs(PlanArgs):
    expected_revision: int = Field(
        ..., ge=0,
        description="提交时的 MapSpec 修订（CAS）；不匹配返回 superseded，不盲目重试")


def register_thematic_authoring_tools(registry: ToolRegistry) -> None:
    """注册 G33 三工具（只读×1 + 预览×1 + 受控写×1）。"""
    from app.services.thematic_authoring import (
        commit_expression,
        inspect_layer,
        preview_expression,
    )
    from app.services.thematic_authoring.contract import parse_plan
    from app.services.thematic_authoring.errors import (
        ThematicAuthoringError,
        error_payload,
    )

    async def _guard(coro):
        try:
            return await coro
        except ThematicAuthoringError as exc:
            payload = error_payload(exc)
            payload["success"] = False
            return payload
        except Exception:  # noqa: BLE001 - 工具错误契约
            logger.warning("[thematic_authoring] tool failed", exc_info=True)
            return {
                "success": False, "code": "THEMATIC_TOOL_FAILED",
                "message": "thematic authoring operation failed",
                "correction_hint": "Check the layer id, plan shape and MapSpec revision.",
            }

    def _bounded_receipt(result: Dict[str, Any]) -> Dict[str, Any]:
        """有界投影：类表/字段/类别 top 截断，源锚与统计口径保留。"""
        out: Dict[str, Any] = {
            "success": True,
            "layer_id": result.get("layer_id"),
            "field": result.get("field"),
            "mode": result.get("mode"),
            "committed": result.get("committed", False),
            "source_anchor": result.get("source_anchor"),
            "stats_basis": result.get("stats_basis"),
            "requested_k": result.get("requested_k"),
            "actual_k": result.get("actual_k"),
            "disclosures": (result.get("disclosures") or [])[:12],
        }
        if result.get("committed"):
            out["revision"] = result.get("revision")
            out["candidate_fingerprint"] = result.get("candidate_fingerprint")
            out["can_undo"] = result.get("can_undo")
            out["mutations"] = result.get("mutations")
            out["reconcile_via"] = "mapspec_event"
        else:
            out["current_fingerprint"] = result.get("current_fingerprint")
            out["candidate_fingerprint"] = result.get("candidate_fingerprint")
        table = result.get("class_table") or []
        out["class_count"] = len(table)
        out["class_table"] = table[:_MAX_TOOL_CLASSES]
        out["class_table_truncated"] = len(table) > _MAX_TOOL_CLASSES
        return out

    @tool(
        registry,
        name="webgis_thematic_style_inspect",
        capabilities=["thematic_cartography", "layer_browse"],
        tier=2,
        domains=["cartography", "mapspec"],
        args_model=InspectArgs,
        description=(
            "只读查看**已有**矢量专题层的当前表达与字段真实分布：当前 legend_spec"
            "（方法/断点/类表/指纹）、同版本统计（总数/有效/无效分型/直方图/类别"
            "计数）、字段清单与源修订。统计总体=提交源全量，绝不从视窗推断。"
            "\n何时用：用户问『这个层怎么分级的』『分布什么样』『有哪些字段可换』，"
            "或编辑表达之前。\n何时不用：新建专题层用 create_thematic_map。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        scale_class="large",
        tags=("专题图", "分类", "分布", "图例", "thematic authoring"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "missing_data", "permission_denied"),
    )
    async def webgis_thematic_style_inspect(
        layer_id: str, field: Optional[str] = None, session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        result = await _guard(inspect_layer(
            session_id, layer_id, field=field, trusted_dispatch=True))
        if not result.get("layer_id") and not result.get("success"):
            return result
        current = result.get("current") or {}
        legend = current.get("legend_spec") or {}
        dist = result.get("distribution") or {}
        cat = (dist.get("categorical") or {})
        return {
            "success": True,
            "layer_id": layer_id,
            "layer_type": result.get("layer_type"),
            "source": result.get("source"),
            "revision": result.get("revision"),
            "current": {
                "type": legend.get("type"),
                "field": legend.get("field"),
                "method": legend.get("method"),
                "k": legend.get("k"),
                "breaks": (legend.get("breaks") or [])[:_MAX_TOOL_CLASSES + 1],
                "palette": legend.get("palette") or legend.get("palette_id"),
                "fingerprint": current.get("fingerprint"),
                "authoring": current.get("authoring"),
            },
            "fields": (result.get("fields") or [])[:_MAX_TOOL_FIELDS],
            "fields_truncated": len(result.get("fields") or []) > _MAX_TOOL_FIELDS,
            "distribution": {
                "basis": dist.get("basis"),
                "numeric": dist.get("numeric", {}),
                "top_categories": (cat.get("top") or [])[:_MAX_TOOL_TOP_CATEGORIES],
                "other_count": cat.get("other_count"),
                "distinct": cat.get("distinct"),
                "ambiguous_keys": cat.get("ambiguous_keys"),
            },
            "shared_expression_scope": result.get("shared_expression_scope"),
            "notes": result.get("notes"),
        }

    @tool(
        registry,
        name="webgis_thematic_style_preview",
        capabilities=["thematic_cartography"],
        tier=2,
        domains=["cartography", "mapspec"],
        args_model=PlanArgs,
        description=(
            "预览作者计划（零 mutation）：typed 计划 → 候选 legend_spec/paint/类计数"
            "表与披露（requested vs actual 类数、越界、无效值口径），不写 MapSpec。"
            "\n何时用：改断点/换字段/换色带/改类表**之前**先看效果与拒绝理由。"
            "\n何时不用：确认无误要落图用 webgis_thematic_style_apply。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        scale_class="large",
        tags=("专题图", "预览", "断点", "分类", "thematic authoring"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "validation_error", "missing_data"),
    )
    async def webgis_thematic_style_preview(
        layer_id: str, plan: Dict[str, Any], session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        try:
            parsed = parse_plan({**(plan or {}), "layer_id": layer_id})
        except ThematicAuthoringError as exc:
            payload = error_payload(exc)
            payload["success"] = False
            return payload
        result = await _guard(preview_expression(
            session_id, layer_id, parsed, trusted_dispatch=True))
        if result.get("success") is False:
            return result
        return _bounded_receipt(result)

    @tool(
        registry,
        name="webgis_thematic_style_apply",
        capabilities=["thematic_cartography"],
        tier=2,
        domains=["cartography", "mapspec"],
        args_model=ApplyArgs,
        description=(
            "把作者计划**单笔**提交到权威 MapSpec（与 UI 面板同一领域服务、同一"
            "CAS/checkpoint/undo 语义）：断点/类表/色带/发散中心/比例符号或线宽"
            "映射一次生效，地图/图例/导出同源更新；一次 undo 恢复完整表达。"
            "回持有界；expected_revision 不匹配返回 superseded，须重新 inspect。"
            "\n何时用：用户明确要改现有专题层的表达（『改成 0/10/20/50 四段』"
            "『把类别改色/改名/排序』『恢复自动分级』）。"
            "\n何时不用：探索性调整先 preview；新建层用 create_thematic_map。"
        ),
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        tags=("专题图", "提交", "MapSpec", "断点", "thematic authoring"),
        output_semantic_type="json",
        result_size_policy="bounded",
        required_context=("map_state",),
        map_mutations=("style_layer",),
        failure_modes=("invalid_args", "superseded", "validation_error", "missing_data"),
    )
    async def webgis_thematic_style_apply(
        layer_id: str, plan: Dict[str, Any], expected_revision: int,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        try:
            parsed = parse_plan({**(plan or {}), "layer_id": layer_id})
        except ThematicAuthoringError as exc:
            payload = error_payload(exc)
            payload["success"] = False
            return payload
        # P3-3（评审）：确定性 mutation_id = 计划指纹 × 目标修订 —— 同一
        # apply 超时重放由引擎幂等面去重（lifecycle mutation_id 幂等）。
        result = await _guard(commit_expression(
            session_id, layer_id, parsed,
            expected_revision=expected_revision,
            origin="agent", actor="pi:thematic_style_apply",
            trusted_dispatch=True,
            mutation_id=f"thematic:{parsed.plan_digest()}:r{int(expected_revision)}",
        ))
        if result.get("success") is False:
            return result
        return _bounded_receipt(result)
