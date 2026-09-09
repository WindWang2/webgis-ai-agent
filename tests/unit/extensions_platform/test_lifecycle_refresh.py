"""生命周期 V2：投影变化 → 权威视图刷新（ADR-0105 Wave 9）。

证明 V1 known limitation「post-startup deactivate 后 runtime manifest 悬挂
旧值」已被消除：钩子在真实 ToolRegistry + 真实 compile_runtime_manifest
上断言指纹与工具计数随投影提交同步变化。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app.extensions_platform.diagnostics import has_errors
from app.extensions_platform.host import ExtensionHost, ExtensionState, HostPolicy
from app.extensions_platform.refresh import make_projection_refresher
from app.tools.registry import ToolRegistry

EXTENSION_ID = "acme.pack"

TOOL_MAIN = textwrap.dedent(
    """
    from app.extensions_platform.sdk import ToolExtensionSpec


    def _noop() -> dict:
        return {}


    def activate(ctx):
        ctx.register_tool(ToolExtensionSpec(
            name="noop", description="noop", func=_noop,
            side_effect="pure", deterministic=True,
            parameters={"type": "object", "properties": {}},
        ))
    """
)


def _make_pack(tmp_path: Path, *, mode: str = "in_process") -> Path:
    pack = tmp_path / "acme.pack.dir"
    pack.mkdir(exist_ok=True)
    manifest: dict = {
        "schema_version": 1,
        "id": EXTENSION_ID,
        "name": "pack",
        "namespace": "acme",
        "version": "1.0.0",
        "api_version": "1.1.0",
        "entry_point": "main",
        "description": "refresh pack",
        "tools": [{"name": "noop", "description": "noop"}],
    }
    if mode == "worker":
        manifest["execution"] = {"mode": "worker", "call_timeout_s": 10.0}
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pack / "main.py").write_text(TOOL_MAIN, encoding="utf-8")
    return pack


@pytest.fixture()
def manifest_view():
    """compile_runtime_manifest 的纯观察窗口（不依赖宿主单例缓存）。"""
    from app.lib.gis import runtime_manifest as rm

    def view(registry: ToolRegistry) -> dict:
        compiled = rm.compile_runtime_manifest(registry)
        summary = compiled.summary()
        return {
            "fingerprint": compiled.fingerprint,
            "tools": summary["counts"]["tools"],
        }

    return view


class TestProjectionRefreshHook:
    def test_deactivate_recompiles_manifest(self, tmp_path, manifest_view):
        _make_pack(tmp_path)
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,)),
        )
        events: list[tuple[str, str]] = []
        hook = make_projection_refresher(registry)
        host.set_projection_change_hook(lambda eid, event: (events.append((eid, event)), hook(eid, event)))
        host.discover()
        assert not has_errors(host.activate(EXTENSION_ID))
        with_tool = manifest_view(registry)
        assert registry.has("acme_noop")
        host.deactivate(EXTENSION_ID)
        without_tool = manifest_view(registry)
        assert not registry.has("acme_noop")
        assert with_tool["tools"] == without_tool["tools"] + 1
        assert with_tool["fingerprint"] != without_tool["fingerprint"]
        assert events == [(EXTENSION_ID, "activate"), (EXTENSION_ID, "deactivate")]

    def test_activation_failure_rollback_refreshes(self, tmp_path, manifest_view):
        pack = _make_pack(tmp_path)
        # 激活期崩溃：activate 抛异常 → 台账回滚。
        (pack / "main.py").write_text(
            TOOL_MAIN + "\n    raise RuntimeError('boom after register')\n",
            encoding="utf-8",
        )
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,)),
        )
        events: list[str] = []
        host.set_projection_change_hook(lambda eid, event: events.append(event))
        host.discover()
        host.activate(EXTENSION_ID)
        record = host.get_record(EXTENSION_ID)
        assert record is not None and record.state is ExtensionState.FAILED
        assert "rollback" in events
        assert not registry.has("acme_noop")
        baseline = manifest_view(registry)
        # 回滚后 manifest 与 baseline 一致（无扩展工具残留）。
        assert baseline["tools"] == manifest_view(registry)["tools"]

    def test_hook_exception_never_breaks_lifecycle(self, tmp_path):
        _make_pack(tmp_path)
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,)),
        )
        host.set_projection_change_hook(lambda eid, event: 1 / 0)
        host.discover()
        # 钩子抛异常不阻断激活/停用。
        assert not has_errors(host.activate(EXTENSION_ID))
        host.deactivate(EXTENSION_ID)
        assert host.get_record(EXTENSION_ID).state is ExtensionState.COMPATIBLE
        host.reset()

    def test_worker_mode_refresh_after_crash(self, tmp_path):
        """worker 崩溃 → 自动停用 → 钩子刷新（工具从权威视图消失）。"""
        pack = _make_pack(tmp_path, mode="worker")
        (pack / "main.py").write_text(
            textwrap.dedent(
                """
                import os

                from app.extensions_platform.sdk import ToolExtensionSpec


                def _crash() -> dict:
                    os._exit(70)


                def activate(ctx):
                    ctx.register_tool(ToolExtensionSpec(
                        name="crash", description="crash", func=_crash,
                        side_effect="pure", deterministic=True,
                        parameters={"type": "object", "properties": {}},
                    ))
                """
            ),
            encoding="utf-8",
        )
        pack_manifest = json.loads((pack / "manifest.json").read_text())
        pack_manifest["tools"] = [{"name": "crash", "description": "crash"}]
        (pack / "manifest.json").write_text(json.dumps(pack_manifest), encoding="utf-8")

        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,), max_worker_crashes=5),
        )
        events: list[str] = []
        host.set_projection_change_hook(lambda eid, event: events.append(event))
        host.discover()
        assert not has_errors(host.activate(EXTENSION_ID))
        assert registry.has("acme_crash")
        with pytest.raises(Exception):
            registry._tools["acme_crash"]()
        assert registry.has("acme_crash") is False
        assert "activate" in events and "deactivate" in events
        host.reset()

    def test_no_hook_is_noop(self, tmp_path):
        _make_pack(tmp_path)
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,)),
        )
        host.discover()
        assert not has_errors(host.activate(EXTENSION_ID))
        host.deactivate(EXTENSION_ID)
        host.reset()
