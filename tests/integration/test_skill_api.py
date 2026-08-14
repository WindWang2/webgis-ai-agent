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
    """Authenticated callers use the same metadata endpoint."""
    from app.core.auth import create_access_token
    token = create_access_token({"sub": "skill-user", "username": "s", "role": "viewer"})
    return {"Authorization": f"Bearer {token}"}


class TestSkillListAPI:
    def test_list_skills_anonymous_gets_metadata_only(self, client):
        """The catalog read is anonymous-accessible (the shipped SkillsHub UI is
        anonymous-first with no Bearer capability), but MUST expose metadata
        only — never skill bodies or filenames."""
        _md_skills["urban_planning_x"] = {
            "description": "城市规划设计",
            "body": "SECRET BODY 分析城市布局...",
            "filename": "urban_planning.md",
        }
        resp = client.get("/api/v1/chat/skills")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["skills"] == [
            {"name": "urban_planning_x", "description": "城市规划设计"}
        ]
        raw = resp.text
        assert "SECRET BODY" not in raw
        assert "urban_planning.md" not in raw
        _md_skills.pop("urban_planning_x", None)

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
