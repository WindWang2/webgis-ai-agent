"""空间审查/会签 API（ADR-0203）——会话级 proposal 审查面。

auth：与 mapspec_mutations 同款 —— ``require_owned_session``（会话所有权，
匿名 owner_token 兼容）+ ``get_current_user_optional``（actor 身份/角色）。
actor_kind 恒为 "user"（HTTP 面）；agent 作者提案经服务层 seam
（review_service.create_proposal）接入，同一契约同一策略。

feature-off：REVIEW_WORKFLOW_ENABLED=0 → 本路由全部 404（additive 关闭语义）。
错误映射：404 not found / 403 policy / 409 transition+conflict / 429 容量 /
400 其余服务语义；信封统一走 app 级 unified handler（ADR-0138）。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from app.core.auth import (
    actor_ids,
    get_current_user_optional,
    require_owned_session,
)
from app.models.db_model import Conversation
from app.schemas.review_schema import (
    ProposalListResponse,
    ProposalProjectionResponse,
    ReviewCommentRequest,
    ReviewCreateRequest,
    ReviewDecisionRequest,
    ReviewExportResponse,
    ReviewSubmitRequest,
)
from app.services.review.export import export_session
from app.services.review.service import (
    Forbidden,
    InvalidTransition,
    ProposalNotFound,
    ReviewService,
    ReviewServiceError,
)
from app.services.review.store import (
    ReviewStoreCorrupt,
    ReviewStoreFull,
)

router = APIRouter(prefix="/chat", tags=["审查会签"])
review_service = ReviewService()


def _feature_enabled() -> None:
    from app.core.config import settings

    if not getattr(settings, "REVIEW_WORKFLOW_ENABLED", True):
        raise HTTPException(status_code=404, detail="review workflow disabled")


def _review_actor(user: dict):
    from app.schemas.review_schema import ReviewActor

    user_id, _org = actor_ids(user)
    role = str(user.get("role") or "anonymous")
    if role not in ("anonymous", "viewer", "editor", "admin"):
        role = "viewer"
    return ReviewActor(
        actor_id=user_id or "anonymous",
        actor_kind="user",
        role=role,  # type: ignore[arg-type]
    )


async def _run(fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
    """服务异常 → HTTP 语义映射（信封由 app 级 unified handler 统一）。"""
    try:
        return await fn(*args, **kwargs)
    except ProposalNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Forbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ReviewStoreFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ReviewStoreCorrupt as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ReviewServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/sessions/{session_id}/review/proposals",
    response_model=ProposalProjectionResponse,
    status_code=201,
)
async def create_review_proposal(
    session_id: str,
    req: ReviewCreateRequest,
    _conv: Conversation = Depends(require_owned_session),
    user: dict = Depends(get_current_user_optional),
) -> dict[str, Any]:
    _feature_enabled()
    actor = _review_actor(user)

    async def _go() -> dict[str, Any]:
        proposal = await review_service.create_proposal(
            session_id,
            author=actor,
            title=req.title,
            description=req.description,
            intents=req.mutation_intents,
        )
        return await review_service.get_proposal_projection(
            session_id, proposal.proposal_id,
        )

    return await _run(_go)


@router.get(
    "/sessions/{session_id}/review/proposals",
    response_model=ProposalListResponse,
)
async def list_review_proposals(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
    _user: dict = Depends(get_current_user_optional),
) -> dict[str, Any]:
    _feature_enabled()

    async def _go() -> dict[str, Any]:
        proposals = await review_service.list_proposals(session_id)
        return {
            "session_id": session_id,
            "proposals": [
                jsonable_encoder(p.model_dump(mode="json")) for p in proposals
            ],
        }

    return await _run(_go)


@router.get(
    "/sessions/{session_id}/review/proposals/{proposal_id}",
    response_model=ProposalProjectionResponse,
)
async def get_review_proposal(
    session_id: str,
    proposal_id: str,
    _conv: Conversation = Depends(require_owned_session),
    _user: dict = Depends(get_current_user_optional),
) -> dict[str, Any]:
    _feature_enabled()
    return await _run(review_service.get_proposal_projection, session_id, proposal_id)


@router.post(
    "/sessions/{session_id}/review/proposals/{proposal_id}/submit",
    response_model=ProposalProjectionResponse,
)
async def submit_review_proposal(
    session_id: str,
    proposal_id: str,
    req: ReviewSubmitRequest,
    _conv: Conversation = Depends(require_owned_session),
    user: dict = Depends(get_current_user_optional),
) -> dict[str, Any]:
    _feature_enabled()
    actor = _review_actor(user)

    async def _go() -> dict[str, Any]:
        await review_service.submit(
            session_id, proposal_id, actor, req.base_revision,
        )
        return await review_service.get_proposal_projection(session_id, proposal_id)

    return await _run(_go)


@router.post(
    "/sessions/{session_id}/review/proposals/{proposal_id}/comments",
    response_model=ProposalProjectionResponse,
)
async def add_review_comment(
    session_id: str,
    proposal_id: str,
    req: ReviewCommentRequest,
    _conv: Conversation = Depends(require_owned_session),
    user: dict = Depends(get_current_user_optional),
) -> dict[str, Any]:
    _feature_enabled()
    actor = _review_actor(user)

    async def _go() -> dict[str, Any]:
        await review_service.add_comment(
            session_id, proposal_id, actor, req.body, req.anchor,
        )
        return await review_service.get_proposal_projection(session_id, proposal_id)

    return await _run(_go)


@router.post(
    "/sessions/{session_id}/review/proposals/{proposal_id}/decisions",
    response_model=ProposalProjectionResponse,
)
async def record_review_decision(
    session_id: str,
    proposal_id: str,
    req: ReviewDecisionRequest,
    _conv: Conversation = Depends(require_owned_session),
    user: dict = Depends(get_current_user_optional),
) -> dict[str, Any]:
    _feature_enabled()
    actor = _review_actor(user)

    async def _go() -> dict[str, Any]:
        await review_service.record_decision(
            session_id, proposal_id, actor, req.decision, req.reason,
        )
        return await review_service.get_proposal_projection(session_id, proposal_id)

    return await _run(_go)


@router.post(
    "/sessions/{session_id}/review/proposals/{proposal_id}/merge",
    response_model=ProposalProjectionResponse,
)
async def merge_review_proposal(
    session_id: str,
    proposal_id: str,
    _conv: Conversation = Depends(require_owned_session),
    user: dict = Depends(get_current_user_optional),
) -> dict[str, Any]:
    _feature_enabled()
    actor = _review_actor(user)

    async def _go() -> dict[str, Any]:
        proposal, outcome = await review_service.merge(session_id, proposal_id, actor)
        projection = await review_service.get_proposal_projection(
            session_id, proposal.proposal_id,
        )
        # 合并回执携带 outcome（冲突/交错/回滚语义的机器可读反馈）。
        projection["merge_outcome"] = {
            "ok": outcome.ok,
            "conflict": outcome.conflict,
            "interleaved": outcome.interleaved,
            "rolled_back": outcome.rolled_back,
            "failure": outcome.failure,
        }
        return projection

    return await _run(_go)


@router.post(
    "/sessions/{session_id}/review/proposals/{proposal_id}/rebase",
    response_model=ProposalProjectionResponse,
)
async def rebase_review_proposal(
    session_id: str,
    proposal_id: str,
    _conv: Conversation = Depends(require_owned_session),
    user: dict = Depends(get_current_user_optional),
) -> dict[str, Any]:
    _feature_enabled()
    actor = _review_actor(user)

    async def _go() -> dict[str, Any]:
        await review_service.rebase(session_id, proposal_id, actor)
        return await review_service.get_proposal_projection(session_id, proposal_id)

    return await _run(_go)


@router.post(
    "/sessions/{session_id}/review/proposals/{proposal_id}/withdraw",
    response_model=ProposalProjectionResponse,
)
async def withdraw_review_proposal(
    session_id: str,
    proposal_id: str,
    _conv: Conversation = Depends(require_owned_session),
    user: dict = Depends(get_current_user_optional),
) -> dict[str, Any]:
    _feature_enabled()
    actor = _review_actor(user)

    async def _go() -> dict[str, Any]:
        await review_service.withdraw(session_id, proposal_id, actor)
        return await review_service.get_proposal_projection(session_id, proposal_id)

    return await _run(_go)


@router.post(
    "/sessions/{session_id}/review/proposals/{proposal_id}/supersede",
    response_model=ProposalProjectionResponse,
)
async def supersede_review_proposal(
    session_id: str,
    proposal_id: str,
    _conv: Conversation = Depends(require_owned_session),
    user: dict = Depends(get_current_user_optional),
) -> dict[str, Any]:
    _feature_enabled()
    actor = _review_actor(user)

    async def _go() -> dict[str, Any]:
        await review_service.supersede(session_id, proposal_id, actor)
        return await review_service.get_proposal_projection(session_id, proposal_id)

    return await _run(_go)


@router.get(
    "/sessions/{session_id}/review/export",
    response_model=ReviewExportResponse,
)
async def export_review_records(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
    _user: dict = Depends(get_current_user_optional),
) -> dict[str, Any]:
    """审计导出：allowlist 投影（无凭据/无 CoT 字段），整会话可 diff。"""
    _feature_enabled()

    async def _go() -> dict[str, Any]:
        proposals = await review_service.list_proposals(session_id)
        return export_session(proposals, session_id)

    return await _run(_go)
