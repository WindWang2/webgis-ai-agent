"""认证 harness 与 CLI certify 测试（ADR-0105 Wave 11）。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.extensions_platform.certification import certify_extension
from app.extensions_platform.cli import main as cli_main
from app.extensions_platform.host import ExtensionHost, HostPolicy
from app.tools.registry import ToolRegistry

EXTENSION_ID = "extdemo.pack"
PACK_SOURCE = Path(__file__).resolve().parents[3] / "extensions" / "examples" / "extdemo-pack"


def _host_with_pack(tmp_path: Path) -> ExtensionHost:
    target = tmp_path / "extdemo-pack"
    shutil.copytree(PACK_SOURCE, target)
    host = ExtensionHost(
        tool_registry=ToolRegistry(),
        policy=HostPolicy(roots=(tmp_path,), builtin_ids=frozenset({EXTENSION_ID})),
    )
    host.discover()
    return host


class TestCertifyExtension:
    def test_example_pack_certifies_green(self, tmp_path):
        host = _host_with_pack(tmp_path)
        report = certify_extension(host, EXTENSION_ID)
        assert report["certified"] is True, report["checks"]
        names = [c["check"] for c in report["checks"]]
        assert names == [
            "manifest_contract",
            "api_compatible",
            "dependency_constraints",
            "signature",
            "sbom_secret_scan",
            "package_layout",
            "resource_budget_declared",
            "protocol_compat",
            "provider_conformance",
            "execution_mode",
            "lifecycle_smoke",
            "deactivate_clean",
        ]
        # smoke 后状态恢复（COMPATIBLE，投影零残留）。
        record = host.get_record(EXTENSION_ID)
        assert record is not None and record.state.value == "compatible"
        host.reset()

    def test_report_is_deterministic(self, tmp_path):
        host = _host_with_pack(tmp_path)
        one = certify_extension(host, EXTENSION_ID)
        two = certify_extension(host, EXTENSION_ID)
        assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
        host.reset()

    def test_unknown_extension_single_fail(self, tmp_path):
        host = _host_with_pack(tmp_path)
        report = certify_extension(host, "ghost.pack")
        assert report["certified"] is False
        assert report["checks"][0]["status"] == "fail"
        host.reset()

    def test_secret_planted_in_pack_fails_certification(self, tmp_path):
        host = _host_with_pack(tmp_path)
        record = host.get_record(EXTENSION_ID)
        assert record is not None
        (record.path / "creds.py").write_text(
            'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8"
        )
        host.reload(EXTENSION_ID, activate=False)
        report = certify_extension(host, EXTENSION_ID)
        assert report["certified"] is False
        scan = next(c for c in report["checks"] if c["check"] == "sbom_secret_scan")
        assert scan["status"] == "fail"
        assert "aws_access_key" in scan["detail"]
        host.reset()

    def test_activation_failure_fails_smoke(self, tmp_path):
        host = _host_with_pack(tmp_path)
        record = host.get_record(EXTENSION_ID)
        assert record is not None
        # 破坏入口：activate 期崩溃 → smoke fail。
        main_path = record.path / "main.py"
        main_path.write_text(main_path.read_text() + "\n    raise RuntimeError('boom')\n", encoding="utf-8")
        host.reload(EXTENSION_ID, activate=False)
        report = certify_extension(host, EXTENSION_ID)
        assert report["certified"] is False
        smoke = next(c for c in report["checks"] if c["check"] == "lifecycle_smoke")
        assert smoke["status"] == "fail"
        host.reset()


class TestCliCertify:
    def test_cli_json_report_green(self, tmp_path, capsys, monkeypatch):
        target = tmp_path / "extdemo-pack"
        shutil.copytree(PACK_SOURCE, target)
        # CLI 经 settings 构造 host：点名信任以通过激活门。
        monkeypatch.setattr(
            "app.core.config.settings.EXTENSIONS_BUILTIN_IDS", EXTENSION_ID, raising=False
        )
        argv = ["certify", "--root", str(tmp_path), "--json", EXTENSION_ID]
        code = cli_main(argv)
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["certified"] is True
        assert payload["extension_id"] == EXTENSION_ID

    def test_cli_exit_code_reflects_failure(self, tmp_path, capsys):
        host = _host_with_pack(tmp_path)
        record = host.get_record(EXTENSION_ID)
        assert record is not None
        (record.path / "creds.py").write_text(
            'KEY = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8"
        )
        host.reload(EXTENSION_ID, activate=False)
        host.reset()
        code = cli_main(["certify", "--root", str(tmp_path), "--json", EXTENSION_ID])
        assert code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["certified"] is False

    def test_cli_human_output(self, tmp_path, capsys, monkeypatch):
        target = tmp_path / "extdemo-pack"
        shutil.copytree(PACK_SOURCE, target)
        monkeypatch.setattr(
            "app.core.config.settings.EXTENSIONS_BUILTIN_IDS", EXTENSION_ID, raising=False
        )
        code = cli_main(["certify", "--root", str(tmp_path), EXTENSION_ID])
        assert code == 0
        out = capsys.readouterr().out
        assert "certified: True" in out
        assert "lifecycle_smoke" in out

