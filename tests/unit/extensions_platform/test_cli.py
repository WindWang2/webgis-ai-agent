"""扩展平台开发者 CLI 测试（ADR-0104 / Wave 13）。

直接驱动 :func:`app.extensions_platform.cli.main`（不启子进程、不碰网络
/LLM）；扩展目录构造方式与 test_host_lifecycle.py 一致（合成包 + 真实
ToolRegistry 的只读 host）。CLI 永不激活扩展，故无需清理 registry 状态。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extensions_platform.cli import main

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
    (ext_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    (ext_dir / "main.py").write_text(main_py, encoding="utf-8")
    return ext_dir


GOOD_MAIN = '''
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


def _write_good_ext(root: Path) -> Path:
    """合法工具扩展：manifest 声明 synth_double，main.py 如实注册。"""
    return _write_extension(
        root,
        "acme",
        "pack",
        GOOD_MAIN,
        manifest_extra={"tools": [{"name": "synth_double", "description": "declared"}]},
    )


def _write_broken_ext(root: Path) -> Path:
    """坏包：manifest 不是 JSON → 发现期 DiscoveryFailure。"""
    ext_dir = root / "acme-broken"
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "manifest.json").write_text("{ not json", encoding="utf-8")
    return ext_dir


# ---------------------------------------------------------------- list


class TestList:
    def test_human_exit_zero_with_failures_section(self, tmp_path, capsys):
        _write_good_ext(tmp_path)
        _write_broken_ext(tmp_path)
        rc = main(["list", "--root", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        # 表格行（id / state）。
        assert "acme.pack" in out
        assert "compatible" in out
        assert "local_untrusted" in out
        assert "acme_synth_double" not in out  # 列表只到声明粒度，不含投影名
        # 信任边界提示（诚实表述：不是沙箱）。
        assert "trusted-code boundary" in out
        assert "NOT sandboxed" in out
        # 失败节：坏包路径 + 解析诊断码。
        assert "acme-broken" in out
        assert "manifest_parse_failed" in out

    def test_json_machine_readable(self, tmp_path, capsys):
        _write_good_ext(tmp_path)
        _write_broken_ext(tmp_path)
        rc = main(["list", "--json", "--root", str(tmp_path)])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)  # stdout 必须是纯 JSON
        assert payload["trust_boundary"].startswith("trust boundary:")
        assert "NOT sandboxed" in payload["trust_boundary"]
        assert payload["roots"] == [str(tmp_path)]
        assert len(payload["extensions"]) == 1
        ext = payload["extensions"][0]
        assert ext["id"] == "acme.pack"
        assert ext["state"] == "compatible"
        assert ext["declared_types"] == ["tools"]
        assert ext["entry_point"] == "main"
        assert len(payload["failures"]) == 1
        failure = payload["failures"][0]
        assert failure["path"].endswith("acme-broken")
        assert failure["diagnostics"][0]["code"] == "manifest_parse_failed"


# ---------------------------------------------------------------- validate


class TestValidate:
    def test_good_extension_exits_zero(self, tmp_path, capsys):
        _write_good_ext(tmp_path)
        rc = main(["validate", "acme.pack", "--root", str(tmp_path)])
        assert rc == 0
        assert "compatible" in capsys.readouterr().out

    def test_bad_declaration_exits_one(self, tmp_path, capsys):
        # manifest 可解析但声明非法（未知权限词）→ 静态校验 error → 1。
        _write_extension(
            tmp_path,
            "acme",
            "pack",
            GOOD_MAIN,
            manifest_extra={
                "permissions": ["not_a_permission"],
                "tools": [{"name": "synth_double", "description": "declared"}],
            },
        )
        rc = main(["validate", "acme.pack", "--root", str(tmp_path)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "permission_declaration_invalid" in out
        assert "NOT compatible" in out

    def test_unknown_id_exits_one(self, tmp_path, capsys):
        _write_broken_ext(tmp_path)  # 坏 manifest → id 根本不会被发现
        rc = main(["validate", "acme.broken", "--root", str(tmp_path)])
        assert rc == 1
        captured = capsys.readouterr()
        assert "manifest_invalid" in captured.out  # host.validate_extension typed 诊断
        assert "list" in captured.err  # 指向 list 看解析失败详情


# ---------------------------------------------------------------- inspect


class TestInspect:
    def test_prints_manifest_fingerprint_and_edges(self, tmp_path, capsys):
        _write_extension(
            tmp_path,
            "acme",
            "pack",
            GOOD_MAIN,
            manifest_extra={
                "tools": [{"name": "synth_double", "description": "declared"}],
                "optional_dependencies": [{"id": "acme.helper", "required": False}],
            },
        )
        rc = main(["inspect", "acme.pack", "--root", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        # manifest dump（JSON pretty）关键字段。
        assert '"id": "acme.pack"' in out
        assert '"entry_point": "main"' in out
        assert '"schema_version": 1' in out
        # 指纹 + 依赖边 + 诊断节。
        assert "fingerprint:" in out
        assert "optional: acme.helper" in out
        assert "diagnostics:" in out

    def test_json_payload(self, tmp_path, capsys):
        _write_good_ext(tmp_path)
        rc = main(["inspect", "acme.pack", "--json", "--root", str(tmp_path)])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["manifest"]["id"] == "acme.pack"
        assert len(payload["fingerprint"]) == 64  # sha256 hex
        assert payload["dependencies"]["required"] == []
        assert payload["state"] == "compatible"

    def test_unknown_id_exits_one_to_stderr(self, tmp_path, capsys):
        rc = main(["inspect", "nope.missing", "--root", str(tmp_path)])
        assert rc == 1
        assert "not discovered" in capsys.readouterr().err


# ---------------------------------------------------------------- doctor


class TestDoctor:
    def test_exits_zero_mentions_settings_and_read_only(self, tmp_path, capsys):
        _write_good_ext(tmp_path)
        rc = main(["doctor", "--root", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "EXTENSIONS_ENABLED" in out
        assert "EXTENSIONS_DIRS" in out
        assert "EXTENSION_PERMISSION_GRANTS" in out
        assert "read-only" in out  # 永不激活的显式声明
        assert "acme.pack" in out  # 每扩展状态

    def test_doctor_json_payload(self, tmp_path, capsys):
        _write_good_ext(tmp_path)
        rc = main(["doctor", "--json", "--root", str(tmp_path)])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["read_only"] is True
        assert "EXTENSIONS_ENABLED" in payload["settings"]
        assert isinstance(payload["grants_parsed"], dict)
        assert payload["extensions"][0]["id"] == "acme.pack"


# ---------------------------------------------------------------- scaffold


class TestScaffold:
    def test_scaffolded_pack_passes_validate_immediately(self, tmp_path, capsys):
        out_dir = tmp_path / "extensions"
        rc = main(["scaffold", "acme", "starter", "--dir", str(out_dir)])
        assert rc == 0
        pack = out_dir / "acme-starter"
        assert (pack / "manifest.json").is_file()
        assert (pack / "main.py").is_file()
        assert (pack / "health.py").is_file()
        assert (pack / "test_starter.py").is_file()
        # 脚手架产物直接通过平台校验（同一 CLI 命令闭环）。
        rc = main(["validate", "acme.starter", "--root", str(out_dir)])
        assert rc == 0
        assert "compatible" in capsys.readouterr().out

    def test_scaffold_json_artifacts_are_wellformed(self, tmp_path, capsys):
        out_dir = tmp_path / "extensions"
        assert main(["scaffold", "acme", "starter", "--dir", str(out_dir)]) == 0
        manifest = json.loads(
            (out_dir / "acme-starter" / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["id"] == "acme.starter"
        assert manifest["diagnostics_entry"] == "check_health"
        main_py = (out_dir / "acme-starter" / "main.py").read_text(encoding="utf-8")
        assert "def activate(ctx" in main_py
        assert "ToolExtensionSpec" in main_py
        assert "check_health" in main_py  # health.py 的转发入口

    def test_refuses_reserved_namespace_bad_tokens_and_existing_dir(
        self, tmp_path, capsys
    ):
        assert main(["scaffold", "core", "x", "--dir", str(tmp_path)]) == 2
        assert main(["scaffold", "bad-ns", "x", "--dir", str(tmp_path)]) == 2
        assert main(["scaffold", "acme", "BadName", "--dir", str(tmp_path)]) == 2
        # 拒绝路径不落盘。
        for rejected in ("core-x", "bad-ns-x", "acme-BadName"):
            assert not (tmp_path / rejected).exists()
        rc = main(["scaffold", "acme", "starter", "--dir", str(tmp_path)])
        assert rc == 0
        rc = main(["scaffold", "acme", "starter", "--dir", str(tmp_path)])
        assert rc == 2  # 已存在 → 拒绝覆盖
        assert "already exists" in capsys.readouterr().err


# ---------------------------------------------------------------- catalog


class TestCatalog:
    def test_markdown_groups_by_namespace(self, tmp_path, capsys):
        _write_good_ext(tmp_path)
        rc = main(["catalog", "--root", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "# GIS Extension Catalog" in out
        assert "## namespace `acme`" in out
        assert "`acme.pack` v1.0.0" in out
        # 声明条目以投影后的命名空间化名字呈现。
        assert "`acme_synth_double`" in out

    def test_catalog_json(self, tmp_path, capsys):
        _write_good_ext(tmp_path)
        rc = main(["catalog", "--json", "--root", str(tmp_path)])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        ns = payload["namespaces"][0]
        assert ns["namespace"] == "acme"
        ext = ns["extensions"][0]
        assert ext["id"] == "acme.pack"
        assert ext["declared"]["tools"][0]["name"] == "acme_synth_double"


# ---------------------------------------------------------------- 契约杂项


class TestCliContract:
    def test_help_exits_zero_and_lists_commands(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            main(["--help"])
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        for command in ("list", "inspect", "validate", "doctor", "scaffold", "catalog"):
            assert command in out

    def test_missing_command_exits_two(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            main([])
        assert excinfo.value.code == 2

    def test_help_does_not_import_heavy_modules(self, capsys):
        # --help 快速路径契约：argparse 解析不得新增 app.core.config /
        # app.tools.registry 导入（全量套件里它们可能已被别的测试加载，
        # 故只断言「新增为零」而非「不在 sys.modules」）。
        import sys

        before = set(sys.modules)
        with pytest.raises(SystemExit):
            main(["scaffold", "--help"])
        capsys.readouterr()
        added = set(sys.modules) - before
        assert not any(m == "app.core.config" or m.startswith("app.core.config.") for m in added)
        assert not any(m == "app.tools.registry" or m.startswith("app.tools.registry.") for m in added)
