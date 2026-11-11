"""G17 staged 导入工作台 —— Pi 标准工具。

与浏览器工作台共用同一 DataImportService、同一导入计划与同一提交收据
（任务书专属目标）。会话归属遵循 upload_tools 同款纪律：LLM 传入的
session_id 不可信，以 runtime context 已验证会话为准，且批次 owner 必须
与之匹配。工具不提供任何本地路径读取能力 —— 源只能来自已上传 staging。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.lib.runtime.context import current_runtime_context
from app.services.data_import.service import DataImportError, get_data_import_service
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _resolve_session_id(provided: Optional[str]) -> Optional[str]:
    """runtime context 已验证会话优先；与 LLM 传参冲突 → None（拒绝）。"""
    ctx = current_runtime_context()
    verified = getattr(ctx, "session_id", None)
    if verified:
        return verified if provided in (None, verified) else None
    return provided


def _fail(code: str, message: str) -> Dict[str, Any]:
    return {"success": False, "code": code, "message": message, "data": None}


class ImportPlanConfigureArgs(BaseModel):
    """import_plan_configure 参数合同（显式模型：registry 不再从签名推导 Dict）。"""

    batch_id: str = Field(..., min_length=3, max_length=64)
    member_configs: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    selected: Optional[List[str]] = None
    session_id: Optional[str] = None


def register_data_import_tools(registry: ToolRegistry) -> None:
    """注册导入工作台工具族（检查→计划→预览→提交→收据）。"""

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="import_batch_inspect",
        capabilities=["workspace_state_inspection"],
        description=(
            "列出当前会话的 staged 导入批次及其成员清单（文件、GPKG/KML 图层、"
            "shapefile 集、CSV/XLSX 工作表）。指定 batch_id 时返回该批次成员详情"
            "（字段、几何类型、CRS 证据、支持状态）。"
        ),
        param_descriptions={
            "batch_id": "可选；缺省列出全部批次摘要",
            "session_id": "可选；以当前运行时会话为准",
        },
        side_effect="pure",
        network=False,
        deterministic=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["导入", "import", "批次", "工作台", "staging"],
        output_semantic_type="list",
        result_size_policy="bounded",
        failure_modes=["empty_result", "not_found"],
    )
    async def import_batch_inspect(
        batch_id: Optional[str] = None, session_id: Optional[str] = None
    ) -> dict:
        sid = _resolve_session_id(session_id)
        if not sid:
            return {"success": True, "batches": [], "count": 0,
                    "message": "当前会话没有 staged 导入批次。"}
        service = get_data_import_service()
        try:
            if batch_id:
                state = await service.get_batch(batch_id, sid)
                members = [
                    {
                        "member_key": m.member_key,
                        "kind": m.kind.value,
                        "display_name": m.display_name,
                        "geometry_type": m.geometry_type,
                        "feature_count": m.feature_count,
                        "crs": m.crs,
                        "crs_source": m.crs_source,
                        "fields": m.fields[:16],
                        "status": m.status.value,
                        "selected": m.selected,
                        "unsupported_reason": m.unsupported_reason,
                    }
                    for m in state.members
                ]
                return {
                    "success": True,
                    "batch_id": batch_id,
                    "status": state.status.value,
                    "plan_version": state.plan_version,
                    "members": members,
                    "notes": state.notes[-8:],
                }
            batches = await service.list_batches(sid)
            return {"success": True, "batches": batches, "count": len(batches)}
        except DataImportError as e:
            if e.code in ("NOT_FOUND", "EXPIRED"):
                raise KeyError(f"导入批次不存在: {batch_id}")
            return _fail(e.code, e.detail)

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="import_plan_configure",
        capabilities=["dataset_preparation"],
        description=(
            "为 staged 批次成员保存/修改解释计划（CSV/XLSX：模式 points/wkt/table、"
            "X/Y 或 WKT 列、分隔符、编码、字段类型、源 CRS 声明；矢量：源 CRS 声明、"
            "保留字段）。计划版本与 hash 随保存更新；旧预览随之失效。"
        ),
        param_descriptions={
            "batch_id": "批次 ID（import_batch_inspect 获取）",
            "member_configs": "成员 key → 解释配置（与 API MemberConfig 同构）",
            "selected": "可选；勾选待导入成员 key 列表",
            "session_id": "可选；以当前运行时会话为准",
        },
        args_model=ImportPlanConfigureArgs,
        side_effect="state_mutation",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["导入", "解释计划", "计划", "plan"],
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=["invalid_args", "target_not_found", "conflict"],
    )
    async def import_plan_configure(
        batch_id: str,
        member_configs: Dict[str, Dict[str, Any]],
        selected: Optional[list] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        sid = _resolve_session_id(session_id)
        if not sid:
            return _fail("NO_SESSION", "无可验证会话")
        if not isinstance(member_configs, dict) or not member_configs:
            return _fail("INVALID_ARGS", "member_configs 必须是非空对象")
        try:
            state = await get_data_import_service().save_plan(
                batch_id, sid, member_configs, selected=selected
            )
        except DataImportError as e:
            if e.code == "NOT_FOUND":
                raise KeyError(f"导入批次不存在: {batch_id}")
            return _fail(e.code, e.detail)
        return {
            "success": True,
            "batch_id": batch_id,
            "plan_version": state.plan_version,
            "plan_hashes": state.plan_hashes,
            "message": "解释计划已保存；请预览确认后提交。",
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="import_batch_preview",
        capabilities=["dataset_preparation"],
        description=(
            "对批次成员做有界解释预览（默认 200 行，上限 2000）：样本行、列类型建议、"
            "拒绝行（可定位源行）、坐标/几何摘要、CRS 确认状态。预览不创建数据。"
        ),
        param_descriptions={
            "batch_id": "批次 ID",
            "member_key": "成员 key（可选；缺省全部可预览成员）",
            "row_limit": "样本行数（默认 200，≤2000）",
            "session_id": "可选；以当前运行时会话为准",
        },
        side_effect="pure",
        network=False,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="small",
        tags=["导入", "预览", "preview", "样本"],
        output_semantic_type="text",
        result_size_policy="bounded",
        failure_modes=["invalid_args", "target_not_found", "interpretation_failed"],
    )
    async def import_batch_preview(
        batch_id: str,
        member_key: Optional[str] = None,
        row_limit: int = 200,
        session_id: Optional[str] = None,
    ) -> dict:
        sid = _resolve_session_id(session_id)
        if not sid:
            return _fail("NO_SESSION", "无可验证会话")
        keys = [member_key] if member_key else None
        try:
            out = await get_data_import_service().preview_members(
                batch_id, sid, keys, min(max(row_limit, 1), 2000)
            )
        except DataImportError as e:
            if e.code == "NOT_FOUND":
                raise KeyError(f"导入批次不存在: {batch_id}")
            return _fail(e.code, e.detail)
        previews = out.get("previews", [])
        for p in previews:
            if "sample_rows" in p:
                p["sample_rows"] = p["sample_rows"][:5]   # LLM 侧再收敛
            if "rejects_inline" in p:
                p["rejects_inline"] = p["rejects_inline"][:5]
        return {"success": True, "batch_id": batch_id, "plan_version": out.get("plan_version"),
                "previews": previews}

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="import_batch_commit",
        capabilities=["dataset_ingestion"],
        description=(
            "提交批次成员为正式会话数据：空间结果经摄入管线产出 ref:upload-*（含质量"
            "画像），纯表产出 ref:table-*。逐成员幂等（同计划重试复用既有产物）；部分"
            "成功以批次收据披露。需产品确认条件（如 CRS 未确认的成员会被拒绝）。"
        ),
        param_descriptions={
            "batch_id": "批次 ID",
            "member_keys": "可选；缺省提交全部勾选成员",
            "session_id": "可选；以当前运行时会话为准",
        },
        side_effect="artifact_creation",
        produced_refs=["data"],
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="medium",
        idempotent=True,
        tags=["导入", "提交", "commit", "数据集", "ingest"],
        output_semantic_type="ref",
        result_size_policy="ref_offload",
        failure_modes=["invalid_args", "target_not_found", "plan_stale",
                       "crs_unconfirmed", "partial_failure"],
    )
    async def import_batch_commit(
        batch_id: str,
        member_keys: Optional[list] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        sid = _resolve_session_id(session_id)
        if not sid:
            return _fail("NO_SESSION", "无可验证会话")
        try:
            out = await get_data_import_service().start_commit(
                batch_id, sid, member_keys, background=False
            )
        except DataImportError as e:
            if e.code == "NOT_FOUND":
                raise KeyError(f"导入批次不存在: {batch_id}")
            return _fail(e.code, e.detail)
        receipts = out.get("receipts", {})
        completed = [r for r in receipts.values() if r.get("status") == "completed"]
        return {
            "success": True,
            "batch_id": batch_id,
            "status": out.get("status"),
            "completed": len(completed),
            "total": len(receipts),
            "produced_refs": [r["output_ref"] for r in completed if r.get("output_ref")],
            "receipts": receipts,
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="import_batch_receipt",
        capabilities=["workspace_state_inspection"],
        description="读取批次提交收据（每成员输出 ref、行数、拒绝数、CRS 证据、错误码）。",
        param_descriptions={"batch_id": "批次 ID", "session_id": "可选；以当前运行时会话为准"},
        side_effect="pure",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["导入", "收据", "receipt"],
        output_semantic_type="text",
        result_size_policy="bounded",
        failure_modes=["empty_result", "not_found"],
    )
    async def import_batch_receipt(
        batch_id: str, session_id: Optional[str] = None
    ) -> dict:
        sid = _resolve_session_id(session_id)
        if not sid:
            return _fail("NO_SESSION", "无可验证会话")
        try:
            out = await get_data_import_service().get_receipts(batch_id, sid)
        except DataImportError as e:
            if e.code in ("NOT_FOUND", "EXPIRED"):
                raise KeyError(f"导入批次不存在: {batch_id}")
            return _fail(e.code, e.detail)
        return {"success": True, **out}
