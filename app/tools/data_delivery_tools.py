"""Agent 数据交付工具面（G18 WP09）：查看格式限制→预览→提交→查收据。

信任边界：
- capabilities/preview 为只读（无 state mutation）；
- submit 是 state_mutation（磁盘产物 + 台账登记）—— 服务端强制预览复检
  与损失确认，工具不旁路（无权限或损失未确认时直接 typed 拒绝）；
- get 为只读查询（有界回执投影 + 授权下载引用，不回服务器文件路径）。

参数纪律：只含有界引用与选择（spec 结构同 REST 契约），大 payload 永远
经 ref 表达。
"""
import logging
from typing import Any, Dict, Optional

from app.schemas.data_delivery_schema import DeliverySpec
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _actor() -> "Optional[Any]":
    """从 JobOrigin 上下文取调用方身份（owner_id/owner_token/org）。"""
    from app.services.data_delivery.source_resolver import DeliveryActor
    from app.services.jobs.context import current_origin

    origin = current_origin()
    if origin is None:
        return DeliveryActor()
    return DeliveryActor(
        user_id=origin.owner_id, org_id=origin.org_id,
        owner_token=origin.owner_token)


def register_data_delivery_tools(registry: ToolRegistry) -> None:
    """注册数据交付工具族（预览→提交→查询 + 能力查询）。"""

    @tool(
        registry,
        tier=2, domains=["dataset"], name="delivery_capabilities",
        capabilities=["dataset_ingest"],
        description=(
            "查询数据交付格式能力与限制（真实驱动探测）：可用/受限/不可用格式、"
            "行数/字节/字段硬帽、writer 版本。"
            "\n返回：{formats: [{format, available, constrained, reason, notes}], limits}"
        ),
        param_descriptions={},
        side_effect="pure",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["数据交付", "delivery", "导出", "格式能力"],
        failure_modes=[],
    )
    async def delivery_capabilities_tool() -> dict:
        from app.services.data_delivery.contracts import (
            probe_all_formats, writer_versions,
        )

        return {
            "status": "success",
            "formats": [
                {"format": p.format, "available": p.available,
                 "constrained": p.constrained, "reason": p.reason,
                 "notes": p.notes}
                for p in probe_all_formats()
            ],
            "writer_versions": writer_versions(),
        }

    @tool(
        registry,
        tier=2, domains=["dataset"], name="preview_data_delivery",
        capabilities=["dataset_ingest"],
        description=(
            "交付预览（不改数据）：解析源引用 → 源身份/行数/完整性/CRS/几何画像、"
            "逐格式能力、损失清单（requires_ack）与阻断项。提交前必看。"
            "\n返回：{spec_fingerprint, source, losses, blocking_issues, "
            "required_acknowledgments, estimated_rows}"
        ),
        param_descriptions={
            "spec": "交付规格（DeliverySpec JSON：source/scope/fields/crs/format/filename）",
        },
        side_effect="pure",
        network=False,
        deterministic=False,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=["数据交付", "delivery", "预览", "保真", "损失"],
        failure_modes=["source_not_found", "source_expired", "invalid_args"],
    )
    async def preview_data_delivery_tool(spec: Dict[str, Any]) -> dict:
        from app.services.data_delivery import preview_delivery
        from app.services.data_delivery.service import DeliveryError

        try:
            parsed = DeliverySpec.model_validate(spec)
        except Exception as e:
            return {"status": "error", "error_type": "INVALID_SPEC",
                    "error": f"规格非法: {e}"}
        try:
            preview = await preview_delivery(parsed, _actor())
        except DeliveryError as e:
            return {"status": "error", "error_type": e.error_code,
                    "error": e.reason}
        d = preview.model_dump(exclude_none=True)
        d["status"] = "success"
        return d

    @tool(
        registry,
        tier=2, domains=["dataset"], name="submit_data_delivery",
        capabilities=["dataset_ingest"],
        description=(
            "提交数据交付任务（GeoJSON/GeoPackage/GeoParquet/CSV；受限 "
            "Shapefile ZIP/FlatGeobuf）。服务端复检阻断项并强制损失确认；"
            "返回 job_id 供查询。幂等：同规格重复提交复用既有任务。"
            "\n返回：{job_id, status, idempotent_reuse, message}"
        ),
        param_descriptions={
            "spec": "交付规格（与 preview 相同的 DeliverySpec JSON）",
            "acknowledged_losses": "预览返回的 required_acknowledgments 全集",
            "project_id": "可选：登记交付成果到项目",
        },
        execution_policy="async",
        side_effect="state_mutation",
        data_mutations=["artifact_write"],
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        tags=["数据交付", "delivery", "导出", "提交", "任务"],
        produced_refs=["artifact"],
        output_semantic_type="ref",
        result_size_policy="ref_offload",
        failure_modes=["loss_ack_required", "format_unavailable",
                       "source_expired", "source_revision_changed",
                       "row_limit_exceeded", "write_failed", "verify_failed"],
    )
    async def submit_data_delivery_tool(
        spec: Dict[str, Any],
        acknowledged_losses: Optional[list] = None,
        project_id: Optional[str] = None,
    ) -> dict:
        from app.services.data_delivery import submit_delivery
        from app.services.data_delivery.service import DeliveryError

        try:
            parsed = DeliverySpec.model_validate(spec)
        except Exception as e:
            return {"status": "error", "error_type": "INVALID_SPEC",
                    "error": f"规格非法: {e}"}
        try:
            result = await submit_delivery(
                parsed, _actor(),
                acknowledged_losses=acknowledged_losses or [],
                project_id=project_id)
        except DeliveryError as e:
            return {"status": "error", "error_type": e.error_code,
                    "error": e.reason}
        result["status"] = "success"
        result["poll_hint"] = (
            f"GET /api/v1/data-delivery/jobs/{result.get('job_id')} 查询状态与回执")
        return result

    @tool(
        registry,
        tier=2, domains=["dataset"], name="get_data_delivery",
        capabilities=["dataset_ingest"],
        description=(
            "查询交付任务状态与回执（completed 含成员清单/行数/校验和/损失应用/"
            "CRS 证据 + 授权下载 URL）。"
            "\n返回：{status, receipt?, download_url?}"
        ),
        param_descriptions={
            "job_id": "submit_data_delivery 返回的 job_id",
        },
        side_effect="pure",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["数据交付", "delivery", "回执", "下载"],
        failure_modes=["not_found", "unauthorized"],
    )
    async def get_data_delivery_tool(job_id: str) -> dict:
        from app.services.data_delivery import get_delivery_job
        from app.services.data_delivery.service import DeliveryError

        try:
            view = await get_delivery_job(job_id, _actor())
        except DeliveryError as e:
            return {"status": "error", "error_type": e.error_code,
                    "error": e.reason}
        d: Dict[str, Any] = {"status": "success", "job_status": view.status,
                             "progress": view.progress,
                             "progress_message": view.progress_message,
                             "format": view.format, "filename": view.filename}
        if view.receipt is not None:
            d["receipt"] = view.receipt.model_dump(exclude_none=True)
        if view.download_url:
            d["download_url"] = view.download_url
        if view.error_code:
            d["error_code"] = view.error_code
            d["error"] = view.error
        return d
