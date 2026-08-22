"""审计 T5: /config/* 路由的集成测试。

config.py 之前没有任何路由级测试（只有 Settings 模块测试）。
这些端点是 admin-only（PR #93 的 require_admin），是 RCE 等价入口
（skills/upload 写盘 + importlib.exec_module），必须有测试覆盖。
"""
import os
import pytest
import pytest_asyncio
import httpx
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-config-routes-32-chars")
os.environ.setdefault("ENV", "development")


@pytest_asyncio.fixture
async def app_and_client():
    """加载 config 路由 + chat 路由（config 依赖 chat.get_engine/get_registry）。"""
    from app.api.routes import config as config_routes
    from app.api.routes import chat as chat_routes
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry
    from app.core.auth import get_current_user, get_current_user_with_version

    # 给 chat 模块注入 engine + registry（config 路由会用）
    registry = ToolRegistry()
    chat_routes.registry = registry
    chat_routes.engine = ChatEngine(registry)

    app = FastAPI()
    # require_admin 现在依赖 get_current_user_with_version（会查 DB）。
    # 本测试套不建 DB，复用不查库的 get_current_user 作为 override，
    # 让 role 仍从 JWT claim 中读取（require_admin 在缺 user 对象时
    # 会回退到 _user["role"]）。
    app.dependency_overrides[get_current_user_with_version] = get_current_user
    app.include_router(config_routes.router, prefix="/api/v1")
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield app, c
    finally:
        chat_routes.engine = None
        chat_routes.registry = None


@pytest.mark.asyncio
async def test_config_llm_requires_admin_token(app_and_client):
    """S29: /config/llm 必须 admin token，无 token -> 401。"""
    _, client = app_and_client
    resp = await client.get("/api/v1/config/llm")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_config_llm_rejects_viewer(app_and_client):
    """S29: viewer token -> 403。"""
    from app.core.auth import create_access_token
    viewer_token = create_access_token({"sub": "v1", "username": "v", "role": "viewer"})
    _, client = app_and_client
    resp = await client.get(
        "/api/v1/config/llm",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_config_llm_accepts_admin(app_and_client):
    """S29: admin token -> 200。"""
    from app.core.auth import create_access_token
    admin_token = create_access_token({"sub": "a1", "username": "a", "role": "admin"})
    _, client = app_and_client
    resp = await client.get(
        "/api/v1/config/llm",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_config_skills_list_requires_admin(app_and_client):
    """S29: /config/skills 也需要 admin。"""
    _, client = app_and_client
    resp = await client.get("/api/v1/config/skills")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_config_skills_upload_rejects_non_admin(app_and_client):
    """S29: skills/upload 是 RCE 等价，必须 admin。"""
    from app.core.auth import create_access_token
    viewer_token = create_access_token({"sub": "v1", "username": "v", "role": "viewer"})
    _, client = app_and_client

    # 即使带了文件，viewer 也应被 403 拒绝
    resp = await client.post(
        "/api/v1/config/skills/upload",
        headers={"Authorization": f"Bearer {viewer_token}"},
        files={"file": ("test.py", b"print('hello')", "text/python")},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_config_skills_refresh_requires_admin(app_and_client):
    """S29: /config/skills/refresh 也需要 admin。"""
    _, client = app_and_client
    resp = await client.post("/api/v1/config/skills/refresh")
    assert resp.status_code == 401


def _admin_headers():
    from app.core.auth import create_access_token
    admin_token = create_access_token({"sub": "a1", "username": "a", "role": "admin"})
    return {"Authorization": f"Bearer {admin_token}"}


# ─── #390: /config/llm/test 与 /config/rag/test 连通性测试 ──────────────


@pytest.mark.asyncio
async def test_config_llm_test_requires_admin(app_and_client):
    """#390: /config/llm/test 必须 admin token，无 token -> 401。"""
    _, client = app_and_client
    resp = await client.post("/api/v1/config/llm/test", json={})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_config_llm_test_rejects_viewer(app_and_client):
    """#390: viewer token -> 403。"""
    from app.core.auth import create_access_token
    viewer_token = create_access_token({"sub": "v1", "username": "v", "role": "viewer"})
    _, client = app_and_client
    resp = await client.post(
        "/api/v1/config/llm/test",
        json={},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_config_llm_test_success_with_engine_config(app_and_client, monkeypatch):
    """#390: admin 空请求体 -> 用引擎当前生效配置做真实探针，成功返回 ok。

    探针被替换为假实现，验证路由正确回退到引擎的 base_url/model/api_key
    （前端测试按钮不携带 apiKey，真实 key 由服务端持有）。
    """
    from app.api.routes import chat as chat_routes

    captured = {}

    async def fake_test_llm_connection(cfg, timeout=httpx.Timeout(1)):
        captured["cfg"] = cfg
        return {"id": "probe"}

    monkeypatch.setattr(
        "app.services.chat.llm_client.test_llm_connection",
        fake_test_llm_connection,
    )
    _, client = app_and_client
    resp = await client.post(
        "/api/v1/config/llm/test", json={}, headers=_admin_headers()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert "连接成功" in body["detail"]
    cfg = captured["cfg"]
    assert cfg.base_url == chat_routes.engine.base_url
    assert cfg.model == chat_routes.engine.model
    assert cfg.api_key == chat_routes.engine.api_key


@pytest.mark.asyncio
async def test_config_llm_test_honors_request_overrides(app_and_client, monkeypatch):
    """#390: 显式传 base_url/model/api_key 时探针使用请求值而非引擎配置。"""
    captured = {}

    async def fake_test_llm_connection(cfg, timeout=httpx.Timeout(1)):
        captured["cfg"] = cfg
        return {}

    monkeypatch.setattr(
        "app.services.chat.llm_client.test_llm_connection",
        fake_test_llm_connection,
    )
    _, client = app_and_client
    resp = await client.post(
        "/api/v1/config/llm/test",
        json={"base_url": "https://example.com/v1", "model": "probe-model", "api_key": "k"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    cfg = captured["cfg"]
    assert cfg.base_url == "https://example.com/v1"
    assert cfg.model == "probe-model"
    assert cfg.api_key == "k"


@pytest.mark.asyncio
async def test_config_llm_test_connect_failure_returns_502(app_and_client, monkeypatch):
    """#390: 传输层失败 -> 502 + 错误详情（不再是假成功）。"""
    async def failing_probe(cfg, timeout=httpx.Timeout(1)):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(
        "app.services.chat.llm_client.test_llm_connection",
        failing_probe,
    )
    _, client = app_and_client
    resp = await client.post(
        "/api/v1/config/llm/test", json={}, headers=_admin_headers()
    )
    assert resp.status_code == 502
    body = resp.json()
    assert "连接失败" in body["detail"]
    assert "connection refused" in body["detail"]


@pytest.mark.asyncio
async def test_config_llm_test_provider_error_detail(app_and_client, monkeypatch):
    """#390: 上游 4xx 时返回 provider 的 error.message 作为详情。"""
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")

    async def failing_probe(cfg, timeout=httpx.Timeout(1)):
        raise httpx.HTTPStatusError(
            "401 Unauthorized",
            request=request,
            response=httpx.Response(401, json={"error": {"message": "invalid api key"}}),
        )

    monkeypatch.setattr(
        "app.services.chat.llm_client.test_llm_connection",
        failing_probe,
    )
    _, client = app_and_client
    resp = await client.post(
        "/api/v1/config/llm/test", json={}, headers=_admin_headers()
    )
    assert resp.status_code == 502
    assert "invalid api key" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_config_llm_test_ssrf_rejected(app_and_client, monkeypatch):
    """#390: base_url 指向云元数据端点 -> 400（与 POST /llm 同一校验）。"""
    called = {"probe": False}

    async def fake_test_llm_connection(cfg, timeout=httpx.Timeout(1)):
        called["probe"] = True
        return {}

    monkeypatch.setattr(
        "app.services.chat.llm_client.test_llm_connection",
        fake_test_llm_connection,
    )
    _, client = app_and_client
    resp = await client.post(
        "/api/v1/config/llm/test",
        json={"base_url": "http://169.254.169.254/latest/meta-data", "model": "m"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 400
    assert not called["probe"]  # SSRF 校验在探针之前，探针不得被调用


@pytest.mark.asyncio
async def test_config_llm_test_missing_api_key_rejected(app_and_client):
    """#390: 引擎无 api_key 且请求未提供 -> 400，不向 provider 发请求。"""
    from app.api.routes import chat as chat_routes
    original = chat_routes.engine.api_key
    chat_routes.engine.api_key = ""
    try:
        _, client = app_and_client
        resp = await client.post(
            "/api/v1/config/llm/test", json={}, headers=_admin_headers()
        )
        assert resp.status_code == 400
        assert "API Key" in resp.json()["detail"]
    finally:
        chat_routes.engine.api_key = original


@pytest.mark.asyncio
async def test_config_rag_test_requires_admin(app_and_client):
    """#390: /config/rag/test 必须 admin token，无 token -> 401。"""
    _, client = app_and_client
    resp = await client.post("/api/v1/config/rag/test", json={})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_config_rag_test_success(app_and_client, monkeypatch):
    """#390: admin -> 校验内置本地向量库健康，返回 store 类型。"""
    async def fake_check():
        return "内置本地向量库（FAISS）就绪，已索引 3 个分块"

    monkeypatch.setattr("app.api.routes.config._check_rag_store", fake_check)
    _, client = app_and_client
    resp = await client.post(
        "/api/v1/config/rag/test",
        json={"address": "http://localhost:19530", "collection": "geoagent"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["store"] == "local-faiss"
    assert "FAISS" in body["detail"]


@pytest.mark.asyncio
async def test_config_rag_test_failure_returns_502(app_and_client, monkeypatch):
    """#390: 本地向量库不可用 -> 502 + 错误详情。"""
    async def failing_check():
        raise RuntimeError("index.faiss 损坏")

    monkeypatch.setattr("app.api.routes.config._check_rag_store", failing_check)
    _, client = app_and_client
    resp = await client.post(
        "/api/v1/config/rag/test", json={}, headers=_admin_headers()
    )
    assert resp.status_code == 502
    assert "index.faiss" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_config_skills_upload_audit_logs_real_actor(
    app_and_client, tmp_path, monkeypatch, caplog
):
    """#759: RCE 等价操作的审计行必须记录真实 actor（此前读不存在的键恒为 unknown）。"""
    import logging
    from types import SimpleNamespace
    from app.core.auth import require_admin

    app, client = app_and_client

    async def _fake_admin():
        # require_admin 真实返回形状：user_id + user ORM 对象
        return {
            "user_id": "admin-42",
            "role": "admin",
            "org_id": None,
            "user": SimpleNamespace(username="alice"),
        }

    app.dependency_overrides[require_admin] = _fake_admin
    # skills_dir 是路由内相对路径 —— 切到临时目录避免污染仓库
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.WARNING, logger="app.api.routes.config"):
        resp = await client.post(
            "/api/v1/config/skills/upload",
            files={"file": ("audit_probe.py", b"def run():\n    return 1\n", "text/python")},
        )
    assert resp.status_code == 200, resp.text
    audit = [r for r in caplog.records if "Skill uploaded by" in r.getMessage()]
    assert audit, "audit warning must fire"
    msg = audit[0].getMessage()
    assert "admin-42" in msg
    assert "Skill uploaded by unknown" not in msg
