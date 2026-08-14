"""Integration tests for skill API endpoints using FastAPI TestClient."""
import pytest
from fastapi.testclient import TestClient

from app.tools.skills import _md_skills
from app.api.routes.chat import router


@pytest.fixture(autouse=True)
def clear_skills():
    _md_skills.clear()
    yield
    _md_skills.clear()


@pytest.fixture
def client():
    """Create TestClient with a minimal app that includes only the chat router."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """The skill list is internal metadata and requires authentication."""
    from app.core.auth import create_access_token
    token = create_access_token({"sub": "skill-user", "username": "s", "role": "viewer"})
    return {"Authorization": f"Bearer {token}"}


class TestSkillListAPI:
    def test_list_skills_requires_authentication(self, client):
        """A-7 regression: anonymous callers must get 401, not the skill list."""
        resp = client.get("/api/v1/chat/skills")
        assert resp.status_code == 401

    def test_list_skills_empty(self, client, auth_headers):
        resp = client.get("/api/v1/chat/skills", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"skills": []}

    def test_list_skills_returns_loaded_skills(self, client, auth_headers):
        _md_skills["urban_planning"] = {
            "description": "城市规划设计",
            "body": "分析城市布局...",
            "filename": "urban_planning.md",
        }
        resp = client.get("/api/v1/chat/skills", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["skills"]) == 1
        assert data["skills"][0]["name"] == "urban_planning"
