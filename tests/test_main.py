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
