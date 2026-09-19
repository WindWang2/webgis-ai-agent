"""API-05: POST /chat/sessions/{id}/map-state must not 204 a rejected write.

The route discarded the boolean from ``set_map_state``: a stale-seq viewport
rejection (F4 out-of-order guard) and a base_layer persistence failure both
returned the 204 success status. Assert 409 (stale seq) / 503 (write failed)
and that an applied write still returns the 204 default.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.routes.chat import MapStatePushRequest, push_session_map_state


def _patch_store(monkeypatch, result):
    fake = MagicMock()
    fake.set_map_state = AsyncMock(return_value=result)
    monkeypatch.setattr(
        "app.services.session_data.session_data_manager", fake
    )
    return fake


@pytest.mark.asyncio
async def test_stale_seq_viewport_rejected_with_409(monkeypatch):
    _patch_store(monkeypatch, False)
    req = MapStatePushRequest(
        viewport={"center": [116.4, 39.9], "zoom": 10}, seq=1
    )

    with pytest.raises(HTTPException) as exc_info:
        await push_session_map_state("sess-stale", req, _conv=MagicMock())

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_base_layer_write_failure_returns_503(monkeypatch):
    _patch_store(monkeypatch, False)
    req = MapStatePushRequest(base_layer="streets")

    with pytest.raises(HTTPException) as exc_info:
        await push_session_map_state("sess-fail", req, _conv=MagicMock())

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_applied_viewport_write_still_succeeds(monkeypatch):
    fake = _patch_store(monkeypatch, True)
    req = MapStatePushRequest(
        viewport={"center": [116.4, 39.9], "zoom": 10}, seq=2
    )

    result = await push_session_map_state("sess-ok", req, _conv=MagicMock())

    assert result is None  # 204 route default
    fake.set_map_state.assert_awaited_once_with(
        "sess-ok",
        "viewport",
        {"center": [116.4, 39.9], "zoom": 10},
        seq=2,
    )
