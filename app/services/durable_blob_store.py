"""Durable Blob Store — the single durable content backend (Wave 1, Goal D).

单一事实源边界（single-truth boundary）：**BlobStore 是唯一的内容持久后端；
本模块不是第二个 registry，也不新增第三份真相** —— 它把既有晋升内容库
（``project_artifacts/<shard4>/<key>.json``，audit §7.1）的提升式存储机制
提炼成最小接口，供 promotion / GC / pin / clone 复用。会话内账本仍归
session store（alias ``artifacts``）；内容寻址真相归这里。

Layout compatibility contract（与 project_artifact_promotion.py:84-90 同一
布局，既有 JSON blob 原样可读）:

    <root>/<key[:4]>/<key>.json        JSON blobs（与既有晋升文件同路径）
    <root>/<key[:4]>/<key>.bin         binary blobs
    <root>/<key[:4]>/<key>.bin.meta    binary sidecar {content_type, byte_size}

Keys: 新写入一律 ``<64hex sha256>``（内容寻址，天然全局去重）；历史晋升
内容以 descriptor 指纹为键（非 hex）仍可读写 —— 键校验只要求文件名安全
（拒绝 ``/`` ``\\`` ``..``），不做 hex 强制，读路径向后兼容。

Write discipline（与晋升/磁盘缓存同款）: idempotent put-if-absent（文件已
在场即跳过，同 promotion :168-169）+ 原子发布（tmp + ``os.replace``，失败
清理 tmp，无半截文件）+ digest 校验读（expected_sha256 不匹配返回 None，
绝不静默顶替内容，同 read_content :189-223）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Callable, NamedTuple, Optional, Union

logger = logging.getLogger(__name__)

#: JSON blob 后缀（既有晋升内容库布局的一部分，不可更改）
_JSON_SUFFIX = ".json"
#: binary blob 后缀 + sidecar
_BIN_SUFFIX = ".bin"
_META_SUFFIX = ".meta"


class BlobKeyError(ValueError):
    """键不安全（空 / 含路径分隔符 / 含 ``..``）—— 派生路径越界即拒绝。"""


class PutResult(NamedTuple):
    """put_blob 结果：put_new=False 表示内容已在场（去重命中，未重写）。"""

    put_new: bool
    location: str  # 相对 root 的位置（存入 Artifact.metadata_json.content_location）


def safe_blob_key(key: str) -> str:
    """键守卫：非空 + 无 ``/`` ``\\`` ``..`` + 无控制字符（与晋升同规则）。"""
    key = str(key or "").strip()
    if not key or len(key) > 200:
        raise BlobKeyError(f"invalid blob key: {key[:32]!r}")
    if any(c in key for c in ("/", "\\", "\x00")) or ".." in key:
        raise BlobKeyError(f"unsafe blob key: {key[:32]!r}")
    if any(ord(c) < 0x20 for c in key):
        raise BlobKeyError(f"unsafe blob key: {key[:32]!r}")
    return key


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BlobStore:
    """最小内容寻址后端接口（put/get/exists/size/delete + put_json 便捷层）。

    S3 等其它后端日后挂同一接口（audit §7.1）；调用方永远经由此抽象，
    不直接触碰文件路径。
    """

    def put_blob(self, key: str, data: bytes, content_type: str = "json") -> PutResult:
        raise NotImplementedError

    def get_blob(self, key: str, expected_sha256: Optional[str] = None) -> Optional[bytes]:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def size(self, key: str) -> Optional[int]:
        raise NotImplementedError

    def delete_blob(self, key: str) -> bool:
        raise NotImplementedError

    def location(self, key: str) -> str:
        raise NotImplementedError

    def put_json(self, payload) -> "tuple[str, str]":
        """canonical 序列化 → sha256 → put。返回 (key, sha256)（CAS 下相等）。

        使用既有 canonical 序列化口径（app/lib/data/fingerprints.canonical_dumps，
        与 provenance/fingerprint 同 sort_keys+紧凑分隔符），保证同输入必同键。
        不可序列化 / 含 NaN·Inf → BlobKeyError 之外的 ValueError 透传（调用方
        决定诚实降级），绝不写半截内容。
        """
        from app.lib.data.fingerprints import canonical_dumps, sha256_hex

        blob = canonical_dumps(payload)
        digest = sha256_hex(blob)
        self.put_blob(digest, blob.encode("utf-8"), "json")
        return digest, digest


class FilesystemBlobStore(BlobStore):
    """晋升内容库同布局的文件系统实现（sharded `<key[:4]>/<key>` + 后缀）。

    ``root`` 可以是路径或 **root provider 可调用**（单例用后者绑定
    promotion 的 ``content_store_root()``，保证 settings 覆写/测试
    monkeypatch 时根地址实时解析，不固化陈旧路径）。
    """

    def __init__(self, root: Union[str, Path, Callable[[], Path]]):
        self._root_provider: Callable[[], Path] = (
            root if callable(root) else (lambda: Path(root))  # type: ignore[arg-type,return-value]
        )

    @property
    def root(self) -> Path:
        return self._root_provider()

    # ── 路径派生（唯一的路径真相；读向后兼容既有布局）─────────────────

    def _candidate_paths(self, key: str):
        """读序：JSON（既有晋升布局）→ binary → 无后缀（防御性兼容）。"""
        shard = key[:4]
        base = self.root / shard
        return (
            base / f"{key}{_JSON_SUFFIX}",
            base / f"{key}{_BIN_SUFFIX}",
            base / key,
        )

    def primary_path(self, key: str, content_type: str = "json") -> Path:
        """写入路径：JSON 走既有 ``<key>.json`` 布局（layout-compatible）。

        键守卫在唯一路径派生点强制（越界键在成为路径前即拒绝）。
        """
        key = safe_blob_key(key)
        shard = key[:4]
        if content_type == "binary":
            return self.root / shard / f"{key}{_BIN_SUFFIX}"
        return self.root / shard / f"{key}{_JSON_SUFFIX}"

    def sidecar_path(self, key: str, content_type: str = "binary") -> Path:
        return self.primary_path(key, content_type).with_name(
            self.primary_path(key, content_type).name + _META_SUFFIX
        )

    def location(self, key: str, content_type: str = "json") -> str:
        return str(self.primary_path(key, content_type).relative_to(self.root))

    # ── 核心操作 ─────────────────────────────────────────────────────────

    def put_blob(self, key: str, data: bytes, content_type: str = "json") -> PutResult:
        key = safe_blob_key(key)
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("put_blob expects bytes")
        data = bytes(data)
        path = self.primary_path(key, content_type)
        location = str(path.relative_to(self.root))
        if path.exists():
            # put-if-absent：同键 = 同内容（CAS），跳过重写（同 promotion :168-169）
            return PutResult(put_new=False, location=location)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex[:8]}")
            try:
                tmp.write_bytes(data)
                os.replace(tmp, path)  # 原子发布：读者只见完整文件或无文件
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            if content_type == "binary":
                self._write_sidecar(key, len(data), content_type)
            return PutResult(put_new=True, location=location)
        except BlobKeyError:
            raise
        except Exception as e:  # noqa: BLE001 — 持久化失败由调用方诚实披露
            logger.warning("[blob_store] put failed for %s: %s", key, e)
            raise

    def _write_sidecar(self, key: str, byte_size: int, content_type: str) -> None:
        """binary sidecar（同样原子写纪律；失败不回滚主件 —— 主件自校验）。"""
        meta_path = self.sidecar_path(key, content_type)
        try:
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = meta_path.with_name(f".{meta_path.name}.tmp-{uuid.uuid4().hex[:8]}")
            try:
                tmp.write_text(
                    json.dumps({"content_type": content_type, "byte_size": byte_size}),
                    encoding="utf-8",
                )
                os.replace(tmp, meta_path)
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        except Exception as e:  # noqa: BLE001 — sidecar 缺失只影响展示元数据
            logger.warning("[blob_store] sidecar write failed for %s: %s", key, e)

    def read_meta(self, key: str) -> Optional[dict]:
        """读 binary sidecar（JSON blob / 缺 sidecar → None）。"""
        for candidate in self._candidate_paths(key):
            meta_path = candidate.with_name(candidate.name + _META_SUFFIX)
            if meta_path.is_file():
                try:
                    return json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001 — 坏 sidecar 按缺失处理
                    return None
        return None

    def get_blob(self, key: str, expected_sha256: Optional[str] = None) -> Optional[bytes]:
        key = safe_blob_key(key)
        path = self._first_existing(key)
        if path is None:
            return None
        try:
            raw = path.read_bytes()
        except OSError as e:  # noqa: BLE001 — 读失败按缺内容（诚实）
            logger.warning("[blob_store] read failed for %s: %s", key, e)
            return None
        if expected_sha256:
            actual = sha256_of_bytes(raw)
            if actual != expected_sha256:
                logger.warning(
                    "[blob_store] digest mismatch for %s (expected %s, got %s)",
                    key, expected_sha256, actual,
                )
                return None  # 内容被篡改/损坏 → 绝不静默顶替（同 read_content）
        return raw

    def get_text(self, key: str, expected_sha256: Optional[str] = None) -> Optional[str]:
        """JSON blob 的文本读（调用方自行 json.loads；digest 校验语义同上）。"""
        raw = self.get_blob(key, expected_sha256)
        if raw is None:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def exists(self, key: str) -> bool:
        try:
            key = safe_blob_key(key)
        except BlobKeyError:
            return False
        return self._first_existing(key) is not None

    def size(self, key: str) -> Optional[int]:
        try:
            key = safe_blob_key(key)
        except BlobKeyError:
            return None
        path = self._first_existing(key)
        if path is None:
            return None
        try:
            return path.stat().st_size
        except OSError:
            return None

    def delete_blob(self, key: str) -> bool:
        """删除键的全部物理形态（含 sidecar 与崩溃遗留 tmp）。返回主件是否删除。"""
        try:
            key = safe_blob_key(key)
        except BlobKeyError:
            return False
        deleted_primary = False
        for candidate in self._candidate_paths(key):
            try:
                if candidate.is_file():
                    candidate.unlink()
                    if not candidate.name.endswith(_META_SUFFIX):
                        deleted_primary = deleted_primary or True
            except OSError as e:  # noqa: BLE001 — 单文件失败不阻断清扫
                logger.warning("[blob_store] delete failed for %s: %s", candidate, e)
            meta = candidate.with_name(candidate.name + _META_SUFFIX)
            try:
                if meta.is_file():
                    meta.unlink()
            except OSError:
                pass
        return deleted_primary

    def iter_blob_files(self):
        """全量枚举（GC 清扫器用）：产出 (key, path)。非 blob 后缀跳过。"""
        root = self.root
        if not root.is_dir():
            return
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            name = path.name
            if name.endswith(_JSON_SUFFIX):
                key = name[: -len(_JSON_SUFFIX)]
            elif name.endswith(_BIN_SUFFIX):
                key = name[: -len(_BIN_SUFFIX)]
            elif name.endswith(_META_SUFFIX) or ".tmp-" in name:
                continue  # sidecar / 临时件不是独立 blob
            else:
                key = name  # 无后缀（防御性兼容形态）
            yield key, path

    def blob_mtime(self, path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    # ── 内部 ─────────────────────────────────────────────────────────────

    def _first_existing(self, key: str) -> Optional[Path]:
        for candidate in self._candidate_paths(key):
            try:
                if candidate.is_file():
                    return candidate
            except OSError:  # noqa: BLE001 — stat 失败按缺席（诚实保守）
                continue
        return None


#: 进程级单例（root 经 provider 实时绑定 promotion 的 content_store_root ——
#: 同一 DATA_DIR/project_artifacts 根，绝不另起目录）。
_FS_STORE: Optional[FilesystemBlobStore] = None


def get_filesystem_blob_store() -> FilesystemBlobStore:
    """Singleton BlobStore rooted at the promotion store's own root.

    root 用 provider 延迟解析（而非构建期取值）：settings 的
    PROJECT_ARTIFACT_CONTENT_DIR/DATA_DIR 覆写与测试 monkeypatch
    （``pap.content_store_root``）即刻生效。
    """
    global _FS_STORE
    if _FS_STORE is None:
        from app.services import project_artifact_promotion as _pap

        def _root() -> Path:
            return _pap.content_store_root()

        _FS_STORE = FilesystemBlobStore(root=_root)
    return _FS_STORE


def reset_filesystem_blob_store() -> None:
    """Test hook: singleton 重置（root provider 重新绑定）。"""
    global _FS_STORE
    _FS_STORE = None
