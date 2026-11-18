"""#2210 HTTP caller integration, using real auth and ownership guards."""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes import chat
from app.core.auth import create_access_token
from app.core.config import settings
from app.core.database import get_async_db
from app.models.db_model import Base, User
from app.utils.sse import sse_event


@pytest.fixture
async def caller_http(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def database():
        async with factory() as db:
            yield db
            await db.commit()

    async def database_dependency():
        async with database() as db:
            yield db

    calls = []

    class Bridge:
        async def prompt(self, message, **kwargs):
            calls.append(kwargs)
            return {"sessionId": kwargs["session_id"], "content": "answer"}

        async def stream_prompt(self, message, **kwargs):
            calls.append(kwargs)
            yield sse_event("done", {"session_id": kwargs["session_id"]})

    async def no_progress(*args, **kwargs):
        if False:
            yield ""

    bridge = Bridge()
    # Only infrastructure/LLM/derived UI seams are replaced. In particular,
    # no overrides of auth, ownership, token matching, history, or guards.
    monkeypatch.setattr(settings, "AUTH_DISABLED", False)
    monkeypatch.setattr(chat, "_ensure_pi_bridge_available", AsyncMock(return_value=True))
    monkeypatch.setattr(chat, "_pi_turn_bridge", lambda sid: bridge)
    monkeypatch.setattr(chat, "_record_frontend_cartographic_observation", AsyncMock())
    monkeypatch.setattr(chat, "_build_situation_env_block", AsyncMock(return_value=""))
    monkeypatch.setattr(chat, "_build_cartography_turn_context", AsyncMock(return_value=""))
    monkeypatch.setattr(chat, "_maybe_bind_pi_mission", AsyncMock())
    monkeypatch.setattr(chat, "async_db_session", database)
    monkeypatch.setattr("app.core.database.async_db_session", database)
    monkeypatch.setattr(chat, "bridge_session_explorer_progress", no_progress)
    monkeypatch.setattr(chat, "get_engine", lambda: SimpleNamespace(
        _fire_and_forget=MagicMock(), _generate_title=MagicMock(),
    ))
    monkeypatch.setattr("app.services.gis_harness.map_completion.maybe_finalize_map_product", AsyncMock())
    app = FastAPI()
    app.include_router(chat.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = database_dependency
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, calls=calls, database=database)
    await engine.dispose()


def session_receipt(response, mode):
    assert response.status_code == 200, response.text
    if mode == "completions":
        return response.json()
    for frame in response.text.split("\n\n"):
        if "event: session" in frame:
            return json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))
    return {}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["completions", "stream"])
async def test_anonymous_http_caller_capability_is_validated_and_session_scoped(caller_http, mode):
    client, calls = caller_http.client, caller_http.calls
    sid_a, sid_b = str(uuid.uuid4()), str(uuid.uuid4())
    url = f"/api/v1/chat/{mode}"

    async def send(sid, token=None):
        return await client.post(url, json={"message": "hello", "session_id": sid},
                                 headers={"X-Session-Token": token} if token else {})

    first = session_receipt(await send(None, "unvalidated-stale-header"), mode)
    sid_a = first["session_id"]
    token_a = first["owner_token"]
    assert token_a != "unvalidated-stale-header"
    assert calls[-1]["context_owner_token"] == token_a
    assert calls[-1]["context_user_id"] == ""
    assert calls[-1]["session_id"] == sid_a

    second = session_receipt(await send(sid_b, token_a), mode)
    token_b = second["owner_token"]
    assert token_b != token_a
    assert calls[-1]["context_owner_token"] == token_b
    assert calls[-1]["session_id"] == sid_b

    followup = session_receipt(await send(sid_a, token_a), mode)
    assert not followup.get("owner_token")  # never re-emit stored credentials
    assert calls[-1]["context_owner_token"] == token_a
    assert calls[-1]["context_user_id"] == ""
    assert calls[-1]["session_id"] == sid_a
    count = len(calls)
    for invalid in (token_b, "wrong-token", None):
        denied = await send(sid_a, invalid)
        assert denied.status_code == 404, denied.text
        assert len(calls) == count
    session_receipt(await send(sid_b, token_b), mode)
    assert calls[-1]["context_owner_token"] == token_b
    assert calls[-1]["session_id"] == sid_b


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["completions", "stream"])
async def test_authenticated_http_caller_does_not_forward_unvalidated_owner_token(caller_http, mode):
    uid, other_uid, sid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    async with caller_http.database() as db:
        for user_id in (uid, other_uid):
            db.add(User(id=user_id, username=user_id, email=f"{user_id}@test.invalid",
                        role="editor", is_active=True, token_version=0))
    jwt = create_access_token({"sub": uid, "role": "editor"}, token_version=0)
    other_jwt = create_access_token({"sub": other_uid, "role": "editor"}, token_version=0)
    url = f"/api/v1/chat/{mode}"
    for _ in range(2):  # new and existing authenticated session
        response = await caller_http.client.post(
            url, json={"message": "hello", "session_id": sid},
            headers={"Authorization": f"Bearer {jwt}", "X-Session-Token": "unvalidated-other-cap"},
        )
        receipt = session_receipt(response, mode)
        assert not receipt.get("owner_token")
        call = caller_http.calls[-1]
        assert call["context_user_id"] == uid
        assert call.get("context_owner_token") is None
        assert call["session_id"] == sid
    count = len(caller_http.calls)
    denied = await caller_http.client.post(
        url, json={"message": "other user", "session_id": sid},
        headers={"Authorization": f"Bearer {other_jwt}"},
    )
    assert denied.status_code == 404, denied.text
    assert len(caller_http.calls) == count


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["completions", "stream"])
async def test_signed_caller_still_needs_valid_capability_for_anonymous_session(caller_http, mode):
    url = f"/api/v1/chat/{mode}"
    first = session_receipt(await caller_http.client.post(url, json={"message": "new"}), mode)
    sid, capability = first["session_id"], first["owner_token"]
    uid = str(uuid.uuid4())
    async with caller_http.database() as db:
        db.add(User(id=uid, username=uid, email=f"{uid}@test.invalid",
                    role="editor", is_active=True, token_version=0))
    jwt = create_access_token({"sub": uid, "role": "editor"}, token_version=0)
    count = len(caller_http.calls)
    invalid = await caller_http.client.post(
        url, json={"message": "reuse", "session_id": sid},
        headers={"Authorization": f"Bearer {jwt}", "X-Session-Token": "unvalidated"},
    )
    assert invalid.status_code == 404
    assert len(caller_http.calls) == count
    valid = await caller_http.client.post(
        url, json={"message": "reuse", "session_id": sid},
        headers={"Authorization": f"Bearer {jwt}", "X-Session-Token": capability},
    )
    session_receipt(valid, mode)
    assert caller_http.calls[-1]["context_user_id"] == uid
    assert caller_http.calls[-1]["context_owner_token"] == capability
