"""Round-1 审查修复回归测试（ADR-0105）。

钉死：worker 崩溃隔离不挂宿主线程（CRITICAL-1）、invoke 工具扁平 kwargs
派发约定（CRITICAL-2）、worker 启动 OSError 兜底（MAJOR-1）、计数连续语义
（MINOR-1）、disable 被拒中止（MINOR-2）、allowlist 端口规约（MINOR-5）、
跨节投影名碰撞（MINOR-6）。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app.extensions_platform.diagnostics import DiagnosticCode, has_errors
from app.extensions_platform.host import ExtensionHost, ExtensionState, HostPolicy
from app.extensions_platform.manifest import GisExtensionManifest
from app.extensions_platform.settings_bridge import parse_network_allow
from app.tools.registry import ToolRegistry


def _make_worker_pack(
    tmp_path: Path,
    main_source: str,
    *,
    tools: list[dict],
    ext_id: str = "acme.pack",
    call_timeout_s: float = 10.0,
) -> Path:
    ns, name = ext_id.split(".")
    pack = tmp_path / f"{ext_id}.dir"
    pack.mkdir(exist_ok=True)
    manifest = {
        "schema_version": 1,
        "id": ext_id,
        "name": name,
        "namespace": ns,
        "version": "1.0.0",
        "api_version": "1.1.0",
        "entry_point": "main",
        "description": "review fix pack",
        "execution": {"mode": "worker", "call_timeout_s": call_timeout_s},
        "tools": tools,
    }
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pack / "main.py").write_text(main_source, encoding="utf-8")
    return pack


class TestCritical1StdoutCloseHang:
    def test_stdout_close_survivor_produces_typed_crash(self, tmp_path):
        """关掉 stdout 但存活的 worker：宿主必须有界地产出 typed 崩溃，
        而不是在 stderr 抽取上永久挂起。"""
        main = textwrap.dedent(
            """
            import os
            import time

            from app.extensions_platform.sdk import ToolExtensionSpec


            def _hang() -> dict:
                os.close(1)  # 关 stdout：宿主读到 EOF
                time.sleep(300)  # 存活并握住 stderr
                return {}

            def activate(ctx):
                ctx.register_tool(ToolExtensionSpec(
                    name="hang", description="hang", func=_hang,
                    side_effect="pure", deterministic=True,
                    parameters={"type": "object", "properties": {}},
                ))
            """
        )
        _make_worker_pack(tmp_path, main, tools=[{"name": "hang", "description": "h"}])
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,), max_worker_crashes=9),
        )
        host.discover()
        assert not has_errors(host.activate("acme.pack"))
        # 若回归为无界 read，此调用将挂起直至 pytest timeout(60s)。
        with pytest.raises(Exception, match="worker_crashed"):
            registry._tools["acme_hang"]()
        host.reset()


class TestCritical2FlatKwargsDispatch:
    def test_in_process_flat_kwargs_reach_invoke(self, tmp_path):
        main = textwrap.dedent(
            """
            from app.extensions_platform.sdk import ModelProviderSpec


            def _echo(request, ctx):
                return {"type": "final", "echo": request.get("text", "")}

            def activate(ctx):
                ctx.register_model_provider(ModelProviderSpec(
                    provider_id="echo", description="x", invoke_fn=_echo,
                    capabilities=["cancellation"],
                ))
            """
        )
        _make_worker_pack(
            tmp_path,
            main,
            tools=[],
            ext_id="acme.pack",
        )
        data = json.loads((tmp_path / "acme.pack.dir" / "manifest.json").read_text())
        data.pop("execution")
        data.pop("tools")
        data["permissions"] = ["model_provider"]
        data["model_providers"] = [
            {"id": "echo", "description": "x", "capabilities": ["cancellation"]}
        ]
        (tmp_path / "acme.pack.dir" / "manifest.json").write_text(json.dumps(data))
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,), builtin_ids=frozenset({"acme.pack"})),
        )
        host.discover()
        assert not has_errors(host.activate("acme.pack"))
        # 真实派发约定：**扁平 kwargs（即 schema 顶层属性）。
        result = registry._tools["acme_echo_invoke"](text="hello")
        assert result == {"type": "final", "echo": "hello"}
        # 包膜形态依旧兼容。
        result2 = registry._tools["acme_echo_invoke"](request={"text": "wrapped"})
        assert result2 == {"type": "final", "echo": "wrapped"}
        host.reset()

    def test_worker_flat_kwargs_dispatch(self, tmp_path):
        main = textwrap.dedent(
            """
            from app.extensions_platform.sdk import ToolExtensionSpec


            def _shout(word: str = "") -> dict:
                return {"shout": word.upper()}

            def activate(ctx):
                ctx.register_tool(ToolExtensionSpec(
                    name="shout", description="s", func=_shout,
                    side_effect="pure", deterministic=True,
                    parameters={"type": "object",
                                "properties": {"word": {"type": "string"}}},
                ))
            """
        )
        _make_worker_pack(tmp_path, main, tools=[{"name": "shout", "description": "s"}])
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,), max_worker_crashes=9),
        )
        host.discover()
        assert not has_errors(host.activate("acme.pack"))
        assert registry._tools["acme_shout"](word="hey") == {"shout": "HEY"}
        host.reset()


class TestMajor1StartupOSError:
    def test_start_oserror_fails_closed_not_stuck_loading(self, tmp_path, monkeypatch):
        _make_worker_pack(
            tmp_path,
            "def activate(ctx):\n    return None\n",
            tools=[],
        )
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,)),
        )
        host.discover()

        def _boom(*_a, **_kw):
            raise OSError("EMFILE: too many open files")

        monkeypatch.setattr(
            "app.extensions_platform.worker.client.WorkerProcess.start", _boom
        )
        diags = host.activate("acme.pack")
        record = host.get_record("acme.pack")
        assert record is not None and record.state is ExtensionState.FAILED
        assert any("worker startup failed" in d.message for d in diags)
        # 重试不再撞 AssertionError。
        diags2 = host.activate("acme.pack")
        assert record.state is ExtensionState.FAILED
        assert isinstance(diags2, list)
        host.reset()


class TestMinor1CrashCountConsecutive:
    def test_crash_count_survives_reactivation_and_resets_on_graceful(self, tmp_path):
        main = textwrap.dedent(
            """
            import os

            from app.extensions_platform.sdk import ToolExtensionSpec


            def _crash() -> dict:
                os._exit(70)

            def _noop() -> dict:
                return {}

            def activate(ctx):
                for name, func in (("crash", _crash), ("noop", _noop)):
                    ctx.register_tool(ToolExtensionSpec(
                        name=name, description=name, func=func,
                        side_effect="pure", deterministic=True,
                        parameters={"type": "object", "properties": {}},
                    ))
            """
        )
        _make_worker_pack(
            tmp_path, main, tools=[{"name": "crash", "description": "c"},
                                   {"name": "noop", "description": "n"}]
        )
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,), max_worker_crashes=2),
        )
        host.discover()
        assert not has_errors(host.activate("acme.pack"))
        with pytest.raises(Exception):
            registry._tools["acme_crash"]()
        record = host.get_record("acme.pack")
        assert record is not None and record.worker_crash_count == 1
        # 重激活不清零（崩溃 → 崩溃仍可达 quarantine）。
        assert not has_errors(host.activate("acme.pack"))
        assert record.worker_crash_count == 1
        # 优雅 deactivate 清零（「连续」= 跨越一次干净关停才中断）。
        host.deactivate("acme.pack")
        assert record.worker_crash_count == 0
        host.reset()


class TestMinor2DisableRefusedKeepsActive:
    def test_disable_with_active_dependent_aborts(self, tmp_path):
        # base + consumer(consumer 依赖 base)：disable(base) 因依赖者活跃被拒。
        base = tmp_path / "basepack.base.dir"
        base.mkdir()
        (base / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "id": "basepack.base", "name": "base",
            "namespace": "basepack", "version": "1.0.0", "api_version": "1.0.0",
            "entry_point": "main", "description": "b",
        }), encoding="utf-8")
        (base / "main.py").write_text("def activate(ctx):\n    return None\n", encoding="utf-8")
        consumer = tmp_path / "userpack.consumer.dir"
        consumer.mkdir()
        (consumer / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "id": "userpack.consumer", "name": "consumer",
            "namespace": "userpack", "version": "1.0.0", "api_version": "1.0.0",
            "entry_point": "main", "description": "c",
            "dependencies": [{"id": "basepack.base"}],
        }), encoding="utf-8")
        (consumer / "main.py").write_text("def activate(ctx):\n    return None\n", encoding="utf-8")
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,), allow_local_untrusted_activation=True),
        )
        host.discover()
        results = host.activate_all()
        assert all(not has_errors(v) for v in results.values())
        diags = host.disable("basepack.base")
        record = host.get_record("basepack.base")
        assert record is not None and record.state is ExtensionState.ACTIVE
        assert any(d.code is DiagnosticCode.DEPENDENT_ACTIVE for d in diags)
        host.reset()


class TestMinor5NetworkAllowPortNormalization:
    def test_host_port_entry_normalized_to_host(self):
        parsed = parse_network_allow("acme.pack:api.example.com:8443")
        assert parsed["acme.pack"] == frozenset({"api.example.com"})


class TestMinor6CrossSectionCollision:
    def test_tool_named_pid_invoke_rejected(self):
        with pytest.raises(Exception, match="collide"):
            GisExtensionManifest.model_validate(
                {
                    "schema_version": 1,
                    "id": "acme.pack",
                    "name": "pack",
                    "namespace": "acme",
                    "version": "1.0.0",
                    "api_version": "1.1.0",
                    "entry_point": "main",
                    "tools": [{"name": "echo_invoke", "description": "x"}],
                    "model_providers": [{"id": "echo", "description": "x"}],
                }
            )
