"""SBOM / provenance 测试（ADR-0105 / Wave 7）。

钉死三件事：确定性（同包同指纹 → 逐字节相同 JSON）、清单正确性
（文件/imports/依赖与真实包内容一致）、secret 扫描只报事实。CLI 用例
直接驱动 :func:`app.extensions_platform.cli.main`（不启子进程）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.extensions_platform.cli import main as cli_main
from app.extensions_platform.diagnostics import DiagnosticCode, ExtensionPlatformError
from app.extensions_platform.discovery import FINGERPRINT_MAX_FILES, compute_fingerprint
from app.extensions_platform.manifest import manifest_from_dict
from app.extensions_platform.sbom import build_sbom

# ---------------------------------------------------------------- helpers

BASE_MANIFEST: dict = {
    "schema_version": 1,
    "id": "acme.pack",
    "name": "pack",
    "namespace": "acme",
    "version": "1.0.0",
    "entry_point": "main",
    "description": "synthetic extension",
    "vendor": "ACME Corp",
    "permissions": ["network"],
}

IMPORTS_MAIN = '''\
import json
import numpy as np
from app.extensions_platform.sdk import ToolExtensionSpec
from .helpers import HELPER_VALUE


def activate(ctx):
    return None
'''


def _write_pack(
    root: Path,
    main_py: str = "def activate(ctx):\n    return None\n",
    manifest_extra: dict | None = None,
    manifest: dict | None = None,
) -> Path:
    ext_dir = root / "acme-pack"
    ext_dir.mkdir(parents=True, exist_ok=True)
    data = manifest or {**BASE_MANIFEST, **(manifest_extra or {})}
    (ext_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    (ext_dir / "main.py").write_text(main_py, encoding="utf-8")
    return ext_dir


def _sbom(pack_dir: Path) -> dict:
    manifest, err = manifest_from_dict(
        json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    )
    assert manifest is not None, err
    fingerprint, diag = compute_fingerprint(pack_dir)
    assert diag is None and fingerprint
    return build_sbom(pack_dir, manifest, fingerprint)


# ---------------------------------------------------------------- 确定性


class TestDeterminism:
    def test_two_builds_are_byte_identical(self, tmp_path):
        pack = _write_pack(
            tmp_path,
            manifest_extra={"dependencies": [{"id": "acme.helper"}]},
        )
        (pack / "sub").mkdir()
        (pack / "sub" / "data.txt").write_text("zeta\n", encoding="utf-8")
        first = _sbom(pack)
        second = _sbom(pack)
        assert first == second
        assert (
            json.dumps(first, sort_keys=True, indent=2)
            == json.dumps(second, sort_keys=True, indent=2)
        )

    def test_no_timestamps_anywhere(self, tmp_path):
        # 确定性的硬保证：payload 里不允许出现时间语义字段。
        payload = _sbom(_write_pack(tmp_path))
        assert "signed_at" not in json.dumps(payload)
        assert "timestamp" not in json.dumps(payload)
        assert "generated_at" not in json.dumps(payload)


# ---------------------------------------------------------------- 清单正确性


class TestInventory:
    def test_file_paths_bytes_and_sha256(self, tmp_path):
        pack = _write_pack(tmp_path)
        (pack / "helpers.py").write_text("HELPER_VALUE = 1\n", encoding="utf-8")
        (pack / "sub").mkdir()
        blob = bytes(range(256))
        (pack / "sub" / "data.bin").write_bytes(blob)
        payload = _sbom(pack)
        entries = {f["path"]: f for f in payload["files"]}
        assert sorted(entries) == ["helpers.py", "main.py", "manifest.json", "sub/data.bin"]
        assert [f["path"] for f in payload["files"]] == sorted(entries)  # 按 path 排序
        assert entries["sub/data.bin"]["bytes"] == len(blob)
        assert entries["sub/data.bin"]["sha256"] == hashlib.sha256(blob).hexdigest()
        main_bytes = (pack / "main.py").read_bytes()
        assert entries["main.py"]["sha256"] == hashlib.sha256(main_bytes).hexdigest()

    def test_signature_json_excluded_from_inventory(self, tmp_path):
        # SBOM 描述的是签名所覆盖的内容；signature.json 不进清单（其中的
        # signed_at 若进清单会破坏确定性），但仍在 secret 扫描面内。
        pack = _write_pack(tmp_path)
        (pack / "signature.json").write_text('{"signed_at": "2026-01-01"}\n', encoding="utf-8")
        payload = _sbom(pack)
        assert all(f["path"] != "signature.json" for f in payload["files"])

    def test_metadata_and_dependency_sections(self, tmp_path):
        # version 约束是 V2 特性 → manifest 需 api_version >= 1.1.0。
        pack = _write_pack(
            tmp_path,
            manifest_extra={
                "api_version": "1.1.0",
                "dependencies": [{"id": "acme.helper", "version": ">=1.2,<2.0"}],
                "optional_dependencies": [{"id": "acme.extra", "required": False}],
            },
        )
        payload = _sbom(pack)
        assert payload["sbom_version"] == "1"
        assert payload["extension_id"] == "acme.pack"
        assert payload["namespace"] == "acme"
        assert payload["vendor"] == "ACME Corp"
        assert payload["permissions"] == ["network"]
        assert payload["trust_declared"] == "local_untrusted"
        assert payload["dependencies"] == [
            {"id": "acme.extra", "required": False, "version": None, "optional": True},
            {"id": "acme.helper", "required": True, "version": ">=1.2,<2.0", "optional": False},
        ]

    def test_python_imports_exclude_stdlib_sdk_and_relative(self, tmp_path):
        pack = _write_pack(tmp_path, main_py=IMPORTS_MAIN)
        (pack / "helpers.py").write_text("HELPER_VALUE = 1\n", encoding="utf-8")
        payload = _sbom(pack)
        # json = 标准库；app.* = 平台 SDK 通道面；相对导入 = 包内兄弟模块；
        # 只剩真正的第三方依赖。
        assert payload["python_imports"] == ["numpy"]

    def test_unparseable_py_is_skipped_not_fatal(self, tmp_path):
        pack = _write_pack(tmp_path, main_py="def broken(:\n")
        payload = _sbom(pack)
        assert payload["python_imports"] == []


# ---------------------------------------------------------------- secret 扫描


class TestSecretScan:
    def test_aws_key_shape_reported(self, tmp_path):
        dirty = (
            "AWS_KEY = 'AKIAABCDEFGHIJKLMNOP'\n"
            "\n"
            "\n"
            "def activate(ctx):\n"
            "    return None\n"
        )
        payload = _sbom(_write_pack(tmp_path, main_py=dirty))
        assert payload["secret_scan"]["clean"] is False
        assert payload["secret_scan"]["findings"] == [
            {"file": "main.py", "kind": "aws_access_key"}
        ]

    def test_clean_pack_is_clean(self, tmp_path):
        payload = _sbom(_write_pack(tmp_path))
        assert payload["secret_scan"] == {"clean": True, "findings": []}

    def test_signature_json_is_also_scanned(self, tmp_path):
        pack = _write_pack(tmp_path)
        token = "ghp_" + "a" * 36
        (pack / "signature.json").write_text(
            json.dumps({"signature": token}), encoding="utf-8"
        )
        payload = _sbom(pack)
        assert payload["secret_scan"]["findings"] == [
            {"file": "signature.json", "kind": "github_token"}
        ]

    def test_binary_files_are_skipped(self, tmp_path):
        pack = _write_pack(tmp_path)
        # 二进制噪声里埋 AWS key 形状（utf-8 不可解码 → 不扫，不误报）。
        (pack / "blob.bin").write_bytes(b"\xff\xfe\x00AKIA" + b"\x00" * 20)
        payload = _sbom(pack)
        assert payload["secret_scan"]["clean"] is True


# ---------------------------------------------------------------- 上界


class TestBounds:
    def test_over_bound_pack_is_typed_refusal(self, tmp_path):
        pack = _write_pack(tmp_path)
        for i in range(FINGERPRINT_MAX_FILES + 2):
            (pack / f"f{i}.txt").write_text("x", encoding="utf-8")
        manifest, err = manifest_from_dict(
            json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
        )
        assert manifest is not None, err
        with pytest.raises(ExtensionPlatformError) as exc_info:
            build_sbom(pack, manifest, "0" * 64)
        assert exc_info.value.diagnostic.code is DiagnosticCode.FINGERPRINT_CHANGED


# ---------------------------------------------------------------- CLI


class TestSbomCli:
    def test_json_payload_for_discovered_extension(self, tmp_path, capsys):
        _write_pack(tmp_path, main_py=IMPORTS_MAIN)
        (tmp_path / "acme-pack" / "helpers.py").write_text("HELPER_VALUE = 1\n", encoding="utf-8")
        rc = cli_main(["sbom", "acme.pack", "--json", "--root", str(tmp_path)])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)  # stdout 必须是纯 JSON
        assert payload["extension_id"] == "acme.pack"
        assert payload["python_imports"] == ["numpy"]
        assert payload["secret_scan"]["clean"] is True
        assert len(payload["fingerprint"]) == 64

    def test_human_output_reports_secret_findings(self, tmp_path, capsys):
        dirty = "AWS_KEY = 'AKIAABCDEFGHIJKLMNOP'\n\n\ndef activate(ctx):\n    return None\n"
        _write_pack(tmp_path, main_py=dirty)
        rc = cli_main(["sbom", "acme.pack", "--root", str(tmp_path)])
        assert rc == 0  # secret 命中是事实陈述，不算 CLI 失败
        out = capsys.readouterr().out
        assert "secret_scan: FINDINGS" in out
        assert "aws_access_key: main.py" in out

    def test_unknown_id_exits_one_to_stderr(self, tmp_path, capsys):
        rc = cli_main(["sbom", "nope.missing", "--root", str(tmp_path)])
        assert rc == 1
        assert "not discovered" in capsys.readouterr().err
