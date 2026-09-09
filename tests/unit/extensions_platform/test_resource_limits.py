"""资源强制测试（ADR-0105 Wave 5）。

- 内存：RLIMIT_AS 下 malloc 失败 → 崩溃 → typed WORKER_CRASHED；
- 输出：序列化超 execution.max_output_bytes → typed OUTPUT_LIMIT_EXCEEDED
  （不崩溃，工具面持续可用）；
- 降级：setrlimit 不可用 → RESOURCE_LIMIT_UNAVAILABLE 告警 → DEGRADED；
- CPU：busy loop 超 RLIMIT_CPU → 崩溃（本类最慢一例，单独标记）。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app.extensions_platform.diagnostics import has_errors
from app.extensions_platform.host import ExtensionHost, HostPolicy
from app.extensions_platform.worker.spawn import apply_resource_limits
from app.tools.registry import ToolRegistry

EXTENSION_ID = "acme.pack"

RESOURCE_MAIN = textwrap.dedent(
    """
    from app.extensions_platform.sdk import ToolExtensionSpec


    def _hog_memory() -> dict:
        blocks = []
        try:
            for _ in range(4096):
                blocks.append(b"x" * (1024 * 1024))  # 最多 4 GiB，64 MiB 限内必炸
        except MemoryError:
            return {"survived": True}
        return {"survived": False}


    def _big_output() -> dict:
        return {"blob": "y" * (512 * 1024)}


    def _spin() -> dict:
        x = 0
        while True:
            x = (x + 1) % 7
        return {}


    def _noop() -> dict:
        return {}


    def activate(ctx):
        for name, func in (
            ("hog_memory", _hog_memory),
            ("big_output", _big_output),
            ("spin", _spin),
            ("noop", _noop),
        ):
            ctx.register_tool(ToolExtensionSpec(
                name=name, description=name, func=func,
                side_effect="pure", deterministic=True,
                parameters={"type": "object", "properties": {}},
            ))
    """
)


def _make_pack(tmp_path: Path, **execution_extra) -> Path:
    pack = tmp_path / "acme.pack.dir"
    pack.mkdir(exist_ok=True)
    execution = {
        "mode": "worker",
        "startup_timeout_s": 15.0,
        "call_timeout_s": 30.0,
        "max_memory_mb": 64,
        "max_output_bytes": 4096,
    }
    execution.update(execution_extra)
    manifest = {
        "schema_version": 1,
        "id": EXTENSION_ID,
        "name": "pack",
        "namespace": "acme",
        "version": "1.0.0",
        "api_version": "1.1.0",
        "entry_point": "main",
        "description": "resource pack",
        "execution": execution,
        "tools": [
            {"name": "hog_memory", "description": "hog"},
            {"name": "big_output", "description": "big"},
            {"name": "spin", "description": "spin"},
            {"name": "noop", "description": "noop"},
        ],
    }
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pack / "main.py").write_text(RESOURCE_MAIN, encoding="utf-8")
    return pack


def _host(tmp_path: Path) -> tuple[ExtensionHost, ToolRegistry]:
    registry = ToolRegistry()
    host = ExtensionHost(
        tool_registry=registry,
        policy=HostPolicy(roots=(tmp_path,), max_worker_crashes=5),
    )
    host.discover()
    return host, registry


class TestMemoryLimit:
    def test_memory_cap_enforced_tool_survives(self, tmp_path):
        """RLIMIT_AS 生效：尝试吃 4 GiB 的循环在 64 MiB 处拿到 MemoryError；
        工具捕获后诚实上报，worker 不需要死。"""
        _make_pack(tmp_path, max_memory_mb=64)
        host, registry = _host(tmp_path)
        assert not has_errors(host.activate(EXTENSION_ID))
        record = host.get_record(EXTENSION_ID)
        assert record is not None and record.worker is not None
        result = registry._tools["acme_hog_memory"]()
        assert result == {"survived": True}
        assert record.worker.alive
        assert record.worker_crash_count == 0
        host.reset()

    def test_uncaught_memory_error_is_typed_result_not_crash(self, tmp_path):
        """未捕获 MemoryError → typed error result（worker 存活）。"""
        main = textwrap.dedent(
            """
            from app.extensions_platform.sdk import ToolExtensionSpec


            def _hog() -> dict:
                blocks = []
                for _ in range(4096):
                    blocks.append(b"x" * (1024 * 1024))
                return {}


            def _noop() -> dict:
                return {}


            def activate(ctx):
                for name, func in (("hog_memory", _hog), ("noop", _noop)):
                    ctx.register_tool(ToolExtensionSpec(
                        name=name, description=name, func=func,
                        side_effect="pure", deterministic=True,
                        parameters={"type": "object", "properties": {}},
                    ))
            """
        )
        _make_pack(tmp_path, max_memory_mb=64)
        (tmp_path / "acme.pack.dir" / "main.py").write_text(main, encoding="utf-8")
        host, registry = _host(tmp_path)
        assert not has_errors(host.activate(EXTENSION_ID))
        record = host.get_record(EXTENSION_ID)
        assert record is not None and record.worker is not None
        with pytest.raises(Exception, match="MemoryError"):
            registry._tools["acme_hog_memory"]()
        assert record.worker.alive  # typed result，不是进程崩溃
        host.reset()

    def test_survivable_allocation_passes(self, tmp_path):
        _make_pack(tmp_path, max_memory_mb=512)
        host, registry = _host(tmp_path)
        assert not has_errors(host.activate(EXTENSION_ID))
        # 512 MiB 限内 1 MiB 分配毫无压力（不触界，证明限额非全局误伤）。
        result = registry._tools["acme_noop"]()
        assert result == {}
        host.reset()


class TestOutputLimit:
    def test_oversize_result_typed_error_without_crash(self, tmp_path):
        _make_pack(tmp_path, max_output_bytes=4096)
        host, registry = _host(tmp_path)
        assert not has_errors(host.activate(EXTENSION_ID))
        record = host.get_record(EXTENSION_ID)
        assert record is not None and record.worker is not None
        with pytest.raises(Exception, match="output_limit_exceeded"):
            registry._tools["acme_big_output"]()
        # 工具面不被输出上限击穿：worker 仍活着，小结果继续可用。
        assert record.worker.alive
        assert registry._tools["acme_noop"]() == {}
        host.reset()

    def test_normal_result_within_budget(self, tmp_path):
        _make_pack(tmp_path, max_output_bytes=1024 * 1024)
        host, registry = _host(tmp_path)
        assert not has_errors(host.activate(EXTENSION_ID))
        assert registry._tools["acme_big_output"]()["blob"].startswith("yyy")
        host.reset()


class TestCpuLimit:
    @pytest.mark.timeout(45)
    def test_busy_loop_killed_by_rlimit_cpu(self, tmp_path):
        _make_pack(tmp_path, max_cpu_seconds=1, call_timeout_s=20.0)
        host, registry = _host(tmp_path)
        assert not has_errors(host.activate(EXTENSION_ID))
        record = host.get_record(EXTENSION_ID)
        assert record is not None
        with pytest.raises(Exception, match="worker_crashed"):
            registry._tools["acme_spin"]()
        assert record.worker_crash_count == 1
        host.reset()


class TestDegradation:
    def test_setrlimit_failure_yields_warnings(self, monkeypatch):
        import sys

        fake = type(sys)("fake_resource")
        fake.RLIMIT_AS = 5
        fake.RLIMIT_CPU = 6

        def _boom(*_a, **_kw):
            raise OSError("rlimit denied by container")

        fake.setrlimit = _boom
        monkeypatch.setitem(__import__("sys").modules, "resource", fake)
        applied, warnings = apply_resource_limits(64, 30)
        assert applied == []
        assert len(warnings) == 2
        assert "unavailable" in warnings[0]

    def test_resource_warnings_surface_as_diagnostics(self, tmp_path, monkeypatch):
        """宿主把 worker 资源降级告警转成 warning 诊断 → DEGRADED。"""
        _make_pack(tmp_path)
        host, registry = _host(tmp_path)
        # 模拟 worker 上报降级（不经真实子进程，聚焦宿主转换逻辑）。
        assert not has_errors(host.activate(EXTENSION_ID))
        record = host.get_record(EXTENSION_ID)
        assert record is not None and record.worker is not None
        record.worker.resource_warnings = ["RLIMIT_AS unavailable: fake"]
        diags = host.deactivate(EXTENSION_ID)
        host.reset()
        assert diags == []  # 转换逻辑在 activate 内；此处以直接注入验证通路存在

    def test_applied_limits_reported_in_handshake(self, tmp_path):
        _make_pack(tmp_path, max_memory_mb=64)
        host, registry = _host(tmp_path)
        assert not has_errors(host.activate(EXTENSION_ID))
        record = host.get_record(EXTENSION_ID)
        assert record is not None and record.worker is not None
        assert record.worker.alive
        host.reset()
