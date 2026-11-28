"""Agent 工程包交换工具面（05 线 WP5）：检视→导出计划→导出 / 导入预检→导入计划→导入。

信任边界（与 data_delivery 工具族同款纪律）：
- inspect/plan/import_inspect/import_plan 只读（无 state mutation）；
- export/import 是 state_mutation（磁盘产物 / 新项目注册）—— 服务端
  强制授权复检与冲突确认，工具不旁路；
- 输入只接受授权 project_id / 受控上传 ID / job id，绝不接受磁盘路径；
- 摘要有界；产物经既有导出下载链（签名 URL）交付，不回服务器路径。
"""
import logging
from typing import Any, Dict, Optional

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _actor() -> "Optional[Any]":
    from app.services.data_delivery.source_resolver import DeliveryActor
    from app.services.jobs.context import current_origin

    origin = current_origin()
    if origin is None:
        return DeliveryActor()
    return DeliveryActor(
        user_id=origin.owner_id, org_id=origin.org_id,
        owner_token=origin.owner_token)


def _origin_session_id() -> Optional[str]:
    from app.services.jobs.context import current_origin

    origin = current_origin()
    return origin.session_id if origin else None


def register_project_exchange_tools(registry: ToolRegistry) -> None:
    """注册工程包交换工具族（05 线；deferred 超集，可被 tool_search 发现）。"""

    @tool(
        registry,
        tier=2, domains=["dataset", "project"], name="project_exchange_inspect",
        description=(
            "工程包检视（只读，零写入）：项目依赖闭包（数据集/工作流/产物/地图）"
            "分类（embedded/external_reference/redacted/missing/unsupported）、"
            "复现分级、预估体积与披露清单。导出前必看。"
            "\n返回：{reproduction_level, classification_counts, totals, "
            "runnable_workflows, disclosure, exportable}"
        ),
        param_descriptions={
            "project_id": "项目 ID（须为当前用户可读的项目）",
            "selection": "可选范围 {kind: project|map|analysis, map_product_ids?, workflow_ids?}",
        },
        side_effect="pure",
        network=False,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        result_size_policy="bounded",
        scale_class="medium",
        tags=["工程包", "交换", "导出", "检视", "依赖"],
        failure_modes=["project_not_found", "unauthorized"],
    )
    async def project_exchange_inspect_tool(
        project_id: str, selection: Optional[Dict[str, Any]] = None,
    ) -> dict:
        from app.services.project_exchange.inspector import inspect_project

        def _run():
            from app.core.database import SessionLocal
            from app.services.project_service import ProjectService

            actor = _actor()
            with SessionLocal() as db:
                project = ProjectService.get_project_with_auth(
                    db, project_id, user_id=actor.user_id, org_id=actor.org_id)
                if project is None:
                    return {"status": "error",
                            "error_type": "PROJECT_NOT_FOUND",
                            "error": "项目不存在或无权访问"}
                return inspect_project(db, project_id, selection=selection)

        from fastapi.concurrency import run_in_threadpool

        result = await run_in_threadpool(_run)
        if result.get("exportable") is False:
            result["note"] = ("闭包内没有可打包载荷 —— 诚实分级为 partial；"
                              "导出将被拒绝（EMPTY_SELECTION）")
        return {"status": "success", **result}

    @tool(
        registry,
        tier=2, domains=["dataset", "project"], name="project_exchange_export_plan",
        description=(
            "导出打包计划（只读）：逻辑 ID 分配、逐项载荷路径/大小预估、"
            "标准 GIS 物化布局（layers.gpkg 图层/栅格/表格）、工作流可运行判定。"
            "\n返回：{datasets, workflows, artifacts, maps, standard_files, limits}"
        ),
        param_descriptions={
            "project_id": "项目 ID",
            "selection": "可选范围（同 inspect）",
        },
        side_effect="pure",
        network=False,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        result_size_policy="bounded",
        scale_class="medium",
        tags=["工程包", "交换", "导出", "计划"],
        failure_modes=["project_not_found", "unauthorized"],
    )
    async def project_exchange_export_plan_tool(
        project_id: str, selection: Optional[Dict[str, Any]] = None,
    ) -> dict:
        from app.services.project_exchange.inspector import plan_project

        def _run():
            from app.core.database import SessionLocal
            from app.services.project_service import ProjectService

            actor = _actor()
            with SessionLocal() as db:
                project = ProjectService.get_project_with_auth(
                    db, project_id, user_id=actor.user_id, org_id=actor.org_id)
                if project is None:
                    return {"status": "error",
                            "error_type": "PROJECT_NOT_FOUND",
                            "error": "项目不存在或无权访问"}
                return plan_project(db, project_id, selection=selection)

        from fastapi.concurrency import run_in_threadpool

        result = await run_in_threadpool(_run)
        return {"status": "success", **result}

    @tool(
        registry,
        tier=2, domains=["dataset", "project"], name="project_exchange_export",
        description=(
            "提交工程包导出（生成 .wgproject.zip）：服务端复检授权与闭包；"
            "标准 GIS 物化 + manifest + 保真回执；产物经受保护下载链交付，"
            "并自动出现在会话文件面板。幂等：同规格重复提交复用同一任务。"
            "\n返回：{job_id, status, message}；用 GET "
            "/api/v1/project-exchange/exports/{package_id}/receipt 查收据"
            "（先经 GET /api/v1/jobs/{job_id} 拿 package_id）。"
        ),
        param_descriptions={
            "project_id": "项目 ID（须可写或可读——导出为读操作，产物归调用者）",
            "selection": "可选范围（同 inspect）",
            "package_name": "可选包名（下载文件名主干）",
        },
        execution_policy="async",
        side_effect="state_mutation",
        data_mutations=["artifact_write"],
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        tags=["工程包", "交换", "导出", "提交", "任务"],
        produced_refs=["artifact"],
        output_semantic_type="ref",
        result_size_policy="ref_offload",
        failure_modes=["empty_selection", "size_budget_exceeded",
                       "write_failed", "unauthorized"],
    )
    async def project_exchange_export_tool(
        project_id: str,
        selection: Optional[Dict[str, Any]] = None,
        package_name: str = "",
    ) -> dict:
        from app.services.project_exchange.exporter import submit_export

        try:
            result = await submit_export(
                project_id, _actor(), selection=selection,
                package_name=package_name,
                session_id=_origin_session_id())
        except Exception as e:  # noqa: BLE001 — typed 错误转结构化返回
            code = getattr(e, "error_code", "EXPORT_FAILED")
            return {"status": "error", "error_type": code,
                    "error": str(getattr(e, "reason", e))[:400]}
        result["status"] = "success"
        result["poll_hint"] = (
            f"GET /api/v1/jobs/{result.get('job_id')} 查询任务进度；"
            "完成后经 /api/v1/project-exchange/exports/{package_id}/receipt "
            "获取下载引用")
        return result

    @tool(
        registry,
        tier=2, domains=["dataset", "project"], name="project_exchange_import_inspect",
        description=(
            "导入预检（只读，不落 staging）：受控上传 ID → manifest 摘要、"
            "许可清单、冲突（缺失声明/重复逻辑 ID/多余条目）、复现分级。"
            "\n返回：{classification_counts, totals, licenses, conflicts, limits}"
        ),
        param_descriptions={
            "package_upload_id": "工程包受控上传 ID（/project-exchange/packages/upload 返回）",
        },
        side_effect="pure",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        scale_class="small",
        tags=["工程包", "交换", "导入", "预检", "许可"],
        failure_modes=["import_source_invalid", "unauthorized",
                       "package_format_unsupported", "manifest_invalid"],
    )
    async def project_exchange_import_inspect_tool(package_upload_id: str) -> dict:
        from app.services.project_exchange.importer import (
            inspect_package, resolve_package_upload,
        )

        try:
            zip_path, _meta = resolve_package_upload(package_upload_id, _actor())
        except Exception as e:  # noqa: BLE001
            return {"status": "error",
                    "error_type": getattr(e, "error_code", "IMPORT_SOURCE_INVALID"),
                    "error": str(getattr(e, "reason", e))[:400]}
        from fastapi.concurrency import run_in_threadpool

        result = await run_in_threadpool(inspect_package, zip_path)
        return {"status": "success", **result}

    @tool(
        registry,
        tier=2, domains=["dataset", "project"], name="project_exchange_import_plan",
        description=(
            "导入计划（只读）：预检 + 默认目标策略 —— 全新隔离项目、"
            "不覆盖任何现有项目、不自动重算工作流。"
            "\n返回：inspect 全字段 + default_target"
        ),
        param_descriptions={
            "package_upload_id": "工程包受控上传 ID",
            "target_name": "可选目标项目名（缺省用包内项目名）",
        },
        side_effect="pure",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        scale_class="small",
        tags=["工程包", "交换", "导入", "计划"],
        failure_modes=["import_source_invalid", "unauthorized"],
    )
    async def project_exchange_import_plan_tool(
        package_upload_id: str, target_name: str = "",
    ) -> dict:
        result = await project_exchange_import_inspect_tool(package_upload_id)
        if result.get("status") != "success":
            return result
        result["default_target"] = {
            "mode": "new_isolated_project",
            "overwrites_existing": False,
            "auto_recompute": False,
            "target_name": (target_name or result.get("project_name") or "")[:120],
        }
        return result

    @tool(
        registry,
        tier=2, domains=["dataset", "project"], name="project_exchange_import",
        description=(
            "提交工程包导入（全新隔离项目）：服务端强制预检复检与冲突确认；"
            "拓扑序注册（数据集→工作流→产物→地图）+ ID 重映射 + 单事务回滚；"
            "完成后产出恢复报告与导入后校验。绝不覆盖现有项目、绝不自动重算。"
            "\n返回：{job_id, status, message}；完成后经 "
            "GET /api/v1/project-exchange/imports/{import_id}/receipt "
            "获取恢复报告。"
        ),
        param_descriptions={
            "package_upload_id": "工程包受控上传 ID",
            "target_name": "可选目标项目名",
            "acknowledged_conflicts": "预检 conflicts 的确认清单（需覆盖全部阻断项）",
        },
        execution_policy="async",
        side_effect="state_mutation",
        data_mutations=["upload", "artifact_write"],
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        tags=["工程包", "交换", "导入", "提交", "任务"],
        produced_refs=["artifact"],
        output_semantic_type="ref",
        result_size_policy="ref_offload",
        failure_modes=["conflict_unresolved", "hash_mismatch",
                       "path_unsafe", "write_failed", "unauthorized"],
    )
    async def project_exchange_import_tool(
        package_upload_id: str,
        target_name: str = "",
        acknowledged_conflicts: Optional[list] = None,
    ) -> dict:
        from app.services.project_exchange.importer import submit_import

        try:
            result = await submit_import(
                package_upload_id, _actor(), target_name=target_name,
                session_id=_origin_session_id(),
                acknowledged_conflicts=acknowledged_conflicts or [])
        except Exception as e:  # noqa: BLE001 — typed 错误转结构化返回
            code = getattr(e, "error_code", "IMPORT_FAILED")
            return {"status": "error", "error_type": code,
                    "error": str(getattr(e, "reason", e))[:400]}
        result["status"] = "success"
        result["poll_hint"] = (
            f"GET /api/v1/jobs/{result.get('job_id')} 查询任务进度；"
            "完成后经 /api/v1/project-exchange/imports/{import_id}/receipt "
            "获取恢复报告")
        return result
