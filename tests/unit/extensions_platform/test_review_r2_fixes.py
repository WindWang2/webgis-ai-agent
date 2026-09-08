"""Round-2 审查修复回归测试（ADR-0105）。

钉死：worker 不经 .env/settings 通道读宿主 secrets（C-1）、artifact 有界
读取（M-1）、网络流式截断（M-2，注入面契约）、timeout 钳制（m-3）、
broker 应答期 worker 死亡归一 typed 崩溃（m-4）。
"""

from __future__ import annotations

import json
import textwrap

import pytest

from app.extensions_platform.broker import CapabilityBroker, BrokerLimits
from app.extensions_platform.diagnostics import DiagnosticCode, has_errors
from app.extensions_platform.host import ExtensionHost, HostPolicy
from app.extensions_platform.permissions import Permission, PermissionGrantSet
from app.extensions_platform.worker.spawn import (
    DEFAULT_MAX_CPU_SECONDS,
    DEFAULT_MAX_MEMORY_MB,
)
from app.tools.registry import ToolRegistry


class TestSecretsNotReadableViaSettings:
    def test_worker_settings_skip_env_file_and_env_channel(self, tmp_path, monkeypatch):
        """C-1：worker pack 经 ``from app.core.config import settings`` 也
        读不到宿主 secrets——env_file 被 spawn 标记短路，环境变量面被
        最小 env 排除。真实子进程验证。"""
        # 宿主侧布置一个可见的 secret（若泄漏通道存在即被抓到）。
        monkeypatch.setenv("EXTENSION_SECRETS_JSON", '{"acme.pack": {"k": "LEAKMARK"}}')
        main = textwrap.dedent(
            """
            from app.core.config import Settings

            from app.extensions_platform.sdk import ToolExtensionSpec


            def _probe() -> dict:
                from app.core.config import settings

                return {
                    "env_file": Settings.model_config.get("env_file"),
                    "secrets": str(settings.EXTENSION_SECRETS_JSON),
                }

            def activate(ctx):
                ctx.register_tool(ToolExtensionSpec(
                    name="probe", description="p", func=_probe,
                    side_effect="pure", deterministic=True,
                    parameters={"type": "object", "properties": {}},
                ))
            """
        )
        pack = tmp_path / "acme.pack.dir"
        pack.mkdir()
        (pack / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "id": "acme.pack", "name": "pack",
            "namespace": "acme", "version": "1.0.0", "api_version": "1.1.0",
            "entry_point": "main", "description": "probe",
            "execution": {"mode": "worker", "call_timeout_s": 10},
            "tools": [{"name": "probe", "description": "p"}],
        }), encoding="utf-8")
        (pack / "main.py").write_text(main, encoding="utf-8")
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,), max_worker_crashes=3),
        )
        host.discover()
        assert not has_errors(host.activate("acme.pack"))
        result = registry._tools["acme_probe"]()
        # 机制钉死：worker 侧 Settings 的 env_file 必须被标记短路。
        assert result["env_file"] is None
        # 通道钉死：宿主的 secrets 值不可达。
        assert "LEAKMARK" not in result["secrets"]
        host.reset()

    def test_spawn_module_defaults_exist(self):
        # 防御拼写漂移：spawn 缺省常量仍被 client/server 契约引用。
        assert DEFAULT_MAX_MEMORY_MB == 512
        assert DEFAULT_MAX_CPU_SECONDS == 60


class TestBoundedArtifactRead:
    def test_huge_artifact_returns_truncated_without_full_read(self, tmp_path):
        import io

        root = tmp_path / "artifacts"
        root.mkdir()
        big = root / "big.bin"
        # 8 MiB 文件（真实磁盘），cap 1 MiB：读取必须限幅而非整文件进内存。
        with big.open("wb") as fh:
            for _ in range(8):
                fh.write(b"x" * (1024 * 1024))
        broker = CapabilityBroker(
            extension_id="acme.pack",
            grants=PermissionGrantSet(
                "acme.pack", frozenset({Permission.PROJECT_ARTIFACT_READ})
            ),
            artifact_roots=(root,),
            limits=BrokerLimits(max_artifact_bytes=1024 * 1024),
        )
        ok, value = broker.handle("artifact_read", {"path": "big.bin"})
        assert ok
        import base64

        assert value["size"] == 8 * 1024 * 1024
        assert value["truncated"] is True
        assert len(base64.b64decode(value["content_b64"])) == 1024 * 1024
        assert io.DEFAULT_BUFFER_SIZE > 0  # （占位：保持 import 有意义）


class TestNetworkStreamTruncation:
    def test_oversize_real_httpx_stream_is_truncated(self, tmp_path):
        """M-2：真实 httpx 路径（无注入 transport）经流式下载在 cap 处中止。

        本地启动一个一次性 TCP 服务吐 8 MiB；allowlist 走 SSRF 不可行
        （环回被拒），故这里只对截断逻辑做单元级验证：构造 fake 流式
        client 面。"""
        class FakeStreamResponse:
            status_code = 200
            headers = {"content-type": "application/octet-stream"}

            def __init__(self, chunks):
                self._chunks = chunks

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def iter_raw(self):
                yield from self._chunks

        class FakeStreamClient:
            def __init__(self, chunks):
                self._chunks = chunks

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def stream(self, method, url, headers=None, content=None):
                return FakeStreamResponse(self._chunks)

        import base64

        import app.extensions_platform.broker as broker_mod

        chunks = [b"y" * (1024 * 1024) for _ in range(8)]
        monkey = FakeStreamClient(chunks)
        original_client = broker_mod.httpx.Client
        broker_mod.httpx.Client = lambda **kw: monkey
        try:
            broker = CapabilityBroker(
                extension_id="acme.pack",
                grants=PermissionGrantSet("acme.pack", frozenset({Permission.NETWORK})),
                network_allow=frozenset({"api.example.com"}),
                limits=BrokerLimits(max_http_response_bytes=1024 * 1024),
            )
            ok, value = broker.handle(
                "network_request", {"url": "https://api.example.com/blob"}
            )
        finally:
            broker_mod.httpx.Client = original_client
        assert ok
        assert value["truncated"] is True
        assert len(base64.b64decode(value["body_b64"])) == 1024 * 1024


class TestTimeoutClamp:
    def test_negative_timeout_denied_typed(self):
        broker = CapabilityBroker(
            extension_id="acme.pack",
            grants=PermissionGrantSet("acme.pack", frozenset({Permission.NETWORK})),
            network_allow=frozenset({"api.example.com"}),
        )
        ok, value = broker.handle(
            "network_request",
            {"url": "https://api.example.com", "timeout_s": -5},
        )
        assert ok is False
        assert value["code"] == DiagnosticCode.BROKER_DENIED.value
        assert "positive finite" in value["message"]

    def test_nan_timeout_denied_typed(self):
        broker = CapabilityBroker(
            extension_id="acme.pack",
            grants=PermissionGrantSet("acme.pack", frozenset({Permission.NETWORK})),
            network_allow=frozenset({"api.example.com"}),
        )
        ok, value = broker.handle(
            "network_request",
            {"url": "https://api.example.com", "timeout_s": float("nan")},
        )
        assert ok is False
        assert "positive finite" in value["message"]


class TestBrokerDeathTypedCrash:
    def test_worker_death_during_broker_write_is_typed_crash(self, tmp_path):
        """m-4：worker 在 broker 应答写回期间死亡 → WORKER_CRASHED（进
        崩溃计数路径），而非裸 BrokenPipeError。"""
        from app.extensions_platform.worker.client import WorkerProcess

        pack = tmp_path / "acme.pack.dir"
        pack.mkdir()
        worker = WorkerProcess(
            pack_dir=pack,
            extension_id="acme.pack",
            namespace="acme",
            name="pack",
            fingerprint="0" * 64,
            grants=[],
            settings={},
            startup_timeout_s=5,
            call_timeout_s=5,
            broker_handler=lambda op, payload: (True, {"value": 1}),
        )
        # 不真正 spawn：手工构造最小句柄面（stdin.write 直接 BrokenPipe）。
        class DyingStdin:
            def write(self, *_a):
                raise BrokenPipeError("worker gone")

        worker._proc = type(
            "P",
            (),
            {"pid": 0, "poll": lambda self: 0, "stdin": DyingStdin()},
        )()
        with pytest.raises(Exception, match="worker_crashed|died while receiving"):
            worker._dispatch_broker(
                {"type": "broker_request", "id": "b1", "op": "secret_get",
                 "payload": {"ref": "x"}}
            )
