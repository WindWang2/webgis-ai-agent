#!/usr/bin/env python3
"""端点 scope 矩阵生成器（ADR-0139 P3/P5；tests/test_endpoint_scope_matrix.py）。

从 ``app.main.app`` 的 router 树枚举全部 HTTP 端点，推导
``docs/dev/endpoint-scope-matrix.csv``（列：method,path,scope,auth_admin,
auth_user）。矩阵 diff 即权限面变更的评审面；CI 校验三重保证
（100% 覆盖 / 词表封闭 / 无陈旧行）由 tests/test_endpoint_scope_matrix.py
强制，字节闸（--check）由 quality preflight 级联调用。

推导规则（优先级自上而下，命中即停）：

1. **显式声明**：端点直接依赖 ``require_scope("X")`` 闭包 → ``X``
   （闭包 cell 中命名词表 ``app.core.scopes.SCOPES`` 的字符串；
   参见 security_admin.py 的 admin 面）。
2. **require_admin**：端点直接依赖 ``require_admin`` → ``admin:{read|
   write}``（按方法；config / metrics / geocompute cluster / 根级
   /health 等权威守卫面）。
3. **OVERRIDES**：推导歧义项按「版本归一化路径 + 方法」钉住
   （v1/v2 共享后缀键，一条覆盖两版）。
4. **域路径规则（DOMAIN_RULES）**：版本归一化路径的首段 →
   ``(read_scope, write_scope)``（GET → read，其余 → write）或
   固定 scope；v1/v2 天然共享同一后缀规则。

flags 与 scope 同源、取端点**直接**依赖（不递归展开 require_* 的
内嵌依赖）：

- ``auth_admin=Y`` ⇔ 直接依赖含 ``require_admin``；
- ``auth_user=Y`` ⇔ 直接依赖含 ``get_current_user{,_optional,
  _with_version}``。

确定性：路由枚举走 FastAPI router 树（FastAPI 0.140+ 的
_IncludedRouter 不平铺进 app.routes）；排序键 ``(path, method)``
字典序。词汇表外 scope 或无规则可套的端点直接 fail-loud
（新端点必须显式声明或补规则，禁止静默默认值）。

用法::

    python scripts/generate_endpoint_scope_matrix.py           # 原地刷新矩阵
    python scripts/generate_endpoint_scope_matrix.py --check   # 字节闸（不一致 exit 1）
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUTPUT = REPO / "docs" / "dev" / "endpoint-scope-matrix.csv"

HEADER = ("method", "path", "scope", "auth_admin", "auth_user")

# ── 版本归一化（v1/v2 共享后缀规则）────────────────────────────────────

_VERSION_PREFIXES = ("/api/v1", "/api/v2")


def normalize_path(path: str) -> str:
    """剥掉 ``/api/v1`` / ``/api/v2`` 前缀（v2 是 v1 router 的重挂载面）。"""
    for prefix in _VERSION_PREFIXES:
        if path == prefix:
            return "/"
        if path.startswith(prefix + "/"):
            return path[len(prefix):]
    return path


# ── 推导规则表（规则要点见模块 docstring；新歧义项 → OVERRIDES）────────

#: 推导歧义项：版本归一化路径 + 方法 → scope（钉住，防推导漂移）。
OVERRIDES: dict[tuple[str, str], str] = {
    # /auth/me 走会话语义而非 auth 域（登录态自省，非认证动作）
    ("/auth/me", "GET"): "session:read",
    # drift-check 是 plan 校验器的 admin 投影（非提交动作）
    ("/geocompute/plans/drift-check", "POST"): "geocompute:admin",
}

#: 域路径规则：版本归一化路径首段 → (read, write) 或固定 scope。
DOMAIN_RULES: dict[str, tuple[str, str] | str] = {
    "admin": ("admin:read", "admin:write"),
    "auth": ("auth:read", "auth:write"),
    "chat": ("session:read", "session:write"),
    "config": ("admin:read", "admin:write"),
    "data-fabric": ("gis:read", "gis:write"),
    "data-lifecycle": ("gis:read", "gis:write"),
    "data-quality": ("gis:read", "gis:write"),
    "explorer": ("gis:read", "gis:write"),
    "export": ("gis:read", "gis:write"),
    "extensions": ("extensions:read", "extensions:write"),
    # geocompute 的写动作是「提交」而非「改配置」
    "geocompute": ("geocompute:read", "geocompute:submit"),
    "health": "public:read",
    "healthz": "public:read",
    "knowledge": ("gis:read", "gis:write"),
    "lakehouse": ("lakehouse:read", "lakehouse:write"),
    "layer-types": ("gis:read", "gis:write"),
    "layers": ("gis:read", "gis:write"),
    "local-data": ("gis:read", "gis:write"),
    "metrics": "metrics:read",
    "pi-tools": ("session:read", "session:write"),
    "projects": ("projects:read", "projects:write"),
    "ready": "public:read",
    "reports": ("gis:read", "gis:write"),
    "sessions": ("session:read", "session:write"),
    "static": ("gis:read", "gis:write"),
    "status": ("gis:read", "gis:write"),
    "tasks": ("jobs:read", "jobs:write"),
    "templates": ("gis:read", "gis:write"),
    "upload": ("gis:read", "gis:write"),
    "uploads": ("gis:read", "gis:write"),
    "version": "public:read",
    "workflow-runtime": ("workflow:read", "workflow:write"),
}

#: 直接依赖中的用户鉴权函数（qualname 匹配；嵌套依赖不计 —— require_*
#: 内部都包着 get_current_user_with_version，递归会把所有行都标 Y）。
_USER_DEP_QUALNAMES = frozenset({
    "get_current_user",
    "get_current_user_optional",
    "get_current_user_with_version",
})
_ADMIN_DEP_QUALNAME = "require_admin"


# ── 路由枚举（与 tests/test_endpoint_scope_matrix.py 同一展开语义）────

def _iter_api_routes(routes, prefix=""):
    """递归展开（FastAPI 0.140+ 的 _IncludedRouter 不再平铺进 app.routes）。"""
    from fastapi.routing import APIRoute

    for route in routes:
        if isinstance(route, APIRoute):
            if route.methods:  # websocket 等无 methods 的路由跳过
                yield route, prefix + route.path
            continue
        original = getattr(route, "original_router", None)
        if original is not None:
            ctx = getattr(route, "include_context", None)
            sub_prefix = prefix + (getattr(ctx, "prefix", "") or "")
            yield from _iter_api_routes(original.routes, sub_prefix)
            continue
        sub = getattr(route, "routes", None)
        if sub is not None:
            yield from _iter_api_routes(sub, prefix + (getattr(route, "path", "") or ""))


def _load_app():
    sys.path.insert(0, str(REPO))
    from app.main import app  # noqa: E402 - 脚本入口，延迟导入

    return app


def _direct_dep_names(route) -> list[str]:
    """端点直接依赖的 qualname（route 级 dependencies 已并入 dependant）。"""
    names = []
    for dep in route.dependant.dependencies:
        call = dep.call
        qualname = getattr(call, "__qualname__", None)
        if qualname is None:  # 可调用对象（非函数）—— 本生成器不识别
            qualname = repr(call)
        names.append(qualname)
    return names


def _explicit_scope(call) -> str | None:
    """require_scope(scope) 闭包的 scope 字符串（词表内 str cell）。"""
    from app.core import scopes

    for cell in getattr(call, "__closure__", None) or ():
        try:
            value = cell.cell_contents
        except ValueError:  # 空 cell
            continue
        if isinstance(value, str) and value in scopes.SCOPES:
            return value
    return None


def _read_or_write(method: str) -> str:
    return "read" if method in ("GET", "HEAD", "OPTIONS") else "write"


def derive_row(method: str, path: str, route) -> tuple[str, str, str]:
    """(method, path, route) → (scope, auth_admin, auth_user)。"""
    from app.core import scopes

    dep_calls = [dep.call for dep in route.dependant.dependencies]
    auth_admin = "N"
    auth_user = "N"
    explicit: str | None = None
    for call in dep_calls:
        qualname = getattr(call, "__qualname__", repr(call))
        if qualname == _ADMIN_DEP_QUALNAME:
            auth_admin = "Y"
        if qualname in _USER_DEP_QUALNAMES:
            auth_user = "Y"
        if explicit is None:
            explicit = _explicit_scope(call)

    normalized = normalize_path(path)
    segment = normalized.strip("/").split("/", 1)[0]
    rw = _read_or_write(method)

    scope = explicit
    if scope is None and auth_admin == "Y":
        scope = f"admin:{rw}"
    if scope is None:
        scope = OVERRIDES.get((normalized, method))
    if scope is None:
        rule = DOMAIN_RULES.get(segment)
        if rule is None:
            raise SystemExit(
                f"no scope rule for {method} {path}（域 '{segment}' 不在 "
                f"DOMAIN_RULES）—— 新端点必须 require_scope 显式声明，"
                f"或在本生成器补 OVERRIDES/DOMAIN_RULES"
            )
        scope = rule if isinstance(rule, str) else (
            rule[0] if rw == "read" else rule[1]
        )
    if scope not in scopes.SCOPES:
        raise SystemExit(
            f"{method} {path}: derived scope {scope!r} 不在词表 "
            f"app.core.scopes.SCOPES —— 禁止矩阵引入词表外 scope"
        )
    return scope, auth_admin, auth_user


def build_matrix() -> str:
    """生成整个 CSV 文本（表头 + (path, method) 字典序数据行，LF 结尾）。"""
    rows: list[tuple[str, str, str, str, str]] = []
    for route, path in _iter_api_routes(_load_app().routes):
        for method in sorted(route.methods):
            if method == "HEAD":
                continue
            scope, auth_admin, auth_user = derive_row(method, path, route)
            rows.append((method, path, scope, auth_admin, auth_user))
    rows.sort(key=lambda r: (r[1], r[0]))

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows(rows)
    return buf.getvalue()


def main() -> int:
    check = "--check" in sys.argv
    rendered = build_matrix()
    if check:
        if not OUTPUT.exists():
            print(f"[scope-matrix] MISSING {OUTPUT}", file=sys.stderr)
            return 1
        current = OUTPUT.read_bytes()
        if current != rendered.encode("utf-8"):
            print(
                "[scope-matrix] DRIFT: 端点 scope 矩阵与 app 不一致；"
                "run `python scripts/generate_endpoint_scope_matrix.py` "
                "to regenerate",
                file=sys.stderr,
            )
            return 1
        print("[scope-matrix] ok")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(rendered.encode("utf-8"))
    print(f"[scope-matrix] wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
