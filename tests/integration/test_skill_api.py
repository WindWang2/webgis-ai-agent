"""Integration tests for skill API endpoints using FastAPI TestClient."""
import pytest
import importlib.util
import os
from fastapi.testclient import TestClient

from app.tools.skills import _md_skills

# Load chat module directly without triggering __init__.py
_spec = importlib.util.spec_from_file_location(
    "app.api.routes.chat",
    os.path.join(os.path.dirname(__file__), "..", "..", "app", "api", "routes", "chat.py"),
    submodule_search_locations=[]
)
_chat_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_chat_mod)
router = _chat_mod.router


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
