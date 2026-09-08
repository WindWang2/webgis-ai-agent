"""Spatial Lakehouse REST routes — V6 (ADR-0118).

最小生产面：manifest 读取 / 矢量窗口扫描 / cube 构建 / cube 窗口读 /
对象完整性校验。安全边界：

- **所有权**：所有 session 域端点经 ``require_owned_session``（SEC-08 /
  S31 跨租户隔离守卫）；DataObject 校验按请求声明的 owner scope 强制
  （lakehouse.data_object 的 owner 校验 —— 越权 = 404 语义的 typed 错误）；
- **资源边界**：scan max_rows 硬预算（≤200k）；cube 时间步 ≤512；
  分页/游标不在此面（窗口读即分页）。

失败语义：服务层 typed 错误 → 404（不存在/不可见）或 422/400（契约
违例）—— 绝不把失败包装成 200 空结果。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import require_owned_session
from app.schemas.lakehouse_schema import (
    CubeBuildRequest,
    CubeWindowRequest,
    ObjectVerifyRequest,
    VectorScanRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _http_from(exc: Exception, *, not_found_codes: tuple = ()) -> HTTPException:
    code = getattr(exc, "code", "")
    if code in not_found_codes:
        return HTTPException(status_code=404, detail=str(getattr(exc, "message", exc)))
    return HTTPException(status_code=400, detail=str(getattr(exc, "message", exc)))


@router.get("/lakehouse/objects/{data_object_id}")
async def get_lakehouse_object(
    data_object_id: str,
    session_id: str = "",
    project_id: str = "",
    _conv=Depends(require_owned_session),
) -> Dict[str, Any]:
    """DataObject manifest（owner 校验后只读）。"""
    from app.services.lakehouse.data_object import (
        owner_scope_allows,
        resolve_data_object,
    )

    manifest = await asyncio.to_thread(resolve_data_object, data_object_id)
    if manifest is None or not owner_scope_allows(
        manifest, session_id=session_id or None, project_id=project_id or None,
    ):
        raise HTTPException(status_code=404, detail="data object not found")
    return {"success": True, "data_object_id": data_object_id, "manifest": manifest}


@router.post("/lakehouse/vector/scan")
async def scan_lakehouse_vector(
    req: VectorScanRequest,
    _conv=Depends(require_owned_session),
) -> Dict[str, Any]:
    """``ref:fabric-parquet/<id>`` 的 bbox 窗口扫描（row-group 剪枝）。"""
    from app.services.lakehouse.vector_scan import LakehouseScanError, scan_fabric_parquet_ref

    try:
        return await asyncio.to_thread(
            scan_fabric_parquet_ref,
            req.session_id, req.ref, req.bbox,
            columns=req.columns, max_rows=req.max_rows,
        )
    except LakehouseScanError as e:
        raise _http_from(e, not_found_codes=("LAKEHOUSE_REF_MISSING",))


@router.post("/lakehouse/cubes")
async def build_lakehouse_cube(
    req: CubeBuildRequest,
    _conv=Depends(require_owned_session),
) -> Dict[str, Any]:
    """时间片栅格 → 会话 cube（``ref:cube/<id>`` + durable 身份）。"""
    from app.services.lakehouse.cube_service import (
        CubeServiceError,
        build_session_cube,
    )

    try:
        return await build_session_cube(
            req.session_id,
            time_sources=[ts.model_dump() for ts in req.time_sources],
            title=req.title,
            window_side=req.window_side,
        )
    except CubeServiceError as e:
        raise _http_from(e, not_found_codes=("CUBE_SOURCE_MISSING", "CUBE_REF_MISSING"))


@router.post("/lakehouse/cubes/window")
async def read_lakehouse_cube_window(
    req: CubeWindowRequest,
    _conv=Depends(require_owned_session),
) -> Dict[str, Any]:
    """cube 窗口读（zarr chunk 粒度；只触窗口相交 chunk）。"""
    from app.services.lakehouse.cube_service import (
        CubeServiceError,
        read_session_cube_window,
    )

    time_slice = slice(req.time[0], req.time[1]) if req.time else None
    y_slice = slice(req.y[0], req.y[1]) if req.y else None
    x_slice = slice(req.x[0], req.x[1]) if req.x else None
    try:
        result = await read_session_cube_window(
            req.session_id, req.ref, time=time_slice, y=y_slice, x=x_slice,
        )
    except CubeServiceError as e:
        raise _http_from(e, not_found_codes=("CUBE_REF_MISSING",))
    # ndarray → 嵌套 list（JSON 安全；窗口读的量纲已在服务层有界）。
    result["bands"] = {
        band: (data.tolist() if hasattr(data, "tolist") else data)
        for band, data in result["bands"].items()
    }
    return result


@router.post("/lakehouse/objects/{data_object_id}/verify")
async def verify_lakehouse_object(
    data_object_id: str,
    req: ObjectVerifyRequest,
    _conv=Depends(require_owned_session),
) -> Dict[str, Any]:
    """DR 完整性校验（manifest + 全部 content blob 的 digest 校验）。"""
    from app.services.lakehouse.data_object import (
        owner_scope_allows,
        resolve_data_object,
        verify_data_object,
    )

    manifest = await asyncio.to_thread(resolve_data_object, data_object_id)
    if manifest is None or not owner_scope_allows(
        manifest,
        session_id=req.session_id or None,
        project_id=req.project_id or None,
    ):
        raise HTTPException(status_code=404, detail="data object not found")
    state = await asyncio.to_thread(verify_data_object, data_object_id)
    return {"success": True, "data_object_id": data_object_id, "state": state}
