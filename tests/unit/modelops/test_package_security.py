"""包安全门测试（Wave 3 验收；R1-m4 补充）。"""
from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pytest

from app.lib.modelops.package_security import (
    inspect_archive,
    load_weights_array,
    sha256_of_bytes,
    verify_checksum,
    PackageSecurityError,
    ModelChecksumError,
)


def _zip_bytes(members: dict, *, metadata: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        if metadata and "manifest.json" not in members:
            zf.writestr("manifest.json", json.dumps({"format": "test"}))
    return buf.getvalue()


def test_good_package_passes():
    data = _zip_bytes({"weights/stable.npz": b"\x00\x01", "manifest.json": json.dumps({"ok": 1})})
    report = inspect_archive(data)
    assert report.entry_count if hasattr(report, "entry_count") else len(report.entries) >= 2
    assert report.metadata == {"ok": 1}


def test_bad_checksum_rejected():
    data = _zip_bytes({"a.bin": b"x"})
    with pytest.raises(ModelChecksumError):
        inspect_archive(data, expected_checksum="f" * 64)


def test_path_traversal_rejected():
    data = _zip_bytes({"../evil.bin": b"x"})
    with pytest.raises(PackageSecurityError, match="escapes root|unsafe"):
        inspect_archive(data)


def test_absolute_path_rejected():
    data = _zip_bytes({"/etc/passwd": b"x"})
    with pytest.raises(PackageSecurityError):
        inspect_archive(data)


def test_symlink_member_rejected():
    buf = io.BytesIO()
    info = zipfile.ZipInfo("link.bin")
    info.external_attr = (0o120777 << 16)  # symlink mode
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(info, "/etc/passwd")
        zf.writestr("manifest.json", "{}")
    with pytest.raises(PackageSecurityError, match="symlink"):
        inspect_archive(buf.getvalue())


def test_pickle_member_rejected():
    data = _zip_bytes({"model.pkl": b"\x80\x04pickle"})
    with pytest.raises(PackageSecurityError, match="forbidden format"):
        inspect_archive(data)


def test_python_code_member_rejected():
    data = _zip_bytes({"impl.py": b"import os"})
    with pytest.raises(PackageSecurityError, match="forbidden format"):
        inspect_archive(data)


def test_nested_archive_rejected():
    data = _zip_bytes({"inner.zip": b"PK\x03\x04"})
    with pytest.raises(PackageSecurityError, match="nested archive"):
        inspect_archive(data)


def test_missing_metadata_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.bin", b"x")
    with pytest.raises(PackageSecurityError, match="manifest.json"):
        inspect_archive(buf.getvalue())


def test_metadata_secret_key_rejected():
    data = _zip_bytes({"manifest.json": json.dumps({"api_key": "sk-1"})})
    with pytest.raises(PackageSecurityError, match="secret"):
        inspect_archive(data)


def test_member_inflation_rejected():
    """声明尺寸 vs 实际不符（zip bomb 形态）→ 拒绝。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("bomb.bin")
        info.file_size = 1 << 20
        zf.writestr(info, b"x" * 10)
        zf.writestr("manifest.json", "{}")
    with pytest.raises(PackageSecurityError):
        inspect_archive(buf.getvalue())


def test_tar_with_symlink_rejected():
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
        meta = tarfile.TarInfo("manifest.json")
        meta.size = 2
        tf.addfile(meta, io.BytesIO(b"{}"))
    with pytest.raises(PackageSecurityError):
        inspect_archive(buf.getvalue())


def test_load_weights_rejects_object_array():
    raw = io.BytesIO()
    np.save(raw, np.array([{"x": 1}], dtype=object), allow_pickle=True)
    with pytest.raises(PackageSecurityError):
        load_weights_array(raw.getvalue())


def test_load_weights_accepts_numeric():
    raw = io.BytesIO()
    np.save(raw, np.array([[1.0, 2.0]]))
    arr = load_weights_array(raw.getvalue())
    assert arr.shape == (1, 2)


def test_verify_checksum_helpers():
    data = b"payload"
    digest = sha256_of_bytes(data)
    assert verify_checksum(data=data, expected=digest) == digest
    with pytest.raises(ModelChecksumError):
        verify_checksum(data=data, expected="0" * 64)
