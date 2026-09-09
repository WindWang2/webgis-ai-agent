"""Marketplace service：publish / search / 生命周期（ADR-0119 / Wave 5）。

publish 强制前置（全部通过才落库，缺一 typed 拒绝）：
1. tar 解包到临时 staging（zip-slip 安全解包复用 distribution 的硬防线）；
2. manifest 可解析、id 与声明一致；
3. blob digest 对账（sha256）；
4. trust store 验签（ed25519；active key；retired → 拒绝发布——发布面
   只收新签名）+ 包级/指纹级 revocation 预检；
5. publisher allowlist（registry 运维配置；包 identity claim-once，
   第二 publisher 发布同 id = dependency-confusion 防线 typed 拒绝）；
6. SBOM secret 扫描 clean。

读面（search/get）确定性：`(id asc, version desc)`，全内存有界索引。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..diagnostics import ExtensionPlatformError
from ..discovery import MANIFEST_FILENAME, _parse_manifest_file
from ..trust_store import TrustStore
from .models import (
    PACKAGE_STATUS_DEPRECATED,
    PACKAGE_STATUS_REVOKED,
    PackageRecord,
    RegistryState,
    VersionRecord,
)
from .store import RegistryStore, _store_error


@dataclass(frozen=True)
class PublishPolicy:
    """registry 运维策略（CLI 层消费）。"""

    allowed_publishers: frozenset[str] = frozenset()  # 空 = 拒绝一切发布
    max_blob_bytes: int = 64 * 1024 * 1024


@dataclass(frozen=True)
class SearchResult:
    total: int
    items: tuple[dict[str, Any], ...]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RegistryService:
    """registry 读写服务（写路径全部在 store 文件锁内）。"""

    def __init__(
        self,
        store: RegistryStore,
        trust_store: Optional[TrustStore] = None,
        policy: Optional[PublishPolicy] = None,
    ) -> None:
        self._store = store
        self._trust_store = trust_store
        self._policy = policy or PublishPolicy()

    @property
    def store(self) -> RegistryStore:
        return self._store

    # ── 发布（锁内全前置）─────────────────────────────────────────────
    def publish(self, package_blob: bytes) -> dict[str, Any]:
        """发布一个已签名的包 blob（.tar.gz 字节）。返回发布摘要。

        前置失败 typed 拒绝且**零状态残留**（staging 清理、索引未动）。
        """
        if self._trust_store is None:
            raise _store_error("publish requires a trust store (asymmetric-only intake)")
        if len(package_blob) > self._policy.max_blob_bytes:
            raise _store_error(f"package blob exceeds {self._policy.max_blob_bytes} bytes")
        import tempfile

        with tempfile.TemporaryDirectory(prefix="ext-publish-") as tmp:
            staging = Path(tmp) / "pack"
            from ..distribution import safe_extract_package

            try:
                safe_extract_package(package_blob, staging)
            except ExtensionPlatformError:
                raise
            manifest, diags, _ = _parse_manifest_file(staging / MANIFEST_FILENAME)
            if manifest is None:
                raise _store_error(
                    "package manifest invalid: "
                    + ("; ".join(d.message for d in diags) or "unknown")
                )
            # digest + 签名 + 吊销（对解包后的目录验签；指纹绑定同 blob）。
            from ..signing import (
                STATUS_SIGNED_VERIFIED,
                verify_pack_signature,
            )

            status = verify_pack_signature(
                staging,
                publishers={},
                trust_store=self._trust_store,
                package_id=manifest.id,
                version=manifest.version,
            )
            if status.status != STATUS_SIGNED_VERIFIED:
                raise _store_error(
                    f"package signature not acceptable: {status.status} ({status.detail})"
                )
            sbom_digest = self._scan_sbom_or_raise(staging, manifest)
            fingerprint, fp_diag = _fingerprint(staging)
            if fp_diag is not None or fingerprint is None:
                raise _store_error(
                    f"package fingerprint unavailable: {fp_diag.message if fp_diag else '?'}"
                )
            signature_doc = json.loads(
                (staging / "signature.json").read_text(encoding="utf-8")
            )
        # Mi-2：allowlist 拒绝发生在任何 blob 落盘之前（不产孤儿 blob）。
        if (
            status.publisher
            and self._policy.allowed_publishers
            and status.publisher not in self._policy.allowed_publishers
        ):
            raise _store_error(
                f"publisher {status.publisher!r} is not in the registry allowlist"
            )
        with self._store.locked():
            self._store.publish_blob(package_blob)
            digest = hashlib.sha256(package_blob).hexdigest()
            version = VersionRecord(
                version=manifest.version,
                digest=digest,
                size_bytes=len(package_blob),
                publisher=status.publisher or "",
                key_id=signature_doc["key_id"],
                signature=signature_doc,
                fingerprint=fingerprint,
                sbom_digest=sbom_digest,
                permissions=sorted(manifest.permissions),
                api_version=manifest.api_version,
                min_core_version=manifest.minimum_core_version,
                dependencies=[d.model_dump() for d in manifest.dependencies],
                created_at=_now_iso(),
            )
            package = PackageRecord(
                id=manifest.id,
                title=manifest.title or manifest.name,
                description=manifest.description,
                publisher=status.publisher or "",
                versions={},
            )
            state = self._store.commit_package(package, version)
        return {
            "package_id": manifest.id,
            "version": manifest.version,
            "digest": digest,
            "generation": state.generation,
        }

    def _scan_sbom_or_raise(self, staging: Path, manifest: Any) -> str:
        from ..sbom import build_sbom

        fingerprint, _ = _fingerprint(staging)
        if fingerprint is None:
            raise _store_error("sbom scan failed: fingerprint unavailable")
        sbom = build_sbom(staging, manifest, fingerprint)
        scan = sbom.get("secret_scan") or {}
        if not scan.get("clean", False):
            raise _store_error(f"SBOM secret scan found findings: {scan.get('findings')}")
        return hashlib.sha256(
            json.dumps(sbom, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    # ── 读面（确定性）────────────────────────────────────────────────
    def search(
        self,
        query: str = "",
        tag: str = "",
        publisher: str = "",
        include_revoked: bool = False,
        offset: int = 0,
        limit: int = 20,
    ) -> SearchResult:
        state = self._store.load_state()
        q = query.strip().lower()
        hits: list[PackageRecord] = []
        for package_id in sorted(state.packages):
            record = state.packages[package_id]
            if not include_revoked and record.status == PACKAGE_STATUS_REVOKED:
                continue
            if publisher and record.publisher != publisher:
                continue
            if tag and tag not in record.tags:
                continue
            if q and q not in package_id.lower() and q not in record.title.lower() and q not in record.description.lower():
                continue
            hits.append(record)
        window = hits[offset : offset + limit]
        return SearchResult(
            total=len(hits),
            items=tuple(self._summarize(record) for record in window),
        )

    def get_package(self, package_id: str) -> Optional[PackageRecord]:
        return self._store.load_state().packages.get(package_id)

    def get_version(self, package_id: str, version: Optional[str] = None) -> Optional[VersionRecord]:
        """取版本记录；version=None = latest（非 yanked semver 最大）。"""
        record = self.get_package(package_id)
        if record is None:
            return None
        if version is None:
            latest = record.latest_version()
            return record.versions.get(latest) if latest else None
        return record.versions.get(version)

    # ── 生命周期（幂等）──────────────────────────────────────────────
    def deprecate(self, package_id: str, note: str) -> RegistryState:
        with self._store.locked():
            return self._store.mutate_package(
                package_id,
                lambda rec: rec.model_copy(
                    update={
                        "status": PACKAGE_STATUS_DEPRECATED,
                        "deprecation_note": note or rec.deprecation_note,
                    }
                ),
            )

    def revoke(self, package_id: str, version: Optional[str] = None) -> RegistryState:
        """吊销包/版本：registry 状态 + trust store（传播给全部宿主）。

        语义分级：version=None = 整包吊销（registry 包状态 REVOKED，
        一切安装/回滚拒绝）；version=X = 仅该版本吊销（trust store 版本
        级条目 + yank；包状态降为 deprecated——其余版本仍可安装，精确
        拦截发生在 preflight 的 is_package_revoked(id, version)）。
        """
        # trust store 写与 registry 索引写同锁串行（Round-1 CR-2）：
        # 吊销是安全控制，杜绝「registry 已标记、trust store 未落」窗口。
        with self._store.locked():
            if version is None:
                state = self._store.mutate_package(
                    package_id,
                    lambda rec: rec.model_copy(
                        update={
                            "status": PACKAGE_STATUS_REVOKED,
                            "deprecation_note": rec.deprecation_note or "revoked",
                        }
                    ),
                )
            else:
                state = self._store.mutate_package(
                    package_id,
                    lambda rec: rec.model_copy(
                        update={
                            "status": (
                                PACKAGE_STATUS_REVOKED
                                if rec.status == PACKAGE_STATUS_REVOKED
                                else PACKAGE_STATUS_DEPRECATED
                            ),
                            "deprecation_note": rec.deprecation_note
                            or f"version {version} revoked",
                            "versions": {
                                v: (
                                    vr.model_copy(update={"yanked": True})
                                    if v == version
                                    else vr
                                )
                                for v, vr in rec.versions.items()
                            },
                        }
                    ),
                )
            if self._trust_store is not None:
                self._trust_store.revoke_package(package_id, version)
        return state

    def yank(self, package_id: str, version: str) -> RegistryState:
        with self._store.locked():
            return self._store.mutate_package(
                package_id,
                lambda rec: rec.model_copy(
                    update={
                        "versions": {
                            v: (vr.model_copy(update={"yanked": True}) if v == version else vr)
                            for v, vr in rec.versions.items()
                        }
                    }
                ),
            )

    def _summarize(self, record: PackageRecord) -> dict[str, Any]:
        latest = record.latest_version()
        return {
            "id": record.id,
            "title": record.title,
            "description": record.description,
            "publisher": record.publisher,
            "status": record.status,
            "deprecation_note": record.deprecation_note,
            "latest_version": latest,
            "versions": sorted(
                record.versions,
                key=lambda v: (_semver_key(v), v),
                reverse=True,
            )[:64],
            "tags": list(record.tags),
        }


def _semver_key(version: str) -> tuple[int, int, int]:
    from ..api_version import parse_version

    parsed = parse_version(version)
    return parsed if parsed is not None else (-1, -1, -1)


def _fingerprint(staging: Path):
    from ..discovery import compute_fingerprint

    return compute_fingerprint(staging)
