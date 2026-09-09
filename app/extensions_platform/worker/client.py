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
import tempfile
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
        max_memory_mb: Optional[int] = None,
        max_cpu_seconds: Optional[int] = None,
        broker_handler: Optional[BrokerHandler] = None,
        isolation_backend: str = "process",
        stream_window: int = 16,
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
        self._max_memory_mb = max_memory_mb
        self._max_cpu_seconds = max_cpu_seconds
        self._broker_handler = broker_handler
        # V3（ADR-0119）：isolation backend（process=V2；bubblewrap=netns）。
        self._isolation_backend = isolation_backend
        self._stream_window = max(1, int(stream_window))
        self._proc: Optional[subprocess.Popen] = None
        # Round-2 M-6：**有界**帧队列（64 帧 ≈ 信用窗口的 4 倍余量）。
        # 恶意/被击穿 worker 无视信用洪泛时，reader 线程在满队列上阻塞 →
        # 管道背压 → worker 写入阻塞：宿主内存上界 = 64 × max_frame_bytes，
        # 而非无界 OOM 面。守序 worker 不受影响（消费节奏 >> 生产节奏）。
        self._frames: "queue.Queue[Any]" = queue.Queue(maxsize=64)
        self._reader: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self.in_flight = False
        self.tools: list[dict[str, Any]] = []
        # V3（ADR-0119）：握手申报的 worker 化投影声明。
        self.algorithms: list[dict[str, Any]] = []
        self.data_providers: list[dict[str, Any]] = []
        self.cartography_items: list[dict[str, Any]] = []
        self.workflow_packs: list[dict[str, Any]] = []
        self.resource_warnings: list[str] = []
        self.pid: Optional[int] = None
        self.protocol_version: str = WORKER_PROTOCOL_VERSION
        self.effective_isolation: str = isolation_backend

    # ── 启动 / 握手 ──────────────────────────────────────────────────
    def start(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            self._frames = queue.Queue(maxsize=64)
            server_argv = [
                "-m",
                "app.extensions_platform.worker.server",
                "--pack-dir",
                str(self._pack_dir),
            ]
            # limits 由 worker 子进程入口自施（信任边界内先 limit 后加载；
            # 规避 preexec_fn 在多线程宿主中的 fork 不安全性）。
            if self._max_memory_mb:
                server_argv += ["--max-memory-mb", str(self._max_memory_mb)]
            if self._max_cpu_seconds:
                server_argv += ["--max-cpu-seconds", str(self._max_cpu_seconds)]
            env = self._child_env()
            executable = sys.executable
            # V3：bubblewrap 后端——per-spawn 失败 = typed 激活失败，绝不
            # 静默回退 process（M-9：静默降级 = 隔离承诺失效）。
            if self._isolation_backend == "bubblewrap":
                from .isolation import build_bwrap_command, probe_bubblewrap

                bwrap = probe_bubblewrap()
                if bwrap is None:
                    raise _diagnostic(
                        DiagnosticCode.ISOLATION_UNAVAILABLE.value,
                        "isolation backend 'bubblewrap' requested but bwrap is "
                        "unavailable on this host (typed refusal; no silent "
                        "fallback to process isolation)",
                        self._extension_id,
                    )
                repo_root = Path(__file__).resolve().parents[3]
                argv, env_overrides = build_bwrap_command(
                    python_executable=executable,
                    server_argv=server_argv,
                    repo_app_dir=repo_root / "app",
                    pack_dir=self._pack_dir,
                )
                argv[0] = bwrap
                env.update(env_overrides)
                full_argv = argv
            else:
                full_argv = [executable, *server_argv]
            self.effective_isolation = self._isolation_backend
            self._proc = subprocess.Popen(
                full_argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=tempfile.gettempdir(),
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
                        stream_window=self._stream_window,
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
                        self.algorithms = list(frame.get("algorithms") or [])
                        self.data_providers = list(frame.get("data_providers") or [])
                        self.cartography_items = list(frame.get("cartography_items") or [])
                        self.workflow_packs = list(frame.get("workflow_packs") or [])
                        self.protocol_version = str(frame.get("protocol"))
                        limits = frame.get("resource_limits") or {}
                        self.resource_warnings = [
                            str(w) for w in (limits.get("warnings") or [])
                        ]
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
        # Round-2 审查 C-1：标记位令 worker 侧 Settings 跳过 ``.env`` 解析
        # （app.core.config 据此短路 env_file），cwd 同时移出 repo root
        # ——否则 worker 经 ``from app.core.config import settings`` 一次
        # 读走宿主全部 secrets，击穿「供给即授权」模型。
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONPATH": str(_REPO_ROOT),
            "LANG": "C.UTF-8",
            # HOME 缺失时回退系统临时目录（尊重 TMPDIR），不硬编码 /tmp。
            "HOME": os.environ.get("HOME") or tempfile.gettempdir(),
            "PYTHONHASHSEED": "0",
            "WEBGIS_EXTENSION_WORKER": "1",
        }
        return env

    def _reader_loop(self, stdout: Any) -> None:
        try:
            for line in stdout:
                try:
                    frame = decode_frame(line)
                except ProtocolError:
                    frame = {"type": "__protocol_error__", "message": "undecodable frame"}
                # 阻塞 put：满队列 = 宿主消费不过来（或恶意洪泛）→ reader
                # 停读 → 管道背压反压 worker（结构性内存上界的一部分）。
                self._frames.put(frame)
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
        """崩溃留痕抽取。Round-1 审查 CRITICAL-1 修复：必须先 killpg 并有界
        等待死亡，再以 select+os.read 非阻塞抽取——此前的无界阻塞
        ``stderr.read()`` 会被「关 stdout 但存活并握住 stderr」的 worker
        永久挂起宿主线程（击穿崩溃隔离声明）。"""
        import select
        import time as _time

        proc = self._proc
        stderr = getattr(proc, "stderr", None) if proc is not None else None
        if stderr is None:
            return ""
        try:
            if proc.poll() is None:
                if hasattr(os, "killpg"):
                    os.killpg(os.getpgid(proc.pid), 9)
                else:
                    proc.kill()
                proc.wait(timeout=2)
        except Exception:  # noqa: BLE001 - 进程已死/权限/等待超时都不阻断留痕
            pass
        data = b""
        try:
            fd = stderr.fileno()
            deadline = _time.monotonic() + 0.5
            while _time.monotonic() < deadline and len(data) < 65536:
                ready, _, _ = select.select([fd], [], [], 0.1)
                if not ready:
                    break
                chunk = os.read(fd, 8192)
                if not chunk:
                    break
                data += chunk
        except Exception:  # noqa: BLE001
            pass
        return data.decode("utf-8", "replace")[-limit:].strip()

    # ── 调用面 ───────────────────────────────────────────────────────
    def call(
        self,
        tool: str,
        args: dict[str, Any],
        timeout: Optional[float] = None,
        max_output_bytes: Optional[int] = None,
    ) -> Any:
        value = self._request(
            {"type": "call", "tool": tool, "args": dict(args or {})},
            timeout if timeout is not None else self._call_timeout_s,
            DiagnosticCode.WORKER_CALL_TIMEOUT,
        )
        # M-5：宿主侧结果尺寸强制（恶意/被击穿 worker 的自报上限不可信）。
        if max_output_bytes is not None and value is not None:
            import json as _json

            try:
                size = len(_json.dumps(value, ensure_ascii=False, default=str).encode())
            except (TypeError, ValueError):
                size = -1
            if size > max_output_bytes:
                raise _diagnostic(
                    DiagnosticCode.OUTPUT_LIMIT_EXCEEDED.value,
                    f"worker result serialized to {size} bytes, exceeding "
                    f"max_output_bytes={max_output_bytes}",
                    self._extension_id,
                )
        return value

    def health(self, timeout: Optional[float] = None) -> dict[str, Any]:
        value = self._request(
            {"type": "health"},
            timeout if timeout is not None else min(self._call_timeout_s, 10.0),
            DiagnosticCode.WORKER_CALL_TIMEOUT,
        )
        return value if isinstance(value, dict) else {"status": "unknown", "messages": []}

    # ── V3：流式调用（C-3/M-5/M-6 语义）───────────────────────────────
    def call_stream(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        idle_timeout_s: Optional[float] = None,
        max_events: int = 10000,
        window: int = 16,
        per_frame_max_bytes: Optional[int] = None,
    ) -> "Any":
        """流式调用：yield 事件 payload；返回生成器（消费方驱动）。

        - 逐帧 idle timeout（每帧重置；**不**用整 call deadline，C-3）；
        - idle 超时 / 流控取消 → typed STREAM_FLOW_CONTROL，**不**进入
          worker 崩溃计数（由调用方显式决定处置）；
        - host 收帧处检查每帧序列化尺寸 ≤ per_frame_max_bytes（M-5）；
        - 事件数上界 max_events（结构性防无界流）；
        - 消费方提前 close（生成器 GC/break）→ 发 stream_cancel 并 drain。
        """
        return self._stream_generator(
            tool,
            args,
            idle_timeout_s=self._call_timeout_s if idle_timeout_s is None else idle_timeout_s,
            max_events=max_events,
            window=window,
            per_frame_max_bytes=per_frame_max_bytes,
        )

    def _stream_generator(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        idle_timeout_s: float,
        max_events: int,
        window: int,
        per_frame_max_bytes: Optional[int],
    ) -> Any:
        import json as _json

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
        stream_id = f"s{time.monotonic_ns()}"
        cancelled_sent = False
        ended_normally = False
        consumed = 0
        credits_to_replenish = 0
        try:
            try:
                write_frame(
                    self._proc.stdin,
                    {
                        "type": "call",
                        "id": stream_id,
                        "tool": tool,
                        "args": dict(args or {}),
                        "stream": True,
                    },
                )
            except (ProtocolError, BrokenPipeError, OSError) as exc:
                self.in_flight = False
                raise self._crashed(f"stream call send failed: {exc}") from exc

            def _consume_frame(frame: dict[str, Any]) -> Optional[dict[str, Any]]:
                """处理流间帧；返回带 payload 的 stream_frame 或 None。"""
                nonlocal consumed, credits_to_replenish
                ftype = frame.get("type")
                if ftype == "stream_frame" and frame.get("id") == stream_id:
                    consumed += 1
                    credits_to_replenish += 1
                    # 信用成批补还（减 RTT；结构性上界仍 = window 帧）。
                    if credits_to_replenish >= max(1, window // 2):
                        try:
                            write_frame(
                                self._proc.stdin,
                                {"type": "stream_credit", "id": stream_id,
                                 "n": credits_to_replenish},
                            )
                        except (ProtocolError, BrokenPipeError, OSError) as exc:
                            raise self._crashed(
                                f"credit replenish failed: {exc}"
                            ) from exc
                        credits_to_replenish = 0
                    if per_frame_max_bytes is not None:
                        size = len(
                            _json.dumps(
                                frame.get("payload"), ensure_ascii=False, default=str
                            ).encode()
                        )
                        if size > per_frame_max_bytes:
                            raise _diagnostic(
                                DiagnosticCode.OUTPUT_LIMIT_EXCEEDED.value,
                                f"stream frame {frame.get('seq')} serialized to "
                                f"{size} bytes, exceeding per-frame cap",
                                self._extension_id,
                            )
                    return frame
                if ftype == "stream_end" and frame.get("id") == stream_id:
                    return {"__stream_end__": frame}
                if ftype == "result" and frame.get("id") == stream_id:
                    if frame.get("ok"):
                        # 工具返回了 dict（非迭代器）→ 单帧结果作为一个事件
                        # 产出（流协议对非流式结果的降级形态）。
                        return {
                            "__stream_end__": {"cancelled": False},
                            "__payload__": frame.get("value"),
                        }
                    err = frame.get("error") or {}
                    raise _diagnostic(
                        err.get("code", DiagnosticCode.ENTRY_POINT_FAILED.value),
                        err.get("message", "worker stream failed"),
                        self._extension_id,
                    )
                if ftype == "broker_request":
                    self._dispatch_broker(frame)
                    return None
                return None

            deadline = time.monotonic() + max(idle_timeout_s, 0.01)
            events = 0
            try:
                while True:
                    frame = self._next_stream_frame(deadline)
                    parsed = _consume_frame(frame)
                    if parsed is None:
                        deadline = time.monotonic() + max(idle_timeout_s, 0.01)
                        continue
                    if "__stream_end__" in parsed:
                        ended_normally = True
                        payload = parsed.get("__payload__")
                        if payload is not None:
                            events += 1
                            if events > max_events:
                                raise _diagnostic(
                                    DiagnosticCode.STREAM_LIMIT_EXCEEDED.value,
                                    f"stream exceeded {max_events} events",
                                    self._extension_id,
                                )
                            yield payload
                        return
                    events += 1
                    if events > max_events:
                        raise _diagnostic(
                            DiagnosticCode.STREAM_LIMIT_EXCEEDED.value,
                            f"stream exceeded {max_events} events",
                            self._extension_id,
                        )
                    deadline = time.monotonic() + max(idle_timeout_s, 0.01)
                    yield parsed.get("payload")
            except GeneratorExit:
                # 消费方提前关闭：协作取消 + 有界 drain。
                cancelled_sent = True
                try:
                    write_frame(
                        self._proc.stdin,
                        {"type": "stream_cancel", "id": stream_id},
                    )
                    grace_deadline = time.monotonic() + 2.0
                    while time.monotonic() < grace_deadline:
                        frame = self._next_stream_frame(
                            grace_deadline, timeout_code=DiagnosticCode.STREAM_CANCELLED
                        )
                        parsed = _consume_frame(frame)
                        if parsed is not None and "__stream_end__" in parsed:
                            break
                except (ExtensionPlatformError, ProtocolError, OSError):
                    pass
                raise
        finally:
            # Mi-3：正常结束不发冗余 cancel（worker 侧取消集对已结束流 id
            # 不膨胀）；仅消费方提前关闭/异常路径发 cancel。
            if (
                not cancelled_sent
                and not ended_normally
                and self._proc is not None
                and self._proc.poll() is None
            ):
                try:
                    write_frame(
                        self._proc.stdin,
                        {"type": "stream_cancel", "id": stream_id},
                    )
                except (ProtocolError, BrokenPipeError, OSError):
                    pass
            self.in_flight = False

    def _next_stream_frame(
        self,
        deadline: float,
        timeout_code: DiagnosticCode = DiagnosticCode.STREAM_FLOW_CONTROL,
    ) -> dict[str, Any]:
        """流式读帧：逐帧 idle timeout；**超时不入崩溃计数**（C-3）。

        与 `_next_frame` 的差异：超时抛 typed STREAM_FLOW_CONTROL（或指定
        码）而不是 WORKER_CALL_TIMEOUT——调用方（proxy 层）据此不触发
        `_on_worker_death`。
        """
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _diagnostic(
                    timeout_code.value,
                    "stream idle budget exceeded (worker produced no frame in "
                    "time; NOT counted as a worker crash)",
                    self._extension_id,
                )
            try:
                frame = self._frames.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                if self._proc is not None and self._proc.poll() is not None:
                    raise self._crashed("worker exited mid-stream")
                continue
            if frame.get("type") == "__eof__":
                raise self._crashed("worker closed the RPC channel mid-stream (EOF)")
            if frame.get("type") == "__protocol_error__":
                raise self._crashed(frame.get("message", "protocol error"))
            return frame

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
        try:
            write_frame(self._proc.stdin, response)
        except (ProtocolError, BrokenPipeError, OSError) as exc:
            # worker 在等待 broker 应答期间死亡 → 归一为 typed 崩溃
            # （Round-2 m-4：此前 BrokenPipe 逃逸崩溃计数/回滚路径）。
            raise self._crashed(f"worker died while receiving broker response: {exc}") from exc

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
