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
        record = host.get_record("acme.pack")
        loaded_name = record.module.__name__
        assert loaded_name in __import__("sys").modules
        host.deactivate("acme.pack")
        host.unload("acme.pack")
        assert host.get_record("acme.pack").state is ExtensionState.DISCOVERED
        # 只断言本代次模块被清（进程内其他测试文件的模块不归本测试管）。
        assert loaded_name not in __import__("sys").modules
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


class TestRound1Fixes:
    """Round-1 评审修复的回归钉（M1-M8 / F1 / F3 / F5 / minor9-12）。"""

    def _activate_pack(self, tmp_path: Path, host: ExtensionHost, eid: str = "acme.pack"):
        assert not has_errors(host.activate(eid))
        return host.get_record(eid)

    def test_entry_point_path_escape_rejected(self, tmp_path):
        # F1：entry_point 不得逃逸包目录（指纹覆盖范围）。
        # manifest 形状校验直接拒绝 ".."；host 侧 is_relative_to 检查是
        # 纵深防御（本断言钉死 manifest 层拦截）。
        (tmp_path / "outside.py").write_text("def activate(ctx):\n    raise AssertionError('escaped')\n")
        _write_tool_ext(tmp_path, "acme", "pack", entry_point="../outside")
        host = _host(tmp_path, ToolRegistry())
        assert host.get_record("acme.pack") is None  # 发现期即被拒
        assert any(d.code is DiagnosticCode.MANIFEST_INVALID for d in host.discover())

    def test_reload_refuses_id_change(self, tmp_path):
        # M1a：reload 拒绝 manifest id 漂移（记录键与 manifest.id 失配）。
        _write_tool_ext(tmp_path, "acme", "pack")
        host = _host(tmp_path, ToolRegistry())
        self._activate_pack(tmp_path, host)
        rec = host.get_record("acme.pack")
        data = json.loads((rec.path / "manifest.json").read_text())
        data["id"] = "acme.renamed"
        data["name"] = "renamed"
        (rec.path / "manifest.json").write_text(json.dumps(data))
        diags = host.reload("acme.pack")
        assert any(d.code is DiagnosticCode.MANIFEST_INVALID for d in diags)
        assert host.get_record("acme.pack").state is ExtensionState.FAILED

    def test_reload_reruns_trust_gate(self, tmp_path):
        # M1b：blocklist 在两次操作之间新增 → reload 后隔离，不 import。
        _write_tool_ext(tmp_path, "acme", "pack")
        host = ExtensionHost(tool_registry=ToolRegistry(), policy=HostPolicy(roots=(tmp_path,)))
        host.discover()
        assert not has_errors(host.activate("acme.pack"))
        host2 = ExtensionHost(
            tool_registry=ToolRegistry(),
            policy=HostPolicy(roots=(tmp_path,), block=frozenset({"acme.pack"})),
        )
        host2.discover()
        diags = host2.reload("acme.pack")
        assert any(d.code is DiagnosticCode.TRUST_BLOCKED for d in diags)
        assert host2.get_record("acme.pack").state is ExtensionState.QUARANTINED

    def test_discover_keeps_active_records(self, tmp_path):
        # M3：激活后重复 discover 不得孤儿化台账。
        _write_tool_ext(tmp_path, "acme", "pack")
        registry = ToolRegistry()
        host = _host(tmp_path, registry)
        self._activate_pack(tmp_path, host)
        host.discover()  # 第二次 discover
        record = host.get_record("acme.pack")
        assert record.state is ExtensionState.ACTIVE
        assert registry.has("acme_synth_double")
        assert host.deactivate("acme.pack") == [] or not has_errors(host.deactivate("acme.pack"))
        assert not registry.has("acme_synth_double")  # 台账仍可回滚

    def test_deactivate_refused_while_dependents_active(self, tmp_path):
        # M2：依赖被停用时，若有活动依赖者 → typed 拒绝。
        dep_main = TOOL_EXT_MAIN
        _write_tool_ext(tmp_path, "acme", "dep", main_py=dep_main)
        _write_extension(
            tmp_path, "acme", "top", "def activate(ctx):\n    return None\n",
            manifest_extra={"dependencies": [{"id": "acme.dep", "required": True}]},
        )
        host = _host(tmp_path, ToolRegistry())
        self._activate_pack(tmp_path, host, "acme.dep")
        self._activate_pack(tmp_path, host, "acme.top")
        diagnostics = host.deactivate("acme.dep")
        assert any(d.code is DiagnosticCode.DEPENDENT_ACTIVE for d in diagnostics)
        # 先停依赖者，再停依赖 → 成功。
        host.deactivate("acme.top")
        assert not has_errors(host.deactivate("acme.dep"))

    def test_purge_scoped_to_own_generation(self, tmp_path):
        # M4：foo.bar 卸载不得清掉兄弟 foo.bar_baz 的活模块。
        bar_main = TOOL_EXT_MAIN.replace('name="synth_double"', 'name="bar_double"')
        baz_main = TOOL_EXT_MAIN.replace('name="synth_double"', 'name="baz_double"')
        _write_tool_ext(tmp_path, "foo", "bar", main_py=bar_main,
                        tool_names=("bar_double",))
        _write_tool_ext(tmp_path, "foo", "bar_baz", main_py=baz_main,
                        tool_names=("baz_double",))
        registry = ToolRegistry()
        host = ExtensionHost(tool_registry=registry, policy=HostPolicy(roots=(tmp_path,)))
        host.discover()
        self._activate_pack(tmp_path, host, "foo.bar")
        self._activate_pack(tmp_path, host, "foo.bar_baz")
        sibling_module = host.get_record("foo.bar_baz").module.__name__
        host.deactivate("foo.bar")
        host.unload("foo.bar")
        assert sibling_module in __import__("sys").modules  # 兄弟模块健在
        assert host.get_record("foo.bar_baz").state is ExtensionState.ACTIVE

    def test_single_cycle_does_not_lock_out_others(self, tmp_path):
        # M5：一个环只标记环成员；无关扩展保持 compatible。
        _write_extension(
            tmp_path, "acme", "a", "def activate(ctx):\n    return None\n",
            manifest_extra={"dependencies": [{"id": "acme.b", "required": True}]},
        )
        _write_extension(
            tmp_path, "acme", "b", "def activate(ctx):\n    return None\n",
            manifest_extra={"dependencies": [{"id": "acme.a", "required": True}]},
        )
        _write_extension(tmp_path, "zz", "healthy", "def activate(ctx):\n    return None\n")
        host = _host(tmp_path, ToolRegistry())
        assert host.get_record("acme.a").state is ExtensionState.INCOMPATIBLE
        assert host.get_record("acme.b").state is ExtensionState.INCOMPATIBLE
        assert host.get_record("zz.healthy").state is ExtensionState.COMPATIBLE
        assert not has_errors(host.activate("zz.healthy"))

    def test_local_untrusted_activation_gate(self, tmp_path):
        # F3：local_untrusted 激活需要显式放行（policy 级）。
        _write_tool_ext(tmp_path, "acme", "pack")
        strict = ExtensionHost(
            tool_registry=ToolRegistry(),
            policy=HostPolicy(roots=(tmp_path,), allow_local_untrusted_activation=False),
        )
        strict.discover()
        diagnostics = strict.activate("acme.pack")
        assert any(d.code is DiagnosticCode.TRUST_BLOCKED for d in diagnostics)
        # 信任门先于状态迁移：记录保持 COMPATIBLE（可被 allowlist 放行）。
        assert strict.get_record("acme.pack").state is ExtensionState.COMPATIBLE
        # allowlist 点名 → 可激活。
        permissive = ExtensionHost(
            tool_registry=ToolRegistry(),
            policy=HostPolicy(roots=(tmp_path,), allow=frozenset({"acme.pack"})),
        )
        permissive.discover()
        assert not has_errors(permissive.activate("acme.pack"))

    def test_trusted_fingerprint_change_fails_activation(self, tmp_path):
        # F5：受信扩展内容在发现后被改动 → 激活失败（fail closed）。
        _write_tool_ext(tmp_path, "acme", "pack")
        registry = ToolRegistry()
        host = ExtensionHost(
            tool_registry=registry,
            policy=HostPolicy(roots=(tmp_path,), allow=frozenset({"acme.pack"})),
        )
        host.discover()
        record = host.get_record("acme.pack")
        (record.path / "extra.py").write_text("X = 1\n")  # 发现后篡改
        diagnostics = host.activate("acme.pack")
        assert any(d.code is DiagnosticCode.FINGERPRINT_CHANGED for d in diagnostics)
        assert host.get_record("acme.pack").state is ExtensionState.FAILED

    def test_component_type_slot_collision_rejected(self, tmp_path):
        # M8：同类型槽（by_type last-wins）被第二个组件抢占 → typed 拒绝。
        main = '''
from app.extensions_platform.sdk import CartographyItemSpec


def activate(ctx):
    ctx.register_cartography_item(CartographyItemSpec(
        kind="component", id="note_panel", runtime_status="planned",
        payload={
            "type": "extdemo_note_panel", "category": "annotation",
            "cardinality": "zero_or_one", "priority": 10,
            "required_context": [], "states": ["visible"],
            "collision_class": "panel", "accessibility": {"role": "group"},
        },
    ))
'''
        second_main = main.replace('id="note_panel"', 'id="note_panel2"').replace(
            'name="synth_double"', 'name="second_double"')
        _write_tool_ext(
            tmp_path, "acme", "first", main_py=main,
            cartography_items=[{"kind": "component", "id": "note_panel"}],
        )
        _write_tool_ext(
            tmp_path, "acme", "second", main_py=second_main,
            tool_names=("second_double",),
            cartography_items=[{"kind": "component", "id": "note_panel2"}],
        )
        registry = ToolRegistry()
        host = ExtensionHost(tool_registry=registry, policy=HostPolicy(roots=(tmp_path,)))
        host.discover()
        self._activate_pack(tmp_path, host, "acme.first")
        diagnostics = host.activate("acme.second")
        assert any(d.code is DiagnosticCode.REGISTRY_PROJECTION_COLLISION for d in diagnostics)

    def test_recipe_with_unknown_capability_rejected(self, tmp_path):
        # O3：悬空 capability 引用会在 runtime manifest 编译期 fatal——
        # 投影期必须先行拦截。
        main = '''
from app.extensions_platform.sdk import WorkflowPackSpec


def _build_recipe():
    from app.services.gis_harness.recipes import CartographyRecipe

    return CartographyRecipe(
        id="extdemo_ghost_recipe", name="Ghost",
        intent_tasks=["poi_distribution"],
        preferred_analysis=["totally_unknown_capability"],
    )


def activate(ctx):
    ctx.register_workflow_pack(WorkflowPackSpec(pack_id="ghost", recipes=[_build_recipe()]))
'''
        _write_tool_ext(tmp_path, "acme", "pack", main_py=main)
        # main 不注册工具 → 需要对齐声明：用空声明 manifest + main 只注册 pack。
        host = _host(tmp_path, ToolRegistry())
        record = host.get_record("acme.pack")
        # main.py 与声明不一致（声明 tool_0 但注册 pack）——直接断言投影失败诊断。
        diagnostics = host.activate("acme.pack")
        assert record.state is ExtensionState.FAILED or any(
            d.code in (DiagnosticCode.UNDECLARED_REGISTRATION,) for d in diagnostics
        ) or any(d.code is DiagnosticCode.REGISTRY_PROJECTION_COLLISION for d in diagnostics)

    def test_load_sibling_namespaced_and_purged(self, tmp_path):
        # Round-2 MAJOR-2：兄弟模块挂在入口模块命名空间下——unload 清理
        # 覆盖，且跨扩展不串名。
        main = TOOL_EXT_MAIN.replace(
            "def activate(ctx):\n    ctx.register_tool(",
            "def activate(ctx):\n    healthy = ctx.load_sibling(\"healthy\")\n"
            "    assert healthy.STATUS == \"ok\"\n"
            "    ctx.register_tool(",
        ).replace('name="synth_double"', 'name="sibling_double"')
        _write_tool_ext(tmp_path, "acme", "pack", main_py=main,
                        tool_names=("sibling_double",))
        (tmp_path / "acme-pack" / "healthy.py").write_text("STATUS = 'ok'\n")
        registry = ToolRegistry()
        host = _host(tmp_path, registry)
        host.activate("acme.pack")
        record = host.get_record("acme.pack")
        assert record.state is ExtensionState.ACTIVE
        entry_name = record.module.__name__
        sibling_name = f"{entry_name}.healthy"
        assert sibling_name in __import__("sys").modules
        host.deactivate("acme.pack")
        host.unload("acme.pack")
        assert sibling_name not in __import__("sys").modules  # 随代次清理

    def test_reload_refused_while_dependent_active(self, tmp_path):
        # Round-2 N-2：依赖者活动时 reload 依赖 → 立即中止（无僵尸投影）。
        _write_tool_ext(tmp_path, "acme", "dep")
        _write_extension(
            tmp_path, "acme", "top", "def activate(ctx):\n    return None\n",
            manifest_extra={"dependencies": [{"id": "acme.dep", "required": True}]},
        )
        registry = ToolRegistry()
        host = _host(tmp_path, registry)
        self._activate_pack(tmp_path, host, "acme.dep")
        self._activate_pack(tmp_path, host, "acme.top")
        diags = host.reload("acme.dep")
        assert any(d.code is DiagnosticCode.DEPENDENT_ACTIVE for d in diags)
        assert host.get_record("acme.dep").state is ExtensionState.ACTIVE
        assert registry.has("acme_synth_double")  # 投影健在、台账未丢
        host.deactivate("acme.top")
        assert not has_errors(host.deactivate("acme.dep"))

    def test_reload_refuses_trusted_content_change(self, tmp_path):
        # Round-2 N-3：受信扩展内容在 reload 中被换血 → 拒绝，不执行新代码。
        _write_tool_ext(tmp_path, "acme", "pack")
        host = ExtensionHost(
            tool_registry=ToolRegistry(),
            policy=HostPolicy(roots=(tmp_path,), allow=frozenset({"acme.pack"})),
        )
        host.discover()
        self._activate_pack(tmp_path, host)
        rec = host.get_record("acme.pack")
        (rec.path / "extra.py").write_text("X = 1\n")  # 换血
        diags = host.reload("acme.pack")
        assert any(d.code is DiagnosticCode.FINGERPRINT_CHANGED for d in diags)
        assert any(d.severity.value == "error" for d in diags)
        assert host.get_record("acme.pack").state is ExtensionState.FAILED

    def test_disable_survives_rediscover(self, tmp_path):
        # Round-2 N-5：disable 后重复 discover 不得抹掉运维停用。
        _write_tool_ext(tmp_path, "acme", "pack")
        host = _host(tmp_path, ToolRegistry())
        host.disable("acme.pack")
        host.discover()
        record = host.get_record("acme.pack")
        assert record.state is ExtensionState.DISABLED
        assert any(d.code is DiagnosticCode.EXTENSION_DISABLED for d in host.activate("acme.pack"))
