"""HTTP replay must prove live resource access even alongside a valid Bearer."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.auth import create_access_token, invalidate_live_user_state
from app.core.database import Base, get_async_db
from app.core.idempotency import IdempotencyMiddleware
from app.models.db_model import Conversation, User
from app.services.auth_history_bridge import require_owned_session


@pytest_asyncio.fixture
async def replay_env(tmp_path, monkeypatch):
    import fakeredis.aioredis
    from app.api.routes.chat import rotate_session_owner_token
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'replay.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add(User(id='replay-user', username='replay-user', email='replay@example.test', password_hash='fixture',
                    role='viewer', is_active=True, token_version=0))
        db.add(Conversation(id='anonymous-replay', owner_token='original-secret'))
        db.add(Conversation(id='owned-replay', user_id='replay-user'))
        await db.commit()
    @asynccontextmanager
    async def session():
        async with factory() as db:
            yield db
    async def dependency():
        async with session() as db:
            yield db
    monkeypatch.setattr('app.core.database.async_db_session', session)
    monkeypatch.setattr('app.core.async_runner.current_loop_async_session', session)
    monkeypatch.setattr('app.api.owner_token_rotation.session_data_manager.set_map_state', AsyncMock(return_value=True))
    invalidate_live_user_state()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app = FastAPI()
    calls = []
    @app.post('/api/v1/chat/sessions/{session_id}/canvas-actions')
    async def owned_mutation(session_id: str, conv=Depends(require_owned_session)):
        calls.append(session_id)
        return {'accepted': session_id}
    app.add_api_route('/api/v1/chat/sessions/{session_id}/rotate-owner-token',
                      rotate_session_owner_token, methods=['POST'])
    app.dependency_overrides[get_async_db] = dependency
    app.add_middleware(IdempotencyMiddleware, redis=SimpleNamespace(client=AsyncMock(return_value=redis)))
    bearer = 'Bearer ' + create_access_token({'sub': 'replay-user', 'role': 'viewer'})
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            yield SimpleNamespace(client=client, factory=factory, redis=redis, calls=calls, bearer=bearer, app=app)
    finally:
        invalidate_live_user_state()
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_bearer_does_not_bypass_rotated_anonymous_capability_on_cached_session_response(replay_env):
    env = replay_env
    headers = {'Authorization': env.bearer, 'X-Session-Token': 'original-secret', 'Idempotency-Key': 'same'}
    url = '/api/v1/chat/sessions/anonymous-replay/canvas-actions'
    assert (await env.client.post(url, json={}, headers=headers)).status_code == 200
    replay = await env.client.post(url, json={}, headers=headers)
    assert replay.status_code == 200 and replay.headers.get('Idempotent-Replay') == 'true'
    async with env.factory() as db:
        row = await db.get(Conversation, 'anonymous-replay')
        row.owner_token = 'replacement-secret'
        await db.commit()
    denied = await env.client.post(url, json={}, headers=headers)
    assert denied.status_code == 404
    assert 'Idempotent-Replay' not in denied.headers
    assert env.calls == ['anonymous-replay']


@pytest.mark.asyncio
async def test_rotation_never_reads_or_writes_replay_cache_even_with_bearer_and_previous_secret(replay_env):
    env = replay_env
    headers = {'Authorization': env.bearer, 'X-Session-Token': 'original-secret', 'Idempotency-Key': 'rotate'}
    url = '/api/v1/chat/sessions/anonymous-replay/rotate-owner-token'
    first = await env.client.post(url, json={}, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()['owner_token'] != 'original-secret'
    assert await env.redis.keys('idem:*') == []
    replay = await env.client.post(url, json={}, headers=headers)
    assert replay.status_code == 404
    assert first.json()['owner_token'] not in replay.text


@pytest.mark.asyncio
async def test_changed_session_owner_invalidates_bearer_replay_without_user_role_change(replay_env):
    env = replay_env
    headers = {'Authorization': env.bearer, 'Idempotency-Key': 'owned'}
    url = '/api/v1/chat/sessions/owned-replay/canvas-actions'
    assert (await env.client.post(url, json={}, headers=headers)).status_code == 200
    async with env.factory() as db:
        row = await db.get(Conversation, 'owned-replay')
        row.user_id = 'another-owner'
        await db.commit()
    assert (await env.client.post(url, json={}, headers=headers)).status_code == 404
    assert env.calls == ['owned-replay']


@pytest.mark.asyncio
async def test_unproven_resource_routes_execute_their_own_authorization_every_time(replay_env):
    env = replay_env
    allowed = [True]
    @env.app.post('/api/v1/projects/private/export')
    async def export():
        if not allowed[0]:
            raise HTTPException(404, 'Project not found')
        return {'private': 'project output'}
    headers = {'Authorization': env.bearer, 'Idempotency-Key': 'project'}
    assert (await env.client.post('/api/v1/projects/private/export', json={}, headers=headers)).status_code == 200
    allowed[0] = False
    denied = await env.client.post('/api/v1/projects/private/export', json={}, headers=headers)
    assert denied.status_code == 404 and 'project output' not in denied.text
    assert await env.redis.keys('idem:*') == []
