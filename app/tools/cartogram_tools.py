"""G47 Dorling cartogram Pi 工具（WP11）——inspect / plan / run / status / apply / export。

与 UI 面板共用 ``cartogram_authoring.service``：同一 typed 计划、同一强版本冻结、
同一 durable job、同一独立质量门、同一 CAS 应用。``session_id`` 由 ToolRegistry
从已授权上下文注入，model-origin 不能借工具跳过所有权或用户锁。

回执**有界**：只返回统计口径、面积/重叠/位移/邻接摘要、少量样例圆与合法 refs；
绝不回传全量圆集合、参考轮廓点、媒体字节或密钥。导出工具只回传字节数与 sha256
以及下载路径（真正的字节由受权 HTTP 下载给出）。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

_MAX_CIRCLES = 8
_MAX_DISCLOSURES = 12


class InspectArgs(BaseModel):
    layer_id: str = Field(..., min_length=1, max_length=200,
                          description="目标矢量层 id（必须是会话 ref 源）")


class PlanArgs(BaseModel):
    layer_id: str = Field(..., min_length=1, max_length=200)
    plan: Dict[str, Any] = Field(
        ...,
        description=(
            "typed 计划 schema_version=1，mode=dorling_circle。必填 quantity:"
            "{field, unit, meaning∈count|amount, aggregation=sum}；可选 source_crs"
            "（默认 EPSG:4326）、projection.crs（默认 EPSG:3857）、scope、"
            "area_scale、solver、quality_gate、budgets。率/密度/混合单位会被拒绝；"
            "零/缺失默认 list_only（保留身份、不画圆）。"))


class RunArgs(PlanArgs):
    source_anchor: Dict[str, Any] = Field(
        default_factory=dict,
        description="最近一次 plan 返回的 source_anchor；源代次变化会让旧计划失效")


class ApplyArgs(BaseModel):
    layer_id: str = Field(..., min_length=1, max_length=200)
    # ref_cursor：布局 ref 是**游标**不是数据别名。不声明时 registry 的透明
    # 解引用会把 `ref:g47layout-...` 就地替换成布局载荷（dict），
    # `layout_ref: str` 随即校验失败 —— Pi/SSE 链上 apply 永远不可能成功。
    layout_ref: str = Field(..., min_length=5, max_length=200,
                            json_schema_extra={"ref_cursor": True})
    expected_revision: int = Field(..., ge=0, description="权威 MapSpec 修订（CAS）")
    allow_diagnostic: bool = Field(
        default=False,
        description="true 时允许以诊断草稿身份应用未达质量门的布局")
    acknowledge_stale_source: bool = Field(
        default=False, description="true 时允许应用锚定旧源代次的布局（标记 stale）")


class StatusArgs(BaseModel):
    job_id: int = Field(..., ge=1)


class ExportArgs(BaseModel):
    # ref_cursor：同 ApplyArgs —— 布局 ref 必须按游标直传，否则被透明解引用。
    layout_ref: str = Field(..., min_length=5, max_length=200,
                            json_schema_extra={"ref_cursor": True})
    format: str = Field(default="svg", pattern="^(svg|png|pdf)$")
    dpi: int = Field(default=96, ge=48, le=600)


def register_cartogram_tools(registry: ToolRegistry) -> None:
    """注册 G47 六工具（只读×1 + 预检×1 + 作业×2 + 受控写×1 + 导出×1）。"""
    from app.services.cartogram_authoring import (
        AuthoringError,
        apply_layout,
        error_payload,
        export_layout,
        inspect_layer,
        layout_status,
        parse_plan,
        plan_layout,
        start_layout,
    )

    async def _guard(coro):
        try:
            return await coro
        except AuthoringError as exc:
            payload = error_payload(exc)
            payload["success"] = False
            return payload
        except Exception:  # noqa: BLE001 - 工具错误契约
            logger.warning("[g47] cartogram tool failed", exc_info=True)
            return {
                "success": False, "code": "G47_TOOL_FAILED",
                "message": "dorling cartogram operation failed",
                "correction_hint": "Check layer id, plan shape, source anchor, "
                                   "layout_ref and MapSpec revision.",
            }

    def _bounded_plan(result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "success": True,
            "layer_id": result.get("layer_id"),
            "mode": result.get("mode"),
            "algorithm": result.get("algorithm"),
            "source_anchor": result.get("source_anchor"),
            "plan_digest": result.get("plan_digest"),
            "candidate_digest": result.get("candidate_digest"),
            "quantity": result.get("quantity"),
            "units_for_layout": result.get("units_for_layout"),
            "geometry": result.get("geometry"),
            "area_scale": result.get("area_scale"),
            "examples": (result.get("examples") or [])[:_MAX_CIRCLES],
            "solver": result.get("solver"),
            "quality_gate": result.get("quality_gate"),
            "budgets": result.get("budgets"),
            "disclosures": (result.get("disclosures") or [])[:_MAX_DISCLOSURES],
            "revision": result.get("revision"),
            "preview_only": True,
        }

    def _bounded_layout(result: Dict[str, Any]) -> Dict[str, Any]:
        out = {
            "success": True,
            "layout_ref": result.get("layout_ref"),
            "status": result.get("status"),
            "publication_ready": result.get("publication_ready"),
            "coordinate_space": result.get("coordinate_space"),
            "units": result.get("units"),
            "is_geographic": False,
            "candidate_digest": result.get("candidate_digest"),
            "plan_digest": result.get("plan_digest"),
            "quantity": result.get("quantity"),
            "area_scale": result.get("area_scale"),
            "metrics": result.get("metrics"),
            "circle_count": result.get("circle_count"),
            "circles_sample": (result.get("circles_sample") or [])[:_MAX_CIRCLES],
            "zero_count": result.get("zero_count"),
            "missing_count": result.get("missing_count"),
            "excluded_count": result.get("excluded_count"),
            "disclosures": (result.get("disclosures") or [])[:_MAX_DISCLOSURES],
        }
        return {k: v for k, v in out.items() if v is not None}

    @tool(
        registry,
        name="webgis_dorling_cartogram_inspect",
        capabilities=["thematic_cartography", "layer_browse"],
        tier=2,
        domains=["cartography", "mapspec"],
        args_model=InspectArgs,
        description=(
            "只读检查一个矢量层能否进入 Dorling 圆面积 cartogram 工作台：强版本源锚"
            "（ref + 单调 content_revision + 内容指纹）、有界字段画像（正值/零/缺失/"
            "非法分型）、几何类型与 CRS 线索、当前 cartogram 证据、支持矩阵。"
            "\n何时用：用户问『这层能做 cartogram 吗』『哪个字段可做面积圆』。"
            "\n何时不用：原位比例圆用 create_thematic_map；泛 cartogram（扩散/矩形）"
            "本系统仍未实现。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        scale_class="large",
        tags=("cartogram", "dorling", "面积", "作者工作台"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "missing_data", "permission_denied"),
    )
    async def webgis_dorling_cartogram_inspect(
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
            "geometry_types": result.get("geometry_types"),
            "fields": (result.get("fields") or [])[:40],
            "fields_truncated": result.get("fields_truncated"),
            "current": result.get("current"),
            "supported": result.get("supported"),
            "contract": result.get("contract"),
            "revision": result.get("revision"),
            "algorithm_version": result.get("algorithm_version"),
        }

    @tool(
        registry,
        name="webgis_dorling_cartogram_plan",
        capabilities=["thematic_cartography"],
        tier=2,
        domains=["cartography", "mapspec"],
        args_model=PlanArgs,
        description=(
            "预检 Dorling 计划（零 mutation、零求解）：数量语义（必须声明单位与"
            "count/amount）、零/缺失/非法值分型、身份与几何资格、CRS/轴序、原邻接"
            "边数、资源上界、共同面积系数与参考圆、预计圆数。返回 candidate_digest "
            "——run 时源代次变化会让它失效。"
            "\n何时用：确认字段/单位/范围/投影是否可行，先看拒绝理由。"
            "\n何时不用：已经确认要出图，直接 run。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        tags=("cartogram", "dorling", "计划", "预检"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "validation_error", "missing_data", "stale_source"),
    )
    async def webgis_dorling_cartogram_plan(
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
        result = await _guard(plan_layout(
            session_id, layer_id, parsed, trusted_dispatch=True))
        if result.get("success") is False:
            return result
        return _bounded_plan(result)

    @tool(
        registry,
        name="webgis_dorling_cartogram_run",
        capabilities=["thematic_cartography"],
        tier=2,
        domains=["cartography", "mapspec"],
        args_model=RunArgs,
        description=(
            "提交 Dorling 布局求解（进程内 durable job）：重新冻结同代源 → 资格复核 →"
            "有界迭代求解 → 独立质量核验 → 持久化布局 ref。幂等键 = 计划 + 源代次 + "
            "算法版本；返回 job_id，用 webgis_dorling_cartogram_status 轮询。"
            "作业完成 ≠ 已应用 ≠ publication-ready。"
            "\n何时用：用户确认要做面积 cartogram 图。"
            "\n何时不用：只想看能不能做（用 plan）。"
        ),
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        tags=("cartogram", "dorling", "求解", "作业"),
        output_semantic_type="json",
        result_size_policy="bounded",
        required_context=("map_state",),
        failure_modes=("invalid_args", "validation_error", "budget_exceeded",
                       "stale_source"),
    )
    async def webgis_dorling_cartogram_run(
        layer_id: str, plan: Dict[str, Any], source_anchor: Dict[str, Any] | None = None,
        session_id: str = "",
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
        if source_anchor:
            parsed = parsed.model_copy(update={"source_anchor": source_anchor})
        result = await _guard(start_layout(
            session_id, layer_id, parsed, trusted_dispatch=True))
        if result.get("success") is False:
            return result
        return {
            "success": True,
            "job_id": result.get("job_id"),
            "status": result.get("status"),
            "idempotent_reuse": result.get("idempotent_reuse"),
            "source_anchor": result.get("source_anchor"),
            "plan_digest": result.get("plan_digest"),
            "candidate_digest": result.get("candidate_digest"),
            "poll": result.get("poll"),
            "note": result.get("note"),
        }

    @tool(
        registry,
        name="webgis_dorling_cartogram_status",
        capabilities=["thematic_cartography"],
        tier=2,
        domains=["cartography", "mapspec"],
        args_model=StatusArgs,
        description=(
            "读取本会话 cartogram 作业的真实状态（queued/running/cancelling/"
            "completed/failed/cancelled/stale）与有界结果摘要（面积误差、重叠、"
            "邻接保留、layout_ref）。只看本会话的 job。"
            "\n何时用：run 之后轮询；用户问『算好了吗』。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("cartogram", "状态", "作业"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "not_found"),
    )
    async def webgis_dorling_cartogram_status(
        job_id: int, session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        result = await _guard(layout_status(session_id, int(job_id)))
        if result.get("success") is False:
            return result
        return {"success": True, **result}

    @tool(
        registry,
        name="webgis_dorling_cartogram_apply",
        capabilities=["thematic_cartography"],
        tier=2,
        domains=["cartography", "mapspec"],
        args_model=ApplyArgs,
        description=(
            "把已完成的布局作为**参考成果**应用到层（与 UI 同一 CAS/checkpoint/undo）："
            "重新校验布局属于本会话、锚定同代源、达到质量门；单笔 UpsertLayerIntent "
            "写 cartogram_authoring 证据块。原几何/字段/样式/用户锁都不改。"
            "未达质量门需 allow_diagnostic=true；源已漂移需 acknowledge_stale_source=true。"
            "\n何时用：用户看过图面与指标后确认『就用这个』。"
        ),
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="fast",
        memory_class="light",
        scale_class="medium",
        tags=("cartogram", "应用", "MapSpec"),
        output_semantic_type="json",
        result_size_policy="bounded",
        required_context=("map_state",),
        map_mutations=("style_layer",),
        failure_modes=("invalid_args", "quality_gate_failed", "stale_source",
                       "superseded"),
    )
    async def webgis_dorling_cartogram_apply(
        layer_id: str, layout_ref: str, expected_revision: int,
        allow_diagnostic: bool = False, acknowledge_stale_source: bool = False,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        result = await _guard(apply_layout(
            session_id, layer_id, layout_ref=layout_ref,
            expected_revision=int(expected_revision),
            allow_diagnostic=bool(allow_diagnostic),
            acknowledge_stale_source=bool(acknowledge_stale_source),
            origin="agent", actor="pi:dorling_cartogram_apply",
            mutation_id=f"g47:{layout_ref}:r{int(expected_revision)}",
        ))
        if result.get("success") is False:
            return result
        return {
            "success": True,
            "layer_id": result.get("layer_id"),
            "layout_ref": result.get("layout_ref"),
            "committed": result.get("committed"),
            "publication_ready": result.get("publication_ready"),
            "diagnostic": result.get("diagnostic"),
            "stale_source": result.get("stale_source"),
            "revision": result.get("revision"),
            "mapspec_fingerprint": result.get("mapspec_fingerprint"),
            "can_undo": result.get("can_undo"),
            "mutations": result.get("mutations"),
            "coordinate_space": result.get("coordinate_space"),
            "disclosures": (result.get("disclosures") or [])[:_MAX_DISCLOSURES],
        }

    @tool(
        registry,
        name="webgis_dorling_cartogram_export",
        capabilities=["thematic_cartography", "map_export_publishing"],
        tier=2,
        domains=["cartography", "publication"],
        args_model=ExportArgs,
        description=(
            "从**冻结布局**生成并登记一份真实导出（svg/png/pdf），回传字节数与 "
            "sha256 以及受权下载路径。PNG 由同一 SVG 按 DPI 栅格化，PDF 按 mm 排版，"
            "三者圆面积比例一致；SVG 保留真实圆与 data-feature-id 可核身份。"
            "\n何时用：用户要下载/交付 cartogram 图。"
            "\n何时不用：想看指标用 status/layout。"
        ),
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="slow",
        memory_class="medium",
        scale_class="medium",
        tags=("cartogram", "导出", "svg", "png", "pdf"),
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "not_found", "export_unavailable"),
    )
    async def webgis_dorling_cartogram_export(
        layout_ref: str, format: str = "svg", dpi: int = 96, session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        result = await _guard(export_layout(
            session_id, layout_ref, format, dpi=int(dpi)))
        if isinstance(result, dict):
            return result
        data, media, filename = result
        return {
            "success": True,
            "layout_ref": layout_ref,
            "format": format,
            "dpi": int(dpi),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "media_type": media,
            "filename": filename,
            "download_path": (f"/api/v1/cartogram/sessions/{session_id}/layouts/"
                              f"{layout_ref}/export?format={format}&dpi={int(dpi)}"),
        }
