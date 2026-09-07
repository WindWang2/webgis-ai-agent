"""扩展生命周期测试（ADR-0104 Wave 2）。

用真实 ToolRegistry + 合成扩展目录驱动完整状态机：
activate / degrade / 回滚原子性 / 卸载零残留 / reload 幂等 / 依赖环 /
信任隔离。算法投影测试使用全局 AlgorithmRegistry 单例（带种子数据）。
"""

from __future__ import annotations

import json
from pathlib import Path


from app.extensions_platform.diagnostics import DiagnosticCode, has_errors
from app.extensions_platform.host import ExtensionHost, ExtensionState, HostPolicy
from app.extensions_platform.permissions import Permission
from app.tools.registry import ToolRegistry

# ---------------------------------------------------------------- helpers


def _manifest(ns: str, name: str, **extra) -> dict:
    data = {
        "id": f"{ns}.{name}",
        "name": name,
        "namespace": ns,
        "version": "1.0.0",
        "entry_point": "main",
        "description": "synthetic extension",
    }
    data.update(extra)
    return data


def _write_extension(
    root: Path,
    ns: str,
    name: str,
    main_py: str,
    manifest_extra: dict | None = None,
    manifest: dict | None = None,
) -> Path:
    ext_dir = root / f"{ns}-{name}"
    ext_dir.mkdir(parents=True, exist_ok=True)
    data = manifest or _manifest(ns, name, **(manifest_extra or {}))
    (ext_dir / "manifest.json").write_text(json.dumps(data))
    (ext_dir / "main.py").write_text(main_py)
    return ext_dir


TOOL_EXT_MAIN = '''
from app.extensions_platform.sdk import ToolExtensionSpec


def _run(x: float) -> dict:
    return {"doubled": x * 2}


def activate(ctx):
    ctx.register_tool(ToolExtensionSpec(
        name="synth_double",
        description="Double a number.",
        func=_run,
        side_effect="pure",
        deterministic=True,
        param_descriptions={"x": "number to double"},
    ))
'''


def _write_tool_ext(
    root: Path,
    ns: str,
    name: str,
    main_py: str = TOOL_EXT_MAIN,
    tool_names: tuple[str, ...] = ("synth_double",),
    **manifest_extra,
) -> Path:
    declared = list(manifest_extra.pop("tools", None) or [
        {"name": n, "description": "declared"} for n in tool_names
    ])
    return _write_extension(
        root, ns, name, main_py,
        manifest_extra={**manifest_extra, "tools": declared},
    )


def _host(tmp_path: Path, tool_registry: ToolRegistry, **policy_extra) -> ExtensionHost:
    host = ExtensionHost(
        tool_registry=tool_registry,
        policy=HostPolicy(roots=(tmp_path,), builtin_ids=frozenset({"acme.builtin"}), **policy_extra),
    )
    host.discover()
    return host


class TestActivation:
    async def test_tool_projection_activation_and_dispatch(self, tmp_path):
        _write_tool_ext(tmp_path, "acme", "pack")
        registry = ToolRegistry()
        host = _host(tmp_path, registry)
        host.activate("acme.pack")
        record = host.get_record("acme.pack")
        assert record is not None and record.state is ExtensionState.ACTIVE
        assert registry.has("acme_synth_double")
        result = await registry.dispatch("acme_synth_double", {"x": 21})
        assert result == {"doubled": 42.0}

    def test_activation_idempotent(self, tmp_path):
        _write_tool_ext(tmp_path, "acme", "pack")
        registry = ToolRegistry()
        host = _host(tmp_path, registry)
        assert host.activate("acme.pack") == []
        assert registry.has("acme_synth_double")

    def test_undeclared_registration_is_fail_closed(self, tmp_path):
        rogue = TOOL_EXT_MAIN.replace('name="synth_double"', 'name="synth_rogue"')
        _write_extension(
            tmp_path, "acme", "pack", rogue,
            manifest_extra={"tools": [{"name": "declared", "description": "x"}]},
        )
        registry = ToolRegistry()
        host = _host(tmp_path, registry)
        host.activate("acme.pack")
        record = host.get_record("acme.pack")
        assert record is not None and record.state is ExtensionState.FAILED
        assert not registry.has("acme_synth_rogue")
        assert not registry.has("acme_declared")
        assert any(
            d.code is DiagnosticCode.UNDECLARED_REGISTRATION and d.severity.value == "error"
            for d in record.diagnostics
        )

    def test_failed_activation_rolls_back_clean(self, tmp_path):
        main = TOOL_EXT_MAIN + "\n    raise RuntimeError('boom after registering')\n"
        _write_extension(
            tmp_path, "acme", "pack", main,
            manifest_extra={"tools": [{"name": "synth_double", "description": "x"}]},
        )
        registry = ToolRegistry()
        host = _host(tmp_path, registry)
        host.activate("acme.pack")
        record = host.get_record("acme.pack")
        assert record is not None and record.state is ExtensionState.FAILED
        assert not registry.has("acme_synth_double")
        assert registry.get_schemas() == []

    async def test_extension_cannot_shadow_core_tool(self, tmp_path):
        # 预注册一个核心同名工具；扩展声明同名（投影前缀化后碰撞）。
        registry = ToolRegistry()

        def _core() -> dict:
            return {"core": True}

        registry.register("acme_synth_double", "core impl", _core)
        _write_tool_ext(tmp_path, "acme", "pack")
        host = _host(tmp_path, registry)
        host.activate("acme.pack")
        record = host.get_record("acme.pack")
        assert record is not None and record.state is ExtensionState.FAILED
        assert any(
            d.code is DiagnosticCode.REGISTRY_PROJECTION_COLLISION for d in record.diagnostics
        )
        # 核心工具原封不动。
        assert await registry.dispatch("acme_synth_double", {}) == {"core": True}

    def test_quarantined_extension_never_imports(self, tmp_path):
        bomb = "raise ImportError('quarantined code executed')\n"
        _write_extension(tmp_path, "acme", "pack", bomb)
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,), block=frozenset({"acme.pack"})),
        )
        host.discover()
        record = host.get_record("acme.pack")
        assert record is not None and record.state is ExtensionState.QUARANTINED
        assert host.activate("acme.pack") != []
        assert record.state is ExtensionState.QUARANTINED


class TestDeactivateUnloadReload:
    def test_deactivate_leaves_no_zombies(self, tmp_path):
        _write_tool_ext(tmp_path, "acme", "pack")
        registry = ToolRegistry()
        host = _host(tmp_path, registry)
        host.activate("acme.pack")
        schemas_before = len(registry.get_schemas())
        host.deactivate("acme.pack")
        assert not registry.has("acme_synth_double")
        assert len(registry.get_schemas()) == schemas_before - 1
        assert host.get_record("acme.pack").state is ExtensionState.COMPATIBLE

    def test_reload_is_idempotent(self, tmp_path):
        _write_tool_ext(tmp_path, "acme", "pack")
        registry = ToolRegistry()
        host = _host(tmp_path, registry)
        host.activate("acme.pack")
        fp1 = registry.get_schemas()
        meta1 = dict(registry._metadata["acme_synth_double"])
        host.reload("acme.pack")  # active → deactivate → unload → activate
        fp2 = registry.get_schemas()
        meta2 = dict(registry._metadata["acme_synth_double"])
        assert fp1 == fp2
        assert meta1 == meta2
        assert host.get_record("acme.pack").state is ExtensionState.ACTIVE

    def test_unload_purges_module_and_allows_rediscover(self, tmp_path):
        _write_tool_ext(tmp_path, "acme", "pack")
        registry = ToolRegistry()
        host = _host(tmp_path, registry)
        host.activate("acme.pack")
        host.deactivate("acme.pack")
        host.unload("acme.pack")
        assert host.get_record("acme.pack").state is ExtensionState.DISCOVERED
        assert not any(m.startswith("webgis_ext_acme") for m in __import__("sys").modules)
        # 卸载后可再次激活。
        assert host.activate("acme.pack") == []
        assert registry.has("acme_synth_double")


class TestDependencies:
    def _dep_pair(self, tmp_path: Path):
        dep_main = TOOL_EXT_MAIN  # declares + registers acme_synth_double
        _write_extension(
            tmp_path, "acme", "dep", dep_main,
            manifest_extra={"tools": [{"name": "synth_double", "description": "x"}]},
        )
        _write_extension(
            tmp_path, "acme", "top",
            "def activate(ctx):\n    return None\n",
            manifest_extra={"dependencies": [{"id": "acme.dep", "required": True}]},
        )

    def test_required_dependency_enforced(self, tmp_path):
        self._dep_pair(tmp_path)
        registry = ToolRegistry()
        host = _host(tmp_path, registry)
        # 依赖未激活时激活依赖者 → typed 失败。
        diagnostics = host.activate("acme.top")
        assert any(d.code is DiagnosticCode.DEPENDENCY_MISSING for d in diagnostics)
        assert host.get_record("acme.top").state is ExtensionState.FAILED
        # 激活被依赖者后重试 → 成功（失败状态允许重试）。
        assert not has_errors(host.activate("acme.dep"))
        assert host.activate("acme.top") == []

    def test_activate_all_uses_topo_order(self, tmp_path):
        self._dep_pair(tmp_path)
        registry = ToolRegistry()
        host = _host(tmp_path, registry)
        results = host.activate_all()
        assert "acme.top" in results
        assert not has_errors(results["acme.top"])
        assert host.get_record("acme.top").state is ExtensionState.ACTIVE

    def test_dependency_cycle_detected(self, tmp_path):
        _write_extension(
            tmp_path, "acme", "a", "def activate(ctx):\n    return None\n",
            manifest_extra={"dependencies": [{"id": "acme.b", "required": True}]},
        )
        _write_extension(
            tmp_path, "acme", "b", "def activate(ctx):\n    return None\n",
            manifest_extra={"dependencies": [{"id": "acme.a", "required": True}]},
        )
        host = _host(tmp_path, ToolRegistry())
        for eid in ("acme.a", "acme.b"):
            record = host.get_record(eid)
            assert record.state is ExtensionState.INCOMPATIBLE
            assert any(d.code is DiagnosticCode.DEPENDENCY_CYCLE for d in record.diagnostics)

    def test_optional_dependency_absent_degrades(self, tmp_path):
        _write_tool_ext(
            tmp_path, "acme", "pack",
            optional_dependencies=[{"id": "acme.missing", "required": False}],
        )
        registry = ToolRegistry()
        host = _host(tmp_path, registry)
        diagnostics = host.activate("acme.pack")
        assert not has_errors(diagnostics)
        assert host.get_record("acme.pack").state is ExtensionState.DEGRADED
        assert any(
            d.code is DiagnosticCode.OPTIONAL_DEPENDENCY_ABSENT for d in diagnostics
        )


class TestPermissionsInheritance:
    async def test_tool_permission_enforced_at_dispatch(self, tmp_path):
        main = '''
from app.extensions_platform.sdk import ToolExtensionSpec
from app.extensions_platform.permissions import Permission


def _fetch(url: str) -> dict:
    return {"ok": True}


def activate(ctx):
    ctx.register_tool(ToolExtensionSpec(
        name="synth_fetch",
        description="Fetch.",
        func=_fetch,
        side_effect="cacheable_read",
        network=True,
        required_permissions=[Permission.NETWORK],
    ))
'''
        _write_extension(
            tmp_path, "acme", "pack", main,
            manifest_extra={
                "permissions": ["network"],
                "tools": [{"name": "synth_fetch", "description": "x"}],
            },
        )
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(
                roots=(tmp_path,),
                grants={"acme.pack": frozenset({Permission.NETWORK})},
            ),
        )
        host.discover()
        assert not has_errors(host.activate("acme.pack"))
        result = await registry.dispatch("acme_synth_fetch", {"url": "https://x"})
        assert result == {"ok": True}

    async def test_ungranted_permission_fails_typed_at_dispatch(self, tmp_path):
        main = '''
from app.extensions_platform.sdk import ToolExtensionSpec
from app.extensions_platform.permissions import Permission


def _fetch(url: str) -> dict:
    return {"ok": True}


def activate(ctx):
    ctx.register_tool(ToolExtensionSpec(
        name="synth_fetch",
        description="Fetch.",
        func=_fetch,
        side_effect="cacheable_read",
        network=True,
        required_permissions=[Permission.NETWORK],
    ))
'''
        _write_extension(
            tmp_path, "acme", "pack", main,
            manifest_extra={
                "permissions": ["network"],
                "tools": [{"name": "synth_fetch", "description": "x"}],
            },
        )
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry, policy=HostPolicy(roots=(tmp_path,), grants={})
        )
        host.discover()
        host.activate("acme.pack")
        result = await registry.dispatch("acme_synth_fetch", {"url": "https://x"})
        assert result.get("code") == "TOOL_ERROR"
        assert result.get("error_type") == "ExtensionPermissionDenied"
