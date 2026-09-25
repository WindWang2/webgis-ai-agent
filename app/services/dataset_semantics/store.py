"""Dataset Semantics Store —— 语义 descriptor 的有界持久化（ADR-0215 D5）。

布局（session 内、内容寻址、与 MapSpec 同一 DATA_DIR 基座）：

```
<DATA_DIR>/.webgis-agent/<session_id>/dataset_semantics/
├── <key_hash>/head.json                      # 指针（原子替换）
└── <key_hash>/<descriptor_fingerprint>.json  # 载荷（内容寻址，幂等）
```

可靠性契约：

- **指纹即身份**：同指纹重复写幂等（no-op）；跨进程一致（内容寻址 +
  原子 head 替换，last-writer-wins 收敛到自洽状态）；
- **fail-closed 读**：head 缺席 → ``DESCRIPTOR_MISSING``；载荷损坏 →
  ``DESCRIPTOR_STORE_CORRUPT``；未知契约版本 → ``DESCRIPTOR_VERSION_
  UNSUPPORTED``；读回载荷指纹与文件名不符 → ``DESCRIPTOR_FINGERPRINT_
  MISMATCH`` —— 绝不把损坏/未知载荷猜成语义；
- **有界**：每 dataset_key 保留最近 ``MAX_VERSIONS_PER_DATASET`` 个版本，
  写入时 pruning；载荷字节超 ``MAX_DESCRIPTOR_BYTES`` 拒收
  （``DESCRIPTOR_TOO_LARGE``）；
- **事件循环纪律**：文件 IO 全部 ``asyncio.to_thread``（与 mapspec/store.py
  同规）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.lib.data.fingerprints import canonical_dumps
from app.lib.gis.dataset_descriptor import (
    CODE_FINGERPRINT_MISMATCH,
    CODE_STORE_CORRUPT,
    CODE_TOO_LARGE,
    CODE_VERSION_UNSUPPORTED,
    GISDatasetDescriptor,
)
from app.services.dataset_semantics.builder import (
    MAX_DESCRIPTOR_BYTES,
    descriptor_payload_bytes,
)

logger = logging.getLogger(__name__)

#: 每 dataset_key 保留的版本数（有界；旧版本仍可按指纹直读直至被 pruning）。
MAX_VERSIONS_PER_DATASET = 8

STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_CORRUPT = "corrupt"
STATUS_UNSUPPORTED = "unsupported"

_HEAD_NAME = "head.json"


def _storage_base() -> Path:
    """与 mapspec/store.py 同一 DATA_DIR 基座（单一 session 路径真相）。"""
    from app.services.mapspec.store import BASE_STORAGE_DIR

    return BASE_STORAGE_DIR.resolve()


def dataset_key_hash(dataset_key: str) -> str:
    """dataset_key → 文件系统安全目录名（sha256 前 16 位；映射确定性）。"""
    return hashlib.sha256(str(dataset_key or "").encode("utf-8")).hexdigest()[:16]


@dataclass
class DescriptorRecord:
    """store 读结果（status + reason_code 显式；不成功不返回 descriptor）。"""

    status: str
    reason_code: str = ""
    dataset_key: str = ""
    descriptor: Optional[GISDatasetDescriptor] = None
    fingerprint: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "dataset_key": self.dataset_key[:200],
            "descriptor_fingerprint": self.fingerprint[:96],
        }


@dataclass
class PutResult:
    ok: bool = False
    reason_code: str = ""
    fingerprint: str = ""
    created: bool = False        # False = 幂等 no-op（同指纹已存在）
    pruned: int = 0

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "reason_code": self.reason_code,
            "descriptor_fingerprint": self.fingerprint[:96],
            "created": self.created,
            "pruned": self.pruned,
        }


def _atomic_write_sync(path: Path, data: str) -> None:
    """原子写（同目录临时文件 + fsync + os.replace；同 mapspec 纪律）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_json_sync(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def migrate_payload(payload: Any) -> DescriptorRecord:
    """载荷 → descriptor（契约级迁移点；未知版本 fail-closed）。

    v1 → 直收并复核指纹；未来版本在此追加向后兼容读法（先 migrate 再验）。
    """
    try:
        descriptor = GISDatasetDescriptor.from_dict(payload)
    except ValueError as exc:
        msg = str(exc)
        if CODE_VERSION_UNSUPPORTED in msg:
            return DescriptorRecord(status=STATUS_UNSUPPORTED,
                                    reason_code=CODE_VERSION_UNSUPPORTED)
        return DescriptorRecord(status=STATUS_CORRUPT, reason_code=CODE_STORE_CORRUPT)
    except Exception:  # noqa: BLE001 — 任何形状异常都 fail-closed，不猜
        return DescriptorRecord(status=STATUS_CORRUPT, reason_code=CODE_STORE_CORRUPT)
    return DescriptorRecord(status=STATUS_OK, descriptor=descriptor,
                            dataset_key=descriptor.dataset_key,
                            fingerprint=descriptor.descriptor_fingerprint)


class DatasetSemanticStore:
    """GISDatasetDescriptor 的 session 内有界持久化。"""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._base = base_dir.resolve() if base_dir else None  # None = DATA_DIR 基座

    # ── 路径 ─────────────────────────────────────────────────────────
    def _root(self) -> Path:
        base = self._base if self._base is not None else _storage_base()
        return base

    def _dataset_dir(self, session_id: str, dataset_key: str) -> Path:
        root = self._root()
        session_dir = (root / str(session_id)).resolve()
        if session_dir.parent != root:
            raise ValueError("invalid session id for dataset semantics storage")
        return session_dir / "dataset_semantics" / dataset_key_hash(dataset_key)

    # ── 写 ───────────────────────────────────────────────────────────
    async def put(
        self,
        session_id: str,
        dataset_key: str,
        descriptor: GISDatasetDescriptor,
    ) -> PutResult:
        """descriptor 落库（内容寻址幂等；head 指针原子替换；版本 pruning）。"""
        if not session_id or not dataset_key:
            return PutResult(ok=False, reason_code="DATASET_KEY_REQUIRED")
        if descriptor.dataset_key and descriptor.dataset_key != dataset_key:
            # key 是身份的一部分：不一致说明调用方拿错了 descriptor —— 拒收。
            return PutResult(ok=False, reason_code="DATASET_KEY_MISMATCH")
        if descriptor_payload_bytes(descriptor) > MAX_DESCRIPTOR_BYTES:
            return PutResult(ok=False, reason_code=CODE_TOO_LARGE)
        fingerprint = descriptor.descriptor_fingerprint
        if not fingerprint:
            return PutResult(ok=False, reason_code="DESCRIPTOR_FINGERPRINT_REQUIRED")
        return await asyncio.to_thread(
            self._put_sync, session_id, dataset_key, descriptor, fingerprint,
        )

    def _put_sync(
        self,
        session_id: str,
        dataset_key: str,
        descriptor: GISDatasetDescriptor,
        fingerprint: str,
    ) -> PutResult:
        ddir = self._dataset_dir(session_id, dataset_key)
        head_path = ddir / _HEAD_NAME
        history: List[str] = []
        if head_path.exists():
            try:
                head = _read_json_sync(head_path)
                if isinstance(head, dict):
                    history = [
                        str(h) for h in (head.get("history") or [])
                        if isinstance(h, str)
                    ]
            except Exception:  # noqa: BLE001 — head 损坏按空历史重建（载荷仍内容寻址）
                history = []
        if history and history[-1] == fingerprint:
            return PutResult(ok=True, fingerprint=fingerprint, created=False)
        payload_path = ddir / f"{fingerprint}.json"
        payload_text = json.dumps(
            descriptor.to_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        try:
            _atomic_write_sync(payload_path, payload_text)
        except OSError as exc:
            logger.warning("[dataset-semantics] payload write failed: %s", exc)
            return PutResult(ok=False, reason_code="DESCRIPTOR_STORE_WRITE_FAILED")
        new_history = ([h for h in history if h != fingerprint] + [fingerprint])[-MAX_VERSIONS_PER_DATASET:]
        head_payload = {
            "dataset_key": str(dataset_key)[:200],
            "descriptor_version": descriptor.descriptor_version,
            "descriptor_fingerprint": fingerprint,
            "schema_fingerprint": descriptor.schema_fingerprint,
            "history": new_history,
        }
        try:
            _atomic_write_sync(
                head_path,
                json.dumps(head_payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")),
            )
        except OSError as exc:
            logger.warning("[dataset-semantics] head write failed: %s", exc)
            return PutResult(ok=False, reason_code="DESCRIPTOR_STORE_WRITE_FAILED")
        pruned = self._prune_sync(ddir, new_history)
        return PutResult(ok=True, fingerprint=fingerprint, created=True, pruned=pruned)

    def _prune_sync(self, ddir: Path, history: List[str]) -> int:
        """保留 head 历史内的版本，其余载荷删除（有界磁盘）。"""
        keep = {f"{h}.json" for h in history}
        pruned = 0
        try:
            entries = list(ddir.glob("*.json"))
        except OSError:
            return 0
        for p in entries:
            if p.name == _HEAD_NAME or p.name in keep:
                continue
            try:
                p.unlink()
                pruned += 1
            except OSError:
                continue
        return pruned

    # ── 读 ───────────────────────────────────────────────────────────
    async def get(self, session_id: str, dataset_key: str) -> DescriptorRecord:
        """head 指针 → 载荷 → migrate（全链 fail-closed）。"""
        if not session_id or not dataset_key:
            return DescriptorRecord(status=STATUS_MISSING,
                                    reason_code="DATASET_KEY_REQUIRED")
        return await asyncio.to_thread(self._get_sync, session_id, dataset_key, None)

    async def get_by_fingerprint(
        self, session_id: str, dataset_key: str, fingerprint: str,
    ) -> DescriptorRecord:
        """按版本指纹直读（旧版本可读直到被 pruning）。"""
        if not session_id or not dataset_key or not fingerprint:
            return DescriptorRecord(status=STATUS_MISSING,
                                    reason_code="DATASET_KEY_REQUIRED")
        return await asyncio.to_thread(
            self._get_sync, session_id, dataset_key, str(fingerprint)[:96],
        )

    def _get_sync(
        self, session_id: str, dataset_key: str, fingerprint: Optional[str],
    ) -> DescriptorRecord:
        ddir = self._dataset_dir(session_id, dataset_key)
        fp = fingerprint
        if fp is None:
            head_path = ddir / _HEAD_NAME
            if not head_path.exists():
                return DescriptorRecord(status=STATUS_MISSING,
                                        reason_code="DESCRIPTOR_MISSING",
                                        dataset_key=dataset_key)
            try:
                head = _read_json_sync(head_path)
            except Exception:  # noqa: BLE001 — head 损坏：fail-closed
                return DescriptorRecord(status=STATUS_CORRUPT,
                                        reason_code=CODE_STORE_CORRUPT,
                                        dataset_key=dataset_key)
            if not isinstance(head, dict) or not head.get("descriptor_fingerprint"):
                return DescriptorRecord(status=STATUS_CORRUPT,
                                        reason_code=CODE_STORE_CORRUPT,
                                        dataset_key=dataset_key)
            fp = str(head["descriptor_fingerprint"])
        payload_path = ddir / f"{fp}.json"
        if not payload_path.exists():
            return DescriptorRecord(status=STATUS_MISSING,
                                    reason_code="DESCRIPTOR_MISSING",
                                    dataset_key=dataset_key, fingerprint=fp)
        try:
            payload = _read_json_sync(payload_path)
        except Exception:  # noqa: BLE001 — 载荷损坏：fail-closed，不猜
            return DescriptorRecord(status=STATUS_CORRUPT,
                                    reason_code=CODE_STORE_CORRUPT,
                                    dataset_key=dataset_key, fingerprint=fp)
        record = migrate_payload(payload)
        record.dataset_key = dataset_key
        record.fingerprint = fp
        if record.status != STATUS_OK:
            return record
        assert record.descriptor is not None
        # 完整性复核（tamper-evident）：语义载荷重算指纹必须与身份一致 ——
        # 载荷被篡改/写坏 → 拒收（fail-closed），绝不返回被改过的语义。
        recomputed = record.descriptor.with_fingerprints().descriptor_fingerprint
        if record.descriptor.descriptor_fingerprint != fp or recomputed != fp:
            return DescriptorRecord(status=STATUS_CORRUPT,
                                    reason_code=CODE_FINGERPRINT_MISMATCH,
                                    dataset_key=dataset_key, fingerprint=fp)
        return record

    # ── 版本清单 ─────────────────────────────────────────────────────
    async def list_versions(self, session_id: str, dataset_key: str) -> List[str]:
        """head 历史指纹（旧 → 新；有界 ≤MAX_VERSIONS_PER_DATASET）。"""
        return await asyncio.to_thread(self._list_sync, session_id, dataset_key)

    def _list_sync(self, session_id: str, dataset_key: str) -> List[str]:
        ddir = self._dataset_dir(session_id, dataset_key)
        head_path = ddir / _HEAD_NAME
        if not head_path.exists():
            return []
        try:
            head = _read_json_sync(head_path)
            history = head.get("history") if isinstance(head, dict) else None
            if isinstance(history, list):
                return [str(h) for h in history][-MAX_VERSIONS_PER_DATASET:]
        except Exception:  # noqa: BLE001 — 损坏 head 返回空清单（fail-closed 读面）
            return []
        return []


_store: Optional[DatasetSemanticStore] = None


def get_dataset_semantic_store() -> DatasetSemanticStore:
    global _store
    if _store is None:
        _store = DatasetSemanticStore()
    return _store


def reset_dataset_semantic_store() -> None:
    global _store
    _store = None


__all__ = [
    "MAX_VERSIONS_PER_DATASET",
    "DatasetSemanticStore",
    "DescriptorRecord",
    "PutResult",
    "migrate_payload",
    "dataset_key_hash",
    "get_dataset_semantic_store",
    "reset_dataset_semantic_store",
    "STATUS_OK", "STATUS_MISSING", "STATUS_CORRUPT", "STATUS_UNSUPPORTED",
]
