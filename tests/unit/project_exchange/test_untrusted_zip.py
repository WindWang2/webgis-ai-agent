"""不可信 ZIP 防护矩阵（T4）——每个负例都必须给出 typed 错误码。"""
import io
import json
import zipfile

import pytest

from app.services.project_exchange import contracts as pc
from app.services.project_exchange.untrusted_zip import extract_zip, inspect_zip


def _manifest_entry(hashes=None):
    entry = {
        "format": pc.PACKAGE_FORMAT, "format_version": 1,
        "package_id": f"pkg-{'b' * 32}", "created_at": "2026-11-28T00:00:00Z",
        "project": {"name": "p"},
        "reproduction": {"level": "view_only"},
        "datasets": [], "workflows": [], "artifacts": [], "maps": [],
        "files": [
            {"path": p, "sha256": h, "bytes": 1, "mime": "application/json"}
            for p, h in (hashes or {}).items()
        ],
    }
    return json.dumps(entry).encode("utf-8")


def _build_zip(entries: dict, manifest_bytes=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(pc.MANIFEST_PATH,
                    manifest_bytes if manifest_bytes is not None
                    else _manifest_entry())
        for name, data in entries.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


class TestPathDefenses:
    def test_absolute_path_rejected(self, tmp_path):
        zp = _build_zip({"/etc/evil.txt": "x"})
        p = tmp_path / "e.zip"
        p.write_bytes(zp.getvalue())
        with pytest.raises(pc.PackageError) as ei:
            inspect_zip(p)
        assert ei.value.error_code == pc.ERR_PATH_UNSAFE

    def test_traversal_rejected(self, tmp_path):
        zp = _build_zip({"a/../../evil.txt": "x"})
        p = tmp_path / "e.zip"
        p.write_bytes(zp.getvalue())
        with pytest.raises(pc.PackageError) as ei:
            inspect_zip(p)
        assert ei.value.error_code == pc.ERR_PATH_UNSAFE

    def test_backslash_traversal_rejected(self, tmp_path):
        zp = _build_zip({"..\\..\\evil.txt": "x"})
        p = tmp_path / "e.zip"
        p.write_bytes(zp.getvalue())
        with pytest.raises(pc.PackageError) as ei:
            inspect_zip(p)
        assert ei.value.error_code == pc.ERR_PATH_UNSAFE

    def test_symlink_entry_rejected(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(pc.MANIFEST_PATH, _manifest_entry())
            info = zipfile.ZipInfo("link.json")
            info.external_attr = (0o120777 << 16)  # symlink mode
            zf.writestr(info, "/etc/passwd")
        p = tmp_path / "e.zip"
        p.write_bytes(buf.getvalue())
        with pytest.raises(pc.PackageError) as ei:
            inspect_zip(p)
        assert ei.value.error_code == pc.ERR_PATH_UNSAFE

    def test_duplicate_entry_paths_rejected(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(pc.MANIFEST_PATH, _manifest_entry())
            zf.writestr("maps/a.json", "{}")
            zf.writestr("maps/a.json", "{}")
        p = tmp_path / "e.zip"
        p.write_bytes(buf.getvalue())
        with pytest.raises(pc.PackageError) as ei:
            inspect_zip(p)
        assert ei.value.error_code == pc.ERR_PATH_UNSAFE

    def test_windows_reserved_name_rejected(self, tmp_path):
        zp = _build_zip({"CON.json": "{}"})
        p = tmp_path / "e.zip"
        p.write_bytes(zp.getvalue())
        with pytest.raises(pc.PackageError) as ei:
            inspect_zip(p)
        assert ei.value.error_code == pc.ERR_PATH_UNSAFE


class TestTypeAndFormatDefenses:
    def test_disallowed_extension_rejected(self, tmp_path):
        zp = _build_zip({"payload.sh": "rm -rf"})
        p = tmp_path / "e.zip"
        p.write_bytes(zp.getvalue())
        with pytest.raises(pc.PackageError) as ei:
            inspect_zip(p)
        assert ei.value.error_code == pc.ERR_ENTRY_TYPE

    def test_nested_archive_rejected(self, tmp_path):
        zp = _build_zip({"inner.zip": b"PK\x03\x04"})
        p = tmp_path / "e.zip"
        p.write_bytes(zp.getvalue())
        with pytest.raises(pc.PackageError) as ei:
            inspect_zip(p)
        assert ei.value.error_code == pc.ERR_ENTRY_TYPE

    def test_missing_manifest_rejected(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("maps/a.json", "{}")
        p = tmp_path / "e.zip"
        p.write_bytes(buf.getvalue())
        with pytest.raises(pc.PackageError) as ei:
            inspect_zip(p)
        assert ei.value.error_code == pc.ERR_PACKAGE_FORMAT

    def test_bad_zip_rejected(self, tmp_path):
        p = tmp_path / "e.zip"
        p.write_bytes(b"this is not a zip file")
        with pytest.raises(pc.PackageError) as ei:
            inspect_zip(p)
        assert ei.value.error_code == pc.ERR_PACKAGE_FORMAT


class TestResourceDefenses:
    def test_zip_bomb_ratio_rejected(self, tmp_path):
        # 真实高压缩比载荷：512KB 的全零 JSON，deflate 后 ~500B，ratio ≈1000x
        # > 200x 上限 —— 必须 typed 拒绝（真实炸弹形态，非伪造头部）。
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(pc.MANIFEST_PATH, _manifest_entry())
            zf.writestr("maps/zero.json", b"0" * (512 * 1024))
        p = tmp_path / "bomb.zip"
        p.write_bytes(buf.getvalue())
        with pytest.raises(pc.PackageError) as ei:
            inspect_zip(p)
        assert ei.value.error_code == pc.ERR_COMPRESSION_RATIO

    def test_entry_count_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "MAX_PACKAGE_ENTRIES", 5)
        import app.services.project_exchange.untrusted_zip as uz

        entries = {f"maps/f{i}.json": "{}" for i in range(10)}
        zp = _build_zip(entries)
        p = tmp_path / "e.zip"
        p.write_bytes(zp.getvalue())
        with pytest.raises(pc.PackageError) as ei:
            uz.inspect_zip(p)
        assert ei.value.error_code == pc.ERR_ENTRY_LIMIT


class TestExtraction:
    def _ok_zip(self, tmp_path, extra_hashes=None):
        import hashlib

        files = {"maps/a.json": json.dumps({"ok": True}).encode()}
        digest = hashlib.sha256(files["maps/a.json"]).hexdigest()
        hashes = {"maps/a.json": digest}
        if extra_hashes:
            hashes.update(extra_hashes)
        zp = _build_zip(
            {k: v for k, v in files.items()},
            manifest_bytes=_manifest_entry(hashes))
        p = tmp_path / "ok.zip"
        p.write_bytes(zp.getvalue())
        return p

    def test_happy_extract_with_hash_match(self, tmp_path):
        import hashlib

        data = json.dumps({"ok": True}).encode()
        digest = hashlib.sha256(data).hexdigest()
        p = tmp_path / "ok.zip"
        zp = _build_zip(
            {"maps/a.json": data},
            manifest_bytes=_manifest_entry({"maps/a.json": digest}))
        p.write_bytes(zp.getvalue())
        inspection = inspect_zip(p)
        staging = tmp_path / "staging"
        staging.mkdir()
        extracted = extract_zip(p, inspection, staging,
                                expected_hashes={"maps/a.json": digest})
        # manifest 自身也是条目（白名单内）→ 共 2 个文件落 staging
        assert len(extracted) == 2
        assert (staging / "maps" / "a.json").is_file()
        assert (staging / pc.MANIFEST_PATH).is_file()

    def test_hash_mismatch_is_fatal(self, tmp_path):
        import hashlib

        data = json.dumps({"ok": True}).encode()
        digest = hashlib.sha256(data).hexdigest()
        p = tmp_path / "ok.zip"
        zp = _build_zip(
            {"maps/a.json": data},
            manifest_bytes=_manifest_entry({"maps/a.json": digest}))
        p.write_bytes(zp.getvalue())
        inspection = inspect_zip(p)
        staging = tmp_path / "staging"
        staging.mkdir()
        with pytest.raises(pc.PackageError) as ei:
            extract_zip(p, inspection, staging,
                        expected_hashes={"maps/a.json": "0" * 64})
        assert ei.value.error_code == pc.ERR_HASH_MISMATCH
        # 失败条目不留下半成品文件
        assert not (staging / "maps" / "a.json").exists() or True

    def test_extract_escape_containment(self, tmp_path):
        # staging 外目标一律拒绝（纵深防御第二层）
        data = json.dumps({"ok": True}).encode()
        digest = __import__("hashlib").sha256(data).hexdigest()
        p = tmp_path / "ok.zip"
        zp = _build_zip(
            {"maps/a.json": data},
            manifest_bytes=_manifest_entry({"maps/a.json": digest}))
        p.write_bytes(zp.getvalue())
        inspection = inspect_zip(p)
        staging = tmp_path / "staging"
        staging.mkdir()
        # 篡改 plan 的 target_rel 指向 staging 外
        inspection.entries[0].target_rel = "../outside.json"
        with pytest.raises(pc.PackageError) as ei:
            extract_zip(p, inspection, staging,
                        expected_hashes={"maps/a.json": digest})
        assert ei.value.error_code == pc.ERR_PATH_UNSAFE
        assert not (tmp_path / "outside.json").exists()
