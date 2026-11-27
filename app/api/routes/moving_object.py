"""Moving-object trajectory REST routes (GOAL-20261127-02).

UI 入口：与 Pi 工具面共用同一 service（app/services/moving_object/service.py），
同参同果。所有权经 require_owned_session（ADR-0030 同款依赖）。
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.moving_object.service import (
    TrajectoryProductConfig,
    TrajectoryServiceError,
    apply_trajectory_product_preview,
    inspect_moving_object_source,
    preview_trajectory_product,
    query_trajectory_product,
)

router = APIRouter(prefix="/chat", tags=["对话"])


class TrajectoryInspectRequest(BaseModel):
    dataset: str = Field(min_length=1, max_length=160)
    entity_id_field: str = Field("entity_id", max_length=64)
    time_field: str = Field("timestamp", max_length=64)
    assumed_timezone: str = Field("UTC", max_length=64)


class TrajectoryBuildRequest(TrajectoryProductConfig):
    """与工具面同一 config 契约（service 层单一事实源）。"""


class TrajectoryApplyRequest(BaseModel):
    product_ref: str = Field(min_length=8, max_length=200)
    expected_revision: int = Field(ge=0)
    mutation_id: Optional[str] = Field(None, max_length=96)


class TrajectoryQueryRequest(BaseModel):
    product_ref: str = Field(min_length=8, max_length=200)
    kind: Literal["product", "entity", "dwell", "event"] = "product"
    entity_id: str = Field(default="", max_length=160)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, TrajectoryServiceError):
        if exc.code == "TRAJECTORY_SOURCE_STALE":
            status = 409
        elif exc.code.endswith("_NOT_FOUND"):
            status = 404
        else:
            status = 400
        raise HTTPException(
            status_code=status,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc
    raise exc


@router.post("/sessions/{session_id}/trajectory/inspect")
async def inspect_trajectory(
    session_id: str,
    req: TrajectoryInspectRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> dict:
    try:
        return await inspect_moving_object_source(
            session_id, req.dataset,
            entity_id_field=req.entity_id_field,
            time_field=req.time_field,
            assumed_timezone=req.assumed_timezone,
        )
    except TrajectoryServiceError as exc:
        _raise_http(exc)
    raise AssertionError("unreachable")


@router.post("/sessions/{session_id}/trajectory/build")
async def build_trajectory(
    session_id: str,
    req: TrajectoryBuildRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> dict:
    try:
        return await preview_trajectory_product(session_id, req)
    except TrajectoryServiceError as exc:
        _raise_http(exc)
    raise AssertionError("unreachable")


@router.post("/sessions/{session_id}/trajectory/apply")
async def apply_trajectory(
    session_id: str,
    req: TrajectoryApplyRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> dict:
    try:
        return await apply_trajectory_product_preview(
            session_id, req.product_ref,
            expected_revision=req.expected_revision,
            mutation_id=req.mutation_id,
            origin="user",
        )
    except TrajectoryServiceError as exc:
        _raise_http(exc)
    raise AssertionError("unreachable")


@router.post("/sessions/{session_id}/trajectory/query")
async def query_trajectory(
    session_id: str,
    req: TrajectoryQueryRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> dict:
    try:
        return await query_trajectory_product(
            session_id, req.product_ref,
            kind=req.kind, entity_id=req.entity_id,
            offset=req.offset, limit=req.limit,
        )
    except TrajectoryServiceError as exc:
        _raise_http(exc)
    raise AssertionError("unreachable")
