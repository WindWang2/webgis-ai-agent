"""G37 浏览器全链车道（real 档 Playwright journey 的编排驱动）。

复刻既有 quality-e2e real 档装配（ADR-0146）：本驱动以子进程承载
stub LLM（node）+ 生产 uvicorn 后端 + `next dev` 前端，再以 Playwright
跑 ``frontend/e2e/journeys/j18-g37-session-model.journey.ts`` ——
浏览器 → 后端 → 真实 Pi（stub LLM 边界）→ dispatch → SSE → UI 的
会话模型选择/生效/用量/预算拒绝全链。

门控：node + 已构建 vendor + Playwright/Chromium 可用 + 双 opt-in
（``PI_JOURNEY_E2E=1`` 与 ``REQUIRE_BROWSER=1``，与仓库浏览器车道纪律
一致 —— 缺席显式 skip，绝不静默绿）。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
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
    res.write(`data: ${JSON.stringify({ id, object: 'chat.completion.chunk', created: 1, model: 'g37-x-r9', choices: [{ index: 0, delta: { content: '好的，已完成。' } }] })}\n\n`);
    res.write(`data: ${JSON.stringify({ id, object: 'chat.completion.chunk', created: 1, model: 'g37-x-r9', choices: [{ index: 0, delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 80, completion_tokens: 20, prompt_tokens_details: { cached_tokens: 4 } } })}\n\n`);
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
                if resp.status < 500:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def test_g37_browser_session_model_journey(tmp_path):
    from app.services.chat.pi_compat import pi_vendor_identity

    if not pi_vendor_identity().entry_built:
        pytest.skip("vendored Pi RPC entry not built — run scripts/build-vendor-pi.sh")

    work = tmp_path
    stub_js = work / "stub.mjs"
    stub_js.write_text(_STUB_JS, encoding="utf-8")
    stub_err = work / "stub_err.log"
    stub = subprocess.Popen(
        ["node", str(stub_js)], stdout=subprocess.DEVNULL,
        stderr=open(stub_err, "w"),
    )
    stub_port = None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and stub_port is None:
        for line in stub_err.read_text().splitlines():
            if "STUB_READY" in line:
                stub_port = int(line.split()[1])
        time.sleep(0.1)
    assert stub_port, "stub LLM did not become ready"

    db_path = work / "browser.db"
    api_port = _free_port()
    web_port = _free_port()
    admin_user, admin_pass = "g37admin", "g37-browser-pass-1"

    backend_env = {
        **{k: v for k, v in os.environ.items() if k != "REDIS_URL"},
        "DATABASE_URL": f"sqlite:///{db_path}",
        "USE_REDIS": "false",
        "PI_AUTOSTART": "true",
        "LLM_BASE_URL": f"http://127.0.0.1:{stub_port}/v1",
        "LLM_API_KEY": "stub-browser-key",
        "LLM_MODEL": "g37-a",
        "SESSION_MODEL_CATALOG_EXTRA": "g37-b",
        "SESSION_MODEL_TOKEN_CAP_MAX": "5000000",
        "WEBGIS_API_BASE": f"http://127.0.0.1:{api_port}",
        "API_PORT": str(api_port),
    }
    # 建库 + 管理员（旅程经真实 /auth/login）
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT, env=backend_env, check=True,
        capture_output=True, timeout=300,
    )
    subprocess.run(
        [sys.executable, "manage.py", "create-admin",
         admin_user, f"{admin_user}@example.com", "--password-stdin"],
        cwd=REPO_ROOT, env=backend_env, check=True,
        input=admin_pass, text=True, capture_output=True, timeout=120,
    )

    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(api_port), "--log-level", "warning"],
        cwd=REPO_ROOT, env=backend_env,
        stdout=open(work / "backend.log", "w"),
        stderr=subprocess.STDOUT,
    )
    frontend_env = {
        **os.environ,
        "NEXT_TELEMETRY_DISABLED": "1",
        "NEXT_PUBLIC_API_URL": f"http://127.0.0.1:{api_port}",
    }
    frontend = subprocess.Popen(
        # --webpack：worktree 的 node_modules 是共享符号链接，Turbopack 的
        # hermetic root 解析会拒绝 root 外文件（实测 ENOENT next/package.json）；
        # 与仓库 build 脚本同款 webpack 路径。
        ["./node_modules/.bin/next", "dev", "--webpack", "-p", str(web_port), "-H", "127.0.0.1"],
        cwd=FRONTEND, env=frontend_env,
        stdout=open(work / "frontend.log", "w"),
        stderr=subprocess.STDOUT,
    )
    try:
        assert _wait_http(f"http://127.0.0.1:{api_port}/api/v1/health", 90), (
            "backend did not become ready"
        )
        assert _wait_http(f"http://127.0.0.1:{web_port}/", 180), (
            "frontend dev server did not become ready"
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
            ["./node_modules/.bin/playwright", "test",
             "e2e/journeys/j18-g37-session-model.journey.ts",
             "--reporter=list", "--workers=1"],
            cwd=FRONTEND, env=pw_env,
            capture_output=True, text=True, timeout=600,
        )
        assert proc.returncode == 0, (
            f"playwright journey failed rc={proc.returncode}\n"
            f"stdout tail:\n{proc.stdout[-3000:]}\nstderr tail:\n{proc.stderr[-2000:]}"
        )
        assert "1 passed" in proc.stdout, proc.stdout[-2000:]
    finally:
        for child in (frontend, backend, stub):
            child.terminate()
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
