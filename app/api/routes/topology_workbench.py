"""Topology Workbench（G31）REST 面 —— 新路由文件（不动既有路由签名）。

端点（前缀 /api/v1/topology-workbench，挂载见 app/main.py）：

- ``GET  /rules/catalog``            规则目录（词表/角色/参数形状，单一事实源）；
- ``POST /rulesets``                 创建规则集；``PUT /{id}`` 内容修改 → 新版本；
- ``GET  /rulesets`` / ``/{id}``     列表/详情（created_by 过滤，admin 全见）；
- ``POST /runs``                     提交运行（快照解析 + durable job；幂等键）；
- ``GET  /runs`` / ``/{id}``         运行列表/详情（逐规则维度分离结论 + 完整性）；
- ``GET  /runs/{id}/findings``       问题分页（全量计数披露 + 状态计数）；
- ``GET  /findings/{id}``            问题详情（locator/度量/witness 摘要）；
- ``GET  /findings/{id}/witness``    完整 witness 几何（BlobStore 读回 + stale）；
- ``POST /findings/{id}/exception``  接受例外（用户决定；理由必填）；
- ``DELETE /exceptions/{id}``        撤销例外（finding 回 open）；
- ``GET  /exceptions``               例外列表；
- ``POST /findings/{id}/repair``     候选修复列表（纯建议）；
- ``POST /findings/{id}/repair/preview`` 候选 → G14 预览 plan（不写数据）；
- ``POST /runs/{id}/recheck``        复验（同规则 + 最新源 → resolved 证据）；
- ``POST /runs/{id}/export``         证据 GeoJSON 导出（BlobStore + 回读核验）。

信封/鉴权约定与 data_quality 同面：Page[T] 列表、dict 直返；创建/例外/
运行提交强制认证；读取按 created_by 过滤（admin 覆盖）；session 绑定在
service 层经 verify_session_owner（外来会话 404 不泄露存在性）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.auth import get_current_user, get_current_user_optional
from app.schemas.pagination import Page, clamp_pagination
from app.services.topology_workbench.exceptions_svc import (
    accept_exception,
    list_exceptions,
    recheck_run,
    revoke_exception,
)
from app.services.topology_workbench.repair import (
    build_g14_preview,
    build_repair_candidates,
    export_run_findings_geojson,
)
from app.services.topology_workbench.service import (
    WorkbenchError,
    create_ruleset,
    get_finding,
    get_finding_witness,
    get_ruleset,
    get_run,
    list_findings,
    list_rulesets,
    list_runs,
    submit_run,
    update_ruleset,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/topology-workbench", tags=["跨图层拓扑工作台 G31"])


def _is_admin(user: dict) -> bool:
    return isinstance(user, dict) and user.get("role") == "admin"


def _uid(user: Any) -> Optional[str]:
    if not isinstance(user, dict):
        return None
    uid = user.get("user_id")
    return str(uid) if uid else None


def _map_error(exc: WorkbenchError) -> HTTPException:
    status = {
        "not_owned": 404,
        "finding_not_found": 404,
        "source_unauthorized": 404,
        "source_unresolvable": 410,
        "budget_exceeded": 413,
        "source_changed_after_submission": 409,
        "exception_stale": 409,
    }.get(exc.code, exc.status if isinstance(exc.status, int) else 400)
    return HTTPException(status_code=status, detail={
        "code": exc.code, "message": exc.message, **(exc.detail or {}),
    })



async def _verify_session_stamp(session_id: Optional[str], user: dict) -> None:
    """REST 请求体 session_id 的所有权校验（P1-1：外来会话一律 404）。

    资源行上落一个未验证的 session_id 会让该会话的属主获得资源所有权
    （service._owned 的会话属主兜底）—— 必须在落库前证明当前用户拥有
    该会话（与 feature_edits 的 require_owned_session 同语义）。
    """
    if not session_id:
        return
    from app.services.topology_workbench.service import _verify_session_access

    await _verify_session_access(session_id, user, None)


# ─── 请求模型 ─────────────────────────────────────────────────────────


class RuleIn(BaseModel):
    rule_id: str = Field(min_length=1, max_length=64)
    rule_type: str
    severity: str = "warn"
    enabled: bool = True
    description: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)


class RulesetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    rules: List[RuleIn]
    project_id: Optional[str] = None
    session_id: Optional[str] = None


class RulesetUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    rules: List[RuleIn]


class RoleBinding(BaseModel):
    kind: str  # session_ref | project_artifact | bbox
    ref: Optional[str] = None
    session_id: Optional[str] = None
    project_id: Optional[str] = None
    artifact_id: Optional[str] = None
    revision_no: Optional[int] = None
    bbox: Optional[List[float]] = None
    scope: Optional[Dict[str, Any]] = None
    selection_handle: Optional[str] = None


class RunSubmit(BaseModel):
    ruleset_id: str
    bindings: Dict[str, RoleBinding]
    session_id: Optional[str] = None
    project_id: Optional[str] = None


class ExceptionAccept(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class RepairPreview(BaseModel):
    kind: str
    session_id: str
    ownership_choice: Optional[Dict[str, Any]] = None


class RecheckRequest(BaseModel):
    repair_receipt: Optional[Dict[str, Any]] = None


# ─── 规则目录与规则集 ──────────────────────────────────────────────────


@router.get("/rules/catalog")
def rules_catalog(_user: dict = Depends(get_current_user_optional)) -> dict:
    from app.services.topology_workbench.ruleset import rule_catalog

    return {"success": True, "rules": rule_catalog()}


@router.post("/rulesets")
async def create_ruleset_route(
    body: RulesetCreate, user: dict = Depends(get_current_user)
) -> dict:
    await _verify_session_stamp(body.session_id, user)
    try:
        return {"success": True, "ruleset": create_ruleset(
            name=body.name,
            rules=[r.model_dump() for r in body.rules],
            project_id=body.project_id, session_id=body.session_id,
            created_by=_uid(user), org_id=user.get("org_id"),
        )}
    except WorkbenchError as exc:
        raise _map_error(exc) from exc


@router.get("/rulesets")
def list_rulesets_route(
    limit: Optional[int] = Query(None, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
) -> Page[Dict[str, Any]]:
    limit_n, offset_n = clamp_pagination(limit, offset)
    items, total = list_rulesets(
        project_id=project_id, session_id=session_id,
        actor_user_id=_uid(user), is_admin=_is_admin(user),
        limit=limit_n, offset=offset_n,
    )
    return Page(items=items, total=total, limit=limit_n, offset=offset_n,
                has_more=(offset_n + limit_n) < total)


@router.get("/rulesets/{ruleset_id}")
def get_ruleset_route(
    ruleset_id: str, user: dict = Depends(get_current_user)
) -> dict:
    try:
        return {"success": True, "ruleset": get_ruleset(
            ruleset_id, actor_user_id=_uid(user),
        )}
    except WorkbenchError as exc:
        raise _map_error(exc) from exc


@router.put("/rulesets/{ruleset_id}")
def update_ruleset_route(
    ruleset_id: str, body: RulesetUpdate,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        return {"success": True, "ruleset": update_ruleset(
            ruleset_id, name=body.name,
            rules=[r.model_dump() for r in body.rules],
            actor_user_id=_uid(user),
        )}
    except WorkbenchError as exc:
        raise _map_error(exc) from exc


# ─── 运行 ─────────────────────────────────────────────────────────────


@router.post("/runs")
async def submit_run_route(
    body: RunSubmit, user: dict = Depends(get_current_user)
) -> dict:
    await _verify_session_stamp(body.session_id, user)
    try:
        result = await submit_run(
            ruleset_id=body.ruleset_id,
            bindings={k: v.model_dump(exclude_none=True) for k, v in body.bindings.items()},
            actor_user_id=_uid(user), actor_owner_token=None,
            org_id=user.get("org_id"), project_id=body.project_id,
            session_id=body.session_id,
        )
        return {"success": True, **result}
    except WorkbenchError as exc:
        raise _map_error(exc) from exc


@router.get("/runs")
def list_runs_route(
    limit: Optional[int] = Query(None, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ruleset_id: Optional[str] = None,
    project_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
) -> Page[Dict[str, Any]]:
    limit_n, offset_n = clamp_pagination(limit, offset)
    items, total = list_runs(
        ruleset_id=ruleset_id, project_id=project_id, session_id=session_id,
        actor_user_id=_uid(user), is_admin=_is_admin(user),
        limit=limit_n, offset=offset_n,
    )
    return Page(items=items, total=total, limit=limit_n, offset=offset_n,
                has_more=(offset_n + limit_n) < total)


@router.get("/runs/{run_id}")
def get_run_route(run_id: str, user: dict = Depends(get_current_user)) -> dict:
    try:
        return {"success": True, "run": get_run(
            run_id, actor_user_id=_uid(user), is_admin=_is_admin(user),
        )}
    except WorkbenchError as exc:
        raise _map_error(exc) from exc


@router.get("/runs/{run_id}/findings")
def list_findings_route(
    run_id: str,
    limit: Optional[int] = Query(None, ge=1, le=200),
    offset: int = Query(0, ge=0),
    rule_id: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        limit_n, offset_n = clamp_pagination(limit, offset)
        items, total, counts = list_findings(
            run_id, rule_id=rule_id, severity=severity, status=status,
            actor_user_id=_uid(user), is_admin=_is_admin(user),
            limit=limit_n, offset=offset_n,
        )
        return {
            "success": True,
            "items": items, "total": total,
            "limit": limit_n, "offset": offset_n,
            "has_more": (offset_n + limit_n) < total,
            "status_counts": counts,
            "count_semantics": "total 为该筛选下的全量计数（非当前页数量）",
        }
    except WorkbenchError as exc:
        raise _map_error(exc) from exc


# ─── 问题与例外 ───────────────────────────────────────────────────────


@router.get("/findings/{finding_id}")
def get_finding_route(
    finding_id: str, user: dict = Depends(get_current_user)
) -> dict:
    try:
        return {"success": True, "finding": get_finding(
            finding_id, actor_user_id=_uid(user), is_admin=_is_admin(user),
        )}
    except WorkbenchError as exc:
        raise _map_error(exc) from exc


@router.get("/findings/{finding_id}/witness")
async def get_finding_witness_route(
    finding_id: str, user: dict = Depends(get_current_user)
) -> dict:
    try:
        return {"success": True, **await get_finding_witness(
            finding_id, actor_user_id=_uid(user), is_admin=_is_admin(user),
        )}
    except WorkbenchError as exc:
        raise _map_error(exc) from exc


@router.post("/findings/{finding_id}/exception")
def accept_exception_route(
    finding_id: str, body: ExceptionAccept,
    user: dict = Depends(get_current_user),
) -> dict:
    """接受例外 —— 用户决定面（Agent 工具不提供此操作）。"""
    try:
        return {"success": True, "exception": accept_exception(
            finding_id, reason=body.reason, actor_user_id=_uid(user),
            is_admin=_is_admin(user),
        )}
    except WorkbenchError as exc:
        raise _map_error(exc) from exc


@router.get("/exceptions")
def list_exceptions_route(
    ruleset_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
) -> dict:
    items = list_exceptions(
        ruleset_id=ruleset_id, actor_user_id=_uid(user),
        is_admin=_is_admin(user), limit=limit, offset=offset,
    )
    return {"success": True, "items": items, "limit": limit, "offset": offset}


@router.delete("/exceptions/{exception_id}")
def revoke_exception_route(
    exception_id: str, user: dict = Depends(get_current_user)
) -> dict:
    try:
        return {"success": True, "exception": revoke_exception(
            exception_id, actor_user_id=_uid(user), is_admin=_is_admin(user),
        )}
    except WorkbenchError as exc:
        raise _map_error(exc) from exc


# ─── 修复与复验 ───────────────────────────────────────────────────────


@router.post("/findings/{finding_id}/repair")
def repair_candidates_route(
    finding_id: str, user: dict = Depends(get_current_user)
) -> dict:
    try:
        candidates = build_repair_candidates(
            finding_id, actor_user_id=_uid(user), is_admin=_is_admin(user),
        )
        return {"success": True, **candidates}
    except WorkbenchError as exc:
        raise _map_error(exc) from exc


@router.post("/findings/{finding_id}/repair/preview")
async def repair_preview_route(
    finding_id: str, body: RepairPreview,
    user: dict = Depends(get_current_user),
) -> dict:
    # P2-2：G14 预览会向该会话的 plan store 写入 —— session_id 必须归当前
    # 用户（与 feature_edits 的 require_owned_session 同纪律）。
    await _verify_session_stamp(body.session_id, user)
    try:
        result = await build_g14_preview(
            finding_id, session_id=body.session_id, kind=body.kind,
            ownership_choice=body.ownership_choice,
            actor_user_id=_uid(user), is_admin=_is_admin(user),
        )
        return {"success": True, **result}
    except WorkbenchError as exc:
        raise _map_error(exc) from exc


@router.post("/runs/{run_id}/recheck")
async def recheck_route(
    run_id: str, body: RecheckRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        result = await recheck_run(
            run_id, actor_user_id=_uid(user), actor_owner_token=None,
            is_admin=_is_admin(user), repair_receipt=body.repair_receipt,
        )
        return {"success": True, **result}
    except WorkbenchError as exc:
        raise _map_error(exc) from exc


@router.post("/runs/{run_id}/export")
def export_route(run_id: str, user: dict = Depends(get_current_user)) -> dict:
    try:
        return {"success": True, **export_run_findings_geojson(
            run_id, actor_user_id=_uid(user), is_admin=_is_admin(user),
        )}
    except WorkbenchError as exc:
        raise _map_error(exc) from exc


__all__ = ["router"]
