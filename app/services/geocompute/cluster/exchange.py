"""GeoCompute V8 Artifact Exchange（Phase E，ADR-0133 §5）。

执行面与**内容寻址 BlobStore**（durable_blob_store）的接线层 —— 之前
BlobStore 的 content-hash/流式原语已存在但 geocompute 从未使用，执行面
大载荷只能进内存 LRU。本模块补上缺的那一段：

- **spill-to-disk**：executor checkpoint 超过内存预算的载荷落 BlobStore
  （zlib 压缩，钳 64MiB），LRU 里只留 stub —— 大 run 不再挤爆 checkpoint；
- **content hash**：键 = sha256(logical bytes)（天然全局去重）；落盘字节
  经 digest 校验读回（BlobDigestMismatch → 类型化失败）；
- **retry**：读回有界重试 + 退避（瞬态 IO；digest 不符不重试 —— 内容
  寻址下重试同一份坏字节没有意义，诚实失败）；
- **cleanup**：``geocompute_artifacts`` 元数据表 + TTL 清扫（coordinator
  tick 有界批）；行丢失 = 孤儿字节（TTL 兜底），绝不是数据丢失。

诚实边界：root 未配置（``WEBGIS_EXCHANGE_ROOT`` 缺席）→ exchange 整体
**停用**（enabled=False，spill 退回纯内存 LRU —— 与 V7 行为逐字节一致）。
单机 FS 后端；跨机共享根由部署保证（与 raster_path 载荷同一主机假设）。
"""
from __future__ import annotations

import logging
import os
import time
import zlib
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import delete, select, update

logger = logging.getLogger(__name__)

#: spill 载荷的压缩上界（超过则原样存 —— zlib 全量驻留内存有界）。
MAX_COMPRESS_BYTES = 64 * 1024 * 1024

#: 读回重试（瞬态 IO）与退避。
GET_RETRIES = 2
GET_RETRY_BACKOFF_S = 0.2

#: 单次清扫的有界批。
CLEANUP_BATCH = 128


@dataclass(frozen=True)
class SpillHandle:
    """spill 条目的最小凭证（进 LRU stub；get 的全部输入）。"""

    key: str          # sha256(logical bytes) hex 64
    size_bytes: int   # 逻辑字节（解压后）
    codec: str        # raw | zlib


def spill_threshold_bytes() -> int:
    """checkpoint 条目 spill 阈值（env；缺省 8MiB）。0 = 禁用 spill。"""
    try:
        return max(0, int(os.environ.get("WEBGIS_EXCHANGE_SPILL_BYTES", "") or 8 * 1024 * 1024))
    except ValueError:
        return 8 * 1024 * 1024


def exchange_root() -> Optional[str]:
    """exchange 根目录（env；缺席 = 停用）。"""
    raw = (os.environ.get("WEBGIS_EXCHANGE_ROOT", "") or "").strip()
    return raw or None


class ArtifactExchange:
    """执行面 artifact 交换门面（BlobStore 适配 + 元数据 + TTL 清扫）。"""

    def __init__(self, *, root: Optional[str] = None, factory: Optional[Any] = None,
                 ttl_s: float = 24 * 3600.0):
        self._root = root if root is not None else exchange_root()
        self._ttl_s = float(ttl_s)
        self._factory = factory
        self._store: Any = None
        if self._root:
            from app.services.durable_blob_store import FilesystemBlobStore

            self._store = FilesystemBlobStore(self._root)

    @property
    def enabled(self) -> bool:
        return self._store is not None

    # ── 元数据表（geocompute_artifacts；缺席/失败一律 fail-open）───────

    def _meta_session(self):
        if self._factory is None:
            from app.services.geocompute.cluster.store import session_factory as _sf

            return _sf()
        return self._factory()

    def _register(self, handle: SpillHandle, *, run_id: Optional[str],
                  owner_scope: Optional[str], kind: str, codec: str) -> None:
        try:
            from app.models.db_model import GeoComputeArtifact
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            expires = now + timedelta(seconds=self._ttl_s)
            with self._meta_session() as db:
                existing = db.execute(
                    select(GeoComputeArtifact.id).where(
                        GeoComputeArtifact.artifact_key == handle.key)
                ).scalar_one_or_none()
                if existing is None:
                    db.add(GeoComputeArtifact(
                        artifact_key=handle.key, run_id=run_id,
                        owner_scope=owner_scope, kind=kind, codec=codec,
                        size_bytes=handle.size_bytes, stored_at=now,
                        expires_at=expires,
                    ))
                else:
                    db.execute(
                        update(GeoComputeArtifact)
                        .where(GeoComputeArtifact.artifact_key == handle.key)
                        .values(last_hit_at=now, expires_at=expires)
                    )
                db.commit()
        except Exception:  # noqa: BLE001 - 元数据失败不影响载荷路径
            logger.debug("[geocompute-v8] artifact metadata register failed",
                         exc_info=True)

    def _touch(self, key: str) -> None:
        try:
            from app.models.db_model import GeoComputeArtifact
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            with self._meta_session() as db:
                db.execute(
                    update(GeoComputeArtifact)
                    .where(GeoComputeArtifact.artifact_key == key)
                    .values(last_hit_at=now)
                )
                db.commit()
        except Exception:  # noqa: BLE001
            pass

    # ── 载荷路径（put 幂等；get 校验 + 有界重试）────────────────────

    def put_bytes(
        self, data: bytes, *, run_id: Optional[str] = None,
        owner_scope: Optional[str] = None, kind: str = "spill",
        compress: bool = True,
    ) -> SpillHandle:
        """字节 → BlobStore（内容寻址 put-if-absent；返回 spill 凭证）。

        compress=True 且大小 ≤ MAX_COMPRESS_BYTES → zlib 落盘（键仍是
        **逻辑字节**的 sha256 —— 完整性与去重都以逻辑内容为准）。
        """
        if not self.enabled:
            raise RuntimeError("artifact exchange is not enabled")
        import hashlib

        key = hashlib.sha256(data).hexdigest()
        raw_size = len(data)
        codec = "raw"
        payload = data
        if compress and 0 < len(data) <= MAX_COMPRESS_BYTES:
            payload = zlib.compress(data, level=1)
            codec = "zlib"
        self._store.put_blob(key, payload, content_type="binary")
        handle = SpillHandle(key=key, size_bytes=raw_size, codec=codec)
        try:
            self._register(handle, run_id=run_id, owner_scope=owner_scope,
                           kind=kind, codec=codec)
        except Exception:
            raise
        # 补偿（评审建议：register fail-open 的 blob 无元数据行 = TTL
        # 永远扫不到的永久孤儿）。register 内部已吞异常，这里无法区分
        # 成败 —— 改为可判定版本：直接检查行是否存在，缺席即删字节。
        if not self._has_row(key):
            try:
                self._store.delete_blob(key)
            except Exception:  # noqa: BLE001 - 补偿失败 = 有界孤儿
                pass
        return handle

    def _has_row(self, key: str) -> bool:
        try:
            from app.models.db_model import GeoComputeArtifact

            with self._meta_session() as db:
                return db.execute(
                    select(GeoComputeArtifact.id).where(
                        GeoComputeArtifact.artifact_key == key)
                ).scalar_one_or_none() is not None
        except Exception:  # noqa: BLE001 - 判定失败 = 保留字节（有界孤儿）
            return True

    def get_bytes(self, handle: SpillHandle) -> bytes:
        """凭证 → 字节（digest 校验 + 有界瞬态重试；失败类型化上抛）。"""
        if not self.enabled:
            raise RuntimeError("artifact exchange is not enabled")
        from app.services.durable_blob_store import BlobDigestMismatch

        last_exc: Optional[Exception] = None
        for attempt in range(GET_RETRIES + 1):
            try:
                blob = self._store.get_blob(handle.key)
                if blob is None:
                    raise KeyError(
                        f"artifact {handle.key[:16]} missing from blob store")
                if handle.codec == "zlib":
                    blob = zlib.decompress(blob)
                if len(blob) != handle.size_bytes:
                    raise ValueError(
                        f"artifact {handle.key[:16]} size mismatch: "
                        f"{len(blob)} != {handle.size_bytes}")
                self._touch(handle.key)
                return blob
            except zlib.error as exc:
                # 压缩流损坏 = 确定性腐坏（重试无意义）
                raise RuntimeError(
                    f"artifact {handle.key[:16]} corrupt: {exc}") from exc
            except BlobDigestMismatch as exc:
                # 内容寻址下重试同一份坏字节无意义 —— 诚实失败
                raise ValueError(f"artifact digest mismatch: {exc}") from exc
            except ValueError as exc:
                # 尺寸/内容损坏 = 确定性腐坏（内容寻址下重试无意义）
                raise RuntimeError(
                    f"artifact {handle.key[:16]} corrupt: {exc}") from exc
            except (OSError, KeyError) as exc:  # 瞬态 IO / 未落盘
                last_exc = exc
                if attempt < GET_RETRIES:
                    time.sleep(GET_RETRY_BACKOFF_S * (attempt + 1))
        raise RuntimeError(
            f"artifact {handle.key[:16]} unreadable after retries: {last_exc}"
        ) from last_exc

    # ── cleanup（TTL 清扫；coordinator tick 有界批调用）──────────────

    def cleanup_expired(self, *, limit: int = CLEANUP_BATCH,
                        now: Optional[Any] = None) -> int:
        """过期 artifact 清扫（元数据行 + 对应 blob 字节）。返回清理数。"""
        if not self.enabled:
            return 0
        try:
            from app.models.db_model import GeoComputeArtifact
            from app.services.geocompute.cluster.store import _utcnow

            cutoff = now or _utcnow()
            removed = 0
            with self._meta_session() as db:
                rows = db.execute(
                    select(GeoComputeArtifact.artifact_key)
                    .where(GeoComputeArtifact.expires_at.is_not(None))
                    .where(GeoComputeArtifact.expires_at < cutoff)
                    .limit(max(1, int(limit)))
                ).scalars().all()
                for key in rows:
                    self._store.delete_blob(key)
                if rows:
                    db.execute(
                        delete(GeoComputeArtifact)
                        .where(GeoComputeArtifact.artifact_key.in_(list(rows)))
                    )
                    db.commit()
                removed = len(rows)
            return removed
        except Exception:  # noqa: BLE001 - 清扫失败有界计数可见即可
            logger.debug("[geocompute-v8] artifact cleanup failed",
                         exc_info=True)
            return 0


#: 进程内单例（懒构建；root 缺席 = 停用实例）。
_default_exchange: Optional[ArtifactExchange] = None
_exchange_lock = None


def get_exchange() -> ArtifactExchange:
    global _default_exchange, _exchange_lock
    import threading

    if _exchange_lock is None:
        _exchange_lock = threading.Lock()
    if _default_exchange is None:
        with _exchange_lock:
            if _default_exchange is None:
                try:
                    _default_exchange = ArtifactExchange()
                except Exception:  # noqa: BLE001 - 构建失败 = 停用
                    _default_exchange = ArtifactExchange(root=None)
    return _default_exchange


def reset_exchange_for_tests() -> None:
    global _default_exchange
    _default_exchange = None
