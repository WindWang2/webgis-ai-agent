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
    def _all_paths(self, app, depth=0):
        """Recursively collect all route paths at any nesting depth."""
        if depth > 5:
            return []
        paths = []
        for route in app.routes:
            path = getattr(route, 'path', None)
            if path:
                paths.append(path)
            nested = getattr(route, 'routes', None)
            if nested and depth < 5:
                paths.extend(self._all_paths(type('obj', (), {'routes': nested})(), depth + 1))
        return paths

    def test_health_router_registered(self):
        from app.main import app
        paths = self._all_paths(app)
        assert any("/health" in p for p in paths), f"health route not found in {paths[:15]}"

    def test_chat_router_registered(self):
        from app.main import app
        paths = self._all_paths(app)
        assert any("/chat" in p for p in paths), f"chat route not found in {paths[:15]}"
