"""SEC-07 regression: /storymap/compile and /storymap/export require auth.

Deep-review swarm 2026-09-19 (SEC-07): both endpoints were anonymous
(arbitrary spec compilation / HTML rendering with no identity).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import storymap as storymap_routes


def _app(*, authenticated: bool) -> FastAPI:
    from app.core.auth import get_current_user

    app = FastAPI()
    app.include_router(storymap_routes.router, prefix="/api/v1")
    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "sec07", "role": "editor",
        }
    return app


async def _post(path: str, payload: dict, *, authenticated: bool):
    app = _app(authenticated=authenticated)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(path, json=payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/v1/storymap/compile", {"messages": [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]}),
        ("/api/v1/storymap/export", {"spec": {}, "format": "json"}),
    ],
)
async def test_storymap_endpoints_require_auth(path, payload):
    assert (await _post(path, payload, authenticated=False)).status_code == 401


@pytest.mark.asyncio
async def test_storymap_compile_works_when_authenticated():
    resp = await _post(
        "/api/v1/storymap/compile",
        {"messages": [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]},
        authenticated=True,
    )
    assert resp.status_code == 200
