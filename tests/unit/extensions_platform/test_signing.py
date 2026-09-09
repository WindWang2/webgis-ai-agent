"""扩展包签名/验签测试（ADR-0105 / Wave 6）。

覆盖三层：sign_pack/verify_pack_signature 纯函数契约、内容指纹对
signature.json 的排除（反循环依赖）、host discover() 的验签信任流
（篡改隔离 / 提权 / 告警 / V1 行为保持）。CLI 用例直接驱动
:func:`app.extensions_platform.cli.main`（不启子进程、不碰网络），
扩展目录构造方式与 test_host_lifecycle.py 一致。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extensions_platform.cli import main as cli_main
from app.extensions_platform.diagnostics import (
    DiagnosticCode,
    ExtensionPlatformError,
)
from app.extensions_platform.discovery import (
    FINGERPRINT_MAX_FILES,
    SIGNATURE_FILENAME,
    compute_fingerprint,
)
from app.extensions_platform.host import ExtensionHost, ExtensionState, HostPolicy
from app.extensions_platform.signing import (
    SIGNATURE_ALGORITHM,
    sign_pack,
    verify_pack_signature,
)
from app.tools.registry import ToolRegistry

# ---------------------------------------------------------------- helpers

KEY_ID = "acme"
KEY_BYTES = b"wave-6-test-signing-key\n"  # 任意确定性字节即 HMAC 密钥

_SIGNATURE_CODES = {
    DiagnosticCode.SIGNATURE_INVALID,
    DiagnosticCode.SIGNATURE_VERIFIED,
    DiagnosticCode.PUBLISHER_UNTRUSTED,
    DiagnosticCode.PACKAGE_TAMPERED,
}


def _write_key(tmp_path: Path) -> Path:
    key_file = tmp_path / "keys" / f"{KEY_ID}.key"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(KEY_BYTES)
    return key_file


def _write_pack(root: Path, ns: str = "acme", name: str = "pack") -> Path:
    """最小可激活工具扩展（与 test_host_lifecycle 的合成包同构）。"""
    ext_dir = root / f"{ns}-{name}"
    ext_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": f"{ns}.{name}",
        "name": name,
        "namespace": ns,
        "version": "1.0.0",
        "entry_point": "main",
        "description": "synthetic extension",
        "tools": [{"name": "synth_double", "description": "declared"}],
    }
    (ext_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (ext_dir / "main.py").write_text(
        "from app.extensions_platform.sdk import ToolExtensionSpec\n"
        "\n"
        "\n"
        "def _run(x: float) -> dict:\n"
        "    return {'doubled': x * 2}\n"
        "\n"
        "\n"
        "def activate(ctx):\n"
        "    ctx.register_tool(ToolExtensionSpec(\n"
        "        name='synth_double',\n"
        "        description='Double a number.',\n"
        "        func=_run,\n"
        "        side_effect='pure',\n"
        "        deterministic=True,\n"
        "        param_descriptions={'x': 'number to double'},\n"
        "    ))\n",
        encoding="utf-8",
    )
    return ext_dir


def _sign(pack_dir: Path, key_file: Path, key_id: str = KEY_ID) -> dict:
    return sign_pack(pack_dir, key_id, key_file)


def _host(tmp_path: Path, **policy_extra) -> ExtensionHost:
    host = ExtensionHost(
        tool_registry=ToolRegistry(),
        policy=HostPolicy(roots=(tmp_path,), **policy_extra),
    )
    host.discover()
    return host


# ---------------------------------------------------------------- sign_pack


class TestSignPack:
    def test_roundtrip_signed_verified(self, tmp_path):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        _sign(pack, key)
        status = verify_pack_signature(pack, {KEY_ID: key})
        assert status.status == "signed_verified"
        assert status.publisher == KEY_ID

    def test_signature_document_written_with_expected_fields(self, tmp_path):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        doc = _sign(pack, key)
        assert doc["algorithm"] == SIGNATURE_ALGORITHM == "hmac-sha256"
        assert doc["key_id"] == KEY_ID
        assert len(doc["fingerprint"]) == 64  # sha256 hex
        assert len(doc["signature"]) == 64  # hmac-sha256 hex
        assert "signed_at" in doc  # 纯信息性，不参与验签
        on_disk = json.loads((pack / SIGNATURE_FILENAME).read_text(encoding="utf-8"))
        assert on_disk["signature"] == doc["signature"]

    def test_signing_is_deterministic_except_signed_at(self, tmp_path):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        first = _sign(pack, key)
        second = _sign(pack, key)
        # signed_at 纯信息性（允许不同），不参与比较；其余字段确定性。
        for doc in (first, second):
            doc.pop("signed_at")
        assert first == second

    def test_unreadable_key_file_is_typed_error_and_writes_nothing(self, tmp_path):
        pack = _write_pack(tmp_path)
        with pytest.raises(ExtensionPlatformError) as exc_info:
            sign_pack(pack, KEY_ID, tmp_path / "keys" / "missing.key")
        assert exc_info.value.diagnostic.code is DiagnosticCode.MANIFEST_PARSE_FAILED
        assert not (pack / SIGNATURE_FILENAME).exists()

    def test_unfingerprintable_pack_is_typed_error(self, tmp_path):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        for i in range(FINGERPRINT_MAX_FILES + 1):
            (pack / f"f{i}.txt").write_text("x", encoding="utf-8")
        with pytest.raises(ExtensionPlatformError) as exc_info:
            _sign(pack, key)
        assert exc_info.value.diagnostic.code is DiagnosticCode.MANIFEST_PARSE_FAILED


# ---------------------------------------------------- verify_pack_signature


class TestVerifyPackSignature:
    def test_missing_signature(self, tmp_path):
        pack = _write_pack(tmp_path)
        status = verify_pack_signature(pack, {})
        assert status.status == "missing"
        assert status.publisher is None

    def test_tampered_content_after_signing(self, tmp_path):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        _sign(pack, key)
        (pack / "main.py").write_text("x = 1\n", encoding="utf-8")  # 签名后改内容
        status = verify_pack_signature(pack, {KEY_ID: key})
        assert status.status == "tampered"
        assert status.publisher == KEY_ID

    def test_edited_signature_hmac_is_invalid(self, tmp_path):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        _sign(pack, key)
        sig_path = pack / SIGNATURE_FILENAME
        doc = json.loads(sig_path.read_text(encoding="utf-8"))
        doc["signature"] = "0" * 64  # 内容未变，签名对不上 → 伪造/换钥
        sig_path.write_text(json.dumps(doc), encoding="utf-8")
        status = verify_pack_signature(pack, {KEY_ID: key})
        assert status.status == "invalid"

    def test_malformed_signature_json_is_invalid(self, tmp_path):
        pack = _write_pack(tmp_path)
        (pack / SIGNATURE_FILENAME).write_text("{ not json", encoding="utf-8")
        assert verify_pack_signature(pack, {}).status == "invalid"

    def test_signature_missing_field_is_invalid(self, tmp_path):
        pack = _write_pack(tmp_path)
        (pack / SIGNATURE_FILENAME).write_text(
            json.dumps({"algorithm": "hmac-sha256", "key_id": "acme"}),
            encoding="utf-8",
        )
        assert verify_pack_signature(pack, {}).status == "invalid"

    def test_unknown_algorithm_is_invalid(self, tmp_path):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        _sign(pack, key)
        sig_path = pack / SIGNATURE_FILENAME
        doc = json.loads(sig_path.read_text(encoding="utf-8"))
        doc["algorithm"] = "rsa-sha256"
        sig_path.write_text(json.dumps(doc), encoding="utf-8")
        status = verify_pack_signature(pack, {KEY_ID: key})
        assert status.status == "invalid"

    def test_unknown_publisher_is_signed_untrusted(self, tmp_path):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        _sign(pack, key)
        status = verify_pack_signature(pack, {})  # 空受信集合
        assert status.status == "signed_untrusted"
        assert status.publisher == KEY_ID
        assert status.detail == "publisher not in trusted set"


# ------------------------------------------------------- fingerprint 排除


class TestFingerprintExclusion:
    def test_adding_signature_json_does_not_change_fingerprint(self, tmp_path):
        # 签名覆盖内容，内容指纹不得覆盖签名（循环依赖）→ 落盘
        # signature.json 前后指纹必须一致。
        pack = _write_pack(tmp_path)
        before, diag = compute_fingerprint(pack)
        assert diag is None and before
        (pack / SIGNATURE_FILENAME).write_text('{"bogus": true}\n', encoding="utf-8")
        after, diag = compute_fingerprint(pack)
        assert diag is None
        assert after == before


# --------------------------------------------------- host 验签信任流


class TestHostSignatureFlow:
    def test_tampered_pack_quarantined_even_if_allowlisted(self, tmp_path):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        _sign(pack, key)
        (pack / "main.py").write_text("x = 1\n", encoding="utf-8")
        host = _host(
            tmp_path,
            trusted_publishers={KEY_ID: key},
            allow=frozenset({"acme.pack"}),  # allowlist 也救不回被篡改的包
        )
        record = host.get_record("acme.pack")
        assert record is not None and record.state is ExtensionState.QUARANTINED
        assert any(
            d.code is DiagnosticCode.PACKAGE_TAMPERED and d.severity.value == "error"
            for d in record.diagnostics
        )
        assert host.activate("acme.pack") != []  # 激活被拒
        assert record.state is ExtensionState.QUARANTINED

    def test_trusted_publisher_elevates_trust_without_allowlist(self, tmp_path):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        _sign(pack, key)
        host = _host(
            tmp_path,
            trusted_publishers={KEY_ID: key},
            trust_signed=True,
            # 注意：无 allow / 无 EXTENSIONS_ACTIVATE_UNTRUSTED。
        )
        report = host.status_report()["extensions"][0]
        assert report["trust"] == "trusted_extension"
        assert any(
            d["code"] == "signature_verified" and d["severity"] == "info"
            for d in report["diagnostics"]
        )
        host.activate("acme.pack")
        assert host.get_record("acme.pack").state is ExtensionState.ACTIVE

    def test_broken_signature_quarantines(self, tmp_path):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        _sign(pack, key)
        sig_path = pack / SIGNATURE_FILENAME
        doc = json.loads(sig_path.read_text(encoding="utf-8"))
        doc["signature"] = "f" * 64
        sig_path.write_text(json.dumps(doc), encoding="utf-8")
        host = _host(tmp_path, trusted_publishers={KEY_ID: key}, trust_signed=True)
        record = host.get_record("acme.pack")
        assert record is not None and record.state is ExtensionState.QUARANTINED
        assert any(
            d.code is DiagnosticCode.SIGNATURE_INVALID and d.severity.value == "error"
            for d in record.diagnostics
        )
        assert host.activate("acme.pack") != []

    def test_default_flags_keep_v1_behavior(self, tmp_path):
        # 无签名 + 缺省策略：无签名诊断、无隔离，行为与 V1 逐字节一致。
        _write_pack(tmp_path)
        host = _host(tmp_path)
        report = host.status_report()["extensions"][0]
        assert report["trust"] == "local_untrusted"
        assert not any(
            DiagnosticCode(d["code"]) in _SIGNATURE_CODES
            for d in report["diagnostics"]
        )
        host.activate("acme.pack")
        assert host.get_record("acme.pack").state is ExtensionState.ACTIVE

    def test_allow_unsigned_dev_warns(self, tmp_path):
        _write_pack(tmp_path)
        host = _host(tmp_path, allow_unsigned_dev=True)
        record = host.get_record("acme.pack")
        assert record is not None
        assert record.trust.value == "local_untrusted"
        assert any(
            d.code is DiagnosticCode.SIGNATURE_INVALID
            and d.severity.value == "warning"
            and "EXTENSIONS_ALLOW_UNSIGNED_DEV" in d.message
            for d in record.diagnostics
        )

    def test_trust_signed_unsigned_pack_warns_and_stays_local(self, tmp_path):
        _write_pack(tmp_path)
        host = _host(tmp_path, trust_signed=True)
        record = host.get_record("acme.pack")
        assert record is not None
        assert record.trust.value == "local_untrusted"
        assert any(
            d.code is DiagnosticCode.SIGNATURE_INVALID
            and d.severity.value == "warning"
            and "EXTENSIONS_TRUST_SIGNED" in d.message
            for d in record.diagnostics
        )

    def test_signed_untrusted_publisher_warns_not_quarantines(self, tmp_path):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        _sign(pack, key, key_id="someone-else")
        host = _host(
            tmp_path,
            trusted_publishers={"other": key},
            trust_signed=True,
        )
        record = host.get_record("acme.pack")
        assert record is not None
        assert record.state is ExtensionState.COMPATIBLE  # 不隔离
        assert record.trust.value == "local_untrusted"  # 不提权
        assert any(
            d.code is DiagnosticCode.PUBLISHER_UNTRUSTED
            and d.severity.value == "warning"
            for d in record.diagnostics
        )

    def test_blocked_extension_skips_signature_flow(self, tmp_path):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        _sign(pack, key)
        host = _host(
            tmp_path,
            block=frozenset({"acme.pack"}),
            trusted_publishers={KEY_ID: key},
            trust_signed=True,
        )
        record = host.get_record("acme.pack")
        assert record is not None and record.state is ExtensionState.QUARANTINED
        codes = {d.code for d in record.diagnostics}
        assert codes == {DiagnosticCode.TRUST_BLOCKED}  # 签名诊断不掺和


# ---------------------------------------------------------------- CLI


class TestSigningCli:
    def test_package_then_verify_roundtrip(self, tmp_path, capsys):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        rc = cli_main(
            ["package", str(pack), "--key-id", KEY_ID, "--key-file", str(key), "--json"]
        )
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)  # stdout 必须是纯 JSON
        assert payload["pack"] == str(pack)
        assert payload["key_id"] == KEY_ID
        assert len(payload["fingerprint"]) == 64
        assert payload["signature_file"] == str(pack / SIGNATURE_FILENAME)
        assert KEY_BYTES.decode() not in out  # 密钥材料绝不出现

        rc = cli_main(
            ["verify", str(pack), "--json", "--publisher", f"{KEY_ID}:{key}"]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == {
            "pack": str(pack),
            "status": "signed_verified",
            "publisher": KEY_ID,
            "detail": "",
        }

    def test_verify_publisher_repeatable_and_comma_separated(self, tmp_path, capsys):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        _sign(pack, key)
        rc = cli_main(
            [
                "verify", str(pack), "--json",
                "--publisher", f"other:{key}",
                "--publisher", f"{KEY_ID}:{key},third:{key}",
            ]
        )
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["status"] == "signed_verified"

    def test_verify_exit_code_contract(self, tmp_path, capsys):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        # missing → 0（未签名由宿主策略告警，不算 CLI 失败）。
        assert cli_main(["verify", str(pack), "--json", "--publisher", f"{KEY_ID}:{key}"]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "missing"
        # signed_untrusted → 1。
        _sign(pack, key, key_id="someone-else")
        assert cli_main(["verify", str(pack), "--json", "--publisher", f"{KEY_ID}:{key}"]) == 1
        assert json.loads(capsys.readouterr().out)["status"] == "signed_untrusted"
        # tampered → 1。
        _sign(pack, key)
        (pack / "main.py").write_text("x = 1\n", encoding="utf-8")
        assert cli_main(["verify", str(pack), "--json", "--publisher", f"{KEY_ID}:{key}"]) == 1
        assert json.loads(capsys.readouterr().out)["status"] == "tampered"

    def test_bad_publisher_spec_and_missing_key_fail_closed(self, tmp_path, capsys):
        pack = _write_pack(tmp_path)
        key = _write_key(tmp_path)
        # key_id 词表非法 → settings_bridge fail closed → exit 1。
        rc = cli_main(["verify", str(pack), "--json", "--publisher", f"BAD:{key}"])
        assert rc == 1
        assert "error" in capsys.readouterr().err
        # 密钥文件不可读 → typed 异常 → exit 1，且不落 signature.json。
        rc = cli_main(
            ["package", str(pack), "--key-id", KEY_ID, "--key-file", "/nonexistent.key"]
        )
        assert rc == 1
        assert not (pack / SIGNATURE_FILENAME).exists()
