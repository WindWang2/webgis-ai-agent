"""WorkerProcess：宿主侧的隔离子进程句柄（ADR-0105 V2 / Wave 3）。

职责与不变式：
- spawn：最小环境（无 ambient authority——不继承宿主的环境变量面，仅
  PATH/PYTHONPATH/LANG/HOME/PYTHONHASHSEED）、独立进程组（POSIX，可整组
  kill）、stdin/stdout 管道走 RPC 协议，stderr 捕获（崩溃诊断用）；
- 握手：startup_timeout_s 预算；超时/失败 → killpg + typed diagnostic；
- 调用：call_timeout_s 预算；串行（一把锁——V2 契约：一个 worker 一个
  in-flight 调用）；broker_request 就地分派给宿主 broker；
- 崩溃检测：EOF / 进程退出 → typed WORKER_CRASHED（附 stderr 尾部留痕）；
- 退出：shutdown 优雅（等 bye + exit 0）→ 超时 killpg。

线程模型：读帧在独立线程（队列），send/await 由 ``_lock`` 串行化；
``in_flight`` 标志供宿主 deactivate 前检查（OPERATION_IN_FLIGHT）。
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ..diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError
from .protocol import (
    WORKER_PROTOCOL_VERSION,
    ProtocolError,
    decode_frame,
    error_payload,
    make_handshake,
    write_frame,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BROKER_ENV_DENY = object()

BrokerHandler = Callable[[str, dict[str, Any]], tuple[bool, Any]]


def _diagnostic(code: str, message: str, extension_id: Optional[str]) -> ExtensionPlatformError:
    member = DiagnosticCode(code) if code in DiagnosticCode._value2member_map_ else None
    return ExtensionPlatformError(
        ExtensionDiagnostic.error(
            member if member is not None else DiagnosticCode.WORKER_CRASHED,
            f"[{code}] {message}",
            extension_id=extension_id,
        )
    )


class WorkerProcess:
    """一个 worker 扩展的进程句柄（宿主独占；不可跨进程共享）。"""

    def __init__(
        self,
        pack_dir: Path,
        extension_id: str,
        namespace: str,
        name: str,
        fingerprint: str,
        grants: list[str],
        settings: dict[str, Any],
        startup_timeout_s: float,
        call_timeout_s: float,
        broker_handler: Optional[BrokerHandler] = None,
    ) -> None:
        self._pack_dir = Path(pack_dir)
        self._extension_id = extension_id
        self._namespace = namespace
        self._name = name
        self._fingerprint = fingerprint
        self._grants = list(grants)
        self._settings = dict(settings)
        self._startup_timeout_s = float(startup_timeout_s)
        self._call_timeout_s = float(call_timeout_s)
        self._broker_handler = broker_handler
        self._proc: Optional[subprocess.Popen] = None
        self._frames: "queue.Queue[Any]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self.in_flight = False
        self.tools: list[dict[str, Any]] = []
        self.pid: Optional[int] = None

    # ── 启动 / 握手 ──────────────────────────────────────────────────
    def start(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            self._frames = queue.Queue()
            self._proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "app.extensions_platform.worker.server",
                    "--pack-dir",
                    str(self._pack_dir),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._child_env(),
                cwd=str(_REPO_ROOT),
                start_new_session=hasattr(os, "setsid"),
            )
            self.pid = self._proc.pid
            self._reader = threading.Thread(
                target=self._reader_loop, args=(self._proc.stdout,), daemon=True
            )
            self._reader.start()
            deadline = time.monotonic() + self._startup_timeout_s
            try:
                write_frame(
                    self._proc.stdin,
                    make_handshake(
                        extension_id=self._extension_id,
                        fingerprint=self._fingerprint,
                        grants=self._grants,
                        settings=self._settings,
                        expected_namespace=self._namespace,
                        expected_name=self._name,
                    ),
                )
                while True:
                    frame = self._next_frame(deadline, DiagnosticCode.WORKER_STARTUP_TIMEOUT)
                    ftype = frame.get("type")
                    if ftype == "handshake_ok":
                        if frame.get("protocol") != WORKER_PROTOCOL_VERSION:
                            raise _diagnostic(
                                DiagnosticCode.WORKER_PROTOCOL_MISMATCH.value,
                                "worker replied with unknown protocol version",
                                self._extension_id,
                            )
                        self.tools = list(frame.get("tools") or [])
                        return
                    if ftype == "handshake_failed":
                        err = frame.get("error") or {}
                        raise _diagnostic(
                            err.get("code", DiagnosticCode.ENTRY_POINT_FAILED.value),
                            f"worker handshake failed: {err.get('message', 'unknown')}",
                            self._extension_id,
                        )
                    # 握手期的其它帧一律违规。
                    raise _diagnostic(
                        DiagnosticCode.WORKER_PROTOCOL_MISMATCH.value,
                        f"unexpected frame during handshake: {ftype!r}",
                        self._extension_id,
                    )
            except Exception:
                self.kill()
                raise

    def _child_env(self) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONPATH": str(_REPO_ROOT),
            "LANG": "C.UTF-8",
            "HOME": os.environ.get("HOME", "/tmp"),
            "PYTHONHASHSEED": "0",
        }
        return env

    def _reader_loop(self, stdout: Any) -> None:
        try:
            for line in stdout:
                try:
                    self._frames.put(decode_frame(line))
                except ProtocolError:
                    self._frames.put(
                        {"type": "__protocol_error__", "message": "undecodable frame"}
                    )
        finally:
            self._frames.put({"type": "__eof__"})

    def _next_frame(self, deadline: float, timeout_code: DiagnosticCode) -> dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _diagnostic(
                    timeout_code.value,
                    f"worker exceeded {timeout_code.value} budget",
                    self._extension_id,
                )
            try:
                frame = self._frames.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                if self._proc is not None and self._proc.poll() is not None:
                    raise self._crashed("worker exited before responding")
                continue
            if frame.get("type") == "__eof__":
                raise self._crashed("worker closed the RPC channel (EOF)")
            if frame.get("type") == "__protocol_error__":
                raise self._crashed(frame.get("message", "protocol error"))
            return frame

    def _crashed(self, detail: str) -> ExtensionPlatformError:
        return _diagnostic(
            DiagnosticCode.WORKER_CRASHED.value,
            f"{detail}; stderr tail: {self._stderr_tail()!r}",
            self._extension_id,
        )

    def _stderr_tail(self, limit: int = 400) -> str:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return ""
        try:
            proc.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            data = proc.stderr.read() or b""
        except Exception:  # noqa: BLE001
            data = b""
        return data.decode("utf-8", "replace")[-limit:].strip()

    # ── 调用面 ───────────────────────────────────────────────────────
    def call(self, tool: str, args: dict[str, Any], timeout: Optional[float] = None) -> Any:
        return self._request(
            {"type": "call", "tool": tool, "args": dict(args or {})},
            timeout if timeout is not None else self._call_timeout_s,
            DiagnosticCode.WORKER_CALL_TIMEOUT,
        )

    def health(self, timeout: Optional[float] = None) -> dict[str, Any]:
        value = self._request(
            {"type": "health"},
            timeout if timeout is not None else min(self._call_timeout_s, 10.0),
            DiagnosticCode.WORKER_CALL_TIMEOUT,
        )
        return value if isinstance(value, dict) else {"status": "unknown", "messages": []}

    def _request(self, body: dict[str, Any], timeout: float, timeout_code: DiagnosticCode) -> Any:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                raise self._crashed("worker is not running")
            if self.in_flight:
                raise _diagnostic(
                    DiagnosticCode.OPERATION_IN_FLIGHT.value,
                    "another call is in flight (worker calls are serial)",
                    self._extension_id,
                )
            self.in_flight = True
            call_id = f"c{time.monotonic_ns()}"
            body = {"id": call_id, **body}
            deadline = time.monotonic() + max(timeout, 0.01)
            try:
                assert self._proc is not None and self._proc.stdin is not None
                write_frame(self._proc.stdin, body)
                while True:
                    frame = self._next_frame(deadline, timeout_code)
                    ftype = frame.get("type")
                    if ftype == "result" and frame.get("id") == call_id:
                        if frame.get("ok"):
                            return frame.get("value")
                        err = frame.get("error") or {}
                        raise _diagnostic(
                            err.get("code", DiagnosticCode.ENTRY_POINT_FAILED.value),
                            err.get("message", "worker call failed"),
                            self._extension_id,
                        )
                    if ftype == "broker_request":
                        self._dispatch_broker(frame)
                        continue
                    if ftype == "error":
                        err = frame.get("error") or {}
                        raise _diagnostic(
                            err.get("code", DiagnosticCode.WORKER_PROTOCOL_MISMATCH.value),
                            err.get("message", "worker protocol error"),
                            self._extension_id,
                        )
                    # 无关帧（陈旧 result 等）：丢弃并继续。
            finally:
                self.in_flight = False

    def _dispatch_broker(self, frame: dict[str, Any]) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        handler = self._broker_handler
        if handler is None:
            ok, value = False, error_payload(
                DiagnosticCode.BROKER_DENIED.value, "broker not configured for this worker"
            )
        else:
            try:
                ok, value = handler(str(frame.get("op")), dict(frame.get("payload") or {}))
            except ExtensionPlatformError as exc:
                ok = False
                value = error_payload(exc.diagnostic.code.value, exc.diagnostic.message)
            except Exception as exc:  # noqa: BLE001 - broker 内部故障不击穿 worker
                ok = False
                value = error_payload(
                    DiagnosticCode.BROKER_DENIED.value,
                    f"broker handler failed: {type(exc).__name__}",
                )
        response: dict[str, Any] = {"type": "broker_response", "id": frame.get("id"), "ok": ok}
        if ok:
            response["value"] = value
        else:
            response["error"] = value if isinstance(value, dict) else error_payload(
                DiagnosticCode.BROKER_DENIED.value, str(value)
            )
        write_frame(self._proc.stdin, response)

    # ── 退出 ─────────────────────────────────────────────────────────
    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def shutdown(self, grace_s: float = 3.0) -> None:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                self._release()
                return
            try:
                if proc.stdin is not None:
                    write_frame(proc.stdin, {"type": "shutdown"})
            except (ProtocolError, BrokenPipeError, OSError):
                self.kill()
                return
            deadline = time.monotonic() + grace_s
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    self._release()
                    return
                time.sleep(0.02)
            self.kill()

    def kill(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                if hasattr(os, "killpg"):
                    os.killpg(os.getpgid(proc.pid), 9)
                else:
                    proc.kill()
            except (ProcessLookupError, PermissionError, OSError):
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        self._release()

    def _release(self) -> None:
        proc = self._proc
        if proc is not None:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:  # noqa: BLE001
                    pass
        self._proc = None
        self.pid = None
        self.in_flight = False
