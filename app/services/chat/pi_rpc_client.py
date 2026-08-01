"""PiRpcClient - the deep RPC-client core for the Pi agent bridge.

Owns the Pi subprocess lifecycle (spawn, readiness poll, teardown) and the
async JSON-RPC multiplexing over stdin/stdout (request/future registry, pipe
writes via executor, response/event routing). This is the one genuinely deep
module extracted from ``app/agent_pi_bridge.py`` (architecture-review F3).

Interface:
    - :meth:`start` / :meth:`stop` - subprocess lifecycle
    - :meth:`request` - send a JSON-RPC command, await the multiplexed response
    - :attr:`events` - the raw ``AgentSessionEvent`` queue (consumed by the mapper)
    - :attr:`process_died` - True after the Pi process exits (lets the bridge
      fall back to the legacy path)

The ADR-0022 dispatch-result cache and the two dispatch adapters stay in
``agent_pi_bridge.py`` - this module has no knowledge of tool dispatch.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from app.agent_pi_bridge import PiRpcError

logger = logging.getLogger(__name__)

# Pi RPC entry point
PI_RPC_ENTRY = Path(__file__).parent.parent.parent.parent / "vendor" / "pi" / "packages" / "coding-agent" / "dist" / "rpc-entry.js"

# Default session directory
DEFAULT_SESSION_DIR = Path(__file__).parent.parent.parent.parent / ".pi" / "sessions"


# ── Config (CONFIG-04: env-tunable with safe defaults) ──────────────────────

def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "CONFIG-04: ignoring invalid %s=%r, using default %s", name, raw, default,
        )
        return default


PI_RPC_TIMEOUT = _env_float("PI_RPC_TIMEOUT", 300.0)
PI_STARTUP_READY_TIMEOUT = _env_float("PI_STARTUP_READY_TIMEOUT", 10.0)


class PiRpcClient:
    """Owns the Pi subprocess and the async JSON-RPC multiplexing over pipes.

    One instance per Pi bridge. Not thread-safe (asyncio-bound); the caller
    serializes ``request`` calls via a lock if needed.
    """

    def __init__(
        self,
        pi_rpc_entry: Optional[Path] = None,
        session_dir: Optional[Path] = None,
        cwd: Optional[Path] = None,
        extension_paths: Optional[list[str]] = None,
    ):
        self._pi_rpc_entry = pi_rpc_entry or PI_RPC_ENTRY
        self._session_dir = session_dir or DEFAULT_SESSION_DIR
        self._cwd = cwd or Path.cwd()
        self._extension_paths = extension_paths or []
        self._process: Optional[subprocess.Popen] = None
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._request_counter = 0
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        # 审计 AGENT-05：Pi 进程死亡后标记为 True，让 _use_pi_bridge() 能回退
        self._process_died = False

    @property
    def events(self) -> asyncio.Queue:
        """The raw AgentSessionEvent queue (consumed by the SSE mapper)."""
        return self._event_queue

    @property
    def process_died(self) -> bool:
        """True after the Pi process exits or the reader task crashes."""
        return self._process_died

    async def start(self) -> None:
        """Start the Pi subprocess."""
        if self._process is not None:
            return

        self._session_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["PI_SESSION_DIR"] = str(self._session_dir)
        env["PI_OFFLINE"] = "1"
        env["PI_SKIP_VERSION_CHECK"] = "1"
        # 审计 SEC-01：注入共享密钥，Pi 扩展的 HTTP 回调用它调 /pi-tools/execute
        from app.api.routes.pi_tools import get_bridge_secret
        env["WEBGIS_BRIDGE_SECRET"] = get_bridge_secret()
        env["WEBGIS_API_BASE"] = env.get("WEBGIS_API_BASE", "http://127.0.0.1:8000")

        # Build CLI args with --extension flags for each extension path
        args = ["node", str(self._pi_rpc_entry), "--mode", "rpc", "--no-session"]
        for ext_path in self._extension_paths:
            args.extend(["--extension", str(ext_path)])

        self._process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(self._cwd),
            text=True,
        )

        # Start reader tasks for stdout and stderr to avoid OS pipe deadlock
        self._reader_task = asyncio.create_task(self._read_responses())
        self._stderr_task = asyncio.create_task(self._read_stderr())

        # Yield to let the reader task start (avoid race where _send_request writes
        # to stdin before the reader is ready to consume the response).
        await asyncio.sleep(0)

        # Wait for Pi to initialize by polling get_state until it responds
        try:
            await asyncio.wait_for(self._wait_for_ready(), timeout=PI_STARTUP_READY_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(f"[PiRpcClient] Pi did not become ready within {PI_STARTUP_READY_TIMEOUT}s, continuing anyway")

    async def _wait_for_ready(self) -> None:
        """Poll get_state until Pi responds or the reader task ends."""
        while True:
            try:
                await self.request("get_state", {})
                return  # Pi is ready
            except PiRpcError:
                await asyncio.sleep(0.2)

    async def stop(self) -> None:
        """Stop the Pi subprocess."""
        if self._process is None:
            return

        try:
            self._process.terminate()
            await asyncio.sleep(0.5)
            if self._process.poll() is None:
                self._process.kill()
        finally:
            self._process = None

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None

    async def _read_stderr(self) -> None:
        """Read and log stderr lines from Pi subprocess to prevent OS pipe deadlock."""
        try:
            while self._process and self._process.stderr:
                line = await asyncio.get_running_loop().run_in_executor(None, self._process.stderr.readline)
                if not line:
                    break
                line = line.strip()
                if line:
                    logger.debug(f"[PiStderr] {line[:200]}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[PiStderr] Reader exited: {e}")

    async def _read_responses(self) -> None:
        """Read responses and events from Pi stdout.

        审计 AGENT-05：之前 Pi 进程退出时 readline 返回 ""，循环 break 但
        所有 _pending_requests 的 future 永远不会被 resolve -> 调用方挂 300s。
        现在退出时主动 fail 所有 pending 请求。
        """
        try:
            while self._process and self._process.stdout:
                line = await asyncio.get_running_loop().run_in_executor(None, self._process.stdout.readline)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    # Request response: has "type": "response" and "id"
                    if obj.get("type") == "response" and obj.get("id"):
                        await self._handle_response(obj)
                    # AgentSessionEvent: has "type" but no "id"
                    elif "type" in obj and "id" not in obj:
                        await self._event_queue.put(obj)
                except json.JSONDecodeError:
                    logger.warning(f"[PiRpcClient] Invalid JSON: {line[:200]}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[PiRpcClient] Reader task crashed: {e}", exc_info=True)
        finally:
            # Pi 进程退出 / reader 异常 -> fail 所有 pending 请求，避免 300s 挂起
            self._fail_all_pending("Pi process exited or reader stopped")
            # 标记为不可用
            self._process_died = True
            logger.error("[PiRpcClient] Pi subprocess exited; bridge is now unavailable until restart")

    async def _handle_response(self, response: dict) -> None:
        """Handle a request response from Pi."""
        request_id = response.get("id")
        if not request_id:
            return
        future = self._pending_requests.pop(request_id, None)
        if future and not future.done():
            if response.get("success"):
                future.set_result(response.get("data"))
            else:
                future.set_exception(PiRpcError(response.get("error", "Unknown error")))

    def _fail_all_pending(self, reason: str) -> None:
        """审计 AGENT-05：Pi 退出时主动 fail 所有 pending future。"""
        for rid, future in list(self._pending_requests.items()):
            if not future.done():
                future.set_exception(PiRpcError(reason))
        self._pending_requests.clear()

    async def request(self, command: str, data: Optional[dict] = None) -> Any:
        """Send a JSON-RPC request to Pi and wait for the multiplexed response.

        审计 BUG-02：之前 stdin.write + flush 是同步阻塞调用，在 async 路径里
        会卡住事件循环（所有并发协程被冻结）。现在用 run_in_executor 把
        write+flush 放到线程池，不阻塞事件循环。
        """
        if self._process is None or self._process.stdin is None:
            raise PiRpcError("Pi process not started")

        self._request_counter += 1
        request_id = str(self._request_counter)

        # 审计 AGENT-02：Pi 的 handleCommand 读扁平字段 (command.message,
        # command.provider, command.modelId 等)，不拆 command.data。
        # 之前嵌套在 data 里导致 Pi 收到 undefined 字段。
        request = {"id": request_id, "type": command}
        if data is not None:
            request.update(data)

        future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future

        try:
            line = json.dumps(request) + "\n"

            def _write_and_flush():
                self._process.stdin.write(line)
                self._process.stdin.flush()

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _write_and_flush)
            result = await asyncio.wait_for(future, timeout=PI_RPC_TIMEOUT)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise PiRpcError(f"Pi request timeout: {command}")
        except (BrokenPipeError, OSError) as e:
            self._pending_requests.pop(request_id, None)
            raise PiRpcError(f"Pi pipe error: {e}")
