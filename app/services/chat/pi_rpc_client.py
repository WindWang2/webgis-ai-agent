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
    - :attr:`process_died_event` - public ``asyncio.Event`` set at the same
      moment ``process_died`` flips True; ``start()`` clears both so a stale
      death can't fast-fail a healthy respawned turn. Lets a streaming turn
      park on ``{events.get(), process_died_event.wait()}`` and learn of a
      mid-stream death promptly instead of via heartbeat silence.

The ADR-0022 dispatch-result cache and the two dispatch adapters stay in
``agent_pi_bridge.py`` - this module has no knowledge of tool dispatch.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from app.agent_pi_bridge import PiRpcError


def _write_agent_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Publish whole configuration files atomically across worker spawns."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding=encoding, dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

logger = logging.getLogger(__name__)

# Bundled Pi (vendor/pi) — never the user's global `pi` CLI / ~/.pi tree.
REPO_ROOT = Path(__file__).resolve().parents[3]
# Pi ≥v1.5.0 ships `dist/bundle/rpc-entry.js`; loading even an .mjs extension
# also requires its locked external jiti package under vendor/pi/node_modules.
# The tsgo-emitted `dist/rpc-entry.js` (workspace-wide build) stays a fallback
# for source checkouts. First existing entry wins; pi_doctor surfaces which.
PI_RPC_ENTRY_CANDIDATES = (
    REPO_ROOT / "vendor" / "pi" / "packages" / "coding-agent" / "dist" / "bundle" / "rpc-entry.js",
    REPO_ROOT / "vendor" / "pi" / "packages" / "coding-agent" / "dist" / "rpc-entry.js",
)


def resolve_pi_rpc_entry() -> Path:
    """First built Pi RPC entry that exists (bundle preferred), else the
    canonical bundle path (so the FileNotFoundError names the primary target)."""
    for candidate in PI_RPC_ENTRY_CANDIDATES:
        if candidate.is_file():
            return candidate
    return PI_RPC_ENTRY_CANDIDATES[0]


PI_RPC_ENTRY = resolve_pi_rpc_entry()
PI_AGENT_DIR = REPO_ROOT / ".pi" / "agent"
DEFAULT_SESSION_DIR = REPO_ROOT / ".pi" / "sessions"
BUNDLED_PI_PROVIDER = "webgis"

# REVIEW-P2 (Pi subprocess trust boundary):
# Cap a single stdout line at 16 MiB. Pi is a trusted subprocess, but "trusted"
# does not mean "well-behaved under every condition" — a runaway tool payload,
# a corrupted stream, or a debug log line that omits its trailing newline would
# otherwise buffer indefinitely in the reader thread and OOM the worker.
# 16 MiB is well above any legitimate AgentSessionEvent we have observed
# (typical tool-execution-end events are <10 KiB even with geojson refs).
MAX_STDOUT_LINE_BYTES = int(os.environ.get("PI_MAX_STDOUT_LINE_BYTES", 16 * 1024 * 1024))

# Cap the event queue at 1024 entries. A disconnected or slow SSE consumer
# would otherwise let events accumulate without bound; the previous unbounded
# asyncio.Queue meant a single abandoned chat turn could grow the process
# indefinitely. On overflow we drop new events and log — the alternative
# (blocking the reader) backpressures Pi's stdout pipe and can hang the
# subprocess, which is worse.
MAX_EVENT_QUEUE_SIZE = int(os.environ.get("PI_MAX_EVENT_QUEUE_SIZE", 1024))


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

# ADR-0288（W07）/ C14：v1.5.1 停机语义 —— stdin EOF 触发 vendor 有序
# shutdown()（dispose 等 tool settlement + credential writes，**可合法耗时
# 数秒**）；首 SIGTERM 同为有序清理；**第二**信号才立即强退。宿主侧对应：
# EOF → 有界等待（本值，默认 8s）→ 单次 SIGTERM → 有界等待 → SIGKILL。
# 我们绝不在窗口内重复发信号（会截断 vendor 清理）。
PI_GRACEFUL_STOP_TIMEOUT = _env_float("PI_GRACEFUL_STOP_TIMEOUT_S", 8.0)
#: SIGTERM 后的等待窗（终止信号已发，vendor 此刻要么已清理完要么卡死）。
PI_TERM_STOP_TIMEOUT = _env_float("PI_TERM_STOP_TIMEOUT_S", 2.0)


def _drain_and_close_stdin(proc: subprocess.Popen) -> None:
    """Close the child's stdin — v1.5.1 rpc-mode treats EOF as an orderly
    shutdown request (onInputEnd → shutdown()). Safe when already closed."""
    try:
        if proc.stdin is not None and not proc.stdin.closed:
            proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass  # child already gone / pipe torn down — EOF already delivered



# Review F16: environment allowlist for the Pi subprocess.
_PI_ENV_ALLOW_EXACT = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TZ",
    "TMPDIR", "TEMP", "TMP", "TERM", "SYSTEMROOT", "COMSPEC", "PATHEXT",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
    "API_PORT", "PORT", "TOOL_TIMEOUT_S",
    "WEBGIS_API_BASE", "WEBGIS_TOOL_TIMEOUT_MS",
    # LLM provider credentials Pi may legitimately need for PI_PROVIDER.
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
    "GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "OPENROUTER_API_KEY",
    "MOONSHOT_API_KEY", "DASHSCOPE_API_KEY", "ZAI_API_KEY", "GROQ_API_KEY",
    "XAI_API_KEY", "MISTRAL_API_KEY",
})
_PI_ENV_ALLOW_PREFIXES = ("LC_", "NODE_", "NPM_CONFIG_", "PI_", "XDG_")


def _pi_subprocess_env(source: "os._Environ[str] | dict[str, str]") -> dict[str, str]:
    """Allowlisted copy of ``source`` for the Pi subprocess (review F16).

    Operators can pass extra names via ``PI_ENV_PASSTHROUGH`` (comma list).
    """
    extra = {
        n.strip() for n in (source.get("PI_ENV_PASSTHROUGH") or "").split(",") if n.strip()
    }
    return {
        k: v for k, v in source.items()
        if k in _PI_ENV_ALLOW_EXACT or k in extra or k.startswith(_PI_ENV_ALLOW_PREFIXES)
    }


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
        self._pi_rpc_entry = Path(pi_rpc_entry) if pi_rpc_entry else PI_RPC_ENTRY
        self._session_dir = session_dir or DEFAULT_SESSION_DIR
        self._cwd = cwd or REPO_ROOT
        self._extension_paths = extension_paths or []
        self._process: Optional[subprocess.Popen] = None
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_EVENT_QUEUE_SIZE)
        self._request_counter = 0
        # PI-03（ADR-0286 D4）：本次 spawn 的 dump registry_generation（空 =
        # v2 dump / 未锚定 —— bridge 的代漂移检测 fail-open 不回收）。
        self.spawn_surface_generation: str = ""
        self._surface_dump_path: Optional[Path] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        # 审计 AGENT-05：Pi 进程死亡后标记为 True，让 _use_pi_bridge() 能回退
        self._process_died = False
        # 与 _process_died 同步翻转的 asyncio.Event：stream_prompt 把它和事件
        # 队列一起 wait，进程中途死亡时能立刻结束 turn，而不是靠心跳静默等满
        # PI_EVENT_STREAM_TIMEOUT。start() 里与 flag 一起 clear，防止上一次
        # 死亡的残留信号误杀重连后的新 turn。
        self._process_died_event = asyncio.Event()
        # Register an atexit hook so that if the Python process exits for any
        # catchable reason (SIGTERM, unhandled exception, normal shutdown) the
        # Pi child is terminated rather than orphaned. The k8s entrypoint's
        # `trap 'kill 0' TERM INT` covers graceful pod termination at the
        # container level; this hook covers the application level. SIGKILL/OOM
        # is uncatchable and remains a deployment-level concern (tini/init).
        atexit.register(self._cleanup_on_exit)

    def _cleanup_on_exit(self) -> None:
        """Terminate the Pi subprocess if still alive (atexit safety net).

        Runs on Python-level exit. Cannot await stop() (no running loop at
        exit time), so does a best-effort synchronous terminate/kill. This is
        not a substitute for stop() during graceful ASGI shutdown - it's the
        safety net for paths that bypass lifespan teardown.
        """
        proc = self._process
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            # Process already gone or slow to exit; fall through to kill.
            try:
                proc.kill()
                # #810: kill 后同步收尸（atexit 安全网同样不留僵尸）
                try:
                    proc.wait(timeout=2)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
            except ProcessLookupError:
                # Best-effort only: at exit the process may already be gone or
                # the OS is tearing down; nothing to do, nothing to log.
                pass

    @property
    def events(self) -> asyncio.Queue:
        """The raw AgentSessionEvent queue (consumed by the SSE mapper)."""
        return self._event_queue

    def is_alive(self) -> bool:
        """Check if the Pi child process is running and healthy.

        Proactively polls the OS subprocess: if the process exited before
        the stdout reader reached EOF, sets process_died, clears _process,
        and triggers death events immediately.
        """
        if self._process_died:
            return False
        if self._process is None:
            return False
        if self._process.poll() is not None:
            self._process_died = True
            self._process_died_event.set()
            self._process = None
            self._fail_all_pending("Pi process exited (detected via poll)")
            return False
        return True

    @property
    def process_died(self) -> bool:
        """True after the Pi process exits or the reader task crashes."""
        if not self._process_died and self._process is not None and self._process.poll() is not None:
            self.is_alive()
        return self._process_died

    @property
    def process_died_event(self) -> asyncio.Event:
        """Public death signal: set the moment ``process_died`` flips True.

        Awaitable alongside :attr:`events` so a streaming consumer can fast-fail
        on a mid-stream subprocess death instead of waiting out the stall
        budget. Cleared by :meth:`start` so a stale death from a previous
        process can't fast-fail a healthy respawned turn.
        """
        return self._process_died_event

    async def start(self) -> None:
        """Start the Pi subprocess."""
        if self._process is not None and self._process.poll() is None:
            return

        # PI-08（ADR-0286 D4）：spawn→ready 墙钟计时（冷启动观测）。
        _spawn_t0 = time.monotonic()

        # 重连/respawn：清掉上一次死亡的残留信号，防止陈旧死亡误杀新 turn
        # （reader finally 里与 flag 一起 set 的 event，这里与 flag 一起 clear）。
        self._process_died = False
        self._process_died_event.clear()

        # #1438：spawn 的全部阻塞步骤（目录/配置文件写盘、native surface
        # dump、subprocess.Popen 的 fork）整体 offload 到 worker 线程 ——
        # respawn 发生在流量中（Pi 死亡后的下一 turn），在协程体内直接执行
        # 会停摆事件循环上所有并发会话的流。
        await asyncio.to_thread(self._spawn_process)

        # Start reader tasks for stdout and stderr to avoid OS pipe deadlock
        self._reader_task = asyncio.create_task(self._read_responses())
        self._stderr_task = asyncio.create_task(self._read_stderr())

        # Yield to let the reader task start (avoid race where _send_request writes
        # to stdin before the reader is ready to consume the response).
        await asyncio.sleep(0)

        # Wait for Pi to initialize by polling get_state until it responds
        try:
            await asyncio.wait_for(self._wait_for_ready(), timeout=PI_STARTUP_READY_TIMEOUT)
        except (asyncio.TimeoutError, PiRpcError) as exc:
            await self.stop()
            raise PiRpcError("Pi did not become ready within its startup deadline") from exc

        # PI-03/PI-08（ADR-0286 D4）：锚定本次 spawn 的 dump 世代（代漂移
        # 检测锚点）+ spawn→ready 计时入聚合。观测面失败绝不影响启动。
        try:
            self.spawn_surface_generation = self._read_spawn_surface_generation()
        except Exception:  # noqa: BLE001 — 世代缺席则 bridge 不做代回收
            self.spawn_surface_generation = ""
        try:
            from app.services.chat.pi_surface_metrics import record_timing

            record_timing("spawn_ready_ms", (time.monotonic() - _spawn_t0) * 1000.0)
        except Exception:  # noqa: BLE001
            pass

        # audit4 #987: 可选的显式模型选择 —— PI_PROVIDER + PI_MODEL 同时设置
        # 时通过 set_model RPC 把 Pi 切到目录内指定模型（provider/modelId 必须
        # 是 Pi 已注册的；后端 chat-completions 端点不是 Pi provider，不能盲映射）。
        # 失败只记日志：模型保持 Pi 自身配置，绝不阻断桥启动。
        pi_provider = os.environ.get("PI_PROVIDER", "").strip()
        pi_model = os.environ.get("PI_MODEL", "").strip()
        if pi_provider and pi_model:
            try:
                await self.request("set_model", {"provider": pi_provider, "modelId": pi_model})
                logger.info(f"[PiRpcClient] Pi model set to {pi_provider}/{pi_model}")
            except Exception as e:  # noqa: BLE001 — 尽力而为
                logger.warning(f"[PiRpcClient] set_model {pi_provider}/{pi_model} failed: {e}")

    def _read_spawn_surface_generation(self) -> str:
        """PI-03（ADR-0286 D4）：读回本次 spawn dump 的 ``registry_generation``。

        v2 dump（无该键）→ 空串（bridge 的代漂移检测 fail-open 不回收）。
        """
        path = self._surface_dump_path
        if not path or not path.is_file():
            return ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return str(payload.get("registry_generation") or "")
        except Exception:  # noqa: BLE001 — 观测锚缺席不阻断启动
            return ""

    def _spawn_process(self) -> None:
        """Pi 子进程 spawn 的全部阻塞步骤（worker 线程内执行，#1438）。

        目录/配置文件写盘、native surface dump、subprocess.Popen 的 fork
        都是同步阻塞操作 —— respawn 发生在流量中（Pi 死亡后的下一 turn），
        在协程体内直接执行会停摆事件循环上所有并发会话的流。
        """
        self._session_dir.mkdir(parents=True, exist_ok=True)
        PI_AGENT_DIR.mkdir(parents=True, exist_ok=True)

        # Review F16: least privilege — the LLM-driven Pi host must not inherit
        # DATABASE_URL / JWT_SECRET_KEY / cloud & Redis credentials. Only an
        # allowlisted env is passed; the bridge secret and LLM key are added
        # explicitly below.
        env = _pi_subprocess_env(os.environ)
        env["PI_CODING_AGENT_DIR"] = str(PI_AGENT_DIR)
        env["PI_SESSION_DIR"] = str(self._session_dir)
        env["PI_OFFLINE"] = "1"
        env["PI_SKIP_VERSION_CHECK"] = "1"
        # 审计 SEC-01：注入共享密钥，Pi 扩展的 HTTP 回调用它调 /pi-tools/execute
        from app.core.bridge_secret import get_bridge_secret
        env["WEBGIS_BRIDGE_SECRET"] = get_bridge_secret()
        if not (env.get("WEBGIS_API_BASE") or "").strip():
            port = (env.get("API_PORT") or env.get("PORT") or "").strip()
            env["WEBGIS_API_BASE"] = (
                f"http://127.0.0.1:{port}" if port else "http://127.0.0.1:18000"
            )
        # audit4 #987: 后端 LLM 凭证映射进 Pi 子进程。仓内 models.json 指向
        # 同一套 LLM_BASE_URL / LLM_MODEL，不读用户 ~/.pi。
        llm_base = ""
        llm_model = ""
        try:
            from app.core.config import settings as _settings
            llm_base = str(getattr(_settings, "LLM_BASE_URL", "") or "")
            llm_model = str(getattr(_settings, "LLM_MODEL", "") or "")
            if not env.get("OPENAI_API_KEY"):
                _key = getattr(_settings, "LLM_API_KEY", "")
                if _key and "your-api-key" not in _key:
                    env["OPENAI_API_KEY"] = _key
        except Exception:  # noqa: BLE001 — env 映射是尽力而为，绝不阻断 spawn
            pass

        if llm_base and llm_model:
            _write_agent_text(PI_AGENT_DIR / "models.json",
                json.dumps(
                    {
                        "providers": {
                            BUNDLED_PI_PROVIDER: {
                                "baseUrl": llm_base.rstrip("/"),
                                "api": "openai-completions",
                                "apiKey": "$OPENAI_API_KEY",
                                "compat": {
                                    "supportsDeveloperRole": False,
                                    "supportsReasoningEffort": False,
                                },
                                "models": [
                                    {"id": llm_model, "name": llm_model, "reasoning": True}
                                ],
                            }
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            _write_agent_text(PI_AGENT_DIR / "settings.json",
                json.dumps(
                    {"defaultProvider": BUNDLED_PI_PROVIDER, "defaultModel": llm_model},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        # Build CLI args with --extension flags for each extension path
        rpc_entry = self._pi_rpc_entry.expanduser().resolve()
        if not rpc_entry.is_file():
            raise FileNotFoundError(
                f"Bundled Pi RPC entry missing: {rpc_entry}. "
                "Build vendor/pi (packages/coding-agent dist/rpc-entry.js)."
            )
        # Fail-fast: without this dump Pi would start with webgis_execute only
        # and the whole native surface (incl. webgis_map_intent) is unreachable
        # for the process lifetime. A raise here aborts the spawn; the API
        # lifespan catches it and falls back to ChatEngine.
        # ADR-0103: dump is now the v2 dynamic surface (registered superset +
        # default-active); extension registers all and activates the default.
        from app.services.chat.pi_native_surface import dump_surface_file
        _dump_path = dump_surface_file(PI_AGENT_DIR / "native-tools.json")
        env["WEBGIS_NATIVE_TOOLS_PATH"] = str(_dump_path)
        # PI-03（ADR-0286 D4）：记路径供 start() 读回世代锚点。
        self._surface_dump_path = _dump_path

        # GIS product: Pi is the host, not a coding agent. Built-in bash/read/
        # write/edit would otherwise swallow GIS failures (live: cartography
        # status validation error → bash). Extension tools stay enabled.
        args = [
            "node", str(rpc_entry), "--mode", "rpc", "--no-session",
            "--no-builtin-tools",
        ]
        for ext_path in self._extension_paths:
            args.extend(["--extension", str(ext_path)])

        # Binary pipes: _readline_bounded concatenates into a bytearray and
        # compares against b"\\n". Text mode made stdout.read(1) return str
        # and crashed the reader on the first character (TypeError).
        self._process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(self._cwd),
            text=False,
        )

    async def _wait_for_ready(self) -> None:
        """Poll get_state until Pi responds or the reader task ends."""
        while True:
            if self.process_died:
                raise PiRpcError("Pi exited before becoming ready")
            try:
                await self.request("get_state", {})
                return  # Pi is ready
            except PiRpcError:
                if self.process_died:
                    raise
                await asyncio.sleep(0.2)

    async def stop(self) -> None:
        """Stop the Pi subprocess (ADR-0288 / W07, vendor C14).

        升序收敛，窗口分开记录：stdin EOF（vendor 有序 shutdown：dispose 等
        tool settlement + credential writes）→ ``PI_GRACEFUL_STOP_TIMEOUT``（默
        认 8s）内等自然退出 → 单次 SIGTERM → ``PI_TERM_STOP_TIMEOUT``（2s）→
        SIGKILL → 收尸。绝不双发信号（vendor 语义：第二信号立即强退、截断
        清理）；SIGTERM 只在 EOF 窗超时后才发（此刻清理要么完成要么卡死）。
        """
        proc = self._process
        if proc is None:
            return

        # CONC-F5: the reader task's finally can clear self._process the
        # moment the terminated process exits (EOF → poll() is not None) —
        # dereferencing the field again below raised AttributeError and
        # skipped reader/stderr task reclamation. Work on a local handle.
        try:
            proc = self._process
            _t0 = time.monotonic()
            # 1) 有序：stdin EOF → vendor shutdown()（dispose 可合法耗时数秒）
            _drain_and_close_stdin(proc)
            try:
                # proc.wait 是阻塞调用 —— offload 到 worker 线程，绝不冻结
                # 事件循环上其他并发会话（#1438 同款纪律）。
                await asyncio.to_thread(proc.wait, timeout=PI_GRACEFUL_STOP_TIMEOUT)
                logger.info(
                    "[PiRpcClient] orderly stop via stdin EOF after %.2fs",
                    time.monotonic() - _t0,
                )
            except subprocess.TimeoutExpired:
                # 2) EOF 窗超时：单次 SIGTERM（vendor 首信号=有序、第二=强退；
                #    我们只发一次，绝不截断清理）。
                proc.terminate()
                try:
                    await asyncio.to_thread(proc.wait, timeout=PI_TERM_STOP_TIMEOUT)
                    logger.warning(
                        "[PiRpcClient] SIGTERM stop after EOF window (%.2fs); "
                        "orderly cleanup did not finish in %ss",
                        time.monotonic() - _t0, PI_GRACEFUL_STOP_TIMEOUT,
                    )
                except subprocess.TimeoutExpired:
                    # 3) 强制：SIGKILL + 收尸（#810：不留 defunct 僵尸）。
                    proc.kill()
                    try:
                        await asyncio.to_thread(proc.wait, timeout=2)
                    except (ProcessLookupError, subprocess.TimeoutExpired):
                        pass
                    logger.error(
                        "[PiRpcClient] SIGKILL stop after %.2fs (EOF+SIGTERM "
                        "windows both expired)",
                        time.monotonic() - _t0,
                    )
        finally:
            if self._process is proc:
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

    def _enqueue_event(self, event: dict) -> None:
        """Keep completion events when incremental messages exhaust the budget."""
        terminal = {"agent_settled", "agent_end", "tool_execution_end"}
        try:
            self._event_queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            if event.get("type") not in terminal:
                logger.warning("Pi event queue full; dropping incremental event %s", event.get("type"))
                return
        buffered = []
        while not self._event_queue.empty():
            buffered.append(self._event_queue.get_nowait())
        victim = next((i for i, item in enumerate(buffered)
                       if item.get("type") in {"message_update", "message_delta"}), None)
        if victim is None:
            victim = next((i for i, item in enumerate(buffered)
                           if item.get("type") not in terminal), None)
        if victim is None:
            # No safe eviction remains: make the turn fail explicitly, never hang.
            for item in buffered:
                self._event_queue.put_nowait(item)
            self._process_died = True
            self._process_died_event.set()
            self._fail_all_pending("Pi terminal event queue overflow")
            logger.error("Pi terminal event queue overflow; turn must restart")
            return
        buffered.pop(victim)
        for item in buffered:
            self._event_queue.put_nowait(item)
        self._event_queue.put_nowait(event)

    async def _read_responses(self) -> None:
        """Read responses and events from Pi stdout.

        审计 AGENT-05：之前 Pi 进程退出时 readline 返回 ""，循环 break 但
        所有 _pending_requests 的 future 永远不会被 resolve -> 调用方挂 300s。
        现在退出时主动 fail 所有 pending 请求。
        """
        try:
            while self._process and self._process.stdout:
                # Bounded read: cap a single line at MAX_STDOUT_LINE_BYTES so
                # a runaway Pi payload can't OOM the reader thread. readline()
                # has no length limit; we approximate one by reading in chunks
                # and bailing if no newline appears within the budget.
                line = await asyncio.get_running_loop().run_in_executor(
                    None, self._readline_bounded
                )
                if line is None:
                    # Line exceeded the budget without a newline. Drop it and
                    # continue — the next readline picks up after the next
                    # newline in the stream, which resyncs the reader.
                    logger.warning(
                        "[PiRpcClient] Dropped stdout line exceeding %d bytes "
                        "(no newline within budget); stream may be resyncing",
                        MAX_STDOUT_LINE_BYTES,
                    )
                    self._fail_all_pending("Pi response exceeded stdout byte budget")
                    if self._process is not None:
                        self._process.terminate()
                    break
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
                        self._enqueue_event(obj)
                except json.JSONDecodeError:
                    logger.warning(f"[PiRpcClient] Invalid JSON: {line[:200]}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[PiRpcClient] Reader task crashed: {e}", exc_info=True)
        finally:
            # Pi 进程退出 / reader 异常 -> fail 所有 pending 请求，避免 300s 挂起
            self._fail_all_pending("Pi process exited or reader stopped")
            # 标记为不可用（flag + 可等待的 asyncio.Event 同步翻转，让正在
            # stream_prompt 里等待的 turn 立刻感知死亡并 fast-fail）
            self._process_died = True
            self._process_died_event.set()
            # Clear the dead process reference so start() can respawn (its guard
            # is `if self._process is not None: return`). Only clear when the
            # process has actually exited (poll() returns a non-None exit code) -
            # on the stop()-cancellation path the process may still be
            # mid-terminate and stop()'s own finally handles the final clearing.
            if self._process is not None and self._process.poll() is not None:
                self._process = None
                logger.error(
                    "[PiRpcClient] Pi subprocess exited (poll=dead); "
                    "_process cleared, bridge can respawn via start()"
                )
            else:
                logger.error(
                    "[PiRpcClient] Pi subprocess exited; bridge is now unavailable until restart"
                )

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

    def fail_all_pending(self, reason: str) -> None:
        """Public version of _fail_all_pending. Used by PiBridge.abort() so
        in-flight `prompt` futures resolve with PiRpcError instead of timing
        out 30s later. Same semantics as _fail_all_pending."""
        self._fail_all_pending(reason)

    def fail_pending_ids(self, request_ids, reason: str) -> int:
        """Fail ONLY the given pending request ids (see PiBridge.abort F1:
        a late abort must not fail a successor turn's freshly registered
        futures). Returns how many were failed."""
        failed = 0
        for rid in request_ids:
            future = self._pending_requests.pop(rid, None)
            if future is not None and not future.done():
                future.set_exception(PiRpcError(reason))
                failed += 1
        return failed

    def pending_request_ids(self) -> set:
        """Snapshot of currently pending request ids (abort-scoping)."""
        return set(self._pending_requests.keys())

    def _readline_bounded(self) -> Optional[bytes]:
        """Read one line from Pi stdout, capped at MAX_STDOUT_LINE_BYTES.

        Returns the line (bytes, including newline) on success, b"" on EOF,
        or None if the line exceeded the budget without a newline (the caller
        should resync by reading until the next newline and dropping the
        oversized payload).

        Synchronous — called via run_in_executor so it doesn't block the
        event loop. Production buffered pipes use one size-bounded readline;
        minimal legacy pipe adapters retain the bounded read(1) compatibility
        path. Neither buffers an entire unbounded malformed line.
        """
        stdout = self._process.stdout
        if stdout is None:
            return b""
        import io

        if isinstance(stdout, (io.BufferedIOBase, io.RawIOBase, io.TextIOBase)):
            line = stdout.readline(MAX_STDOUT_LINE_BYTES)
            data = line.encode("utf-8") if isinstance(line, str) else line
            if len(data) > MAX_STDOUT_LINE_BYTES:
                return None
            if data.endswith(b"\n") or len(data) < MAX_STDOUT_LINE_BYTES:
                return data
            return None
        buf = bytearray()
        budget = MAX_STDOUT_LINE_BYTES
        while budget > 0:
            ch = stdout.read(1)
            if not ch:
                # EOF
                return bytes(buf) if buf else b""
            # text=True pipes yield str; production is binary. Accept both.
            chunk = ch.encode("utf-8") if isinstance(ch, str) else ch
            buf += chunk
            if chunk.endswith(b"\n") or ch == "\n":
                return bytes(buf)
            budget -= len(chunk)
        # Budget exhausted without a newline — signal overflow.
        return None

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
            # #810: 在 executor 跳转前捕获本地句柄 —— stop() 可能在循环侧
            # 校验之后、线程执行之前把 self._process 置 None，线程里再读
            # self._process.stdin 会抛裸 AttributeError 而非 PiRpcError。
            proc = self._process

            def _write_and_flush():
                stdin = proc.stdin if proc is not None else None
                if stdin is None:
                    # stop() 并发清场 —— 分类为 PiRpcError 而非 AttributeError
                    raise BrokenPipeError("Pi process stopped concurrently")
                # Binary Popen (production) needs bytes; a text pipe keeps str.
                payload = line if getattr(stdin, "encoding", None) else line.encode("utf-8")
                stdin.write(payload)
                stdin.flush()

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _write_and_flush)
            result = await asyncio.wait_for(future, timeout=PI_RPC_TIMEOUT)
            return result
        except asyncio.TimeoutError:
            raise PiRpcError(f"Pi request timeout: {command}")
        except (BrokenPipeError, OSError) as e:
            raise PiRpcError(f"Pi pipe error: {e}")
        finally:
            # F19: pop unconditionally, not just on TimeoutError/pipe errors.
            # A caller cancellation (CancelledError) previously leaked the
            # registry entry — one dead future per cancelled request, and a
            # late response for that id would resolve a future nobody awaits.
            # Idempotent: the normal response path already popped the entry
            # in _handle_response, so this is a no-op there.
            self._pending_requests.pop(request_id, None)
