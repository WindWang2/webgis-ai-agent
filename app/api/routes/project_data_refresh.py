"""G10 项目数据刷新 REST（薄入口，复用 project_runtime 的三重权限门）。

端点全部在 ``/api/v1/projects/{project_id}/data-refresh`` 之下：
  POST   /check                     检查来源变化（首次建立基线）
  GET    /sources                   来源版本清单
  POST   /preview                   只读影响预览 → proposal
  GET    /proposals                 提案清单（刷新后恢复）
  GET    /proposals/{id}            提案详情
  GET    /proposals/{id}/status     候选进度（恢复语义）
  GET    /proposals/{id}/explain    解释某图为何过期
  POST   /proposals/{id}/execute    执行隔离候选 / 取消
  GET    /proposals/{id}/compare    候选与当前对比
  POST   /proposals/{id}/adopt      原子 CAS 采纳
  POST   /proposals/{id}/discard    放弃候选（释放 GC pin）
  POST   /proposals/{id}/rollback   回退（新 revision / 既有 resume）

权限：项目鉴权（跨 owner 一律 404 不泄露存在性）→ 会话所有权
（写面）→ 服务侧 owner 二道守卫；客户端传来的 owner/approved/verified
字段一概不作权威（请求模型 extra=forbid）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.routes.project_runtime import _auth_project, _verify_session_access
from app.core.auth import actor_ids, get_current_user, get_owner_token
from app.core.database import get_db
from app.schemas.project_data_refresh_schema import (
    AdoptRefreshRequest,
    CheckUpdatesRequest,
    ExecuteRefreshRequest,
    ExecuteRefreshResponse,
    PreviewRefreshRequest,
    ProposalListResponse,
    StatusResponse,
    reject_non_finite_numbers,
)
from app.services.project_runtime.refresh.contracts import DiffBudget
from app.services.project_runtime.refresh.records import (
    AdoptionReceipt,
    CheckReceipt,
    RefreshProposal,
)
from app.services.project_runtime.refresh.service import (
    ProjectDataRefreshService,
    RefreshError,
)

router = APIRouter(prefix="/projects", tags=["Project Data Refresh (G10)"])


def _svc() -> ProjectDataRefreshService:
    return ProjectDataRefreshService()


def _http_error(exc: RefreshError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "detail": exc.detail},
    )


async def _guard(
    *, project_id: str, session_id: str, db: Session,
    user: Dict[str, Any], owner_token: Optional[str],
) -> None:
    await _auth_project(db, project_id, user)
    if session_id:
        await _verify_session_access(session_id, user, owner_token)


async def _guard_proposal(
    *, project_id: str, proposal_id: str, db: Session,
    user: Dict[str, Any], owner_token: Optional[str],
) -> RefreshProposal:
    """R1-P2：所有读差异摘要 / 写项目状态的端点统一三重门。

    项目鉴权 → 服务侧 owner 二道守卫 → 会话所有权（撤权窗口内不得凭
    proposal 里存的 session_id 继续操作会话 MapSpec）。
    """
    await _auth_project(db, project_id, user)
    proposal = await _svc().get_proposal(
        project_id=project_id, proposal_id=proposal_id, user_id=_user_id(user))
    if proposal.session_id:
        await _verify_session_access(proposal.session_id, user, owner_token)
    return proposal


def _user_id(user: Dict[str, Any]) -> str:
    uid, _org = actor_ids(user)
    return str(uid or "")


def _org_id(user: Dict[str, Any]) -> str:
    _uid, org = actor_ids(user)
    return str(org or "")


@router.post("/{project_id}/data-refresh/check", response_model=CheckReceipt)
async def check_updates(
    project_id: str,
    req: CheckUpdatesRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> CheckReceipt:
    """手动「检查更新」（默认手动；本轮无后台定时器/自动抓取）。"""
    await _guard(project_id=project_id, session_id=req.session_id, db=db,
                 user=user, owner_token=owner_token)
    try:
        return await _svc().check_updates(
            db, project_id=project_id, session_id=req.session_id,
            user_id=_user_id(user), establish_baseline=req.establish_baseline,
        )
    except RefreshError as exc:
        raise _http_error(exc) from exc


@router.get("/{project_id}/data-refresh/sources")
async def list_sources(
    project_id: str,
    session_id: str = "",
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Dict[str, Any]:
    """来源版本清单（逻辑身份 / 内容版本 / 证据级别 / 五态）。"""
    await _guard(project_id=project_id, session_id=session_id, db=db,
                 user=user, owner_token=owner_token)
    try:
        receipt = await _svc().check_updates(
            db, project_id=project_id, session_id=session_id,
            user_id=_user_id(user), establish_baseline=False,
        )
        return receipt.inventory.model_dump(mode="json")
    except RefreshError as exc:
        raise _http_error(exc) from exc


@router.post("/{project_id}/data-refresh/preview", response_model=RefreshProposal)
async def preview_refresh(
    project_id: str,
    req: PreviewRefreshRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> RefreshProposal:
    """只读影响预览：不写 MapSpec / 数据集 / 版本 / 基线，只落 proposal 账本。"""
    await _guard(project_id=project_id, session_id=req.session_id, db=db,
                 user=user, owner_token=owner_token)
    if req.dag is not None:
        try:
            reject_non_finite_numbers(req.dag)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return await _svc().preview(
            db, project_id=project_id, session_id=req.session_id,
            user_id=_user_id(user), org_id=_org_id(user),
            style_only=req.style_only, dag=req.dag,
            budget=DiffBudget(max_features=req.max_features, max_bytes=req.max_bytes),
        )
    except RefreshError as exc:
        raise _http_error(exc) from exc


@router.get("/{project_id}/data-refresh/proposals", response_model=ProposalListResponse)
async def list_proposals(
    project_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
) -> ProposalListResponse:
    """有界提案清单（刷新页面后恢复候选/进度的入口）。"""
    await _auth_project(db, project_id, user)
    items = await _svc().list_proposals(project_id=project_id, owner_id=_user_id(user))
    return ProposalListResponse(project_id=project_id, count=len(items), items=items)


@router.get("/{project_id}/data-refresh/proposals/{proposal_id}",
            response_model=RefreshProposal)
async def get_proposal(
    project_id: str, proposal_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> RefreshProposal:
    await _guard_proposal(project_id=project_id, proposal_id=proposal_id, db=db, user=user, owner_token=owner_token)
    try:
        return await _svc().get_proposal(
            project_id=project_id, proposal_id=proposal_id, user_id=_user_id(user))
    except RefreshError as exc:
        raise _http_error(exc) from exc


@router.get("/{project_id}/data-refresh/proposals/{proposal_id}/status",
            response_model=StatusResponse)
async def proposal_status(
    project_id: str, proposal_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> StatusResponse:
    await _guard_proposal(project_id=project_id, proposal_id=proposal_id, db=db, user=user, owner_token=owner_token)
    try:
        data = await _svc().status(
            project_id=project_id, proposal_id=proposal_id, user_id=_user_id(user))
        return StatusResponse(**data)
    except RefreshError as exc:
        raise _http_error(exc) from exc


@router.get("/{project_id}/data-refresh/proposals/{proposal_id}/explain")
async def explain_stale(
    project_id: str, proposal_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Dict[str, Any]:
    """Pi / UI 解释「为什么更新、哪些结果过期、哪些不可判定」。"""
    await _guard_proposal(project_id=project_id, proposal_id=proposal_id, db=db, user=user, owner_token=owner_token)
    try:
        return await _svc().explain_stale(
            project_id=project_id, proposal_id=proposal_id, user_id=_user_id(user))
    except RefreshError as exc:
        raise _http_error(exc) from exc


@router.post("/{project_id}/data-refresh/proposals/{proposal_id}/execute",
             response_model=ExecuteRefreshResponse)
async def execute_candidate(
    project_id: str, proposal_id: str,
    req: ExecuteRefreshRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> ExecuteRefreshResponse:
    """执行获授权的隔离候选（或取消）；失败/取消不污染当前成果。"""
    await _guard_proposal(project_id=project_id, proposal_id=proposal_id, db=db, user=user, owner_token=owner_token)
    try:
        result = await _svc().execute(
            db, project_id=project_id, proposal_id=proposal_id,
            user_id=_user_id(user), cancel=req.cancel,
        )
        conflicts = result.get("conflicts") or []
        return ExecuteRefreshResponse(
            candidate=result.get("candidate"),
            replayed=bool(result.get("replayed")),
            conflicts=[c.model_dump() if hasattr(c, "model_dump") else dict(c)
                       for c in conflicts],
            preserved=list(result.get("preserved") or []),
            receipt=dict(result.get("receipt") or {}),
        )
    except RefreshError as exc:
        raise _http_error(exc) from exc


@router.get("/{project_id}/data-refresh/proposals/{proposal_id}/compare")
async def compare_candidate(
    project_id: str, proposal_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Dict[str, Any]:
    await _guard_proposal(project_id=project_id, proposal_id=proposal_id, db=db, user=user, owner_token=owner_token)
    try:
        return await _svc().compare(
            project_id=project_id, proposal_id=proposal_id, user_id=_user_id(user))
    except RefreshError as exc:
        raise _http_error(exc) from exc


@router.post("/{project_id}/data-refresh/proposals/{proposal_id}/adopt",
             response_model=AdoptionReceipt)
async def adopt_candidate(
    project_id: str, proposal_id: str,
    req: AdoptRefreshRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> AdoptionReceipt:
    """原子 CAS 采纳；客户端声明的 revision/指纹只作提示，服务端双核对。"""
    await _guard_proposal(project_id=project_id, proposal_id=proposal_id, db=db, user=user, owner_token=owner_token)
    try:
        proposal = await _svc().get_proposal(
            project_id=project_id, proposal_id=proposal_id, user_id=_user_id(user))
        if req.proposal_fingerprint and \
                req.proposal_fingerprint != proposal.proposal_fingerprint:
            raise RefreshError("proposal_fingerprint_mismatch",
                               "client fingerprint differs from authoritative proposal")
        if req.expected_revision is not None and \
                int(req.expected_revision) != int(proposal.mapspec_revision):
            raise RefreshError("expected_revision_mismatch",
                               "client revision differs from authoritative proposal")
        return await _svc().adopt(
            db, project_id=project_id, proposal_id=proposal_id,
            user_id=_user_id(user),
        )
    except RefreshError as exc:
        raise _http_error(exc) from exc


@router.post("/{project_id}/data-refresh/proposals/{proposal_id}/discard",
             response_model=AdoptionReceipt)
async def discard_candidate(
    project_id: str, proposal_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> AdoptionReceipt:
    await _guard_proposal(project_id=project_id, proposal_id=proposal_id, db=db, user=user, owner_token=owner_token)
    try:
        return await _svc().discard(
            project_id=project_id, proposal_id=proposal_id, user_id=_user_id(user))
    except RefreshError as exc:
        raise _http_error(exc) from exc


@router.post("/{project_id}/data-refresh/proposals/{proposal_id}/rollback",
             response_model=AdoptionReceipt)
async def rollback_candidate(
    project_id: str, proposal_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> AdoptionReceipt:
    """回退：新 revision（forward revert）或既有 runtime snapshot resume。"""
    await _guard_proposal(project_id=project_id, proposal_id=proposal_id, db=db, user=user, owner_token=owner_token)
    try:
        return await _svc().rollback(
            project_id=project_id, proposal_id=proposal_id,
            user_id=_user_id(user), db=db,
        )
    except RefreshError as exc:
        raise _http_error(exc) from exc


__all__ = ["router"]
