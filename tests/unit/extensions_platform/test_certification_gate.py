"""认证 gate 与持久化报告信任模型测试（ADR-0201）。

覆盖：
- gate 默认关（零行为变更）；开启后无报告 → CERTIFICATION_REQUIRED、
  状态 FAILED（可重试）；builtin 豁免；
- 报告过期（包内容变更后指纹漂移）→ CERTIFICATION_STALE；
- evidence 模式接受未签名报告但如实产出 warning（不防篡改的诚实边界）；
- strict 模式：未签名拒绝、错钥拒绝、对钥通过（fail closed）；
- 报告 HMAC 用规范化 JSON（EOL/呈现形态解耦，autocrlf 安全）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extensions_platform.capability_certification import (
    load_certification_report,
    run_pack_certification,
    save_certification_report,
)
from app.extensions_platform.diagnostics import ExtensionPlatformError
from app.extensions_platform.host import ExtensionHost, ExtensionState

EXTENSION_ID = "certv4.pack"


def _make_key(tmp_path: Path, name: str = "cert.key") -> Path:
    key = tmp_path / name
    # 密钥材料必须随名字不同而不同（wrong-key 场景依赖这一点）。
    key.write_bytes(f"test-certification-key-material:{name}".encode("utf-8"))
    return key


class TestGateOff:
    def test_default_gate_off_activates_uncertified_pack(self, v4_env):
        """kill-switch 语义：默认关 → 未认证 pack 行为与旧版完全一致。"""
        v4_env.build()
        host, _ = v4_env.make_host()
        diagnostics = host.activate(EXTENSION_ID)
        assert diagnostics == []
        record = host.get_record(EXTENSION_ID)
        assert record.state is ExtensionState.ACTIVE
        host.deactivate(EXTENSION_ID)


class TestGateOn:
    def test_missing_report_blocks_activation(self, v4_env):
        v4_env.build()
        host, _ = v4_env.make_host(require_certified=True, builtin_ids=frozenset())
        diagnostics = host.activate(EXTENSION_ID)
        assert any(d.code.value == "certification_required" for d in diagnostics)
        record = host.get_record(EXTENSION_ID)
        assert record.state is ExtensionState.FAILED
        # FAILED 可重试：认证 + 报告落地后同一记录可再激活。
        report = run_pack_certification(host, EXTENSION_ID, save=True)
        assert report["certified"] is True
        host.discover()
        diagnostics = host.activate(EXTENSION_ID)
        assert all(d.severity.value != "error" for d in diagnostics)
        record = host.get_record(EXTENSION_ID)
        assert record.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED)
        host.deactivate(EXTENSION_ID)

    def test_valid_report_admits_activation(self, v4_env):
        v4_env.build()
        host, _ = v4_env.make_host(require_certified=True, builtin_ids=frozenset())
        report = run_pack_certification(host, EXTENSION_ID, save=True)
        assert report["certified"] is True
        host.discover()
        diagnostics = host.activate(EXTENSION_ID)
        assert [d for d in diagnostics if d.severity.value == "error"] == []
        record = host.get_record(EXTENSION_ID)
        # evidence 模式的未签名 warning 会让激活进入 DEGRADED（宿主警告
        # 语义）；strict + 对钥签名才是干净 ACTIVE。
        assert record.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED)
        host.deactivate(EXTENSION_ID)

    def test_builtin_ids_exempt_from_gate(self, v4_env):
        v4_env.build()
        host, _ = v4_env.make_host()  # builtin_ids 默认含本扩展
        diagnostics = host.activate(EXTENSION_ID)
        assert diagnostics == []
        host.deactivate(EXTENSION_ID)

    def test_stale_report_blocked_after_content_change(self, v4_env):
        v4_env.build()
        host, _ = v4_env.make_host(require_certified=True, builtin_ids=frozenset())
        report = run_pack_certification(host, EXTENSION_ID, save=True)
        assert report["certified"] is True
        # 认证后修改包内容（指纹漂移；.certification.json 不入指纹）。
        main_py = v4_env.tmp_path / "certv4-pack" / "main.py"
        main_py.write_text(main_py.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")
        host.discover()
        diagnostics = host.activate(EXTENSION_ID)
        assert any(d.code.value == "certification_stale" for d in diagnostics)
        record = host.get_record(EXTENSION_ID)
        assert record.state is ExtensionState.FAILED


class TestTrustModes:
    def test_evidence_mode_accepts_unsigned_with_warning(self, v4_env):
        v4_env.build()
        host, _ = v4_env.make_host(require_certified=True, builtin_ids=frozenset())  # 默认 evidence
        run_pack_certification(host, EXTENSION_ID, save=True)
        host.discover()
        diagnostics = host.activate(EXTENSION_ID)
        errors = [d for d in diagnostics if d.severity.value == "error"]
        warnings = [d for d in diagnostics if d.severity.value == "warning"]
        assert errors == []
        assert any("UNSIGNED" in w.message for w in warnings)
        host.deactivate(EXTENSION_ID)

    def test_evidence_mode_signed_report_with_key_is_verified(self, v4_env):
        """P3：evidence 模式遇到带 HMAC 的报告且密钥可用 → 顺手验签；
        篡改升级为硬失败而不是仅 warning。"""
        v4_env.build()
        key = _make_key(v4_env.tmp_path)
        host, _ = v4_env.make_host(
            require_certified=True, certification_key=key, builtin_ids=frozenset(),
        )
        report = run_pack_certification(host, EXTENSION_ID, save=True, sign_key_file=key)
        assert report["certified"] is True
        host.discover()
        diagnostics = host.activate(EXTENSION_ID)
        assert [d for d in diagnostics if d.severity.value == "error"] == []
        assert [d for d in diagnostics if d.severity.value == "warning"] == []
        host.deactivate(EXTENSION_ID)

    def test_evidence_mode_signed_report_without_key_warns_not_verified(self, v4_env):
        from app.extensions_platform.discovery import compute_fingerprint
        from app.extensions_platform.capability_certification import (
            load_certification_report,
        )

        v4_env.build()
        key = _make_key(v4_env.tmp_path)
        host, _ = v4_env.make_host()
        run_pack_certification(host, EXTENSION_ID, save=True, sign_key_file=key)
        pack_dir = v4_env.tmp_path / "certv4-pack"
        fingerprint, _ = compute_fingerprint(pack_dir)
        report, diag = load_certification_report(pack_dir, fingerprint, mode="evidence")
        assert report is not None
        assert diag is not None and "NOT verified" in diag.message

    def test_evidence_mode_tampered_hmac_report_rejected_when_key_available(
        self, v4_env
    ):
        from app.extensions_platform.discovery import compute_fingerprint
        from app.extensions_platform.capability_certification import (
            load_certification_report,
        )

        v4_env.build()
        key = _make_key(v4_env.tmp_path)
        host, _ = v4_env.make_host()
        run_pack_certification(host, EXTENSION_ID, save=True, sign_key_file=key)
        pack_dir = v4_env.tmp_path / "certv4-pack"
        path = pack_dir / ".certification.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["certified"] = True
        doc["fingerprint"] = doc["fingerprint"]  # 内容改动（execution_mode）
        doc["execution_mode"] = "worker"
        path.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
        fingerprint, _ = compute_fingerprint(pack_dir)
        report, diag = load_certification_report(
            pack_dir, fingerprint, mode="evidence", hmac_key_file=key,
        )
        assert report is None
        assert "HMAC" in diag.message

    def test_strict_mode_rejects_unsigned_report(self, v4_env):
        v4_env.build()
        host, _ = v4_env.make_host(
            require_certified=True, certification_trust="strict",
            builtin_ids=frozenset(),
        )
        run_pack_certification(host, EXTENSION_ID, save=True)  # 无 sign_key
        host.discover()
        diagnostics = host.activate(EXTENSION_ID)
        assert any(
            d.code.value == "certification_invalid" and "signed" in d.message
            for d in diagnostics
        )

    def test_strict_mode_rejects_wrong_key(self, v4_env):
        v4_env.build()
        good_key = _make_key(v4_env.tmp_path, "good.key")
        bad_key = _make_key(v4_env.tmp_path, "bad.key")
        host, _ = v4_env.make_host(
            require_certified=True, certification_trust="strict",
            builtin_ids=frozenset(),
        )
        report = run_pack_certification(host, EXTENSION_ID, save=True, sign_key_file=good_key)
        assert report["certified"] is True
        # 同一 HostPolicy 是 frozen dataclass：注入验签密钥走签发侧对应物。
        from app.extensions_platform.host import HostPolicy

        host_bad = ExtensionHost(
            tool_registry=host._tool_registry,
            policy=HostPolicy(
                roots=(v4_env.tmp_path,),
                require_certified=True,
                certification_trust="strict",
                certification_key=bad_key,
                builtin_ids=frozenset(),
            ),
        )
        host_bad.discover()
        diagnostics = host_bad.activate(EXTENSION_ID)
        assert any(
            d.code.value == "certification_invalid" and "HMAC" in d.message
            for d in diagnostics
        )

    def test_strict_mode_accepts_matching_key(self, v4_env):
        v4_env.build()
        key = _make_key(v4_env.tmp_path)
        host, _ = v4_env.make_host(
            require_certified=True, certification_trust="strict",
            certification_key=key, builtin_ids=frozenset(),
        )
        report = run_pack_certification(host, EXTENSION_ID, save=True, sign_key_file=key)
        assert report["certified"] is True
        host.discover()
        diagnostics = host.activate(EXTENSION_ID)
        assert [d for d in diagnostics if d.severity.value == "error"] == []
        record = host.get_record(EXTENSION_ID)
        assert record.state is ExtensionState.ACTIVE
        host.deactivate(EXTENSION_ID)

    def test_strict_without_key_configured_fails_closed(self, v4_env):
        v4_env.build()
        key = _make_key(v4_env.tmp_path)
        host, _ = v4_env.make_host(
            require_certified=True, certification_trust="strict",
            builtin_ids=frozenset(),
        )
        run_pack_certification(host, EXTENSION_ID, save=True, sign_key_file=key)
        host.discover()
        diagnostics = host.activate(EXTENSION_ID)
        assert any(
            d.code.value == "certification_invalid" and "key" in d.message
            for d in diagnostics
        )


class TestPersistence:
    def test_report_excluded_from_fingerprint(self, v4_env):
        """.certification.json 不入指纹：save 前后指纹稳定（gate 可用性）。"""
        from app.extensions_platform.discovery import compute_fingerprint

        v4_env.build()
        pack_dir = v4_env.tmp_path / "certv4-pack"
        before, _ = compute_fingerprint(pack_dir)
        host, _ = v4_env.make_host()
        report = run_pack_certification(host, EXTENSION_ID, save=True)
        assert report["certified"] is True
        after, _ = compute_fingerprint(pack_dir)
        assert before == after
        assert (pack_dir / ".certification.json").is_file()

    def test_load_roundtrip_and_stale_detection(self, v4_env):
        from app.extensions_platform.discovery import compute_fingerprint

        v4_env.build()
        pack_dir = v4_env.tmp_path / "certv4-pack"
        host, _ = v4_env.make_host()
        run_pack_certification(host, EXTENSION_ID, save=True)
        fingerprint, _ = compute_fingerprint(pack_dir)
        report, diag = load_certification_report(pack_dir, fingerprint, mode="evidence")
        assert report is not None and report["certified"] is True
        stale, stale_diag = load_certification_report(pack_dir, "0" * 64, mode="evidence")
        assert stale is None
        assert stale_diag.code.value == "certification_stale"

    def test_hmac_covers_canonical_json_not_file_bytes(self, v4_env):
        """EOL 安全（commit 017d1d41 教训）：重排 JSON 键呈现不影响验签。"""
        from app.extensions_platform.discovery import compute_fingerprint

        v4_env.build()
        pack_dir = v4_env.tmp_path / "certv4-pack"
        key = _make_key(v4_env.tmp_path)
        host, _ = v4_env.make_host()
        run_pack_certification(host, EXTENSION_ID, save=True, sign_key_file=key)
        fingerprint, _ = compute_fingerprint(pack_dir)
        # 以「键序不同」的呈现形态重写同一份报告（内容等价）。
        path = pack_dir / ".certification.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        reordered = dict(reversed(list(doc.items())))
        path.write_text(json.dumps(reordered, indent=1), encoding="utf-8")
        report, diag = load_certification_report(
            pack_dir, fingerprint, mode="strict", hmac_key_file=key,
        )
        assert report is not None, diag
        assert report["certified"] is True

    def test_save_without_fingerprint_computes_one(self, v4_env):
        from app.extensions_platform.discovery import compute_fingerprint

        v4_env.build()
        pack_dir = v4_env.tmp_path / "certv4-pack"
        doc = save_certification_report(pack_dir, {"schema_version": 1, "certified": True})
        assert doc["fingerprint"] == compute_fingerprint(pack_dir)[0]

    def test_unwritable_key_raises_typed(self, v4_env):
        v4_env.build()
        pack_dir = v4_env.tmp_path / "certv4-pack"
        with pytest.raises(ExtensionPlatformError):
            save_certification_report(
                pack_dir, {"schema_version": 1},
                sign_key_file=v4_env.tmp_path / "missing.key",
            )

    def test_tampered_certified_field_detected_under_strict(self, v4_env):
        """伪造 certified=true：strict 模式 HMAC 失配拒绝。"""
        from app.extensions_platform.discovery import compute_fingerprint

        v4_env.build()
        pack_dir = v4_env.tmp_path / "certv4-pack"
        key = _make_key(v4_env.tmp_path)
        host, _ = v4_env.make_host()
        run_pack_certification(host, EXTENSION_ID, save=True, sign_key_file=key)
        path = pack_dir / ".certification.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["certified"] is True
        # 攻击者改一个小字段再保留原 HMAC → 失配。
        doc["execution_mode"] = "worker"
        path.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
        fingerprint, _ = compute_fingerprint(pack_dir)
        report, diag = load_certification_report(
            pack_dir, fingerprint, mode="strict", hmac_key_file=key,
        )
        assert report is None
        assert "HMAC" in diag.message
