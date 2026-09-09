"""Worker 隔离执行端到端测试（ADR-0105 Wave 3）。

真实子进程：spawn `python -m app.extensions_platform.worker.server`，经
ExtensionHost 全生命周期驱动（激活 → 工具调用 → 崩溃隔离 → 超时 kill →
停用 → 重新激活 → 隔离）。产出 typed 诊断与状态断言。

诚实边界（同时被本套件钉进契约）：worker 内的 Python 代码仍以服务用户
身份运行（os._exit 等系统调用无法被平台拦截）——crash → 回滚 →
quarantine 是平台语义，不是内存安全 sandbox。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app.extensions_platform.diagnostics import DiagnosticCode, has_errors
from app.extensions_platform.host import ExtensionHost, ExtensionState, HostPolicy
from app.tools.registry import ToolRegistry

EXTENSION_ID = "acme.pack"

WORKER_MAIN = textwrap.dedent(
    """
    import os

    from app.extensions_platform.sdk import ToolExtensionSpec


    def _double(x: float) -> dict:
        return {"value": x * 2}


    def _crash() -> dict:
        os._exit(70)


    def _sleep(seconds: float) -> dict:
        import time

        time.sleep(seconds)
        return {"slept": seconds}


    def _import_secret_env() -> dict:
        return {"has_secret": bool(os.environ.get("WORKER_TEST_SECRET"))}


    def activate(ctx):
        for name, func, params in (
            ("double", _double, {"type": "object", "properties": {"x": {"type": "number"}},
                                 "required": ["x"]}),
            ("crash", _crash, {"type": "object", "properties": {}}),
            ("sleep", _sleep, {"type": "object", "properties": {"seconds": {"type": "number"}},
                               "required": ["seconds"]}),
            ("import_secret_env", _import_secret_env, {"type": "object", "properties": {}}),
        ):
            ctx.register_tool(ToolExtensionSpec(
                name=name, description=name, func=func,
                side_effect="pure", deterministic=True, parameters=params,
            ))
    """
)

HEALTHY_MAIN = textwrap.dedent(
    """
    from app.extensions_platform.sdk import ToolExtensionSpec


    def check():
        return {"status": "healthy", "messages": ["worker ok"]}


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


def _make_pack(
    tmp_path: Path,
    main_source: str,
    *,
    call_timeout_s: float = 10.0,
    startup_timeout_s: float = 15.0,
    entry: str = "main",
    tools: list[dict] | None = None,
    diagnostics_entry: str | None = None,
) -> Path:
    pack = tmp_path / "acme.pack.dir"
    pack.mkdir(exist_ok=True)
    manifest: dict = {
        "schema_version": 1,
        "id": EXTENSION_ID,
        "name": "pack",
        "namespace": "acme",
        "version": "1.0.0",
        "api_version": "1.1.0",
        "entry_point": entry,
        "description": "worker integration pack",
        "execution": {
            "mode": "worker",
            "startup_timeout_s": startup_timeout_s,
            "call_timeout_s": call_timeout_s,
        },
        "tools": tools
        or [
            {"name": "double", "description": "double"},
            {"name": "crash", "description": "crash"},
            {"name": "sleep", "description": "sleep"},
            {"name": "import_secret_env", "description": "env probe"},
        ],
    }
    if diagnostics_entry:
        manifest["diagnostics_entry"] = diagnostics_entry
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pack / f"{entry}.py").write_text(main_source, encoding="utf-8")
    return pack


def _make_host(pack_root: Path, max_worker_crashes: int = 2) -> tuple[ExtensionHost, ToolRegistry]:
    registry = ToolRegistry()
    host = ExtensionHost(
        tool_registry=registry,
        policy=HostPolicy(roots=(pack_root,), max_worker_crashes=max_worker_crashes),
    )
    host.discover()
    return host, registry


def _raw_tool(registry: ToolRegistry, name: str):
    """绕过 dispatch 包装拿到原始代理函数（typed 异常不被 dispatch 归一）。"""
    return registry._tools[name]


class TestWorkerLifecycle:
    def test_activate_call_deactivate_roundtrip(self, tmp_path):
        _make_pack(tmp_path, WORKER_MAIN)
        host, registry = _make_host(tmp_path)
        diags = host.activate(EXTENSION_ID)
        assert not has_errors(diags), [d.message for d in diags]
        record = host.get_record(EXTENSION_ID)
        assert record is not None and record.state is ExtensionState.ACTIVE
        assert record.worker is not None and record.worker.alive
        assert registry.has("acme_double")
        # 原始代理 → worker 进程执行 → 结果跨进程返回。
        assert _raw_tool(registry, "acme_double")(x=21) == {"value": 42}
        # 停用：worker 退出、投影零残留。
        host.deactivate(EXTENSION_ID)
        assert record.worker is None
        assert not registry.has("acme_double")
        host.reset()

    def test_tool_dispatch_through_registry(self, tmp_path):
        _make_pack(tmp_path, WORKER_MAIN)
        host, registry = _make_host(tmp_path)
        assert not has_errors(host.activate(EXTENSION_ID))
        result = _raw_tool(registry, "acme_double")(x=3)
        assert result == {"value": 6}
        host.reset()

    def test_worker_crash_rolls_back_then_quarantines(self, tmp_path):
        _make_pack(tmp_path, WORKER_MAIN)
        host, registry = _make_host(tmp_path, max_worker_crashes=2)
        assert not has_errors(host.activate(EXTENSION_ID))
        record = host.get_record(EXTENSION_ID)
        assert record is not None
        # 第一次崩溃：投影回滚 → COMPATIBLE（可重新激活）。
        with pytest.raises(Exception, match="worker_crashed"):
            _raw_tool(registry, "acme_crash")()
        assert record.worker_crash_count == 1
        assert record.state is ExtensionState.COMPATIBLE
        assert not registry.has("acme_crash")
        # 重新激活后再崩溃：达到上限 → QUARANTINED。
        assert not has_errors(host.activate(EXTENSION_ID))
        with pytest.raises(Exception):
            _raw_tool(registry, "acme_crash")()
        assert record.state is ExtensionState.QUARANTINED
        assert any(
            d.code is DiagnosticCode.WORKER_RESTART_QUARANTINED for d in record.diagnostics
        )
        host.reset()

    def test_call_timeout_kills_worker(self, tmp_path):
        _make_pack(tmp_path, WORKER_MAIN, call_timeout_s=1.0)
        host, registry = _make_host(tmp_path, max_worker_crashes=5)
        assert not has_errors(host.activate(EXTENSION_ID))
        record = host.get_record(EXTENSION_ID)
        assert record is not None
        with pytest.raises(Exception, match="worker_call_timeout"):
            _raw_tool(registry, "acme_sleep")(seconds=8.0)
        assert record.worker is None
        assert record.worker_crash_count == 1
        assert record.state is ExtensionState.COMPATIBLE
        assert not registry.has("acme_sleep")
        host.reset()

    def test_undeclared_tool_fails_activation(self, tmp_path):
        main = WORKER_MAIN.replace('("import_secret_env", _import_secret_env', '("ghost_tool", _import_secret_env')
        _make_pack(tmp_path, main)
        host, registry = _make_host(tmp_path)
        diags = host.activate(EXTENSION_ID)
        record = host.get_record(EXTENSION_ID)
        assert record is not None and record.state is ExtensionState.FAILED
        assert any(d.code is DiagnosticCode.UNDECLARED_REGISTRATION for d in diags)
        host.reset()

    def test_health_rpc(self, tmp_path):
        _make_pack(
            tmp_path,
            HEALTHY_MAIN,
            tools=[{"name": "noop", "description": "noop"}],
            diagnostics_entry="check",
        )
        host, _ = _make_host(tmp_path)
        assert not has_errors(host.activate(EXTENSION_ID))
        assert host.health(EXTENSION_ID)["status"] == "healthy"
        host.reset()

    def test_worker_env_is_sanitized(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKER_TEST_SECRET", "leaky-secret")
        _make_pack(tmp_path, WORKER_MAIN)
        host, registry = _make_host(tmp_path)
        assert not has_errors(host.activate(EXTENSION_ID))
        # 最小环境：宿主注入的任意敏感环境变量不进入 worker。
        assert _raw_tool(registry, "acme_import_secret_env")() == {"has_secret": False}
        host.reset()

    def test_worker_packs_cannot_project_class_instances(self, tmp_path):
        """manifest 层拒绝（worker + algorithm 声明）→ 解析失败，fail closed。"""
        pack = _make_pack(tmp_path, HEALTHY_MAIN, tools=[{"name": "noop", "description": "n"}])
        data = json.loads((pack / "manifest.json").read_text())
        data["algorithms"] = [{"id": "kde", "description": "x"}]
        (pack / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
        host, _ = _make_host(tmp_path)
        diags = host.activate(EXTENSION_ID)
        assert any(d.code is DiagnosticCode.MANIFEST_INVALID for d in diags)
        assert host.get_record(EXTENSION_ID) is None

    def test_status_report_reflects_worker(self, tmp_path):
        _make_pack(tmp_path, WORKER_MAIN)
        host, _ = _make_host(tmp_path)
        assert not has_errors(host.activate(EXTENSION_ID))
        report = host.status_report()
        entry = next(e for e in report["extensions"] if e["id"] == EXTENSION_ID)
        assert entry["execution"] == "worker"
        assert isinstance(entry["worker_pid"], int)
        assert entry["worker_crash_count"] == 0
        host.reset()
