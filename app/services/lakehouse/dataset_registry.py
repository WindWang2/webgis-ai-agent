"""Dataset registry — Versioned Geospatial Lakehouse V8 (ADR-0130).

数据集级版本层的**单一写入口**：dataset / version / branch / tag /
rollback。字节真相仍只有 BlobStore（CAS）一份 —— 本模块发布的两类
manifest 与 DataObject 同纪律（canonical JSON → 内容寻址、确定性、
无 wall-clock/随机、有界）：

- **dataset 描述符**：``kind="dataset_descriptor"``，id = canonical
  sha256。描述符不可变 —— 改名/改契约 = 新 dataset id（新注册行）；
- **版本 commit record**：``kind="dataset_commit"``，id = canonical
  sha256 = version_id。字段 = (dataset_id, parent, data_object_id,
  content_sha256, branch, action, provenance)。同 dataset 同 (parent,
  content, provenance) 的重提交幂等去重为同一版本 —— 「同内容同 id」
  语义在版本层延续（实验复现由此可验证）。

**原子提交协议**（中断安全 —— 验收：中断写入不产生可见半成品版本）：

1. 内容 DataObject 必须已可解析（本层绝不发明内容）；
2. commit manifest 经 CAS 发布（put-if-absent，原子出场）；
3. 版本台账行 savepoint 插入（幂等：唯一键撞 = 复用已落地行）；
4. branch 指针行 generation CAS 前移（乐观并发，撞号 = typed 冲突）。

任意一步中断：分支 head 不动 → 没有可见的"半成品版本"；已发布的
commit manifest 成为 GC 可回收孤儿（准入 GC root 反向 —— 无版本行
引用即候选）。重试同提交 → 同 version_id → 幂等收敛。

**rollback 语义**（revert，非 reset）：回滚 = 以目标版本的内容创建
新 commit（parent = 当前 head，action="rollback"）+ 分支前移 ——
分支历史只增不减，审计可追溯；绝不改写/删除既有版本行。

GC 契约：本模块三表引用的 64-hex id（dataset_id / version_id /
data_object_id）全部进入 ``lakehouse_gc._protected_references``
（仅被版本历史引用的 manifest/blob 绝不回收）。

事务纪律：DAO 只 flush，commit 由调用方控制（与 record_revision /
LineageService 同纪律）。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Mapping, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)

#: manifest/commit 契约版本（结构演进 = 升版本，绝不原地改语义）。
DATASET_DESCRIPTOR_SCHEMA_VERSION = 1
COMMIT_RECORD_SCHEMA_VERSION = 1

#: commit record canonical 尺寸闸（与 DataObject manifest 同量级纪律）。
MAX_COMMIT_JSON_BYTES = 64 * 1024
#: provenance 有界约定（固定键集合；防御性键数上限）。
MAX_PROVENANCE_KEYS = 24
#: 版本 DAG 上溯 / 列表有界（诚实截断披露）。
MAX_LINEAGE_DEPTH = 64
MAX_LIST_LIMIT = 200

#: dataset 名 / branch / tag 名白名单（路径段边界 —— 平面命名空间，
#: 不含 '/'；层级语义用 '-' 表达）。
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}\Z")
_ID64_RE = re.compile(r"^[a-f0-9]{64}\Z")

#: commit action 有界集合（与模型 CheckConstraint 同源）。
COMMIT_ACTIONS = ("commit", "rollback", "delta", "import", "workflow_publish")


class DatasetRegistryError(ValueError):
    """数据集版本层契约违例。"""

    code = "DATASET_INVALID"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict:
        return {"success": False, "code": self.code, "message": self.message}


class DatasetNotFound(DatasetRegistryError):
    code = "DATASET_NOT_FOUND"


class DatasetVersionNotFound(DatasetRegistryError):
    code = "DATASET_VERSION_NOT_FOUND"


class DatasetRefConflict(DatasetRegistryError):
    """并发 branch 移动撞号 / tag 已存在。"""

    code = "DATASET_REF_CONFLICT"


class DatasetContentUnresolved(DatasetRegistryError):
    """commit 引用的内容 DataObject 不可解析 / 越权。"""

    code = "DATASET_CONTENT_UNRESOLVED"


class CommitIdentity(NamedTuple):
    """publish 结果：version_id 即 commit manifest digest。"""

    version_id: str
    deduped: bool


class CommitResult(NamedTuple):
    """commit_version 结果（分支移动后的位置证据）。"""

    version_id: str
    parent_version_id: Optional[str]
    data_object_id: str
    branch: str
    generation: int
    version_deduped: bool
    ref_moved: bool


# ── CAS manifest（描述符 / commit record）────────────────────────────────


def _canonical_sha(payload: Mapping[str, Any]) -> str:
    from app.lib.data.fingerprints import canonical_dumps, sha256_hex

    return sha256_hex(canonical_dumps(payload))


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    from app.lib.data.fingerprints import canonical_dumps

    return canonical_dumps(payload).encode("utf-8")


def _object_store() -> Any:
    from app.services.s3_blob_store import get_object_store

    return get_object_store()


def _redact(value: Any) -> Any:
    """provenance 防御性脱敏（复用 provenance 唯一口径）。"""
    from app.services.provenance.manifest import redact_provenance_args

    return redact_provenance_args(value)


def build_dataset_descriptor(
    *,
    name: str,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    description: str = "",
    default_branch: str = "main",
    cube_contract: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """确定性 dataset 描述符（无 wall-clock —— 同输入同 id）。

    **owner_scope 参与身份**（与 DataObject 同纪律）：跨 owner 的同名
    dataset 是两个不同的逻辑数据集（BlobStore 字节级共享仍安全 ——
    访问永远经 owner-checked 台账行）。
    """
    if not _NAME_RE.match(name or ""):
        raise DatasetRegistryError(
            f"invalid dataset name: {str(name)[:32]!r} "
            "(expected [A-Za-z0-9._-]{1,128})"
        )
    if not _NAME_RE.match(default_branch or ""):
        raise DatasetRegistryError(
            f"invalid default branch: {str(default_branch)[:32]!r}"
        )
    from app.services.lakehouse.data_object import normalize_owner_scope

    descriptor: Dict[str, Any] = {
        "schema_version": DATASET_DESCRIPTOR_SCHEMA_VERSION,
        "kind": "dataset_descriptor",
        "owner_scope": normalize_owner_scope(
            session_id=session_id, project_id=project_id
        ),
        "name": name,
        "description": str(description or "")[:512],
        "default_branch": default_branch,
    }
    if cube_contract is not None:
        if not isinstance(cube_contract, Mapping):
            raise DatasetRegistryError("cube_contract must be a mapping")
        descriptor["cube_contract"] = _redact(dict(cube_contract))
    return descriptor


def dataset_descriptor_id(descriptor: Mapping[str, Any]) -> str:
    return _canonical_sha(descriptor)


def publish_dataset_descriptor(descriptor: Mapping[str, Any]) -> CommitIdentity:
    """描述符 manifest CAS 发布（幂等）。"""
    store = _object_store()
    payload = _canonical_bytes(descriptor)
    did = _canonical_sha(descriptor)
    result = store.put_blob(did, payload, "json")
    return CommitIdentity(version_id=did, deduped=not result.put_new)


def resolve_dataset_descriptor(dataset_id: str) -> Optional[Dict[str, Any]]:
    """digest 校验解析描述符（不可解析 = None，诚实）。"""
    if not _ID64_RE.match(dataset_id or ""):
        return None
    raw = _object_store().get_blob(dataset_id, expected_sha256=dataset_id)
    if raw is None:
        return None
    import json as _json

    try:
        parsed = _json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, Mapping) or parsed.get("kind") != "dataset_descriptor":
        return None
    return dict(parsed)


def build_commit_record(
    *,
    dataset_id: str,
    parent_version_id: Optional[str],
    data_object_id: str,
    content_sha256: str,
    action: str = "commit",
    provenance: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """确定性 commit record（身份 = canonical sha256 —— 绝不含 wall-clock）。

    **branch 不参与身份**（git 语义）：分支是指针层的落点注记（台账行
    记录），同一 (parent, content, provenance) 经不同分支重放 = 同一
    版本 —— 实验复现不依赖分支命名。
    """
    if not _ID64_RE.match(dataset_id or ""):
        raise DatasetRegistryError("commit requires a 64-hex dataset_id")
    if not _ID64_RE.match(data_object_id or ""):
        raise DatasetRegistryError("commit requires a 64-hex data_object_id")
    if parent_version_id is not None and not _ID64_RE.match(parent_version_id):
        raise DatasetRegistryError("parent_version_id must be 64-hex or None")
    if action not in COMMIT_ACTIONS:
        raise DatasetRegistryError(
            f"invalid commit action {action!r} (expected one of {COMMIT_ACTIONS})"
        )
    prov = _bounded_provenance(provenance)
    record: Dict[str, Any] = {
        "schema_version": COMMIT_RECORD_SCHEMA_VERSION,
        "kind": "dataset_commit",
        "dataset_id": dataset_id,
        "parent_version_id": parent_version_id,
        "data_object_id": data_object_id,
        "content_sha256": str(content_sha256 or ""),
        "action": action,
        "provenance": prov,
    }
    if len(_canonical_bytes(record)) > MAX_COMMIT_JSON_BYTES:
        raise DatasetRegistryError(
            "commit record exceeds canonical size cap — provenance must be "
            "summarized (oversized payloads belong in a blob, not identity)"
        )
    return record


def _bounded_provenance(
    provenance: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """provenance 契约：固定键、redacted、有界（超界键拒绝 —— 诚实）。"""
    if provenance is None:
        return {}
    if not isinstance(provenance, Mapping):
        raise DatasetRegistryError("provenance must be a mapping")
    if len(provenance) > MAX_PROVENANCE_KEYS:
        raise DatasetRegistryError(
            f"provenance exceeds {MAX_PROVENANCE_KEYS} keys"
        )
    return _redact(dict(provenance))


def commit_record_id(record: Mapping[str, Any]) -> str:
    return _canonical_sha(record)


def publish_commit_record(record: Mapping[str, Any]) -> CommitIdentity:
    """commit manifest CAS 发布（幂等；中断重试同 id 收敛）。"""
    store = _object_store()
    payload = _canonical_bytes(record)
    vid = _canonical_sha(record)
    result = store.put_blob(vid, payload, "json")
    return CommitIdentity(version_id=vid, deduped=not result.put_new)


def resolve_commit_record(version_id: str) -> Optional[Dict[str, Any]]:
    if not _ID64_RE.match(version_id or ""):
        return None
    raw = _object_store().get_blob(version_id, expected_sha256=version_id)
    if raw is None:
        return None
    import json as _json

    try:
        parsed = _json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, Mapping) or parsed.get("kind") != "dataset_commit":
        return None
    return dict(parsed)


# ── DAO：dataset 注册行 ──────────────────────────────────────────────────


def _owner_kwargs(
    *, session_id: Optional[str] = None, project_id: Optional[str] = None
) -> Dict[str, str]:
    if bool(session_id) == bool(project_id):
        raise DatasetRegistryError(
            "exactly one of session_id / project_id is required"
        )
    if session_id is not None:
        return {"owner_type": "session", "owner_id": session_id}
    return {"owner_type": "project", "owner_id": str(project_id)}


def create_dataset(
    db,
    *,
    name: str,
    description: str = "",
    default_branch: str = "main",
    cube_contract: Optional[Mapping[str, Any]] = None,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Tuple[Any, bool]:
    """注册数据集（幂等：同 owner 同描述符复用既有行）。

    描述符 manifest CAS 发布 + 台账行 savepoint 插入；返回 ``(row, created)``。
    只 flush 不 commit。
    """
    from sqlalchemy import select

    from app.models.lakehouse_datasets import LakehouseDataset

    owner = _owner_kwargs(session_id=session_id, project_id=project_id)
    descriptor = build_dataset_descriptor(
        name=name,
        session_id=session_id,
        project_id=project_id,
        description=description,
        default_branch=default_branch,
        cube_contract=cube_contract,
    )
    did = dataset_descriptor_id(descriptor)
    publish_dataset_descriptor(descriptor)
    existing = db.execute(
        select(LakehouseDataset).where(LakehouseDataset.dataset_id == did)
    ).scalar_one_or_none()
    if existing is not None:
        # owner 参与描述符身份：id 相同 = 同一 (owner, 描述符) → 复用。
        return existing, False
    row = LakehouseDataset(
        dataset_id=did,
        name=descriptor["name"],
        description=descriptor.get("description") or "",
        default_branch=descriptor["default_branch"],
        cube_contract=descriptor.get("cube_contract"),
        **owner,
    )
    with db.begin_nested():
        db.add(row)
        db.flush()
    return row, True


def get_dataset_by_descriptor_id(db, dataset_id: str):
    from sqlalchemy import select

    from app.models.lakehouse_datasets import LakehouseDataset

    return db.execute(
        select(LakehouseDataset).where(LakehouseDataset.dataset_id == dataset_id)
    ).scalar_one_or_none()


def get_dataset(db, dataset_row_id: str):
    from sqlalchemy import select

    from app.models.lakehouse_datasets import LakehouseDataset

    return db.execute(
        select(LakehouseDataset).where(LakehouseDataset.id == dataset_row_id)
    ).scalar_one_or_none()


def dataset_owner_allows(row, *, session_id: Optional[str] = None,
                         project_id: Optional[str] = None) -> bool:
    """owner 校验（fail-closed：非 owner = False，绝不泄漏存在性）。"""
    if session_id is not None:
        return row is not None and row.owner_type == "session" \
            and row.owner_id == session_id
    if project_id is not None:
        return row is not None and row.owner_type == "project" \
            and row.owner_id == project_id
    return False


def list_datasets(
    db, *, session_id: Optional[str] = None, project_id: Optional[str] = None,
    limit: int = MAX_LIST_LIMIT,
):
    from sqlalchemy import select

    from app.models.lakehouse_datasets import LakehouseDataset

    owner = _owner_kwargs(session_id=session_id, project_id=project_id)
    return list(
        db.execute(
            select(LakehouseDataset)
            .where(
                LakehouseDataset.owner_type == owner["owner_type"],
                LakehouseDataset.owner_id == owner["owner_id"],
            )
            .order_by(LakehouseDataset.created_at.desc())
            .limit(max(1, min(int(limit or MAX_LIST_LIMIT), MAX_LIST_LIMIT)))
        ).scalars().all()
    )


# ── DAO：版本账本与指针 ──────────────────────────────────────────────────


def get_version_row(db, dataset_row_id: str, version_id: str):
    from sqlalchemy import select

    from app.models.lakehouse_datasets import LakehouseDatasetVersion

    return db.execute(
        select(LakehouseDatasetVersion).where(
            LakehouseDatasetVersion.dataset_row_id == dataset_row_id,
            LakehouseDatasetVersion.version_id == version_id,
        )
    ).scalar_one_or_none()


def list_versions(
    db, dataset_row_id: str, *, branch: Optional[str] = None,
    limit: int = MAX_LIST_LIMIT,
) -> List[Any]:
    """版本历史（新→旧，有界；branch 过滤走台账 branch 列）。"""
    from sqlalchemy import select

    from app.models.lakehouse_datasets import LakehouseDatasetVersion

    stmt = select(LakehouseDatasetVersion).where(
        LakehouseDatasetVersion.dataset_row_id == dataset_row_id
    )
    if branch is not None:
        if not _NAME_RE.match(branch):
            raise DatasetRegistryError(f"invalid branch name: {branch[:32]!r}")
        stmt = stmt.where(LakehouseDatasetVersion.branch == branch)
    return list(
        db.execute(
            stmt.order_by(LakehouseDatasetVersion.created_at.desc(),
                          LakehouseDatasetVersion.id.desc())
            .limit(max(1, min(int(limit or MAX_LIST_LIMIT), MAX_LIST_LIMIT)))
        ).scalars().all()
    )


def get_ref(db, dataset_row_id: str, *, ref_type: str, ref_name: str):
    from sqlalchemy import select

    from app.models.lakehouse_datasets import LakehouseDatasetRef

    if ref_type not in ("branch", "tag"):
        raise DatasetRegistryError(f"invalid ref_type: {ref_type!r}")
    if not _NAME_RE.match(ref_name or ""):
        raise DatasetRegistryError(f"invalid ref name: {str(ref_name)[:32]!r}")
    return db.execute(
        select(LakehouseDatasetRef).where(
            LakehouseDatasetRef.dataset_row_id == dataset_row_id,
            LakehouseDatasetRef.ref_type == ref_type,
            LakehouseDatasetRef.ref_name == ref_name,
        )
    ).scalar_one_or_none()


def list_refs(db, dataset_row_id: str, *, ref_type: Optional[str] = None):
    from sqlalchemy import select

    from app.models.lakehouse_datasets import LakehouseDatasetRef

    stmt = select(LakehouseDatasetRef).where(
        LakehouseDatasetRef.dataset_row_id == dataset_row_id
    )
    if ref_type is not None:
        if ref_type not in ("branch", "tag"):
            raise DatasetRegistryError(f"invalid ref_type: {ref_type!r}")
        stmt = stmt.where(LakehouseDatasetRef.ref_type == ref_type)
    return list(
        db.execute(
            stmt.order_by(LakehouseDatasetRef.ref_type,
                          LakehouseDatasetRef.ref_name)
        ).scalars().all()
    )


def branch_head(db, dataset_row_id: str, branch: str):
    """分支当前 head 指针行（无提交历史 = None）。"""
    return get_ref(db, dataset_row_id, ref_type="branch", ref_name=branch)


def create_branch(
    db,
    dataset_row,
    *,
    name: str,
    from_version_id: Optional[str] = None,
) -> Tuple[Any, bool]:
    """从某版本（缺省当前默认分支 head）开分支（幂等：已存在复用）。"""
    from sqlalchemy.exc import IntegrityError

    from app.models.lakehouse_datasets import LakehouseDatasetRef

    if not _NAME_RE.match(name or ""):
        raise DatasetRegistryError(f"invalid branch name: {str(name)[:32]!r}")
    existing = get_ref(db, dataset_row.id, ref_type="branch", ref_name=name)
    if existing is not None:
        return existing, False
    if from_version_id is None:
        head = branch_head(db, dataset_row.id, dataset_row.default_branch)
        if head is None:
            raise DatasetRegistryError(
                "cannot branch from an empty history — commit a first version "
                "or pass from_version_id explicitly"
            )
        from_version_id = head.version_id
    target = get_version_row(db, dataset_row.id, from_version_id)
    if target is None:
        raise DatasetVersionNotFound(
            f"branch source version not found: {from_version_id[:12]}"
        )
    row = LakehouseDatasetRef(
        dataset_row_id=dataset_row.id,
        ref_type="branch",
        ref_name=name,
        version_id=target.version_id,
        generation=1,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        winner = get_ref(db, dataset_row.id, ref_type="branch", ref_name=name)
        if winner is not None:
            return winner, False
        raise
    return row, True


def create_tag(
    db, dataset_row, *, name: str, version_id: str,
) -> Tuple[Any, bool]:
    """创建不可变 tag（已存在 → typed 冲突 —— 绝不静默改写）。"""
    from sqlalchemy.exc import IntegrityError

    from app.models.lakehouse_datasets import LakehouseDatasetRef

    if not _NAME_RE.match(name or ""):
        raise DatasetRegistryError(f"invalid tag name: {str(name)[:32]!r}")
    if get_ref(db, dataset_row.id, ref_type="tag", ref_name=name) is not None:
        raise DatasetRefConflict(f"tag already exists: {name}")
    target = get_version_row(db, dataset_row.id, version_id)
    if target is None:
        raise DatasetVersionNotFound(
            f"tag target version not found: {str(version_id)[:12]}"
        )
    row = LakehouseDatasetRef(
        dataset_row_id=dataset_row.id,
        ref_type="tag",
        ref_name=name,
        version_id=target.version_id,
        generation=1,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        raise DatasetRefConflict(f"tag already exists: {name}")
    return row, True


def _move_branch_ref(db, dataset_row, *, branch: str, version_id: str,
                     expect: Optional[Any]) -> int:
    """branch 指针 generation CAS 前移（乐观并发）。

    ``expect``：观察到的指针行（None = 首次创建）。行已被并发移动
    （generation 漂移）→ typed 冲突；返回移动后的 generation。
    """
    from sqlalchemy import update

    from app.models.lakehouse_datasets import LakehouseDatasetRef

    if expect is None:
        row = LakehouseDatasetRef(
            dataset_row_id=dataset_row.id,
            ref_type="branch",
            ref_name=branch,
            version_id=version_id,
            generation=1,
        )
        from sqlalchemy.exc import IntegrityError

        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            # 并发首提交撞唯一键 → 视为 generation=1 的期望行重试一次 CAS。
            observed = get_ref(db, dataset_row.id, ref_type="branch",
                               ref_name=branch)
            if observed is None:
                raise DatasetRefConflict(
                    f"branch concurrently created: {branch}"
                )
            return _move_branch_ref(db, dataset_row, branch=branch,
                                    version_id=version_id, expect=observed)
        return 1
    result = db.execute(
        update(LakehouseDatasetRef)
        .where(
            LakehouseDatasetRef.dataset_row_id == dataset_row.id,
            LakehouseDatasetRef.ref_type == "branch",
            LakehouseDatasetRef.ref_name == branch,
            LakehouseDatasetRef.generation == int(expect.generation),
        )
        .values(version_id=version_id,
                generation=int(expect.generation) + 1)
    )
    if result.rowcount != 1:
        raise DatasetRefConflict(
            f"branch {branch} moved concurrently "
            f"(expected generation {expect.generation})"
        )
    db.flush()
    return int(expect.generation) + 1


def _resolve_commit_parent(
    db, dataset_row, *, branch: str, parent_version_id: Optional[str],
) -> Optional[str]:
    """parent 解析与校验：显式 parent 必须在台账；缺省 = 分支 head。"""
    if parent_version_id is not None:
        parent = get_version_row(db, dataset_row.id, parent_version_id)
        if parent is None:
            raise DatasetVersionNotFound(
                f"parent version not found: {parent_version_id[:12]}"
            )
        return parent_version_id
    head = branch_head(db, dataset_row.id, branch)
    return head.version_id if head is not None else None


def _resolved_content(
    data_object_id: str,
    *,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """内容 DataObject 解析 + owner 校验（跨 owner 内容 = 越权拒绝）。"""
    from app.services.lakehouse.data_object import (
        owner_scope_allows,
        resolve_data_object,
    )

    manifest = resolve_data_object(data_object_id)
    if manifest is None:
        raise DatasetContentUnresolved(
            f"data object not resolvable: {str(data_object_id)[:12]}"
        )
    if not owner_scope_allows(
        manifest, session_id=session_id, project_id=project_id
    ):
        raise DatasetContentUnresolved(
            "data object belongs to a different owner scope"
        )
    return dict(manifest)


def commit_version(
    db,
    dataset_row,
    *,
    branch: str,
    data_object_id: str,
    action: str = "commit",
    provenance: Optional[Mapping[str, Any]] = None,
    parent_version_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    _hooks: Optional[Mapping[str, Callable[[], None]]] = None,
) -> CommitResult:
    """提交新版本（原子协议见模块 docstring）。只 flush 不 commit。

    ``_hooks``：中断注入 seam（``after_manifest_publish`` /
    ``after_version_record``）—— 测试进程中断语义的确定性证据点；
    生产路径恒 None。
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.models.lakehouse_datasets import LakehouseDatasetVersion

    hooks = dict(_hooks or {})

    def _hook(name: str) -> None:
        fn = hooks.get(name)
        if fn is not None:
            fn()

    owner_kwargs = {
        "session_id": (
            dataset_row.owner_id if dataset_row.owner_type == "session" else None
        ),
        "project_id": (
            dataset_row.owner_id if dataset_row.owner_type == "project" else None
        ),
    }
    if not _NAME_RE.match(branch or ""):
        raise DatasetRegistryError(f"invalid branch name: {str(branch)[:32]!r}")
    manifest = _resolved_content(
        data_object_id, **owner_kwargs  # type: ignore[arg-type]
    )
    parent = _resolve_commit_parent(
        db, dataset_row, branch=branch, parent_version_id=parent_version_id
    )
    # 重提交幂等口径：内容 + parent + provenance 相同 = 同一 commit
    # （branch 是落点注记，不参与身份）。
    record = build_commit_record(
        dataset_id=dataset_row.dataset_id,
        parent_version_id=parent,
        data_object_id=data_object_id,
        content_sha256=str(manifest.get("content_sha256") or ""),
        action=action,
        provenance=provenance,
    )
    published = publish_commit_record(record)
    version_id = published.version_id
    _hook("after_manifest_publish")

    existing = get_version_row(db, dataset_row.id, version_id)
    if existing is None:
        row = LakehouseDatasetVersion(
            dataset_row_id=dataset_row.id,
            version_id=version_id,
            parent_version_id=parent,
            data_object_id=data_object_id,
            content_sha256=str(manifest.get("content_sha256") or ""),
            byte_size=int(manifest.get("byte_size") or 0),
            branch=branch,
            action=action,
            provenance_json=record["provenance"],
            workflow_run_id=workflow_run_id,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            winner = get_version_row(db, dataset_row.id, version_id)
            if winner is None:
                raise
        _hook("after_version_record")

    expect = branch_head(db, dataset_row.id, branch)
    if expect is not None and expect.version_id == version_id:
        generation = int(expect.generation)
        moved = False
    else:
        generation = _move_branch_ref(
            db, dataset_row, branch=branch, version_id=version_id, expect=expect,
        )
        moved = True
    return CommitResult(
        version_id=version_id,
        parent_version_id=parent,
        data_object_id=data_object_id,
        branch=branch,
        generation=generation,
        version_deduped=existing is not None or published.deduped,
        ref_moved=moved,
    )


def rollback_branch(
    db,
    dataset_row,
    *,
    branch: str,
    to_version_id: str,
    provenance: Optional[Mapping[str, Any]] = None,
) -> CommitResult:
    """revert 式回滚：以目标内容创建 rollback commit + 分支前移。

    分支历史只增不减；目标必须属于本数据集；目标 = 当前 head → typed
    NOOP（无意义的空回滚，绝不制造空 commit）。
    """
    target = get_version_row(db, dataset_row.id, to_version_id)
    if target is None:
        raise DatasetVersionNotFound(
            f"rollback target not found: {str(to_version_id)[:12]}"
        )
    head = branch_head(db, dataset_row.id, branch)
    if head is not None and head.version_id == to_version_id:
        raise DatasetRegistryError(
            "branch head already at rollback target — nothing to roll back"
        )
    prov = dict(provenance or {})
    prov.setdefault("rollback_target", to_version_id)
    return commit_version(
        db,
        dataset_row,
        branch=branch,
        data_object_id=target.data_object_id,
        action="rollback",
        provenance=prov,
        parent_version_id=head.version_id if head is not None else None,
    )


def version_lineage(
    db, dataset_row_id: str, version_id: str, *,
    max_depth: int = MAX_LINEAGE_DEPTH,
) -> Dict[str, Any]:
    """沿 parent 链上溯（有界，诚实截断披露）。"""
    chain: List[Dict[str, Any]] = []
    seen = set()
    current: Optional[str] = version_id
    truncated = False
    while current is not None and len(chain) < max(1, int(max_depth)):
        if current in seen:
            truncated = True  # 环（不可能，防御性披露）
            break
        seen.add(current)
        row = get_version_row(db, dataset_row_id, current)
        if row is None:
            truncated = True  # 链断裂（台账被 retention 裁剪）—— 诚实披露
            break
        chain.append(row.to_dict())
        current = row.parent_version_id
    else:
        if current is not None:
            truncated = True
    return {
        "version_id": version_id,
        "chain": chain,
        "depth": len(chain),
        "truncated": truncated,
    }


def resolve_version(
    db, dataset_row, *, version_id: str,
    session_id: Optional[str] = None, project_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """解析版本（内容 manifest 合成；owner fail-closed）。"""
    if not dataset_owner_allows(
        dataset_row, session_id=session_id, project_id=project_id
    ):
        return None
    row = get_version_row(db, dataset_row.id, version_id)
    if row is None:
        return None
    from app.services.lakehouse.data_object import resolve_data_object

    manifest = resolve_data_object(row.data_object_id)
    out = row.to_dict()
    out["manifest"] = manifest
    out["content_available"] = manifest is not None
    return out
