"""扩展包内容签名与验签（ADR-0105 / Wave 6；ADR-0119 / Wave 2-4 非对称化）。

供应链模型::

    发布者侧：sign_pack(pack_dir, key_id, key_file)              [V2, HMAC]
              sign_pack_asymmetric(pack_dir, publisher, key_id, private_key_file)
                                                            [V3, Ed25519]
        内容指纹（discovery.compute_fingerprint，已排除 signature.json）
        → HMAC-SHA256(domain1 || key_id || fingerprint) 或
          Ed25519.sign(domain2 || publisher || key_id || fingerprint)
        → 写 signature.json

    宿主侧：verify_pack_signature(pack_dir, trusted_publishers, trust_store=None)
        纯函数式裁决（确定性、无网络、无时间语义）→ SignatureStatus
        host.discover() 依裁决执行信任流（隔离 / 提权 / 告警）。

设计边界：
- **签名覆盖内容，不覆盖自身**：signature.json 被内容指纹排除（见
  discovery.SIGNATURE_FILENAME），否则指纹循环依赖；
- **无自研 crypto**：V2 = stdlib ``hmac``；V3 = ``cryptography`` 的
  Ed25519（成熟库 hazmat 面）；
- **V2 HMAC 载荷逐字节保留**：v1 域分隔前缀 + key_id + 指纹不动，V2 包
  在 V3 宿主上的验签行为不变；
- **v2 载荷含 publisher**：Ed25519 公钥可公开分发，载荷绑定
  (publisher, key_id, fingerprint) 三元组，防签名挪用到其它 publisher；
- **fail closed**：指纹不可用 / 密钥不可读 → typed 异常；验签遇到任何
  形状问题 → "invalid"（宿主侧隔离），绝不存在「签名损坏当没签名」；
- **signed_at 纯信息性**：时间戳永不参与验签（验签必须确定性、可重放）；
- **retired ≠ verified**：trust store 中 retired 密钥的签名是独立裁决
  ``signed_retired``——数学上有效但绝不提权（见 trust_store 模块 docstring）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError
from .discovery import SIGNATURE_FILENAME, compute_fingerprint

# HMAC 载荷域分隔前缀（与指纹的 webgis-extension-fingerprint-v1 同风格）。
# V2 语义冻结：v1 载荷逐字节不动。
SIGNATURE_DOMAIN = b"webgis-extension-signature-v1\n"
SIGNATURE_ALGORITHM = "hmac-sha256"
# V3（ADR-0119）：Ed25519 非对称签名。
SIGNATURE_DOMAIN_V2 = b"webgis-extension-signature-v2\n"
SIGNATURE_ALGORITHM_ED25519 = "ed25519"

# SignatureStatus.status 的封闭词表（宿主/CLI 据此分派，不做模糊匹配）。
STATUS_SIGNED_VERIFIED = "signed_verified"
STATUS_SIGNED_UNTRUSTED = "signed_untrusted"
STATUS_INVALID = "invalid"
STATUS_TAMPERED = "tampered"
STATUS_MISSING = "missing"
# V3：retired 密钥的签名——数学有效、不提权。
STATUS_SIGNED_RETIRED = "signed_retired"
# V3：包/指纹/密钥被吊销——与 tampered 同级生死语义（宿主 quarantine）。
STATUS_REVOKED = "revoked"

# signature.json 的必填字段（algorithm / key_id / fingerprint / signature
# 决定验签结果；signed_at / publisher 视算法而定）。
_REQUIRED_FIELDS = ("algorithm", "key_id", "fingerprint", "signature")


def _signature_payload(key_id: str, fingerprint_hex: str) -> bytes:
    """HMAC v1 载荷：域分隔前缀 + key_id + 指纹（hex），``\\n`` 分段防拼接歧义。

    V2 语义冻结——任何改动都会让既有包全部变 tampered。
    """
    return SIGNATURE_DOMAIN + key_id.encode("utf-8") + b"\n" + fingerprint_hex.encode("utf-8")


def _signature_payload_v2(publisher: str, key_id: str, fingerprint_hex: str) -> bytes:
    """Ed25519 v2 载荷：绑定 (publisher, key_id, fingerprint)。"""
    return (
        SIGNATURE_DOMAIN_V2
        + publisher.encode("utf-8")
        + b"\n"
        + key_id.encode("utf-8")
        + b"\n"
        + fingerprint_hex.encode("utf-8")
    )


def _fingerprint_or_raise(pack_dir: Path) -> str:
    fingerprint, diag = compute_fingerprint(pack_dir)
    if fingerprint is None:
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                "cannot sign pack: content fingerprint unavailable "
                f"({diag.message if diag else 'unknown reason'})",
            )
        )
    return fingerprint


def _write_signature_doc(pack_dir: Path, doc: dict) -> dict:
    (pack_dir / SIGNATURE_FILENAME).write_text(
        json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return doc


def sign_pack(pack_dir: Path, key_id: str, key_file: Path) -> dict:
    """对扩展包做内容签名，写 signature.json 并返回其内容 dict。

    - 指纹不可用（目录超界等）或密钥文件不可读 → typed
      ExtensionPlatformError（fail closed，绝不产出半份签名）；
    - ``signed_at`` 为 ISO8601 UTC，纯信息性；
    - 重复签名幂等：除 ``signed_at`` 外各字段确定性（同内容同密钥 →
      同指纹同签名）。
    """
    pack_dir = Path(pack_dir)
    fingerprint = _fingerprint_or_raise(pack_dir)
    try:
        key = Path(key_file).read_bytes()
    except OSError as exc:
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                f"cannot read signing key file {str(key_file)!r}: {exc}",
            )
        ) from exc
    signature = hmac.new(key, _signature_payload(key_id, fingerprint), hashlib.sha256).hexdigest()
    doc = {
        "algorithm": SIGNATURE_ALGORITHM,
        "key_id": key_id,
        "fingerprint": fingerprint,
        "signature": signature,
        "signed_at": datetime.now(timezone.utc).isoformat(),
    }
    return _write_signature_doc(pack_dir, doc)


def sign_pack_asymmetric(
    pack_dir: Path, publisher: str, key_id: str, private_key_file: Path
) -> dict:
    """V3（ADR-0119）：Ed25519 内容签名。

    - ``publisher`` 必须与 trust store 中的发布者名一致（验签期绑定）；
    - 私钥文件 = PEM PKCS#8 Ed25519 private key（``generate_signing_keypair``
      产出）；不可读/非 Ed25519 → typed fail closed；
    - 其余幂等/信息性语义与 :func:`sign_pack` 一致。
    """
    pack_dir = Path(pack_dir)
    if not publisher.strip():
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED, "publisher must be a non-empty string"
            )
        )
    fingerprint = _fingerprint_or_raise(pack_dir)
    private = _load_ed25519_private_key(Path(private_key_file))
    signature = _ed25519_sign_b64(
        private, _signature_payload_v2(publisher, key_id, fingerprint)
    )
    doc = {
        "algorithm": SIGNATURE_ALGORITHM_ED25519,
        "key_id": key_id,
        "publisher": publisher,
        "fingerprint": fingerprint,
        "signature": signature,
        "signed_at": datetime.now(timezone.utc).isoformat(),
    }
    return _write_signature_doc(pack_dir, doc)


def generate_signing_keypair(out_dir: Path, key_id: str) -> tuple[Path, Path]:
    """生成 Ed25519 密钥对：``<key_id>.private.pem``（0600）+ ``<key_id>.public.pem``。

    私钥文件权限收紧为属主可读（供应链最小卫生）；已存在 → typed 拒绝
    （绝不静默覆盖既有密钥材料）。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    out_dir = Path(out_dir)
    private_path = out_dir / f"{key_id}.private.pem"
    public_path = out_dir / f"{key_id}.public.pem"
    if private_path.exists() or public_path.exists():
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                f"refusing to overwrite existing keypair for {key_id!r} in "
                f"{str(out_dir)!r}",
            )
        )
    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)
    try:
        private_path.chmod(0o600)
    except OSError:  # noqa: BLE001 - 非 POSIX 平台尽力而为
        pass
    return private_path, public_path


def _load_ed25519_private_key(path: Path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                f"cannot read signing key file {str(path)!r}: {exc}",
            )
        ) from exc
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except Exception as exc:  # noqa: BLE001 - 解析失败归一为 typed
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                f"signing key {str(path)!r} is not a valid PEM private key: "
                f"{type(exc).__name__}",
            )
        ) from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                f"signing key {str(path)!r} must be an Ed25519 private key",
            )
        )
    return key


def _ed25519_sign_b64(private, payload: bytes) -> str:
    import base64

    return base64.b64encode(private.sign(payload)).decode("ascii")


@dataclass(frozen=True)
class SignatureStatus:
    """验签裁决（封闭词表；宿主与 CLI 共同消费）。"""

    status: str  # signed_verified | signed_untrusted | signed_retired | revoked | invalid | tampered | missing
    publisher: Optional[str] = None
    detail: str = ""


def _read_signature_doc(pack_dir: Path) -> tuple[Optional[dict], Optional[SignatureStatus]]:
    """读 + 形状校验 signature.json：(doc, None) 或 (None, status)。"""
    sig_path = pack_dir / SIGNATURE_FILENAME
    if not sig_path.is_file():
        return None, SignatureStatus(STATUS_MISSING)
    try:
        doc = json.loads(sig_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, SignatureStatus(STATUS_INVALID, detail="signature.json is not valid JSON")
    if not isinstance(doc, dict) or any(
        not isinstance(doc.get(field), str) or not doc.get(field) for field in _REQUIRED_FIELDS
    ):
        return None, SignatureStatus(STATUS_INVALID, detail="signature.json is missing required fields")
    return doc, None


def verify_pack_signature(
    pack_dir: Path,
    publishers: dict[str, Path],
    trust_store: Optional[Any] = None,
    package_id: Optional[str] = None,
    version: Optional[str] = None,
) -> SignatureStatus:
    """确定性验签：无网络、无时间语义；所有分支可重放。

    V3 trust store 启用时（``trust_store`` 非 None）：

    0. 包级/指纹级吊销 → ``revoked``（先于验签：吊销是运维意志）；
    1. ed25519：trust store 内查 publisher+key_id；
       revoked key → ``revoked``；retired → ``signed_retired``（不提权）；
       active → Ed25519 验签（v2 载荷）；unknown → ``signed_untrusted``；
    2. hmac-sha256：沿用 V2 publishers 表路径（载荷逐字节不变）。

    未启用 trust store 时行为与 V2 逐字节一致（HMAC only）。

    裁决顺序（先形状后语义，fail closed）：
    1. 无 signature.json → missing（宿主按策略决定是否告警）；
    2. 形状非法（坏 JSON / 缺字段 / 算法不符）→ invalid；
    3. key/publisher 不在受信集合 → signed_untrusted；
    4. 内容指纹与签名中记载不一致 → tampered（签名之后内容被改）；
    5. 签名不匹配 → invalid（伪造/密钥不符）；
    6. 否则 → signed_verified。
    """
    pack_dir = Path(pack_dir)
    doc, status = _read_signature_doc(pack_dir)
    if status is not None:
        return status
    algorithm = doc["algorithm"]
    key_id = doc["key_id"]
    publisher = doc.get("publisher")
    if trust_store is not None:
        return _verify_with_trust_store(
            pack_dir, doc, algorithm, key_id, publisher, trust_store, publishers, package_id, version
        )
    # ── V2 HMAC 路径（无 trust store）：判定顺序与 V2 逐字节一致 ──────
    if algorithm != SIGNATURE_ALGORITHM:
        return SignatureStatus(
            STATUS_INVALID,
            publisher=publisher,
            detail=f"unsupported signature algorithm {algorithm!r}",
        )
    key_file = publishers.get(key_id)
    if key_file is None:
        return SignatureStatus(
            STATUS_SIGNED_UNTRUSTED,
            publisher=key_id,
            detail="publisher not in trusted set",
        )
    try:
        key = Path(key_file).read_bytes()
    except OSError as exc:
        # 受信发布者的密钥文件不可读：宿主配置问题，按 fail closed 处理
        # （等同签名无效，而非降级为未签名）。
        return SignatureStatus(STATUS_INVALID, publisher=key_id, detail=f"key unreadable: {exc}")
    fingerprint, diag = compute_fingerprint(pack_dir)
    if fingerprint is None:
        return SignatureStatus(
            STATUS_INVALID,
            publisher=key_id,
            detail=f"content fingerprint unavailable ({diag.message if diag else 'unknown'})",
        )
    if fingerprint != doc["fingerprint"]:
        return SignatureStatus(
            STATUS_TAMPERED,
            publisher=key_id,
            detail="content fingerprint differs from the signed fingerprint",
        )
    expected = hmac.new(key, _signature_payload(key_id, fingerprint), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, doc["signature"]):
        return SignatureStatus(
            STATUS_INVALID,
            publisher=key_id,
            detail="HMAC mismatch (wrong key or forged signature)",
        )
    return SignatureStatus(STATUS_SIGNED_VERIFIED, publisher=key_id)


def _verify_with_trust_store(
    pack_dir: Path,
    doc: dict,
    algorithm: str,
    key_id: str,
    publisher: Optional[str],
    trust_store: Any,
    publishers: dict[str, Path],
    package_id: Optional[str],
    version: Optional[str],
) -> SignatureStatus:
    """V3 trust store 路径：吊销先于一切，ed25519 走信任根，HMAC 回落 V2 表。"""
    fingerprint, diag = compute_fingerprint(pack_dir)
    if fingerprint is None:
        return SignatureStatus(
            STATUS_INVALID,
            publisher=publisher,
            detail=f"content fingerprint unavailable ({diag.message if diag else 'unknown'})",
        )
    # 0) 吊销先于验签：指纹级 / 包级（吊销是运维意志，不受包内容影响）。
    if trust_store.is_fingerprint_revoked(fingerprint) or (
        package_id is not None and trust_store.is_package_revoked(package_id, version)
    ):
        return SignatureStatus(
            STATUS_REVOKED,
            publisher=publisher,
            detail="package/fingerprint is revoked in the trust store",
        )
    if algorithm == SIGNATURE_ALGORITHM_ED25519:
        return _verify_ed25519(pack_dir, doc, key_id, publisher, fingerprint, trust_store)
    # hmac-sha256 + trust store：HMAC 密钥不在 trust store 模型内，沿用
    # V2 publishers 表（吊销判定已在上面完成）。
    if algorithm != SIGNATURE_ALGORITHM:
        return SignatureStatus(
            STATUS_INVALID,
            publisher=publisher,
            detail=f"unsupported signature algorithm {algorithm!r}",
        )
    return _verify_hmac_half(pack_dir, doc, key_id, fingerprint, publishers)


def _verify_ed25519(
    pack_dir: Path,
    doc: dict,
    key_id: str,
    publisher: Optional[str],
    fingerprint: str,
    trust_store: Any,
) -> SignatureStatus:
    if not isinstance(publisher, str) or not publisher:
        return SignatureStatus(
            STATUS_INVALID, detail="ed25519 signature.json requires 'publisher'"
        )
    state = trust_store.key_state(publisher, key_id)
    if state == "revoked":
        return SignatureStatus(
            STATUS_REVOKED,
            publisher=publisher,
            detail=f"signing key {key_id!r} is revoked",
        )
    if state is None:
        return SignatureStatus(
            STATUS_SIGNED_UNTRUSTED,
            publisher=publisher,
            detail="publisher/key not in trust store",
        )
    if fingerprint != doc["fingerprint"]:
        return SignatureStatus(
            STATUS_TAMPERED,
            publisher=publisher,
            detail="content fingerprint differs from the signed fingerprint",
        )
    ok = _ed25519_verify_b64(
        trust_store.public_key_pem(publisher, key_id),
        _signature_payload_v2(publisher, key_id, fingerprint),
        doc["signature"],
    )
    if not ok:
        return SignatureStatus(
            STATUS_INVALID,
            publisher=publisher,
            detail="Ed25519 signature mismatch (forged or wrong key)",
        )
    if state == "retired":
        return SignatureStatus(
            STATUS_SIGNED_RETIRED,
            publisher=publisher,
            detail="signing key is retired; signature valid but not "
            "eligible for trust elevation",
        )
    return SignatureStatus(STATUS_SIGNED_VERIFIED, publisher=publisher)


def _verify_hmac_half(
    pack_dir: Path, doc: dict, key_id: str, fingerprint: str, publishers: dict[str, Path]
) -> SignatureStatus:
    """HMAC 验签后半程（吊销判定已完成）：与 V2 判定语义一致。"""
    key_file = publishers.get(key_id)
    if key_file is None:
        return SignatureStatus(
            STATUS_SIGNED_UNTRUSTED,
            publisher=key_id,
            detail="publisher not in trusted set",
        )
    try:
        key = Path(key_file).read_bytes()
    except OSError as exc:
        return SignatureStatus(STATUS_INVALID, publisher=key_id, detail=f"key unreadable: {exc}")
    if fingerprint != doc["fingerprint"]:
        return SignatureStatus(
            STATUS_TAMPERED,
            publisher=key_id,
            detail="content fingerprint differs from the signed fingerprint",
        )
    expected = hmac.new(key, _signature_payload(key_id, fingerprint), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, doc["signature"]):
        return SignatureStatus(
            STATUS_INVALID,
            publisher=key_id,
            detail="HMAC mismatch (wrong key or forged signature)",
        )
    return SignatureStatus(STATUS_SIGNED_VERIFIED, publisher=key_id)


def _ed25519_verify_b64(public_key_pem: Optional[str], payload: bytes, signature_b64: str) -> bool:
    """信任根内的公钥验签；任何形状/解析问题 → False（fail closed）。"""
    import base64

    if not isinstance(public_key_pem, str) or not public_key_pem:
        return False
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        if not isinstance(key, Ed25519PublicKey):
            return False
        key.verify(base64.b64decode(signature_b64, validate=True), payload)
        return True
    except Exception:  # noqa: BLE001 - 验签失败即拒绝，不区分原因
        return False
