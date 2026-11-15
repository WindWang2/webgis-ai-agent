"""G48 Browser Real-Mode Playwright Journey Runner (R37).

Mirrors ADR-0146 / test_g37_browser_journey.py:
Spawns stub LLM + real uvicorn backend + `next dev --webpack` against isolated
sqlite DB & DATA_DIR, then executes `frontend/e2e/journeys/g48-georeferencing.journey.ts`.

Gated on node + playwright/chromium + PI_JOURNEY_E2E=1 + REQUIRE_BROWSER=1.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "frontend"

pytestmark = [
    pytest.mark.heavy,
    pytest.mark.skipif(shutil.which("node") is None, reason="needs node"),
    pytest.mark.skipif(
        os.getenv("PI_JOURNEY_E2E") != "1" or os.getenv("REQUIRE_BROWSER") != "1",
        reason=(
            "browser journey is opt-in: set PI_JOURNEY_E2E=1 REQUIRE_BROWSER=1 "
            "(explicit skip, never silent-green)"
        ),
    ),
]

_STUB_JS = r"""
import http from 'node:http';
const srv = http.createServer((req, res) => {
  let body = ''; req.on('data', (c) => (body += c));
  req.on('end', () => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    const id = 'c' + Date.now();
    res.write(`data: ${JSON.stringify({ id, object: 'chat.completion.chunk', created: 1, model: 'g48-stub', choices: [{ index: 0, delta: { content: '会话已就绪，可进行影像配准。' } }] })}\n\n`);
    res.write(`data: ${JSON.stringify({ id, object: 'chat.completion.chunk', created: 1, model: 'g48-stub', choices: [{ index: 0, delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 40, completion_tokens: 12 } })}\n\n`);
    res.write('data: [DONE]\n\n'); res.end();
  });
});
srv.listen(0, '127.0.0.1', () => process.stderr.write('STUB_READY ' + srv.address().port + '\n'));
"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_http(url: str, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 400:
                    return True
        except urllib.error.HTTPError as exc:
            if exc.code < 400:
                return True
            time.sleep(0.5)
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def test_g48_browser_georeferencing_journey(tmp_path: Path):
    from app.services.chat.pi_compat import pi_vendor_identity

    if not pi_vendor_identity().entry_built:
        pytest.skip("vendored Pi RPC entry not built — run scripts/build-vendor-pi.sh")

    work = tmp_path
    data_dir = work / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    stub_js = work / "stub.mjs"
    stub_js.write_text(_STUB_JS, encoding="utf-8")
    stub_err = work / "stub_err.log"
    stub_err_fh = open(stub_err, "w")
    stub = subprocess.Popen(
        ["node", str(stub_js)], stdout=subprocess.DEVNULL, stderr=stub_err_fh
    )
    stub_port = None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and stub_port is None:
        for line in stub_err.read_text().splitlines():
            if "STUB_READY" in line:
                stub_port = int(line.split()[1])
        time.sleep(0.1)
    assert stub_port, "stub LLM did not become ready"

    db_path = work / "g48_browser.db"
    api_port = _free_port()
    web_port = _free_port()
    admin_user, admin_pass = "g48admin", "g48-browser-pass-1"

    backend_env = {
        **{k: v for k, v in os.environ.items() if k != "REDIS_URL"},
        "DATABASE_URL": f"sqlite:///{db_path}",
        "DATA_DIR": str(data_dir),
        "USE_REDIS": "false",
        "PI_AUTOSTART": "true",
        "LLM_BASE_URL": f"http://127.0.0.1:{stub_port}/v1",
        "LLM_API_KEY": "stub-browser-key",
        "LLM_MODEL": "g48-stub",
        "WEBGIS_API_BASE": f"http://127.0.0.1:{api_port}",
        "API_PORT": str(api_port),
        "CORS_ORIGINS": json.dumps(
            [f"http://127.0.0.1:{web_port}", f"http://localhost:{web_port}"]
        ),
    }

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=backend_env,
        check=True,
        capture_output=True,
        timeout=300,
    )
    subprocess.run(
        [
            sys.executable,
            "manage.py",
            "create-admin",
            admin_user,
            f"{admin_user}@example.com",
            "--password-stdin",
        ],
        cwd=REPO_ROOT,
        env=backend_env,
        check=True,
        input=admin_pass,
        text=True,
        capture_output=True,
        timeout=120,
    )

    backend_log_fh = open(work / "backend.log", "w")
    backend = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env=backend_env,
        stdout=backend_log_fh,
        stderr=subprocess.STDOUT,
    )

    frontend_env = {
        **os.environ,
        "NEXT_TELEMETRY_DISABLED": "1",
        "NEXT_PUBLIC_API_URL": f"http://127.0.0.1:{api_port}",
    }
    frontend_log_fh = open(work / "frontend.log", "w")
    frontend = subprocess.Popen(
        ["./node_modules/.bin/next", "dev", "--webpack", "-p", str(web_port), "-H", "127.0.0.1"],
        cwd=FRONTEND,
        env=frontend_env,
        stdout=frontend_log_fh,
        stderr=subprocess.STDOUT,
    )
    try:
        assert _wait_http(f"http://127.0.0.1:{api_port}/api/v1/health", 90), (
            f"backend did not become ready; log:\n{(work / 'backend.log').read_text()[-1500:]}"
        )
        assert _wait_http(f"http://127.0.0.1:{web_port}/", 180), (
            f"frontend dev server did not become ready; log:\n{(work / 'frontend.log').read_text()[-1500:]}"
        )
        pw_env = {
            **os.environ,
            "E2E_MODE": "real",
            "E2E_API_URL": f"http://127.0.0.1:{api_port}",
            "E2E_BASE_URL": f"http://127.0.0.1:{web_port}",
            "E2E_USER": admin_user,
            "E2E_PASS": admin_pass,
            "PLAYWRIGHT_BASE_URL": f"http://127.0.0.1:{web_port}",
        }
        proc = subprocess.run(
            [
                "./node_modules/.bin/playwright",
                "test",
                "e2e/journeys/g48-georeferencing.journey.ts",
                "--reporter=list",
                "--workers=1",
            ],
            cwd=FRONTEND,
            env=pw_env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        (work / "playwright.log").write_text(
            proc.stdout + "\n---- stderr ----\n" + proc.stderr, encoding="utf-8"
        )
        assert proc.returncode == 0, (
            f"playwright journey failed rc={proc.returncode}; "
            f"full log: {work / 'playwright.log'}\n"
            f"stdout tail:\n{proc.stdout[-3000:]}\nstderr tail:\n{proc.stderr[-2000:]}"
        )
        assert "1 passed" in proc.stdout, proc.stdout[-2000:]
    finally:
        for child in (frontend, backend, stub):
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        for fh in (frontend_log_fh, backend_log_fh, stub_err_fh):
            fh.close()
