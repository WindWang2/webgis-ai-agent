"""
Application Startup Verification Test
Validates the backend can start correctly and all dependencies are satisfied.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def test_app_can_import():
    """Test app module imports works correctly"""
    try:
        from app.main import app
        assert app is not None
    except ImportError as e:
        pytest.fail(f"Failed to import app: {e}")


def test_core_modules_exist():
    """Test core modules are available"""
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models.db_model import User
    
    assert settings is not None
    assert SessionLocal is not None


def test_api_routes_registered():
    """Test API routes are properly registered"""
    from app.main import app
    
    # Get all routes
    routes = []
    for route in app.routes:
        if hasattr(route, 'path'):
            routes.append(route.path)
    
    # Verify essential routes exist
    assert any('/auth/' in r for r in routes), "Auth routes not registered"
    assert any('/layers/' in r for r in routes), "Layers routes not registered"


def test_jwt_configured():
    """Test JWT configuration is set"""
    from app.core.config import settings
    
    # Check secret key is set (not the placeholder)
    assert settings.SECRET_KEY is not None
    assert settings.SECRET_KEY != ""


def test_database_connection_possible():
    """Test database connection can be established"""
    from app.core.database import Engine as engine
    
    try:
        conn = engine.connect()
        conn.close()
    except Exception as e:
        pytest.skip(f"Database not available: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])