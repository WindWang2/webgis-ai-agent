"""Raster serving route (ADR-0011) — session-scoped PNG delivery for
`type:"raster"` MapSpec sources.

Model: a tmp session dir + a PNG, monkeypatch BASE_STORAGE_DIR, hit the route.
Covers the happy path + path-traversal rejection (the route's security seam).
"""
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

  # Route reads BASE_STORAGE_DIR as a module-level import from mapspec_store;
  # patch the attribute on the route module so it resolves under tmp_path.
  from app.api.routes import raster as raster_routes
  monkeypatch.setattr(raster_routes, "BASE_STORAGE_DIR", storage)

  app = FastAPI()
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
