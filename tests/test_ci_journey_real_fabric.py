"""Cold real-auth proof of the journey's private-source registration boundary."""
from __future__ import annotations

import subprocess
import sys

from tests.test_ci_journey_real_bootstrap import _cold_checkout, _run


def test_real_journey_private_source_registration_remains_forbidden(tmp_path):
    _, script, env = _cold_checkout(tmp_path)
    provision = _run(script, tmp_path, env)
    assert provision.returncode == 0, provision.stdout + provision.stderr
    check = subprocess.run(
        [sys.executable, "-c", """
import os
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.routes.auth import router as auth
from app.api.routes.data_fabric import router as fabric
from app.core.database import SessionLocal
from app.models.data_fabric import DataSourceModel
from app.services.data_fabric.security import DataFabricSecurity, DataFabricSecurityError
app = FastAPI()
app.include_router(auth, prefix='/api/v1')
app.include_router(fabric, prefix='/api/v1')
with TestClient(app) as client:
    request = {'name': 'blocked private source', 'source_type': 'postgis',
               'endpoint_url': 'postgresql://localhost:5432/gis', 'allow_private': True}
    assert client.post('/api/v1/data-fabric/sources', json=request).status_code == 401
    login = client.post('/api/v1/auth/login', json={
        'identifier': os.environ['E2E_USER'], 'password': os.environ['E2E_PASS'],
    })
    assert login.status_code == 200, login.text
    headers = {'Authorization': 'Bearer ' + login.json()['access_token']}
    try:
        DataFabricSecurity.validate_url(request['endpoint_url'], allow_private=False)
    except DataFabricSecurityError as exc:
        assert 'SSRF Protection' in str(exc), exc
    else:
        raise AssertionError('private fixture URL escaped SSRF validation')
    response = client.post('/api/v1/data-fabric/sources', headers=headers, json=request)
    assert response.status_code == 400, response.text
    assert response.json() == {'detail': '数据源创建失败'}, response.text
    with SessionLocal() as db:
        assert db.query(DataSourceModel).count() == 0
"""], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120,
    )
    assert check.returncode == 0, check.stdout + check.stderr


def test_owned_fabric_fixture_rejects_non_runner_targets_before_writing(tmp_path):
    from pathlib import Path
    import sqlite3

    _, script, env = _cold_checkout(tmp_path)
    provision = _run(script, tmp_path, env)
    assert provision.returncode == 0, provision.stdout + provision.stderr
    fixture = Path(__file__).resolve().parents[1] / "frontend/e2e/tools/seed-fabric-source.py"
    for override, message in [
        ({"E2E_MODE": "mock"}, "only for the real development journey lane"),
        ({"DATABASE_URL": "sqlite:///../outside-runner.db"}, "authenticated runner-owned SQLite"),
        ({"E2E_POSTGIS_DSN": "postgresql://example.org:5432/gis"}, "runner's localhost:5432/gis"),
    ]:
        result = subprocess.run(
            [sys.executable, str(fixture)], cwd=tmp_path, env={**env, **override},
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode != 0
        assert message in result.stderr, result.stdout + result.stderr
        with sqlite3.connect(tmp_path / "data/webgis.db") as db:
            assert db.execute("SELECT count(*) FROM data_sources").fetchone()[0] == 0
    assert not (tmp_path.parent / "outside-runner.db").exists()
