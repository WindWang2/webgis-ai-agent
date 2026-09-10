"""Spatial Lakehouse dataset REST routes — Versioned Lakehouse V8 (ADR-0130).

dataset 版本层的 REST 面（session 域；owner fail-closed 404 —— 与
lakehouse.py 既有语义一致）。端点：

- POST   ``/lakehouse/datasets``                            注册（幂等）
- GET    ``/lakehouse/datasets/{id}``                       描述 + 指针 + head
- GET    ``/lakehouse/datasets/{id}/versions``              版本历史（有界）
- GET    ``/lakehouse/datasets/{id}/versions/{vid}``        版本解析 + commit
- POST   ``/lakehouse/datasets/{id}/commit``                提交（原子协议）
- POST   ``/lakehouse/datasets/{id}/branches``              开分支
- POST   ``/lakehouse/datasets/{id}/tags``                  不可变 tag
- POST   ``/lakehouse/datasets/{id}/rollback``              revert 式回滚
- GET    ``/lakehouse/datasets/{id}/lineage``               parent 链上溯
- POST   ``/lakehouse/datasets/{id}/retention/plan``        retention dry-run
- POST   ``/lakehouse/datasets/{id}/retention/execute``     prune（token 重验）

安全边界：所有端点先 ``verify_session_owner``（SEC-08），dataset 行
owner 不符一律 404（不泄漏存在性）；typed 错误映射 404/409/400；
服务在线程池以自带 SessionLocal 执行（与 GC 端点同纪律 —— 同步 DAO
只 flush，事务边界在端点内 commit）。
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
from app.api.routes.lakehouse import (
    _client_message,
    _require_session_id,
)
from app.schemas.lakehouse_schema import (
    DatasetBranchRequest,
    DatasetCommitRequest,
    DatasetCreateRequest,
    DatasetRetentionExecuteRequest,
    DatasetRetentionPlanRequest,
    DatasetRollbackRequest,
    DatasetTagRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: typed 错误 → HTTP 映射（404 = 不存在/不可见；409 = 并发/漂移；
#: 其余契约违例 = 400 —— 与 lakehouse 面既有语义一致）。
_DATASET_NOT_FOUND_CODES = ("DATASET_NOT_FOUND", "DATASET_VERSION_NOT_FOUND",
                            "DATASET_CONTENT_UNRESOLVED")
_DATASET_CONFLICT_CODES = ("DATASET_REF_CONFLICT",)


def _dataset_http_from(exc: Exception) -> HTTPException:
    code = getattr(exc, "code", "")
    if code in _DATASET_NOT_FOUND_CODES:
        return HTTPException(status_code=404, detail=_client_message(exc))
    if code in _DATASET_CONFLICT_CODES:
        return HTTPException(status_code=409, detail=_client_message(exc))
    return HTTPException(status_code=400, detail=_client_message(exc))


def _owned_dataset_or_404(sync_db, dataset_id: str, session_id: str):
    """同步守卫链：dataset 行存在 → owner fail-closed（session 域）。"""
    from app.services.lakehouse import dataset_registry as reg

    row = reg.get_dataset_by_descriptor_id(sync_db, dataset_id)
    if row is None or not reg.dataset_owner_allows(row, session_id=session_id):
        raise HTTPException(status_code=404, detail="dataset not found")
    return row


def _call_in_thread(fn, *args, **kwargs):
    """同步服务在线程池执行（自带 SessionLocal —— 与 GC 端点同纪律）。"""
    from app.core.database import SessionLocal

    def _invoke():
        with SessionLocal() as sync_db:
            return fn(sync_db, *args, **kwargs)

    return asyncio.to_thread(_invoke)


@router.post("/lakehouse/datasets")
async def create_lakehouse_dataset(
    req: DatasetCreateRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """注册数据集（描述符 CAS + 台账行；幂等）。"""
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse.dataset_registry import DatasetRegistryError

    def _fn(sync_db):
        row, created = reg.create_dataset(
            sync_db,
            name=req.name,
            description=req.description,
            default_branch=req.default_branch,
            cube_contract=req.cube_contract,
            session_id=str(conv.session_id),
        )
        sync_db.commit()
        return row.to_dict(), created

    try:
        dataset, created = await _call_in_thread(_fn)
    except DatasetRegistryError as e:
        raise _dataset_http_from(e)
    return {"success": True, "created": created, "dataset": dataset}


@router.get("/lakehouse/datasets")
async def list_lakehouse_datasets(
    session_id: str = "",
    limit: int = 50,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """owner 域数据集清单（新→旧，有界 ≤200）。"""
    conv = await verify_session_owner(
        db, _require_session_id(session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    from app.services.lakehouse import dataset_registry as reg

    def _fn(sync_db):
        rows = reg.list_datasets(
            sync_db, session_id=str(conv.session_id), limit=limit,
        )
        return {"datasets": [r.to_dict() for r in rows], "count": len(rows)}

    payload = await _call_in_thread(_fn)
    return {"success": True, **payload}


@router.get("/lakehouse/datasets/{dataset_id}")
async def get_lakehouse_dataset(
    dataset_id: str,
    session_id: str = "",
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """数据集描述 + 指针（branches/tags/head；owner fail-closed 404）。"""
    conv = await verify_session_owner(
        db, _require_session_id(session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    from app.services.lakehouse import dataset_registry as reg

    def _fn(sync_db):
        row = _owned_dataset_or_404(sync_db, dataset_id, str(conv.session_id))
        head = reg.branch_head(sync_db, row.id, row.default_branch)
        return {
            "dataset": row.to_dict(),
            "refs": [r.to_dict() for r in reg.list_refs(sync_db, row.id)],
            "head": head.to_dict() if head is not None else None,
            "descriptor": reg.resolve_dataset_descriptor(row.dataset_id),
        }

    payload = await _call_in_thread(_fn)
    return {"success": True, **payload}


@router.get("/lakehouse/datasets/{dataset_id}/versions")
async def list_lakehouse_dataset_versions(
    dataset_id: str,
    session_id: str = "",
    branch: str = "",
    limit: int = 50,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """版本历史（新→旧，有界；branch 过滤）。"""
    conv = await verify_session_owner(
        db, _require_session_id(session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse.dataset_registry import DatasetRegistryError

    def _fn(sync_db):
        row = _owned_dataset_or_404(sync_db, dataset_id, str(conv.session_id))
        versions = reg.list_versions(
            sync_db, row.id, branch=branch or None, limit=limit,
        )
        return {"versions": [v.to_dict() for v in versions], "count": len(versions)}

    try:
        payload = await _call_in_thread(_fn)
    except DatasetRegistryError as e:
        raise _dataset_http_from(e)
    return {"success": True, **payload}


@router.get("/lakehouse/datasets/{dataset_id}/versions/{version_id}")
async def resolve_lakehouse_dataset_version(
    dataset_id: str,
    version_id: str,
    session_id: str = "",
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """版本解析（内容 manifest 合成；commit record 同步披露）。"""
    conv = await verify_session_owner(
        db, _require_session_id(session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    from app.services.lakehouse import dataset_registry as reg

    def _fn(sync_db):
        row = _owned_dataset_or_404(sync_db, dataset_id, str(conv.session_id))
        resolved = reg.resolve_version(
            sync_db, row, version_id=version_id,
            session_id=str(conv.session_id),
        )
        if resolved is None:
            raise HTTPException(
                status_code=404, detail="dataset version not found")
        resolved["commit"] = reg.resolve_commit_record(version_id)
        return resolved

    payload = await _call_in_thread(_fn)
    return {"success": True, **payload}


@router.post("/lakehouse/datasets/{dataset_id}/commit")
async def commit_lakehouse_dataset_version(
    dataset_id: str,
    req: DatasetCommitRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """提交新版本（原子协议：CAS manifest → 台账行 → 指针 CAS）。"""
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse.dataset_registry import DatasetRegistryError

    def _fn(sync_db):
        row = _owned_dataset_or_404(sync_db, dataset_id, str(conv.session_id))
        result = reg.commit_version(
            sync_db, row,
            branch=req.branch,
            data_object_id=req.data_object_id,
            action=req.action,
            provenance=req.provenance,
            parent_version_id=req.parent_version_id,
            workflow_run_id=req.workflow_run_id,
        )
        sync_db.commit()
        return result

    try:
        result = await _call_in_thread(_fn)
    except DatasetRegistryError as e:
        raise _dataset_http_from(e)
    return {"success": True, **result._asdict()}


@router.post("/lakehouse/datasets/{dataset_id}/branches")
async def create_lakehouse_dataset_branch(
    dataset_id: str,
    req: DatasetBranchRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """开分支（从指定版本或默认分支 head）。"""
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse.dataset_registry import DatasetRegistryError

    def _fn(sync_db):
        row = _owned_dataset_or_404(sync_db, dataset_id, str(conv.session_id))
        ref, created = reg.create_branch(
            sync_db, row, name=req.name,
            from_version_id=req.from_version_id,
        )
        sync_db.commit()
        return ref.to_dict(), created

    try:
        ref, created = await _call_in_thread(_fn)
    except DatasetRegistryError as e:
        raise _dataset_http_from(e)
    return {"success": True, "created": created, "ref": ref}


@router.post("/lakehouse/datasets/{dataset_id}/tags")
async def create_lakehouse_dataset_tag(
    dataset_id: str,
    req: DatasetTagRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """创建不可变 tag（重复 → 409）。"""
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse.dataset_registry import DatasetRegistryError

    def _fn(sync_db):
        row = _owned_dataset_or_404(sync_db, dataset_id, str(conv.session_id))
        ref, created = reg.create_tag(
            sync_db, row, name=req.name, version_id=req.version_id,
        )
        sync_db.commit()
        return ref.to_dict(), created

    try:
        ref, created = await _call_in_thread(_fn)
    except DatasetRegistryError as e:
        raise _dataset_http_from(e)
    return {"success": True, "created": created, "ref": ref}


@router.post("/lakehouse/datasets/{dataset_id}/rollback")
async def rollback_lakehouse_dataset(
    dataset_id: str,
    req: DatasetRollbackRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """revert 式回滚（历史只增不减；head 已在目标 → 400）。"""
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse.dataset_registry import DatasetRegistryError

    def _fn(sync_db):
        row = _owned_dataset_or_404(sync_db, dataset_id, str(conv.session_id))
        result = reg.rollback_branch(
            sync_db, row, branch=req.branch,
            to_version_id=req.to_version_id,
        )
        sync_db.commit()
        return result

    try:
        result = await _call_in_thread(_fn)
    except DatasetRegistryError as e:
        raise _dataset_http_from(e)
    return {"success": True, **result._asdict()}


@router.get("/lakehouse/datasets/{dataset_id}/lineage")
async def get_lakehouse_dataset_lineage(
    dataset_id: str,
    version_id: str,
    session_id: str = "",
    max_depth: int = 64,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """版本血缘（parent 链上溯；截断诚实披露）。"""
    conv = await verify_session_owner(
        db, _require_session_id(session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    from app.services.lakehouse import dataset_registry as reg

    def _fn(sync_db):
        row = _owned_dataset_or_404(sync_db, dataset_id, str(conv.session_id))
        return reg.version_lineage(
            sync_db, row.id, version_id, max_depth=max_depth,
        )

    payload = await _call_in_thread(_fn)
    return {"success": True, **payload}


@router.post("/lakehouse/datasets/{dataset_id}/retention/plan")
async def plan_lakehouse_dataset_retention(
    dataset_id: str,
    req: DatasetRetentionPlanRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """retention dry-run（只读；per-dataset 元数据级 prune 计划）。"""
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse import dataset_retention as ret
    from app.services.lakehouse.dataset_retention import RetentionError

    def _fn(sync_db):
        row = _owned_dataset_or_404(sync_db, dataset_id, str(conv.session_id))
        return ret.plan_retention(
            sync_db, row,
            max_versions=req.max_versions,
            min_age_hours=req.min_age_hours,
        )

    try:
        payload = await _call_in_thread(_fn)
    except RetentionError as e:
        raise _dataset_http_from(e)
    return {"success": True, **payload}


@router.post("/lakehouse/datasets/{dataset_id}/retention/execute")
async def execute_lakehouse_dataset_retention(
    dataset_id: str,
    req: DatasetRetentionExecuteRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db=Depends(get_async_db),
) -> Dict[str, Any]:
    """执行 retention prune（token 重验；漂移 → 409；只删版本行）。"""
    conv = await verify_session_owner(
        db, _require_session_id(req.session_id),
        user_id=_user.get("user_id"), owner_token=owner_token,
    )
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse import dataset_retention as ret
    from app.services.lakehouse.dataset_retention import (
        RetentionError,
        RetentionStalePlan,
    )

    def _fn(sync_db):
        row = _owned_dataset_or_404(sync_db, dataset_id, str(conv.session_id))
        plan = dict(req.plan)
        if str(plan.get("dataset_row_id") or "") != str(row.id):
            raise ret.RetentionError("plan belongs to a different dataset")
        result = ret.execute_retention(sync_db, plan)
        sync_db.commit()
        return result

    try:
        result = await _call_in_thread(_fn)
    except RetentionStalePlan as e:
        raise HTTPException(status_code=409, detail=_client_message(e))
    except RetentionError as e:
        raise _dataset_http_from(e)
    return {"success": True, **result}
