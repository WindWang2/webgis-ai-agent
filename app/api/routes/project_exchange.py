"""Project Exchange Routes — /api/v1/projects/{pid}/exchange 与
/api/v1/project-exchange（05 线 · 工程包交换与可移植复现）。

路由只做验证与装配（鉴权 → 服务层），重逻辑全部在
``app/services/project_exchange``：
- inspect/plan：零写入检视（ProjectService 读门后调用）；
- export/import：幂等 durable job（取消/重试复用既有
  ``GET/DELETE/POST /api/v1/jobs/{id}``，此处不重复实现）；
- 包上传：专用受控接收面（.zip 白名单 + 预算 + owner sidecar）——
  标准上传端点格式词表不含 zip，工程包走独立入口；
- 回执查询：project 授权后投影（404 不泄露存在性）。
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.auth import get_current_user, get_owner_token
from app.schemas.project_exchange_schema import (
    ExchangeInspectionResponse,
    ExchangeInspectRequest,
    ExchangeLimits,
    ExchangePlanResponse,
    ExportStatusResponse,
    ExportSubmitRequest,
    ExportSubmitResponse,
    ImportInspectRequest,
    ImportInspectResponse,
    ImportPlanRequest,
    ImportPlanResponse,
    ImportStatusResponse,
    ImportSubmitRequest,
    ImportSubmitResponse,
    PackageUploadResponse,
)
from app.services.data_delivery.source_resolver import DeliveryActor
from app.services.project_exchange import contracts as pc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/project-exchange")


def _actor(user: dict, owner_token: Optional[str]) -> DeliveryActor:
    a = DeliveryActor.from_user_dict(user)
    a.owner_token = owner_token
    return a


def _map_error(exc: pc.PackageError) -> HTTPException:
    code = exc.error_code
    if code in (pc.ERR_PROJECT_NOT_FOUND, pc.ERR_UNAUTHORIZED,
                pc.ERR_JOB_NOT_FOUND, pc.ERR_IMPORT_SOURCE_INVALID):
        return HTTPException(status_code=404, detail=exc.reason)
    if code in (pc.ERR_EMPTY_SELECTION,):
        return HTTPException(status_code=422,
                             detail={"code": code, "error": exc.reason})
    return HTTPException(status_code=400,
                         detail={"code": code, "error": exc.reason})


async def _run_sync(fn, *args):
    import asyncio

    return await asyncio.to_thread(fn, *args)


def _sync_db(fn, *args, **kwargs):
    """同步 DB 调用统一走线程池（事件循环零阻塞）。"""
    import asyncio

    def _run():
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            return fn(db, *args, **kwargs)

    return asyncio.to_thread(_run)


# ── 导出侧（挂载在 /projects/{project_id}/exchange）────────────────────

exchange_sub = APIRouter(prefix="/projects/{project_id}/exchange")


@exchange_sub.post("/inspect", response_model=ExchangeInspectionResponse,
                   tags=["Project Exchange / 工程包交换"])
async def inspect_project_exchange(
    project_id: str,
    body: ExchangeInspectRequest,
    user: dict = Depends(get_current_user),
):
    """零写入检视：依赖闭包 + 分类 + 复现分级 + 预估体积 + 披露。"""
    from app.services.project_exchange.inspector import inspect_project

    def _run(db):
        from app.services.project_service import ProjectService

        if ProjectService.get_project_with_auth(
                db, project_id, user_id=str(user.get("id") or ""),
                org_id=user.get("org_id")) is None:
            raise pc.PackageError(pc.ERR_PROJECT_NOT_FOUND, "项目不存在或无权访问")
        return inspect_project(db, project_id,
                               selection=body.selection.model_dump())

    try:
        result = await _run_sync(_run)
    except pc.PackageError as e:
        raise _map_error(e) from e
    return result


@exchange_sub.post("/plan", response_model=ExchangePlanResponse,
                   tags=["Project Exchange / 工程包交换"])
async def plan_project_exchange(
    project_id: str,
    body: ExchangeInspectRequest,
    user: dict = Depends(get_current_user),
):
    """零写入打包计划：逻辑 ID 分配 + 逐项载荷预估 + 工作流可运行判定。"""
    from app.services.project_exchange.inspector import plan_project

    def _run(db):
        from app.services.project_service import ProjectService

        if ProjectService.get_project_with_auth(
                db, project_id, user_id=str(user.get("id") or ""),
                org_id=user.get("org_id")) is None:
            raise pc.PackageError(pc.ERR_PROJECT_NOT_FOUND, "项目不存在或无权访问")
        return plan_project(db, project_id,
                            selection=body.selection.model_dump())

    try:
        result = await _run_sync(_run)
    except pc.PackageError as e:
        raise _map_error(e) from e
    result["limits"] = ExchangeLimits(**result["limits"])
    return result


@exchange_sub.post("/export", response_model=ExportSubmitResponse,
                   tags=["Project Exchange / 工程包交换"])
async def export_project_package(
    project_id: str,
    body: ExportSubmitRequest,
    user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """提交工程包导出（幂等 durable job；产物进 exports 下载链 + 会话面板）。"""
    from app.services.project_exchange.exporter import submit_export

    try:
        result = await submit_export(
            project_id, _actor(user, owner_token),
            selection=body.selection.model_dump(),
            package_name=body.package_name,
            session_id=body.session_id)
    except pc.PackageError as e:
        raise _map_error(e) from e
    return result


@router.get("/exports/{package_id}/receipt",
            response_model=ExportStatusResponse,
            tags=["Project Exchange / 工程包交换"])
async def export_receipt(
    package_id: str,
    user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """导出回执（project 授权后投影）。"""
    from app.services.project_exchange.exporter import _authorize_project
    from app.services.project_exchange.importer import get_export_receipt

    try:
        receipt = await _run_sync(get_export_receipt, package_id,
                                  _actor(user, owner_token))
        project = await _authorize_project(receipt.get("project_id", ""),
                                           _actor(user, owner_token))
        if project is None:
            raise pc.PackageError(pc.ERR_PROJECT_NOT_FOUND, "项目不存在或无权访问")
    except pc.PackageError as e:
        raise _map_error(e) from e
    return receipt


# ── 导入侧 ───────────────────────────────────────────────────────────────

@router.post("/packages/upload", response_model=PackageUploadResponse,
             tags=["Project Exchange / 工程包交换"])
async def upload_project_package(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """工程包受控上传（.zip 白名单 + 预算 + owner sidecar；返回受控 ID）。"""
    from app.services.project_exchange.importer import save_package_upload

    filename = file.filename or "package.zip"
    lower = filename.lower()
    if not (lower.endswith(".zip") or
            lower.endswith(pc.PACKAGE_FILENAME_SUFFIX)):
        raise HTTPException(status_code=422,
                            detail={"code": pc.ERR_ENTRY_TYPE,
                                    "error": "工程包必须是 .zip / .wgproject.zip"})
    from app.core.config import settings

    budget = int(getattr(settings, "PROJECT_EXCHANGE_MAX_PACKAGE_BYTES",
                         pc.DEFAULT_MAX_PACKAGE_BYTES))
    fd, tmp_name = tempfile.mkstemp(prefix=".pkg-upload-", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        written = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > budget:
                raise HTTPException(
                    status_code=413,
                    detail={"code": pc.ERR_SIZE_BUDGET,
                            "error": f"包超过预算 {budget}B"})
            with open(tmp_path, "ab") as fh:
                fh.write(chunk)
        try:
            meta = await _run_sync(save_package_upload,
                                   _actor(user, owner_token), filename,
                                   tmp_path, None)
        except pc.PackageError as e:
            raise _map_error(e) from e
    finally:
        tmp_path.unlink(missing_ok=True)
    return PackageUploadResponse(
        package_upload_id=meta["upload_id"], filename=meta["filename"],
        bytes=meta["bytes"], sha256=meta["sha256"])


@router.post("/imports/inspect", response_model=ImportInspectResponse,
             tags=["Project Exchange / 工程包交换"])
async def import_inspect(
    body: ImportInspectRequest,
    user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """导入预检（零解包）：manifest 摘要 + 许可 + 冲突 + 复现分级。"""
    from app.services.project_exchange.importer import (
        inspect_package, resolve_package_upload,
    )

    try:
        zip_path, _meta = await _run_sync(resolve_package_upload,
                                          body.package_upload_id,
                                          _actor(user, owner_token))
        result = await _run_sync(inspect_package, zip_path)
    except pc.PackageError as e:
        raise _map_error(e) from e
    result["limits"] = ExchangeLimits(**result["limits"])
    return result


@router.post("/imports/plan", response_model=ImportPlanResponse,
             tags=["Project Exchange / 工程包交换"])
async def import_plan(
    body: ImportPlanRequest,
    user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """导入计划 = 预检 + 默认目标策略（全新隔离项目；无覆盖路径）。"""
    return await import_inspect(
        ImportInspectRequest(package_upload_id=body.package_upload_id),
        user, owner_token)


@router.post("/imports", response_model=ImportSubmitResponse,
             tags=["Project Exchange / 工程包交换"])
async def submit_import_route(
    body: ImportSubmitRequest,
    user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """提交工程包导入（幂等 durable job；默认全新隔离项目，不覆盖现有）。"""
    from app.services.project_exchange.importer import submit_import

    try:
        result = await submit_import(
            body.package_upload_id, _actor(user, owner_token),
            target_name=body.target_name,
            session_id=body.session_id,
            acknowledged_conflicts=body.acknowledged_conflicts)
    except pc.PackageError as e:
        raise _map_error(e) from e
    return result


@router.get("/imports/{import_id}/receipt",
            response_model=ImportStatusResponse,
            tags=["Project Exchange / 工程包交换"])
async def import_receipt(
    import_id: str,
    user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """导入回执/恢复报告（新项目授权后投影）。"""
    from app.services.project_exchange.exporter import _authorize_project
    from app.services.project_exchange.importer import get_import_receipt

    try:
        receipt = await _run_sync(get_import_receipt, import_id,
                                  _actor(user, owner_token))
        project = await _authorize_project(receipt.get("project_id", ""),
                                           _actor(user, owner_token))
        if project is None:
            raise pc.PackageError(pc.ERR_PROJECT_NOT_FOUND, "项目不存在或无权访问")
    except pc.PackageError as e:
        raise _map_error(e) from e
    return receipt
