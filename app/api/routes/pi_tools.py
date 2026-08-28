"""Pi tool execution endpoint — thin delegate to PiBridge.

审计 SEC-01：之前此端点完全无认证 —— 任意能到达 API 的攻击者可执行任意
已注册工具（含 create_new_skill = RCE）。现在要求请求方（Pi 扩展的 HTTP
回调）携带与后端共享的密钥，通过 `X-Pi-Bridge-Secret` header 校验。

密钥由后端在启动 Pi subprocess 时注入其环境变量（WEBGIS_BRIDGE_SECRET），
扩展从 `process.env.WEBGIS_BRIDGE_SECRET` 读取并在每次回调时带上。
外部攻击者不知道此密钥，无法调用。

此外，dispatch_tool 内部也复用 /chat/tools/execute 的 tier 校验逻辑
（tier>=3 需 confirm_destructive），防止 Pi 被诱导执行高危工具。
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.agent_pi_bridge import PiToolRequest, PiToolResponse, dispatch_tool

logger = logging.getLogger(__name__)



router = APIRouter(prefix="/pi-tools", tags=["pi-tools"])

# 共享密钥：若环境变量 WEBGIS_BRIDGE_SECRET 未提供，在 DATA_DIR 读写共享 secret 文件，
# 确保多 worker 进程间密钥一致。
# E-2（#893）：实现下沉 app/core/bridge_secret.py（services 层反向
# import 路由层的隐式环收口）；此处 re-export 保持兼容。
from app.core.bridge_secret import get_bridge_secret  # noqa: E402


async def verify_bridge_secret(
    x_pi_bridge_secret: str | None = Header(default=None, alias="X-Pi-Bridge-Secret"),
) -> None:
    """FastAPI 依赖：校验请求方持有共享密钥。

    用 hmac.compare_digest 防时序侧信道。
    """
    secret = get_bridge_secret()
    if not x_pi_bridge_secret or not hmac.compare_digest(x_pi_bridge_secret, secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bridge secret",
        )


@router.post("/execute", response_model=PiToolResponse)
async def execute_tool(
    request: PiToolRequest,
    _secret: None = Depends(verify_bridge_secret),
) -> PiToolResponse:
    """Execute a GIS tool on behalf of the Pi agent.

    Delegates to the PiBridge which owns the ToolRegistry and dispatch logic.
    审计 SEC-01：要求 X-Pi-Bridge-Secret header。
    """
    from app.services.chat.pi_turn_context import verify_turn_token

    verified = verify_turn_token(get_bridge_secret(), request.turnToken or "")
    if verified is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, missing, or expired Pi turn context",
        )
    # Ignore all caller-supplied routing fields. A signature proves who minted
    # the capability; the live-turn check also proves it has not completed or
    # been superseded while still inside its clock validity window.
    session_id = str(verified["session_id"])
    turn_id = str(verified["turn_id"])
    import inspect
    from app.agent_pi_bridge import is_active_pi_turn
    active = is_active_pi_turn(session_id, turn_id)
    if inspect.isawaitable(active):
        active = await active
    if not active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "Pi turn context is no longer active",
                "code": "TURN_CONTEXT_INACTIVE",
                "session_id": session_id,
                "turn_id": turn_id,
                "guidance": (
                    "The turn ownership token has completed or belongs to a different turn. "
                    "In multi-pod deployments, ensure ingress session affinity is enabled so callbacks "
                    "route to the pod holding the active subprocess."
                ),
            },
        )
    request.sessionId = session_id
    request.verifiedTurnId = turn_id
    return await dispatch_tool(request)
