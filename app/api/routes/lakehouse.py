"""Spatial Lakehouse REST routes — V6 (ADR-0118).

最小生产面：manifest 读取 / 矢量窗口扫描 / cube 构建 / cube 窗口读 /
cube 修订 / 对象完整性校验。安全边界：

- **所有权**：session 域端点对**请求中实际生效的 session_id**（POST 的
  body 字段 / GET 的 query 字段）做 ``verify_session_owner`` 校验
  （SEC-08 / S31 跨租户隔离守卫）。调用方身份经依赖注入显式传入守卫
  （``get_current_user_optional`` 的 JWT 身份 + ``get_owner_token`` 的
  ``X-Session-Token``）—— 守卫没有身份时对所有 owned 会话 fail-closed
  （404），路由不匿名可用（同 ``require_owned_session`` 的接线语义）。
  POST 端点**不**使用 ``require_owned_session`` 依赖 —— 它解析的是 query
  参数，会与 body 的 session_id 脱钩（review B1 跨租户访问）；守卫与
  业务操作必须绑定同一 session 标识。``project_id`` 在 REST 面显式拒绝
  （项目域发布通道尚不存在，见 ADR-0118 non-goals）。
- **资源边界**：scan max_rows 硬预算（≤200k）；cube 时间步 ≤512；cube
  窗口读必须至少给出一个有限切片且元素非负（防全 cube OOM）；错误
  消息对客户端脱敏（不含服务器路径）。

失败语义：服务层 typed 错误 → 404（不存在/不可见）或 422/400（契约
违例）—— 绝不把失败包装成 200 空结果。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import (
    get_async_db,
    get_current_user_optional,
    get_owner_token,
    verify_session_owner,
)
from app.schemas.lakehouse_schema import (
    CubeBuildRequest,
    CubeRevisionRequest,
    CubeWindowRequest,
    ObjectVerifyRequest,
    VectorScanRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _client_message(exc: Exception) -> str:
    """错误消息脱敏：不向 REST 客户端泄露服务器路径/内部细节。"""
    import os

    from app.core.config import settings

    text = str(getattr(exc, "message", exc))[:256]
    for secret in (
        str(settings.DATA_DIR),
        os.getcwd(),
        str(settings.PROJECT_ARTIFACT_CONTENT_DIR or ""),
    ):
        if secret:
            text = text.replace(secret, "<server>")
    return text


def _http_from(exc: Exception, *, not_found_codes: tuple = ()) -> HTTPException:
    code = getattr(exc, "code", "")
    status = 404 if code in not_found_codes else 400
    return HTTPException(status_code=status, detail=_client_message(exc))


def _require_session_id(session_id: Optional[str]) -> str:
    """空 session_id 的显式 400（GET query 默认空串 / 可选 body 字段的兜底）。"""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    return session_id


def _reject_project_scope(project_id: Optional[str]) -> None:
    if project_id:
        raise HTTPException(
            status_code=400,
            detail="project-scoped lakehouse objects are not exposed via REST yet",
        )


@router.get("/lakehouse/objects/{data_object_id}")
async def get_lakehouse_object(
    data_object_id: str,
    session_id: str = "",
    project_id: str = "",
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """DataObject manifest（owner 校验后只读）。"""
    _reject_project_scope(project_id or None)
    conv = await verify_session_owner(
        db, _require_session_id(session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    session_id = str(conv.session_id)
    from app.services.lakehouse.data_object import (
        owner_scope_allows,
        resolve_data_object,
    )

    manifest = await asyncio.to_thread(resolve_data_object, data_object_id)
    if manifest is None or not owner_scope_allows(manifest, session_id=session_id):
        raise HTTPException(status_code=404, detail="data object not found")
    return {"success": True, "data_object_id": data_object_id, "manifest": manifest}


@router.post("/lakehouse/vector/scan")
async def scan_lakehouse_vector(
    req: VectorScanRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """``ref:fabric-parquet/<id>`` 的 bbox 窗口扫描（row-group 剪枝）。"""
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    session_id = str(conv.session_id)
    from app.services.lakehouse.vector_scan import LakehouseScanError, scan_fabric_parquet_ref

    try:
        return await asyncio.to_thread(
            scan_fabric_parquet_ref,
            session_id, req.ref, req.bbox,
            columns=req.columns, max_rows=req.max_rows,
        )
    except LakehouseScanError as e:
        raise _http_from(e, not_found_codes=("LAKEHOUSE_REF_MISSING",))


@router.post("/lakehouse/cubes")
async def build_lakehouse_cube(
    req: CubeBuildRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """时间片栅格 → 会话 cube（``ref:cube/<id>`` + durable 身份）。"""
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    session_id = str(conv.session_id)
    from app.services.lakehouse.cube_service import (
        CubeServiceError,
        build_session_cube,
    )

    try:
        return await build_session_cube(
            session_id,
            time_sources=[ts.model_dump() for ts in req.time_sources],
            title=req.title,
            window_side=req.window_side,
        )
    except CubeServiceError as e:
        raise _http_from(e, not_found_codes=("CUBE_SOURCE_MISSING", "CUBE_REF_MISSING"))


@router.post("/lakehouse/cubes/window")
async def read_lakehouse_cube_window(
    req: CubeWindowRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """cube 窗口读（zarr chunk 粒度；至少一个有限切片，防全 cube 读取）。"""
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    session_id = str(conv.session_id)
    if req.time is None and req.y is None and req.x is None:
        raise HTTPException(
            status_code=422,
            detail="cube window read requires at least one bounded slice "
                   "(time/y/x) — whole-cube reads are refused",
        )
    for name, pair in (("time", req.time), ("y", req.y), ("x", req.x)):
        if pair is not None and (pair[0] < 0 or pair[1] < 0):
            raise HTTPException(
                status_code=422, detail=f"{name} slice must be non-negative"
            )
    from app.services.lakehouse.cube_service import (
        CubeServiceError,
        read_session_cube_window,
    )

    time_slice = slice(req.time[0], req.time[1]) if req.time else None
    y_slice = slice(req.y[0], req.y[1]) if req.y else None
    x_slice = slice(req.x[0], req.x[1]) if req.x else None
    try:
        result = await read_session_cube_window(
            session_id, req.ref, time=time_slice, y=y_slice, x=x_slice,
        )
    except CubeServiceError as e:
        raise _http_from(e, not_found_codes=("CUBE_REF_MISSING",))
    # ndarray → 嵌套 list（JSON 安全；读量由切片预算约束）。
    result["bands"] = {
        band: (data.tolist() if hasattr(data, "tolist") else data)
        for band, data in result["bands"].items()
    }
    return result


@router.post("/lakehouse/cubes/revise")
async def revise_lakehouse_cube(
    req: CubeRevisionRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """cube 修订（硬链接 CoW fork：源 store 逐字节不动，新不可变修订）。"""
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    session_id = str(conv.session_id)
    from app.services.lakehouse.cube_service import (
        CubeServiceError,
        revise_session_cube,
    )

    try:
        return await revise_session_cube(
            session_id, req.ref,
            updates=[u.model_dump() for u in req.updates],
            title=req.title,
        )
    except CubeServiceError as e:
        raise _http_from(e, not_found_codes=("CUBE_REF_MISSING",))


@router.post("/lakehouse/objects/{data_object_id}/verify")
async def verify_lakehouse_object(
    data_object_id: str,
    req: ObjectVerifyRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """DR 完整性校验（manifest + 全部 content blob 的 digest 校验）。"""
    _reject_project_scope(req.project_id or None)
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id or ""),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    session_id = str(conv.session_id)
    from app.services.lakehouse.data_object import (
        owner_scope_allows,
        resolve_data_object,
        verify_data_object,
    )

    manifest = await asyncio.to_thread(resolve_data_object, data_object_id)
    if manifest is None or not owner_scope_allows(manifest, session_id=session_id):
        raise HTTPException(status_code=404, detail="data object not found")
    state = await asyncio.to_thread(verify_data_object, data_object_id)
    return {"success": True, "data_object_id": data_object_id, "state": state}
