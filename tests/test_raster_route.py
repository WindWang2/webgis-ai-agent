"""Raster serving route (ADR-0011) — session-scoped PNG delivery for
`type:"raster"` MapSpec sources.

Model: a tmp session dir + a PNG, monkeypatch BASE_STORAGE_DIR, hit the route.
Covers the happy path + path-traversal rejection (the route's security seam)
+ the #408 ownership guard (verify_session_owner semantics: token-less
anonymous sessions still pass; owned sessions require the token)."""
import os
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET_KEY", "test-raster-secret-32chars-min-okk")
os.environ.setdefault("ENV", "development")

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest_asyncio.fixture
async def app_and_dir(tmp_path, monkeypatch):
  storage = tmp_path / "webgis-agent"
  sid = "raster-test-session"
  rdir = storage / sid / "raster"
  rdir.mkdir(parents=True)
  (rdir / "ndvi_src.png").write_bytes(_PNG_MAGIC + b"\x00" * 16)
  (rdir / "second.png").write_bytes(_PNG_MAGIC + b"\x01" * 8)
  owned_dir = storage / "owned-session" / "raster"
  owned_dir.mkdir(parents=True)
  (owned_dir / "ndvi_src.png").write_bytes(_PNG_MAGIC + b"\x02" * 8)

  # Route reads BASE_STORAGE_DIR as a module-level import from mapspec_store;
  # patch the attribute on the route module so it resolves under tmp_path.
  from app.api.routes import raster as raster_routes
  monkeypatch.setattr(raster_routes, "BASE_STORAGE_DIR", storage)

  # #408: the route now enforces session ownership like every sibling
  # data-plane route. Token-less sessions pass (anonymous capability
  # semantics); owned sessions must present the token (header or query).
  from app.api.routes import raster as _rr
  _SESSIONS = {
      sid: None,           # token-less anonymous session → session_id grants access
      "owned-session": "tok-123",
  }

  class _FakeConv:
      pass

  async def _fake_verify(db, session_id, user_id=None, owner_token=None):
      tok = _SESSIONS.get(session_id, "?missing?")
      if tok is None:
          return _FakeConv()
      if owner_token and owner_token == tok:
          return _FakeConv()
      from fastapi import HTTPException
      raise HTTPException(status_code=404, detail="Session not found")

  async def _fake_user():
      return {"user_id": "anonymous", "role": "anonymous"}

  async def _fake_db():
      yield None

  app = FastAPI(dependencies=[])
  app.dependency_overrides[_rr.get_current_user_optional] = _fake_user
  app.dependency_overrides[_rr.get_async_db] = _fake_db
  monkeypatch.setattr(_rr, "verify_session_owner", _fake_verify)
  app.include_router(raster_routes.router, prefix="/api/v1")
  yield app, sid


@pytest_asyncio.fixture
async def client(app_and_dir):
  app, _ = app_and_dir
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as c:
    yield c


@pytest.mark.asyncio
async def test_get_raster_png_happy_path(client):
  """An existing raster PNG is served with image/png and the right bytes."""
  res = await client.get("/api/v1/sessions/raster-test-session/raster/ndvi_src.png")
  assert res.status_code == 200
  assert res.headers["content-type"] == "image/png"
  assert res.content.startswith(_PNG_MAGIC)


@pytest.mark.asyncio
async def test_get_raster_png_404_when_missing(client):
  res = await client.get("/api/v1/sessions/raster-test-session/raster/nope.png")
  assert res.status_code == 404


@pytest.mark.asyncio
async def test_get_raster_png_rejects_traversal_in_raster_id(client):
  """raster_id is validated to be a plain identifier — `..`/`/` are rejected
  before any disk access (the route's security seam)."""
  # `..` would otherwise let an attacker read `<sid>/raster/../../<anything>`.
  res = await client.get("/api/v1/sessions/raster-test-session/raster/..png")
  assert res.status_code == 400
  # A dotfile-style id (leading dot) is also rejected by the identifier regex.
  res3 = await client.get("/api/v1/sessions/raster-test-session/raster/.env.png")
  assert res3.status_code == 400


@pytest.mark.asyncio
async def test_get_raster_png_rejects_dotted_session_id(client):
  """session_id is path-interpolated and must reject a `.` in its charset —
  a dotted id (e.g. `foo.bar`) is the form that survives HTTP-layer path
  normalization and reaches the handler, where the strict regex blocks it.
  (A literal `..` in the path is normalized away by the HTTP router before
  reaching the handler, so we test the form that actually exposes the regex.)"""
  res = await client.get("/api/v1/sessions/foo.bar/raster/ndvi_src.png")
  assert res.status_code == 400


@pytest.mark.asyncio
async def test_get_raster_png_second_file_served(client):
  """Sanity: a different raster_id in the same session resolves too."""
  res = await client.get("/api/v1/sessions/raster-test-session/raster/second.png")
  assert res.status_code == 200


@pytest.mark.asyncio
async def test_get_raster_png_ownership_guard(client):
  """#408: a token-bearing session 404s without the token and serves with it
  (header form and the MapLibre-compatible query form both work)."""
  # Session not in the table at all → 404 regardless of the file existing.
  res = await client.get("/api/v1/sessions/unknown-session/raster/ndvi_src.png")
  assert res.status_code == 404

  # Owned session without token → 404 (ownership guard precedes file lookup).
  res = await client.get("/api/v1/sessions/owned-session/raster/ndvi_src.png")
  assert res.status_code == 404

  # Header form works.
  res = await client.get(
      "/api/v1/sessions/owned-session/raster/ndvi_src.png",
      headers={"X-Session-Token": "tok-123"},
  )
  assert res.status_code == 200

  # Query form (MapLibre image fetches cannot attach headers).
  res = await client.get("/api/v1/sessions/owned-session/raster/ndvi_src.png?token=tok-123")
  assert res.status_code == 200

  # Wrong token → 404.
  res = await client.get("/api/v1/sessions/owned-session/raster/ndvi_src.png?token=bad")
  assert res.status_code == 404
