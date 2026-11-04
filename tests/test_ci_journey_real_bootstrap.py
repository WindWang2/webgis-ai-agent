"""Run the nightly lane's actual provisioning script from a cold checkout.

The CLI runs before app.main, so app.main's DATA_DIR setup cannot bootstrap
the SQLite file. Keep the runner-owned database explicit and verify that the
real auth routes read the same persisted admin with authentication enabled.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "quality-e2e.yml"


def _cold_checkout(tmp_path):
    job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["journey-real"]
    script = next(
        step["run"] for step in job["steps"]
        if step.get("name") == "Provision admin account"
    )
    shutil.copy2(REPO_ROOT / "manage.py", tmp_path / "manage.py")
    env = {
        **os.environ,
        **{key: str(value) for key, value in job["env"].items()},
        "AUTH_DISABLED": "false",
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONPATH": str(REPO_ROOT),
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    # Do not let conftest's isolated DB hide a missing workflow DB setting.
    if "DATABASE_URL" not in job["env"]:
        env.pop("DATABASE_URL", None)
    assert not (tmp_path / "data").exists()
    return job, script, env


def _run(script, tmp_path, env):
    return subprocess.run(
        ["bash", "-e", "-c", script], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=120,
    )


def test_real_journey_cold_provision_persists_and_authenticates_admin(tmp_path):
    job, script, env = _cold_checkout(tmp_path)
    provision = _run(script, tmp_path, env)
    assert provision.returncode == 0, provision.stdout + provision.stderr
    assert job["env"]["DATABASE_URL"] == "sqlite:///./data/webgis.db"
    assert job["env"]["DATA_DIR"] == "./data"

    with sqlite3.connect(tmp_path / "data" / "webgis.db") as db:
        user = db.execute(
            "SELECT id, username, email, role, is_active, password_hash FROM users"
        ).fetchone()
    assert user is not None
    assert user[1:5] == (env["E2E_USER"], "e2e@example.org", "admin", 1)
    assert user[5] and user[5] != env["E2E_PASS"]

    # A fresh process exercises the actual async auth/DB dependencies, JWT
    # issuance and /me verification. No mocked DB, auth bypass or init_db call
    # may manufacture the user after the workflow's provisioning completes.
    auth_check = subprocess.run(
        [sys.executable, "-c", """
import os
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.routes.auth import router
from app.core.config import settings

assert settings.AUTH_DISABLED is False
app = FastAPI()
app.include_router(router, prefix='/api/v1')
with TestClient(app) as client:
    assert client.get('/api/v1/auth/me').status_code == 401
    rejected = client.post('/api/v1/auth/login', json={
        'identifier': os.environ['E2E_USER'], 'password': 'wrong-password',
    })
    assert rejected.status_code == 401, rejected.text
    login = client.post('/api/v1/auth/login', json={
        'identifier': os.environ['E2E_USER'], 'password': os.environ['E2E_PASS'],
    })
    assert login.status_code == 200, login.text
    token = login.json()['access_token']
    assert token
    me = client.get('/api/v1/auth/me', headers={'Authorization': f'Bearer {token}'})
    assert me.status_code == 200, me.text
    assert me.json()['user_id'] == login.json()['user']['id']
    assert me.json()['username'] == os.environ['E2E_USER']
    assert me.json()['role'] == 'admin'
"""],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert auth_check.returncode == 0, auth_check.stdout + auth_check.stderr


def test_real_journey_without_directory_bootstrap_reproduces_failure(tmp_path):
    _, script, env = _cold_checkout(tmp_path)
    without_bootstrap = "\n".join(
        line for line in script.splitlines() if "mkdir" not in line
    )
    assert without_bootstrap != script
    result = _run(without_bootstrap, tmp_path, env)
    assert result.returncode != 0
    assert "unable to open database file" in result.stdout + result.stderr
    assert not (tmp_path / "data").exists()


def test_real_journey_directory_setup_failure_is_fatal(tmp_path):
    _, script, env = _cold_checkout(tmp_path)
    (tmp_path / "data").write_text("not a directory", encoding="utf-8")
    result = _run(script, tmp_path, env)
    assert result.returncode != 0
    assert "Admin 创建成功" not in result.stdout
    assert (tmp_path / "data").read_text(encoding="utf-8") == "not a directory"
