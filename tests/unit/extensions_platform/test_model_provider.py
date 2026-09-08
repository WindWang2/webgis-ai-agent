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

from app.extensions_platform.diagnostics import has_errors
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
PACK_SOURCE = Path(__file__).resolve().parents[3] / "extensions" / "examples" / "extdemo-ml-pack"


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
            {"text": "a b a c a", "top_k": 2}
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
        result = registry._tools["extdemoml_wordfreq_invoke"]({"text": "q"})
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
            registry._tools["extdemoml_wordfreq_invoke"]({"text": "q"})
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
            policy=HostPolicy(roots=(tmp_path,), max_worker_crashes=5),
        )
        host.discover()
        diags = host.activate("workerml.pack")
        assert not has_errors(diags), [d.message for d in diags]
        assert registry.has("workerml_echo_invoke")
        result = registry._tools["workerml_echo_invoke"](request={"text": "ping"})
        # dict 形态的 invoke 结果原样透传（聚合器仅包装迭代器形态）。
        assert result == {"type": "final", "echo": "ping"}
        # worker 模式流式 → typed 拒绝。
        with pytest.raises(Exception, match="streaming is unavailable"):
            host.invoke_model_provider("workerml_echo_invoke", {}, stream=True)
        host.reset()

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


