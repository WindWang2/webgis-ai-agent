#!/usr/bin/env python
"""多进程集成 Harness（Quality V3 W12，Epic 10 §J/L）。

真实进程级验证（非进程内 mock）：
1. 以 N 个 worker 启动 uvicorn（app.main:app，tmp SQLite，禁 AUTH bypass
   —— 生产姿态）；
2. traceparent 贯穿：带 W3C 头的请求 → 断言 X-Trace-ID 回显 == 注入值
   （跨进程关联，C-3 的实测证明）；
3. 无头请求 → 生成新 trace 并回显；
4. API 重启 chaos（--chaos）：kill -9 全部 worker → 重启 → /health 恢复
   200（真实进程损失与恢复）；
5. /health、/ready（无鉴权面）与 /api/v1/status/detailed（鉴权面 401）
   的语义边界实测。

资源纪律：有界请求数（--requests，默认 20）、有界超时、tmp DB、
结束必清理进程树。退出码 0=全部通过。

用法：
    python scripts/integration_harness.py --port 8901 --workers 2
    python scripts/integration_harness.py --port 8901 --chaos
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN_ID = "00f067aa0ba902b7"
TRACEPARENT = f"00-{TRACE_ID}-{SPAN_ID}-01"


class Harness:
    def __init__(self, port: int, workers: int, verbose: bool = False):
        self.port = port
        self.workers = workers
        self.verbose = verbose
        self.proc: subprocess.Popen | None = None
        self.tmpdir = tempfile.mkdtemp(prefix="webgis-harness-")

    def start(self) -> None:
        env = dict(os.environ)
        env.update({
            "DATABASE_URL": f"sqlite:///{self.tmpdir}/harness.db",
            "AUTH_DISABLED": "false",       # 生产姿态：401 语义真实生效
            "USE_NEW_AGENT": "false",       # 不起 LLM 子进程（资源纪律）
        })
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(self.port),
             "--workers", str(self.workers)],
            cwd=REPO, env=env,
            stdout=subprocess.DEVNULL if not self.verbose else None,
            stderr=subprocess.DEVNULL if not self.verbose else None,
            start_new_session=True,
        )
        if not self._wait_healthy(timeout=60):
            raise RuntimeError(f"app 在 60s 内未 healthy（port={self.port}）")

    def _wait_healthy(self, timeout: float) -> bool:
        import httpx

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc and self.proc.poll() is not None:
                return False
            try:
                resp = httpx.get(self._url("/api/v1/health"), timeout=2.0)
                if resp.status_code == 200:
                    return True
            except Exception:  # noqa: BLE001 — 启动窗口期连接失败是常态
                time.sleep(0.5)
        return False

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def kill_workers(self) -> None:
        """kill -9 整个进程组（uvicorn worker 是子进程；组杀 = worker loss）。"""
        if self.proc is None:
            return
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self.proc.wait(timeout=15)
        self.proc = None

    def stop(self) -> None:
        if self.proc is None:
            return
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.kill_workers()


def run_checks(h: Harness, requests: int) -> list[str]:
    """核心断言面；返回失败清单（空 = 通过）。"""
    import httpx

    failures: list[str] = []
    with httpx.Client(timeout=10.0) as client:
        # 1. traceparent 贯穿（跨进程：client → 任一 worker → 回显）
        for _ in range(min(requests, 5)):
            resp = client.get(h._url("/api/v1/health"),
                              headers={"traceparent": TRACEPARENT})
            echoed = resp.headers.get("x-trace-id")
            if echoed != TRACE_ID:
                failures.append(
                    f"trace 回显断裂: got {echoed!r} want {TRACE_ID!r}")
        # 2. 无头请求生成新 trace
        resp = client.get(h._url("/api/v1/health"))
        fresh = resp.headers.get("x-trace-id")
        if not fresh or len(fresh) != 32:
            failures.append(f"生成 trace 非法: {fresh!r}")
        if fresh == TRACE_ID:
            failures.append("无头请求不应复用外部 trace")
        # 3. 鉴权边界（生产姿态）：status/detailed 必须拒绝匿名
        resp = client.get(h._url("/api/v1/status/detailed"))
        if resp.status_code != 401:
            failures.append(
                f"匿名访问 status/detailed 应 401，实际 {resp.status_code}")
        # 4. /ready 语义（依赖齐全性不硬断言——本 harness 不起 Redis/PG；
        # 只断言端点存活且 body 极简，无拓扑泄露，SEC-11）
        resp = client.get(h._url("/api/v1/ready"))
        body = resp.json()
        if set(body) != {"ready"}:
            failures.append(f"/ready body 越界: {sorted(body)}")
        # 5. 有界压测面：health 打点 requests 次，全部 200
        for _ in range(requests):
            if client.get(h._url("/api/v1/health")).status_code != 200:
                failures.append("health 非 200")
                break
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8901)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--requests", type=int, default=20,
                        help="有界请求数（默认 20）")
    parser.add_argument("--chaos", action="store_true",
                        help="追加 API 重启 chaos（kill -9 → 重启 → 恢复）")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    failures: list[str] = []
    h = Harness(args.port, args.workers, args.verbose)
    try:
        h.start()
        print(f"[harness] app up（workers={args.workers} port={args.port}）")
        failures += run_checks(h, args.requests)

        if args.chaos:
            print("[harness] chaos: kill -9 全部 worker …")
            h.kill_workers()
            time.sleep(1)
            print("[harness] 重启 …")
            h.start()
            failures += run_checks(h, min(args.requests, 5))
            print("[harness] 重启后恢复验证完成")
    finally:
        h.stop()

    for f in failures:
        print(f"FAIL: {f}")
    verdict = "PASS" if not failures else "FAIL"
    print(f"[harness] {verdict}（{len(failures)} failures）")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
