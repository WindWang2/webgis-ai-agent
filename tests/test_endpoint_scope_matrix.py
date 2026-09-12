"""端点 scope 矩阵 CI 校验（ADR-0139 P3 门禁）。

三重保证：

1. **100% 覆盖**：``app.main.app`` 的每个 (method, path) 都必须出现在
   ``docs/dev/endpoint-scope-matrix.csv``——新增端点不带矩阵行 = 红；
2. **词汇表封闭**：矩阵中的 scope 必须在 ``app/core/scopes.SCOPES``；
3. **无陈旧行**：矩阵中的路由必须真实存在于 app——删除端点不清矩阵 = 红。

重新生成：``python scripts/generate_endpoint_scope_matrix.py``（推导歧义
项用 OVERRIDES 钉住；矩阵的 diff 即权限面变更的评审面）。
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

REPO = Path(__file__).resolve().parents[1]
MATRIX = REPO / "docs" / "dev" / "endpoint-scope-matrix.csv"


def _iter_api_routes(routes, prefix=""):
    """递归展开（FastAPI 0.140+ 的 _IncludedRouter 不再平铺进 app.routes）。"""
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


def _app_routes() -> set[tuple[str, str]]:
    from app.main import app

    out = set()
    for route, path in _iter_api_routes(app.routes):
        for method in route.methods:
            if method == "HEAD":
                continue
            out.add((method, path))
    return out


def _matrix_rows() -> dict[tuple[str, str], str]:
    with MATRIX.open(newline="", encoding="utf-8") as f:
        return {
            (r["method"], r["path"]): r["scope"]
            for r in csv.DictReader(f)
        }


def test_matrix_file_exists():
    assert MATRIX.exists(), (
        "docs/dev/endpoint-scope-matrix.csv 缺失 —— 用 "
        "scripts/generate_endpoint_scope_matrix.py 重新生成"
    )


def test_every_route_is_in_matrix():
    routes = _app_routes()
    matrix = _matrix_rows()
    missing = sorted(routes - set(matrix))
    assert not missing, (
        f"{len(missing)} 个端点未进 scope 矩阵（新增端点必须标注 scope）：\n"
        + "\n".join(f"  {m} {p}" for m, p in missing[:20])
    )


def test_matrix_scopes_are_in_vocabulary():
    from app.core.scopes import SCOPES

    matrix = _matrix_rows()
    unknown = {p: s for (m, p), s in matrix.items() if s not in SCOPES}
    assert not unknown, (
        f"矩阵含词汇表外 scope（封闭词表见 app/core/scopes.py）：{unknown}"
    )


def test_matrix_has_no_stale_rows():
    routes = _app_routes()
    matrix = _matrix_rows()
    stale = sorted(set(matrix) - routes)
    assert not stale, (
        f"{len(stale)} 个矩阵行在 app 中已不存在（陈旧矩阵误导评审）：\n"
        + "\n".join(f"  {m} {p}" for m, p in stale[:20])
    )


def test_admin_flagged_rows_use_admin_scopes():
    """auth_admin=Y 的行 scope 必须是 admin:* 或 geocompute:admin
    （require_admin 面与矩阵标注一致性；防止推导漂移）。"""
    with MATRIX.open(newline="", encoding="utf-8") as f:
        bad = [
            (r["method"], r["path"], r["scope"])
            for r in csv.DictReader(f)
            if r["auth_admin"] == "Y"
            and not r["scope"].startswith("admin:")
            and r["scope"] != "geocompute:admin"
        ]
    assert not bad, f"require_admin 端点未标 admin scope: {bad[:10]}"


def test_require_scope_dependency_rejects_unknown_scope():
    """require_scope 对词汇表外 scope 是编程错误（fail-loud）。"""
    from app.core.scopes import require_scope

    with pytest.raises(ValueError):
        require_scope("not:a-scope")
