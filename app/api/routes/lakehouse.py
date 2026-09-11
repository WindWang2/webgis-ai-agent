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
    require_admin,
    verify_session_owner,
)
from app.schemas.lakehouse_schema import (
    CubeBuildRequest,
    CubeRevisionRequest,
    CubeWindowRequest,
    GCExecuteRequest,
    GCPlanRequest,
    LabeledWindowRequest,
    ObjectScrubRequest,
    ObjectVerifyRequest,
    PublishRequest,
    RevokeRequest,
    RSCubeBuildRequest,
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
    # virtual 对象走深度校验（递归 children —— R1-7：普通校验对零 blob
    # 的 virtual 恒 verified 假绿）。
    if manifest.get("kind") == "virtual":
        from app.services.lakehouse.virtual_object import (
            verify_data_object_deep,
        )

        state = await asyncio.to_thread(
            verify_data_object_deep, data_object_id
        )
    else:
        state = await asyncio.to_thread(verify_data_object, data_object_id)
    return {"success": True, "data_object_id": data_object_id, "state": state}

# ── V7（ADR-0119）：遥感 cube / labeled 窗口 / 发布 / catalog / GC ──────


@router.post("/lakehouse/cubes/rs")
async def build_rs_lakehouse_cube(
    req: RSCubeBuildRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """光学/SAR/掩膜多源 → 对齐 labeled cube（不重采样；typed 网格闸）。"""
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    session_id = str(conv.session_id)
    from app.services.lakehouse.rs_cube import RSCubeError, build_rs_cube

    try:
        return await build_rs_cube(
            session_id,
            sources=[s.model_dump() for s in req.sources],
            title=req.title,
        )
    except RSCubeError as e:
        # 网格不一致 = 契约违例 → 400（绝不与"不存在"混淆）。
        raise _http_from(e, not_found_codes=("CUBE_SOURCE_MISSING",))


@router.post("/lakehouse/cubes/labeled/window")
async def read_lakehouse_labeled_window(
    req: LabeledWindowRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """labeled cube 标签级窗口读（计划触达块证据随响应披露）。"""
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    session_id = str(conv.session_id)
    selection: Dict[str, Any] = {}
    for dim in ("time", "band", "polarization", "vertical", "model", "scenario"):
        value = getattr(req, dim)
        if value:
            selection[dim] = list(value)
    if req.bbox:
        selection["bbox"] = list(req.bbox)
    if req.index_slices:
        selection["index_slices"] = {
            str(k): list(v) for k, v in req.index_slices.items()
        }
    if not selection:
        raise HTTPException(
            status_code=422,
            detail="labeled window read requires at least one label/bbox/"
                   "index-slice selection",
        )
    from app.services.lakehouse.cube_service import (
        CubeServiceError,
        read_session_labeled_window,
    )

    try:
        result = await read_session_labeled_window(
            session_id, req.ref, selection=selection, max_cells=req.max_cells,
        )
    except CubeServiceError as e:
        raise _http_from(e, not_found_codes=("CUBE_REF_MISSING",))
    result["variables"] = {
        name: (data.tolist() if hasattr(data, "tolist") else data)
        for name, data in result.get("variables", {}).items()
    }
    return result


@router.post("/lakehouse/publish")
async def publish_lakehouse_objects(
    req: PublishRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """session → project 零字节发布（owner 链校验；幂等）。"""
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    from app.services.lakehouse.project_publish import (
        PublishError,
        publish_to_project,
    )

    try:
        result = await publish_to_project(
            db,
            session_id=str(conv.session_id),
            project_id=req.project_id,
            object_ids=list(req.object_ids),
            actor_id=_user.get("user_id"),
            owner_token=owner_token,
            tags=list(req.tags) if req.tags else None,
        )
    except PublishError as e:
        raise _http_from(
            e, not_found_codes=("LAKEHOUSE_PUBLISH_PROJECT_MISSING",)
        )
    return {"success": True, **result}


@router.post("/lakehouse/revoke")
async def revoke_lakehouse_objects(
    req: RevokeRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """撤销项目内发布（tombstone；既有引用仍可解析）。"""
    from app.services.lakehouse.project_publish import (
        PublishError,
        revoke_project_objects,
    )

    try:
        result = await revoke_project_objects(
            db,
            project_id=req.project_id,
            object_ids=list(req.object_ids),
            actor_id=_user.get("user_id"),
        )
    except PublishError as e:
        raise _http_from(
            e, not_found_codes=("LAKEHOUSE_PUBLISH_PROJECT_MISSING",)
        )
    return {"success": True, **result}


@router.get("/lakehouse/catalog")
async def search_lakehouse_catalog(
    owner_type: str,
    owner_id: str,
    session_id: str = "",
    kind: str = "",
    time_from: str = "",
    time_to: str = "",
    producer: str = "",
    include_revoked: bool = False,
    limit: int = 50,
    offset: int = 0,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """域内检索（双域显式分支 —— 评审 R0-24：session 域走所有权守卫，
    project 域走 publish 同族 owner 校验；均 fail-closed 404）。"""
    import asyncio

    from app.models.project import Project
    from app.services.lakehouse.catalog_service import CatalogError

    if owner_type == "session":
        _ = await verify_session_owner(
            db, _require_session_id(session_id or owner_id),
            user_id=_user.get("user_id"), owner_token=owner_token,
        )
        effective_owner = _require_session_id(session_id or owner_id)
    elif owner_type == "project":
        from sqlalchemy import select

        project = (
            await db.execute(
                select(Project).where(Project.id == owner_id)
            )
        ).scalar_one_or_none()
        actor = _user.get("user_id")
        if project is None or (
            actor is not None and str(project.owner_id) != str(actor)
        ):
            raise HTTPException(status_code=404, detail="project not found")
        effective_owner = owner_id
    else:
        raise HTTPException(status_code=400, detail="owner_type must be session|project")

    def _search_sync():
        from app.core.database import SessionLocal
        from app.services.lakehouse.catalog_service import search_catalog

        with SessionLocal() as sync_db:
            return search_catalog(
                sync_db,
                owner_type=owner_type,
                owner_id=effective_owner,
                kind=kind or None,
                time_from=time_from or None,
                time_to=time_to or None,
                producer=producer or None,
                include_revoked=include_revoked,
                limit=limit,
                offset=offset,
            )

    try:
        return await asyncio.to_thread(_search_sync)
    except CatalogError as e:
        raise _http_from(e)


@router.get("/lakehouse/catalog/stac")
async def lakehouse_catalog_stac(
    owner_type: str,
    owner_id: str,
    session_id: str = "",
    limit: int = 20,
    offset: int = 0,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """STAC 1.0.0 投影（只读；分页 links 有界）。"""
    import asyncio

    from app.models.project import Project

    if owner_type == "session":
        _ = await verify_session_owner(
            db, _require_session_id(session_id or owner_id),
            user_id=_user.get("user_id"), owner_token=owner_token,
        )
        effective_owner = _require_session_id(session_id or owner_id)
    elif owner_type == "project":
        from sqlalchemy import select

        project = (
            await db.execute(select(Project).where(Project.id == owner_id))
        ).scalar_one_or_none()
        actor = _user.get("user_id")
        if project is None or (
            actor is not None and str(project.owner_id) != str(actor)
        ):
            raise HTTPException(status_code=404, detail="project not found")
        effective_owner = owner_id
    else:
        raise HTTPException(status_code=400, detail="owner_type must be session|project")

    def _stac_sync():
        from app.core.database import SessionLocal
        from app.services.lakehouse.catalog_service import search_catalog
        from app.services.lakehouse.catalog_stac import (
            entries_to_stac_collection,
        )

        clamped_limit = min(max(int(limit), 1), 100)
        clamped_offset = max(int(offset), 0)
        with SessionLocal() as sync_db:
            page = search_catalog(
                sync_db, owner_type=owner_type, owner_id=effective_owner,
                limit=clamped_limit, offset=clamped_offset,
            )
            return entries_to_stac_collection(
                page["items"],
                owner_type=owner_type,
                owner_id=effective_owner,
                next_offset=page.get("next_offset"),
                prev_offset=(
                    max(0, clamped_offset - clamped_limit)
                    if clamped_offset
                    else None
                ),
            )

    return await asyncio.to_thread(_stac_sync)


@router.post("/lakehouse/objects/{data_object_id}/scrub")
async def scrub_lakehouse_object(
    data_object_id: str,
    req: ObjectScrubRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """DR scrub（采样/全量 digest 校验 + ETag 记录值比对）。"""
    _reject_project_scope(req.project_id or None)
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id or ""),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    session_id = str(conv.session_id)
    import asyncio

    from app.services.lakehouse.data_object import (
        owner_scope_allows,
        resolve_data_object,
    )
    from app.services.lakehouse.dr import DRVerifyError, scrub_object

    manifest = await asyncio.to_thread(resolve_data_object, data_object_id)
    if manifest is None or not owner_scope_allows(
        manifest, session_id=session_id
    ):
        raise HTTPException(status_code=404, detail="data object not found")

    def _scrub():
        return scrub_object(
            data_object_id, mode=req.mode,
            sample_k=req.sample_k, etag_check=req.etag_check,
        )

    try:
        report = await asyncio.to_thread(_scrub)
    except DRVerifyError as e:
        raise _http_from(e)
    return {"success": True, "data_object_id": data_object_id, **report}


@router.post("/lakehouse/gc/plan")
async def plan_lakehouse_gc(
    req: GCPlanRequest,
    _admin: dict = Depends(require_admin),
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """GC dry-run 计划（只读；plan token + 确定性候选清单）。

    **admin 专用**（评审 R1-2）：GC 作用于全局对象存储与全局 DB 根，
    会话所有权不足以授权 —— 任何租户可借此删除宽限外的他人对象、
    plan 响应也会枚举全局孤儿 id（跨租户泄漏）。
    """
    _ = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    import asyncio

    from app.services.lakehouse.lakehouse_gc import GCError, plan_gc

    try:
        plan = await asyncio.to_thread(
            plan_gc, grace_hours=req.grace_hours,
        )
    except GCError as e:
        raise _http_from(e)
    return {"success": True, **plan}


@router.post("/lakehouse/gc/execute")
async def execute_lakehouse_gc(
    req: GCExecuteRequest,
    _admin: dict = Depends(require_admin),
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """执行 GC 计划（admin 专用 —— 同 plan 的跨租户理由；R1-2）。

    token 重验；漂移 → 409 语义 typed 拒绝。
    """
    _ = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    import asyncio

    from app.services.lakehouse.lakehouse_gc import (
        GCStalePlan,
        execute_gc,
    )

    try:
        result = await asyncio.to_thread(execute_gc, req.plan)
    except GCStalePlan as e:
        raise HTTPException(status_code=409, detail=_client_message(e))
    return {"success": True, **result}

@router.get("/lakehouse/objects/{data_object_id}/lineage")
async def get_lakehouse_object_lineage(
    data_object_id: str,
    session_id: str = "",
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """DataObject 血缘祖先视图（manifest source_refs / virtual children；
    深度/节点双闸）。owner 校验同对象读取。"""
    conv = await verify_session_owner(
        db, _require_session_id(session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    session_id = str(conv.session_id)
    import asyncio

    from app.services.lakehouse.data_object import (
        owner_scope_allows,
        resolve_data_object,
    )
    from app.services.lakehouse.lineage import object_lineage

    manifest = await asyncio.to_thread(resolve_data_object, data_object_id)
    if manifest is None or not owner_scope_allows(
        manifest, session_id=session_id
    ):
        raise HTTPException(status_code=404, detail="data object not found")
    view = await asyncio.to_thread(object_lineage, data_object_id)
    view["success"] = True
    return view

@router.get("/lakehouse/projects/{project_id}/objects/{object_id}")
async def get_project_lakehouse_object(
    project_id: str,
    object_id: str,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """项目域 DataObject 解析（R1-8/R0-9）：catalog ``status=active`` 行
    授权读取 session 出身的 manifest；无授权行 → 404（不泄漏存在性）。
    project owner 校验与 publish 同族。"""
    from sqlalchemy import select

    from app.models.project import Project

    actor = _user.get("user_id")
    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None or (
        actor is not None and str(project.owner_id) != str(actor)
    ):
        raise HTTPException(status_code=404, detail="project not found")
    from app.services.lakehouse.project_publish import resolve_project_object

    resolved = await resolve_project_object(
        db, project_id=project_id, object_id=object_id,
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="data object not found")
    if resolved.get("manifest") is None:
        raise HTTPException(
            status_code=410, detail="data object manifest no longer resolvable",
        )
    return {"success": True, **resolved}
