"""分发安装器：registry → 校验 → staging → 原子换装 → versions/（ADR-0119 / Wave 6-7）。

正确性关键机制（架构挑战 C-1/C-2/C-5/M-3/M-4 的落点）：

- **统一 preflight**：install / upgrade / rollback 共用同一个
  `_preflight`（digest/验签/revocation/pin/downgrade/resolver 冲突）——
  回滚**不绕**吊销；
- **安全解包**：tar 成员白名单（仅 REGTYPE；拒绝 hardlink/symlink/
  device/FIFO/绝对路径/`..`）+ 前置流式预算（条目数/总字节）+
  ``extractall(filter="data")`` 兜底；
- **原子换装固定序**（两次 rename，窗口最小化 + 可恢复）：
  1. active 存在 → `rename(active → versions/<old>)`（目标已存在则用
     唯一后缀名，防版本来回升级撞名 ENOTEMPTY）；
  2. `rename(staging → active)`。
  **启动恢复例程**（`recover_pending_swaps`，先于 staging 清扫）：active
  缺失且 staging 存在合法签名包 → 完成第 2 步；清扫只删 TTL 过期 staging；
- **对已激活扩展的升级**走 `host.upgrade()`（discover 对 ACTIVE 记录只
  警告不换血、activate 幂等 no-op——直接 discover+activate 会静默留在
  旧版，C-1）；
- **中断安装**：staging 目录即事务；崩溃后由恢复例程或 TTL 清扫收敛。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import time
from pathlib import Path
from typing import Any, Optional

from .diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError
from .discovery import (
    FINGERPRINT_MAX_FILES,
    FINGERPRINT_MAX_TOTAL_BYTES,
    MANIFEST_FILENAME,
    _parse_manifest_file,
    compute_fingerprint,
)
from .marketplace.models import PACKAGE_STATUS_REVOKED, RegistryState
from .marketplace.store import RegistryStore
from .trust_store import TrustStore

logger = logging.getLogger(__name__)

STAGING_DIRNAME = ".staging"
VERSIONS_DIRNAME = "versions"
REFRESH_FILENAME = ".refresh"
STAGING_TTL_S = 24 * 3600.0

# 解包预算：与指纹上界一致（签名覆盖的就是这个规模的面）。
MAX_PACKAGE_FILES = FINGERPRINT_MAX_FILES
MAX_PACKAGE_TOTAL_BYTES = FINGERPRINT_MAX_TOTAL_BYTES


class DistributionError(ExtensionPlatformError):
    """安装/回滚/预检失败（typed diagnostic 携带）。"""


def _dist_error(code: DiagnosticCode, message: str) -> DistributionError:
    return DistributionError(ExtensionDiagnostic.error(code, message))


# ── 安全解包（publish 与 install 共用；M-4 防线）───────────────────────
def safe_extract_package(blob: bytes, target_dir: Path) -> None:
    """把包 blob（.tar.gz）解包到 target_dir，成员白名单 + 预算硬防线。

    - 仅常规文件（REGTYPE）；hardlink / symlink / device / FIFO / 目录
      成员（显式 mkdir，不落 tar 目录位）一律拒绝；
    - 前置预算：成员数 ≤ 512、解包总字节 ≤ 8 MiB、单成员 ≤ 8 MiB；
    - 路径形状：相对、无 `..` 段、解析后必须留在 target 内；
    - Python 3.12+ 的 ``filter="data"`` 作为最后兜底（再拒一次一切
      绝对路径/链接/设备逃逸）。
    """
    import io

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    resolved_root = target_dir.resolve()
    total_bytes = 0
    entries = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            members = tar.getmembers()
            if len(members) > MAX_PACKAGE_FILES:
                raise _dist_error(
                    DiagnosticCode.PACKAGE_UNSAFE_ENTRY,
                    f"package has {len(members)} entries; limit {MAX_PACKAGE_FILES}",
                )
            for member in members:
                name = member.name
                if member.isdir():
                    continue
                if not member.isreg():
                    raise _dist_error(
                        DiagnosticCode.PACKAGE_UNSAFE_ENTRY,
                        f"package entry {name!r} is not a regular file "
                        f"(type {member.type!r} rejected)",
                    )
                if name.startswith("/") or ".." in Path(name).parts or "\\" in name:
                    raise _dist_error(
                        DiagnosticCode.PACKAGE_UNSAFE_ENTRY,
                        f"package entry {name!r} has an unsafe path shape",
                    )
                if member.size > MAX_PACKAGE_TOTAL_BYTES:
                    raise _dist_error(
                        DiagnosticCode.PACKAGE_UNSAFE_ENTRY,
                        f"package entry {name!r} exceeds {MAX_PACKAGE_TOTAL_BYTES} bytes",
                    )
                total_bytes += member.size
                entries += 1
                if total_bytes > MAX_PACKAGE_TOTAL_BYTES:
                    raise _dist_error(
                        DiagnosticCode.PACKAGE_UNSAFE_ENTRY,
                        f"package exceeds {MAX_PACKAGE_TOTAL_BYTES} unpacked bytes",
                    )
                destination = (target_dir / name).resolve()
                if not destination.is_relative_to(resolved_root):
                    raise _dist_error(
                        DiagnosticCode.PACKAGE_UNSAFE_ENTRY,
                        f"package entry {name!r} escapes the extraction root",
                    )
            # 白名单逐成员安全抽取（不用 extractall 的默认行为）。
            for member in members:
                if member.isdir():
                    (target_dir / member.name).mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isreg():
                    continue
                destination = (target_dir / member.name).resolve()
                destination.parent.mkdir(parents=True, exist_ok=True)
                source_file = tar.extractfile(member)
                if source_file is None:
                    raise _dist_error(
                        DiagnosticCode.PACKAGE_UNSAFE_ENTRY,
                        f"package entry {member.name!r} unreadable",
                    )
                with source_file, open(destination, "wb") as out:
                    shutil.copyfileobj(source_file, out)
    except tarfile.TarError as exc:
        raise _dist_error(
            DiagnosticCode.PACKAGE_UNSAFE_ENTRY, f"package is not a readable tar.gz: {exc}"
        )
    if entries == 0 or not (target_dir / MANIFEST_FILENAME).is_file():
        raise _dist_error(
            DiagnosticCode.PACKAGE_UNSAFE_ENTRY,
            "package lacks a top-level manifest.json",
        )



def recover_pending_swaps(install_root: Path) -> list[str]:
    """启动恢复（C-2）：active 缺失而 staging 有合法签名包 → 完成换装。

    必须先于 staging TTL 清扫执行；返回恢复的 staging 目录名列表。
    """
    install_root = Path(install_root)
    staging = install_root / STAGING_DIRNAME
    if not staging.is_dir():
        return []
    recovered: list[str] = []
    for candidate in sorted(staging.iterdir()):
        if not candidate.is_dir():
            continue
        manifest, diags, _ = _parse_manifest_file(candidate / MANIFEST_FILENAME)
        if manifest is None:
            continue
        if not (candidate / "signature.json").is_file():
            continue
        active = _active_root(install_root, manifest.id)
        if active.exists():
            continue
        # staging 即事务：合法签名包 = 一次被中断的安装 → 完成第 2 步。
        active.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(candidate), str(active))
        recovered.append(candidate.name)
        logger.warning(
            "distribution: recovered interrupted install from staging %r -> %s",
            candidate.name,
            active.name,
        )
    return recovered


def sweep_staging(install_root: Path, ttl_s: float = STAGING_TTL_S, now: Optional[float] = None) -> int:
    """TTL 清扫（恢复例程之后执行；只删过期 staging）。"""
    now = now if now is not None else time.time()
    staging = Path(install_root) / STAGING_DIRNAME
    if not staging.is_dir():
        return 0
    removed = 0
    for candidate in staging.iterdir():
        try:
            if now - candidate.stat().st_mtime <= ttl_s:
                continue
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def write_refresh_signal(install_root: Path, reason: str) -> None:
    """刷新通知信号（纯通知：host 各自 discover 为准；N-1 不做对账）。

    原子写（temp+rename）：读取端只会看到完整 JSON 或旧文件。
    """
    import tempfile

    install_root = Path(install_root)
    install_root.mkdir(parents=True, exist_ok=True)
    signal = install_root / REFRESH_FILENAME
    fd, tmp = tempfile.mkstemp(dir=str(install_root), prefix=".refresh", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json_dumps({"reason": reason, "at": time.time(), "pid": os.getpid()}) + "\n")
        os.replace(tmp, signal)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_refresh_signal(install_root: Path) -> Optional[dict[str, Any]]:
    signal = Path(install_root) / REFRESH_FILENAME
    if not signal.is_file():
        return None
    try:
        import json as _json

        return _json.loads(signal.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _active_root(install_root: Path, package_id: str = "") -> Path:
    """active pack 目录：`<install_root>/extensions/<safe_pkg_id>`。

    discover 的根约定是 `<root>/<ext>/manifest.json`（深度一层），安装根
    因此按包分目录（多包并存，各自的 active + versions）。
    """
    extensions = Path(install_root) / "extensions"
    if not package_id:
        return extensions
    return extensions / _safe_name(package_id)


class ExtensionInstaller:
    """分发安装器（registry + trust store + host 的编排面）。"""

    def __init__(
        self,
        install_root: Path,
        registry: RegistryState | RegistryStore | Any,
        trust_store: Optional[TrustStore] = None,
        host: Optional[Any] = None,
        *,
        allowed_urls: tuple[str, ...] = (),
        keep_versions: int = 3,
        version_pins: Optional[dict[str, str]] = None,
    ) -> None:
        self._install_root = Path(install_root)
        if isinstance(registry, RegistryStore):
            self._store = registry
        else:
            self._store = None
            self._raw_registry = registry
        self._trust_store = trust_store
        self._host = host
        self._allowed_urls = tuple(allowed_urls)
        self._keep_versions = max(1, int(keep_versions))
        self._version_pins = dict(version_pins or {})

    # ── registry 访问（本地 store 或远端 HTTP）────────────────────────
    def _registry_store(self) -> RegistryStore:
        if self._store is not None:
            return self._store
        raise _dist_error(
            DiagnosticCode.REGISTRY_INVALID,
            "remote registry access requires a RegistryStore-backed source "
            "(EXTENSION_REGISTRY_DIR); remote HTTP fetch is download-only",
        )

    def get_state(self) -> RegistryState:
        return self._registry_store().load_state()

    # ── 安装 ─────────────────────────────────────────────────────────
    def install(self, package_id: str, version: Optional[str] = None) -> dict[str, Any]:
        """从 registry 安装（或升级）一个包。返回安装摘要。"""
        store = self._registry_store()
        record = store.load_state().packages.get(package_id)
        if record is None:
            raise _dist_error(DiagnosticCode.REGISTRY_INVALID, f"unknown package {package_id!r}")
        if record.status == PACKAGE_STATUS_REVOKED:
            raise _dist_error(
                DiagnosticCode.PACKAGE_REVOKED, f"package {package_id!r} is revoked"
            )
        target_version = version or record.latest_version()
        if target_version is None:
            raise _dist_error(
                DiagnosticCode.REGISTRY_INVALID, f"package {package_id!r} has no installable version"
            )
        version_record = record.versions.get(target_version)
        if version_record is None:
            raise _dist_error(
                DiagnosticCode.REGISTRY_INVALID,
                f"package {package_id!r} has no version {target_version!r}",
            )
        blob = store.read_blob(version_record.digest)
        staging = self._stage_from_blob(blob, version_record, package_id, target_version)
        try:
            self._preflight(package_id, target_version, current_version=self._installed_version(package_id))
            summary = self._swap_and_activate(package_id, staging, target_version)
        except BaseException:
            _rmtree_quiet(staging)
            raise
        write_refresh_signal(self._install_root, f"install {package_id}=={target_version}")
        return summary

    def _stage_from_blob(self, blob: bytes, version_record: Any, package_id: str, version: str) -> Path:
        """下载语义的落 stage：digest 对账 + 验签 + 安全解包 + 指纹对账。"""
        digest = hashlib.sha256(blob).hexdigest()
        if digest != version_record.digest:
            raise _dist_error(
                DiagnosticCode.PACKAGE_DIGEST_MISMATCH,
                f"blob digest {digest[:12]!r} != registry digest "
                f"{version_record.digest[:12]!r} for {package_id}=={version}",
            )
        staging_root = self._install_root / STAGING_DIRNAME
        staging_root.mkdir(parents=True, exist_ok=True)
        staging = staging_root / f"{_safe_name(package_id)}-{_safe_name(version)}-{digest[:12]}"
        if staging.exists():
            _rmtree_quiet(staging)
        safe_extract_package(blob, staging)
        from .signing import STATUS_SIGNED_VERIFIED, verify_pack_signature

        status = verify_pack_signature(
            staging,
            publishers={},
            trust_store=self._trust_store,
            package_id=package_id,
            version=version,
        )
        if status.status != STATUS_SIGNED_VERIFIED:
            raise _dist_error(
                DiagnosticCode.SIGNATURE_INVALID,
                f"staged package {package_id}=={version} signature not acceptable: "
                f"{status.status} ({status.detail})",
            )
        fingerprint, fp_diag = compute_fingerprint(staging)
        if fp_diag is not None or fingerprint is None:
            raise _dist_error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                f"staged package fingerprint unavailable: {fp_diag.message if fp_diag else '?'}",
            )
        if fingerprint != version_record.fingerprint:
            raise _dist_error(
                DiagnosticCode.PACKAGE_TAMPERED,
                f"staged fingerprint differs from registry fingerprint for "
                f"{package_id}=={version}",
            )
        return staging

    def _current_trust_store(self) -> Optional[TrustStore]:
        """吊销判定前重读 trust store 文件（惰性传播：任何 install/rollback
        都拿到最新吊销面；文件加载实例才有 source_path）。"""
        if self._trust_store is None:
            return None
        source = getattr(self._trust_store, "source_path", None)
        if source is not None:
            try:
                return TrustStore.load(Path(source))
            except ExtensionPlatformError:
                # 信任根暂时不可读：保留已加载视图（吊销判定宁可保守，
                # 这里不放大成宿主故障；文件级错误在 discover 路径 typed）。
                return self._trust_store
        return self._trust_store

    # ── preflight（install/upgrade/rollback 统一；C-5）────────────────
    def _preflight(
        self,
        package_id: str,
        target_version: str,
        current_version: Optional[str],
        *,
        allow_downgrade: bool = False,
    ) -> None:
        from .api_version import parse_version

        # 1) 吊销（registry 状态 + trust store 双通道）。
        state = self.get_state()
        record = state.packages.get(package_id)
        if record is not None and record.status == PACKAGE_STATUS_REVOKED:
            raise _dist_error(
                DiagnosticCode.PACKAGE_REVOKED,
                f"package {package_id!r} is revoked in the registry",
            )
        trust = self._current_trust_store()
        if trust is not None and trust.is_package_revoked(package_id, target_version):
            raise _dist_error(
                DiagnosticCode.PACKAGE_REVOKED,
                f"{package_id}=={target_version} is revoked in the trust store",
            )
        # 2) 版本 pin。
        pinned = self._version_pins.get(package_id)
        if pinned is not None and pinned != target_version:
            raise _dist_error(
                DiagnosticCode.VERSION_PINNED,
                f"{package_id} is pinned to {pinned!r}; refusing {target_version!r}",
            )
        # 3) downgrade 闸。
        if current_version is not None:
            new_v, cur_v = parse_version(target_version), parse_version(current_version)
            if new_v is not None and cur_v is not None and new_v < cur_v and not allow_downgrade:
                raise _dist_error(
                    DiagnosticCode.INSTALL_PREFLIGHT_FAILED,
                    f"{package_id}: {target_version!r} is older than installed "
                    f"{current_version!r}; use rollback() for explicit downgrade",
                )
        # 4) resolver 依赖冲突（host 视角；无 host = 跳过，CLI 单独跑）。
        if self._host is not None:
            from . import resolver

            views = {
                eid: _record_view(rec)
                for eid, rec in getattr(self._host, "_records", {}).items()
            }
            conflicts = resolver.check_upgrade_conflicts(
                package_id, target_version, views
            )
            if conflicts:
                raise _dist_error(
                    DiagnosticCode.DEPENDENCY_CONFLICT,
                    "; ".join(c.message for c in conflicts),
                )

    # ── 换装 + 激活（固定序 + 恢复语义）───────────────────────────────
    def _swap_and_activate(
        self,
        package_id: str,
        staging: Path,
        target_version: str,
        *,
        allow_downgrade: bool = False,
    ) -> dict[str, Any]:
        active = _active_root(self._install_root, package_id)
        versions_dir = self._install_root / VERSIONS_DIRNAME / _safe_name(package_id)
        versions_dir.mkdir(parents=True, exist_ok=True)
        current_version = self._installed_version(package_id)
        # 1) active → versions/<old>（目标已存在 → digest 唯一名，防撞名）。
        if active.is_dir():
            archive_name = _safe_name(current_version or "unknown")
            archive = versions_dir / archive_name
            if archive.exists():
                fp, _ = compute_fingerprint(active)
                archive = versions_dir / f"{archive_name}-{(fp or 'x')[:8]}"
            if archive.exists():
                _rmtree_quiet(archive)
            os.rename(str(active), str(archive))
            # Mi-8：rename 不更新被归档目录自身 mtime——刷新入位时间，
            # 防 prune 按目录 mtime 把刚归档的上一版判「最旧」先删。
            try:
                os.utime(archive, None)
            except OSError:
                pass
        # 2) staging → active（窗口内崩溃 = active 缺失，恢复例程可完成）。
        active.parent.mkdir(parents=True, exist_ok=True)
        os.rename(str(staging), str(active))
        self._prune_versions(versions_dir)
        # 3) 激活（C-1：已激活走 upgrade；未激活走 discover+activate）。
        activation: dict[str, Any] = {"mode": "fresh"}
        if self._host is not None:
            self._host.discover()
            record = self._host.get_record(package_id)
            if record is not None and record.state.value in ("active", "degraded"):
                # 升级路径：deactivate（drain）→ discover（fresh 记录取新
                # 指纹 + 重跑信任/签名裁决）→ activate。不直接调
                # host.upgrade()：其 reload 对「受信扩展内容变更」要求显式
                # re-discover（V2 保护），而本函数的换装恰好换血了磁盘内容。
                # 预检等价性由本 installer 的 _preflight 保证（resolver
                # 冲突/pin/降级闸都在换装前执行）。
                host2 = self._host
                drain_diags = []
                if hasattr(host2, "deactivate"):
                    try:
                        drain_diags = host2.deactivate(package_id, drain=True)
                    except TypeError:  # 旧 host 签名兜底
                        drain_diags = host2.deactivate(package_id)
                if any(d.severity.value == "error" for d in drain_diags):
                    raise _dist_error(
                        DiagnosticCode.OPERATION_IN_FLIGHT,
                        "cannot upgrade: deactivate of running version failed: "
                        + "; ".join(d.message for d in drain_diags if d.severity.value == "error"),
                    )
                host2.unload(package_id)
                host2.discover()
                diags = host2.activate(package_id)
                after = host2.get_record(package_id)
                ok = after is not None and after.state.value in ("active", "degraded")
                activation = {"mode": "upgrade", "ok": ok, "diagnostics": _diag_dump(diags)}
                # Round-1 MAJ-1：升级失败绝不静默成功——把刚归档的旧版
                # 原子换回 active 位并 typed 失败（坏版本不留在 active 位）。
                if not ok:
                    self._restore_archived(package_id, current_version)
                    host2.discover()
                    host2.activate(package_id)
                    raise _dist_error(
                        DiagnosticCode.INSTALL_PREFLIGHT_FAILED,
                        "upgrade activation failed; previous version restored: "
                        + "; ".join(d.message for d in diags if d.severity.value == "error"),
                    )
            elif record is not None:
                diags = self._host.activate(package_id)
                after = self._host.get_record(package_id)
                ok = after is not None and after.state.value in ("active", "degraded")
                activation = {
                    "mode": "activate",
                    "ok": ok,
                    "diagnostics": _diag_dump(diags),
                }
                if not ok:
                    raise _dist_error(
                        DiagnosticCode.INSTALL_PREFLIGHT_FAILED,
                        "activation failed after install: "
                        + "; ".join(d.message for d in diags if d.severity.value == "error"),
                    )
        installed = self._installed_version(package_id)
        if installed is not None and installed != target_version:
            raise _dist_error(
                DiagnosticCode.INSTALL_SWAP_FAILED,
                f"post-install version check: disk={installed!r} expected "
                f"{target_version!r}",
            )
        return {
            "package_id": package_id,
            "version": target_version,
            "activation": activation,
            "install_root": str(self._install_root),
        }

    def _restore_archived(self, package_id: str, version: Optional[str]) -> bool:
        """升级激活失败时把 versions/ 中的旧版换回 active 位（尽力而为）。"""
        if version is None:
            return False
        archive = self._install_root / VERSIONS_DIRNAME / _safe_name(package_id) / _safe_name(version)
        if not archive.is_dir():
            return False
        active = _active_root(self._install_root, package_id)
        try:
            if active.exists():
                _rmtree_quiet(active)
            active.parent.mkdir(parents=True, exist_ok=True)
            os.rename(str(archive), str(active))
            return True
        except OSError:
            return False

    def _prune_versions(self, versions_dir: Path) -> None:
        """有界保留（按 mtime 最旧先删；数量上界 = keep_versions）。"""
        entries = [p for p in versions_dir.iterdir() if p.is_dir()]
        if len(entries) <= self._keep_versions:
            return
        entries.sort(key=lambda p: p.stat().st_mtime)
        for stale in entries[: len(entries) - self._keep_versions]:
            _rmtree_quiet(stale)

    def _installed_version(self, package_id: str) -> Optional[str]:
        manifest_path = _active_root(self._install_root, package_id) / MANIFEST_FILENAME
        if not manifest_path.is_file():
            return None
        manifest, _, _ = _parse_manifest_file(manifest_path)
        if manifest is None or manifest.id != package_id:
            return None
        return manifest.version

    # ── 回滚（同走 preflight；Mi-4 semver 最大缺省）────────────────────
    def rollback(self, package_id: str, version: Optional[str] = None) -> dict[str, Any]:
        from .api_version import parse_version

        versions_dir = self._install_root / VERSIONS_DIRNAME / _safe_name(package_id)
        if not versions_dir.is_dir():
            raise _dist_error(
                DiagnosticCode.INSTALL_PREFLIGHT_FAILED,
                f"no archived versions for {package_id!r}",
            )
        candidates: list[tuple[tuple[int, int, int], str, Path]] = []
        for entry in versions_dir.iterdir():
            if not entry.is_dir():
                continue
            manifest, _, _ = _parse_manifest_file(entry / MANIFEST_FILENAME)
            if manifest is None or manifest.id != package_id:
                continue
            if version is not None and manifest.version != version:
                continue
            parsed = parse_version(manifest.version) or (-1, -1, -1)
            candidates.append((parsed, manifest.version, entry))
        if not candidates:
            raise _dist_error(
                DiagnosticCode.INSTALL_PREFLIGHT_FAILED,
                f"no archived version of {package_id!r} matches {version or 'any'}",
            )
        candidates.sort(key=lambda item: (item[0], item[1]))
        _, target_version, archive = candidates[-1]  # semver 最大
        current_version = self._installed_version(package_id)
        self._preflight(
            package_id, target_version, current_version, allow_downgrade=True
        )
        staging = self._install_root / STAGING_DIRNAME / (
            f"{_safe_name(package_id)}-{_safe_name(target_version)}-rollback"
        )
        if staging.exists():
            _rmtree_quiet(staging)
        shutil.copytree(str(archive), str(staging))
        try:
            summary = self._swap_and_activate(
                package_id, staging, target_version, allow_downgrade=True
            )
        except BaseException:
            _rmtree_quiet(staging)
            raise
        summary["activation"]["mode"] = "rollback"
        write_refresh_signal(self._install_root, f"rollback {package_id}=={target_version}")
        return summary

    # ── 启动收敛（恢复 → 清扫）───────────────────────────────────────
    def bootstrap(self) -> dict[str, Any]:
        recovered = recover_pending_swaps(self._install_root)
        swept = sweep_staging(self._install_root)
        return {"recovered": recovered, "swept_staging": swept}


def _safe_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in value)[:96]


def _rmtree_quiet(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _diag_dump(diags: list) -> list[dict[str, str]]:
    return [
        {"code": d.code.value, "severity": d.severity.value, "message": d.message}
        for d in diags
    ]


def _record_view(record: Any) -> Any:
    from .host import _record_view

    return _record_view(record)
