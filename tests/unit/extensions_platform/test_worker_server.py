"""Worker 协议与 server 事件循环测试（ADR-0105 Wave 2）。

进程内驱动：用 io.BytesIO 模拟管道，直接构造 WorkerServer，不 spawn 真子
进程（真子进程生命周期见 test_worker_integration.py，Wave 3）。
"""

from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path

import pytest

from app.extensions_platform.discovery import compute_fingerprint
from app.extensions_platform.diagnostics import DiagnosticCode
from app.extensions_platform.worker.protocol import (
    FRAME_MAX_BYTES,
    WORKER_PROTOCOL_VERSION,
    ProtocolError,
    decode_frame,
    encode_frame,
    error_payload,
    make_handshake,
)
from app.extensions_platform.worker.server import WorkerServer

TOOL_MAIN = textwrap.dedent(
    """
    from app.extensions_platform.sdk import ToolExtensionSpec


    def _run(x: float) -> dict:
        return {"value": x * 2}


    def _boom() -> dict:
        raise RuntimeError("tool exploded")


    def _denied() -> dict:
        return {"secret": "nope"}


    def activate(ctx):
        ctx.register_tool(ToolExtensionSpec(
            name="doubler", description="double a number", func=_run,
            side_effect="pure", deterministic=True,
            parameters={"type": "object", "properties": {"x": {"type": "number"}},
                        "required": ["x"]},
        ))
        ctx.register_tool(ToolExtensionSpec(
            name="boom", description="always raises", func=_boom,
            side_effect="pure", deterministic=True,
            parameters={"type": "object", "properties": {}},
        ))
        ctx.register_tool(ToolExtensionSpec(
            name="denied", description="needs network", func=_denied,
            side_effect="pure", deterministic=True, network=True,
            required_permissions=["network"],
            parameters={"type": "object", "properties": {}},
        ))
    """
)

HEALTH_MODULE = (
    textwrap.dedent(
        """
        def check():
            return {"status": "healthy", "messages": ["all good"]}
        """
    ).strip()
)


def _make_pack(tmp_path: Path, main_source: str, *, entry: str = "main", **manifest_extra) -> Path:
    pack = tmp_path / "acme_pack"
    pack.mkdir()
    manifest = {
        "schema_version": 1,
        "id": "acme.pack",
        "name": "pack",
        "namespace": "acme",
        "version": "1.0.0",
        "api_version": "1.1.0",
        "entry_point": entry,
        "description": "worker test pack",
        "execution": {"mode": "worker", "startup_timeout_s": 5, "call_timeout_s": 5},
        "permissions": ["network"],
        "tools": [
            {"name": "doubler", "description": "double a number"},
            {"name": "boom", "description": "always raises"},
            {"name": "denied", "description": "needs network"},
        ],
    }
    manifest.update(manifest_extra)
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pack / f"{entry}.py").write_text(main_source, encoding="utf-8")
    return pack


class _Pipe:
    """进程内管道对（server 端只看到 in/out 两个流）。"""

    def __init__(self) -> None:
        self.server_in = io.BytesIO()
        self.server_out = io.BytesIO()

    def server_streams(self):
        return self.server_in, self.server_out

    # host 视角：向 server 写 = 读 server_in 追加；读 server 回帧。
    def host_send(self, obj: dict) -> None:
        pos = self.server_in.tell()
        self.server_in.seek(0, 2)
        self.server_in.write(encode_frame(obj))
        self.server_in.seek(pos)

    def host_recv(self) -> dict:
        self.server_out.seek(0)
        line = self.server_out.readline()
        if not line:
            raise AssertionError("no frame available")
        return decode_frame(line)

    def host_drain(self) -> list[dict]:
        self.server_out.seek(0)
        frames = [decode_frame(line) for line in self.server_out.readlines()]
        self.server_out = io.BytesIO()
        return frames


def _handshake(pack: Path, **overrides) -> dict:
    fingerprint, _ = compute_fingerprint(pack)
    return make_handshake(
        extension_id="acme.pack",
        fingerprint=fingerprint or "",
        grants=overrides.get("grants", []),
        settings={},
        expected_namespace="acme",
        expected_name="pack",
    )


def _boot(pack: Path, pipe: _Pipe, grants: list[str] | None = None) -> WorkerServer:
    server = WorkerServer(pack, *pipe.server_streams())
    pipe.host_send(_handshake(pack, grants=grants or []))
    assert server.handshake() is True
    return server


class TestProtocol:
    def test_frame_roundtrip(self):
        obj = {"type": "call", "id": "c1", "tool": "t", "args": {"x": 1}}
        assert decode_frame(encode_frame(obj)) == obj

    def test_frame_rejects_non_serializable(self):
        with pytest.raises(ProtocolError):
            encode_frame({"x": object()})

    def test_frame_rejects_oversize(self, monkeypatch):
        monkeypatch.setattr(
            "app.extensions_platform.worker.protocol.FRAME_MAX_BYTES", 64
        )
        with pytest.raises(ProtocolError):
            encode_frame({"blob": "x" * 128})

    def test_decode_rejects_bad_shape(self):
        with pytest.raises(ProtocolError):
            decode_frame(b"[1,2,3]\n")
        with pytest.raises(ProtocolError):
            decode_frame(b"not json\n")

    def test_error_payload_shape(self):
        assert error_payload("a", "b") == {"code": "a", "message": "b"}

    def test_protocol_version_pinned(self):
        assert WORKER_PROTOCOL_VERSION == "3.0"  # ADR-0119 V3 流式协议
        assert FRAME_MAX_BYTES >= 64 * 1024 * 1024


class TestHandshake:
    def test_handshake_ok_declares_tools(self, tmp_path):
        pack = _make_pack(tmp_path, TOOL_MAIN)
        pipe = _Pipe()
        _boot(pack, pipe, grants=["network"])
        frame = pipe.host_recv()
        assert frame["type"] == "handshake_ok"
        names = [t["name"] for t in frame["tools"]]
        assert names == ["acme_boom", "acme_denied", "acme_doubler"]
        doubler = next(t for t in frame["tools"] if t["name"] == "acme_doubler")
        assert doubler["kwargs"]["parameters"]["type"] == "object"

    def test_handshake_fails_on_fingerprint_mismatch(self, tmp_path):
        pack = _make_pack(tmp_path, TOOL_MAIN)
        pipe = _Pipe()
        server = WorkerServer(pack, *pipe.server_streams())
        bad = make_handshake(
            extension_id="acme.pack",
            fingerprint="0" * 64,
            grants=[],
            settings={},
            expected_namespace="acme",
            expected_name="pack",
        )
        pipe.host_send(bad)
        assert server.handshake() is False
        frame = pipe.host_recv()
        assert frame["type"] == "handshake_failed"
        assert frame["error"]["code"] == DiagnosticCode.PACKAGE_TAMPERED.value

    def test_handshake_fails_on_protocol_mismatch(self, tmp_path):
        pack = _make_pack(tmp_path, TOOL_MAIN)
        pipe = _Pipe()
        server = WorkerServer(pack, *pipe.server_streams())
        frame = _handshake(pack)
        frame["protocol"] = "0.9"
        pipe.host_send(frame)
        assert server.handshake() is False
        assert pipe.host_recv()["error"]["code"] == "worker_protocol_mismatch"

    def test_handshake_fails_on_id_mismatch(self, tmp_path):
        pack = _make_pack(tmp_path, TOOL_MAIN)
        pipe = _Pipe()
        server = WorkerServer(pack, *pipe.server_streams())
        frame = _handshake(pack)
        frame["extension_id"] = "acme.other"
        pipe.host_send(frame)
        assert server.handshake() is False
        assert pipe.host_recv()["error"]["code"] == "manifest_invalid"

    def test_handshake_fails_for_in_process_pack(self, tmp_path):
        pack = _make_pack(tmp_path, TOOL_MAIN)
        # 改回 in_process（合法 manifest，但 worker 拒绝服务）。
        data = json.loads((pack / "manifest.json").read_text())
        data["execution"] = {"mode": "in_process"}
        (pack / "manifest.json").write_text(json.dumps(data))
        pipe = _Pipe()
        server = WorkerServer(pack, *pipe.server_streams())
        pipe.host_send(_handshake(pack))
        assert server.handshake() is False
        assert pipe.host_recv()["error"]["code"] == "worker_mode_invalid"

    def test_handshake_fails_when_activate_raises(self, tmp_path):
        pack = _make_pack(
            tmp_path,
            "raise RuntimeError('activation boom')\n",
            tools=[],
        )
        pipe = _Pipe()
        server = WorkerServer(pack, *pipe.server_streams())
        pipe.host_send(_handshake(pack))
        assert server.handshake() is False
        frame = pipe.host_recv()
        assert frame["type"] == "handshake_failed"
        assert "activation boom" in frame["error"]["message"]


class TestCallLoop:
    def test_call_executes_and_returns_value(self, tmp_path):
        pack = _make_pack(tmp_path, TOOL_MAIN)
        pipe = _Pipe()
        server = WorkerServer(pack, *pipe.server_streams())
        server._ctx = _boot_ctx_only(pack, grants=[])
        pipe.host_send({"type": "call", "id": "c1", "tool": "acme_doubler", "args": {"x": 21}})
        assert server.serve_once() is True
        frame = pipe.host_recv()
        assert frame == {"type": "result", "id": "c1", "ok": True, "value": {"value": 42}}

    def test_call_tool_exception_typed_result(self, tmp_path):
        pack = _make_pack(tmp_path, TOOL_MAIN)
        pipe = _Pipe()
        server = WorkerServer(pack, *pipe.server_streams())
        server._ctx = _boot_ctx_only(pack, grants=[])
        pipe.host_send({"type": "call", "id": "c2", "tool": "acme_boom", "args": {}})
        server.serve_once()
        frame = pipe.host_recv()
        assert frame["ok"] is False
        assert frame["error"]["code"] == "entry_point_failed"
        assert "tool exploded" in frame["error"]["message"]

    def test_call_unknown_tool_typed(self, tmp_path):
        pack = _make_pack(tmp_path, TOOL_MAIN)
        pipe = _Pipe()
        server = WorkerServer(pack, *pipe.server_streams())
        server._ctx = _boot_ctx_only(pack, grants=[])
        pipe.host_send({"type": "call", "id": "c3", "tool": "acme_ghost", "args": {}})
        server.serve_once()
        frame = pipe.host_recv()
        assert frame["error"]["code"] == "declared_but_unregistered"

    def test_call_permission_denied_worker_side(self, tmp_path):
        pack = _make_pack(tmp_path, TOOL_MAIN)
        pipe = _Pipe()
        server = WorkerServer(pack, *pipe.server_streams())
        server._ctx = _boot_ctx_only(pack, grants=[])
        pipe.host_send({"type": "call", "id": "c4", "tool": "acme_denied", "args": {}})
        server.serve_once()
        frame = pipe.host_recv()
        assert frame["ok"] is False
        assert frame["error"]["code"] == "permission_not_granted"

    def test_shutdown_replies_bye(self, tmp_path):
        import pytest as _pytest

        pack = _make_pack(tmp_path, TOOL_MAIN)
        pipe = _Pipe()
        server = WorkerServer(pack, *pipe.server_streams())
        server._ctx = _boot_ctx_only(pack, grants=[])
        pipe.host_send({"type": "shutdown"})
        with _pytest.raises(SystemExit) as excinfo:
            server.serve_once()
        assert excinfo.value.code == 0
        frames = pipe.host_drain()
        assert frames[-1] == {"type": "bye"}

    def test_eof_ends_loop(self, tmp_path):
        pack = _make_pack(tmp_path, TOOL_MAIN)
        pipe = _Pipe()
        server = WorkerServer(pack, *pipe.server_streams())
        server._ctx = _boot_ctx_only(pack, grants=[])
        assert server.serve_once() is False  # EOF → False（退出循环）

    def test_pending_frames_processed_before_stream(self, tmp_path):
        pack = _make_pack(tmp_path, TOOL_MAIN)
        pipe = _Pipe()
        server = WorkerServer(pack, *pipe.server_streams())
        server._ctx = _boot_ctx_only(pack, grants=[])
        # pending 帧（如 broker 等待期间到达的宿主请求）先于流处理。
        server._pending.append({"type": "health", "id": "h9"})
        assert server.serve_once() is True
        frame = pipe.host_recv()
        assert frame["type"] == "result"
        assert frame["id"] == "h9"
        assert frame["value"]["status"] == "healthy"


def _boot_ctx_only(pack: Path, grants: list[str]):
    """握手后取出 server 的 ctx，供逐帧驱动 serve_once 的用例复用。"""
    from app.extensions_platform.permissions import grants_for
    from app.extensions_platform.trust import TrustLevel
    from app.extensions_platform.worker.context import WorkerContext
    from app.extensions_platform.discovery import _parse_manifest_file, MANIFEST_FILENAME
    from app.extensions_platform.loader import load_entry_module

    manifest, _, _ = _parse_manifest_file(pack / MANIFEST_FILENAME)
    fingerprint, _ = compute_fingerprint(pack)
    module = load_entry_module(
        pack, manifest.namespace, manifest.name, manifest.entry_point, fingerprint
    )
    ctx = WorkerContext(
        manifest=manifest,
        trust=TrustLevel.LOCAL_UNTRUSTED,
        grants=grants_for(manifest.id, {manifest.id: frozenset(grants)}),
        settings={},
        module_dir=pack,
        entry_module_name=module.__name__,
    )
    getattr(module, "activate")(ctx)
    return ctx


class TestResultSerialization:
    def test_non_serializable_result_is_typed_error_not_crash(self, tmp_path):
        main = textwrap.dedent(
            """
            from app.extensions_platform.sdk import ToolExtensionSpec


            def _weird() -> dict:
                return {"obj": object()}


            def _noop() -> dict:
                return {}


            def activate(ctx):
                for name, func in (("weird", _weird), ("noop", _noop)):
                    ctx.register_tool(ToolExtensionSpec(
                        name=name, description=name, func=func,
                        side_effect="pure", deterministic=True,
                        parameters={"type": "object", "properties": {}},
                    ))
            """
        )
        pack = _make_pack(tmp_path, main, tools=[
            {"name": "weird", "description": "w"},
            {"name": "noop", "description": "n"},
        ])
        pipe = _Pipe()
        server = WorkerServer(pack, *pipe.server_streams())
        server._ctx = _boot_ctx_only(pack, grants=[])
        pipe.host_send({"type": "call", "id": "w1", "tool": "acme_weird", "args": {}})
        server.serve_once()
        frame = pipe.host_recv()
        assert frame["ok"] is False
        assert frame["error"]["code"] == "worker_result_invalid"
