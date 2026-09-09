"""SEC-KG-01/02 收口回归（Quality V2 security matrix）。

SEC-KG-01 artifact ownership：
- 路由层**禁止直调** artifact_registry（AST 契约扫描）——产物读写的唯一
  合法入口是已过会话守卫的 service/tool 边界；
- artifact_registry 公共读/改 API 的 session 作用域签名被钉扎（任何把
  session 键控改掉的 refactor 直接红）；
- 行为隔离回归见 test_security_regression.py::TestArtifactSessionScope。

SEC-KG-02 templates / knowledge delete authZ：
- DELETE /templates/{id} 与 DELETE /knowledge/document/{id} 的 owner 矩阵
  （#1109 同款 7 案思路）：owner 放行、他人拒绝、admin 越权语义、
  匿名拒绝、内置/缺失资源语义、删除后行确实消失。
"""
from __future__ import annotations

import ast
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

REPO = Path(__file__).resolve().parents[2]

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-sec-kg-matrix-32charsxx")
os.environ.setdefault("ENV", "development")

# ── SEC-KG-01：路由层直调禁令（AST 契约扫描）────────────────────────────


def _route_modules() -> list[Path]:
    return sorted((REPO / "app" / "api" / "routes").glob("*.py"))


def test_no_route_module_calls_artifact_registry_directly():
    """SEC-KG-01 静态契约：app/api/routes/** 不得 import/引用 artifact_registry。

    路由必须经过已过 verify_session_owner / 会话守卫的 service/tool 边界；
    新增便捷路由直连注册表 = 绕过 owner 语义，直接红。

    覆盖形态（R2 review MAJOR 修复）：绝对 import、`from app.services
    import artifact_registry`（ImportFrom 的 alias 名）、`import
    app.services.artifact_registry as x`、模块属性引用
    `<x>.artifact_registry`、以及裸名引用（import 进来的模块名直接出现
    在本模块作用域 —— Name 节点匹配，防别名调用漏检）。动态形态
    （importlib.import_module / getattr 字符串）由字符串常量探针兜底。
    """
    offenders: list[str] = []
    for path in _route_modules():
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            hit = None
            if isinstance(node, ast.ImportFrom):
                if node.module and "artifact_registry" in node.module:
                    hit = f"import from {node.module}"
                for alias in node.names:
                    if "artifact_registry" in alias.name:
                        hit = f"from {node.module} import {alias.name}"
                        break
                if hit:
                    # `from app.services import artifact_registry` 把模块
                    # 名绑进本模块作用域 —— 记录以捕获后续裸名调用
                    for alias in node.names:
                        if "artifact_registry" in alias.name:
                            imported_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "artifact_registry" in alias.name:
                        hit = f"import {alias.name}"
                        imported_names.add(alias.asname or
                                           alias.name.split(".")[0])
            elif isinstance(node, ast.Attribute) and \
                    node.attr == "artifact_registry":
                hit = "attribute reference"
            elif isinstance(node, ast.Name) and node.id in imported_names:
                hit = f"reference to imported module name {node.id}"
            elif isinstance(node, ast.Constant) and \
                    isinstance(node.value, str) and \
                    "artifact_registry" in node.value:
                hit = f"dynamic import/probe string {node.value!r}"
            if hit:
                rel = path.relative_to(REPO)
                offenders.append(f"{rel}: {hit}")
    assert not offenders, (
        "路由层禁止直调 artifact_registry（SEC-KG-01）；违规: " + "; ".join(offenders)
    )


def test_artifact_registry_public_api_is_session_scoped():
    """SEC-KG-01 签名钉扎：公共读写 API 第一参必须是 session 作用域键。"""
    import inspect

    from app.services import artifact_registry as reg

    for fn_name in ("register_artifact", "get_artifact", "list_artifacts",
                    "mark_status"):
        fn = getattr(reg, fn_name)
        params = list(inspect.signature(fn).parameters)
        assert params and params[0] == "session_id", (
            f"{fn_name} 第一参必须是 session_id（session 作用域是 owner 语义的"
            f"根基）；当前: {params}"
        )


# ── SEC-KG-02：templates / knowledge delete owner 矩阵 ──────────────────

_OWNER = {"user_id": "kg-owner", "role": "viewer"}
_OTHER = {"user_id": "kg-other", "role": "viewer"}
_ADMIN = {"user_id": "kg-admin", "role": "admin"}


@pytest_asyncio.fixture
async def templates_app(tmp_path, monkeypatch):
    """模板 delete 路由的独立 sqlite 环境（#1109 同款 override 模式）。"""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.auth import get_current_user
    from app.core.database import get_async_db
    from app.models.db_model import Base
    from app.api.routes import templates as templates_routes

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'tmpl.db'}",
        connect_args={"check_same_thread": False},
    )
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def override_db():
        async with sessionmaker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    async def override_db_dep():
        # FastAPI 依赖 override 需要异步生成器（非 contextmanager 工厂）
        async with sessionmaker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app = FastAPI()
    app.include_router(templates_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_db_dep
    current = {"user": _OWNER}
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as client:
        yield client, sessionmaker, current
    await engine.dispose()


async def _seed_template(sessionmaker, *, template_id: str, creator_id,
                         is_builtin: bool = False) -> None:
    from app.models.db_model import CartographyTemplate

    async with sessionmaker() as s:
        s.add(CartographyTemplate(
            id=template_id,
            name=f"tmpl-{template_id}",
            kind="layout",
            creator_id=creator_id,
            is_builtin=is_builtin,
            payload={"version": "1"},
        ))
        await s.commit()


async def _current_user(current: dict, user: dict) -> None:
    current["user"] = user


def get_current_user_dep():
    from app.core.auth import get_current_user

    return get_current_user


@pytest.mark.asyncio
async def test_template_delete_owner_allowed(templates_app):
    client, sessionmaker, _current = templates_app
    await _seed_template(sessionmaker, template_id="t-owned",
                         creator_id=_OWNER["user_id"])
    resp = await client.delete("/api/v1/templates/t-owned")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    from sqlalchemy import select
    from app.models.db_model import CartographyTemplate
    async with sessionmaker() as s:
        gone = (await s.execute(
            select(CartographyTemplate).where(
                CartographyTemplate.id == "t-owned"))).scalar_one_or_none()
    assert gone is None


@pytest.mark.asyncio
async def test_template_delete_other_user_denied_row_intact(templates_app):
    client, sessionmaker, current = templates_app
    await _seed_template(sessionmaker, template_id="t-foreign",
                         creator_id=_OWNER["user_id"])
    await _current_user(current, _OTHER)
    resp = await client.delete("/api/v1/templates/t-foreign")
    assert resp.status_code == 403
    from sqlalchemy import select
    from app.models.db_model import CartographyTemplate
    async with sessionmaker() as s:
        row = (await s.execute(
            select(CartographyTemplate).where(
                CartographyTemplate.id == "t-foreign"))).scalar_one_or_none()
    assert row is not None, "403 拒绝后模板行必须原样保留"


@pytest.mark.asyncio
async def test_template_delete_admin_may_delete_foreign(templates_app):
    """creator-or-admin 语义：admin 是越权删除的**显式**白名单（文档化行为）。"""
    client, sessionmaker, current = templates_app
    await _seed_template(sessionmaker, template_id="t-admin",
                         creator_id=_OWNER["user_id"])
    await _current_user(current, _ADMIN)
    resp = await client.delete("/api/v1/templates/t-admin")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_template_delete_anonymous_denied(tmp_path):
    """匿名（无 token）打真实 authN 依赖（无 override）：必须 401/403 拒绝。"""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.database import get_async_db
    from app.api.routes import templates as templates_routes
    from app.models.db_model import Base

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'tmpl-anon.db'}",
        connect_args={"check_same_thread": False},
    )
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_db_dep():
        async with sessionmaker() as s:
            yield s

    app = FastAPI()
    app.include_router(templates_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_db_dep
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as client:
        resp = await client.delete("/api/v1/templates/t-anon")
    await engine.dispose()
    assert resp.status_code in (401, 403), "匿名必须被 authN 依赖拒绝"


@pytest.mark.asyncio
async def test_template_builtin_readonly_even_for_admin(templates_app):
    client, sessionmaker, current = templates_app
    await _seed_template(sessionmaker, template_id="t-builtin",
                         creator_id=None, is_builtin=True)
    await _current_user(current, _ADMIN)
    resp = await client.delete("/api/v1/templates/t-builtin")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_template_missing_404(templates_app):
    client, _, _ = templates_app
    resp = await client.delete("/api/v1/templates/t-nope")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_template_null_creator_fail_closed_for_viewer(templates_app):
    """creator_id IS NULL 的自定义模板：viewer 不可删（fail-closed，仅 admin）。"""
    client, sessionmaker, current = templates_app
    await _seed_template(sessionmaker, template_id="t-null",
                         creator_id=None, is_builtin=False)
    await _current_user(current, _OTHER)
    resp = await client.delete("/api/v1/templates/t-null")
    assert resp.status_code == 403


# ── knowledge document delete ────────────────────────────────────────────


@pytest_asyncio.fixture
async def knowledge_env(tmp_path, monkeypatch):
    """knowledge delete：真实 service 路径 + 真实 sqlite；向量引擎打桩
    （外部基础设施；owner 校验与 DB 删除仍是真实代码路径）。"""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import database
    from app.core.auth import get_current_user_with_version
    from app.api.routes import knowledge as knowledge_routes
    from app.models.db_model import Base
    from app.tools import _utils

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'kb.db'}",
        connect_args={"check_same_thread": False},
    )
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def override_db():
        async with sessionmaker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(database, "AsyncSessionLocal", sessionmaker)
    monkeypatch.setattr(_utils, "async_db_session", override_db)

    class _StubEngine:
        def __init__(self):
            self.deleted = []

        async def delete_document(self, document_id, tenant=None):
            self.deleted.append(document_id)
            return True

    stub = _StubEngine()
    monkeypatch.setattr(
        "app.services.rag.engine.get_knowledge_engine", lambda: stub)

    app = FastAPI()
    app.include_router(knowledge_routes.router, prefix="/api/v1")
    kb_current = {"user": _OWNER}
    app.dependency_overrides[get_current_user_with_version] = \
        lambda: kb_current["user"]

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as client:
        yield client, sessionmaker, kb_current, stub
    await engine.dispose()


async def _seed_document(sessionmaker, *, doc_id: str, creator_id) -> None:
    from app.models.knowledge_base import Document

    async with sessionmaker() as s:
        s.add(Document(id=doc_id, title=f"doc-{doc_id}",
                       creator_id=creator_id, status="completed"))
        await s.commit()


async def _document_exists(sessionmaker, doc_id: str) -> bool:
    from sqlalchemy import select
    from app.models.knowledge_base import Document

    async with sessionmaker() as s:
        row = (await s.execute(
            select(Document.id).where(Document.id == doc_id)
        )).scalar_one_or_none()
    return row is not None


@pytest.mark.asyncio
async def test_knowledge_delete_creator_allowed(knowledge_env):
    client, sessionmaker, _, stub = knowledge_env
    doc_id = f"doc_{uuid.uuid4().hex[:10]}"
    await _seed_document(sessionmaker, doc_id=doc_id,
                         creator_id=_OWNER["user_id"])
    resp = await client.delete(f"/api/v1/knowledge/document/{doc_id}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert doc_id in stub.deleted, "向量清理必须先于/伴随 DB 删除"
    assert not await _document_exists(sessionmaker, doc_id)


@pytest.mark.asyncio
async def test_knowledge_delete_other_user_denied_row_intact(knowledge_env):
    """IDOR 核心：他人（同 org 或不同 org）不能删 creator 的文档。"""
    client, sessionmaker, kb_current, stub = knowledge_env
    doc_id = f"doc_{uuid.uuid4().hex[:10]}"
    await _seed_document(sessionmaker, doc_id=doc_id,
                         creator_id=_OWNER["user_id"])
    kb_current["user"] = _OTHER
    resp = await client.delete(f"/api/v1/knowledge/document/{doc_id}")
    assert resp.status_code == 200  # envelope 200
    body = resp.json()
    assert body.get("success") is False, "业务层必须拒绝"
    assert stub.deleted == [], "拒绝路径不得触发向量清理"
    assert await _document_exists(sessionmaker, doc_id), "行必须原样保留"


@pytest.mark.asyncio
async def test_knowledge_delete_same_org_member_denied(knowledge_env):
    """org 成员身份不得放宽 SQL guard（creator-only 语义，S41/#485）。"""
    client, sessionmaker, *_ = knowledge_env
    doc_id = f"doc_{uuid.uuid4().hex[:10]}"
    await _seed_document(sessionmaker, doc_id=doc_id, creator_id="creator-x")
    from app.services import rag_service

    ok = await rag_service.delete_document(
        doc_id, user_id="member-same-org", org_id=1)
    assert ok is False
    assert await _document_exists(sessionmaker, doc_id)


@pytest.mark.asyncio
async def test_knowledge_service_no_identity_fail_closed(knowledge_env):
    """user_id=None（匿名/无身份）→ 不触任何存储，直接 False。"""
    from app.services import rag_service

    doc_id = f"doc_{uuid.uuid4().hex[:10]}"
    await _seed_document(knowledge_env[1], doc_id=doc_id, creator_id=None)
    with patch("app.services.rag.engine.get_knowledge_engine") as never:
        ok = await rag_service.delete_document(doc_id, user_id=None, org_id=None)
    assert ok is False
    never.assert_not_called()
    assert await _document_exists(knowledge_env[1], doc_id)


def test_knowledge_delete_binds_versioned_auth_dependency():
    """R2 review MINOR：delete 路由必须绑定 token_version 校验版依赖
    （logout bump ver 后旧 token 不能删文档）。换成 unversioned 依赖即红。"""
    import inspect

    from app.api.routes import knowledge as knowledge_routes

    dep_callable = None
    for route in knowledge_routes.router.routes:
        # router 带 prefix（/knowledge）；匹配挂载段路径
        if getattr(route, "path", "").endswith("/document/{document_id}" ) \
                and "DELETE" in getattr(route, "methods", set()):
            dep_callable = route.endpoint
            break
    assert dep_callable is not None, "delete_document 路由缺失"
    src = inspect.getsource(dep_callable)
    assert "get_current_user_with_version" in src, (
        "knowledge delete 必须显式绑定 versioned 认证依赖（token_version "
        "语义），发现绑定: " + src[:200])
