"""发现与指纹测试（ADR-0104 Wave 2）：有界、确定性、fail-closed-per-extension。"""

from __future__ import annotations

import json
from pathlib import Path

from app.extensions_platform.discovery import (
    FINGERPRINT_MAX_FILES,
    discover_extensions,
    compute_fingerprint,
)
from app.extensions_platform.diagnostics import DiagnosticCode


def _write_extension(root: Path, ns: str, name: str, **manifest_extra):
    ext_dir = root / f"{ns}-{name}"
    ext_dir.mkdir(parents=True)
    manifest = {
        "id": f"{ns}.{name}",
        "name": name,
        "namespace": ns,
        "version": "1.0.0",
        "entry_point": "main",
        "description": "test extension",
    }
    manifest.update(manifest_extra)
    (ext_dir / "manifest.json").write_text(json.dumps(manifest))
    (ext_dir / "main.py").write_text("def activate(ctx):\n    return None\n")
    return ext_dir


class TestDiscovery:
    def test_discovers_wellformed_extensions_sorted_by_id(self, tmp_path):
        _write_extension(tmp_path, "zeta", "pack")
        _write_extension(tmp_path, "alpha", "pack")
        result = discover_extensions([tmp_path])
        assert [e.extension_id for e in result.extensions] == ["alpha.pack", "zeta.pack"]
        assert result.extensions[0].manifest.entry_point == "main"

    def test_bad_manifest_fails_alone(self, tmp_path):
        _write_extension(tmp_path, "good", "pack")
        bad = tmp_path / "bad-pack"
        bad.mkdir()
        (bad / "manifest.json").write_text("{not json")
        result = discover_extensions([tmp_path])
        assert len(result.extensions) == 1
        assert len(result.failures) == 1
        assert result.failures[0].diagnostics[0].code is DiagnosticCode.MANIFEST_PARSE_FAILED

    def test_duplicate_id_first_path_wins(self, tmp_path):
        import shutil

        original = _write_extension(tmp_path, "acme", "pack")
        # 不同目录、相同 id（拷贝场景）：按排序路径先到先得，后者隔离。
        shutil.copytree(original, tmp_path / "aaa-duplicate")
        result = discover_extensions([tmp_path])
        assert len(result.extensions) == 1
        assert any(f.diagnostics[0].code is DiagnosticCode.ID_COLLISION for f in result.failures)

    def test_discovery_limit_enforced(self, tmp_path):
        for i in range(5):
            _write_extension(tmp_path, f"ns{i}", "pack")
        result = discover_extensions([tmp_path], max_extensions=3)
        assert len(result.extensions) + len(result.failures) == 3
        assert any(d.code is DiagnosticCode.DISCOVERY_LIMIT_EXCEEDED for d in result.diagnostics)

    def test_missing_root_is_warning_not_error(self, tmp_path):
        result = discover_extensions([tmp_path / "nope"])
        assert result.extensions == ()
        assert any("not a directory" in d.message for d in result.diagnostics)


class TestFingerprint:
    def test_fingerprint_deterministic_and_content_sensitive(self, tmp_path):
        ext = _write_extension(tmp_path, "acme", "pack")
        fp1, diag = compute_fingerprint(ext)
        assert diag is None and fp1
        fp2, _ = compute_fingerprint(ext)
        assert fp1 == fp2
        (ext / "main.py").write_text("def activate(ctx):\n    return 42\n")
        fp3, _ = compute_fingerprint(ext)
        assert fp1 != fp3

    def test_fingerprint_refuses_huge_dirs(self, tmp_path):
        ext = _write_extension(tmp_path, "acme", "pack")
        for i in range(FINGERPRINT_MAX_FILES + 2):
            (ext / f"f{i}.txt").write_text("x")
        fp, diag = compute_fingerprint(ext)
        assert fp is None and diag is not None
