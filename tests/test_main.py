"""FastAPI 应用入口测试"""
import pytest

sniffio_available = pytest.importorskip("sniffio", reason="sniffio not installed") is not None


class TestAppCreation:
    def test_app_is_fastapi_instance(self):
        from fastapi import FastAPI
        from app.main import app
        assert isinstance(app, FastAPI)

    def test_app_has_correct_title(self):
        from app.main import app
        assert app.title == "WebGIS AI Agent"

    def test_app_has_lifespan(self):
        from app.main import app
        assert app.router.lifespan_context is not None


class TestMiddleware:
    def test_rate_limit_middleware_registered(self):
        from app.main import app
        middleware_classes = [m.cls.__name__ for m in app.user_middleware]
        assert "RateLimitMiddleware" in middleware_classes

    def test_cors_middleware_registered(self):
        from app.main import app
        middleware_classes = [m.cls.__name__ for m in app.user_middleware]
        assert "CORSMiddleware" in middleware_classes

    def test_cors_preflight_allows_frontend_transport_headers(self):
        """Browser chat send does OPTIONS first; transport always injects
        X-Request-ID, and reconnects send Last-Event-ID. Those must be in
        allow_headers or the preflight is 400 and the UI shows Failed to fetch.
        """
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        response = client.options(
            "/api/v1/chat/stream",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": (
                    "content-type,x-request-id,x-session-token,last-event-id"
                ),
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"

    def test_cors_preflight_allows_loopback_dev_origin(self):
        """http://127.0.0.1:3000 is a different origin from localhost."""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        response = client.options(
            "/api/v1/chat/stream",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-request-id",
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"


class TestRouters:
    def test_health_router_registered(self):
        """Verify health router is mounted by checking app.openapi() schema."""
        from app.main import app
        schema = app.openapi()
        paths = list(schema.get("paths", {}).keys())
        assert any("/health" in p for p in paths), f"health route not in OpenAPI paths: {paths[:15]}"

    def test_chat_router_registered(self):
        """Verify chat router is mounted by checking app.openapi() schema."""
        from app.main import app
        schema = app.openapi()
        paths = list(schema.get("paths", {}).keys())
        assert any("/chat" in p for p in paths), f"chat route not in OpenAPI paths: {paths[:15]}"
