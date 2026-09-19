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
