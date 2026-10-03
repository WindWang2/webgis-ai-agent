"""Deep-review carto-platform 回归（CP-01/02/03/10）。"""

from __future__ import annotations

import io
import os
import py_compile
import sys
import tarfile
import uuid
from pathlib import Path

import pytest

from app.extensions_platform.diagnostics import ExtensionPlatformError
from app.extensions_platform.discovery import compute_fingerprint
from app.extensions_platform.loader import load_entry_module


# ---------------------------------------------------------------- CP-01


def _pyc_tampered_pack(tmp_path: Path) -> Path:
    pack = tmp_path / "pk"
    pack.mkdir()
    (pack / "__init__.py").write_text(
        "import importlib\n"
        "def activate(ctx):\n    pass\n"
        "helper = importlib.import_module(__name__ + '.helper')\n"
    )
    (pack / "helper.py").write_text('MSG = "signed benign"\n')
    return pack


def _plant_pyc(pack: Path, tmp_path: Path, mod: str, cfile: Path) -> None:
    mal = tmp_path / f"mal_{uuid.uuid4().hex}.py"
    mal.write_text('MSG = "TAMPERED CODE RAN"\n')
    cfile.parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(
        str(mal), cfile=str(cfile),
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )


def test_cp01_pycache_bytecode_is_never_executed(tmp_path):
    pack = _pyc_tampered_pack(tmp_path)
    fp_before = compute_fingerprint(pack)[0]
    tag = sys.implementation.cache_tag
    _plant_pyc(pack, tmp_path, "helper", pack / "__pycache__" / f"helper.{tag}.pyc")
    _plant_pyc(pack, tmp_path, "__init__", pack / "__pycache__" / f"__init__.{tag}.pyc")
    fp_after = compute_fingerprint(pack)[0]
    assert fp_before == fp_after  # 字节码不入指纹……
    ns = f"cp01{uuid.uuid4().hex[:8]}"
    module = load_entry_module(pack.resolve(), ns, "n", "__init__", fp_after)
    try:
        # ……因此也绝不能被执行。
        assert module.helper.MSG == "signed benign"
        assert callable(module.activate)
    finally:
        for name in [m for m in sys.modules if m.startswith(f"webgis_ext_{ns}")]:
            sys.modules.pop(name, None)
    # 也不写字节码进包目录（不产生新的 pyc）。
    pycs = sorted(p.name for p in (pack / "__pycache__").iterdir())
    assert pycs == sorted([f"helper.{tag}.pyc", f"__init__.{tag}.pyc"])


def test_cp01_sourceless_pyc_module_not_importable(tmp_path):
    pack = tmp_path / "pk"
    pack.mkdir()
    (pack / "__init__.py").write_text(
        "import importlib\n"
        "def activate(ctx):\n    pass\n"
        "def probe():\n    return importlib.import_module(__name__ + '.ghost')\n"
    )
    _plant_pyc(pack, tmp_path, "ghost", pack / "ghost.pyc")
    ns = f"cp01{uuid.uuid4().hex[:8]}"
    fp = compute_fingerprint(pack)[0]
    module = load_entry_module(pack.resolve(), ns, "n", "__init__", fp)
    try:
        with pytest.raises(ModuleNotFoundError):
            module.probe()
    finally:
        for name in [m for m in sys.modules if m.startswith(f"webgis_ext_{ns}")]:
            sys.modules.pop(name, None)


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics")
def test_cp01_symlinked_dir_fails_closed(tmp_path):
    pack = tmp_path / "pk"
    pack.mkdir()
    (pack / "__init__.py").write_text("def activate(ctx):\n    pass\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.py").write_text("X = 1\n")
    (pack / "lib").symlink_to(outside, target_is_directory=True)
    fp, diag = compute_fingerprint(pack)
    assert fp is None
    assert diag is not None and "symlink" in diag.message


def test_cp01_distribution_rejects_bytecode_members(tmp_path):
    from app.extensions_platform.distribution import safe_extract_package

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (
            ("manifest.json", b"{}"),
            ("__pycache__/helper.cpython-312.pyc", b"\x00" * 16),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    with pytest.raises(ExtensionPlatformError) as exc:
        safe_extract_package(buf.getvalue(), tmp_path / "out")
    assert "bytecode" in str(exc.value)
