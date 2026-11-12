"""selection_handles routes — G24 / P3 服务端权威选择集句柄。

路由面（全部要求会话所有权；每请求重新鉴权 ref，handle 不承载权限）：
- POST   /layers/data/{ref_id}/selection                创建（scope + revision guard）
- GET    /layers/data/{ref_id}/selection/{handle}       元数据（count/completeness/revision）
- GET    /layers/data/{ref_id}/selection/{handle}/features  稳定序分页（复用 page_features 语义）
- POST   /layers/data/{ref_id}/selection/{handle}/materialize 物化成会话 ref（fieldcompute 等）
- DELETE /layers/data/{ref_id}/selection/{handle}       显式释放

错误语义（typed，不静默换语义）：
- 404 not_found：handle 不存在 / 不属于该会话或 ref（不泄露存在性）
- 410 selection_stale：ref 的 content_revision 已前进（数据刷新/编辑）——
  旧 scope 的全集描述不再可信，必须重建；selection_expired：空闲超时
- 409 revision_conflict：创建时携带 v 与当前不符（与 /features 同语义）
- 400 selection_bad_scope / 422 selection_over_budget
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response

from app.api.routes.layer import _extract_fc, _layer_data_budget
from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.selection_handles import (
    SELECTION_TTL_SECONDS,
    SelectionHandleError,
    create_selection,
    handle_store,
    materialize_selection,
    scope_summary,
    selection_features_page,
)
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

router = APIRouter()

_ERR_STATUS = {
    "not_found": 404,
    "stale": 410,
    "expired": 410,
    "bad_scope": 400,
    "bad_request": 400,
    "over_budget": 422,
}


def _raise_selection_error(exc: SelectionHandleError) -> None:
    detail: Dict[str, Any] = {"error": f"selection_{exc.code}", "message": exc.message}
    detail.update(exc.detail)
    raise HTTPException(status_code=_ERR_STATUS.get(exc.code, 400), detail=detail)


def _check_ref_id(ref_id: str) -> None:
    if not ref_id or len(ref_id) > 128 or any(c.isspace() for c in ref_id):
        raise HTTPException(status_code=400, detail="非法 ref_id")


async def _authorized_fc(session_id: str, ref_id: str, owner_token: Optional[str]) -> Dict[str, Any]:
    """会话鉴权（路由依赖已做）+ ref 归属/读取授权 + FC 提取。"""
    res = await session_data_manager.get_ref_data_shared(
        session_id, ref_id, owner_token=owner_token)
    if not res.success:
        status_code = 403 if res.error_type == "PermissionDenied" else 404
        raise HTTPException(status_code=status_code, detail=res.error or "数据不可用")
    fc = _extract_fc(res.data)
    if not fc:
        raise HTTPException(status_code=404, detail="要素不存在")
    return fc


async def _current_revision(session_id: str, ref_id: str) -> Optional[int]:
    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id)
    rev = descriptor.get("content_revision") if descriptor else None
    return int(rev) if isinstance(rev, (int, float)) else None


@router.post("/layers/data/{ref_id}/selection", tags=["图层数据"])
async def create_selection_handle(
    ref_id: str,
    scope: Dict[str, Any],
    session_id: str = Query(..., min_length=8, max_length=128, description="会话 ID"),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    v: Optional[int] = Query(None, alias="v", description="revision guard（客户端持有的 content_revision）"),
    _conv: Conversation = Depends(require_owned_session),
) -> Response:
    """创建服务端选择集句柄：scope（bbox|where|ids）+ revision 快照 → 不透明 handle。

    返回 count/count_exact（诚实：超精确计数上限时 count=None, count_exact=False）。
    """
    _check_ref_id(ref_id)
    await _layer_data_budget(session_id)
    resolved = await session_data_manager.resolve_alias(session_id, ref_id)
    if resolved:
        ref_id = resolved
    current_revision = await _current_revision(session_id, ref_id)
    if v is not None and current_revision is not None and int(v) != int(current_revision):
        raise HTTPException(
            status_code=409,
            detail={"error": "revision_conflict", "current_revision": current_revision},
        )
    fc = await _authorized_fc(session_id, ref_id, owner_token)
    try:
        entry = await create_selection(
            session_id, ref_id, current_revision, scope, fc.get("features", []))
    except SelectionHandleError as exc:
        _raise_selection_error(exc)
    payload = {
        "handle": entry.handle,
        "count": entry.count,
        "count_exact": entry.count_exact,
        "revision": entry.revision,
        "scope": scope_summary(entry.scope),
        **({"missing_ids": entry.missing_ids} if entry.missing_ids else {}),
        "ttl_seconds": SELECTION_TTL_SECONDS,
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        media_type="application/json",
        headers={"X-Content-Type-Options": "nosniff"},
    )


async def _validated_handle(
    session_id: str, ref_id: str, handle: str, owner_token: Optional[str]
):
    """handle 三重绑定 + revision 一致性 + ref 授权（每次消费都重新鉴权）。"""
    _check_ref_id(ref_id)
    resolved = await session_data_manager.resolve_alias(session_id, ref_id)
    if resolved:
        ref_id = resolved
    try:
        entry = handle_store.get(session_id, ref_id, handle)
    except SelectionHandleError as exc:
        _raise_selection_error(exc)
    current_revision = await _current_revision(session_id, ref_id)
    if (entry.revision is not None and current_revision is not None
            and int(entry.revision) != int(current_revision)):
        # 数据刷新/编辑使 revision 前进：旧 scope 的全集不再可信（A22）
        handle_store.release(session_id, ref_id, handle)
        raise HTTPException(
            status_code=410,
            detail={
                "error": "selection_stale",
                "message": "数据已更新（revision 前进），选择集句柄失效；请重新框选",
                "handle_revision": entry.revision,
                "current_revision": current_revision,
            },
        )
    fc = await _authorized_fc(session_id, ref_id, owner_token)
    return entry, fc


@router.get("/layers/data/{ref_id}/selection/{handle}", tags=["图层数据"])
async def get_selection_handle(
    ref_id: str,
    handle: str,
    session_id: str = Query(..., min_length=8, max_length=128, description="会话 ID"),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
) -> Response:
    """handle 元数据：count/completeness/revision/scope 摘要（不含要素）。"""
    await _layer_data_budget(session_id)
    entry, _fc = await _validated_handle(session_id, ref_id, handle, owner_token)
    payload = {
        "handle": entry.handle,
        "count": entry.count,
        "count_exact": entry.count_exact,
        "revision": entry.revision,
        "scope": scope_summary(entry.scope),
        **({"missing_ids": entry.missing_ids} if entry.missing_ids else {}),
        "ttl_seconds": SELECTION_TTL_SECONDS,
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        media_type="application/json",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/layers/data/{ref_id}/selection/{handle}/features", tags=["图层数据"])
async def get_selection_features_page(
    ref_id: str,
    handle: str,
    session_id: str = Query(..., min_length=8, max_length=128, description="会话 ID"),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    limit: int = Query(200, ge=1, le=1000, description="页大小（要素数）"),
    cursor: Optional[str] = Query(None, max_length=512, description="不透明分页游标"),
    fields: Optional[str] = Query(None, max_length=1024, description="properties 投影 CSV"),
    order: Optional[str] = Query(None, max_length=129, description="单字段排序（ids/AM 拆分 scope 不支持）"),
    _conv: Conversation = Depends(require_owned_session),
) -> Response:
    """handle 全集的稳定序分页 —— 属性表/导出/Agent 的权威枚举通道。"""
    from app.services.feature_pages import parse_fields_param, parse_order_param

    await _layer_data_budget(session_id)
    entry, fc = await _validated_handle(session_id, ref_id, handle, owner_token)
    try:
        field_list = parse_fields_param(fields)
        order_spec = parse_order_param(order)
    except Exception as exc:  # FeaturePageError
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        page = await selection_features_page(
            entry, fc, limit=limit, cursor=cursor, fields=field_list, order=order_spec)
    except SelectionHandleError as exc:
        _raise_selection_error(exc)
    except Exception as exc:  # FeaturePageError（cursor/order 非法等）
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = {
        "type": "FeatureCollection",
        "features": page["features"],
        "pagination": {
            "next_cursor": page.get("next_cursor"),
            "has_more": page.get("has_more", False),
            "limit": limit,
            "returned": page.get("returned", len(page["features"])),
            "truncated": page.get("truncated", False),
            "is_complete": not page.get("has_more", False),
        },
        "selection": {
            "handle": entry.handle,
            "count": entry.count,
            "count_exact": entry.count_exact,
        },
        "revision": entry.revision,
        "reason_codes": page.get("reason_codes", []),
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        media_type="application/json",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.post("/layers/data/{ref_id}/selection/{handle}/materialize", tags=["图层数据"])
async def materialize_selection_handle(
    ref_id: str,
    handle: str,
    session_id: str = Query(..., min_length=8, max_length=128, description="会话 ID"),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
) -> Response:
    """handle → 会话数据 ref（FC 子集）—— 只认 selection_ref 的消费者（field_compute）
    的可靠物化通道；超预算 typed 拒绝，绝不静默截断。"""
    await _layer_data_budget(session_id)
    entry, fc = await _validated_handle(session_id, ref_id, handle, owner_token)

    async def _store(out_fc) -> str:
        return await session_data_manager.store(
            session_id, {"geojson": out_fc}, prefix="selection")

    try:
        result = await materialize_selection(entry, fc, store_call=_store)
    except SelectionHandleError as exc:
        _raise_selection_error(exc)
    return Response(
        content=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        media_type="application/json",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete("/layers/data/{ref_id}/selection/{handle}", tags=["图层数据"])
async def release_selection_handle(
    ref_id: str,
    handle: str,
    session_id: str = Query(..., min_length=8, max_length=128, description="会话 ID"),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
) -> Response:
    """显式释放（幂等；不存在的 handle 也 200 —— 释放不是读）。"""
    _check_ref_id(ref_id)
    resolved = await session_data_manager.resolve_alias(session_id, ref_id)
    if resolved:
        ref_id = resolved
    handle_store.release(session_id, ref_id, handle)
    return Response(
        content=json.dumps({"released": True}, separators=(",", ":")),
        media_type="application/json",
        headers={"X-Content-Type-Options": "nosniff"},
    )
