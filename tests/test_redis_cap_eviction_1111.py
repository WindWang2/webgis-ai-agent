"""Regression for #1111: Redis store() capacity eviction must invalidate
spatial_index_cache + tile_lru_cache (not only ref_payload_cache).

Pre-fix: store() eviction only called ref_payload_cache.invalidate, while
delete_ref / overwrite / MemorySessionStore eviction invalidate all three.
An evicted ref whose spatial index / tile LRU still hit kept serving 200
ghost tiles/features after the Redis data key was gone.

Mirrors the capacity-overflow spirit of tests/test_cap_eviction_522.py for
the Redis session-data backend (small capacity → store past limit → assert
evicted refs are fully gone from process caches and data-plane endpoints).
"""
import os

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Must set before app.* imports that read settings.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-sec08-32-chars-okxxxxx")
os.environ.setdefault("ENV", "development")

from app.api.routes import layer as _mod  # noqa: E402
from app.core.auth import require_owned_session  # noqa: E402
from app.models.db_model import Conversation  # noqa: E402
from app.services.mvt import (  # noqa: E402
    build_spatial_index_entry,
    spatial_index_cache,
    tile_lru_cache,
)
from app.services.ref_payload_cache import ref_payload_cache  # noqa: E402
from app.services.session_data_redis import RedisSessionStore  # noqa: E402

_VALID_SID = "session-bbbbbbbbbbbbbbbb"


def _fc(name: str = "ghost"):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "feat-1",
                "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                "properties": {"name": name},
            }
        ],
    }


@pytest.fixture(autouse=True)
def _clear_process_caches():
    spatial_index_cache.clear()
    tile_lru_cache.clear()
    ref_payload_cache.invalidate_session(_VALID_SID)
    yield
    spatial_index_cache.clear()
    tile_lru_cache.clear()
    ref_payload_cache.invalidate_session(_VALID_SID)


@pytest.fixture
async def redis_store():
    import fakeredis.aioredis

    raw = fakeredis.aioredis.FakeRedis(decode_responses=False)
    store = RedisSessionStore(redis_url="redis://unused", redis=raw, capacity=1)
    yield store
    await raw.aclose()


def _seed_caches(session_id: str, ref_id: str, fc: dict) -> None:
    """Simulate a prior MVT/feature hit that populated process caches."""
    key = (session_id, ref_id)
    entry = build_spatial_index_entry(key, fc)
    spatial_index_cache._entries[key] = entry
    spatial_index_cache._total_bytes += getattr(entry, "estimated_bytes", 0)
    tile_lru_cache.put((session_id, ref_id, 0, 0, 0), b"\x1f\x8b" + b"ghost-tile")
    ref_payload_cache.put(session_id, ref_id, fc, 64)


@pytest.mark.asyncio
async def test_redis_store_eviction_invalidates_all_three_caches(redis_store):
    """Capacity overflow on Redis store() must drop spatial + tile + payload caches."""
    first_fc = _fc("first")
    first = await redis_store.store(_VALID_SID, first_fc, prefix="data")
    _seed_caches(_VALID_SID, first, first_fc)

    assert spatial_index_cache.get((_VALID_SID, first)) is not None
    assert tile_lru_cache.get((_VALID_SID, first, 0, 0, 0)) is not None
    assert ref_payload_cache.get(_VALID_SID, first) is not None

    second = await redis_store.store(_VALID_SID, _fc("second"), prefix="data")
    assert second != first
    assert await redis_store.get(_VALID_SID, first) is None
    assert await redis_store.get(_VALID_SID, second) is not None

    assert spatial_index_cache.get((_VALID_SID, first)) is None, (
        "spatial_index_cache must be invalidated on Redis capacity eviction (#1111)"
    )
    assert tile_lru_cache.get((_VALID_SID, first, 0, 0, 0)) is None, (
        "tile_lru_cache must be invalidated on Redis capacity eviction (#1111)"
    )
    assert ref_payload_cache.get(_VALID_SID, first) is None


@pytest.mark.asyncio
async def test_redis_evicted_ref_tile_and_feature_endpoints_404(redis_store, monkeypatch):
    """Acceptance: after Redis store eviction, tile + feature endpoints 404
    (no ghost 200 from leftover process caches). Surviving refs still work."""
    async def _noop_verify(session_id, user_id, owner_token=None):
        return None

    monkeypatch.setattr(_mod, "_verify_session_owner", _noop_verify)
    monkeypatch.setattr(_mod, "session_data_manager", redis_store)

    app = FastAPI()
    app.dependency_overrides[require_owned_session] = lambda: Conversation(id=_VALID_SID)
    app.include_router(_mod.router, prefix="/api/v1")

    first_fc = _fc("first")
    first = await redis_store.store(_VALID_SID, first_fc, prefix="data")
    _seed_caches(_VALID_SID, first, first_fc)

    second = await redis_store.store(_VALID_SID, _fc("second"), prefix="data")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Evicted ref: must not serve from ghost spatial/tile caches.
        feat = await client.get(
            f"/api/v1/layers/data/{first}/feature/feat-1",
            params={"session_id": _VALID_SID},
        )
        assert feat.status_code == 404, feat.text

        tile = await client.get(
            f"/api/v1/layers/data/{first}/tiles/0/0/0.mvt",
            params={"session_id": _VALID_SID},
        )
        assert tile.status_code == 404, tile.text

        # Non-evicted ref still serves.
        keep = await client.get(
            f"/api/v1/layers/data/{second}/feature/feat-1",
            params={"session_id": _VALID_SID},
        )
        assert keep.status_code == 200
        assert keep.json()["properties"]["name"] == "second"
