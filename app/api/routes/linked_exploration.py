"""G46 联合探索 HTTP 面（UI 与 Pi 工具共用 linked_exploration.service）。

会话归属由 ``require_owned_session`` 守卫（G38 同款）；源解析授权在
service 内经 freeze_source 重新复核。错误统一 typed payload。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.linked_exploration import contract, service
from app.services.linked_exploration.errors import (
    LinkedExplorationError,
    http_status,
)

router = APIRouter(prefix="/linked-exploration", tags=["联合探索 G46"])


def _fail(err: LinkedExplorationError) -> HTTPException:
    return HTTPException(status_code=http_status(err), detail=err.payload())


def _actor(conv: Conversation, owner_token: Optional[str],
           session_id: str = "") -> Dict[str, Any]:
    return {
        "user_id": str(getattr(conv, "user_id", "") or "") or None,
        "org_id": getattr(conv, "org_id", None),
        "owner_token": owner_token or getattr(conv, "owner_token", None),
        "trusted_session_id": session_id,
    }


# ─── 请求体 ────────────────────────────────────────────────────────────────

class InspectBody(BaseModel):
    ref: str = Field(min_length=1, max_length=128)


class ChartIn(BaseModel):
    chart_id: str
    kind: str
    field: str
    aggregation: str = "count"
    value_field: Optional[str] = None
    title: str = ""
    bins: int = contract.DEFAULT_BINS
    layer_id: Optional[str] = None


class ConditionIn(BaseModel):
    kind: str
    condition_id: str
    chart_id: str
    field: str
    values: Optional[List[Any]] = None
    include_null: bool = False
    labels: Optional[Dict[str, str]] = None
    gte: Optional[float] = None
    gt: Optional[float] = None
    lte: Optional[float] = None
    lt: Optional[float] = None


class PreviewBody(BaseModel):
    ref: str = Field(min_length=1, max_length=128)
    charts: List[ChartIn] = Field(default_factory=list)
    conditions: List[ConditionIn] = Field(default_factory=list)
    create_handle: bool = True


class SaveViewBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    plan: Dict[str, Any]
    project_id: Optional[str] = None


class UpdateViewBody(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    plan: Optional[Dict[str, Any]] = None
    status: Optional[str] = None
    expected_revision: int = Field(ge=1)


class ApplyBody(BaseModel):
    expected_revision: Optional[int] = Field(default=None, ge=1)


def _chart_model(c: ChartIn) -> contract.ChartSpec:
    try:
        return contract.ChartSpec.model_validate(c.model_dump())
    except Exception as e:
        raise HTTPException(status_code=422, detail={
            "code": "INVALID_CHART", "message": f"图表声明非法：{e}"})


def _condition_model(c: ConditionIn):
    try:
        return contract.parse_condition(c.model_dump(exclude_none=True))
    except LinkedExplorationError as e:
        raise _fail(e)


# ─── inspect / preview ─────────────────────────────────────────────────────

@router.post("/inspect")
async def inspect_source(
    session_id: str,
    body: InspectBody,
    conv: Conversation = Depends(require_owned_session),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> Dict[str, Any]:
    session_id = str(conv.id)
    try:
        return await service.inspect_source(
            session_id, body.ref, _actor(conv, x_session_token, session_id))
    except LinkedExplorationError as e:
        raise _fail(e)


@router.post("/preview")
async def preview_conditions(
    session_id: str,
    body: PreviewBody,
    conv: Conversation = Depends(require_owned_session),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> Dict[str, Any]:
    session_id = str(conv.id)
    charts = [_chart_model(c) for c in body.charts]
    if len(charts) > contract.MAX_CHARTS:
        raise HTTPException(status_code=422, detail={
            "code": "CHARTS_OVER_LIMIT",
            "message": f"图表实例最多 {contract.MAX_CHARTS} 个"})
    conditions = [_condition_model(c) for c in body.conditions]
    if len(conditions) > contract.MAX_CONDITIONS:
        raise HTTPException(status_code=422, detail={
            "code": "CONDITIONS_OVER_LIMIT",
            "message": f"条件最多 {contract.MAX_CONDITIONS} 个"})
    try:
        return await service.preview_conditions(
            session_id, body.ref, charts, conditions,
            _actor(conv, x_session_token, session_id),
            create_handle=body.create_handle)
    except LinkedExplorationError as e:
        raise _fail(e)


# ─── 多源（v3）：计划预览 / 层级钻取 ─────────────────────────────────────

class PlanPreviewBody(BaseModel):
    plan: Dict[str, Any]
    create_handle: bool = True
    selection_ref: Optional[str] = Field(default=None, max_length=32)
    selection_source_id: Optional[str] = Field(default=None, max_length=64)


class DrillBody(BaseModel):
    plan: Dict[str, Any]
    source_id: str = Field(min_length=1, max_length=64)
    direction: str = Field(description="down | up")
    parent_value: Optional[Any] = None
    create_handle: bool = False


@router.post("/plan-preview")
async def preview_plan(
    session_id: str,
    body: PlanPreviewBody,
    conv: Conversation = Depends(require_owned_session),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> Dict[str, Any]:
    """多源联动预览权威面（逐源同代 + 传播键集 + facets + 指标）。"""
    session_id = str(conv.id)
    try:
        return await service.preview_plan(
            session_id, body.plan, _actor(conv, x_session_token, session_id),
            create_handle=body.create_handle,
            selection_ref=body.selection_ref,
            selection_source_id=body.selection_source_id)
    except LinkedExplorationError as e:
        raise _fail(e)


@router.post("/drill")
async def drill_plan(
    session_id: str,
    body: DrillBody,
    conv: Conversation = Depends(require_owned_session),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> Dict[str, Any]:
    """按计划内显式层级做一次钻取/上卷，返回改写后的计划与逐父级计数。"""
    session_id = str(conv.id)
    try:
        return await service.drill_plan(
            session_id, body.plan, _actor(conv, x_session_token, session_id),
            source_id=body.source_id, direction=body.direction,
            parent_value=body.parent_value, create_handle=body.create_handle)
    except LinkedExplorationError as e:
        raise _fail(e)


# ─── 视图 CRUD ─────────────────────────────────────────────────────────────

@router.get("/views")
async def list_views(
    session_id: str,
    conv: Conversation = Depends(require_owned_session),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
    include_archived: bool = Query(default=False),
) -> Dict[str, Any]:
    session_id = str(conv.id)
    views = await service.list_views(
        session_id, _actor(conv, x_session_token, session_id),
        include_archived=include_archived)
    return {"views": views}


@router.post("/views")
async def save_view(
    session_id: str,
    body: SaveViewBody,
    conv: Conversation = Depends(require_owned_session),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> Dict[str, Any]:
    session_id = str(conv.id)
    try:
        return await service.save_view(
            session_id, _actor(conv, x_session_token, session_id),
            title=body.title, plan_raw=body.plan, project_id=body.project_id)
    except LinkedExplorationError as e:
        raise _fail(e)


@router.get("/views/{view_id}")
async def get_view(
    session_id: str,
    view_id: str,
    conv: Conversation = Depends(require_owned_session),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> Dict[str, Any]:
    session_id = str(conv.id)
    try:
        return await service.get_view(
            view_id, session_id, _actor(conv, x_session_token, session_id))
    except LinkedExplorationError as e:
        raise _fail(e)


@router.patch("/views/{view_id}")
async def update_view(
    session_id: str,
    view_id: str,
    body: UpdateViewBody,
    conv: Conversation = Depends(require_owned_session),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> Dict[str, Any]:
    session_id = str(conv.id)
    try:
        return await service.update_view(
            view_id, session_id, _actor(conv, x_session_token, session_id),
            title=body.title, plan_raw=body.plan, status=body.status,
            expected_revision=body.expected_revision)
    except LinkedExplorationError as e:
        raise _fail(e)


@router.delete("/views/{view_id}")
async def delete_view(
    session_id: str,
    view_id: str,
    conv: Conversation = Depends(require_owned_session),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
    expected_revision: int = Query(ge=1),
) -> Dict[str, Any]:
    session_id = str(conv.id)
    try:
        return await service.delete_view(
            view_id, session_id, _actor(conv, x_session_token, session_id),
            expected_revision=expected_revision)
    except LinkedExplorationError as e:
        raise _fail(e)


@router.post("/views/{view_id}/open")
async def open_view(
    session_id: str,
    view_id: str,
    conv: Conversation = Depends(require_owned_session),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> Dict[str, Any]:
    session_id = str(conv.id)
    try:
        return await service.open_view(
            view_id, session_id, _actor(conv, x_session_token, session_id))
    except LinkedExplorationError as e:
        raise _fail(e)


@router.post("/views/{view_id}/apply")
async def apply_view(
    session_id: str,
    view_id: str,
    body: ApplyBody,
    conv: Conversation = Depends(require_owned_session),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> Dict[str, Any]:
    session_id = str(conv.id)
    try:
        return await service.apply_view_to_mapspec(
            view_id, session_id, _actor(conv, x_session_token, session_id),
            expected_revision=body.expected_revision)
    except LinkedExplorationError as e:
        raise _fail(e)


class ExportBody(BaseModel):
    fmt: str = Field(default="svg", pattern="^(svg|png|pdf)$")
    dpi: int = Field(default=150, ge=72, le=400)
    view_id: Optional[str] = None
    plan: Optional[Dict[str, Any]] = None
    title: str = Field(default="", max_length=200)


@router.post("/export")
async def export_exploration(
    session_id: str,
    body: ExportBody,
    conv: Conversation = Depends(require_owned_session),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> Dict[str, Any]:
    """冻结导出：组合条件进图面 + facet 图表统计同范围（SVG/PNG/PDF）。"""
    from app.services.linked_exploration.export import export_exploration as run

    try:
        return await run(
            str(conv.id),
            _actor(conv, x_session_token, session_id),
            fmt=body.fmt, dpi=body.dpi, view_id=body.view_id,
            plan_raw=body.plan, title=body.title)
    except LinkedExplorationError as e:
        raise _fail(e)
