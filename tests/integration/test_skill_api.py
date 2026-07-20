"""Integration tests for skill API endpoints using FastAPI TestClient."""
import pytest
from fastapi.testclient import TestClient

from app.tools.skills import _md_skills
from app.api.routes import chat as _chat_mod
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


class TestSkillListAPI:
    def test_list_skills_empty(self, client):
        resp = client.get("/api/v1/chat/skills")
        assert resp.status_code == 200
        assert resp.json() == {"skills": []}

    def test_list_skills_returns_loaded_skills(self, client):
        _md_skills["urban_planning"] = {
            "description": "城市规划设计",
            "body": "分析城市布局...",
            "filename": "urban_planning.md",
        }
        resp = client.get("/api/v1/chat/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["skills"]) == 1
        assert data["skills"][0]["name"] == "urban_planning"
