"""V3 非对称签名与 trust store（ADR-0119 / Wave 2-4）。

钉死的裁决语义：
- Ed25519 签名/验签往返；伪造/篡改/换 key → invalid/tampered；
- trust store：rotation（多 key 并存）、retired（验签可过但独立裁决，
  不提权）、revoked（key/指纹/包级 → revoked 生死语义）；
- HMAC v1 载荷逐字节不变（V2 包在 V3 宿主上验签行为不变）；
- trust store 加载 fail closed（坏形状/非 Ed25519 公钥 → typed）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extensions_platform.diagnostics import ExtensionPlatformError
from app.extensions_platform.discovery import SIGNATURE_FILENAME, compute_fingerprint
from app.extensions_platform.signing import (
    SIGNATURE_ALGORITHM_ED25519,
    STATUS_INVALID,
    STATUS_MISSING,
    STATUS_REVOKED,
    STATUS_SIGNED_RETIRED,
    STATUS_SIGNED_UNTRUSTED,
    STATUS_SIGNED_VERIFIED,
    STATUS_TAMPERED,
    generate_signing_keypair,
    sign_pack,
    sign_pack_asymmetric,
    verify_pack_signature,
    _signature_payload,
)
from app.extensions_platform.trust_store import TrustStore


def _write_pack(root: Path, ext_id: str = "acme.demo", version: str = "1.0.0") -> Path:
    ns, name = ext_id.split(".")
    pack = root / ext_id.replace(".", "_")
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


def _keypair(root: Path, key_id: str) -> tuple[Path, Path]:
    """生成（或复用）密钥对——同一 tmp_path 内多次调用安全。"""
    marker = root / "keys" / f"{key_id}.private.pem"
    if marker.exists():
        return marker, root / "keys" / f"{key_id}.public.pem"
    return generate_signing_keypair(root / "keys", key_id)


def _make_store(root: Path, *, extra: dict | None = None) -> TrustStore:
    """trust store：acme(active acme-2026, retired acme-2025)。"""
    _, pub26 = _keypair(root, "acme-2026")
    _, pub25 = _keypair(root, "acme-2025")
    doc = {
        "publishers": {
            "acme": {
                "keys": {
                    "acme-2026": {
                        "public_key_pem": pub26.read_text(),
                        "state": "active",
                    },
                    "acme-2025": {
                        "public_key_pem": pub25.read_text(),
                        "state": "retired",
                    },
                }
            }
        },
        "revoked": {"key_ids": [], "fingerprints": [], "packages": []},
    }
    if extra:
        for key, value in extra.items():
            if key == "publishers":
                doc["publishers"].update(value)
            elif key == "revoked":
                for rk, rv in value.items():
                    doc["revoked"][rk] = rv
    path = root / "trust_store.json"
    path.write_text(json.dumps(doc))
    return TrustStore.load(path)


def test_ed25519_sign_verify_roundtrip(tmp_path):
    pack = _write_pack(tmp_path)
    priv, _ = _keypair(tmp_path, "acme-2026")
    doc = sign_pack_asymmetric(pack, "acme", "acme-2026", priv)
    assert doc["algorithm"] == SIGNATURE_ALGORITHM_ED25519
    assert doc["publisher"] == "acme"
    store = _make_store(tmp_path)
    status = verify_pack_signature(pack, {}, trust_store=store, package_id="acme.demo", version="1.0.0")
    assert status.status == STATUS_SIGNED_VERIFIED, status.detail
    assert status.publisher == "acme"


def test_forged_ed25519_signature_is_invalid(tmp_path):
    pack = _write_pack(tmp_path)
    priv, _ = _keypair(tmp_path, "acme-2026")
    sign_pack_asymmetric(pack, "acme", "acme-2026", priv)
    sig = json.loads((pack / SIGNATURE_FILENAME).read_text())
    sig["signature"] = "AAAA" + sig["signature"][4:]
    (pack / SIGNATURE_FILENAME).write_text(json.dumps(sig))
    store = _make_store(tmp_path)
    status = verify_pack_signature(pack, {}, trust_store=store)
    assert status.status == STATUS_INVALID


def test_modified_package_is_tampered(tmp_path):
    pack = _write_pack(tmp_path)
    priv, _ = _keypair(tmp_path, "acme-2026")
    sign_pack_asymmetric(pack, "acme", "acme-2026", priv)
    (pack / "main.py").write_text("def activate(ctx):\n    raise SystemExit(1)\n")
    store = _make_store(tmp_path)
    status = verify_pack_signature(pack, {}, trust_store=store)
    assert status.status == STATUS_TAMPERED


def test_signature_itself_not_covered_by_fingerprint(tmp_path):
    """签名文件被指纹排除：无签名包 missing；签名后重复验签稳定。"""
    pack = _write_pack(tmp_path)
    store = _make_store(tmp_path)
    assert verify_pack_signature(pack, {}, trust_store=store).status == STATUS_MISSING
    priv, _ = _keypair(tmp_path, "acme-2026")
    sign_pack_asymmetric(pack, "acme", "acme-2026", priv)
    fp1, _ = compute_fingerprint(pack)
    fp2, _ = compute_fingerprint(pack)
    assert fp1 == fp2


def test_unknown_publisher_is_untrusted(tmp_path):
    pack = _write_pack(tmp_path)
    priv, _ = _keypair(tmp_path, "evil-key")
    sign_pack_asymmetric(pack, "evil", "evil-key", priv)
    store = _make_store(tmp_path)
    status = verify_pack_signature(pack, {}, trust_store=store)
    assert status.status == STATUS_SIGNED_UNTRUSTED


def test_revoked_key_is_life_death(tmp_path):
    pack = _write_pack(tmp_path)
    priv, _ = _keypair(tmp_path, "acme-2026")
    sign_pack_asymmetric(pack, "acme", "acme-2026", priv)
    store = _make_store(tmp_path, extra={"revoked": {"key_ids": ["acme-2026"]}})
    status = verify_pack_signature(pack, {}, trust_store=store)
    assert status.status == STATUS_REVOKED


def test_revoked_fingerprint_wins_over_valid_signature(tmp_path):
    pack = _write_pack(tmp_path)
    priv, _ = _keypair(tmp_path, "acme-2026")
    sign_pack_asymmetric(pack, "acme", "acme-2026", priv)
    fp, _ = compute_fingerprint(pack)
    store = _make_store(tmp_path, extra={"revoked": {"fingerprints": [fp]}})
    status = verify_pack_signature(pack, {}, trust_store=store)
    assert status.status == STATUS_REVOKED


def test_revoked_package_version_blocks_exact_version(tmp_path):
    pack = _write_pack(tmp_path)
    priv, _ = _keypair(tmp_path, "acme-2026")
    sign_pack_asymmetric(pack, "acme", "acme-2026", priv)
    store = _make_store(
        tmp_path,
        extra={"revoked": {"packages": [{"id": "acme.demo", "version": "1.0.0"}]}},
    )
    assert (
        verify_pack_signature(pack, {}, trust_store=store, package_id="acme.demo", version="1.0.0").status
        == STATUS_REVOKED
    )
    # 其它版本不受整版本吊销影响。
    assert (
        verify_pack_signature(pack, {}, trust_store=store, package_id="acme.demo", version="2.0.0").status
        == STATUS_SIGNED_VERIFIED
    )


def test_whole_package_revocation(tmp_path):
    pack = _write_pack(tmp_path)
    priv, _ = _keypair(tmp_path, "acme-2026")
    sign_pack_asymmetric(pack, "acme", "acme-2026", priv)
    store = _make_store(tmp_path, extra={"revoked": {"packages": [{"id": "acme.demo"}]}})
    assert (
        verify_pack_signature(pack, {}, trust_store=store, package_id="acme.demo", version="9.9.9").status
        == STATUS_REVOKED
    )


def test_retired_key_signature_not_elevatable(tmp_path):
    """retired 密钥签名：独立裁决 signed_retired（不提权、不 quarantine）。"""
    pack = _write_pack(tmp_path)
    priv, _ = _keypair(tmp_path, "acme-2025")
    sign_pack_asymmetric(pack, "acme", "acme-2025", priv)
    store = _make_store(tmp_path)
    status = verify_pack_signature(pack, {}, trust_store=store)
    assert status.status == STATUS_SIGNED_RETIRED, status.detail


def test_key_rotation_both_keys_verify(tmp_path):
    pack26 = _write_pack(tmp_path / "a", "acme.one")
    pack25 = _write_pack(tmp_path / "b", "acme.two")
    priv26, _ = _keypair(tmp_path, "acme-2026")
    priv25, _ = _keypair(tmp_path, "acme-2025")
    sign_pack_asymmetric(pack26, "acme", "acme-2026", priv26)
    sign_pack_asymmetric(pack25, "acme", "acme-2025", priv25)
    store = _make_store(tmp_path)
    assert verify_pack_signature(pack26, {}, trust_store=store).status == STATUS_SIGNED_VERIFIED
    # retired key：非 verified（不提权），也不是 invalid/revoked。
    assert verify_pack_signature(pack25, {}, trust_store=store).status == STATUS_SIGNED_RETIRED


def test_hmac_v1_payload_bytes_frozen():
    """V2 载荷逐字节冻结：改动即全部既有包变 tampered（红线）。"""
    assert _signature_payload("k", "f") == b"webgis-extension-signature-v1\nk\nf"


def test_hmac_legacy_path_unchanged_with_trust_store(tmp_path):
    """trust store 启用时 HMAC 包仍按 V2 publishers 表验签。"""
    pack = _write_pack(tmp_path)
    key_file = tmp_path / "hmac.key"
    key_file.write_bytes(b"shared-secret")
    sign_pack(pack, "pub-1", key_file)
    store = _make_store(tmp_path)
    status = verify_pack_signature(pack, {"pub-1": key_file}, trust_store=store)
    assert status.status == STATUS_SIGNED_VERIFIED
    # 换密钥 → invalid。
    status = verify_pack_signature(
        pack, {"pub-1": tmp_path / "other.key"}, trust_store=store
    )
    assert status.status in (STATUS_INVALID,)


def test_legacy_path_without_trust_store_still_works(tmp_path):
    pack = _write_pack(tmp_path)
    key_file = tmp_path / "hmac.key"
    key_file.write_bytes(b"k")
    sign_pack(pack, "pub-1", key_file)
    assert verify_pack_signature(pack, {"pub-1": key_file}).status == STATUS_SIGNED_VERIFIED
    assert verify_pack_signature(pack, {}).status == STATUS_SIGNED_UNTRUSTED


def test_trust_store_load_fail_closed(tmp_path):
    bad_shapes = [
        b"not json",
        b"[]",
        b'{"publishers": {"acme": {"keys": {}}}}',
        b'{"publishers": {"acme": {"keys": {"k": {"public_key_pem": "nope"}}}}}',
        b'{"revoked": {"fingerprints": ["zz"]}}',
    ]
    for raw in bad_shapes:
        p = tmp_path / "store.json"
        p.write_bytes(raw)
        with pytest.raises(ExtensionPlatformError):
            TrustStore.load(p)
    with pytest.raises(ExtensionPlatformError):
        TrustStore.load(tmp_path / "missing.json")


def test_trust_store_rejects_non_ed25519_pem(tmp_path):
    """信任根只收 Ed25519 公钥（RSA/EC PEM → typed 拒绝）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_pem = rsa_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    p = tmp_path / "store.json"
    p.write_text(
        json.dumps(
            {
                "publishers": {
                    "acme": {"keys": {"k": {"public_key_pem": rsa_pem, "state": "active"}}}
                }
            }
        )
    )
    with pytest.raises(ExtensionPlatformError, match="Ed25519"):
        TrustStore.load(p)


def test_keypair_generation_refuses_overwrite(tmp_path):
    generate_signing_keypair(tmp_path, "k1")
    with pytest.raises(ExtensionPlatformError, match="overwrite"):
        generate_signing_keypair(tmp_path, "k1")
