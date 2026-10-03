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


# ---------------------------------------------------------------- CP-02


def _write_inproc_pack(root: Path, ns: str = "acme", name: str = "pack") -> Path:
    import json

    d = root / f"{ns}-{name}"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({
        "id": f"{ns}.{name}", "name": name, "namespace": ns, "version": "1.0.0",
        "entry_point": "main", "description": f"synthetic {uuid.uuid4().hex}",
    }))
    (d / "main.py").write_text(
        "import pathlib\n"
        "def activate(ctx):\n"
        "    pathlib.Path(__file__).parent.parent.joinpath('RAN').write_text('1')\n"
    )
    return d


@pytest.mark.parametrize(
    "policy,allow,expect_active",
    [
        ("none", frozenset(), True),
        ("untrusted", frozenset(), False),
        ("untrusted", frozenset({"acme.pack"}), True),  # 运维 allowlist = 显式信任
        ("all", frozenset({"acme.pack"}), False),
    ],
)
def test_cp02_operator_policy_decides_in_process(tmp_path, policy, allow, expect_active):
    from app.extensions_platform.diagnostics import DiagnosticCode
    from app.extensions_platform.host import ExtensionHost, ExtensionState, HostPolicy
    from app.tools.registry import ToolRegistry

    pack = _write_inproc_pack(tmp_path)
    host = ExtensionHost(
        tool_registry=ToolRegistry(),
        policy=HostPolicy(roots=(tmp_path,), allow=allow, require_worker_for=policy),
    )
    host.discover()
    diags = host.activate("acme.pack")
    record = host.get_record("acme.pack")
    if expect_active:
        assert record.state is ExtensionState.ACTIVE
        assert (pack.parent / "RAN").exists()
    else:
        assert record.state is ExtensionState.FAILED
        assert any(d.code is DiagnosticCode.ISOLATION_UNAVAILABLE for d in diags)
        assert not (pack.parent / "RAN").exists()  # 代码从未执行


def test_cp02_settings_defaults_are_safe(monkeypatch):
    from app.core.config import Settings
    from app.extensions_platform import settings_bridge

    assert Settings.model_fields["EXTENSIONS_REQUIRE_WORKER_FOR"].default == "untrusted"
    assert Settings.model_fields["EXTENSIONS_ISOLATION_BACKEND"].default == "auto"
    assert settings_bridge._parse_require_worker_for("") == "untrusted"
    with pytest.raises(ExtensionPlatformError):
        settings_bridge._parse_require_worker_for("sometimes")
    import app.extensions_platform.worker.isolation as iso

    monkeypatch.setattr(iso, "probe_bubblewrap", lambda force=False: "/usr/bin/bwrap")
    assert settings_bridge._parse_isolation_backend("auto") == "bubblewrap"
    monkeypatch.setattr(iso, "probe_bubblewrap", lambda force=False: None)
    assert settings_bridge._parse_isolation_backend("auto") == "process"
    assert settings_bridge._parse_isolation_backend("bubblewrap") == "bubblewrap"


def test_cp02_worker_env_has_no_repo_root_or_host_secrets(monkeypatch, tmp_path):
    from app.extensions_platform.worker import client as wc

    monkeypatch.setenv("LLM_API_KEY", "sk-host-secret")
    worker = wc.WorkerProcess(
        pack_dir=tmp_path, extension_id="acme.pack", namespace="acme", name="pack",
        fingerprint="x" * 64, grants=[], settings={},
        startup_timeout_s=1.0, call_timeout_s=1.0,
    )
    env = worker._child_env()
    assert "LLM_API_KEY" not in env and "sk-host-secret" not in env.values()
    pp = Path(env["PYTHONPATH"])
    assert pp != wc._REPO_ROOT
    assert (pp / "app").resolve() == (wc._REPO_ROOT / "app").resolve()
    assert not (pp / ".env").exists()


# ---------------------------------------------------------------- CP-03


def _ns_broker(root: Path, ext_id: str = "acme.a"):
    from app.extensions_platform.broker import CapabilityBroker
    from app.extensions_platform.permissions import grants_for

    perms = frozenset({"project_artifact_read", "project_artifact_write"})
    return CapabilityBroker(
        extension_id=ext_id,
        grants=grants_for(ext_id, {ext_id: perms}),
        artifact_roots=(root,),
        artifact_namespace=True,
    )


def test_cp03_namespace_escape_denied(tmp_path):
    root = tmp_path / "aroot"
    (root / "acme.b").mkdir(parents=True)
    (root / "acme.b" / "data.txt").write_text("victim")
    broker = _ns_broker(root)
    for path in ("../acme.b/data.txt", str((root / "acme.b" / "data.txt").resolve()),
                 "sub/../../acme.b/data.txt"):
        ok, value = broker.handle("artifact_read", {"path": path})
        assert ok is False, path
        ok, value = broker.handle("artifact_write", {"path": path, "content_b64": "eA=="})
        assert ok is False, path
    assert (root / "acme.b" / "data.txt").read_text() == "victim"
    ok, value = broker.handle("artifact_write", {"path": "out/x.txt", "content_b64": "aGk="})
    assert ok is True
    assert (root / "acme.a" / "out" / "x.txt").read_text() == "hi"


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics")
def test_cp03_write_does_not_follow_planted_symlink(tmp_path):
    root = tmp_path / "aroot"
    (root / "acme.a").mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    (root / "acme.a" / "link.txt").symlink_to(victim)
    broker = _ns_broker(root)
    ok, _ = broker.handle("artifact_write", {"path": "link.txt", "content_b64": "eA=="})
    assert ok is False
    assert victim.read_text() == "keep"
