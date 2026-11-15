"""G41 双变量/可靠性作者工具（WP09/WP11）——inspect / preview / apply。

与 UI 面板共用 ``bivariate_reliability_authoring.service``、同一 typed 计划、
同一强版本冻结、同一单笔 CAS 提交语义。工具说明明确「何时需要两有效数值
字段 / 真实误差事实」；``session_id`` 由 ToolRegistry 从已授权上下文注入，
model-origin 不能借工具跳过所有权或用户锁。

回执**有界**：只返回统计口径、类表/格子（≤9）、桶（≤10）、披露与合法 refs，
绝不回传全量 feature、栅格数组、媒体帧或密钥。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

_MAX_CELLS = 9
_MAX_BUCKETS = 10
_MAX_FIELDS = 40
_MAX_DISCLOSURES = 12


class InspectArgs(BaseModel):
    layer_id: str = Field(..., min_length=1, max_length=200,
                          description="目标矢量层 id（必须是会话 ref 源）")


class PlanArgs(BaseModel):
    layer_id: str = Field(..., min_length=1, max_length=200)
    plan: Dict[str, Any] = Field(
        ...,
        description=(
            "typed 作者计划 schema_version=1；mode ∈ bivariate_matrix | "
            "reliability_dual_channel | revert。"
            "bivariate_matrix 需要两个**不同**数值字段的 axis_a/axis_b、"
            "n=2|3、每轴 method(quantiles|equal_interval|manual)、"
            "boundary_rule、可选 matrix_id 或 n*n 个 #rrggbb colors。"
            "reliability_dual_channel 需要 estimate_field + measure "
            "(standard_error|variance|confidence_interval|prediction_interval) "
            "+ visual 有界映射；只有估计值/sample_count/p 值不能建立可靠性表达"
        ))


class ApplyArgs(PlanArgs):
    expected_revision: int = Field(
        ..., ge=0, description="提交时的权威 MapSpec 修订（CAS）")
    preview_digest: str = Field(
        ..., min_length=1, max_length=128,
        description="最近一次 preview 返回的 candidate_digest；"
                    "任何参数/源代次变化都会使旧预览失效")


def register_bivariate_reliability_tools(registry: ToolRegistry) -> None:
    """注册 G41 三工具（只读×1 + 预览×1 + 受控写×1）。"""
    from app.services.bivariate_reliability_authoring import (
        commit_expression,
        error_payload,
        inspect_layer,
        parse_plan,
        preview_expression,
        AuthoringError,
    )

    async def _guard(coro):
        try:
            return await coro
        except AuthoringError as exc:
            payload = error_payload(exc)
            payload["success"] = False
            return payload
        except Exception:  # noqa: BLE001 - 工具错误契约
            logger.warning("[bivariate_reliability] tool failed", exc_info=True)
            return {
                "success": False, "code": "G41_TOOL_FAILED",
                "message": "bivariate/reliability authoring operation failed",
                "correction_hint": "Check layer id, plan shape, source anchor, "
                                   "preview_digest and MapSpec revision.",
            }

    def _bounded(result: Dict[str, Any]) -> Dict[str, Any]:
        legend = result.get("legend") or {}
        out: Dict[str, Any] = {
            "success": True,
            "layer_id": result.get("layer_id"),
            "mode": result.get("mode"),
            "committed": result.get("committed", False),
            "algorithm_version": result.get("algorithm_version"),
            "source_anchor": result.get("source_anchor"),
            "plan_digest": result.get("plan_digest"),
            "candidate_digest": result.get("candidate_digest"),
            "candidate_fingerprint": result.get("candidate_fingerprint"),
            "stats_basis": result.get("stats_basis"),
            "summary": result.get("summary"),
            "disclosures": (result.get("disclosures") or [])[:_MAX_DISCLOSURES],
            "derived": result.get("derived"),
            "legend": {
                "type": legend.get("type"),
                "title": legend.get("title"),
                "n": legend.get("n"),
                "colors": (legend.get("colors") or [])[:_MAX_CELLS],
                "cells": (legend.get("cells") or [])[:_MAX_CELLS],
                "axes": legend.get("axes"),
                "measure": legend.get("measure"),
                "channel": legend.get("channel"),
                "buckets": (legend.get("buckets") or [])[:_MAX_BUCKETS],
                "estimate": legend.get("estimate"),
                "counts": legend.get("counts"),
                "missing": legend.get("missing"),
                "source": legend.get("source"),
                "approximate": legend.get("approximate"),
            },
        }
        if result.get("committed"):
            out.update({
                "revision": result.get("revision"),
                "mapspec_fingerprint": result.get("mapspec_fingerprint"),
                "can_undo": result.get("can_undo"),
                "mutations": result.get("mutations"),
                "reconcile_via": "mapspec_event",
            })
        else:
            out["current_fingerprint"] = result.get("current_fingerprint")
            out["preview_only"] = True
        return out

    @tool(
        registry,
        name="webgis_bivariate_reliability_inspect",
        capabilities=["thematic_cartography", "layer_browse"],
        tier=2,
        domains=["cartography", "mapspec"],
        args_model=InspectArgs,
        description=(
            "只读检查矢量层能否进入双变量/可靠性作者工作台：强版本源锚（ref + "
            "单调 content_revision + 内容指纹）、有界字段画像与显式分型计数、"
            "当前表达指纹/证据、支持的图层与视觉通道、共享表达组资格。"
            "\n何时用：编辑表达之前；用户问『这两个字段的分布如何』『这层能做"
            "双变量吗』『有没有误差字段』。\n何时不用：只想新建普通专题层用 "
            "create_thematic_map。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        scale_class="large",
        tags=("双变量", "可靠性", "不确定度", "作者工作台", "bivariate reliability"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "missing_data", "permission_denied"),
    )
    async def webgis_bivariate_reliability_inspect(
        layer_id: str, session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        result = await _guard(inspect_layer(
            session_id, layer_id, trusted_dispatch=True))
        if result.get("success") is False:
            return result
        return {
            "success": True,
            "layer_id": layer_id,
            "layer_type": result.get("layer_type"),
            "source": result.get("source"),
            "source_anchor": result.get("source_anchor"),
            "revision": result.get("revision"),
            "current": result.get("current"),
            "fields": (result.get("fields") or [])[:_MAX_FIELDS],
            "fields_truncated": result.get("fields_truncated"),
            "supported": result.get("supported"),
            "measure_evidence_contract": result.get("measure_evidence_contract"),
            "shared_expression_scope": result.get("shared_expression_scope"),
            "notes": result.get("notes"),
        }

    @tool(
        registry,
        name="webgis_bivariate_reliability_preview",
        capabilities=["thematic_cartography"],
        tier=2,
        domains=["cartography", "mapspec"],
        args_model=PlanArgs,
        description=(
            "预览成对/可靠性作者计划（零 mutation）：强版本冻结全源 → 成对统计"
            "（有效对/仅A缺失/仅B缺失/双缺失/分型）→ 两轴独立分级与联合格子计数"
            "（或估计主色 + 有来源误差的第二通道桶）→ canonical 图例与有界回执。"
            "返回 candidate_digest，提交时必须原样带回。"
            "\n何时用：改轴/断点/矩阵/交换轴/误差类型/映射之前先看效果与拒绝理由。"
            "\n何时不用：确认无误要落图用 webgis_bivariate_reliability_apply。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        tags=("双变量", "预览", "矩阵", "不确定度", "bivariate reliability"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "validation_error", "missing_data", "stale_source"),
    )
    async def webgis_bivariate_reliability_preview(
        layer_id: str, plan: Dict[str, Any], session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        try:
            parsed = parse_plan({**(plan or {}), "layer_id": layer_id})
        except AuthoringError as exc:
            payload = error_payload(exc)
            payload["success"] = False
            return payload
        result = await _guard(preview_expression(
            session_id, layer_id, parsed, trusted_dispatch=True))
        if result.get("success") is False:
            return result
        return _bounded(result)

    @tool(
        registry,
        name="webgis_bivariate_reliability_apply",
        capabilities=["thematic_cartography"],
        tier=2,
        domains=["cartography", "mapspec"],
        args_model=ApplyArgs,
        description=(
            "把成对/可靠性计划**单笔**提交到权威 MapSpec（与 UI 同一领域服务、"
            "同一 CAS/checkpoint/undo）：重新冻结同代源 → 核对计划锚与 "
            "preview_digest → 生成冻结展示副本（不写回源）→ layer + source "
            "一次原子提交。expected_revision 不匹配返回 superseded；"
            "preview_digest 过期返回 stale_preview。"
            "\n何时用：用户明确要应用双变量矩阵或『估计值 + 误差/区间』双通道图。"
            "\n何时不用：只有估计值而没有任何误差/区间事实（应改走 "
            "bivariate_matrix 或先补证据）；探索性调整先 preview。"
        ),
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        tags=("双变量", "提交", "MapSpec", "可靠性", "bivariate reliability"),
        output_semantic_type="json",
        result_size_policy="bounded",
        required_context=("map_state",),
        map_mutations=("style_layer",),
        failure_modes=("invalid_args", "superseded", "stale_preview",
                       "validation_error", "missing_data"),
    )
    async def webgis_bivariate_reliability_apply(
        layer_id: str, plan: Dict[str, Any], expected_revision: int,
        preview_digest: str, session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        try:
            parsed = parse_plan({**(plan or {}), "layer_id": layer_id})
        except AuthoringError as exc:
            payload = error_payload(exc)
            payload["success"] = False
            return payload
        result = await _guard(commit_expression(
            session_id, layer_id, parsed,
            expected_revision=int(expected_revision),
            preview_digest=preview_digest,
            origin="agent", actor="pi:bivariate_reliability_apply",
            trusted_dispatch=True,
            mutation_id=f"g41:{parsed.plan_digest()}:r{int(expected_revision)}:{preview_digest[:12]}",
        ))
        if result.get("success") is False:
            return result
        return _bounded(result)
