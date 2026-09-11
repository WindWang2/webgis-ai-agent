"""ModelRegistryStore —— learned-model 注册表（ADR-0119 §3.6）。

与既有注册表的边界（架构挑战 M1/基线 Q3/Q7；Round1 C-6/m-3 修订）：

- **不是** chat LLM 域（``app/services/chat/model_runtime/descriptors.py``）；
- **不是** AlgorithmRegistry（ADR-0099 方法语义域）；
- GeoAI 推理模型的唯一身份/版本/完整性真相源。

契约：

- 身份 = **(owner_scope, model_id, model_version)**（R1-C6：跨 owner 的
  同名模型互不干扰——否则 owner B 的合法注册会撞掉 owner A 并使 parity
  拒绝整个 registry）；同 scope 内同 identity 不同 checksum = 碰撞
  （typed 拒绝）；
- owner scope：恰好一维，key+value 都过白名单（value 防路径穿越）；
- 条目不可变：``seq`` 全局单调，"最新版本" = seq 最大（R1-m3：禁版本
  字符串字典序）；
- ``provider_ref`` 只接受 ProviderRegistry 已注册实例 id（挑战 C1）；
- 存储：scope 目录 + 单条 JSON 文档 + index，原子写，parity 校验。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
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

#: scope 值白名单（R1-C6：session/project id 的 charset 边界即拒绝）。
_SCOPE_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")

_ALLOWED_SCOPE_KEYS = {"global", "session_id", "project_id"}


def scope_key(owner_scope: Dict[str, str]) -> str:
    return "_".join(f"{k}-{v}" for k, v in sorted(owner_scope.items()))


@dataclass(frozen=True)
class ModelRecord:
    """registry 一条不可变记录（descriptor + 治理元数据）。"""

    descriptor: GeoModelDescriptor
    owner_scope: Dict[str, str]
    revision: int
    registered_at: float
    registered_by: str
    #: 全局单调注册序（"最新版本"语义的唯一依据，R1-m3）。
    seq: int
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
            "seq": self.seq,
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
            seq=int(data.get("seq", 0)),
            package_report=dict(data.get("package_report") or {}),
        )


class _cross_process_lock:
    """best-effort 跨进程注册锁（O_EXCL lockfile + 30s stale 接管，R2-M6）。

    单进程部署零竞争；多副本部署下并发 register 由本锁串行（锁失效的
    最坏后果 = ModelVersionCollision 误报/漏报，不产生文档损坏——tmp 名
    已唯一化）。
    """

    def __init__(self, path: Path, *, timeout_s: float = 30.0) -> None:
        self._path = path
        self._timeout_s = timeout_s

    def __enter__(self) -> "_cross_process_lock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self._timeout_s
        while True:
            try:
                self._fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    age = time.time() - self._path.stat().st_mtime
                    if age > 30.0:  # stale：接管
                        self._path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    logger.warning("register lock wait timeout at %s; proceeding", self._path)
                    self._fd = None
                    return self
                time.sleep(0.05)

    def __exit__(self, *exc: Any) -> None:
        if getattr(self, "_fd", None) is not None:
            os.close(self._fd)
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass


def _validate_owner_scope(owner_scope: Dict[str, str]) -> None:
    if not owner_scope or set(owner_scope) - _ALLOWED_SCOPE_KEYS or len(owner_scope) != 1:
        raise DescriptorError(
            "owner_scope must be {'global'} or exactly one of session_id/project_id"
        )
    for key, value in owner_scope.items():
        if key == "global":
            continue
        if not isinstance(value, str) or not _SCOPE_VALUE_RE.match(value):
            raise DescriptorError(
                f"invalid owner scope value for {key!r} (charset/length whitelist)"
            )


class ModelRegistryStore:
    """持久化模型注册表（进程内单例语义；文件原子写 + 进程锁）。"""

    def __init__(self, settings: Optional[ModelOpsSettings] = None) -> None:
        self._settings = settings or ModelOpsSettings.load()
        self._lock = threading.RLock()
        # R1-C6：键 = (scope_key, model_id, model_version)。
        self._records: Dict[Tuple[str, str, str], ModelRecord] = {}
        self._seq = 0
        self._loaded = False
        # #1210：load() 时的 index 指纹快照 —— 多副本部署下其它进程 register
        # 会重写 index.json；指纹变化即触发全量重扫（修 _loaded 短路导致的
        # 内存注册表永不刷新 / seq 重复分配 / 碰撞漏检）。
        self._index_fp: Optional[tuple] = None

    # ── 持久化 ──────────────────────────────────────────────────────
    def _scope_dir(self, owner_scope: Dict[str, str]) -> Path:
        return self._settings.registry_dir / "registry" / scope_key(owner_scope)

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
        # R2-M6：tmp 名含 pid+uuid —— 并发写不互踩（固定名会让 A 的
        # os.replace 搬走 B 半写的 tmp → 文档损坏 → parity 永久拒绝）。
        tmp = path.with_suffix(f".json.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
        tmp.write_text(json.dumps(record.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        self._write_index()

    def _write_index(self) -> None:
        entries: Dict[str, Any] = {}
        for (skey, model_id, model_version), rec in sorted(self._records.items()):
            entries[f"{skey}/{model_id}@{model_version}"] = {
                "scope": rec.owner_scope,
                "revision": rec.revision,
                "seq": rec.seq,
                "checksum": rec.descriptor.checksum,
            }
        payload = {"schema_version": REGISTRY_SCHEMA_VERSION, "entries": entries}
        path = self._index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".json.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        # #1210：自己写的 index 也刷新快照（避免下一次 load 触发无谓重扫）。
        self._index_fp = self._index_fingerprint()

    def _index_fingerprint(self) -> Optional[tuple]:
        """index.json 的廉价指纹（条目数 + max seq + mtime_ns）。"""
        idx = self._index_path()
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
            entries = data.get("entries", {})
            max_seq = max(
                (int(e.get("seq", 0)) for e in entries.values() if isinstance(e, dict)),
                default=0,
            )
            return (len(entries), max_seq, idx.stat().st_mtime_ns)
        except (OSError, ValueError, TypeError):
            return None

    def load(self) -> None:
        """从磁盘加载全部记录（幂等；损坏文档 → typed parity 错误）。

        #1210：幂等短路前先比对 index 指纹 —— 其它进程 register 后
        index.json 变化，本副本自动重扫（单进程路径指纹不变，零额外成本）。
        """
        with self._lock:
            if self._loaded:
                if self._index_fingerprint() == self._index_fp:
                    return
                self._records.clear()
                self._seq = 0
            root = self._settings.registry_dir / "registry"
            if root.exists():
                for path in sorted(root.glob("*/*.json")):
                    if path.name == REGISTRY_INDEX_FILENAME or path.name.endswith(".tmp"):
                        continue
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                    except ValueError as exc:
                        raise RegistryParityError(
                            f"corrupt registry document {path.name!r}: {exc}"
                        )
                    record = ModelRecord.from_dict(data)
                    key = (
                        scope_key(record.owner_scope),
                        record.descriptor.model_id,
                        record.descriptor.model_version,
                    )
                    existing = self._records.get(key)
                    if existing is not None and (
                        existing.descriptor.checksum != record.descriptor.checksum
                        or existing.seq != record.seq
                    ):
                        raise RegistryParityError(
                            f"registry documents disagree for {key}"
                        )
                    self._records[key] = record
                    self._seq = max(self._seq, record.seq)
            self._loaded = True
            self._index_fp = self._index_fingerprint()

    def validate_parity(self) -> List[str]:
        """index vs documents 一致性（registry parity/health）。"""
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
                    key = f"{scope_key(rec.owner_scope)}/{rec.descriptor.model_id}@{rec.descriptor.model_version}"
                    docs[key] = {
                        "scope": rec.owner_scope,
                        "revision": rec.revision,
                        "seq": rec.seq,
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
            for skey, mid, mver in self._records:
                if root.exists() and f"{skey}/{mid}@{mver}" not in docs:
                    problems.append(f"in-memory record {(mid, mver)} not persisted")
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
        package_bytes: Optional[bytes] = None,
    ) -> ModelRecord:
        """注册一个模型版本（scope 内不可变；碰撞 typed 拒绝；跨 scope 隔离）。

        R2 m-3：真实包路径必须传 ``package_bytes`` —— 校验全门
        （checksum/结构/成员黑名单）后以报告入册；synthetic 种子（无实体
        包）显式提供报告并标注 ``synthetic: true``，两条路径都不可绕过
        校验叙事。V3 §B：单文件模型工件（onnx/torchscript）按
        ``descriptor.artifact_format`` 分发到 ``inspect_model_file``，
        其余格式走 ``inspect_archive`` 结构审查。
        """
        if package_bytes is not None:
            from app.lib.modelops.package_security import (
                SINGLE_FILE_FORMAT_SUFFIXES,
                inspect_archive,
                inspect_model_file,
            )

            single_suffix = SINGLE_FILE_FORMAT_SUFFIXES.get(descriptor.artifact_format)
            if single_suffix is not None:
                report = inspect_model_file(
                    package_bytes,
                    expected_checksum=descriptor.checksum,
                    allowed_suffix=single_suffix,
                )
            else:
                report = inspect_archive(package_bytes, expected_checksum=descriptor.checksum)
            if report.checksum != descriptor.checksum:
                from app.lib.modelops.errors import ModelChecksumError

                raise ModelChecksumError(
                    f"package checksum {report.checksum[:12]}… != descriptor "
                    f"{descriptor.checksum[:12]}…"
                )
            package_report = report.as_dict()
        _validate_owner_scope(owner_scope)
        if known_provider_refs is not None and not known_provider_refs(descriptor.provider_ref):
            from app.lib.modelops.errors import ProviderError

            raise ProviderError(
                f"descriptor.provider_ref {descriptor.provider_ref!r} does not resolve to a "
                "registered provider instance; dynamic code loading is forbidden (ADR-0119 R1-C1)",
                correction_hint="register the provider implementation first, then reference its id",
            )
        lock_path = self._scope_dir(owner_scope) / ".register.lock"
        with _cross_process_lock(lock_path):
            return self._register_locked(descriptor, owner_scope, registered_by,
                                         package_report, known_provider_refs)

    def _register_locked(
        self,
        descriptor: GeoModelDescriptor,
        owner_scope: Dict[str, str],
        registered_by: str,
        package_report: Optional[Dict[str, Any]],
        known_provider_refs: Optional[Callable[[str], bool]],
    ) -> ModelRecord:
        with self._lock:
            self.load()
            skey = scope_key(owner_scope)
            key = (skey, descriptor.model_id, descriptor.model_version)
            existing = self._records.get(key)
            if existing is not None:
                if existing.descriptor.checksum != descriptor.checksum:
                    raise ModelVersionCollision(
                        f"{(descriptor.model_id, descriptor.model_version)} already registered "
                        f"in scope {skey!r} with different checksum "
                        f"{existing.descriptor.checksum[:12]}…"
                    )
                return existing  # 幂等重注册 = no-op（同内容）
            self._seq += 1
            record = ModelRecord(
                descriptor=descriptor,
                owner_scope=dict(owner_scope),
                revision=1,
                registered_at=time.time(),
                registered_by=registered_by,
                seq=self._seq,
                package_report=dict(package_report or {}),
            )
            self._records[key] = record
            self._persist(record)
            return record

    def _visible(self, rec: ModelRecord, *, session_id: Optional[str],
                 project_id: Optional[str]) -> bool:
        sc = rec.owner_scope
        return "global" in sc or (
            session_id is not None and sc.get("session_id") == session_id
        ) or (project_id is not None and sc.get("project_id") == project_id)

    def resolve(
        self,
        model_id: str,
        model_version: Optional[str] = None,
        *,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> ModelRecord:
        """解析请求者可见的模型（global 种子 + 本 owner；跨 owner 不可见）。

        未指定版本 → 该 id **seq 最大**的可见版本（R1-m3：注册序，非字典序）。
        """
        with self._lock:
            self.load()
            candidates = [
                rec
                for (_s, mid, mver), rec in self._records.items()
                if mid == model_id
                and (model_version is None or mver == model_version)
                and self._visible(rec, session_id=session_id, project_id=project_id)
            ]
            if not candidates:
                raise ModelNotFoundError(
                    f"model {model_id!r}"
                    + (f"@{model_version}" if model_version else "")
                    + " not found in this owner scope"
                )
            candidates.sort(key=lambda r: r.seq)
            return candidates[-1]

    def list_models(
        self,
        *,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> List[ModelRecord]:
        """请求者可见模型清单（global + 本 owner；每 identity 取 seq 最新）。"""
        with self._lock:
            self.load()
            visible: Dict[Tuple[str, str], ModelRecord] = {}
            for (_s, mid, mver), rec in self._records.items():
                if not self._visible(rec, session_id=session_id, project_id=project_id):
                    continue
                if task_type is not None and task_type not in rec.descriptor.task_types:
                    continue
                prev = visible.get((mid, mver))
                if prev is None or rec.seq > prev.seq:
                    visible[(mid, mver)] = rec
            return sorted(
                visible.values(),
                key=lambda r: (r.descriptor.model_id, r.descriptor.model_version),
            )

    def count(self) -> int:
        with self._lock:
            self.load()
            return len(self._records)
