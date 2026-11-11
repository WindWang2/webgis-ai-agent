"""G17 staged 导入工作台 —— HTTP API 路由。

权限模型与上传路由一致（upload.py 同款）：
- ``session_id`` 一律来自已验证上下文（表单/查询 + verify_session_owner），
  模型/客户端传入的 id 不构成授权；
- 匿名会话转发 ``X-Session-Token``（owner_token）；
- 统一 NOT_FOUND 不泄露他人批次存在性。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile

from app.core.auth import get_current_user_optional_with_version
from app.schemas.data_import_schema import (
    BatchCreateResponse,
    BatchDetailResponse,
    BatchListResponse,
    CancelResponse,
    CommitAcceptedResponse,
    CommitRequest,
    DeleteResponse,
    PlanSaveRequest,
    PlanSaveResponse,
    PreviewRequest,
    PreviewResponse,
    ReceiptsResponse,
)
from app.services.auth_history_bridge import verify_session_owner
from app.services.data_import import budgets
from app.services.data_import.service import DataImportError, get_data_import_service
from app.tools._utils import async_db_session

logger = logging.getLogger(__name__)
router = APIRouter()

_ERROR_STATUS = {
    "NOT_FOUND": 404,
    "EXPIRED": 410,
    "COMMIT_IN_PROGRESS": 409,
    "PLAN_VERSION_CONFLICT": 409,
    "NOT_COMMITTING": 409,
    "UNKNOWN_MEMBER": 404,
}


def _to_http(e: DataImportError) -> HTTPException:
    status = e.status if e.status != 400 else _ERROR_STATUS.get(e.code, 400)
    return HTTPException(status_code=status, detail=f"{e.code}: {e.detail}" if e.detail else e.code)


async def _verify_owner(
    db, session_id: Optional[str], user: dict, owner_token: Optional[str]
) -> str:
    if not session_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    await verify_session_owner(db, session_id, user_id=user.get("user_id"), owner_token=owner_token)
    return session_id


def _user_fp(user: dict) -> str:
    uid = user.get("user_id")
    return f"user:{uid}" if uid else "anonymous"


def _member_dict(m) -> Dict[str, Any]:
    return {
        "member_key": m.member_key,
        "source_id": m.source_id,
        "container": m.container.value,
        "kind": m.kind.value,
        "display_name": m.display_name,
        "container_ref": m.container_ref,
        "fields": m.fields,
        "geometry_type": m.geometry_type,
        "feature_count": m.feature_count,
        "crs": m.crs,
        "crs_source": m.crs_source,
        "size_bytes": m.size_bytes,
        "unsupported_reason": m.unsupported_reason,
        "hints": m.hints,
        "status": m.status.value,
        "selected": m.selected,
    }


def _state_members(state) -> List[Dict[str, Any]]:
    return [_member_dict(m) for m in state.members]


@router.post(
    "/data-import/batches",
    response_model=BatchCreateResponse,
    summary="创建导入批次（多文件 staging + 成员枚举）",
)
async def create_batch(
    files: List[UploadFile] = File(..., description="一批本地数据文件"),
    session_id: str = Form(..., description="归属会话（已验证）"),
    license: str = Form(""),
    attribution: str = Form(""),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> BatchCreateResponse:
    async with async_db_session() as db:
        await _verify_owner(db, session_id, _user, owner_token)
    if not files:
        raise HTTPException(status_code=400, detail="批次必须至少包含一个文件")
    if len(files) > budgets.MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"TOO_MANY_FILES: 单批最多 {budgets.MAX_FILES_PER_BATCH} 个文件",
        )
    payloads: List[tuple] = []
    for f in files:
        content = await f.read()
        payloads.append((f.filename or "file", content))
    service = get_data_import_service()
    try:
        state = await service.create_batch(
            session_id, _user_fp(_user), payloads,
            license=license, attribution=attribution,
        )
    except DataImportError as e:
        raise _to_http(e) from e
    return BatchCreateResponse(
        batch_id=state.batch_id,
        status=state.status.value,
        session_id=state.session_id,
        created_at=state.created_at,
        expires_at=state.expires_at,
        sources=[s.model_dump() for s in state.sources],
        members=_state_members(state),
        notes=state.notes,
    )


@router.get("/data-import/batches", response_model=BatchListResponse)
async def list_batches(
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> BatchListResponse:
    async with async_db_session() as db:
        await _verify_owner(db, session_id, _user, owner_token)
    batches = await get_data_import_service().list_batches(session_id)
    return BatchListResponse(batches=batches)


@router.get("/data-import/batches/{batch_id}", response_model=BatchDetailResponse)
async def get_batch(
    batch_id: str,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> BatchDetailResponse:
    async with async_db_session() as db:
        await _verify_owner(db, session_id, _user, owner_token)
    try:
        state = await get_data_import_service().get_batch(batch_id, session_id)
    except DataImportError as e:
        raise _to_http(e) from e
    return BatchDetailResponse(
        batch_id=state.batch_id,
        status=state.status.value,
        session_id=state.session_id,
        created_at=state.created_at,
        updated_at=state.updated_at,
        expires_at=state.expires_at,
        plan_version=state.plan_version,
        sources=[s.model_dump() for s in state.sources],
        members=_state_members(state),
        interpretations=state.interpretations,
        plan_hashes=state.plan_hashes,
        previewed_hashes=state.previewed_hashes,
        receipts={k: r.model_dump() for k, r in state.receipts.items()},
        notes=state.notes,
        license=state.license,
        attribution=state.attribution,
    )


@router.post("/data-import/batches/{batch_id}/plan", response_model=PlanSaveResponse)
async def save_plan(
    batch_id: str,
    body: PlanSaveRequest,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> PlanSaveResponse:
    async with async_db_session() as db:
        await _verify_owner(db, session_id, _user, owner_token)
    try:
        state = await get_data_import_service().save_plan(
            batch_id,
            session_id,
            {k: v.model_dump() for k, v in body.member_configs.items()},
            selected=body.selected,
            expected_plan_version=body.expected_plan_version,
        )
    except DataImportError as e:
        raise _to_http(e) from e
    return PlanSaveResponse(
        batch_id=batch_id,
        plan_version=state.plan_version,
        plan_hashes=state.plan_hashes,
        status=state.status.value,
    )


@router.post("/data-import/batches/{batch_id}/preview", response_model=PreviewResponse)
async def preview(
    batch_id: str,
    body: PreviewRequest,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> PreviewResponse:
    async with async_db_session() as db:
        await _verify_owner(db, session_id, _user, owner_token)
    try:
        out = await get_data_import_service().preview_members(
            batch_id, session_id, body.member_keys, body.row_limit
        )
    except DataImportError as e:
        raise _to_http(e) from e
    return PreviewResponse(**out)


@router.post("/data-import/batches/{batch_id}/commit", response_model=CommitAcceptedResponse)
async def commit(
    batch_id: str,
    body: CommitRequest,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> CommitAcceptedResponse:
    async with async_db_session() as db:
        await _verify_owner(db, session_id, _user, owner_token)
    try:
        out = await get_data_import_service().start_commit(
            batch_id,
            session_id,
            body.member_keys,
            body.expected_plan_version,
            background=True,
        )
    except DataImportError as e:
        raise _to_http(e) from e
    return CommitAcceptedResponse(**out)


@router.post("/data-import/batches/{batch_id}/cancel", response_model=CancelResponse)
async def cancel(
    batch_id: str,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> CancelResponse:
    async with async_db_session() as db:
        await _verify_owner(db, session_id, _user, owner_token)
    try:
        out = await get_data_import_service().cancel_batch(batch_id, session_id)
    except DataImportError as e:
        raise _to_http(e) from e
    return CancelResponse(**out)


@router.get("/data-import/batches/{batch_id}/receipts", response_model=ReceiptsResponse)
async def receipts(
    batch_id: str,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> ReceiptsResponse:
    async with async_db_session() as db:
        await _verify_owner(db, session_id, _user, owner_token)
    try:
        out = await get_data_import_service().get_receipts(batch_id, session_id)
    except DataImportError as e:
        raise _to_http(e) from e
    return ReceiptsResponse(**out)


@router.get("/data-import/batches/{batch_id}/diagnostics")
async def get_diagnostics(
    batch_id: str,
    member_key: str = Query(..., max_length=300),
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> Dict[str, Any]:
    """拒绝行明细产物（同 owner；评审 P2-4：收据 diagnostics_ref 可落地读取）。"""
    async with async_db_session() as db:
        await _verify_owner(db, session_id, _user, owner_token)
    try:
        state = await get_data_import_service().get_batch(batch_id, session_id)
    except DataImportError as e:
        raise _to_http(e) from e
    rel = state.diagnostics.get(member_key)
    if not rel or "/" in rel or ".." in rel:
        raise HTTPException(status_code=404, detail="该成员无拒绝行明细（或未超内联上限）")
    from app.services.data_import import staging

    path = staging.diag_dir(state) / rel
    if not path.exists():
        raise HTTPException(status_code=404, detail="明细产物不存在（staging 已过期或已清理）")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"明细读取失败: {e}") from e
    lines = [ln for ln in content.splitlines() if ln.strip()]
    return {
        "batch_id": batch_id,
        "member_key": member_key,
        "lines": len(lines),
        "rows": lines[: budgets.DIAG_INLINE_MAX_ROWS],
    }


@router.delete("/data-import/batches/{batch_id}", response_model=DeleteResponse)
async def delete_batch(
    batch_id: str,
    session_id: str = Query(...),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _user: dict = Depends(get_current_user_optional_with_version),
) -> DeleteResponse:
    async with async_db_session() as db:
        await _verify_owner(db, session_id, _user, owner_token)
    try:
        deleted = await get_data_import_service().delete_batch(batch_id, session_id)
    except DataImportError as e:
        raise _to_http(e) from e
    return DeleteResponse(batch_id=batch_id, deleted=deleted)
