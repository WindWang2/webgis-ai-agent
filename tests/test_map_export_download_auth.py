"""#515 regression — export download auth contract.

The download route (`map.py::download_map_export`) requires `get_current_user`
(Bearer) and, when ownership is recorded, the requesting user must own the
file (审计 P0 IDOR guard). The frontend fix routes downloads through the
authenticated transport; this file pins the backend contract the frontend
now satisfies: anonymous → 401, owner → 200, non-owner → 403, missing → 404.
"""
import os

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.api.routes import map as _mod
from app.core.auth import get_current_user

# /tmp 在当前开发机是配额受限的 tmpfs（写入报 EDQUOT）；改用 /var/tmp。
_TEST_EXPORT_DIR = "/var/tmp/test_exports_download_auth"
os.makedirs(_TEST_EXPORT_DIR, exist_ok=True)

_owner_user = {"user_id": "file-owner"}
_intruder_user = {"user_id": "other-user"}


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(_mod.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _write_export_file(name: str, content: bytes) -> str:
    path = os.path.join(_TEST_EXPORT_DIR, name)
    with open(path, "wb") as f:
        f.write(content)
    return path


@pytest.fixture(autouse=True)
def _clean():
    _mod._EXPORT_OWNERS.clear()
    for fn in os.listdir(_TEST_EXPORT_DIR):
        os.remove(os.path.join(_TEST_EXPORT_DIR, fn))
    yield
    _mod._EXPORT_OWNERS.clear()


@pytest.mark.asyncio
async def test_download_requires_bearer(client):
    """匿名（无 Bearer）→ 401：下载端点不得退化为公共路径。"""
    name = "anon_map.png"
    _write_export_file(name, b"png")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mod, "EXPORT_DIR", _TEST_EXPORT_DIR)
        resp = await client.get(f"/api/v1/export/download/{name}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_download_owner_bearer_succeeds(client):
    """带有效 Bearer 且为文件所有者 → 200 + 文件体。"""
    name = "owner_map.png"
    _write_export_file(name, b"owner-png-bytes")
    app = client._transport.app
    app.dependency_overrides[get_current_user] = lambda: _owner_user
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mod, "EXPORT_DIR", _TEST_EXPORT_DIR)
        mp.setattr(_mod, "_EXPORT_OWNERS", {name: "file-owner"})
        resp = await client.get(f"/api/v1/export/download/{name}")
    assert resp.status_code == 200
    assert resp.content == b"owner-png-bytes"
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_download_non_owner_forbidden(client):
    """已认证但不是文件所有者 → 403（IDOR 守卫）。"""
    name = "secret_map.png"
    _write_export_file(name, b"secret")
    app = client._transport.app
    app.dependency_overrides[get_current_user] = lambda: _intruder_user
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mod, "EXPORT_DIR", _TEST_EXPORT_DIR)
        mp.setattr(_mod, "_EXPORT_OWNERS", {name: "file-owner"})
        resp = await client.get(f"/api/v1/export/download/{name}")
    assert resp.status_code == 403
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_download_missing_file_404(client):
    """不存在文件 → 404（不泄漏文件存在性）。"""
    app = client._transport.app
    app.dependency_overrides[get_current_user] = lambda: _owner_user
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mod, "EXPORT_DIR", "/tmp/nonexistent_exports_dir")
        resp = await client.get("/api/v1/export/download/ghost.png")
    assert resp.status_code == 404
    app.dependency_overrides.clear()


# ─── #616 regression: owner 侧车缺失时 fail-closed ──────────────────────────
# 此前 owner 为 None（进程重启清空内存 LRU + .owner 侧车丢失/写失败/多 worker
# NFS 未同步）会对全体认证用户放行下载。现改为 fail-closed 404（与文件不存在
# 同响应，不泄漏存在性），与 layer/raster 路由的 fail-closed 语义一致。


@pytest.mark.asyncio
async def test_download_owner_unknown_fails_closed(client):
    """文件存在但内存 LRU 与 .owner 侧车都拿不到 owner → 404（不再放行）。"""
    name = "no_owner_map.png"
    _write_export_file(name, b"secret-bytes")
    app = client._transport.app
    app.dependency_overrides[get_current_user] = lambda: _intruder_user
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mod, "EXPORT_DIR", _TEST_EXPORT_DIR)
        # 模拟进程重启后的空 LRU —— 必须保持 OrderedDict 语义（
        # _export_owners_remember 调用 move_to_end；普通 dict 会 AttributeError）
        mp.setattr(_mod, "_EXPORT_OWNERS", _mod._OD())
        resp = await client.get(f"/api/v1/export/download/{name}")
    assert resp.status_code == 404, "owner 缺失时必须 fail-closed，不得对任意认证用户放行"
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_download_owner_from_sidecar_succeeds(client):
    """回归保护：.owner 侧车（多 worker 持久化兜底）仍能证明归属 ——
    所有者 200、他人 403。进程重启后侧车是唯一所有权事实源。"""
    name = "sidecar_map.png"
    _write_export_file(name, b"sidecar-png")
    with open(os.path.join(_TEST_EXPORT_DIR, f"{name}.owner"), "w", encoding="utf-8") as f:
        f.write("file-owner")
    app = client._transport.app
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mod, "EXPORT_DIR", _TEST_EXPORT_DIR)
        mp.setattr(_mod, "_EXPORT_OWNERS", _mod._OD())  # 空 LRU（重启场景）
        app.dependency_overrides[get_current_user] = lambda: _owner_user
        owner_resp = await client.get(f"/api/v1/export/download/{name}")
        app.dependency_overrides[get_current_user] = lambda: _intruder_user
        intruder_resp = await client.get(f"/api/v1/export/download/{name}")
    assert owner_resp.status_code == 200
    assert owner_resp.content == b"sidecar-png"
    assert intruder_resp.status_code == 403
    app.dependency_overrides.clear()
