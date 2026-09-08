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

#: round-1 review PERF MAJOR-2：单条 IN 谓词的绑定参数上限（PG 硬上限
#: 32767，保守取 10k 分片；超长 IN 列表循环拼接，结果字典归并）。
SQL_IN_CHUNK_SIZE = 10_000


def chunked(seq, size: int = 0):
    """把序列切成 ≤size 片（GC 引用计数等长 IN 列表的有界分片辅助）。

    ``size`` 缺省读模块级 ``SQL_IN_CHUNK_SIZE``（调用时求值 —— 测试可
    monkeypatch 调小以验证分片归并路径）。
    """
    step = max(1, int(size or SQL_IN_CHUNK_SIZE))
    items = list(seq)
    for i in range(0, len(items), step):
        yield items[i:i + step]

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
    from sqlalchemy.exc import IntegrityError

    # 有界重试（≤2 次尝试）：并发不同内容修订读到同一 head → 同一
    # revision_no 撞 0030 唯一约束 (artifact_id, revision_no) → 重读 head
    # 重试一次。重试路径与并发同内容冲突路径共用同一 savepoint 纪律。
    last_error: Optional[BaseException] = None
    for attempt in (1, 2):
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
            # savepoint：并发晋升撞唯一约束（内容身份或 revision_no）时只
            # 回滚本 INSERT —— 复用对方落地的行，绝不污染调用方事务。
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError as e:
            # savepoint 回滚已把 row **分离**出会话 —— 此处绝不能 expunge
            # （对已分离对象 expunge 抛 InvalidRequestError 并中断晋升；
            # round-1 review MAJOR）。以新鲜 DB 状态复诊冲突属于哪条约束。
            winner = db.execute(
                select(ArtifactRevision).where(
                    ArtifactRevision.artifact_id == artifact_id,
                    ArtifactRevision.content_sha256 == content_sha256,
                )
            ).scalar_one_or_none()
            if winner is not None:
                return winner, False  # 内容身份幂等：复用对方行，不 bump
            last_error = e
            continue  # (artifact_id, revision_no) 撞号：重读 head 重试一次
        return row, True
    logger.warning(
        "[artifact_revisions] concurrent insert conflict for %s/%s: %s",
        artifact_id, content_sha256[:12], last_error,
    )
    raise last_error  # type: ignore[misc] — 未知完整性问题按原样上抛


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
    """GC 引用计数：content_location → 引用它的修订行数。

    round-1 review PERF MAJOR-2：IN 列表按 ``SQL_IN_CHUNK_SIZE`` 分片循环
    （promotion GC 快照会把整个 blob store 的 location 都传进来），结果
    归并进同一字典。命中 ``idx_artifact_revision_content_location``。
    """
    from sqlalchemy import func, select

    from app.models.project import ArtifactRevision

    out: Dict[str, int] = {}
    for chunk in chunked([str(loc) for loc in (locations or []) if loc]):
        rows = db.execute(
            select(ArtifactRevision.content_location, func.count(ArtifactRevision.id))
            .where(ArtifactRevision.content_location.in_(chunk))
            .group_by(ArtifactRevision.content_location)
        ).all()
        for loc, cnt in rows:
            key = str(loc)
            out[key] = out.get(key, 0) + int(cnt)
    return out


def referencing_sha_counts(db, shas: Sequence[str]) -> Dict[str, int]:
    """GC 引用计数（sha 口径）：content_sha256 → 引用它的修订行数（IN 分片）。

    quota 保留 plan/execute 与 promotion GC 快照原先各自内联同一条
    GROUP BY 查询（其中两处未分片）—— round-1 PERF MAJOR-2 收敛到这里，
    命中 ``idx_artifact_revision_sha``。
    """
    from sqlalchemy import func, select

    from app.models.project import ArtifactRevision

    out: Dict[str, int] = {}
    for chunk in chunked([str(s) for s in (shas or []) if s]):
        rows = db.execute(
            select(ArtifactRevision.content_sha256, func.count(ArtifactRevision.id))
            .where(ArtifactRevision.content_sha256.in_(chunk))
            .group_by(ArtifactRevision.content_sha256)
        ).all()
        for sha, cnt in rows:
            key = str(sha)
            out[key] = out.get(key, 0) + int(cnt)
    return out


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
    """全部 Artifact.metadata_json.content_location（GC 的 head 指针保护输入）。

    round-1 review PERF MAJOR-2（选定方案：SQL JSON 提取 + 有界流式拉取）：
    不再把整表 ``metadata_json`` 物化到 Python 后逐行 ``isinstance/get``
    解析 —— 改用 SQLAlchemy 通用 JSON 索引访问 ``.as_string()``，方言侧
    编译为 SQLite ``json_extract`` / PG ``->>``（无需原生 text SQL，DRY
    且跨方言），SQL 侧只取一列字符串并过滤 NULL；``yield_per`` 分批拉取，
    事件循环/工作线程内存 ≤ chunk 大小，绝不整表物化。旧实现的逐值语义
    保留：falsy（空串等）剔除、``str()`` 归一，返回 List（调用方一律
    ``set()`` 消费，重复无害）。
    """
    from sqlalchemy import select

    from app.models.project import Artifact

    loc_expr = Artifact.metadata_json["content_location"].as_string()
    result = db.execute(
        select(loc_expr)
        .where(loc_expr.isnot(None))
        .execution_options(yield_per=500)
    )
    return [str(loc) for (loc,) in result if loc]


def project_quota_usage(db, project_id: str) -> Dict[str, Any]:
    """Per-project durable-artifact accounting（Wave 12 quota 的唯一口径）。

    全部按 ``Artifact.project_id`` 归属。字节口径：

    - ``bytes``：**物理去重字节** —— 项目内修订行按 distinct
      content_sha256 计 max(byte_size)（CAS 内容寻址下同内容一份字节；
      指针克隆/去重命中零新增），外加「只有 metadata head 指针、无修订
      行」的旧晋升行按 ``metadata_json.content_summary.payload_bytes``
      兜底（诚实近似，绝不虚构）；
    - ``artifact_count``：Artifact 行数；
    - ``revision_bytes``：修订账本总字节（append-only 口径，不去重 ——
      历史修订各记一次）；
    - ``revision_bytes_by_artifact`` / ``max_per_artifact_revision_bytes``：
      per-artifact 修订字节（``max_revision_bytes_per_artifact`` 限额输入）。

    只读；四次有界查询（与 list_projects 同量级）。

    round-1 review PERF MAJOR-1：修订口径（distinct-sha / per-artifact /
    账本总字节）全部下推为 SQL 聚合（JOIN + GROUP BY + SUM/MAX）—— 此前
    是把项目全部修订行拉到 Python 循环累加的 O(N) 行物化。逐字节口径
    不变：distinct sha 取 max(byte_size) 求和（CAS 内容寻址），账本不去
    重，per-artifact SUM。legacy head 指针兜底保持原判定
    （``isinstance(payload_bytes, int) and > 0``）不变，但改为 ``yield_per``
    流式读取（内存 ≤ chunk；该集合 = 项目内无修订行的旧行，有界）。
    """
    from sqlalchemy import func, select

    from app.models.project import Artifact, ArtifactRevision

    pid = str(project_id or "")
    if not pid:
        return {
            "bytes": 0, "artifact_count": 0, "revision_bytes": 0,
            "revision_bytes_by_artifact": {}, "max_per_artifact_revision_bytes": 0,
        }

    # distinct-sha 物理字节：GROUP BY sha 取 MAX(byte_size) 后求和（SQL 侧）
    distinct_rows = db.execute(
        select(
            ArtifactRevision.content_sha256,
            func.max(ArtifactRevision.byte_size),
        )
        .join(Artifact, Artifact.id == ArtifactRevision.artifact_id)
        .where(Artifact.project_id == pid)
        .group_by(ArtifactRevision.content_sha256)
    ).all()
    bytes_dedup = sum(int(mx or 0) for sha, mx in distinct_rows if sha)

    # per-artifact 账本字节（append-only 口径，不去重；SQL 侧 SUM）
    per_artifact: Dict[str, int] = {
        str(aid): int(total or 0)
        for aid, total in db.execute(
            select(
                ArtifactRevision.artifact_id,
                func.sum(ArtifactRevision.byte_size),
            )
            .join(Artifact, Artifact.id == ArtifactRevision.artifact_id)
            .where(Artifact.project_id == pid)
            .group_by(ArtifactRevision.artifact_id)
        ).all()
    }
    revision_bytes = sum(per_artifact.values())

    artifact_count = int(
        db.execute(
            select(func.count()).select_from(Artifact).where(
                Artifact.project_id == pid)
        ).scalar_one() or 0
    )

    # 兜底：无修订行但有 metadata head 指针的旧行 → content_summary 兜底字节
    # （判定逐条保留原语义；只流式读取，不整列物化）
    fallback_bytes = 0
    if artifact_count > len(per_artifact):
        meta_rows = db.execute(
            select(Artifact.metadata_json).where(
                Artifact.project_id == pid,
                ~Artifact.id.in_(
                    select(ArtifactRevision.artifact_id)
                    .join(Artifact, Artifact.id == ArtifactRevision.artifact_id)
                    .where(Artifact.project_id == pid)
                ),
            ).execution_options(yield_per=200)
        )
        for (meta,) in meta_rows:
            if not isinstance(meta, dict) or meta.get("content_location") is None:
                continue
            summary = meta.get("content_summary")
            payload_bytes = (
                summary.get("payload_bytes")
                if isinstance(summary, dict) else None
            )
            if isinstance(payload_bytes, int) and payload_bytes > 0:
                fallback_bytes += payload_bytes

    return {
        "bytes": bytes_dedup + fallback_bytes,
        "artifact_count": artifact_count,
        "revision_bytes": revision_bytes,
        "revision_bytes_by_artifact": per_artifact,
        "max_per_artifact_revision_bytes": max(per_artifact.values(), default=0),
    }


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
