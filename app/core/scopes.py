"""OAuth scopes 词汇表与角色映射（ADR-0139，foundation/security-tenancy-v9 P3）。

三层模型：

- **词汇表（封闭词表，append-only）**：``SCOPES`` —— 端点 scope 矩阵
  （``docs/dev/endpoint-scope-matrix.csv``）的唯一合法取值域，CI 校验
  （tests/test_endpoint_scope_matrix.py）保证 100% 覆盖 + 词表内。
- **角色 → 默认 scope 集**：既有 viewer/editor/admin 三角色平滑映射
  （``ROLE_SCOPES``）——不发新 token 的存量用户立刻获得等价权限面；
  JWT ``scopes`` claim 缺席时一律按角色默认集回退（back-compat）。
- **匿名最小集**：owner_token 会话隐式持有 ``ANON_SCOPES``（公开读 +
  自有会话读写），org 隔离桶内由既有 owner 过滤收口。

require_scope 与 require_admin 的关系：require_admin 仍是 admin 面
的权威守卫（role 实时读 DB）；require_scope 是细粒度声明层 —— 矩阵
记录每个端点的**意图 scope**，二者可在同一端点叠加。
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, status

# ── 封闭词表（append-only；新增必须同步矩阵生成器与 ADR-0139）────────
SCOPES: frozenset[str] = frozenset({
    "public:read",        # 匿名可读（liveness/version/静态面）
    "auth:write",         # register / login / refresh / logout
    "session:read",       # 会话/消息读（owner 域内）
    "session:write",      # 会话/消息写（owner 域内）
    "projects:read",
    "projects:write",
    "gis:read",           # layers / maps / exports / 数据面读
    "gis:write",
    "lakehouse:read",
    "lakehouse:write",
    "geocompute:read",
    "geocompute:submit",  # plan execute / cluster submit
    "geocompute:admin",   # cluster workers / stuck / ledger admin 面
    "jobs:read",
    "jobs:write",
    "workflow:read",
    "workflow:write",
    "diag:read",          # status/detailed 等认证后诊断面
    "admin:read",
    "admin:write",
    "metrics:read",
    "extensions:read",
    "extensions:write",
})

# ── 角色兼容映射（viewer/editor/admin → 默认 scope 集）────────────────
_VIEWER_SCOPES = frozenset({
    "public:read", "session:read", "session:write",
    "projects:read", "gis:read", "lakehouse:read",
    "geocompute:read", "jobs:read", "workflow:read",
    "diag:read", "extensions:read",
})
_EDITOR_SCOPES = _VIEWER_SCOPES | {
    "projects:write", "gis:write", "lakehouse:write",
    "geocompute:submit", "jobs:write", "workflow:write",
    "extensions:write",
}
_ADMIN_SCOPES = _EDITOR_SCOPES | {
    "admin:read", "admin:write", "metrics:read", "geocompute:admin",
}

ROLE_SCOPES: dict[str, frozenset[str]] = {
    "viewer": _VIEWER_SCOPES,
    "editor": _EDITOR_SCOPES,
    "admin": _ADMIN_SCOPES,
}

#: 匿名 owner_token 会话的隐式最小集（P3 任务书语义）。
ANON_SCOPES: frozenset[str] = frozenset({
    "public:read", "session:read", "session:write",
})

#: scopes claim 的线格式（空格分隔，RFC 6749 风格）。
CLAIM_KEY = "scopes"


def scopes_for_role(role: Optional[str]) -> frozenset[str]:
    """角色 → 默认 scope 集；未知角色按 viewer（最小特权）。"""
    return ROLE_SCOPES.get(role or "", _VIEWER_SCOPES)


def parse_scopes(payload_claim: Optional[str], role: Optional[str]) -> frozenset[str]:
    """JWT scopes claim → 有效集；claim 缺席按角色默认集回退。

    claim 内的未知词直接丢弃（防越权词注入；词汇表封闭性由 CI 保证）。
    """
    if not payload_claim:
        return scopes_for_role(role)
    granted = frozenset(
        s for s in str(payload_claim).split() if s in SCOPES
    )
    # claim 与角色集取交集基线：角色被降级时旧 claim 的宽词不复活。
    return granted & scopes_for_role(role)


def has_scope(user: Optional[dict], scope: str) -> bool:
    """auth 依赖 dict（含 ``scopes`` 集）上的单 scope 判定。"""
    if not user:
        return False
    granted = user.get("scopes")
    if isinstance(granted, frozenset):
        return scope in granted
    return scope in parse_scopes(None, user.get("role"))


def require_scope(scope: str):
    """FastAPI 依赖工厂：要求当前用户持有 ``scope``。

    基于 ``get_current_user_with_version``（ver 实时校验，logout 即时
    生效）。scope 缺失 → 403 ``INSUFFICIENT_SCOPE``（401 留给未认证，
    分类学与既有错误面一致）。词汇表外的 scope 是编程错误 → 直接抛。
    """
    if scope not in SCOPES:
        raise ValueError(f"require_scope: unknown scope {scope!r} "
                         "(not in ADR-0139 vocabulary)")

    from app.core.auth import get_current_user_with_version

    async def _dependency(user: dict = Depends(get_current_user_with_version)) -> dict:
        if not has_scope(user, scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "INSUFFICIENT_SCOPE", "scope": scope},
            )
        return user

    return _dependency
