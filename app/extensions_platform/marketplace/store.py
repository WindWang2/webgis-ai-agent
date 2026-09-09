"""Registry store：content-addressed blob 存储 + 有界索引（ADR-0119 / Wave 5）。

目录布局（`EXTENSION_REGISTRY_DIR`）::

    <registry_root>/
        state.json              # RegistryState（唯一索引事实源）
        objects/<digest[:2]>/<digest>   # 包 blob（.tar.gz，内容寻址）
        .registry.lock          # publish 串行化锁（O_EXCL）

并发语义（架构修订 M-3）：POSIX 无原子 CAS-rename —— **publish 全程持
文件锁串行化**（O_EXCL 创建 + 超时 + 陈旧锁接管），索引写 = temp+rename
原子替换；generation 在锁内单调递增，锁外只读。

GC：持同一把锁、只删「不在当前索引且 mtime 超过 TTL」的 blob（先写
blob 后提交索引的发布序因此安全：并发 publish 引用的 blob 永远新鲜）。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Iterator, Optional

from ..diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError
from .models import DIGEST_RE, PackageRecord, RegistryState, VersionRecord

STATE_FILENAME = "state.json"
LOCK_FILENAME = ".registry.lock"
OBJECTS_DIRNAME = "objects"
BLOB_MAX_BYTES = 64 * 1024 * 1024
MAX_PACKAGES = 4096
MAX_VERSIONS_PER_PACKAGE = 256
LOCK_TIMEOUT_S = 10.0
LOCK_STALE_S = 30.0
GC_MIN_AGE_S = 7 * 24 * 3600.0


class RegistryStore:
    """单机 registry 存储（锁内写、锁外读；读路径零锁零 I/O 竞争）。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._state_path = self._root / STATE_FILENAME
        self._lock_path = self._root / LOCK_FILENAME
        self._objects = self._root / OBJECTS_DIRNAME

    # ── 读路径 ───────────────────────────────────────────────────────
    @property
    def root(self) -> Path:
        return self._root

    def load_state(self) -> RegistryState:
        if not self._state_path.is_file():
            return RegistryState(generation=0, packages={})
        try:
            raw = self._state_path.read_bytes()
        except OSError as exc:
            raise _store_error(f"registry index unreadable: {exc}")
        if len(raw) > 32 * 1024 * 1024:
            raise _store_error("registry index exceeds 32 MiB")
        try:
            data = json.loads(raw.decode("utf-8"))
            state = RegistryState.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - 索引损坏 = 服务不可用（fail closed）
            raise _store_error(f"registry index invalid: {type(exc).__name__}: {exc}")
        return state

    def blob_path(self, digest: str) -> Path:
        if not DIGEST_RE.match(digest or ""):
            raise _store_error(f"invalid blob digest {digest!r}")
        return self._objects / digest[:2] / digest

    def read_blob(self, digest: str) -> bytes:
        path = self.blob_path(digest)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise _store_error(f"blob {digest[:12]!r} unreadable: {exc}")
        if len(data) > BLOB_MAX_BYTES:
            raise _store_error(f"blob {digest[:12]!r} exceeds {BLOB_MAX_BYTES} bytes")
        return data

    def iter_blob_stream(self, digest: str, chunk_size: int = 256 * 1024) -> Iterator[bytes]:
        path = self.blob_path(digest)
        try:
            with path.open("rb") as fh:
                while True:
                    chunk = fh.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except OSError as exc:
            raise _store_error(f"blob {digest[:12]!r} unreadable: {exc}")

    # ── 写路径（锁内）─────────────────────────────────────────────────
    def publish_blob(self, data: bytes) -> str:
        """写 blob（content-addressed，幂等）；返回 digest。调用方持锁。"""
        import hashlib

        if len(data) > BLOB_MAX_BYTES:
            raise _store_error(f"blob exceeds {BLOB_MAX_BYTES} bytes")
        digest = hashlib.sha256(data).hexdigest()
        target = self.blob_path(digest)
        if target.is_file():
            return digest
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return digest

    def commit_package(self, package: PackageRecord, version: VersionRecord) -> RegistryState:
        """注册/更新一个版本（锁内调用；容量上界 + generation 递增）。

        丢失更新防线：整个「读索引 → 改 → 写」在文件锁内串行；本方法
        假定调用方已持锁（`RegistryService` 保证）。
        """
        state = self.load_state()
        existing = state.packages.get(package.id)
        if existing is not None and existing.publisher != package.publisher:
            raise _store_error(
                f"package {package.id!r} is owned by publisher {existing.publisher!r}; "
                f"refusing publish from {package.publisher!r} (dependency-confusion "
                "defense: package identity is claim-once)"
            )
        record = existing if existing is not None else package
        if version.version in record.versions:
            raise _store_error(
                f"version {version.version!r} of {package.id!r} already published "
                "(versions are immutable; yank + republish under a new version)"
            )
        if len(record.versions) + 1 > MAX_VERSIONS_PER_PACKAGE:
            raise _store_error(
                f"package {package.id!r} exceeds {MAX_VERSIONS_PER_PACKAGE} versions"
            )
        if package.id not in state.packages and len(state.packages) + 1 > MAX_PACKAGES:
            raise _store_error(f"registry exceeds {MAX_PACKAGES} packages")
        new_versions = dict(record.versions)
        new_versions[version.version] = version
        updated = record.model_copy(
            update={
                "title": package.title or record.title,
                "description": package.description or record.description,
                "tags": package.tags or record.tags,
                "versions": new_versions,
            }
        )
        new_packages = dict(state.packages)
        new_packages[package.id] = updated
        self._atomic_write_state(
            RegistryState(generation=state.generation + 1, packages=new_packages)
        )
        return self.load_state()

    def mutate_package(self, package_id: str, mutate) -> RegistryState:
        """锁内包级变更（deprecate/revoke/yank）；mutate: PackageRecord→PackageRecord。"""
        state = self.load_state()
        record = state.packages.get(package_id)
        if record is None:
            raise _store_error(f"unknown package {package_id!r}")
        updated = mutate(record)
        new_packages = dict(state.packages)
        new_packages[package_id] = updated
        self._atomic_write_state(
            RegistryState(generation=state.generation + 1, packages=new_packages)
        )
        return self.load_state()

    def _atomic_write_state(self, state: RegistryState) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state.model_dump(), ensure_ascii=False, sort_keys=True, indent=1)
        fd, tmp = tempfile.mkstemp(dir=str(self._root), prefix=".state", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._state_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ── 文件锁 ───────────────────────────────────────────────────────
    class _FileLock:
        """O_EXCL lockfile：超时 typed 失败；陈旧锁（mtime 超龄）接管。"""

        def __init__(self, path: Path, timeout_s: float = LOCK_TIMEOUT_S) -> None:
            self._path = path
            self._timeout_s = timeout_s
            self._fd: Optional[int] = None

        def __enter__(self):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            deadline = time.monotonic() + self._timeout_s
            while True:
                try:
                    self._fd = os.open(str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(self._fd, str(os.getpid()).encode())
                    return self
                except FileExistsError:
                    self._steal_if_stale()
                    if time.monotonic() >= deadline:
                        raise _store_error(
                            "registry lock held by another publisher "
                            f"(timeout {self._timeout_s}s)"
                        )
                    time.sleep(0.05)
                except OSError as exc:
                    raise _store_error(f"registry lock unavailable: {exc}")

        def _steal_if_stale(self) -> None:
            try:
                age = time.time() - self._path.stat().st_mtime
            except OSError:
                return
            if age > LOCK_STALE_S:
                try:
                    os.unlink(self._path)
                except OSError:
                    pass

        def __exit__(self, *exc) -> None:
            if self._fd is not None:
                try:
                    os.close(self._fd)
                finally:
                    try:
                        os.unlink(self._path)
                    except OSError:
                        pass
                self._fd = None

    def locked(self):
        return self._FileLock(self._lock_path)

    # ── GC（锁内；只删孤儿 + 陈旧 blob）───────────────────────────────
    def gc_orphan_blobs(self, now: Optional[float] = None) -> int:
        """删除「不在当前索引且 mtime > GC_MIN_AGE_S」的 blob；返回删除数。"""
        now = now if now is not None else time.time()
        state = self.load_state()
        referenced = {v.digest for p in state.packages.values() for v in p.versions.values()}
        removed = 0
        if not self._objects.is_dir():
            return 0
        for shard in self._objects.iterdir():
            if not shard.is_dir():
                continue
            for blob in shard.iterdir():
                if blob.name in referenced:
                    continue
                try:
                    if now - blob.stat().st_mtime <= GC_MIN_AGE_S:
                        continue
                    blob.unlink()
                    removed += 1
                except OSError:
                    continue
            try:
                next(shard.iterdir())
            except StopIteration:
                try:
                    shard.rmdir()
                except OSError:
                    pass
        return removed


def _store_error(message: str) -> ExtensionPlatformError:
    return ExtensionPlatformError(
        ExtensionDiagnostic.error(DiagnosticCode.REGISTRY_INVALID, message)
    )
