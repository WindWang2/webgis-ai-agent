"""A4 静态文件路由：路径越界 / 签名 / 鉴权 / 公共白名单。

模型：建一个临时 DATA_DIR + 3 个文件（公共、私有、隐藏），跑各种访问组合。
"""
import os
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET_KEY", "test-static-secret-32chars-min-okkk")
os.environ.setdefault("ENV", "development")


@pytest_asyncio.fixture
async def app_and_dir(tmp_path, monkeypatch):
    # 准备 DATA_DIR + 几个文件
    data_dir = tmp_path / "data"
    (data_dir / "public").mkdir(parents=True)
    (data_dir / "private").mkdir(parents=True)
    (data_dir / "public" / "logo.png").write_bytes(b"PNG_PUB")
    (data_dir / "private" / "secret.txt").write_bytes(b"TOP_SECRET")
    (data_dir / ".hidden").write_bytes(b"HIDDEN")

    # 让 settings.DATA_DIR 指向临时目录
    from app.core import config as cfg
    monkeypatch.setattr(cfg.settings, "DATA_DIR", str(data_dir))

    # 重新 import 路由模块以让其读到新 settings（路由用 settings.DATA_DIR 在函数内 resolve，
    # 所以只要 settings 实例属性变了即可，不必 reload）
    from app.api.routes import static as static_routes

    app = FastAPI()
    app.include_router(static_routes.router, prefix="/api/v1")
    yield app, data_dir


@pytest_asyncio.fixture
async def client(app_and_dir):
    app, _ = app_and_dir
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_public_file_accessible_anonymously(client):
    resp = await client.get("/api/v1/static/public/logo.png")
    assert resp.status_code == 200
    assert resp.content == b"PNG_PUB"


@pytest.mark.asyncio
async def test_private_file_rejected_anonymously(client):
    resp = await client.get("/api/v1/static/private/secret.txt")
    # 不暴露存在性：返回 404
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_private_file_accessible_with_jwt(client):
    from app.core.auth import create_access_token
    token = create_access_token({"sub": "user-alice"})
    resp = await client.get(
        "/api/v1/static/private/secret.txt",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.content == b"TOP_SECRET"


@pytest.mark.asyncio
async def test_private_file_accessible_with_signed_url(client):
    from app.core.signing import sign_path
    rel = "private/secret.txt"
    exp, sig = sign_path(rel, ttl_seconds=300)
    resp = await client.get(f"/api/v1/static/{rel}", params={"exp": exp, "sig": sig})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_expired_signature_rejected(client):
    """exp 必须 > now；过期签名失效。"""
    from app.core.signing import make_signature
    rel = "private/secret.txt"
    exp = 1  # 1970，已过期
    sig = make_signature(rel, exp)
    resp = await client.get(f"/api/v1/static/{rel}", params={"exp": exp, "sig": sig})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tampered_signature_rejected(client):
    from app.core.signing import sign_path
    rel = "private/secret.txt"
    exp, sig = sign_path(rel)
    bad = sig[:-2] + "00"
    resp = await client.get(f"/api/v1/static/{rel}", params={"exp": exp, "sig": bad})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_path_traversal_blocked(client):
    """`..` 任何形式都被路由层拒。"""
    resp = await client.get("/api/v1/static/public/../private/secret.txt")
    # FastAPI 会把 `..` URL 段交给路由层处理 — 我们的 split('/')有 '..' 时直接 400
    assert resp.status_code in (400, 403, 404)


@pytest.mark.asyncio
async def test_hidden_file_blocked(client):
    """点开头的文件直接拒。"""
    resp = await client.get("/api/v1/static/.hidden")
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_signature_only_matches_its_own_path(client):
    """签 path A 的签名换到 path B 不能用。"""
    from app.core.signing import sign_path
    exp, sig = sign_path("public/other.txt")
    resp = await client.get(
        "/api/v1/static/private/secret.txt",
        params={"exp": exp, "sig": sig},
    )
    assert resp.status_code == 404
