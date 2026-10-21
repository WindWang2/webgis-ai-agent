"""model_provider 扩展类型端到端测试（ADR-0105 Wave 10）。

生产路径：ModelProviderSpec → 类型化调用工具投影 → ToolRegistry 真实
派发；流式经 host.invoke_model_provider（协作式取消 = 提前 close）；
worker 模式 = 单帧聚合。同时覆盖 SDK 扩展 provider 协议 mixin。
"""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import pytest

from app.extensions_platform.diagnostics import (
    DiagnosticCode,
    ExtensionPlatformError,
    has_errors,
)
from app.extensions_platform.host import ExtensionHost, ExtensionState, HostPolicy
from app.extensions_platform.sdk import (
    RasterWindowProvider,
    StreamingVectorProvider,
    TilePayload,
    TileProvider,
    extended_provider_capabilities,
)
from app.tools.registry import ToolRegistry

EXTENSION_ID = "extdemoml.model"
PACK_SOURCE = Path(__file__).resolve().parents[3] / "examples" / "extensions" / "extdemo-ml-pack"


@pytest.fixture()
def ml_host(tmp_path: Path):
    target = tmp_path / "extdemo-ml-pack"
    shutil.copytree(PACK_SOURCE, target)
    registry = ToolRegistry()
    host = ExtensionHost(
        tool_registry=registry,
        policy=HostPolicy(
            roots=(tmp_path,),
            builtin_ids=frozenset({EXTENSION_ID}),
            grants={EXTENSION_ID: frozenset({"model_provider"})},
            secrets={EXTENSION_ID: {"demo_key": "demo-123456"}},
        ),
    )
    host.discover()
    diags = host.activate(EXTENSION_ID)
    record = host.get_record(EXTENSION_ID)
    assert record is not None and record.state is ExtensionState.ACTIVE, [
        d.message for d in diags
    ]
    try:
        yield host, registry
    finally:
        host.reset()


class TestModelProviderProjection:
    def test_invoke_tool_projected_and_dispatchable(self, ml_host):
        host, registry = ml_host
        assert registry.has("extdemoml_wordfreq_invoke")
        result = registry._tools["extdemoml_wordfreq_invoke"](
            text="a b a c a", top_k=2
        )
        assert result["final"]["total_tokens"] == 5
        assert result["final"]["top_k"] == [{"token": "a", "count": 3}, {"token": "b", "count": 1}]
        token_events = [e for e in result["events"] if e["type"] == "token"]
        assert len(token_events) == 5

    def test_streaming_invocation_with_cancellation(self, ml_host):
        host, _ = ml_host
        stream = host.invoke_model_provider(
            "extdemoml_wordfreq_invoke", {"text": "x y z", "top_k": 1}, stream=True
        )
        first = next(iter(stream))
        assert first["type"] == "token"
        stream.close()  # 协作式取消：调用方提前 close 不炸宿主

    def test_secret_provisioned_and_never_echoed(self, ml_host):
        host, registry = ml_host
        result = registry._tools["extdemoml_wordfreq_invoke"](text="q")
        # 凭据值不进入结果面（只参与 nonce 派生）。
        assert "demo-123456" not in str(result)

    def test_unprovisioned_secret_typed_failure(self, tmp_path):
        target = tmp_path / "extdemo-ml-pack"
        shutil.copytree(PACK_SOURCE, target)
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(
                roots=(tmp_path,),
                builtin_ids=frozenset({EXTENSION_ID}),
                grants={EXTENSION_ID: frozenset({"model_provider"})},
            ),
        )
        host.discover()
        assert not has_errors(host.activate(EXTENSION_ID))
        with pytest.raises(Exception, match="not provisioned"):
            registry._tools["extdemoml_wordfreq_invoke"](text="q")
        host.reset()

    def test_inventory_reflects_declaration(self, ml_host):
        host, _ = ml_host
        inventory = host.model_provider_inventory()
        assert len(inventory) == 1
        entry = inventory[0]
        assert entry["provider_id"] == "wordfreq"
        assert entry["tool"] == "extdemoml_wordfreq_invoke"
        assert entry["capabilities"] == ["cancellation", "streaming"]
        assert entry["registered"] is True
        assert entry["execution"] == "in_process"

    def test_invoke_unknown_provider_typed(self, ml_host):
        host, _ = ml_host
        with pytest.raises(Exception, match="no active model provider"):
            host.invoke_model_provider("extdemoml_ghost_invoke", {})


class TestWorkerModeModelProvider:
    def test_worker_provider_invoke_single_frame(self, tmp_path):
        pack = tmp_path / "workerml.pack.dir"
        pack.mkdir()
        manifest = {
            "schema_version": 1,
            "id": "workerml.pack",
            "name": "pack",
            "namespace": "workerml",
            "version": "1.0.0",
            "api_version": "1.1.0",
            "entry_point": "main",
            "description": "worker model provider",
            "execution": {"mode": "worker", "call_timeout_s": 10},
            "permissions": ["model_provider"],
            "model_providers": [
                {
                    "id": "echo",
                    "description": "echo model (worker, no streaming)",
                    "capabilities": ["cancellation"],
                }
            ],
        }
        (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (pack / "main.py").write_text(
            textwrap.dedent(
                """
                from app.extensions_platform.sdk import ModelProviderSpec


                def _echo(request, ctx):
                    return {
                        "type": "final",
                        "echo": request.get("text", ""),
                    }


                def activate(ctx):
                    ctx.register_model_provider(ModelProviderSpec(
                        provider_id="echo",
                        description="echo model",
                        invoke_fn=_echo,
                        capabilities=["cancellation"],
                    ))
                """
            ),
            encoding="utf-8",
        )
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(
                roots=(tmp_path,),
                max_worker_crashes=5,
                # #1709：manifest 声明 != 授权。未授予 model_provider 时调用
                # 被 typed 拒绝（见 TestModelProviderPermissionEnforced）。
                grants={"workerml.pack": frozenset({"model_provider"})},
            ),
        )
        host.discover()
        diags = host.activate("workerml.pack")
        assert not has_errors(diags), [d.message for d in diags]
        assert registry.has("workerml_echo_invoke")
        result = registry._tools["workerml_echo_invoke"](request={"text": "ping"})
        # dict 形态的 invoke 结果原样透传（聚合器仅包装迭代器形态）。
        assert result == {"type": "final", "echo": "ping"}
        # V3（ADR-0119）：worker 流式经协议 3.0 可用；事件迭代聚合为流帧。
        events = list(
            host.invoke_model_provider("workerml_echo_invoke", {"text": "hi"}, stream=True)
        )
        assert events and events[-1].get("type") == "final"
        host.reset()

        # 协议 < 3.0 的 worker（模拟）→ 流式仍 typed 拒绝（运行期门控）。
        host2 = ExtensionHost(
            tool_registry=ToolRegistry(),
            policy=HostPolicy(roots=(tmp_path,), max_worker_crashes=5),
        )
        host2.discover()
        diags2 = host2.activate("workerml.pack")
        assert not has_errors(diags2), [d.message for d in diags2]
        record2 = host2.get_record("workerml.pack")
        assert record2 is not None
        record2.worker = type(
            "FakeWorker",
            (),
            {
                "protocol_version": "1.0",
                "call_stream": lambda *a, **k: iter(()),
                "kill": lambda self: None,
                "shutdown": lambda self, grace_s=3.0: None,
            },
        )()
        with pytest.raises(Exception, match="streaming.*requires protocol 3.0"):
            host2.invoke_model_provider("workerml_echo_invoke", {}, stream=True)
        host2.reset()

    def test_worker_streaming_capability_rejected_at_manifest(self):
        from app.extensions_platform.manifest import GisExtensionManifest

        with pytest.raises(Exception, match="streaming"):
            GisExtensionManifest.model_validate(
                {
                    "schema_version": 1,
                    "id": "ww.pack",
                    "name": "pack",
                    "namespace": "ww",
                    "version": "1.0.0",
                    "api_version": "1.1.0",
                    "entry_point": "main",
                    "execution": {"mode": "worker"},
                    "model_providers": [
                        {"id": "mm", "description": "x", "capabilities": ["streaming"]}
                    ],
                }
            )


def _write_worker_ml_pack(tmp_path: Path) -> Path:
    """worker 模式 model provider 包（单帧聚合；manifest 声明 model_provider）。"""
    pack = tmp_path / "workerml.pack.dir"
    pack.mkdir()
    manifest = {
        "schema_version": 1,
        "id": "workerml.pack",
        "name": "pack",
        "namespace": "workerml",
        "version": "1.0.0",
        "api_version": "1.1.0",
        "entry_point": "main",
        "description": "worker model provider",
        "execution": {"mode": "worker", "call_timeout_s": 10},
        "permissions": ["model_provider"],
        "model_providers": [
            {
                "id": "echo",
                "description": "echo model (worker, no streaming)",
                "capabilities": ["cancellation"],
            }
        ],
    }
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pack / "main.py").write_text(
        textwrap.dedent(
            """
            from app.extensions_platform.sdk import ModelProviderSpec


            def _echo(request, ctx):
                return {
                    "type": "final",
                    "echo": request.get("text", ""),
                }


            def activate(ctx):
                ctx.register_model_provider(ModelProviderSpec(
                    provider_id="echo",
                    description="echo model",
                    invoke_fn=_echo,
                    capabilities=["cancellation"],
                ))
            """
        ),
        encoding="utf-8",
    )
    return pack


class TestModelProviderPermissionEnforced:
    """#1709：MODEL_PROVIDER 授权不是死权限——两条执行路径都必须在调用期强制。

    manifest ``permissions`` 只是**声明**（意图）；授权是运维决定
    （``HostPolicy.grants`` / ``EXTENSION_PERMISSION_GRANTS``），默认全无。
    """

    def test_in_process_dispatch_allowed_when_granted(self, ml_host):
        host, registry = ml_host
        # 正向钉：fixture 授予 model_provider → 派发照常工作（不因收紧而误伤）。
        assert registry._tools["extdemoml_wordfreq_invoke"](text="a b a", top_k=1)[
            "final"
        ]["total_tokens"] == 3

    def test_in_process_dispatch_denied_without_grant(self, tmp_path):
        target = tmp_path / "extdemo-ml-pack"
        shutil.copytree(PACK_SOURCE, target)
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(
                roots=(tmp_path,),
                builtin_ids=frozenset({EXTENSION_ID}),
                secrets={EXTENSION_ID: {"demo_key": "demo-123456"}},
            ),
        )
        host.discover()
        assert not has_errors(host.activate(EXTENSION_ID))
        try:
            with pytest.raises(ExtensionPlatformError) as excinfo:
                registry._tools["extdemoml_wordfreq_invoke"](text="a b")
            assert excinfo.value.diagnostic.code is DiagnosticCode.PERMISSION_NOT_GRANTED
            assert "model_provider" in excinfo.value.diagnostic.message
        finally:
            host.reset()

    def test_in_process_host_entry_denied_without_grant(self, tmp_path):
        # #1709：host.invoke_model_provider（modelops ExtensionProviderAdapter
        # 的生产入口）此前直连 spec.invoke_fn，绕过注册期权限包裹——in-process
        # 模式下授权可被该入口旁路。流式/非流式两个形态都必须 typed 拒绝。
        target = tmp_path / "extdemo-ml-pack"
        shutil.copytree(PACK_SOURCE, target)
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(
                roots=(tmp_path,),
                builtin_ids=frozenset({EXTENSION_ID}),
                secrets={EXTENSION_ID: {"demo_key": "demo-123456"}},
            ),
        )
        host.discover()
        assert not has_errors(host.activate(EXTENSION_ID))
        try:
            for stream in (False, True):
                with pytest.raises(ExtensionPlatformError) as excinfo:
                    host.invoke_model_provider(
                        "extdemoml_wordfreq_invoke",
                        {"text": "a b"},
                        stream=stream,
                    )
                assert (
                    excinfo.value.diagnostic.code
                    is DiagnosticCode.PERMISSION_NOT_GRANTED
                )
                assert "model_provider" in excinfo.value.diagnostic.message
        finally:
            host.reset()

    def test_worker_invoke_denied_without_grant(self, tmp_path):
        _write_worker_ml_pack(tmp_path)
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,), max_worker_crashes=5),
        )
        host.discover()
        assert not has_errors(host.activate("workerml.pack"))
        assert registry.has("workerml_echo_invoke")
        try:
            with pytest.raises(ExtensionPlatformError) as excinfo:
                registry._tools["workerml_echo_invoke"](request={"text": "ping"})
            assert excinfo.value.diagnostic.code is DiagnosticCode.PERMISSION_NOT_GRANTED
        finally:
            host.reset()

    def test_worker_invoke_allowed_when_granted(self, tmp_path):
        _write_worker_ml_pack(tmp_path)
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(
                roots=(tmp_path,),
                max_worker_crashes=5,
                grants={"workerml.pack": frozenset({"model_provider"})},
            ),
        )
        host.discover()
        assert not has_errors(host.activate("workerml.pack"))
        try:
            result = registry._tools["workerml_echo_invoke"](request={"text": "ping"})
            assert result == {"type": "final", "echo": "ping"}
        finally:
            host.reset()

    def test_manifest_declaration_alone_is_not_authorization(self):
        """声明与授权分离的正向契约钉（permissions.py 模块 docstring 的不变式）。"""
        from app.extensions_platform.permissions import grants_for

        assert grants_for("workerml.pack", {}).granted == frozenset()
        assert grants_for(
            "workerml.pack", {"workerml.pack": frozenset({"network"})}
        ).allows("model_provider") is False


class TestExtendedProviderProtocols:
    def test_capability_detection_class_and_instance(self):
        class Full(StreamingVectorProvider, TileProvider, RasterWindowProvider):
            pass

        class Partial(TileProvider):
            pass

        assert extended_provider_capabilities(Full) == [
            "streaming_vector",
            "tiles",
            "raster_window",
        ]
        assert extended_provider_capabilities(Partial()) == ["tiles"]
        assert extended_provider_capabilities(object) == []

    def test_tile_payload_defaults(self):
        payload = TilePayload(data=b"abc")
        assert payload.content_type == "image/png"
        assert payload.extent is None


