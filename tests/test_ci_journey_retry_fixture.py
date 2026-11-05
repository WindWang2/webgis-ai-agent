"""J2 fixture and retry integration with real task execution, DB and auth.

The offline regression uses Celery's in-memory broker and a real solo worker
thread, not eager execution or a mocked send_task. Nightly exercises the same
path across a Redis broker and a separate worker process through Playwright.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_retry_fixture_real_worker_recovers_input_and_completes_attempt_two(tmp_path):
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "PYTHON_DOTENV_DISABLED": "1",
        "DATABASE_URL": "sqlite:///./data/webgis.db",
        "DATA_DIR": "./data",
        "E2E_MODE": "real",
        "E2E_USER": "fixture-user",
        "AUTH_DISABLED": "false",
        "USE_REDIS": "false",
        "CELERY_BROKER_URL": "memory://",
        "CELERY_RESULT_BACKEND": "cache+memory://",
        "ENV": "development",
    }
    result = subprocess.run(
        [sys.executable, "-c", r'''
import os
from pathlib import Path
Path('data').mkdir()

from celery.contrib.testing.worker import start_worker
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.routes.jobs import router
from app.core.auth import create_access_token, hash_password
from app.core.database import init_db, SessionLocal
from app.models.db_model import User, Conversation
from app.services.task_queue import celery_app
from scripts.seed_journey_retry import prepare_retry_fixture, verify_retry_output

init_db()
with SessionLocal() as db:
    user = User(id='fixture-owner', username='fixture-user', email='fixture@example.org',
                password_hash=hash_password('fixture-password'), role='editor', is_active=True)
    other = User(id='other-owner', username='other-user', email='other@example.org',
                 password_hash=hash_password('other-password'), role='editor', is_active=True)
    db.add_all([user, other])
    db.flush()
    db.add(Conversation(id='owned-session', user_id=user.id, title='J2 fixture'))
    db.add(Conversation(id='foreign-session', user_id=other.id, title='Other fixture'))
    db.commit()

# Ownership and runner guards must fail before creating files or jobs.
for session, owner in [('foreign-session', 'fixture-owner'), ('owned-session', 'other-owner')]:
    try:
        prepare_retry_fixture(session, owner)
    except RuntimeError as exc:
        assert 'ownership mismatch' in str(exc), exc
    else:
        raise AssertionError('fixture accepted a foreign session/user')
assert not Path('data/e2e-journeys').exists()
os.environ['E2E_MODE'] = 'mock'
try:
    prepare_retry_fixture('owned-session', 'fixture-owner')
except RuntimeError as exc:
    assert 'runner-owned' in str(exc), exc
else:
    raise AssertionError('fixture accepted a non-real stack')
os.environ['E2E_MODE'] = 'real'

# This must be a real queued task: send_task ignores task_always_eager.
celery_app.conf.update(task_always_eager=False)
app = FastAPI()
app.include_router(router, prefix='/api/v1')
headers = {'Authorization': 'Bearer ' + create_access_token({
    'sub': 'fixture-owner', 'username': 'fixture-user', 'role': 'editor',
})}
foreign_headers = {'Authorization': 'Bearer ' + create_access_token({
    'sub': 'other-owner', 'username': 'other-user', 'role': 'editor',
})}
with start_worker(celery_app, pool='solo', concurrency=1, perform_ping_check=False):
    seeded = prepare_retry_fixture('owned-session', 'fixture-owner')
    assert seeded['status'] == 'failed' and seeded['attempt'] == 1, seeded
    job_url = '/api/v1/tasks/jobs/' + seeded['job_id']
    with TestClient(app) as client:
        assert client.get(job_url).status_code == 404
        assert client.get(job_url, headers=foreign_headers).status_code == 404
        assert client.post(job_url + '/retry', headers=foreign_headers).status_code == 404
        before = client.get(job_url, headers=headers)
        assert before.status_code == 200, before.text
        assert before.json()['status'] == 'failed' and before.json()['retryable'], before.text
        # The output assertion cannot pass on the seeded failure.
        try:
            verify_retry_output(seeded['job_id'], 'owned-session', 'fixture-owner')
        except RuntimeError as exc:
            assert 'did not complete' in str(exc), exc
        else:
            raise AssertionError('failed first attempt passed completion validation')
        retried = client.post(job_url + '/retry', headers=headers)
        assert retried.status_code == 200, retried.text
        assert retried.json()['retried'] and retried.json()['attempt'] == 2, retried.text
        import time
        deadline = time.monotonic() + 20
        while True:
            current = client.get(job_url, headers=headers)
            assert current.status_code == 200, current.text
            status = current.json()['status']
            if status == 'completed':
                break
            assert status in ('queued', 'running'), current.text
            assert time.monotonic() < deadline, current.text
            time.sleep(0.1)
        assert current.json()['active'] is False and current.json()['result_ref'], current.text
        output = verify_retry_output(seeded['job_id'], 'owned-session', 'fixture-owner')
        assert output == {'job_id': seeded['job_id'], 'status': 'completed',
                          'attempt': 2, 'valid_pixels': 256, 'ndvi': 0.5}, output
        # Exercise the same CLI JSON contract consumed by Playwright.
        import json, subprocess, sys
        verified = subprocess.run(
            [sys.executable, '-m', 'scripts.seed_journey_retry', 'verify',
             '--session-id', 'owned-session', '--user-id', 'fixture-owner',
             '--job-id', seeded['job_id']],
            check=True, capture_output=True, text=True, timeout=30,
        )
        assert json.loads(verified.stdout.strip().splitlines()[-1]) == output
        # A terminal flag alone must not satisfy the artifact validation.
        import rasterio
        with rasterio.open(current.json()['result_ref'], 'r+') as raster:
            pixels = raster.read(1)
            pixels.fill(0.25)
            raster.write(pixels, 1)
        try:
            verify_retry_output(seeded['job_id'], 'owned-session', 'fixture-owner')
        except RuntimeError as exc:
            assert 'expected NDVI result' in str(exc), exc
        else:
            raise AssertionError('corrupt output passed completion validation')
        print('real worker retry and artifact verification passed')
'''],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real worker retry and artifact verification passed" in result.stdout
