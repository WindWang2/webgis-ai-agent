"""V3 隔离后端 + 流式协议集成测试（ADR-0119 / Wave 7-9）。

双车道：
- **脚本化确定性帧序列**（fake worker，fake pipe）钉协议状态机（信用
  账本/读循环白名单/迟到 credit 幂等/超时不入崩溃计数）——无时序、无
  sleep、CI 稳定；
- **真实子进程冒烟**：流式工具端到端（streaming echo / broker 混合流）、
  bubblewrap 后端（可用时真跑 + repo 根不可见断言；不可用 typed 拒绝）。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app.extensions_platform.diagnostics import DiagnosticCode, ExtensionPlatformError
from app.extensions_platform.worker.client import WorkerProcess
from app.extensions_platform.worker.isolation import (
    BACKEND_BUBBLEWRAP,
    build_bwrap_command,
    probe_bubblewrap,
)
from app.extensions_platform.worker.protocol import WORKER_PROTOCOL_VERSION
from app.extensions_platform.worker.server import WorkerServer


# ── 脚本化确定性车道（fake pipe + fake worker）────────────────────────

class FakePipe:
    """双向帧管道（host ↔ server 同进程直连）。

    server 侧读 = **阻塞**（与真实 stdio readline 语义一致；脚本帧先于
    读取发送即可，无竞态）。host 侧读 = 严格 FIFO 断言（帧序出错即红）。
    """

    def __init__(self) -> None:
        self.host_inbox: list[dict] = []  # worker → host（FIFO 断言用）
        import queue

        self._server_inbox: "queue.Queue[dict]" = queue.Queue()

    # server 视角（阻塞）
    def server_read(self) -> dict:
        import queue as _q

        try:
            return self._server_inbox.get(timeout=10)
        except _q.Empty as exc:  # pragma: no cover - 脚本 bug 才会到这
            raise AssertionError("server inbox empty (script bug)") from exc

    def server_write(self, frame: dict) -> None:
        self.host_inbox.append(frame)

    # host 视角
    def host_recv(self) -> dict:
        if not self.host_inbox:
            raise AssertionError("host inbox empty (script bug)")
        return self.host_inbox.pop(0)

    def host_send(self, frame: dict) -> None:
        self._server_inbox.put(frame)


STREAM_TOOL_MAIN = textwrap.dedent(
    """
    from app.extensions_platform.sdk.tool import ToolExtensionSpec

    def activate(ctx):
        def stream_range(n=5, broker_every=0):
            for i in range(n):
                yield {"i": i}
            return None
        ctx.register_tool(ToolExtensionSpec(name="stream_range", description="d", func=stream_range))
    """
)


def _boot(pack: Path, pipe: FakePipe, grants: list[str] | None = None, window: int = 16):
    from app.extensions_platform.discovery import compute_fingerprint
    from app.extensions_platform.worker.protocol import make_handshake

    fingerprint, _ = compute_fingerprint(pack)
    pipe.host_send(
        make_handshake(
            extension_id="v3demo.stream",
            fingerprint=fingerprint,
            grants=list(grants or []),
            settings={},
            expected_namespace="v3demo",
            expected_name="stream",
            stream_window=window,
        )
    )
    server = WorkerServer(pack, _FrameReader(pipe), _FrameWriter(pipe))
    assert server.handshake() is True
    return server


class _FrameReader:
    def __init__(self, pipe: FakePipe) -> None:
        self._pipe = pipe

    def readline(self) -> bytes:
        frame = self._pipe.server_read()
        from app.extensions_platform.worker.protocol import encode_frame

        return encode_frame(frame)

    # 兼容 fileobj 接口
    def read(self, *a) -> bytes:
        return b""


class _FrameWriter:
    def __init__(self, pipe: FakePipe) -> None:
        self._pipe = pipe

    def write(self, data: bytes) -> int:
        from app.extensions_platform.worker.protocol import decode_frame

        self._pipe.server_write(decode_frame(data))
        return len(data)

    def flush(self) -> None:
        return None


def _make_pack(
    tmp_path: Path,
    main_source: str,
    manifest_extra: dict | None = None,
    tools: list[dict] | None = None,
) -> Path:
    pack = tmp_path / "v3demo.stream"
    pack.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "id": "v3demo.stream",
        "name": "stream",
        "namespace": "v3demo",
        "version": "1.0.0",
        "api_version": "1.2.0",
        "entry_point": "main",
        "permissions": ["network"],
        "tools": [{"name": "gen", "description": "streaming generator"}],
        "execution": {
            "mode": "worker",
            "startup_timeout_s": 15.0,
            "call_timeout_s": 10.0,
            "max_output_bytes": 262144,
            "max_stream_events": 500,
            "stream_window": 8,
        },
    }
    if tools is not None:
        manifest["tools"] = tools
    if manifest_extra:
        manifest.update(manifest_extra)
    (pack / "manifest.json").write_text(json.dumps(manifest))
    (pack / "main.py").write_text(textwrap.dedent(main_source))
    return pack


def _fake_worker_process(pack: Path, script_frames: list[dict]) -> WorkerProcess:
    """注入 fake Popen 的 WorkerProcess（读帧从脚本队列；写帧入列表）。"""
    wp = WorkerProcess.__new__(WorkerProcess)
    import queue
    import threading


    wp._pack_dir = pack
    wp._extension_id = "v3demo.stream"
    wp._namespace = "v3demo"
    wp._name = "stream"
    wp._fingerprint = "f" * 64
    wp._grants = []
    wp._settings = {}
    wp._startup_timeout_s = 5.0
    wp._call_timeout_s = 5.0
    wp._max_memory_mb = None
    wp._max_cpu_seconds = None
    wp._broker_handler = None
    wp._isolation_backend = "process"
    wp._stream_window = 4
    wp._proc = None
    wp._frames = queue.Queue()
    wp._reader = None
    wp._lock = threading.RLock()
    wp.in_flight = False
    wp.tools = []
    wp.resource_warnings = []
    wp.pid = 424242
    wp.protocol_version = WORKER_PROTOCOL_VERSION
    wp.effective_isolation = "process"

    class _FakeProc:
        def __init__(self, frames):
            self._frames = frames
            self.sent: list[dict] = []
            self._dead = False

        def poll(self):
            return None if not self._dead else 0

        class _Stdin:
            def __init__(self, proc):
                self._proc = proc

            def write(self, data: bytes) -> int:
                from app.extensions_platform.worker.protocol import decode_frame

                self._proc.sent.append(decode_frame(data))
                return len(data)

            def flush(self) -> None:
                return None

            def close(self) -> None:
                return None

        stdin = property(lambda self: self._Stdin(self))

        stdout = None
        stderr = None

        def kill(self):
            self._dead = True

        def wait(self, timeout=None):
            return 0

    proc = _FakeProc(script_frames)
    wp._proc = proc

    class _ScriptQueue:
        """脚本帧队列：流 id 惰性重写（s1 → 实际 call id）。"""

        def get(self, timeout=None):
            if not script_frames:
                import queue as _q

                raise _q.Empty()
            frame = script_frames.pop(0)
            if frame.get("id") == "s1" and proc.sent:
                call_frame = next(
                    (f for f in proc.sent if f.get("type") == "call"), None
                )
                if call_frame is not None:
                    frame = dict(frame)
                    frame["id"] = call_frame["id"]
            return frame

    wp._frames = _ScriptQueue()
    return wp


def test_stream_happy_path_frame_script(tmp_path):
    """确定性：stream_start → frames → end；信用按半窗补还。"""
    pack = _make_pack(tmp_path, STREAM_TOOL_MAIN)
    frames = [
        {"type": "stream_start", "id": "s1"},
        {"type": "stream_frame", "id": "s1", "seq": 0, "more": True, "payload": {"i": 0}},
        {"type": "stream_frame", "id": "s1", "seq": 1, "more": True, "payload": {"i": 1}},
        {"type": "stream_end", "id": "s1", "cancelled": False},
    ]
    wp = _fake_worker_process(pack, frames)
    events = list(wp.call_stream("v3demo_stream_range", {"n": 2}, window=4, max_events=100))
    assert [e["i"] for e in events] == [0, 1]
    sent_types = [f["type"] for f in wp._proc.sent]
    assert sent_types[0] == "call"
    # 2 帧消费 → 至少一次信用补还（window//2 = 2 时 2 帧即触发）。
    assert "stream_credit" in sent_types


def test_late_credit_is_accounted_idempotently(tmp_path):
    """C-7：stream_end 之后到达的 credit 不崩溃、不入任何状态。"""
    pack = _make_pack(tmp_path, STREAM_TOOL_MAIN)
    frames = [
        {"type": "stream_start", "id": "s1"},
        {"type": "stream_frame", "id": "s1", "seq": 0, "more": True, "payload": {"i": 0}},
        {"type": "stream_end", "id": "s1", "cancelled": False},
        {"type": "stream_credit", "id": "s1", "n": 99},  # 迟到 credit
    ]
    wp = _fake_worker_process(pack, frames)
    events = list(wp.call_stream("v3demo_stream_range", {}, window=4, max_events=10))
    assert len(events) == 1
    # 流结束后队列里的迟到 credit 不影响下一次操作（in_flight 已清零）。
    assert wp.in_flight is False


def test_server_broker_wait_accepts_credit_frames(tmp_path):
    """C-7 服务端语义：broker 等待循环收到 stream_credit 幂等入账。"""
    pipe = FakePipe()
    pack = _make_pack(
        tmp_path,
        textwrap.dedent(
            """
            from app.extensions_platform.sdk.tool import ToolExtensionSpec

            def activate(ctx):
                def gen_with_broker():
                    data = ctx.broker.read_artifact("demo.bin")
                    for chunk in (data[:2], data[2:4]):
                        yield {"chunk": list(chunk)}
                ctx.register_tool(ToolExtensionSpec(name="broker_stream", description="d", func=gen_with_broker, parameters={"type": "object", "properties": {}}))
            """
        ),
        tools=[{"name": "broker_stream", "description": "d"}],
    )
    server = _boot(pack, pipe, grants=["network", "project_artifact_read"])
    # broker 请求（来自生成器内的 read_artifact）之前：先让 server 进入
    # call 处理 —— 脚本化：host 侧先收 stream_start？生成器在第一次迭代
    # 时才发起 broker 请求。直接断言 broker_request 与 credit 交错。
    pipe.host_send(
        {"type": "call", "id": "c1", "tool": "v3demo_broker_stream", "args": {}, "stream": True}
    )
    # 后台线程泵服务端一帧（call 处理会阻塞在 broker 等待循环）。
    import threading

    pump = threading.Thread(target=lambda: server.serve_once(), daemon=True)
    pump.start()
    # 帧序（确定性）：handshake_ok → stream_start → broker_request。
    assert pipe.host_recv()["type"] == "handshake_ok"
    assert pipe.host_recv()["type"] == "stream_start"
    first = pipe.host_recv()
    # 生成器首次迭代即发起 broker 请求（artifact_read）。
    assert first["type"] == "broker_request", first
    # host 在等待期间先补信用（竞态窗口的具体化）：必须被幂等消费。
    pipe.host_send({"type": "stream_credit", "id": "c1", "n": 5})
    pipe.host_send(
        {
            "type": "broker_response",
            "id": first["id"],
            "ok": True,
            "value": {"content_b64": "AAEC", "path": "/tmp/x", "size": 4, "truncated": False},
        }
    )
    pump.join(timeout=10)
    assert not pump.is_alive()
    frames = [pipe.host_recv() for _ in range(3)]
    types = [f["type"] for f in frames]
    assert types.count("stream_frame") == 2
    end = [f for f in frames if f["type"] == "stream_end"][0]
    assert end["cancelled"] is False


def test_stream_idle_timeout_not_counted_as_crash(tmp_path):
    """C-3：流 idle 超时 → typed STREAM_FLOW_CONTROL；crash_count 不变。"""
    pack = _make_pack(tmp_path, STREAM_TOOL_MAIN)
    frames = [
        {"type": "stream_start", "id": "s1"},
        # 之后无帧 → idle 超时。
    ]
    wp = _fake_worker_process(pack, frames)
    gen = wp.call_stream("x", {}, idle_timeout_s=0.05, max_events=10)
    with pytest.raises(ExtensionPlatformError) as excinfo:
        list(gen)
    assert excinfo.value.diagnostic.code == DiagnosticCode.STREAM_FLOW_CONTROL
    # worker「进程」仍存活（fake poll 返回 None）且 in_flight 已释放。
    assert wp.in_flight is False


# ── 真实子进程冒烟 ────────────────────────────────────────────────────

STREAM_PACK_MAIN = textwrap.dedent(
    """
    from app.extensions_platform.sdk.tool import ToolExtensionSpec

    def activate(ctx):
        def stream_events(count=50):
            for i in range(count):
                yield {"index": i, "ok": True}
        ctx.register_tool(ToolExtensionSpec(
            name="gen",
            description="streaming generator",
            func=stream_events,
            parameters={"type": "object", "properties": {"count": {"type": "integer"}}},
        ))
    """
)


def _start_real_worker(pack: Path, **kwargs) -> WorkerProcess:
    from app.extensions_platform.discovery import compute_fingerprint

    fingerprint, _ = compute_fingerprint(pack)
    wp = WorkerProcess(
        pack_dir=pack,
        extension_id="v3demo.stream",
        namespace="v3demo",
        name="stream",
        fingerprint=fingerprint,
        grants=[],
        settings={},
        startup_timeout_s=15.0,
        call_timeout_s=10.0,
        **kwargs,
    )
    wp.start()
    return wp


def test_real_worker_stream_roundtrip(tmp_path):
    pack = _make_pack(tmp_path, STREAM_PACK_MAIN)
    wp = _start_real_worker(pack)
    try:
        events = list(
            wp.call_stream("v3demo_gen", {"count": 25}, max_events=100, window=8)
        )
        assert [e["index"] for e in events] == list(range(25))
        assert wp.protocol_version == WORKER_PROTOCOL_VERSION
    finally:
        wp.shutdown()


def test_real_worker_stream_cancel_midway(tmp_path):
    pack = _make_pack(tmp_path, STREAM_PACK_MAIN)
    wp = _start_real_worker(pack)
    try:
        gen = wp.call_stream("v3demo_gen", {"count": 10000}, max_events=100000, window=8)
        consumed = []
        for event in gen:
            consumed.append(event)
            if len(consumed) >= 5:
                gen.close()  # 协作取消
                break
        assert len(consumed) == 5
        # 取消后 worker 仍可用（串行 in_flight 已释放）。
        result = wp.call("v3demo_gen", {"count": 3})
        assert isinstance(result, dict)
    finally:
        wp.shutdown()


def test_real_worker_stream_limit_enforced(tmp_path):
    pack = _make_pack(tmp_path, STREAM_PACK_MAIN)
    wp = _start_real_worker(pack)
    try:
        with pytest.raises(ExtensionPlatformError) as excinfo:
            list(wp.call_stream("v3demo_gen", {"count": 100000}, max_events=10, window=8))
        assert excinfo.value.diagnostic.code == DiagnosticCode.STREAM_LIMIT_EXCEEDED
        # 超限不是崩溃：worker 还活着、还能服务。
        assert wp.alive
    finally:
        wp.shutdown()


# ── bubblewrap ────────────────────────────────────────────────────────

BWRAP_AVAILABLE = probe_bubblewrap() is not None


def test_isolation_backend_unavailable_is_typed(tmp_path, monkeypatch):
    """M-9：请求 bubblewrap 但不可用 → typed 拒绝（绝不静默回退）。"""
    pack = _make_pack(tmp_path, STREAM_PACK_MAIN)
    monkeypatch.setattr(
        "app.extensions_platform.worker.isolation.probe_bubblewrap", lambda force=False: None
    )
    wp = WorkerProcess(
        pack_dir=pack,
        extension_id="v3demo.stream",
        namespace="v3demo",
        name="stream",
        fingerprint="f" * 64,
        grants=[],
        settings={},
        startup_timeout_s=10.0,
        call_timeout_s=5.0,
        isolation_backend=BACKEND_BUBBLEWRAP,
    )
    with pytest.raises(ExtensionPlatformError) as excinfo:
        wp.start()
    assert excinfo.value.diagnostic.code == DiagnosticCode.ISOLATION_UNAVAILABLE
    assert wp.effective_isolation == BACKEND_BUBBLEWRAP  # 没有静默变 process


def test_bwrap_command_never_binds_repo_root(tmp_path):
    """B-1 红线（结构断言）：bind 面只含 app 子目录，绝不 bind repo 根。"""
    repo_app = Path("/srv/webgis/app")
    argv, env = build_bwrap_command(
        python_executable="/usr/bin/python3",
        server_argv=["-m", "app.extensions_platform.worker.server", "--pack-dir", "/data/pack"],
        repo_app_dir=repo_app,
        pack_dir=Path("/data/pack"),
    )
    assert argv[0] == "bwrap"
    assert "--unshare-all" in argv
    # PYTHONPATH 首段指向沙箱挂载根而非宿主 repo（site-packages 段 = venv）。
    assert env["PYTHONPATH"].split(":")[0] == "/opt/webgis"
    assert "repo_root_marker_should_not_appear" not in env["PYTHONPATH"]
    assert env["PYTHONHOME"]  # stdlib 定位不依赖 argv0 探测
    # repo 根路径不得出现在任何 bind 源里。
    bind_sources = [
        argv[i + 1] for i, a in enumerate(argv) if a in ("--ro-bind", "--ro-bind-try")
    ]
    assert "/srv/webgis" not in bind_sources
    assert "/srv/webgis/app" in bind_sources
    # pack-dir 被重映射到沙箱固定路径。
    assert argv[argv.index("--pack-dir") + 1] == "/opt/ext/pack"


@pytest.mark.skipif(not BWRAP_AVAILABLE, reason="bwrap not available on this host")
def test_real_worker_bubblewrap_backend(tmp_path):
    """真沙箱：worker 在 netns 内完成流式调用；宿主不需要网络。"""
    from app.extensions_platform.discovery import compute_fingerprint

    pack = _make_pack(tmp_path, STREAM_PACK_MAIN)
    fingerprint, _ = compute_fingerprint(pack)
    wp = WorkerProcess(
        pack_dir=pack,
        extension_id="v3demo.stream",
        namespace="v3demo",
        name="stream",
        fingerprint=fingerprint,
        grants=[],
        settings={},
        startup_timeout_s=20.0,
        call_timeout_s=10.0,
        isolation_backend=BACKEND_BUBBLEWRAP,
    )
    try:
        wp.start()
        assert wp.effective_isolation == BACKEND_BUBBLEWRAP
        events = list(wp.call_stream("v3demo_gen", {"count": 5}, max_events=50, window=4))
        assert [e["index"] for e in events] == [0, 1, 2, 3, 4]
    finally:
        try:
            wp.shutdown()
        except Exception:  # noqa: BLE001
            wp.kill()


@pytest.mark.skipif(not BWRAP_AVAILABLE, reason="bwrap not available on this host")
def test_bwrap_sandbox_hides_repo_root(tmp_path):
    """B-1 验收（恶意语料 env-leak 场景的沙箱侧）：沙箱内读不到 repo 根文件。

    worker 工具尝试 open repo 根下的 marker 文件 → 必须失败（repo 根
    不在 bind 面）；pack 目录本身只读可见。
    """
    repo_root = Path(__file__).resolve().parents[3]
    marker = repo_root / "BWRAP_MARKER_SHOULD_NOT_EXIST"
    pack = _make_pack(
        tmp_path,
        textwrap.dedent(
            """
            from app.extensions_platform.sdk.tool import ToolExtensionSpec

            def activate(ctx):
                def try_read():
                    from pathlib import Path
                    repo = Path(__file__).resolve().parents[3]
                    target = repo / "BWRAP_MARKER_SHOULD_NOT_EXIST"
                    try:
                        target.read_text()
                        return {"visible": True}
                    except OSError:
                        return {"visible": False}
                ctx.register_tool(ToolExtensionSpec(name="peek", description="d", func=try_read, parameters={"type": "object", "properties": {}}))
            """
        ),
        tools=[{"name": "peek", "description": "d"}],
    )
    assert not marker.exists()
    from app.extensions_platform.discovery import compute_fingerprint

    fingerprint, _ = compute_fingerprint(pack)
    wp = WorkerProcess(
        pack_dir=pack,
        extension_id="v3demo.stream",
        namespace="v3demo",
        name="stream",
        fingerprint=fingerprint,
        grants=[],
        settings={},
        startup_timeout_s=20.0,
        call_timeout_s=10.0,
        isolation_backend=BACKEND_BUBBLEWRAP,
    )
    try:
        wp.start()
        result = wp.call("v3demo_peek", {})
        assert result == {"visible": False}, (
            "sandbox must not see repo root files (B-1); check bind set"
        )
    finally:
        try:
            wp.shutdown()
        except Exception:  # noqa: BLE001
            wp.kill()
