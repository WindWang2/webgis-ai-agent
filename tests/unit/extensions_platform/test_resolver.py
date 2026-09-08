"""依赖解析器与升级/回滚语义测试（ADR-0105 Wave 8）。"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path


from app.extensions_platform.diagnostics import DiagnosticCode, has_errors
from app.extensions_platform.host import ExtensionHost, ExtensionState, HostPolicy
from app.extensions_platform.resolver import (
    DepView,
    RecordView,
    check_upgrade_conflicts,
    constraint_diagnostics_for,
    resolve_activation_plan,
)
from app.tools.registry import ToolRegistry

NOOP_MAIN = textwrap.dedent(
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


def _make_pack(
    root: Path,
    ext_id: str,
    version: str = "1.0.0",
    *,
    deps: list[dict] | None = None,
    optional_deps: list[dict] | None = None,
) -> Path:
    ns, name = ext_id.split(".")
    pack = root / f"{ext_id}.dir"
    pack.mkdir(exist_ok=True)
    manifest: dict = {
        "schema_version": 1,
        "id": ext_id,
        "name": name,
        "namespace": ns,
        "version": version,
        "api_version": "1.1.0",
        "entry_point": "main",
        "description": f"resolver pack {ext_id}",
        "tools": [{"name": "noop", "description": "noop"}],
    }
    if deps:
        manifest["dependencies"] = deps
    if optional_deps:
        manifest["optional_dependencies"] = optional_deps
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pack / "main.py").write_text(NOOP_MAIN, encoding="utf-8")
    return pack


def _host(root: Path) -> ExtensionHost:
    host = ExtensionHost(
        tool_registry=ToolRegistry(),
        policy=HostPolicy(roots=(root,)),
    )
    host.discover()
    return host


class TestPureResolver:
    def _view(self, ext_id, version, *deps):
        return RecordView(id=ext_id, version=version, state="compatible", deps=tuple(deps))

    def test_constraint_satisfied(self):
        record = self._view("a.b", "1.0.0", DepView("x.y", True, ">=1.0,<2.0"))
        target = self._view("x.y", "1.5.0")
        assert constraint_diagnostics_for(record, {"x.y": target}) == []

    def test_constraint_violated_required_is_error(self):
        record = self._view("a.b", "1.0.0", DepView("x.y", True, ">=2.0"))
        target = self._view("x.y", "1.5.0")
        diags = constraint_diagnostics_for(record, {"x.y": target})
        assert len(diags) == 1
        assert diags[0].code is DiagnosticCode.DEPENDENCY_MISSING
        assert diags[0].severity.value == "error"

    def test_constraint_violated_optional_is_warning(self):
        record = self._view("a.b", "1.0.0", DepView("x.y", False, ">=2.0"))
        target = self._view("x.y", "1.5.0")
        diags = constraint_diagnostics_for(record, {"x.y": target})
        assert diags[0].code is DiagnosticCode.OPTIONAL_DEPENDENCY_ABSENT
        assert diags[0].severity.value == "warning"

    def test_missing_target_is_not_this_layer_s_concern(self):
        record = self._view("a.b", "1.0.0", DepView("x.y", True, ">=1.0"))
        assert constraint_diagnostics_for(record, {}) == []

    def test_topological_order_deterministic(self):
        records = {
            "a.base": self._view("a.base", "1.0.0"),
            "b.mid": self._view("b.mid", "1.0.0", DepView("a.base", True, None)),
            "c.top": self._view("c.top", "1.0.0", DepView("b.mid", True, None)),
            "d.solo": self._view("d.solo", "1.0.0"),
        }
        plan = resolve_activation_plan(records, set(records))
        # 字典序 tie-break（与 V1 activate_all 的 ready.sort() 语义一致）。
        assert list(plan.ordered) == ["a.base", "b.mid", "c.top", "d.solo"]

    def test_cycle_members_excluded_from_plan(self):
        records = {
            "a.one": self._view("a.one", "1.0.0", DepView("a.two", True, None)),
            "a.two": self._view("a.two", "1.0.0", DepView("a.one", True, None)),
            "b.solo": self._view("b.solo", "1.0.0"),
        }
        plan = resolve_activation_plan(records, set(records))
        assert list(plan.ordered) == ["b.solo"]

    def test_upgrade_conflict_detection(self):
        records = {
            "x.lib": self._view("x.lib", "1.0.0"),
            "a.user": self._view("a.user", "1.0.0", DepView("x.lib", True, ">=1.0,<2.0")),
            "b.user": self._view("b.user", "1.0.0", DepView("x.lib", True, ">=1.0")),
        }
        assert check_upgrade_conflicts("x.lib", "1.9.0", records) == []
        conflicts = check_upgrade_conflicts("x.lib", "2.0.0", records)
        assert len(conflicts) == 1
        assert conflicts[0].code is DiagnosticCode.DEPENDENCY_CONFLICT


class TestHostConstraintValidation:
    def test_unsatisfied_constraint_blocks_activation(self, tmp_path):
        _make_pack(tmp_path, "basepack.base", "1.0.0")
        _make_pack(tmp_path, "consumerpack.consumer", "1.0.0", deps=[{"id": "basepack.base", "version": ">=2.0"}])
        host = _host(tmp_path)
        diags = host.activate("consumerpack.consumer")
        record = host.get_record("consumerpack.consumer")
        assert record is not None and record.state is ExtensionState.INCOMPATIBLE
        assert any(
            d.code is DiagnosticCode.DEPENDENCY_MISSING and "constraint" in d.message
            for d in diags
        )
        host.reset()

    def test_satisfied_constraint_activates(self, tmp_path):
        base = _make_pack(tmp_path, "basepack.base", "1.5.0")
        assert base.is_dir()
        _make_pack(tmp_path, "consumerpack.consumer", "1.0.0", deps=[{"id": "basepack.base", "version": ">=1.0,<2.0"}])
        host = _host(tmp_path)
        results = host.activate_all()
        assert not has_errors(results.get("consumerpack.consumer", [])), [
            d.message for d in results.get("consumerpack.consumer", [])
        ]
        # 拓扑序：base 先于 consumer。
        assert host.get_record("basepack.base").state is ExtensionState.ACTIVE
        assert host.get_record("consumerpack.consumer").state is ExtensionState.ACTIVE
        host.reset()


class TestUpgradeRollback:
    def _write_version(self, pack: Path, version: str) -> None:
        data = json.loads((pack / "manifest.json").read_text())
        data["version"] = version
        (pack / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    def test_upgrade_bumps_active_version(self, tmp_path):
        pack = _make_pack(tmp_path, "acme.solo", "1.0.0")
        host = _host(tmp_path)
        assert not has_errors(host.activate("acme.solo"))
        record = host.get_record("acme.solo")
        assert record is not None
        self._write_version(pack, "1.1.0")
        diags = host.upgrade("acme.solo")
        assert not has_errors(diags), [d.message for d in diags]
        assert record.manifest.version == "1.1.0"
        assert record.state is ExtensionState.ACTIVE
        host.reset()

    def test_downgrade_refused_without_flag(self, tmp_path):
        pack = _make_pack(tmp_path, "acme.solo", "2.0.0")
        host = _host(tmp_path)
        assert not has_errors(host.activate("acme.solo"))
        record = host.get_record("acme.solo")
        self._write_version(pack, "1.0.0")
        diags = host.upgrade("acme.solo")
        assert any(d.code is DiagnosticCode.MANIFEST_INVALID and "older" in d.message for d in diags)
        assert record is not None and record.state is ExtensionState.ACTIVE
        host.reset()

    def test_rollback_with_allow_downgrade(self, tmp_path):
        pack = _make_pack(tmp_path, "acme.solo", "2.0.0")
        host = _host(tmp_path)
        assert not has_errors(host.activate("acme.solo"))
        record = host.get_record("acme.solo")
        self._write_version(pack, "1.9.0")
        diags = host.upgrade("acme.solo", allow_downgrade=True)
        assert not has_errors(diags), [d.message for d in diags]
        assert record is not None and record.manifest.version == "1.9.0"
        host.reset()

    def test_upgrade_conflict_refused_and_old_version_stays_active(self, tmp_path):
        lib = _make_pack(tmp_path, "libpack.lib", "1.0.0")
        _make_pack(tmp_path, "userpack.user", "1.0.0", deps=[{"id": "libpack.lib", "version": ">=1.0,<2.0"}])
        host = _host(tmp_path)
        results = host.activate_all()
        assert all(not has_errors(v) for v in results.values())
        record = host.get_record("libpack.lib")
        assert record is not None
        # 新版本破坏 acme.user 的约束 → 升级被拒，旧版继续运行。
        self._write_version(lib, "2.0.0")
        diags = host.upgrade("libpack.lib")
        assert any(d.code is DiagnosticCode.DEPENDENCY_CONFLICT for d in diags)
        assert record.manifest.version == "1.0.0"
        assert record.state is ExtensionState.ACTIVE
        host.reset()

    def test_reload_downgrade_guard(self, tmp_path):
        pack = _make_pack(tmp_path, "acme.solo", "2.0.0")
        host = _host(tmp_path)
        host.validate_extension("acme.solo")
        self._write_version(pack, "1.0.0")
        diags = host.reload("acme.solo", activate=False)
        assert any("downgrade" in d.message for d in diags)
        host.reset()
