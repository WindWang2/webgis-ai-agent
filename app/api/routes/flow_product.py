from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.flow_product.contracts import FlowProductConfig, FlowProductError
from app.services.flow_product.service import (
    FlowServiceError,
    inspect_flow_source,
    preview,
    query_flow_product,
)

router = APIRouter(prefix="/chat", tags=["对话"])


class FlowInspectPreviewRequest(BaseModel):
    config: FlowProductConfig


class FlowQueryRequest(BaseModel):
    product_ref: str = Field(min_length=8, max_length=200)
    kind: Literal["product", "node", "edge", "members"] = "product"
    object_id: str = Field(default="", max_length=200)
    direction: Literal["inbound", "outbound", "both"] = "both"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, FlowServiceError):
        status = 409 if exc.code in ("FLOW_SOURCE_STALE",) else 404 if exc.code.endswith("_NOT_FOUND") else 400
        raise HTTPException(status_code=status, detail={"error": exc.code, "message": str(exc)}) from exc
    if isinstance(exc, FlowProductError):
        raise HTTPException(status_code=400, detail={"error": "FLOW_INPUT_INVALID", "message": str(exc)}) from exc
    raise exc


@router.post("/sessions/{session_id}/flow-product/inspect")
async def inspect_flow(
    session_id: str,
    req: FlowInspectPreviewRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> dict:
    try:
        return await inspect_flow_source(session_id, req.config)
    except (FlowServiceError, FlowProductError) as exc:
        _raise_http(exc)
    raise AssertionError("unreachable")


@router.post("/sessions/{session_id}/flow-product/preview")
async def preview_flow(
    session_id: str,
    req: FlowInspectPreviewRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> dict:
    try:
        return await preview(session_id, req.config)
    except (FlowServiceError, FlowProductError) as exc:
        _raise_http(exc)
    raise AssertionError("unreachable")


@router.post("/sessions/{session_id}/flow-product/query")
async def query_flow(
    session_id: str,
    req: FlowQueryRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> dict:
    try:
        return await query_flow_product(
            session_id,
            req.product_ref,
            kind=req.kind,
            object_id=req.object_id,
            direction=req.direction,
            offset=req.offset,
            limit=req.limit,
        )
    except (FlowServiceError, FlowProductError) as exc:
        _raise_http(exc)
    raise AssertionError("unreachable")
