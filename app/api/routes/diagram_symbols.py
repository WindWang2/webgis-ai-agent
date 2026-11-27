"""地理锚定统计图符 authoring 路由（GOAL-20261126-04，WP4）。

UI 面板与 Agent 工具共用 ``app/services/diagram_symbol_authoring`` 同一
service / 同一 CAS 链（mapspec_mutations.py:199 同款纪律）：
- GET  inspect：资格事实（只读）；
- POST plan：零突变计划（资格/布局/图例证据）；
- POST apply：mode=preview 零突变 / commit 单笔 CAS 挂块。

会话归属由 ``require_owned_session`` 守卫；拒绝走结构化 diagnostics
（HTTP 422/409 按 code 映射），不泄漏栈。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.services.auth_history_bridge import Conversation, require_owned_session
from app.services.diagram_symbol_authoring import (
    apply_diagram_symbol,
    inspect_diagram_symbol,
    plan_diagram_symbol,
)

router = APIRouter(prefix="/diagram-symbols", tags=["地理锚定统计图符 GOAL-20261126-04"])

#: service 拒绝 code → HTTP 状态（路由只做映射，不重复判断语义）。
_STATUS_BY_CODE = {
    "DIAGRAM.SOURCE.NO_MAPSPEC": 409,
    "DIAGRAM.SOURCE.MISSING": 404,
    "DIAGRAM.SOURCE.NOT_FC": 409,
    "DIAGRAM.SOURCE.REF_UNAVAILABLE": 409,
    "DIAGRAM.SOURCE.UNSUPPORTED_TYPE": 409,
    "DIAGRAM.SOURCE.TOO_LARGE": 413,
    "DIAGRAM.LAYER.NOT_UNIQUE": 404,
    "DIAGRAM.BLOCK.INVALID": 422,
    "DIAGRAM.APPLY.UNKNOWN_MODE": 422,
    "DIAGRAM.APPLY.MISSING_EXPECTED_REVISION": 422,
}


def _fail(diagnostics: list) -> HTTPException:
    first = diagnostics[0] if diagnostics else {}
    status = _STATUS_BY_CODE.get(str(first.get("code")), 422)
    if str(first.get("code", "")).startswith("DIAGRAM.QUALIFICATION."):
        status = 422
    return HTTPException(status_code=status, detail={"diagnostics": diagnostics})


async def _owned(owner_token: Optional[str], conv: Conversation) -> Optional[str]:
    return owner_token or getattr(conv, "owner_token", None)


class PlanBody(BaseModel):
    block: Dict[str, Any] = Field(..., description="diagram_symbol.v1 块")
    layout_policy: Optional[Dict[str, Any]] = Field(default=None)


class ApplyBody(BaseModel):
    block: Optional[Dict[str, Any]] = Field(
        default=None, description="diagram_symbol.v1 块；null=解绑恢复既有表达")
    mode: str = Field("preview", description="preview | commit")
    expected_revision: Optional[int] = Field(default=None, ge=0)
    operation_id: Optional[str] = Field(default=None, max_length=128)
    note: str = Field("", max_length=500)


@router.get("/sessions/{session_id}/layers/{layer_id}/inspect")
async def inspect(
    session_id: str,
    layer_id: str,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    result = await inspect_diagram_symbol(session_id, layer_id)
    if result.get("status") == "rejected":
        raise _fail(result["diagnostics"]) from None
    return result


@router.post("/sessions/{session_id}/layers/{layer_id}/plan")
async def plan(
    session_id: str,
    layer_id: str,
    body: PlanBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    result = await plan_diagram_symbol(
        session_id, layer_id, body.block, layout_policy=body.layout_policy)
    if result.get("status") == "rejected":
        raise _fail(result["diagnostics"]) from None
    return result


@router.post("/sessions/{session_id}/layers/{layer_id}/apply")
async def apply(
    session_id: str,
    layer_id: str,
    body: ApplyBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    result = await apply_diagram_symbol(
        session_id, layer_id, body.block,
        mode=body.mode,
        expected_revision=body.expected_revision,
        operation_id=body.operation_id,
        note=body.note,
        origin="user",
        actor="diagram_symbols_route",
    )
    if result.get("status") == "rejected":
        raise _fail(result["diagnostics"]) from None
    return result
