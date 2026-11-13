"""G34 纸面版式工作台 HTTP 面（UI 与 Pi 工具**共用**同一 service）。

新文件优先：不侵入 Studio / mapspec_mutations / atlas 既有路由。
所有权守卫 ``require_owned_session`` + ``X-Session-Token`` 与 G20 同款；
所有写经 service → ``apply_gis_mutation(SetLayoutIntent)`` 单一 CAS 链。

预览返回完整 SVG 供 UI 纸面画布使用（≤8MB 有界）；Pi 工具走
``preview(include_svg=False)`` 只拿有界回执，不把像素送进模型上下文。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.print_layout_authoring import service
from app.services.print_layout_authoring.service import PrintLayoutServiceError

router = APIRouter(prefix="/print-layout", tags=["纸面版式工作台"])


def _raise(payload: Dict[str, Any]) -> None:
    if payload.get("success"):
        return
    code = str(payload.get("code") or "PRINT_LAYOUT_FAILED")
    status = 503 if code in ("SOURCE_UNAVAILABLE", "PNG_RENDERER_UNAVAILABLE",
                             "VECTOR_PDF_UNAVAILABLE") else 409 if code in (
        "SUPERSEDED", "MISSING_EXPECTED_REVISION", "PRINT_LAYOUT_LOCKED_COMPONENT",
        "MUTATION_REJECTED") else 422 if code.startswith("PRINT_LAYOUT") or code in (
        "INVALID_PROPOSAL", "ATLAS_PLAN_FAILED", "UNSUPPORTED_FORMAT",
        "TEXT_OVERFLOW_BLOCKED", "PRINT_LEGEND_INCOMPLETE") else 400
    raise HTTPException(status_code=status, detail=payload)


class _SessionBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)


class ProposeBody(_SessionBody):
    operations: List[Dict[str, Any]] = Field(default_factory=list, max_length=32)
    initialize: bool = False
    dpi: int = Field(default=300, ge=72, le=600)


class ApplyBody(ProposeBody):
    expected_revision: int = Field(ge=0)
    client_mutation_id: str = Field(default="", max_length=128)


class PreviewBody(_SessionBody):
    page_id: str = Field(default="", max_length=128)
    dpi: int = Field(default=300, ge=72, le=600)
    include_svg: bool = True


class ExportBody(_SessionBody):
    format: str = Field(default="pdf", pattern="^(pdf|svg|png)$")
    dpi: int = Field(default=300, ge=72, le=600)
    page_id: str = Field(default="", max_length=128)


class TemplateBody(_SessionBody):
    action: str = Field(pattern="^(save|apply|scope|reset)$")
    template_id: str = Field(default="", max_length=128)
    name: str = Field(default="", max_length=128)
    page_id: str = Field(default="", max_length=128)
    paper_profile: str = Field(default="a4_landscape", max_length=32)
    components: Optional[List[Dict[str, Any]]] = None
    placeholders: Optional[List[str]] = None
    expected_revision: Optional[int] = Field(default=None, ge=0)
    initialize: bool = False


async def _call(coro: Any) -> Any:
    """把 ``PrintLayoutServiceError`` 映射成 typed HTTP（503/400/409）。

    ``load_mapspec`` 对空会话抛 ``SOURCE_UNAVAILABLE``；不接的话 FastAPI 会
    回 500，把「会话还没提交过 MapSpec」误报成服务端故障。
    """
    try:
        return await coro
    except PrintLayoutServiceError as ex:
        _raise(ex.to_dict())


@router.get("/capabilities")
async def capabilities(
    session_id: str = Query(..., min_length=1, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    return await _call(service.capabilities())


@router.get("/inspect")
async def inspect(
    session_id: str = Query(..., min_length=1, max_length=128),
    sync_planner: bool = Query(False),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    return await _call(service.inspect(session_id, sync_planner=sync_planner))


@router.get("/pages")
async def pages(
    session_id: str = Query(..., min_length=1, max_length=128),
    driver: str = Query("frames", pattern="^(frames|category|feature|time|product)$"),
    page_budget: int = Query(20, ge=1, le=20),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    from app.lib.cartography.publication_ir import AtlasPolicy

    atlas = AtlasPolicy(driver=driver, page_budget=page_budget)
    try:
        return await service.prepare_pages(session_id, atlas)
    except PrintLayoutServiceError as ex:
        _raise(ex.to_dict())


@router.post("/propose")
async def propose(
    body: ProposeBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    return await _call(service.propose(body.session_id, body.model_dump()))


@router.post("/apply")
async def apply(
    body: ApplyBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    payload = body.model_dump()
    result = await _call(service.apply(
        body.session_id, payload,
        expected_revision=body.expected_revision, origin="user",
        actor="ui:print-layout-workbench"))
    _raise(result)
    return result


@router.post("/preview")
async def preview(
    body: PreviewBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    result = await _call(service.preview(
        body.session_id, page_id=body.page_id, dpi=body.dpi,
        include_svg=body.include_svg))
    _raise(result)
    return result


@router.post("/export")
async def export(
    body: ExportBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    result = await _call(service.submit_export(
        body.session_id, fmt=body.format, dpi=body.dpi, page_id=body.page_id))
    _raise(result)
    return result


@router.post("/templates")
async def templates(
    body: TemplateBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    proposal = {k: v for k, v in body.model_dump().items()
                if v is not None and k not in ("session_id", "action")}
    result = await _call(service.templates(body.session_id, body.action, proposal))
    _raise(result)
    return result
