"""session → present_tool_credentials 的生产接线（F06, ADR-0215 候选）.

#1402 的执行闸读 ``present_credentials()`` ContextVar，但生产零授予 ——
声明了 ``requires_credentials`` 的工具永远 typed deny。本模块在 dispatch
chokepoint 的执行作用域内把 **presence-only** 事实授予闸：

- 授予的是「凭证 id 已配置」这一事实（frozenset of ids），**不是 secret**；
  工具取用 secret 仍走其自身的安全通道（env secret manager / vault 适配）。
- 权限**不在**本作用域自动授予：``required_permission`` 的授予保持既有
  user-wins 流（tier3 确认 / plan-approved，plan_mode + chat 路由）；
  env 部署声明的权限仅作 presence 披露（situation 的 ``perm:<p>`` 投影），
  供资格判断解释 —— 绝不绕过显式授权语义。

Kill-switch：``GIS_TOOL_SECURITY_SUPPLY``（默认 ON）。零凭证声明/零 env
配置时本作用域为零开销直通。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Dict, Optional

from app.lib.tool_security import (
    MAX_CREDENTIAL_ENTRIES,
    CredentialPresence,
    resolve_credential_presence,
)

SECURITY_SUPPLY_ENV = "GIS_TOOL_SECURITY_SUPPLY"

_STR_MAX = 64


def _env_truthy(name: str, default: str) -> bool:
    raw = (os.environ.get(name) or default).strip().lower()
    return raw not in ("0", "false", "off", "no")


def security_supply_enabled() -> bool:
    return _env_truthy(SECURITY_SUPPLY_ENV, "1")


class SecurityBridgeView:
    """本次作用域授予的 presence 投影（证据/测试面；无 secret）。"""

    def __init__(
        self,
        presences: Optional[Dict[str, CredentialPresence]] = None,
    ) -> None:
        self.presences: Dict[str, CredentialPresence] = dict(presences or {})

    @property
    def credential_ids(self) -> tuple:
        return tuple(sorted(self.presences.keys())[:MAX_CREDENTIAL_ENTRIES])

    def to_bounded_view(self) -> Dict[str, object]:
        return {
            "credential_ids": list(self.credential_ids),
            "count": len(self.presences),
        }


@contextmanager
def bind_session_credentials(session_id: str = ""):
    """授予当前作用域凭证 presence（``present_tool_credentials`` 生产接线）。

    ``session_id`` 预留给未来 per-session credential store provider（当前
    env provider 是部署级 presence，会话无关）。**授予域不吞异常**：
    try/except 只包 presence 计算，绝不包 ``yield`` —— dispatch 体的
    异常必须原样穿透（否则 contextlib 会以
    ``generator didn't stop after throw()`` 掩盖真实错误）。
    """
    view = SecurityBridgeView()
    ids: tuple = ()
    if security_supply_enabled():
        try:
            from app.tools.registry import present_tool_credentials as _ptc

            view = SecurityBridgeView(resolve_credential_presence())
            ids = view.credential_ids
            _ = _ptc  # import 成功即闸供给可用
        except Exception:  # noqa: BLE001 — presence 计算失败 = 空 view 直通
            view = SecurityBridgeView()
            ids = ()
    if ids:
        from app.tools.registry import present_tool_credentials

        with present_tool_credentials(*ids):
            yield view
    else:
        yield view


__all__ = [
    "SECURITY_SUPPLY_ENV",
    "SecurityBridgeView",
    "security_supply_enabled",
    "bind_session_credentials",
]
