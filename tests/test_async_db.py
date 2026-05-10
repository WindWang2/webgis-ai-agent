"""Async database session tests"""
import pytest

from app.core.database import AsyncSessionLocal
from app.tools._utils import async_db_session


class TestAsyncDbSession:
    @pytest.mark.asyncio
    async def test_async_session_context_manager(self):
        async with async_db_session() as db:
            assert db is not None

    @pytest.mark.asyncio
    async def test_async_session_rollback_on_error(self):
        with pytest.raises(ValueError):
            async with async_db_session() as db:
                raise ValueError("test error")

    @pytest.mark.asyncio
    async def test_async_session_local_is_available(self):
        assert AsyncSessionLocal is not None
