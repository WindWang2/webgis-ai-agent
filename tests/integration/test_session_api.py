"""Integration tests for session map-state API endpoint."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from app.api.routes import chat as _chat_mod
from app.api.routes.chat import router
from app.lib.cartography.quality_loop import cartographic_fingerprint


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
        with patch.object(_chat_mod.AsyncHistoryService, "get_session_meta", AsyncMock(return_value=mock_conv)):
            resp = client.get("/api/v1/chat/sessions/sess-123/map-state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess-123"
        assert data["map_state"]["base_layer"] == "dark"

    @patch("app.services.session_data.session_data_manager")
    def test_get_map_state_empty(self, mock_sdm, client):
        mock_sdm.get_map_state = AsyncMock(return_value={})
        mock_conv = MagicMock()
        with patch.object(_chat_mod.AsyncHistoryService, "get_session_meta", AsyncMock(return_value=mock_conv)):
            resp = client.get("/api/v1/chat/sessions/sess-404/map-state")
        assert resp.status_code == 200
        assert resp.json()["map_state"] == {}

    @patch("app.services.session_data.session_data_manager")
    def test_get_map_state_stamps_current_cartographic_generation(self, mock_sdm, client):
        mapspec = {
            "version": 1,
            "sources": {},
            "layers": [],
            "view": {},
            "layout": {},
        }
        mock_sdm.get_map_state = AsyncMock(return_value={
            "mapspec": mapspec,
            "_cartographic_observation": {
                "mapspec_fingerprint": "carto-sha256:stale",
                "layers": [],
            },
        })
        mock_conv = MagicMock()
        with patch.object(
            _chat_mod.AsyncHistoryService,
            "get_session_meta",
            AsyncMock(return_value=mock_conv),
        ):
            resp = client.get("/api/v1/chat/sessions/sess-current/map-state")

        assert resp.status_code == 200
        state = resp.json()["map_state"]
        assert state["_current_cartographic_fingerprint"] == (
            cartographic_fingerprint(mapspec)
        )

    @patch("app.services.session_data.session_data_manager")
    def test_push_map_state_forwards_viewport_seq(self, mock_sdm, client):
        """F4: the throttled POST forwards its monotonic seq to set_map_state,
        so the backend can reject an out-of-order older POST that lands after
        the turn-start write."""
        mock_sdm.set_map_state = AsyncMock(return_value=True)
        mock_conv = MagicMock()
        with patch.object(_chat_mod.AsyncHistoryService, "get_session_meta", AsyncMock(return_value=mock_conv)):
            resp = client.post(
                "/api/v1/chat/sessions/sess-123/map-state",
                json={"viewport": {"center": [116.4, 39.9], "zoom": 10, "bearing": 0, "pitch": 0}, "seq": 3},
            )
        assert resp.status_code == 204
        mock_sdm.set_map_state.assert_awaited_once_with(
            "sess-123", "viewport",
            {"center": [116.4, 39.9], "zoom": 10, "bearing": 0, "pitch": 0},
            seq=3,
        )

    @patch("app.services.session_data.session_data_manager")
    def test_push_map_state_without_seq_is_still_accepted(self, mock_sdm, client):
        """Backward compat: a client that predates the seq contract still gets
        an unsequenced (always-apply) write."""
        mock_sdm.set_map_state = AsyncMock(return_value=True)
        mock_conv = MagicMock()
        with patch.object(_chat_mod.AsyncHistoryService, "get_session_meta", AsyncMock(return_value=mock_conv)):
            resp = client.post(
                "/api/v1/chat/sessions/sess-123/map-state",
                json={"viewport": {"center": [116.4, 39.9], "zoom": 10}},
            )
        assert resp.status_code == 204
        mock_sdm.set_map_state.assert_awaited_once_with(
            "sess-123", "viewport", {"center": [116.4, 39.9], "zoom": 10}, seq=None
        )
