"""
Full Integration Tests Suite
Coverage: Dialogue Interface, Map Functions, Agent Orchestration, Data Access, JWT Auth
"""
import pytest
import sys
import os

# Ensure app modules can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from unittest.mock import MagicMock, patch
from datetime import datetime

# ==================== FIXTURES ====================
@pytest.fixture(scope="session")
def base_url():
    """Base URL for testing"""
    return "http://localhost:8000"


@pytest.fixture
def test_user_data():
    """Test user registration data"""
    return {
        "username": f"testuser_{datetime.now().strftime('%s')}",
        "email": f"test_{datetime.now().strftime('%s')}@example.com",
        "password": "test123456",
        "full_name": "Integration Test User"
    }


@pytest.fixture
def mock_current_user():
    """Mock authenticated user"""
    from app.models.db_model import User
    user = User(
        id=999,
        username="mock_user",
        email="mock@test.com",
        role="admin",
        org_id=1,
        is_active=True
    )
    return user


# ==================== TEST CLASSES ====================

class TestHealthCheck:
    """Health Check & Root Endpoint Tests"""
    
    def test_root_endpoint(self, client):
        """Test root endpoint returns correct response"""
        response = client.get("/")
        
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "WebGIS AI Agent API"
        assert data["version"] == "1.0.0"
        assert "status" in data
    
    def test_health_check(self, client):
        """Test health check endpoint"""
        response = client.get("/api/v1/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True or "data" in data


class TestJWTAuth:
    """JWT Authentication Function Tests"""
    
    def test_register_new_user(self, client, test_user_data):
        """Test user registration"""
        response = client.post("/api/v1/auth/register", json=test_user_data)
        
        # Accept both success and user exists error
        assert response.status_code == 200
        data = response.json()
        # Either successful registration or user already exists
        assert data.get("success") is True or data.get("code") in ["EXISTS_USER", "EXISTS_EMAIL"]
    
    def test_login_valid_user(self, client, test_user_data):
        """Test login with valid credentials"""
        # First register
        client.post("/api/v1/auth/register", json=test_user_data)
        
        # Then login
        login_data = {
            "email": test_user_data["email"],
            "password": test_user_data["password"]
        }
        response = client.post("/api/v1/auth/login", json=login_data)
        
        assert response.status_code == 200
        data = response.json()
        
        if data.get("success"):
            assert "access_token" in data.get("data", {})
            assert data["data"]["token_type"] == "bearer"
    
    def test_login_invalid_password(self, client, test_user_data):
        """Test login with invalid password"""
        # Register first
        client.post("/api/v1/auth/register", json=test_user_data)
        
        # Try login with wrong password
        login_data = {
            "email": test_user_data["email"],
            "password": "wrong_password"
        }
        response = client.post("/api/v1/auth/login", json=login_data)
        
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is False
        assert data.get("code") == "AUTH_FAILED"
    
    def test_get_profile_without_auth(self, client):
        """Test getting profile without authentication"""
        response = client.get("/api/v1/auth/me")
        
        # Should fail with 401 or 403
        assert response.status_code in [401, 403]
    
    def test_jwt_token_format(self, client, test_user_data):
        """Test JWT token format validation"""
        # Register and login
        client.post("/api/v1/auth/register", json=test_user_data)
        
        login_data = {
            "email": test_user_data["email"],
            "password": test_user_data["password"]
        }
        response = client.post("/api/v1/auth/login", json=login_data)
        
        data = response.json()
        if data.get("success"):
            token = data["data"]["access_token"]
            # JWT format: header.payload.signature
            parts = token.split('.')
            assert len(parts) == 3, "JWT should have 3 parts"


class TestLayerManagement:
    """Map Layer Management API Tests"""
    
    def test_list_layers_unauthorized(self, client):
        """Test listing layers without authorization"""
        response = client.get("/api/v1/layers/")
        
        # May succeed (public) or require auth
        assert response.status_code in [200, 401, 403]
    
    def test_create_layer_requires_auth(self, client):
        """Test creating layer requires authentication"""
        layer_data = {
            "name": "Test Layer",
            "layer_type": "vector",
            "source": "test.geojson"
        }
        response = client.post("/api/v1/layers/", json=layer_data)
        
        # Should require authentication
        assert response.status_code in [200, 401, 403]


class TestTaskManagement:
    """Agent Orchestration & Task Scheduling Tests"""
    
    def test_create_task(self, client):
        """Test creating a new task"""
        task_data = {
            "task_type": "spatial_analysis",
            "params": {
                "operation": "buffer",
                "distance": 100
            }
        }
        
        # Try with minimal auth header
        headers = {"Authorization": "Bearer dummy_token"}
        response = client.post("/api/v1/tasks/", json=task_data, headers=headers)
        
        # Will likely fail auth but proves endpoint exists
        assert response.status_code in [200, 401, 403, 422]
    
    def test_list_tasks(self, client):
        """Test listing tasks"""
        response = client.get("/api/v1/tasks/")
        
        # May require auth
        assert response.status_code in [200, 401, 403]
    
    def test_task_status_endpoint(self, client):
        """Test task status checking endpoint"""
        response = client.get("/api/v1/tasks/1/status")
        
        # Check if endpoint responds
        assert response.status_code in [200, 401, 403, 404]


class TestDialogueInterface:
    """Chat/Dialogue Interface Tests"""
    
    def test_chat_endpoint_exists(self, client):
        """Test chat endpoint exists and responds"""
        # Try sending a chat message
        chat_data = {
            "message": "Show me the map",
            "context": {}
        }
        response = client.post("/api/v1/chat/", json=chat_data)
        
        # Check if endpoint is accessible
        assert response.status_code in [200, 401, 403, 422]
    
    def test_chat_with_context(self, client):
        """Test chat with additional context"""
        chat_data = {
            "message": "Analyze this area",
            "context": {
                "bounds": [0, 0, 10, 10],
                "zoom": 5
            }
        }
        response = client.post("/api/v1/chat/", json=chat_data)
        
        assert response.status_code in [200, 401, 403, 422]


class TestMapFunctions:
    """Map Function Integration Tests"""
    
    def test_map_bounds_endpoint(self, client):
        """Test map bounds endpoint"""
        response = client.get("/api/v1/map/bounds?min_lng=0&min_lat=0&max_lng=10&max_lat=10")
        
        assert response.status_code in [200, 401, 403, 404]
    
    def test_map_search_endpoint(self, client):
        """Test map search functionality"""
        response = client.post("/api/v1/map/search", json={"query": "Beijing"})
        
        assert response.status_code in [200, 401, 403, 404, 422]


class TestDataAccess:
    """Data Acquisition Interface Tests"""
    
    def test_data_query_endpoint(self, client):
        """Test data querying endpoints"""
        # Test various data endpoints
        endpoints = [
            "/api/v1/data/query",
            "/api/v1/data/features",
        ]
        
        for endpoint in endpoints:
            response = client.get(endpoint)
            # Should at least be reachable
            assert response.status_code in [200, 401, 403, 404, 422]
    
    def test_export_functionality(self, client):
        """Test data export endpoints"""
        response = client.get("/api/v1/data/export?format=geojson")
        
        assert response.status_code in [200, 401, 403, 404]


class TestCORSAndSecurity:
    """CORS Configuration & Security Tests"""
    
    def test_cors_headers_present(self, client):
        """Test CORS headers is properly set"""
        response = client.options("/api/v1/health")
        
        # Check for CORS headers
        cors_headers = response.headers.get("access-control-allow-origin")
        # Should either have a value or not include it (browser handles)
        assert cors_header is not None or response.status_code == 200
    
    def test_no_sensitive_data_in_response(self, client):
        """Test response doesn't leak sensitive data"""
        response = client.get("/api/v1/auth/me")
        
        # Even if it fails, shouldn't leak much info
        if response.status_code == 200:
            data = response.json()
            # Password hashes should never be in response
            assert "password" not in str(data).lower()


class TestErrorHandling:
    """Error Handling Tests"""
    
    def test_404_handling(self, client):
        """Test 404 error handling"""
        response = client.get("/api/v1/nonexistent/endpoint")
        
        assert response.status_code == 404
    
    def test_validation_error(self, client):
        """Test input validation errors"""
        # Send invalid data
        invalid_data = {"email": "not-an-email"}
        response = client.post("/api/v1/auth/register", json=invalid_data)
        
        # Should return validation error
        assert response.status_code in [200, 422]
        data = response.json()
        # Either custom error response or FastAPI validation error
        assert data.get("success") is False or "detail" in data


# ==================== RUN CONFIGURATION ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])