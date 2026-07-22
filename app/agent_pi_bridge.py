"""Python bridge to Pi (earendil-works/pi) RPC mode.

Spawns Pi as a subprocess and communicates via JSON-RPC over stdin/stdout.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

logger = logging.getLogger(__name__)

# Pi RPC entry point
PI_RPC_ENTRY = Path(__file__).parent.parent.parent / "vendor" / "pi" / "packages" / "coding-agent" / "dist" / "rpc-entry.js"

# Default session directory (use project-local .pi/sessions)
DEFAULT_SESSION_DIR = Path(__file__).parent.parent.parent / ".pi" / "sessions"


class PiRpcError(Exception):
    """Error from Pi RPC."""
    pass


class PiBridge:
    """Bridge to Pi agent via RPC mode.

    Spawns Pi as a subprocess and communicates via JSON-RPC protocol.
    """

    def __init__(
        self,
        pi_rpc_entry: Optional[Path] = None,
        session_dir: Optional[Path] = None,
        cwd: Optional[Path] = None,
    ):
        self._pi_rpc_entry = pi_rpc_entry or PI_RPC_ENTRY
        self._session_dir = session_dir or DEFAULT_SESSION_DIR
        self._cwd = cwd or Path.cwd()
        self._process: Optional[subprocess.Popen] = None
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._request_counter = 0
        self._reader_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the Pi subprocess."""
        if self._process is not None:
            return

        self._session_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["PI_SESSION_DIR"] = str(self._session_dir)
        env["PI_OFFLINE"] = "1"  # Disable version checks for now
        env["PI_SKIP_VERSION_CHECK"] = "1"

        self._process = subprocess.Popen(
            ["node", str(self._pi_rpc_entry), "--mode", "rpc", "--no-session"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(self._cwd),
            text=True,
            bufsize=0,  # Unbuffered
        )

        # Start reader task
        self._reader_task = asyncio.create_task(self._read_responses())

        # Wait for ready signal
        await asyncio.sleep(1)

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

    async def _read_responses(self) -> None:
        """Read responses from Pi stdout."""
        while self._process and self._process.stdout:
            line = await asyncio.get_event_loop().run_in_executor(None, self._process.stdout.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                response = json.loads(line)
                await self._handle_response(response)
            except json.JSONDecodeError:
                logger.warning(f"[PiBridge] Invalid JSON: {line[:200]}")

    async def _handle_response(self, response: dict) -> None:
        """Handle a response from Pi."""
        response_type = response.get("type")
        request_id = response.get("id")

        if response_type == "response" and request_id:
            future = self._pending_requests.pop(request_id, None)
            if future and not future.done():
                if response.get("success"):
                    future.set_result(response.get("data"))
                else:
                    future.set_exception(PiRpcError(response.get("error", "Unknown error")))

    async def _send_request(self, command: str, data: Optional[dict] = None) -> Any:
        """Send a request to Pi and wait for response."""
        if self._process is None or self._process.stdin is None:
            raise PiRpcError("Pi process not started")

        self._request_counter += 1
        request_id = str(self._request_counter)

        request = {"id": request_id, "type": command}
        if data is not None:
            request["data"] = data

        future = asyncio.get_event_loop().create_future()
        self._pending_requests[request_id] = future

        try:
            line = json.dumps(request) + "\n"
            self._process.stdin.write(line)
            self._process.stdin.flush()
            result = await asyncio.wait_for(future, timeout=300.0)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise PiRpcError(f"Pi request timeout: {command}")

    async def prompt(self, message: str, session_id: Optional[str] = None) -> dict:
        """Send a prompt to Pi agent.

        Args:
            message: User message
            session_id: Optional session ID for context

        Returns:
            Response dict with session_id and content
        """
        data = {"message": message}
        if session_id:
            data["sessionId"] = session_id

        async with self._lock:
            result = await self._send_request("prompt", data)
            return result or {}

    async def get_state(self) -> dict:
        """Get current Pi agent state."""
        async with self._lock:
            result = await self._send_request("get_state")
            return result or {}

    async def get_available_models(self) -> list[dict]:
        """Get available models."""
        async with self._lock:
            result = await self._send_request("get_available_models")
            return result.get("models", []) if result else []

    async def set_model(self, provider: str, model_id: str) -> dict:
        """Set the model."""
        async with self._lock:
            result = await self._send_request("set_model", {"provider": provider, "modelId": model_id})
            return result or {}

    async def get_messages(self) -> list[dict]:
        """Get conversation messages."""
        async with self._lock:
            result = await self._send_request("get_messages")
            return result.get("messages", []) if result else []


# Global bridge instance
_pi_bridge: Optional[PiBridge] = None


async def get_pi_bridge() -> PiBridge:
    """Get or create the global Pi bridge instance."""
    global _pi_bridge
    if _pi_bridge is None:
        _pi_bridge = PiBridge()
        await _pi_bridge.start()
    return _pi_bridge


async def shutdown_pi_bridge() -> None:
    """Shutdown the global Pi bridge instance."""
    global _pi_bridge
    if _pi_bridge is not None:
        await _pi_bridge.stop()
        _pi_bridge = None
