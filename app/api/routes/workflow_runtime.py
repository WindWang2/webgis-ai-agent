"""Workflow Runtime V5 REST（additive；架构 §11）。

包注册/发布、实例生命周期（实例化/执行/取消/变更/投影/解释）。

安全语义（与 geocompute 路由同一纪律）：
- 全部端点强制认证（get_current_user）；owner 域经 ``owner_scope_for``
  哈希派生，读路径按 owner 过滤（他人实例/包 → 404，不泄漏存在性）；
- session 资源写路径经 ``authorize_session_write``（陌生会话 404）；
- 执行（run）同步核心统一 to_thread / asyncio 卸载，不阻塞事件循环；
  deadline 默认 60s 有界，超时 typed 错误（不挂 HTTP）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import get_current_user
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import service as SV
from app.services.workflow_runtime import projection as PR
from app.services.workflow_runtime.registry import PackageConflict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflow-runtime", tags=["Workflow Runtime V5"])

_MAX_QUERY_CHARS = 400
_MAX_CHANGES = C.MAX_APPLY_CHANGES


class PackageRegisterRequest(BaseModel):
    query: str = Field(min_length=1, max_length=_MAX_QUERY_CHARS)
    recipe_id: str = Field(default="", max_length=64)
    project_id: str = Field(default="", max_length=255)
    profile: Optional[Dict[str, Any]] = None


class PublishRequest(BaseModel):
    version: str = Field(min_length=1, max_length=16)


class InstantiateRequest(BaseModel):
    package_id: str = Field(min_length=1, max_length=64)
    version: str = Field(default="", max_length=16)
    session_id: str = Field(default="", max_length=255)
    project_id: str = Field(default="", max_length=255)


class ChangeIn(BaseModel):
    dimension: str = Field(min_length=1, max_length=24)
    target_kind: str = Field(min_length=1, max_length=24)
    target: str = Field(default="", max_length=64)
    detail: str = Field(default="", max_length=200)


class ChangesRequest(BaseModel):
    changes: List[ChangeIn] = Field(min_length=1, max_length=_MAX_CHANGES)
    dry_run: bool = False


class RunRequest(BaseModel):
    deadline_s: float = Field(default=60.0, gt=0, le=300.0)


_MAX_PROFILE_BYTES = 32_768
_MAX_PROFILE_DEPTH = 8


def _check_profile(profile: Optional[Dict[str, Any]]) -> None:
    """profile 输入形状界（R2-m-4）：深嵌套/超界 → 422 而非 500。"""
    if not profile:
        return
    try:
        text = __import__("json").dumps(profile, default=str)
    except (TypeError, ValueError, RecursionError):
        raise HTTPException(status_code=422, detail="invalid profile")
    if len(text) > _MAX_PROFILE_BYTES:
        raise HTTPException(status_code=422, detail="profile too large")

    def _depth(x: Any, d: int = 0) -> int:
        if d > _MAX_PROFILE_DEPTH:
            return d
        if isinstance(x, dict):
            return max([_depth(v, d + 1) for v in x.values()] or [d])
        if isinstance(x, list):
            return max([_depth(v, d + 1) for v in x] or [d])
        return d

    if _depth(profile) > _MAX_PROFILE_DEPTH:
        raise HTTPException(status_code=422, detail="profile too deep")


def _owner(user: Dict[str, Any], session_id: str = "") -> str:
    return SV.owner_scope_for(user, session_id or None)


def _svc() -> SV.WorkflowRuntimeService:
    return SV.get_service()


def _http(code: str, status: int, detail: str = "") -> HTTPException:
    return HTTPException(status_code=status, detail=f"{code}: {detail}"[:300])


def _not_found():
    return HTTPException(status_code=404, detail="not found")


# ── 包 ───────────────────────────────────────────────────────────────────

@router.post("/packages/register")
async def register_package(
    body: PackageRegisterRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    svc = _svc()
    owner = _owner(user)
    _check_profile(body.profile)
    try:
        result = await asyncio.to_thread(
            svc.compile_and_register, body.query, owner_scope=owner,
            recipe_id=body.recipe_id, profile=body.profile,
            project_id=body.project_id)
    except SV.WorkflowRuntimeError as e:
        raise _http(e.code, 422, e.detail)
    except (ValueError, RecursionError) as e:
        # 编译器预算守卫（包体积等）→ 422 输入界（R2-m-4）
        raise _http("COMPILE_INPUT_REJECTED", 422, str(e)[:200])
    except PackageConflict as e:
        # 脱敏（R2-B1）：不回指纹前缀 —— 48bit 指纹确认 oracle 可被用于
        # 猜测受害者 query 文本。
        raise _http("PACKAGE_CONFLICT", 409,
                    f"{e.package_id}@{e.version} already registered "
                    "with different content")
    return {"success": True, **result}


@router.post("/packages/{package_id}/publish")
async def publish_package(
    package_id: str, body: PublishRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    svc = _svc()
    row = await asyncio.to_thread(
        svc.registry.publish, package_id, body.version,
        owner_scope=_owner(user))
    if row is None:
        raise _not_found()
    return {"success": True, "package": row}


@router.get("/packages")
async def list_packages(user: Dict[str, Any] = Depends(get_current_user)):
    rows = await asyncio.to_thread(
        _svc().registry.list_packages, owner_scope=_owner(user))
    return {"success": True, "packages": rows}


@router.get("/packages/{package_id}/versions")
async def package_versions(
    package_id: str, user: Dict[str, Any] = Depends(get_current_user),
):
    rows = await asyncio.to_thread(
        _svc().registry.list_versions, package_id, owner_scope=_owner(user))
    if not rows:
        raise _not_found()
    return {"success": True, "versions": rows}


# ── 实例 ─────────────────────────────────────────────────────────────────

@router.post("/instances")
async def create_instance(
    body: InstantiateRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    svc = _svc()
    owner = _owner(user, body.session_id)
    try:
        inst = await asyncio.to_thread(
            svc.instantiate, body.package_id, owner_scope=owner,
            session_id=body.session_id, version=body.version,
            project_id=body.project_id)
    except SV.WorkflowRuntimeError as e:
        status = 404 if e.code == "PACKAGE_NOT_FOUND" else 422
        raise _http(e.code, status, e.detail)
    nodes = await asyncio.to_thread(svc.store.get_nodes,
                                    inst["instance_id"])
    return {"success": True,
            "instance": PR.instance_projection(inst, nodes)}


@router.post("/instances/{instance_id}/run")
async def run_instance(
    instance_id: str, body: RunRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    svc = _svc()
    owner = _owner(user)
    try:
        return await svc.run_instance(
            instance_id, owner_scope=owner, caller=user,
            deadline_s=body.deadline_s)
    except SV.InstanceBusy as e:
        raise _http(e.code, 409, e.detail)
    except SV.WorkflowRuntimeError as e:
        raise _http(e.code, 404 if e.code == "INSTANCE_NOT_FOUND" else 422,
                    e.detail)


@router.post("/instances/{instance_id}/cancel")
async def cancel_instance(
    instance_id: str, user: Dict[str, Any] = Depends(get_current_user),
):
    try:
        return await _svc().cancel_instance(instance_id, owner_scope=_owner(user))
    except SV.WorkflowRuntimeError as e:
        raise _http(e.code, 404, e.detail)


@router.post("/instances/{instance_id}/changes")
async def apply_changes(
    instance_id: str, body: ChangesRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    svc = _svc()
    owner = _owner(user)
    changes = [C.PendingChange(
        dimension=c.dimension, target_kind=c.target_kind,
        target=c.target, detail=c.detail, source="api")
        for c in body.changes]
    if body.dry_run:
        return await svc.recompute_plan(
            instance_id, changes, owner_scope=owner)
    try:
        return await svc.apply_changes(
            instance_id, changes, owner_scope=owner)
    except SV.InstanceBusy as e:
        raise _http(e.code, 409, e.detail)
    except SV.WorkflowRuntimeError as e:
        raise _http(e.code, 404, e.detail)


@router.get("/instances/{instance_id}")
async def get_instance(
    instance_id: str, user: Dict[str, Any] = Depends(get_current_user),
):
    svc = _svc()
    owner = _owner(user)
    inst = await asyncio.to_thread(
        svc.store.get_instance, instance_id, owner)
    if inst is None:
        raise _not_found()
    nodes = await asyncio.to_thread(svc.store.get_nodes, instance_id)
    package = await asyncio.to_thread(
        svc.registry.resolve, inst["package_id"], owner_scope=owner,
        version=inst["package_version"])
    proj = PR.instance_projection(inst, nodes, package=package)
    proj["explain"] = PR.explain(proj["nodes"],
                                 inst.get("decisions") or [])
    return {"success": True, "instance": proj}


@router.get("/instances/{instance_id}/recompute-plan")
async def dry_run_plan(
    instance_id: str, user: Dict[str, Any] = Depends(get_current_user),
):
    """GET 变体（无变更输入）—— 返回当前实例的复用/stale 全景。"""
    svc = _svc()
    owner = _owner(user)
    inst = await asyncio.to_thread(
        svc.store.get_instance, instance_id, owner)
    if inst is None:
        raise _not_found()
    nodes = await asyncio.to_thread(svc.store.get_nodes, instance_id)
    proj = PR.instance_projection(inst, nodes)
    return {"success": True,
            "instance_id": instance_id,
            "stale": proj["counts"].get("STALE", 0),
            "counts": proj["counts"],
            "decisions": proj["decisions"]}


@router.get("/instances")
async def list_instances(user: Dict[str, Any] = Depends(get_current_user)):
    rows = await asyncio.to_thread(
        _svc().store.list_owner_instances, _owner(user))
    return {"success": True, "instances": rows}
