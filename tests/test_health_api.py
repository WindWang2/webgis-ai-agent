"""Health & Readiness API tests"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock, patch
from fastapi import FastAPI

from app.api.routes import health as _mod


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(_mod.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health_check(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data
    assert data["service"] == "WebGIS AI Agent"
    assert data["agent_runtime"] in {"pi", "chatengine"}


@pytest.mark.asyncio
async def test_agent_runtime_badge_reflects_chatengine_fallback(client, monkeypatch):
    """#1032: after a failed bridge start the lifespan serves on ChatEngine
    (``chat.pi_bridge`` stays None — no half-started singleton). The runtime
    badge must honestly report ``chatengine`` even with USE_NEW_AGENT on,
    and flip back to ``pi`` once a live bridge owns the subprocess."""
    import app.api.routes.chat as chat_mod

    monkeypatch.setattr(chat_mod, "USE_NEW_AGENT", True)
    # Exact post-fallback state: start() failed, lifespan left pi_bridge None.
    monkeypatch.setattr(chat_mod, "pi_bridge", None)
    resp = await client.get("/api/v1/health")
    assert resp.json()["agent_runtime"] == "chatengine"

    # Converse pins the badge to actual bridge state (not a hardcoded value).
    alive_bridge = MagicMock()
    alive_bridge._process_died = False
    monkeypatch.setattr(chat_mod, "pi_bridge", alive_bridge)
    resp = await client.get("/api/v1/health")
    assert resp.json()["agent_runtime"] == "pi"


@pytest.mark.asyncio
async def test_readiness_check_healthy(client):
    with patch.object(_mod, "_check_db", return_value=True), \
         patch.object(_mod, "_check_llm", return_value=True), \
         patch.object(_mod, "_check_redis", return_value=True), \
         patch.object(_mod, "_check_celery", return_value=True):
        resp = await client.get("/api/v1/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True


@pytest.mark.asyncio
async def test_readiness_check_unhealthy(client):
    """依赖不可用时 /ready 必须返回 503 —— k8s readinessProbe 只看状态码，
    若返回 200 即使 body.ready=false 也会被当作就绪把流量打过来。"""
    with patch.object(_mod, "_check_db", return_value=False), \
         patch.object(_mod, "_check_llm", return_value=False):
        resp = await client.get("/api/v1/ready")
        assert resp.status_code == 503
        data = resp.json()
        assert data["ready"] is False


@pytest.mark.asyncio
async def test_liveness_check(client):
    """/api/v1/health/live 是 livenessProbe 专用的轻量端点，
    必须无条件返回 200，不做 DB/Redis/Celery 检查（否则依赖抖动会被杀进程）。"""
    resp = await client.get("/api/v1/health/live")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "alive"


@pytest.mark.asyncio
async def test_agent_runtime_reports_chatengine_when_probe_errors(client, monkeypatch):
    """Fail-closed: if the bridge-state probe itself raises, the badge must
    not fall back to "pi" off the flag — report chatengine (#1032 honesty)."""
    import app.api.routes.chat as chat_mod

    monkeypatch.setattr(chat_mod, "USE_NEW_AGENT", True)
    alive_bridge = MagicMock()
    alive_bridge._process_died = False
    monkeypatch.setattr(chat_mod, "pi_bridge", alive_bridge)

    def _boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(chat_mod, "_use_pi_bridge", _boom)
    resp = await client.get("/api/v1/health")
    assert resp.json()["agent_runtime"] == "chatengine"
