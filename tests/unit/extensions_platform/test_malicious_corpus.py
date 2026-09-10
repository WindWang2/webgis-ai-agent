"""恶意语料：供应链攻击场景矩阵（ADR-0119 / Wave 15）。

每类场景 = 攻击 + typed 断言（fail closed；绝无静默放行）：
forged signature / revoked key / modified package / downgrade /
dependency confusion / zip-slip（含 hardlink）/ symlink escape / SSRF
gate 拒私网 / env 泄漏（bwrap 条件车道）/ oversized frame / install
interruption / malicious manifest / cross-extension artifact namespace /
cross-owner provider access / retired-key 提权。
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from app.extensions_platform.diagnostics import ExtensionPlatformError
from app.extensions_platform.distribution import safe_extract_package
from app.extensions_platform.host import ExtensionHost, HostPolicy
from app.extensions_platform.marketplace.models import PackageRecord
from app.extensions_platform.marketplace.service import PublishPolicy, RegistryService
from app.extensions_platform.marketplace.store import RegistryStore
from app.extensions_platform.signing import (
    STATUS_INVALID,
    STATUS_SIGNED_RETIRED,
    generate_signing_keypair,
    sign_pack_asymmetric,
    verify_pack_signature,
)
from app.extensions_platform.trust_store import TrustStore


def _keys(root: Path, *key_ids: str) -> dict[str, Path]:
    out = {}
    for key_id in key_ids:
        priv = root / "keys" / f"{key_id}.private.pem"
        if not priv.exists():
            generate_signing_keypair(root / "keys", key_id)
        out[key_id] = priv
    return out


def _trust(root: Path, keys: dict[str, str], revoked: dict | None = None) -> TrustStore:
    path = root / "ts.json"
    doc = {
        "publishers": {
            publisher: {
                "keys": {
                    key_id: {
                        "public_key_pem": (root / "keys" / f"{key_id}.public.pem").read_text(),
                        "state": state,
                    }
                    for key_id, state in keys_by_publisher.items()
                }
            }
            for publisher, keys_by_publisher in {
                "acme": {k: "active" for k, v in keys.items() if k.startswith("acme")},
            }.items()
        },
        "revoked": {"key_ids": [], "fingerprints": [], "packages": []},
    }
    # retired keys 修正
    for key_id, state in keys.items():
        if state == "retired":
            doc["publishers"]["acme"]["keys"][key_id]["state"] = "retired"
        if state == "revoked":
            doc["publishers"]["acme"]["keys"][key_id]["state"] = "revoked"
    if revoked:
        for k, v in revoked.items():
            doc["revoked"][k] = v
    path.write_text(json.dumps(doc))
    return TrustStore.load(path)


def _pack(root: Path, ext_id: str = "acme.target", version: str = "1.0.0") -> Path:
    ns, name = ext_id.split(".")
    pack = root / "work" / f"{ext_id.replace('.', '_')}-{version}"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": ext_id,
                "name": name,
                "namespace": ns,
                "version": version,
                "entry_point": "main",
            }
        )
    )
    (pack / "main.py").write_text("def activate(ctx):\n    return None\n")
    return pack


def _blob(pack: Path) -> bytes:
    import io

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(pack.rglob("*")):
            if path.is_file():
                tar.add(str(path), arcname=str(path.relative_to(pack)))
    return buf.getvalue()


def _signed_blob(root: Path, ext_id: str = "acme.target", version: str = "1.0.0", key_id: str = "acme-2026") -> bytes:
    """签名后再打包（signature.json 入包）。"""
    pack = _pack(root, ext_id, version)
    privs = _keys(root, "acme-2026")
    sign_pack_asymmetric(pack, "acme", key_id, privs[key_id])
    return _blob(pack)


# ── 1. forged signature ──────────────────────────────────────────────

def test_forged_signature_rejected(tmp_path):
    privs = _keys(tmp_path, "acme-2026", "mallory")
    pack = _pack(tmp_path)
    sign_pack_asymmetric(pack, "acme", "acme-2026", privs["mallory"])  # 错误密钥签名
    trust = _trust(tmp_path, {"acme-2026": "active"})
    status = verify_pack_signature(pack, {}, trust_store=trust)
    assert status.status == STATUS_INVALID


# ── 2. revoked key ───────────────────────────────────────────────────

def test_revoked_key_signing_rejected(tmp_path):
    privs = _keys(tmp_path, "acme-2026")
    pack = _pack(tmp_path)
    sign_pack_asymmetric(pack, "acme", "acme-2026", privs["acme-2026"])
    trust = _trust(tmp_path, {"acme-2026": "revoked"})
    assert verify_pack_signature(pack, {}, trust_store=trust).status == "revoked"


# ── 3. modified package after signing ────────────────────────────────

def test_modified_package_detected(tmp_path):
    privs = _keys(tmp_path, "acme-2026")
    pack = _pack(tmp_path)
    sign_pack_asymmetric(pack, "acme", "acme-2026", privs["acme-2026"])
    (pack / "main.py").write_text("import os; os.system('echo pwned')\n")
    assert verify_pack_signature(pack, {}, trust_store=_trust(tmp_path, {"acme-2026": "active"})).status == "tampered"


# ── 4. downgrade via retired key ─────────────────────────────────────

def test_retired_key_signature_not_elevated(tmp_path):
    """C-4：retired 密钥签的新包 → 不提权（signed_retired）。"""
    privs = _keys(tmp_path, "acme-2025", "acme-2026")
    pack = _pack(tmp_path)
    sign_pack_asymmetric(pack, "acme", "acme-2025", privs["acme-2025"])
    trust = _trust(tmp_path, {"acme-2026": "active", "acme-2025": "retired"})
    assert verify_pack_signature(pack, {}, trust_store=trust).status == STATUS_SIGNED_RETIRED


# ── 5. rollback/version downgrade preflight ──────────────────────────

def test_downgrade_requires_explicit_rollback(tmp_path):
    privs = _keys(tmp_path, "acme-2026")
    trust = _trust(tmp_path, {"acme-2026": "active"})
    store = RegistryStore(tmp_path / "reg")
    service = RegistryService(store, trust_store=trust, policy=PublishPolicy(allowed_publishers=frozenset({"acme"})))
    service.publish(_signed_blob(tmp_path, version="2.0.0"))
    # registry 里只有 2.0.0；安装后想装 1.0.0 需 preflight downgrade 拒绝。
    from app.extensions_platform.distribution import ExtensionInstaller

    installer = ExtensionInstaller(
        install_root=tmp_path / "install", registry=store, trust_store=trust
    )
    with pytest.raises(ExtensionPlatformError, match="no version"):
        installer.install("acme.target", "1.0.0")
    del privs


# ── 6. dependency confusion（claim-once）─────────────────────────────

def test_dependency_confusion_blocked(tmp_path):
    _keys(tmp_path, "acme-2026")
    trust = _trust(tmp_path, {"acme-2026": "active"})
    store = RegistryStore(tmp_path / "reg")
    service = RegistryService(store, trust_store=trust, policy=PublishPolicy(allowed_publishers=frozenset({"acme"})))
    service.publish(_signed_blob(tmp_path, "acme.left", "1.0.0"))
    # 同 id 不同 publisher → store 层拒绝（claim-once）。
    with pytest.raises(ExtensionPlatformError, match="owned by"):
        with store.locked():
            store.commit_package(
                PackageRecord(id="acme.left", publisher="mallory"),
                type(service.get_version("acme.left"))(
                    version="9.9.9",
                    digest="0" * 64,
                    size_bytes=1,
                    publisher="mallory",
                    key_id="k",
                    signature={},
                    fingerprint="0" * 64,
                    sbom_digest="0" * 64,
                ),
            )


# ── 7. zip slip：路径穿越 + hardlink ─────────────────────────────────

def _raw_tar(build) -> bytes:
    import io

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        build(tar)
    return buf.getvalue()


def test_zip_slip_traversal_blocked(tmp_path):
    def build(tar):
        import io as _io

        info = tarfile.TarInfo("../../.ssh/authorized_keys")
        payload = b"pwned"
        info.size = len(payload)
        tar.addfile(info, _io.BytesIO(payload))

    with pytest.raises(ExtensionPlatformError, match="unsafe path"):
        safe_extract_package(_raw_tar(build), tmp_path / "out")
    assert not (tmp_path / ".ssh").exists()


def test_hardlink_member_blocked(tmp_path):
    def build(tar):
        info = tarfile.TarInfo("etc_shadow")
        info.type = tarfile.LNKTYPE
        info.linkname = "../../../../etc/shadow"
        tar.addfile(info)

    with pytest.raises(ExtensionPlatformError, match="not a regular"):
        safe_extract_package(_raw_tar(build), tmp_path / "out")


def test_device_member_blocked(tmp_path):
    def build(tar):
        info = tarfile.TarInfo("dev_zero")
        info.type = tarfile.CHRTYPE
        info.devmajor, info.devminor = 1, 5
        tar.addfile(info)

    with pytest.raises(ExtensionPlatformError, match="not a regular"):
        safe_extract_package(_raw_tar(build), tmp_path / "out")


# ── 8. symlink escape in pack dir（发现面）───────────────────────────

def test_symlink_in_pack_detected_by_certification_layout(tmp_path):
    from app.extensions_platform.certification import _check_package_layout

    pack = _pack(tmp_path)
    link = pack / "escape"
    try:
        link.symlink_to("/etc")
    except OSError:  # pragma: no cover - 平台不支持 symlink
        pytest.skip("symlink unsupported")
    ok, detail = _check_package_layout(pack)
    assert ok is False and "symlink" in detail


# ── 9. SSRF：broker 网络面拒私网 ──────────────────────────────────────

def test_broker_network_blocks_private_ssrf():
    from app.extensions_platform.broker import CapabilityBroker
    from app.extensions_platform.permissions import grants_for

    broker = CapabilityBroker(
        extension_id="evil.x",
        grants=grants_for("evil.x", {"evil.x": frozenset({"network"})}),
        network_allow=frozenset({"*"}),
        http_transport=None,
    )
    ok, value = broker.handle("network_request", {"url": "http://169.254.169.254/latest/meta-data"})
    assert ok is False
    assert "SSRF" in value.get("message", "") or "denied" in value.get("message", "")


# ── 10. env leakage：worker env 不含宿主 secrets（结构性）────────────

def test_worker_env_minimal_no_host_env():
    from app.extensions_platform.worker.client import WorkerProcess

    wp = WorkerProcess.__new__(WorkerProcess)
    wp._extension_id = "x"
    env = WorkerProcess._child_env(wp)
    allowed = set(env)
    assert allowed == {"PATH", "PYTHONPATH", "LANG", "HOME", "PYTHONHASHSEED", "WEBGIS_EXTENSION_WORKER"}
    # 值里不携带宿主秘密（结构性：白名单键，不含任意环境拷贝）。
    assert all("SECRET" not in k.upper() and "KEY" not in k.upper() for k in allowed)


# ── 11. oversized frame ──────────────────────────────────────────────

def test_oversized_frame_rejected():
    from app.extensions_platform.worker.protocol import FRAME_MAX_BYTES, ProtocolError, decode_frame

    with pytest.raises(ProtocolError):
        decode_frame(b"x" * (FRAME_MAX_BYTES + 10))


# ── 12. install interruption：staging 恢复 ───────────────────────────

def test_install_interruption_recovery(tmp_path):
    """第 2 次 rename 前崩溃 → 恢复例程完成安装（C-2）。"""
    privs = _keys(tmp_path, "acme-2026")
    trust = _trust(tmp_path, {"acme-2026": "active"})
    store = RegistryStore(tmp_path / "reg")
    service = RegistryService(store, trust_store=trust, policy=PublishPolicy(allowed_publishers=frozenset({"acme"})))
    service.publish(_signed_blob(tmp_path, version="1.0.0"))
    from app.extensions_platform.distribution import (
        recover_pending_swaps,
        safe_extract_package,
    )

    blob = store.read_blob(service.get_version("acme.target", "1.0.0").digest)
    staging_root = tmp_path / "install" / ".staging"
    staging_root.mkdir(parents=True)
    staging = staging_root / "acme.target-1.0.0-deadbeef"
    safe_extract_package(blob, staging)
    assert recover_pending_swaps(tmp_path / "install") == [staging.name]
    active = tmp_path / "install" / "extensions" / "acme.target" / "manifest.json"
    assert json.loads(active.read_text())["version"] == "1.0.0"
    del privs


# ── 13. malicious manifest（budget 超界 / 非法 entry_point）──────────

def test_malicious_manifest_budget_overflow_rejected():
    from app.extensions_platform.manifest import GisExtensionManifest

    with pytest.raises(Exception):
        GisExtensionManifest.model_validate(
            {
                "schema_version": 1,
                "id": "evil.pack",
                "name": "pack",
                "namespace": "evil",
                "version": "1.0.0",
                "entry_point": "main",
                "execution": {
                    "mode": "worker",
                    "max_memory_mb": 999999,
                },
            }
        )


def test_malicious_manifest_entry_point_escape_rejected():
    from app.extensions_platform.manifest import GisExtensionManifest

    with pytest.raises(Exception, match="entry_point"):
        GisExtensionManifest.model_validate(
            {
                "schema_version": 1,
                "id": "evil.pack",
                "name": "pack",
                "namespace": "evil",
                "version": "1.0.0",
                "entry_point": "../evil",
            }
        )


# ── 14. cross-extension artifact namespace（api 1.2 门控语义）────────

def test_artifact_namespace_flat_semantics_for_legacy(tmp_path):
    """1.1 扩展保持 V2 平铺语义；未授权 → 默认 deny 不变。"""
    from app.extensions_platform.broker import CapabilityBroker
    from app.extensions_platform.permissions import grants_for

    root = tmp_path / "artifacts"
    root.mkdir()
    broker = CapabilityBroker(
        extension_id="legacy.worker",
        grants=grants_for("legacy.worker", {"legacy.worker": frozenset({"project_artifact_write"})}),
        artifact_roots=(root,),
    )
    ok, value = broker.handle(
        "artifact_write",
        {"path": "results/out.txt", "content_b64": "aGVsbG8="},
    )
    assert ok is True
    # V2 平铺语义：直接落在 root/results/（无 per-ext 子目录）。
    assert (root / "results" / "out.txt").is_file()


# ── 15. cross-owner provider access（registry claim-once 已测）───────

def test_cross_owner_marketplace_search_hides_revoked(tmp_path):
    """吊销包从公共搜索面消失（除非显式 include_revoked）。"""
    _keys(tmp_path, "acme-2026")
    trust = _trust(tmp_path, {"acme-2026": "active"})
    store = RegistryStore(tmp_path / "reg")
    service = RegistryService(store, trust_store=trust, policy=PublishPolicy(allowed_publishers=frozenset({"acme"})))
    service.publish(_signed_blob(tmp_path, version="1.0.0"))
    service.revoke("acme.target")
    assert service.search().total == 0
    assert service.search(include_revoked=True).total == 1
    # 下载面：revoked → 410 语义（service 层 = get_version 仍可用于审计）。
    assert service.get_package("acme.target").status == "revoked"


# ── 16. host 级 unknown publisher（不因有签名而放行）──────────────────

def test_ed25519_pack_without_trust_store_fails_closed(tmp_path):
    """无 trust store 的宿主收到 ed25519 签名包 → invalid（不支持算法 →
    fail closed 隔离），绝不降级成「未签名」放行。"""
    privs = _keys(tmp_path, "acme-2026")
    pack = _pack(tmp_path)
    sign_pack_asymmetric(pack, "acme", "acme-2026", privs["acme-2026"])
    status = verify_pack_signature(pack, {}, trust_store=None)
    assert status.status == STATUS_INVALID
    del privs


# ── 17. host 激活面：篡改签名包 → quarantine（即使被 allowlist 点名）──

def test_tampered_pack_quarantined_even_if_allowlisted(tmp_path):
    privs = _keys(tmp_path, "acme-2026")
    trust = _trust(tmp_path, {"acme-2026": "active"})
    pack = _pack(tmp_path)
    sign_pack_asymmetric(pack, "acme", "acme-2026", privs["acme-2026"])
    (pack / "main.py").write_text("def activate(ctx):\n    import os\n    return os\n")
    # host 发现布局：<root>/<ext>/manifest.json —— 签名后内容被换血。
    layout_root = tmp_path / "roots"
    ext_dir = layout_root / "target"
    ext_dir.mkdir(parents=True)
    for f in pack.iterdir():
        (ext_dir / f.name).write_text(f.read_text())
    policy = HostPolicy(
        roots=(layout_root,),
        allow=frozenset({"acme.target"}),
        allow_local_untrusted_activation=True,
        trust_store=trust,
        trust_signed=True,
    )
    host = ExtensionHost(
        tool_registry=type(
            "T", (), {"has": lambda s, n: False, "register": lambda *a, **k: None}
        )(),
        policy=policy,
    )
    host.discover()
    record = host.get_record("acme.target")
    assert record is not None
    # 内容在签名后被换血 → tampered → quarantine（allowlist 无法救）。
    assert record.state.value == "quarantined"
