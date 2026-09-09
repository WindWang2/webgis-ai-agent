"""扩展信任根：发布者公钥、rotation 与 revocation（ADR-0119 / Wave 4）。

信任根是一份运维维护的 JSON 文档（``EXTENSION_TRUST_STORE_PATH``）::

    {
      "publishers": {
        "acme": {
          "keys": {
            "acme-2026": {"public_key_pem": "-----BEGIN PUBLIC KEY-----...",
                          "state": "active"},
            "acme-2025": {"public_key_pem": "...", "state": "retired"}
          }
        }
      },
      "revoked": {
        "key_ids": ["acme-2024"],
        "fingerprints": ["<sha256hex>"],
        "packages": [{"id": "acme.foo", "version": "1.0.0"},
                     {"id": "acme.bar"}]
      }
    }

判定语义（全部确定性、fail closed）：

- **revoked fingerprint / (id, version) / key** → 吊销：验签无从谈起，
  宿主按 quarantine 处理（等同 tampered 的生死语义）；
- **active key + Ed25519 验签通过** → ``signed_verified``（可提权）；
- **retired key 验签通过** → 新裁决 ``signed_retired``：签名在数学上有效，
  但密钥已退役——**绝不提权**、产出 warning。这是防「密钥泄露后运维
  retire（而非 revoke，因为还想让旧包可验）→ 攻击者用旧钥签新恶意包」
  的降级攻击：retire 只表示「新签名不再被签发」，不表示「旧钥签的新包
  可信」；
- **unknown publisher / key** → ``signed_untrusted``；
- 包级 revocation 优先于一切验签（先查吊销再看签名，吊销是运维意志，
  不受包内容影响）。

无网络、无时间语义、可重放：``loaded_at`` 纯信息性，文档自描述。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError

# key state 词表（封闭）。
KEY_STATE_ACTIVE = "active"
KEY_STATE_RETIRED = "retired"
KEY_STATE_REVOKED = "revoked"
_KEY_STATES = frozenset({KEY_STATE_ACTIVE, KEY_STATE_RETIRED, KEY_STATE_REVOKED})

MAX_TRUST_STORE_BYTES = 256 * 1024
MAX_PUBLISHERS = 256
MAX_KEYS_PER_PUBLISHER = 32
MAX_REVOKED_ENTRIES = 4096

_PEM_BEGIN = "-----BEGIN PUBLIC KEY-----"


@dataclass(frozen=True)
class TrustStore:
    """不可变信任根（加载期一次性校验；运行期零 I/O）。"""

    publishers: dict[str, dict[str, str]] = field(default_factory=dict)
    # publisher -> key_id -> {"public_key_pem": str, "state": str}
    revoked_key_ids: frozenset[str] = frozenset()
    revoked_fingerprints: frozenset[str] = frozenset()
    # (package_id, version|None)；version=None = 整包吊销。
    revoked_packages: tuple[tuple[str, Optional[str]], ...] = ()

    # ── 加载 ─────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: Path) -> "TrustStore":
        path = Path(path)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"trust store unreadable at {str(path)!r}: {exc}",
                )
            ) from exc
        if len(raw) > MAX_TRUST_STORE_BYTES:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"trust store exceeds {MAX_TRUST_STORE_BYTES} bytes",
                )
            )
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"trust store is not valid JSON: {exc}",
                )
            ) from exc
        if not isinstance(data, dict):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    "trust store must be a JSON object",
                )
            )
        publishers_raw = data.get("publishers") or {}
        if not isinstance(publishers_raw, dict) or len(publishers_raw) > MAX_PUBLISHERS:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    "trust store 'publishers' must be an object with at most "
                    f"{MAX_PUBLISHERS} publishers",
                )
            )
        publishers: dict[str, dict[str, str]] = {}
        for name, entry in publishers_raw.items():
            if not isinstance(name, str) or not name.strip():
                raise _store_error("publisher name must be a non-empty string")
            keys_raw = (entry or {}).get("keys") if isinstance(entry, dict) else None
            if not isinstance(keys_raw, dict) or not keys_raw:
                raise _store_error(f"publisher {name!r} must declare a non-empty 'keys' object")
            if len(keys_raw) > MAX_KEYS_PER_PUBLISHER:
                raise _store_error(
                    f"publisher {name!r} exceeds {MAX_KEYS_PER_PUBLISHER} keys"
                )
            for key_id, key_entry in keys_raw.items():
                if not isinstance(key_id, str) or not key_id.strip():
                    raise _store_error(f"publisher {name!r}: key_id must be non-empty string")
                if not isinstance(key_entry, dict):
                    raise _store_error(f"publisher {name!r} key {key_id!r} must be an object")
                pem = key_entry.get("public_key_pem")
                state = key_entry.get("state", KEY_STATE_ACTIVE)
                if not isinstance(pem, str) or _PEM_BEGIN not in pem:
                    raise _store_error(
                        f"publisher {name!r} key {key_id!r}: public_key_pem must be "
                        "a PEM 'BEGIN PUBLIC KEY' block"
                    )
                if state not in _KEY_STATES:
                    raise _store_error(
                        f"publisher {name!r} key {key_id!r}: state {state!r} must be "
                        f"one of {sorted(_KEY_STATES)}"
                    )
                _validate_public_key_pem(name, key_id, pem)
                publishers.setdefault(name, {})[key_id] = {
                    "public_key_pem": pem,
                    "state": state,
                }
        revoked = data.get("revoked") or {}
        if not isinstance(revoked, dict):
            raise _store_error("'revoked' must be an object")
        key_ids = revoked.get("key_ids") or []
        fingerprints = revoked.get("fingerprints") or []
        packages_raw = revoked.get("packages") or []
        if not all(isinstance(k, str) and k for k in key_ids):
            raise _store_error("revoked.key_ids must be non-empty strings")
        if not all(isinstance(f, str) and _is_sha256_hex(f) for f in fingerprints):
            raise _store_error("revoked.fingerprints must be sha256 hex strings")
        packages: list[tuple[str, Optional[str]]] = []
        for item in packages_raw:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise _store_error("revoked.packages entries must be objects with 'id'")
            version = item.get("version")
            if version is not None and not isinstance(version, str):
                raise _store_error("revoked.packages 'version' must be a string or null")
            packages.append((item["id"], version))
        total = len(key_ids) + len(fingerprints) + len(packages)
        if total > MAX_REVOKED_ENTRIES:
            raise _store_error(
                f"revocation lists exceed {MAX_REVOKED_ENTRIES} total entries"
            )
        return cls(
            publishers=publishers,
            revoked_key_ids=frozenset(key_ids),
            revoked_fingerprints=frozenset(fingerprints),
            revoked_packages=tuple(packages),
        )

    # ── 吊销判定（包级，先于验签）────────────────────────────────────
    def is_package_revoked(self, package_id: str, version: Optional[str]) -> bool:
        """(id, version) 吊销判定；version=None 的条目 = 整包吊销。"""
        for revoked_id, revoked_version in self.revoked_packages:
            if revoked_id != package_id:
                continue
            if revoked_version is None or revoked_version == version:
                return True
        return False

    def is_fingerprint_revoked(self, fingerprint: str) -> bool:
        return fingerprint in self.revoked_fingerprints

    def key_state(self, publisher: str, key_id: str) -> Optional[str]:
        """publisher+key_id 的状态；未知 → None（调用方判 untrusted）。"""
        key = self.publishers.get(publisher, {}).get(key_id)
        if key is None:
            # key 级吊销独立于 publisher 表存在（吊销先于密钥下架也合法）。
            if key_id in self.revoked_key_ids:
                return KEY_STATE_REVOKED
            return None
        if key_id in self.revoked_key_ids:
            return KEY_STATE_REVOKED
        return key["state"]

    def public_key_pem(self, publisher: str, key_id: str) -> Optional[str]:
        key = self.publishers.get(publisher, {}).get(key_id)
        return key["public_key_pem"] if key is not None else None


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _validate_public_key_pem(publisher: str, key_id: str, pem: str) -> None:
    """加载期把「不是合法 Ed25519 公钥」的 PEM 拒掉（运行期零解析失败面）。"""
    from cryptography.hazmat.primitives import serialization

    try:
        key = serialization.load_pem_public_key(pem.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 任何解析失败都是信任根配置错误
        raise _store_error(
            f"publisher {publisher!r} key {key_id!r}: public_key_pem is not a "
            f"valid PEM public key ({type(exc).__name__})"
        ) from exc
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(key, Ed25519PublicKey):
        raise _store_error(
            f"publisher {publisher!r} key {key_id!r}: only Ed25519 public keys "
            "are supported in the trust store"
        )


def _store_error(message: str) -> ExtensionPlatformError:
    return ExtensionPlatformError(
        ExtensionDiagnostic.error(DiagnosticCode.MANIFEST_PARSE_FAILED, f"trust store: {message}")
    )
