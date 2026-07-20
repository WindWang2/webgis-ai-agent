"""Integration tests for session map-state API endpoint."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import importlib.util
import os
from fastapi.testclient import TestClient

# Load chat module directly without triggering __init__.py
_spec = importlib.util.spec_from_file_location(
    "app.api.routes.chat",
    os.path.join(os.path.dirname(__file__), "..", "..", "app", "api", "routes", "chat.py"),
    submodule_search_locations=[]
)
_chat_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_chat_mod)
router = _chat_mod.router


@pytest.fixture
def client():
    """Create TestClient with a minimal app that includes only the chat router."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


class TestSessionMapStateAPI:
    @patch("app.services.session_data.session_data_manager")
    def test_get_map_state_returns_state(self, mock_sdm, client):
        """审计 S31：map-state 端点现在做所有权校验 —— 需 stub AsyncHistoryService
        让所有权检查通过（跨租户隔离的正向/负向 case 由 test_cross_tenant_isolation
        端到端覆盖）。"""
        mock_sdm.get_map_state = AsyncMock(return_value={
            "base_layer": "dark",
            "layers": [{"id": "l1", "type": "geojson"}],
        })
        # 让所有权校验通过：AsyncHistoryService(...).get_session 返回 truthy
        mock_conv = MagicMock()
        with patch.object(_chat_mod.AsyncHistoryService, "get_session", AsyncMock(return_value=mock_conv)):
            resp = client.get("/api/v1/chat/sessions/sess-123/map-state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess-123"
        assert data["map_state"]["base_layer"] == "dark"

    @patch("app.services.session_data.session_data_manager")
    def test_get_map_state_empty(self, mock_sdm, client):
        mock_sdm.get_map_state = AsyncMock(return_value={})
        mock_conv = MagicMock()
        with patch.object(_chat_mod.AsyncHistoryService, "get_session", AsyncMock(return_value=mock_conv)):
            resp = client.get("/api/v1/chat/sessions/sess-404/map-state")
        assert resp.status_code == 200
        assert resp.json()["map_state"] == {}
