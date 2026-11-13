"""Chaos fixtures (quality-e2e-v9 P7): explicit gating + real process control.

Gating contract (never silent-green):
  - marker ``heavy`` + env ``CHAOS_STACK=1`` + reachable chaos redis, or the
    test is SKIPPED with the reason attached.

Fault injection is real: SIGKILL on the worker subprocess, docker compose
stop/start on redis, client-abort on the SSE stream.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
COMPOSE_FILE = Path(__file__).parent / "docker-compose.chaos.yml"
CHAOS_REDIS_URL = os.getenv("CHAOS_REDIS_URL", "redis://localhost:6399/0")


def _redis_reachable(url: str, timeout: float = 2.0) -> bool:
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(url, socket_connect_timeout=timeout)
        return bool(client.ping())
    except Exception:  # noqa: BLE001 — reachability probe
        return False


def _port_open(port: int, timeout: float = 2.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="session")
def chaos_stack():
    """Session-wide gate: env flag + compose redis reachable."""
    if os.getenv("CHAOS_STACK") != "1":
        pytest.skip("chaos suite is manual/heavy: set CHAOS_STACK=1 (docker "
                    "compose stack up) to run — never silently green")
    if not _redis_reachable(CHAOS_REDIS_URL):
        pytest.skip(f"chaos redis unreachable at {CHAOS_REDIS_URL} — bring the "
                    "compose stack up first (explicit skip, not a pass)")
    yield {"redis_url": CHAOS_REDIS_URL}


class _Process:
    def __init__(self, argv: list[str], env: dict[str, str], log: Path):
        self.log_path = log
        self._log = log.open("wb")
        self.proc = subprocess.Popen(
            argv, cwd=str(REPO), env={**os.environ, **env},
            stdout=self._log, stderr=subprocess.STDOUT)

    def sigkill(self) -> None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            self.proc.wait(timeout=15)

    def terminate(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def close(self) -> None:
        self._log.close()


@pytest.fixture()
def chaos_worker(chaos_stack, tmp_path):
    """Spawn a real celery worker against the chaos redis; SIGKILL-able."""
    started: list[_Process] = []

    def _start() -> _Process:
        proc = _Process(
            [sys.executable, "-m", "celery", "-A", "app.services.task_queue",
             "worker", "--pool=solo", "-Q", "celery", "--concurrency", "1",
             "--loglevel=warning"],
            env={"CELERY_BROKER_URL": CHAOS_REDIS_URL,
                 "CELERY_RESULT_BACKEND": CHAOS_REDIS_URL},
            log=tmp_path / "worker.log")
        started.append(proc)
        return proc

    yield _start
    for proc in started:
        proc.terminate()
        proc.close()


@pytest.fixture()
def chaos_api(chaos_stack, tmp_path):
    """Spawn uvicorn (minimal config + deterministic LLM stub not required for
    health/degradation paths only) on port 8021."""
    import uvicorn  # noqa: F401 — presence check

    base = "http://127.0.0.1:8021"
    proc = _Process(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", "8021", "--log-level", "warning"],
        env={"REDIS_URL": CHAOS_REDIS_URL, "PI_AUTOSTART": "false",
             "JWT_SECRET_KEY": "chaos-secret-key-32-chars-ok",
             "ENV": "development", "LLM_API_KEY": "chaos-not-real"},
        log=tmp_path / "api.log")
    deadline = time.time() + 90
    while time.time() < deadline:
        if _port_open(8021):
            break
        if proc.proc.poll() is not None:
            raise RuntimeError(f"chaos api died at boot: see {proc.log_path}")
        time.sleep(1)
    else:
        proc.terminate()
        raise RuntimeError("chaos api did not open :8021 in 90s")
    yield {"base": base, "proc": proc}
    proc.terminate()
    proc.close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def chaos_pi_api(chaos_stack, tmp_path):
    """Real Pi/backend, with only the external LLM replaced by a wire stub.

    Missing/broken Pi is a fixture failure, never a skip or ChatEngine fallback.
    Keep this separate from the Redis degradation fixture, which needs no LLM.
    """
    import httpx

    stub_port = _free_port()
    processes: list[_Process] = []

    def wait_ready(proc, url, *, pi=False):
        deadline = time.monotonic() + 90
        with httpx.Client(timeout=2, trust_env=False) as client:
            while time.monotonic() < deadline:
                if proc.proc.poll() is not None:
                    break
                try:
                    response = client.get(url)
                    if response.status_code == 200 and (
                        not pi or response.json().get("agent_runtime") == "pi"
                    ):
                        return
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(0.2)
        raise RuntimeError(
            f"chaos process not ready at {url}:\n"
            + proc.log_path.read_text(errors="replace")[-12000:]
        )

    try:
        stub = _Process(
            ["node", "frontend/e2e/tools/llm-stub.mjs"],
            env={"STUB_PORT": str(stub_port), "STUB_FAIL_AT": "0",
                 "STUB_DELAY_FIRST_TOKEN_MS": "10000"},
            log=tmp_path / "llm-stub.log")
        processes.append(stub)
        wait_ready(stub, f"http://127.0.0.1:{stub_port}/healthz")
        api_port = _free_port()
        base = f"http://127.0.0.1:{api_port}"
        data = tmp_path / "data"
        data.mkdir()
        api = _Process(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host",
             "127.0.0.1", "--port", str(api_port), "--log-level", "warning"],
            env={"REDIS_URL": CHAOS_REDIS_URL, "USE_REDIS": "true",
                 "PI_AUTOSTART": "true", "ENV": "development",
                 "JWT_SECRET_KEY": "chaos-secret-key-32-chars-ok",
                 "DATABASE_URL": f"sqlite:///{data / 'webgis.db'}",
                 "DATA_DIR": str(data), "LLM_API_KEY": "chaos-not-real",
                 "LLM_BASE_URL": f"http://127.0.0.1:{stub_port}/v1",
                 "LLM_MODEL": "gpt-4o-mini"},
            log=tmp_path / "pi-api.log")
        processes.append(api)
        wait_ready(api, f"{base}/api/v1/health", pi=True)
        yield {"base": base, "proc": api}
    finally:
        for proc in reversed(processes):
            proc.terminate()
            proc.close()


def docker_compose(action: str) -> subprocess.CompletedProcess:
    """Control the chaos compose services (stop/start redis)."""
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), action, "redis"],
        capture_output=True, text=True, timeout=120)


def wait_redis(url: str, *, want: bool, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _redis_reachable(url) is want:
            return True
        time.sleep(1)
    return False
