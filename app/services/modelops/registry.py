"""ModelRegistryStore —— learned-model 注册表（ADR-0119 §3.6）。

与既有注册表的边界（架构挑战 M1/基线 Q3/Q7）：

- **不是** chat LLM 域（``app/services/chat/model_runtime/descriptors.py``，
  ADR-0102）；
- **不是** AlgorithmRegistry（ADR-0099 方法语义域）；
- GeoAI 推理模型的唯一身份/版本/完整性真相源。

契约：

- identity = (model_id, model_version)；同 identity 不同 checksum =
  :class:`ModelVersionCollision`（typed 拒绝，绝不静默覆盖）；
- 条目不可变：revision 单调递增，"更新" = 新 revision（审计可回放）；
- owner scope 恰好一维（session/project），global 种子单独一层；
- ``provider_ref`` 只接受 ProviderRegistry 已注册实例 id（挑战 C1）；
- 存储：scope 目录 + 单条 JSON 文档 + index 文件，原子写（tmp+replace），
  parity 校验（index vs docs，挑战/审计 Q12）；
- checksum 注册时即验（load 时 provider 层双验）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import (
    DescriptorError,
    ModelNotFoundError,
    ModelVersionCollision,
    RegistryParityError,
)
from app.services.modelops.config import ModelOpsSettings

logger = logging.getLogger(__name__)

REGISTRY_INDEX_FILENAME = "index.json"
REGISTRY_SCHEMA_VERSION = "modelops.registry/v1"


@dataclass(frozen=True)
class ModelRecord:
    """registry 一条不可变记录（descriptor + 治理元数据）。"""

    descriptor: GeoModelDescriptor
    owner_scope: Dict[str, str]        # {"global"} 或 {"session_id": ..}/{"project_id": ..}
    revision: int
    registered_at: float
    registered_by: str
    #: 注册时已验过的包校验摘要（PackageReport.as_dict() 或 synthetic 等价物）。
    package_report: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "descriptor": self.descriptor.as_dict(),
            "owner_scope": dict(self.owner_scope),
            "revision": self.revision,
            "registered_at": self.registered_at,
            "registered_by": self.registered_by,
            "package_report": self.package_report,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelRecord":
        if data.get("schema_version") != REGISTRY_SCHEMA_VERSION:
            raise RegistryParityError(
                f"registry record schema mismatch: {data.get('schema_version')!r}"
            )
        return cls(
            descriptor=GeoModelDescriptor.model_validate(data["descriptor"]),
            owner_scope=dict(data["owner_scope"]),
            revision=int(data["revision"]),
            registered_at=float(data["registered_at"]),
            registered_by=str(data.get("registered_by", "")),
            package_report=dict(data.get("package_report") or {}),
        )


class ModelRegistryStore:
    """持久化模型注册表（进程内单例语义；文件原子写 + 进程锁）。"""

    def __init__(self, settings: Optional[ModelOpsSettings] = None) -> None:
        self._settings = settings or ModelOpsSettings.load()
        self._lock = threading.RLock()
        self._records: Dict[Tuple[str, str], ModelRecord] = {}
        self._loaded = False

    # ── 持久化 ──────────────────────────────────────────────────────
    def _scope_dir(self, owner_scope: Dict[str, str]) -> Path:
        scope_key = "_".join(f"{k}-{v}" for k, v in sorted(owner_scope.items()))
        return self._settings.registry_dir / "registry" / scope_key

    def _record_path(self, identity: Tuple[str, str], owner_scope: Dict[str, str]) -> Path:
        model_id, model_version = identity
        safe_id = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in model_id)
        safe_ver = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in model_version)
        return self._scope_dir(owner_scope) / f"{safe_id}__{safe_ver}.json"

    def _index_path(self) -> Path:
        return self._settings.registry_dir / "registry" / REGISTRY_INDEX_FILENAME

    def _persist(self, record: ModelRecord) -> None:
        path = self._record_path(record.descriptor.identity, record.owner_scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        self._write_index()

    def _write_index(self) -> None:
        """index = 身份→(scope, revision, checksum) 投影（parity 真相的快表）。"""
        entries: Dict[str, Any] = {}
        for (model_id, model_version), rec in sorted(self._records.items()):
            entries[f"{model_id}@{model_version}"] = {
                "scope": rec.owner_scope,
                "revision": rec.revision,
                "checksum": rec.descriptor.checksum,
            }
        payload = {"schema_version": REGISTRY_SCHEMA_VERSION, "entries": entries}
        path = self._index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def load(self) -> None:
        """从磁盘加载全部记录（幂等；损坏文档 → typed parity 错误）。"""
        with self._lock:
            if self._loaded:
                return
            root = self._settings.registry_dir / "registry"
            if root.exists():
                for path in sorted(root.glob("*/*.json")):
                    if path.name == REGISTRY_INDEX_FILENAME or path.name.endswith(".tmp"):
                        continue
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                    except ValueError as exc:
                        raise RegistryParityError(f"corrupt registry document {path.name!r}: {exc}")
                    record = ModelRecord.from_dict(data)
                    identity = record.descriptor.identity
                    existing = self._records.get(identity)
                    if existing is not None and existing.descriptor.checksum != record.descriptor.checksum:
                        raise RegistryParityError(
                            f"registry documents disagree for {identity}: "
                            f"{existing.descriptor.checksum[:12]} vs {record.descriptor.checksum[:12]}"
                        )
                    self._records[identity] = record
            self._loaded = True

    def validate_parity(self) -> List[str]:
        """index vs documents 一致性（registry parity/health， Epic §A 要求）。"""
        problems: List[str] = []
        with self._lock:
            root = self._settings.registry_dir / "registry"
            docs: Dict[str, Dict[str, Any]] = {}
            if root.exists():
                for path in sorted(root.glob("*/*.json")):
                    if path.name == REGISTRY_INDEX_FILENAME or path.name.endswith(".tmp"):
                        continue
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        rec = ModelRecord.from_dict(data)
                    except (ValueError, RegistryParityError, DescriptorError) as exc:
                        problems.append(f"unparsable document {path.name!r}: {exc}")
                        continue
                    docs[f"{rec.descriptor.model_id}@{rec.descriptor.model_version}"] = {
                        "scope": rec.owner_scope,
                        "revision": rec.revision,
                        "checksum": rec.descriptor.checksum,
                    }
            index_path = self._index_path()
            if index_path.exists():
                try:
                    index = json.loads(index_path.read_text(encoding="utf-8"))
                except ValueError as exc:
                    problems.append(f"corrupt index: {exc}")
                    index = {"entries": {}}
                for key, entry in index.get("entries", {}).items():
                    if key not in docs:
                        problems.append(f"index entry {key!r} has no document")
                    elif docs[key] != entry:
                        problems.append(f"index entry {key!r} disagrees with document")
                for key in docs:
                    if key not in index.get("entries", {}):
                        problems.append(f"document {key!r} missing from index")
            elif docs:
                problems.append("index missing but documents exist")
            for key in self._records:
                if f"{key[0]}@{key[1]}" not in docs and (root.exists()):
                    problems.append(f"in-memory record {key} not persisted")
        return problems

    # ── 注册/查询 ───────────────────────────────────────────────────
    def register(
        self,
        descriptor: GeoModelDescriptor,
        *,
        owner_scope: Dict[str, str],
        registered_by: str = "",
        package_report: Optional[Dict[str, Any]] = None,
        known_provider_refs: Optional[Callable[[str], bool]] = None,
    ) -> ModelRecord:
        """注册一个模型版本（不可变；碰撞即 typed 拒绝）。

        ``known_provider_refs``：ProviderRegistry 的可调用探测（``id → bool``）。
        提供 C1 静态拒绝：descriptor.provider_ref 必须解析为已注册 provider
        实例 id —— registry 永不动态加载代码。
        """
        if set(owner_scope) - {"global", "session_id", "project_id"} or not owner_scope:
            raise DescriptorError(
                "owner_scope must be {'global'} or exactly one of session_id/project_id"
            )
        if len(owner_scope) > 1 or ("global" in owner_scope and len(owner_scope) > 1):
            raise DescriptorError("owner_scope must be a single dimension")
        if known_provider_refs is not None and not known_provider_refs(descriptor.provider_ref):
            from app.lib.modelops.errors import ProviderError

            raise ProviderError(
                f"descriptor.provider_ref {descriptor.provider_ref!r} does not resolve to a "
                "registered provider instance; dynamic code loading is forbidden (ADR-0119 R1-C1)",
                correction_hint="register the provider implementation first, then reference its id",
            )
        with self._lock:
            self.load()
            identity = descriptor.identity
            existing = self._records.get(identity)
            if existing is not None:
                if existing.descriptor.checksum != descriptor.checksum:
                    raise ModelVersionCollision(
                        f"{identity} already registered with different checksum "
                        f"{existing.descriptor.checksum[:12]}…"
                    )
                return existing  # 幂等重注册 = no-op（同内容）
            record = ModelRecord(
                descriptor=descriptor,
                owner_scope=dict(owner_scope),
                revision=1,
                registered_at=time.time(),
                registered_by=registered_by,
                package_report=dict(package_report or {}),
            )
            self._records[identity] = record
            self._persist(record)
            return record

    def resolve(
        self,
        model_id: str,
        model_version: Optional[str] = None,
        *,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> ModelRecord:
        """解析请求者可见的模型（global 种子 + 本 owner；跨 owner 不可见）。

        未指定版本 → 该 id 的最新注册版本（revision 语义：同 id 多版本并存，
        "最新" = 注册序最大；显式版本永远优先）。
        """
        with self._lock:
            self.load()
            candidates: List[ModelRecord] = []
            for (mid, mver), rec in self._records.items():
                if mid != model_id:
                    continue
                scope = rec.owner_scope
                if "global" in scope:
                    candidates.append(rec)
                elif session_id and scope.get("session_id") == session_id:
                    candidates.append(rec)
                elif project_id and scope.get("project_id") == project_id:
                    candidates.append(rec)
            if model_version is not None:
                candidates = [r for r in candidates if r.descriptor.model_version == model_version]
            if not candidates:
                raise ModelNotFoundError(
                    f"model {model_id!r}"
                    + (f"@{model_version}" if model_version else "")
                    + " not found in this owner scope"
                )
            candidates.sort(key=lambda r: (r.descriptor.model_version, r.revision))
            return candidates[-1]

    def list_models(
        self,
        *,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> List[ModelRecord]:
        """请求者可见模型清单（global + 本 owner；去重到每 identity 最新）。"""
        with self._lock:
            self.load()
            visible: Dict[Tuple[str, str], ModelRecord] = {}
            for (mid, mver), rec in self._records.items():
                scope = rec.owner_scope
                allowed = "global" in scope or (
                    session_id and scope.get("session_id") == session_id
                ) or (project_id and scope.get("project_id") == project_id)
                if not allowed:
                    continue
                if task_type is not None and task_type not in rec.descriptor.task_types:
                    continue
                visible[(mid, mver)] = rec
            return sorted(
                visible.values(),
                key=lambda r: (r.descriptor.model_id, r.descriptor.model_version),
            )

    def count(self) -> int:
        with self._lock:
            self.load()
            return len(self._records)
