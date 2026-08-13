"""
HTTP-level tests for ``GET /api/v1/layers/descriptor/{ref_id}``
(app/api/routes/layer.py ``get_layer_descriptor``) — the V3 metadata fast path.

Route behaviour under test:
- seam success → 200 with the descriptor fields, no payload hydration
  (store.get / store.get_ref_data never called)
- PermissionDenied → 403, WITHOUT falling back to get_ref_data
- NotFound → existing fallback via get_ref_data(): pre-V3 refs are recomputed
  into a 200 with correct feature_count; genuinely missing refs → 404

Memory backend plus one Redis-on-fakeredis case: store-level dual-backend
parity is covered by tests/unit/test_descriptor_auth_fastpath.py. A fresh
MemorySessionStore is injected as the route module's ``session_data_manager``
per test (module-level import, so the ``app.api.routes.layer`` attribute is
patched); the Redis case injects a fakeredis-backed RedisSessionStore the same
way to lock in the seam-internal on-the-fly recompute path unique to Redis.
"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch
from fastapi import FastAPI

from app.api.routes import layer as _mod
from app.core.auth import require_owned_session
from app.models.db_model import Conversation
from app.services.session_data import MemorySessionStore

_VALID_SID = "session-aaaaaaaaaaaaaaaa"  # >= min_length=8


def _point_fc(n=1000):
    """n-Point FeatureCollection; minimal payload (see store-level twin)."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [116.0 + i * 0.001, 39.9],
                },
                "properties": {"id": i},
            }
            for i in range(n)
        ],
    }


@pytest.fixture
def store():
    return MemorySessionStore()


@pytest.fixture
def app(monkeypatch, store):
    """Inject a fresh MemorySessionStore into the route module's
    ``session_data_manager`` name (layer.py imports it at module top, so the
    attribute on the module is patched). Cross-tenant guard stubbed to
    always-pass like tests/test_layer_api.py (isolation covered elsewhere)."""
    async def _noop_verify(session_id, user_id, owner_token=None):
        return None

    monkeypatch.setattr(_mod, "_verify_session_owner", _noop_verify)
    monkeypatch.setattr(_mod, "session_data_manager", store)

    app = FastAPI()
    app.dependency_overrides[require_owned_session] = lambda: Conversation(id=_VALID_SID)
    app.include_router(_mod.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_descriptor_fastpath_success_no_hydration(client, store):
    """Fast path: 200 with correct descriptor fields, zero payload reads."""
    ref_id = await store.store(_VALID_SID, _point_fc(1000), prefix="data")

    with patch.object(store, "get", wraps=store.get) as spy_get, patch.object(
        store, "get_ref_data", wraps=store.get_ref_data
    ) as spy_grd:
        resp = await client.get(
            f"/api/v1/layers/descriptor/{ref_id}",
            params={"session_id": _VALID_SID},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ref_id"] == ref_id
    assert body["session_id"] == _VALID_SID
    assert body["feature_count"] == 1000
    assert body["point_count"] == 1000
    assert body["geometry_types"] == ["Point"]
    assert body["bbox"] == [116.0, 39.9, 116.999, 39.9]
    assert body["mvt_capable"] is True
    assert body["raster_capable"] is False
    assert body["estimated_bytes"] == 1000 * 100 + 1024
    assert spy_get.call_count == 0
    assert spy_grd.call_count == 0


@pytest.mark.asyncio
async def test_descriptor_anonymous_correct_token_success(client, store):
    """Anonymous session guarded by an owner_token: correct X-Session-Token → 200."""
    await store.set_map_state(_VALID_SID, "owner_token", "secret-token")
    ref_id = await store.store(_VALID_SID, _point_fc(10), prefix="data")

    resp = await client.get(
        f"/api/v1/layers/descriptor/{ref_id}",
        params={"session_id": _VALID_SID},
        headers={"X-Session-Token": "secret-token"},
    )

    assert resp.status_code == 200
    assert resp.json()["feature_count"] == 10


@pytest.mark.asyncio
async def test_descriptor_wrong_token_403(client, store):
    """Wrong token → 403 (no fallback hydration attempted)."""
    await store.set_map_state(_VALID_SID, "owner_token", "secret-token")
    ref_id = await store.store(_VALID_SID, _point_fc(10), prefix="data")

    with patch.object(store, "get", wraps=store.get) as spy_get:
        resp = await client.get(
            f"/api/v1/layers/descriptor/{ref_id}",
            params={"session_id": _VALID_SID},
            headers={"X-Session-Token": "wrong-token"},
        )

    assert resp.status_code == 403
    assert spy_get.call_count == 0


@pytest.mark.asyncio
async def test_descriptor_missing_ref_404(client):
    """Never-stored ref → seam NotFound → fallback NotFound → 404."""
    resp = await client.get(
        "/api/v1/layers/descriptor/ref:missing",
        params={"session_id": _VALID_SID},
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_descriptor_cross_session_404(client, store):
    """Ref stored in another session → 404."""
    ref_id = await store.store("session-bbbbbbbbbbbbbbbb", _point_fc(10), prefix="data")

    resp = await client.get(
        f"/api/v1/layers/descriptor/{ref_id}",
        params={"session_id": _VALID_SID},
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_descriptor_orphaned_descriptor_404(client, store):
    """Descriptor residue with payload gone → seam NotFound → fallback
    get_ref_data also NotFound → 404 (stale descriptor must NOT be served)."""
    ref_id = await store.store(_VALID_SID, _point_fc(10), prefix="data")
    del store._store[_VALID_SID][ref_id]

    resp = await client.get(
        f"/api/v1/layers/descriptor/{ref_id}",
        params={"session_id": _VALID_SID},
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pre_v3_ref_fallback_still_works(client, store):
    """Pre-V3 ref (no stored descriptor): seam NotFound → route-level fallback
    get_ref_data() hydrates and recomputes → 200 with correct feature_count.
    get_ref_data IS legitimately called this time."""
    ref_id = await store.store(_VALID_SID, _point_fc(25), prefix="data")
    store._descriptors[_VALID_SID].pop(ref_id)

    with patch.object(store, "get_ref_data", wraps=store.get_ref_data) as spy_grd:
        resp = await client.get(
            f"/api/v1/layers/descriptor/{ref_id}",
            params={"session_id": _VALID_SID},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["feature_count"] == 25
    assert body["point_count"] == 25
    assert spy_grd.call_count >= 1


@pytest.mark.asyncio
async def test_descriptor_invalid_ref_id_400(client, store):
    """Existing guard: ref_id containing whitespace or exceeding 128 chars → 400."""
    for bad_ref in ("ref with space", "r" * 129):
        resp = await client.get(
            f"/api/v1/layers/descriptor/{bad_ref}",
            params={"session_id": _VALID_SID},
        )
        assert resp.status_code == 400, f"expected 400 for ref_id {bad_ref!r}"


# ─────────────────────────────────────────────── Redis end-to-end path ────
# Locking in the seam-internal on-the-fly recompute that only exists on Redis:
# when the descriptor/meta key is missing (pre-V3 ref, or its TTL expired first)
# RedisSessionStore.get_ref_descriptor reads the full payload, recomputes and
# caches the descriptor — the seam then returns success (Memory instead returns
# NotFound here and the route falls back to get_ref_data). End-user outcome is
# a 200 with correct fields on both backends; this case pins the Redis one.


def _redis_store_factory():
    """RedisSessionStore backed by in-process fakeredis (same injection as
    tests/unit/test_descriptor_auth_fastpath.py). The injected client is wired
    via the ``redis=`` test seam so `store._r` is already the FakeRedis."""
    import fakeredis.aioredis
    from app.services.session_data_redis import RedisSessionStore

    return RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
    )


@pytest.fixture
def redis_store():
    return _redis_store_factory()


@pytest.fixture
def redis_app(monkeypatch, redis_store):
    """Route app with the fakeredis-backed RedisSessionStore injected as
    ``session_data_manager`` (same patching as the memory ``app`` fixture)."""
    async def _noop_verify(session_id, user_id, owner_token=None):
        return None

    monkeypatch.setattr(_mod, "_verify_session_owner", _noop_verify)
    monkeypatch.setattr(_mod, "session_data_manager", redis_store)

    app = FastAPI()
    app.dependency_overrides[require_owned_session] = lambda: Conversation(id=_VALID_SID)
    app.include_router(_mod.router, prefix="/api/v1")
    return app


@pytest.fixture
async def redis_client(redis_app):
    transport = ASGITransport(app=redis_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_descriptor_redis_meta_key_missing_recomputes_200(redis_client, redis_store):
    """Redis only: meta key deleted (pre-V3 ref / early meta TTL expiry) →
    the seam's get_ref_descriptor recomputes from the payload on the fly →
    200 with correct descriptor fields. Asserts the exact same response
    contract as the memory fast-path success test."""
    sid = _VALID_SID
    ref_id = await redis_store.store(sid, _point_fc(1000), prefix="data")
    await redis_store._ensure_connected()
    await redis_store._r.delete(redis_store._descriptor_key(sid, ref_id))

    resp = await redis_client.get(
        f"/api/v1/layers/descriptor/{ref_id}",
        params={"session_id": sid},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ref_id"] == ref_id
    assert body["session_id"] == sid
    assert body["feature_count"] == 1000
    assert body["point_count"] == 1000
    assert body["geometry_types"] == ["Point"]
    assert body["bbox"] == [116.0, 39.9, 116.999, 39.9]
    assert body["mvt_capable"] is True
    assert body["raster_capable"] is False
    assert body["estimated_bytes"] == 1000 * 100 + 1024
