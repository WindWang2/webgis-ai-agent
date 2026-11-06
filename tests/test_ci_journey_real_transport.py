"""Exercise the nightly split-port stack's actual CORS and auth middleware.

The frontend's :3310 origin is deliberately not an application-wide default.
Load the workflow environment in a fresh process so pytest's cached Settings
cannot hide a missing job-owned origin or alter the real middleware policy.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "quality-e2e.yml"

_PREFLIGHT = """
import os
from fastapi.testclient import TestClient
from app.core.config import Settings, settings
from app.main import app
from app.services.task_queue import celery_app

origin = f"http://localhost:{os.environ['E2E_PORT']}"
client = TestClient(app)
transport_headers = (
    'authorization,content-type,x-request-id,x-session-token,'
    'last-event-id,traceparent,tracestate'
)

def preflight(path='/api/v1/chat/stream', method='POST', *,
              request_origin=origin, headers=transport_headers):
    return client.options(path, headers={
        'Origin': request_origin,
        'Access-Control-Request-Method': method,
        'Access-Control-Request-Headers': headers,
    })
"""


def _run_check(tmp_path, assertions, *, without_cors=False):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["journey-real"]
    job_env = {**workflow["env"], **job["env"]}
    env = {
        **os.environ,
        **{key: str(value) for key, value in job_env.items()},
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONPATH": str(REPO_ROOT),
    }
    # Missing workflow configuration must use the real application defaults,
    # not an exported developer/test-shell override.
    for key in ("CORS_ORIGINS", "AUTH_DISABLED"):
        if key not in job_env:
            env.pop(key, None)
    if without_cors:
        env.pop("CORS_ORIGINS", None)
    return subprocess.run(
        [sys.executable, "-c", _PREFLIGHT + assertions],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )


def test_real_journey_origin_and_transport_headers_pass_actual_preflight(tmp_path):
    result = _run_check(tmp_path, """
assert settings.AUTH_DISABLED is False
assert settings.USE_REDIS is True
assert settings.REDIS_URL == 'redis://localhost:6379/0'
assert celery_app.conf.broker_url == settings.REDIS_URL
assert celery_app.conf.result_backend == 'redis://localhost:6379/1'
assert celery_app.conf.task_always_eager is False
# The lane owns its nonstandard port; do not broaden application defaults.
assert Settings.model_fields['CORS_ORIGINS'].default == [
    'http://localhost:3000', 'http://127.0.0.1:3000',
]
for path, method in [
    ('/api/v1/health', 'GET'),
    ('/api/v1/chat/sessions', 'GET'),
    ('/api/v1/projects', 'POST'),
    ('/api/v1/chat/stream', 'POST'),
    ('/api/v1/data-fabric/sources', 'POST'),
]:
    response = preflight(path, method)
    assert response.status_code == 200, (path, response.text)
    assert response.headers['access-control-allow-origin'] == origin
    assert response.headers['access-control-allow-credentials'] == 'true'
assert settings.CORS_ORIGINS == [origin], settings.CORS_ORIGINS

# An exact allowlist must not admit another port, loopback alias or website.
for other_origin in [
    'http://localhost:3311', 'http://127.0.0.1:3310', 'https://untrusted.example',
]:
    response = preflight(request_origin=other_origin)
    assert response.status_code == 400, response.text
    assert response.text == 'Disallowed CORS origin'
    assert 'access-control-allow-origin' not in response.headers

response = preflight(headers='authorization,x-unapproved-header')
assert response.status_code == 400, response.text
assert response.text == 'Disallowed CORS headers'
response = preflight(method='TRACE')
assert response.status_code == 400, response.text
assert response.text == 'Disallowed CORS method'

# Successful browser transport is not authorization to call protected APIs.
response = client.get('/api/v1/auth/me', headers={'Origin': origin})
assert response.status_code == 401, response.text
assert response.headers['access-control-allow-origin'] == origin
""")
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_journey_without_job_origin_reproduces_cors_rejection(tmp_path):
    result = _run_check(tmp_path, """
response = preflight()
assert response.status_code == 400, response.text
assert response.text == 'Disallowed CORS origin'
assert 'access-control-allow-origin' not in response.headers
""", without_cors=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_journey_starts_and_checks_worker_before_browser_journeys():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["journey-real"]
    assert "6379:6379" in job["services"]["redis"]["ports"]
    steps = job["steps"]
    names = [step.get("name", "") for step in steps]
    worker_index = names.index("Start task worker (real Redis execution)")
    assert names.index("Start backend (uvicorn, minimal config)") < worker_index
    assert worker_index < names.index("Run real-mode journeys")
    script = steps[worker_index]["run"]
    assert "app.services.task_queue:celery_app worker" in script
    assert "--pool=solo --concurrency=1" in script
    assert 'kill -0 "$worker_pid"' in script
    assert "inspect ping" in script
    assert '--destination="$worker_name" --timeout=2' in script
    assert script.rstrip().endswith("exit 1")
    syntax = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert syntax.returncode == 0, syntax.stderr
    failure_logs = steps[names.index("Dump service logs on failure")]["run"]
    assert "/tmp/celery-worker.log" in failure_logs
