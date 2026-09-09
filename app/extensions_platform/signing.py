"""扩展包内容签名与验签（ADR-0105 / Wave 6）。

供应链模型::

    发布者侧：sign_pack(pack_dir, key_id, key_file)
        内容指纹（discovery.compute_fingerprint，已排除 signature.json）
        → HMAC-SHA256(domain || key_id || fingerprint) → 写 signature.json

    宿主侧：verify_pack_signature(pack_dir, trusted_publishers)
        纯函数式裁决（确定性、无网络、无时间语义）→ SignatureStatus
        host.discover() 依裁决执行信任流（隔离 / 提权 / 告警）。

设计边界：
- **签名覆盖内容，不覆盖自身**：signature.json 被内容指纹排除（见
  discovery.SIGNATURE_FILENAME），否则指纹循环依赖；
- **无 cryptography 依赖**：stdlib ``hmac`` + ``hashlib``；密钥文件即
  对称 HMAC 密钥（key_id → 密钥文件由运维经 EXTENSION_TRUSTED_PUBLISHERS
  配置，settings_bridge.parse_trusted_publishers 解析）；
- **fail closed**：指纹不可用 / 密钥不可读 → typed 异常；验签遇到任何
  形状问题 → "invalid"（宿主侧隔离），绝不存在「签名损坏当没签名」；
- **signed_at 纯信息性**：时间戳永不参与验签（验签必须确定性、可重放）；
- 域分隔（domain separation）防跨协议重放：载荷前缀
  ``webgis-extension-signature-v1``。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError
from .discovery import SIGNATURE_FILENAME, compute_fingerprint

# HMAC 载荷域分隔前缀（与指纹的 webgis-extension-fingerprint-v1 同风格）。
SIGNATURE_DOMAIN = b"webgis-extension-signature-v1\n"
SIGNATURE_ALGORITHM = "hmac-sha256"

# SignatureStatus.status 的封闭词表（宿主/CLI 据此分派，不做模糊匹配）。
STATUS_SIGNED_VERIFIED = "signed_verified"
STATUS_SIGNED_UNTRUSTED = "signed_untrusted"
STATUS_INVALID = "invalid"
STATUS_TAMPERED = "tampered"
STATUS_MISSING = "missing"

# signature.json 的必填字段（algorithm / key_id / fingerprint / signature
# 决定验签结果；signed_at 纯信息性、可缺省）。
_REQUIRED_FIELDS = ("algorithm", "key_id", "fingerprint", "signature")


def _signature_payload(key_id: str, fingerprint_hex: str) -> bytes:
    """HMAC 载荷：域分隔前缀 + key_id + 指纹（hex），``\\n`` 分段防拼接歧义。"""
    return SIGNATURE_DOMAIN + key_id.encode("utf-8") + b"\n" + fingerprint_hex.encode("utf-8")


def sign_pack(pack_dir: Path, key_id: str, key_file: Path) -> dict:
    """对扩展包做内容签名，写 signature.json 并返回其内容 dict。

    - 指纹不可用（目录超界等）或密钥文件不可读 → typed
      ExtensionPlatformError（fail closed，绝不产出半份签名）；
    - ``signed_at`` 为 ISO8601 UTC，纯信息性；
    - 重复签名幂等：除 ``signed_at`` 外各字段确定性（同内容同密钥 →
      同指纹同签名）。
    """
    pack_dir = Path(pack_dir)
    fingerprint, diag = compute_fingerprint(pack_dir)
    if fingerprint is None:
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                "cannot sign pack: content fingerprint unavailable "
                f"({diag.message if diag else 'unknown reason'})",
            )
        )
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
    (pack_dir / SIGNATURE_FILENAME).write_text(
        json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return doc


@dataclass(frozen=True)
class SignatureStatus:
    """验签裁决（封闭词表；宿主与 CLI 共同消费）。"""

    status: str  # signed_verified | signed_untrusted | invalid | tampered | missing
    publisher: Optional[str] = None
    detail: str = ""


def verify_pack_signature(pack_dir: Path, publishers: dict[str, Path]) -> SignatureStatus:
    """确定性验签：无网络、无时间语义；所有分支可重放。

    裁决顺序（先形状后语义，fail closed）：
    1. 无 signature.json → missing（宿主按策略决定是否告警）；
    2. 形状非法（坏 JSON / 缺字段 / 算法不符）→ invalid；
    3. key_id 不在受信发布者集合 → signed_untrusted（签名存在但发布者
       未知——宿主回退运维信任配置，绝不因「有签名」而放行）；
    4. 内容指纹与签名中记载不一致 → tampered（签名之后内容被改）；
    5. HMAC 不匹配 → invalid（指纹一致但签名对不上：密钥不符/伪造）；
    6. 否则 → signed_verified。
    """
    pack_dir = Path(pack_dir)
    sig_path = pack_dir / SIGNATURE_FILENAME
    if not sig_path.is_file():
        return SignatureStatus(STATUS_MISSING)
    try:
        doc = json.loads(sig_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        return SignatureStatus(STATUS_INVALID, detail="signature.json is not valid JSON")
    if not isinstance(doc, dict) or any(
        not isinstance(doc.get(field), str) or not doc.get(field) for field in _REQUIRED_FIELDS
    ):
        return SignatureStatus(STATUS_INVALID, detail="signature.json is missing required fields")
    if doc["algorithm"] != SIGNATURE_ALGORITHM:
        return SignatureStatus(
            STATUS_INVALID,
            detail=f"unsupported signature algorithm {doc['algorithm']!r}",
        )
    key_id = doc["key_id"]
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
