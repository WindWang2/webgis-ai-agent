"""Artifact Revision DAO + pin/clone services (Wave 1 durable artifact store).

``artifact_revisions`` 是晋升内容的 append-only 版本账本；本模块是它的
**唯一写入口 DAO**（promotion 写入修订，sweeper 读引用计数，pin/clone
做行级治理）。会话内账本仍在 session store（不迁移）；内容持久真相唯一
归 BlobStore（单一持久内容后端，无第二 store）。

幂等契约：同 (artifact_id, content_sha256) 只有一行 —— 重晋升同内容复用
既有修订（不 bump revision_no，不改 created_at）；新内容 = head+1。
写入纪律与 LineageService 一致：DAO 只 flush，commit 由调用方控制。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: revision.metadata 有界约定（brief：bounded JSON ≤16 keys）
MAX_REVISION_METADATA_KEYS = 16

#: content_type 有界集合
CONTENT_TYPE_JSON = "json"
CONTENT_TYPE_BINARY = "binary"


def _bounded_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not metadata:
        return None
    return {
        str(k): v for k, v in list(metadata.items())[:MAX_REVISION_METADATA_KEYS]
    }


# ── DAO ──────────────────────────────────────────────────────────────────


def record_revision(
    db,
    *,
    artifact_id: str,
    content_sha256: str,
    content_location: str,
    content_type: str = CONTENT_TYPE_JSON,
    byte_size: int = 0,
    workflow_run_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, bool]:
    """记录一条内容修订（幂等）：同 (artifact_id, content_sha256) 复用既有行。

    Returns ``(revision_row, created)``。只 flush 不 commit（调用方控制
    事务边界，与 LineageService.record_lineage 同纪律）。
    """
    from sqlalchemy import select

    from app.models.project import ArtifactRevision

    if not artifact_id or not content_sha256 or not content_location:
        return None, False
    if content_type not in (CONTENT_TYPE_JSON, CONTENT_TYPE_BINARY):
        content_type = CONTENT_TYPE_JSON  # 有界集合外的值按 json 降级（诚实缺省）
    existing = db.execute(
        select(ArtifactRevision).where(
            ArtifactRevision.artifact_id == artifact_id,
            ArtifactRevision.content_sha256 == content_sha256,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False  # 重晋升同内容：复用，不 bump
    head = head_revision(db, artifact_id)
    revision_no = (head.revision_no if head is not None else 0) + 1
    row = ArtifactRevision(
        id=str(uuid.uuid4()),
        artifact_id=artifact_id,
        revision_no=revision_no,
        content_sha256=content_sha256,
        content_location=content_location,
        content_type=content_type,
        byte_size=int(byte_size or 0),
        workflow_run_id=workflow_run_id,
        pinned_at=None,
        revision_metadata=_bounded_metadata(metadata),
    )
    try:
        # savepoint：并发同内容晋升撞 (artifact_id, content_sha256) 唯一约束
        # 时只回滚本 INSERT —— 复用对方落地的行，绝不污染调用方事务。
        from sqlalchemy.exc import IntegrityError

        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            db.expunge(row)
            winner = db.execute(
                select(ArtifactRevision).where(
                    ArtifactRevision.artifact_id == artifact_id,
                    ArtifactRevision.content_sha256 == content_sha256,
                )
            ).scalar_one_or_none()
            if winner is not None:
                return winner, False
            raise
    except IntegrityError as e:  # noqa: BLE001 — 未知完整性问题按原样上抛
        logger.warning(
            "[artifact_revisions] concurrent insert conflict for %s/%s: %s",
            artifact_id, content_sha256[:12], e,
        )
        raise
    return row, True


def head_revision(db, artifact_id: str) -> Optional[Any]:
    """某 artifact 当前 head 修订（revision_no 最大）。"""
    from sqlalchemy import select

    from app.models.project import ArtifactRevision

    if not artifact_id:
        return None
    return db.execute(
        select(ArtifactRevision)
        .where(ArtifactRevision.artifact_id == artifact_id)
        .order_by(ArtifactRevision.revision_no.desc())
        .limit(1)
    ).scalar_one_or_none()


def list_revisions(db, artifact_id: str, limit: int = 20) -> List[Any]:
    """某 artifact 的修订历史（新→旧，有界）。"""
    from sqlalchemy import select

    from app.models.project import ArtifactRevision

    if not artifact_id:
        return []
    return list(
        db.execute(
            select(ArtifactRevision)
            .where(ArtifactRevision.artifact_id == artifact_id)
            .order_by(ArtifactRevision.revision_no.desc())
            .limit(max(1, min(int(limit or 20), 200)))
        ).scalars().all()
    )


def referencing_counts(db, locations: Sequence[str]) -> Dict[str, int]:
    """GC 引用计数：content_location → 引用它的修订行数（单条索引查询）。"""
    from sqlalchemy import func, select

    from app.models.project import ArtifactRevision

    locs = [str(loc) for loc in (locations or []) if loc]
    if not locs:
        return {}
    rows = db.execute(
        select(ArtifactRevision.content_location, func.count(ArtifactRevision.id))
        .where(ArtifactRevision.content_location.in_(locs))
        .group_by(ArtifactRevision.content_location)
    ).all()
    return {loc: int(cnt) for loc, cnt in rows}


def pinned_content_sha256s(db) -> List[str]:
    """被 pin 的修订内容摘要集合（GC 的 pinned 保护输入）。"""
    from sqlalchemy import select

    from app.models.project import ArtifactRevision

    return list(
        db.execute(
            select(ArtifactRevision.content_sha256).where(
                ArtifactRevision.pinned_at.isnot(None)
            )
        ).scalars().all()
    )


def artifact_content_locations(db) -> List[str]:
    """全部 Artifact.metadata_json.content_location（GC 的 head 指针保护输入）。"""
    from sqlalchemy import select

    from app.models.project import Artifact

    rows = db.execute(select(Artifact.metadata_json)).scalars().all()
    out: List[str] = []
    for meta in rows:
        if isinstance(meta, dict):
            loc = meta.get("content_location")
            if loc:
                out.append(str(loc))
    return out


# ── Pin / Clone（tenant-checked 服务；路由侧经 ProjectService 鉴权）──────


def _authorized_artifact(db, project_id: str, artifact_id: str):
    from sqlalchemy import select

    from app.models.project import Artifact

    return db.execute(
        select(Artifact).where(
            Artifact.id == artifact_id, Artifact.project_id == project_id
        )
    ).scalar_one_or_none()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def pin_artifact(
    db,
    *,
    project_id: str,
    artifact_id: str,
    pinned: bool,
    user_id: Optional[str] = None,
    org_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Pin/unpin 某 artifact 的 head 修订（tenant-checked）。

    pinned=True → head.pinned_at = UTC now（该修订引用的 blob 从此绝不参与
    GC，无论引用计数）；pinned=False → 清空。鉴权走既有
    ``ProjectService.get_project_with_auth``（IDOR 防护同项目路由）。
    Returns result dict or None（项目/artifact/head 缺失或无权）。
    """
    from app.services.project_service import ProjectService

    project = ProjectService.get_project_with_auth(
        db=db, project_id=project_id, user_id=user_id, org_id=org_id
    )
    if not project:
        return None
    artifact = _authorized_artifact(db, project_id, artifact_id)
    if artifact is None:
        return None
    head = head_revision(db, artifact_id)
    if head is None:
        return None  # 无已物化内容：诚实拒绝（无可 pin 的持久修订）
    head.pinned_at = _utcnow() if pinned else None
    db.commit()
    return {
        "artifact_id": artifact_id,
        "revision_no": head.revision_no,
        "content_sha256": head.content_sha256,
        "pinned": head.pinned,
        "pinned_at": head.pinned_at.isoformat() if head.pinned_at else None,
    }


def clone_artifact(
    db,
    *,
    project_id: str,
    artifact_id: str,
    user_id: Optional[str] = None,
    org_id: Optional[int] = None,
) -> Optional[Any]:
    """Clone-as-pointer：新 Artifact 行指向**同一** content_location（零拷贝）。

    新行复用原 artifact 的语义元数据 + head 指针（content_status/
    content_location/content_payload_sha256），并为其 head 位置写一条**新
    修订行**（同 content_sha256 —— BlobStore 内容寻址下天然零字节复制）。
    无已物化内容的 artifact 无处可指 → 返回 None（诚实拒绝，绝不
    凭空伪造内容指针）。
    """
    from app.models.project import Artifact
    from app.services.project_service import ProjectService

    project = ProjectService.get_project_with_auth(
        db=db, project_id=project_id, user_id=user_id, org_id=org_id
    )
    if not project:
        return None
    source = _authorized_artifact(db, project_id, artifact_id)
    if source is None:
        return None
    head = head_revision(db, artifact_id)
    if head is not None:
        content_sha256 = head.content_sha256
        content_location = head.content_location
        content_type = head.content_type
        byte_size = head.byte_size
    else:
        # 回退：旧晋升行只有 metadata head 指针（无修订行）。
        meta = source.metadata_json if isinstance(source.metadata_json, dict) else {}
        content_sha256 = str(meta.get("content_payload_sha256") or "")
        content_location = str(meta.get("content_location") or "")
        content_type = CONTENT_TYPE_JSON
        byte_size = 0
    if not content_sha256 or not content_location:
        return None  # 无持久内容可指：诚实拒绝
    src_meta = dict(source.metadata_json or {})
    new_meta = dict(src_meta)
    new_meta["content_status"] = "promoted"
    new_meta["content_location"] = content_location
    new_meta["content_payload_sha256"] = content_sha256
    new_meta["cloned_from"] = artifact_id  # 指针克隆的证据（有界 +2 键）
    new_meta["clone_kind"] = "pointer"
    clone_row = Artifact(
        id=f"art_{uuid.uuid4().hex[:16]}",
        project_id=project_id,
        name=f"{source.name} (clone)",
        artifact_type=source.artifact_type,
        format=source.format,
        crs=source.crs,
        storage_ref=source.storage_ref,
        metadata_json=new_meta,
        content_fingerprint=source.content_fingerprint,
    )
    db.add(clone_row)
    db.flush()
    record_revision(
        db,
        artifact_id=clone_row.id,
        content_sha256=content_sha256,
        content_location=content_location,
        content_type=content_type,
        byte_size=byte_size,
        workflow_run_id=None,
        metadata={"cloned_from": artifact_id},
    )
    db.commit()
    return clone_row
