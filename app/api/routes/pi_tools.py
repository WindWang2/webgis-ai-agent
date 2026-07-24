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
import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.agent_pi_bridge import PiToolRequest, PiToolResponse, dispatch_tool

router = APIRouter(prefix="/pi-tools", tags=["pi-tools"])

# 共享密钥：启动时生成（每个进程唯一），注入 Pi subprocess env。
# 也可通过环境变量 WEBGIS_BRIDGE_SECRET 固定（多副本部署需固定）。
_BRIDGE_SECRET = os.getenv("WEBGIS_BRIDGE_SECRET") or secrets.token_urlsafe(32)


def get_bridge_secret() -> str:
    """返回当前 bridge 共享密钥（供 PiBridge.start 注入 subprocess env）。"""
    return _BRIDGE_SECRET


async def verify_bridge_secret(
    x_pi_bridge_secret: str | None = Header(default=None, alias="X-Pi-Bridge-Secret"),
) -> None:
    """FastAPI 依赖：校验请求方持有共享密钥。

    用 hmac.compare_digest 防时序侧信道。
    """
    if not x_pi_bridge_secret or not hmac.compare_digest(x_pi_bridge_secret, _BRIDGE_SECRET):
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
    return await dispatch_tool(request)
