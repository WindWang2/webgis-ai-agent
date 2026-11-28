"""作者请求助手（带依赖覆写的裸 ASGI 调用；与 author_client 同语义）。"""
from __future__ import annotations

from typing import Any, Optional

from httpx import ASGITransport, AsyncClient


async def author_request(
    world: dict,
    method: str,
    path: str,
    *,
    json: Optional[dict] = None,
    token: str = "owner-token-a-secret-value",
    user_id: Optional[str] = None,
) -> tuple[int, Any]:
    """以作者身份（owner_token 链）调用 publications 路由；返回 (status, body)。"""
    from app.core.auth import get_current_user_optional, get_owner_token
    from tests.unit.publication.conftest import make_app

    app = make_app(world["db_factory"])

    async def _user():
        return {"user_id": user_id, "org_id": None}

    async def _token():
        return token

    app.dependency_overrides[get_current_user_optional] = _user
    app.dependency_overrides[get_owner_token] = _token
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.request(method, path, json=json)
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001 — 304/空体
            body = None
        return resp.status_code, body


async def anon_request(world: dict, method: str, path: str) -> tuple[int, Any]:
    """匿名读者请求。"""
    from app.core.auth import get_current_user_optional, get_owner_token
    from tests.unit.publication.conftest import make_app

    app = make_app(world["db_factory"])

    async def _user():
        return {"user_id": None, "org_id": None}

    async def _token():
        return None

    app.dependency_overrides[get_current_user_optional] = _user
    app.dependency_overrides[get_owner_token] = _token
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.request(method, path)
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = None
        return resp.status_code, body
