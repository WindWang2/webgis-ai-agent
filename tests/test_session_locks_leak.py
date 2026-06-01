"""Test: _session_locks must be cleaned up when sessions are cleared."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _make_engine():
    from app.tools.registry import ToolRegistry
    with patch("app.services.chat_engine.settings") as mock_settings:
        mock_settings.LLM_MODEL = "test"
        mock_settings.LLM_API_KEY = "test-key"
        mock_settings.LLM_PROMPT_CACHING_ENABLED = False
        mock_settings.LLM_BASE_URL = "http://localhost"
        from app.services.chat_engine import ChatEngine
        return ChatEngine(ToolRegistry())


class TestSessionLocksCleanup:
    @pytest.mark.asyncio
    async def test_clear_session_removes_lock(self):
        """clear_session must remove the session's lock from _session_locks."""
        engine = _make_engine()
        sid = "sess-to-delete"

        engine._session_locks[sid] = asyncio.Lock()
        engine._sessions[sid] = []

        mock_history = AsyncMock()
        mock_history.delete_session = AsyncMock(return_value=True)

        with patch("app.services.chat_engine.async_db_session") as mock_db_ctx, \
             patch("app.services.chat_engine.AsyncHistoryService", return_value=mock_history), \
             patch("app.services.chat_engine.session_data_manager") as mock_sdm:
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_sdm.clear_session = AsyncMock()

            result = await engine.clear_session(sid)

        assert result is True
        assert sid not in engine._session_locks, (
            f"Lock for {sid} was not cleaned up. Remaining: {list(engine._session_locks.keys())}"
        )

    @pytest.mark.asyncio
    async def test_clear_session_only_removes_target_lock(self):
        """clear_session must only remove the target session's lock."""
        engine = _make_engine()
        engine._session_locks["sess-keep"] = asyncio.Lock()
        engine._session_locks["sess-delete"] = asyncio.Lock()
        engine._sessions["sess-delete"] = []

        mock_history = AsyncMock()
        mock_history.delete_session = AsyncMock(return_value=True)

        with patch("app.services.chat_engine.async_db_session") as mock_db_ctx, \
             patch("app.services.chat_engine.AsyncHistoryService", return_value=mock_history), \
             patch("app.services.chat_engine.session_data_manager") as mock_sdm:
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_sdm.clear_session = AsyncMock()

            await engine.clear_session("sess-delete")

        assert "sess-keep" in engine._session_locks
        assert "sess-delete" not in engine._session_locks
