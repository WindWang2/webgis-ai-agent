"""#1414 regression: GeoAI HTTP owner binding + session-scoped path roots."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    from app.core.config import settings

    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", str(root))
    return root


@pytest.fixture()
def client(data_dir, monkeypatch):
    """Minimal geoai router with stubbed ownership + empty model list."""
    from app.api.routes import geoai as geoai_mod
    from app.core.auth import create_access_token

    owned = {"sess-mine"}

    async def fake_bind(
        *,
        session_id: Optional[str],
        project_id: Optional[str],
        user: dict,
        db: Any,
        owner_token: Optional[str] = None,
        require: bool = False,
    ):
        from app.services.modelops.service import normalize_scope

        sid = (session_id or "").strip() or None
        pid = (project_id or "").strip() or None
        if sid and pid:
            raise HTTPException(status_code=400, detail="xor")
        if require and not sid and not pid:
            raise HTTPException(status_code=400, detail="scope required")
        if sid:
            if sid not in owned:
                raise HTTPException(status_code=404, detail="Session not found")
            return normalize_scope(session_id=sid)
        if pid:
            if pid != "proj-mine":
                raise HTTPException(status_code=404, detail="Project not found")
            return normalize_scope(project_id=pid)
        return {}

    monkeypatch.setattr(geoai_mod, "_bind_owner_scope", fake_bind)

    class _Svc:
        def list_models(self, **kwargs):
            return [{"model_id": "tiny", "owner_scope": kwargs}]

        @property
        def _settings(self):
            class S:
                registry_dir = data_dir / "modelops"

            (data_dir / "modelops").mkdir(exist_ok=True)
            return S()

    monkeypatch.setattr(
        "app.services.modelops.service.get_modelops_service", lambda *a, **k: _Svc()
    )

    app = FastAPI()
    app.include_router(geoai_mod.router, prefix="/api/v1")

    # Bypass real DB dependency
    async def _noop_db():
        yield None

    from app.core.database import get_async_db

    app.dependency_overrides[get_async_db] = _noop_db

    token = create_access_token({"sub": "user-a", "role": "viewer"})
    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c


def test_models_forged_session_404(client):
    assert client.get(
        "/api/v1/geoai/models", params={"session_id": "sess-victim"}
    ).status_code == 404


def test_models_owned_session_ok(client):
    resp = client.get("/api/v1/geoai/models", params={"session_id": "sess-mine"})
    assert resp.status_code == 200
    assert resp.json()["models"][0]["owner_scope"]["session_id"] == "sess-mine"


def test_preview_requires_scope(client, data_dir):
    raster = data_dir / "x.tif"
    raster.write_bytes(b"not-a-real-tif")
    assert client.get(
        "/api/v1/geoai/preview", params={"source_uri": str(raster)}
    ).status_code == 400


def test_artifact_geojson_rejects_other_session_file(client, data_dir, monkeypatch):
    from app.api.routes import geoai as geoai_mod

    victim = data_dir / "sess-victim" / "secret.json"
    victim.parent.mkdir(parents=True)
    victim.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")

    # Real roots helper (session-scoped) — only sess-mine dir is allowed.
    async def real_roots(db, scope):
        root = Path(data_dir)
        sid = scope.get("session_id")
        return [root / sid] if sid else [root]

    monkeypatch.setattr(geoai_mod, "_roots_for_scope", real_roots)

    resp = client.get(
        "/api/v1/geoai/artifact-geojson",
        params={"path": str(victim), "session_id": "sess-mine"},
    )
    assert resp.status_code == 400


def test_artifact_geojson_allows_own_session_file(client, data_dir, monkeypatch):
    from app.api.routes import geoai as geoai_mod

    own = data_dir / "sess-mine" / "ok.json"
    own.parent.mkdir(parents=True)
    own.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")

    async def real_roots(db, scope):
        root = Path(data_dir)
        sid = scope.get("session_id")
        return [root / sid] if sid else [root]

    monkeypatch.setattr(geoai_mod, "_roots_for_scope", real_roots)

    resp = client.get(
        "/api/v1/geoai/artifact-geojson",
        params={"path": str(own), "session_id": "sess-mine"},
    )
    assert resp.status_code == 200
    assert resp.json()["type"] == "FeatureCollection"


# ── security F-01: project scope must NOT grant all of DATA_DIR ─────────────


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeDB:
    """Returns queued result rows in call order (datasets, then uploads)."""

    def __init__(self, *row_sets):
        self._sets = list(row_sets)

    async def execute(self, _stmt):
        return _FakeResult(self._sets.pop(0) if self._sets else [])


def _stub_registry(monkeypatch, data_dir):
    class _Svc:
        class _settings:  # noqa: N801
            registry_dir = data_dir / "modelops"

    monkeypatch.setattr(
        "app.services.modelops.service.get_modelops_service", lambda *a, **k: _Svc()
    )


@pytest.mark.asyncio
async def test_project_scope_roots_exclude_data_dir(data_dir, monkeypatch):
    from app.api.routes import geoai as geoai_mod

    _stub_registry(monkeypatch, data_dir)
    roots = await geoai_mod._roots_for_scope(
        _FakeDB([("7",)], [(7, "up-7/a.tif")]), {"project_id": "proj-mine"}
    )
    resolved = [r.resolve() for r in roots]
    assert data_dir.resolve() not in resolved
    assert data_dir.resolve() / "projects" / "proj-mine" in resolved
    assert data_dir.resolve() / "uploads" / "up-7" in resolved

    victim = data_dir / "sess-victim" / "secret.json"
    victim.parent.mkdir(parents=True)
    victim.write_text("{}", encoding="utf-8")
    with pytest.raises(HTTPException) as ei:
        geoai_mod._gate_source_uri(str(victim), allowed_roots=roots)
    assert ei.value.status_code == 400

    other_upload = data_dir / "uploads" / "up-9" / "b.tif"
    other_upload.parent.mkdir(parents=True)
    other_upload.write_bytes(b"x")
    with pytest.raises(HTTPException):
        geoai_mod._gate_source_uri(str(other_upload), allowed_roots=roots)

    own = data_dir / "uploads" / "up-7" / "a.tif"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"x")
    assert geoai_mod._gate_source_uri(str(own), allowed_roots=roots) == str(
        own.resolve()
    )


@pytest.mark.asyncio
async def test_empty_scope_roots_exclude_data_dir(data_dir, monkeypatch):
    from app.api.routes import geoai as geoai_mod

    _stub_registry(monkeypatch, data_dir)
    roots = await geoai_mod._roots_for_scope(_FakeDB(), {})
    assert data_dir.resolve() not in [r.resolve() for r in roots]


def test_gate_returns_resolved_path(data_dir, tmp_path):
    from app.api.routes import geoai as geoai_mod

    real = data_dir / "sess-mine" / "x.json"
    real.parent.mkdir(parents=True)
    real.write_text("{}", encoding="utf-8")
    link = data_dir / "sess-mine" / "link.json"
    link.symlink_to(real)
    out = geoai_mod._gate_source_uri(str(link), allowed_roots=[data_dir / "sess-mine"])
    assert out == str(real.resolve())
