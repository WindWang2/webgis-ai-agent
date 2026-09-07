"""Per-project quotas, retention age policy, and the shared protection surface
(Wave 12 — audit 01 §7.8 / 02 §6.8; migration plan W12).

Three concerns, one module, zero new truth:

- **Quota**（per-project byte/count caps on durable artifacts）: accounting
  sums ``Artifact``/``ArtifactRevision`` rows scoped by
  ``Artifact.project_id`` (byte_size on revisions; distinct content-sha
  accounting so CAS dedup / pointer clones never double-charge; legacy
  metadata head pointers fall back to ``content_summary.payload_bytes``).
  Enforcement lives at the promotion choke point (before the BlobStore
  put): over-quota → honest ``content_status="quota_exceeded"`` + bounded
  typed ``ProjectQuotaExceededError`` details in metadata; the artifact row
  survives (metadata-only), no payload bytes are written. Default
  deployments (no env) are unlimited — behavior exactly as before W12.

- **Retention**（age policy for unpinned revisions）: plan/execute pair
  following the SAME discipline as W1's promotion GC — the planner is a
  read-only dry-run, the executor re-verifies every candidate with the
  SAME predicate under fresh DB state. Default (no env) keeps revisions
  forever. Blob-level deletion reuses ``_promotion_blob_protection`` from
  ``artifact_lifecycle`` — the single protection predicate — and never
  forks a second one.

- **Single protection path invariant** (cross-cutting invariant 4, migration
  plan W12): every destructive path (session GC planner/executor, promotion
  store GC plan/execute, retention cleanup plan/execute, snapshot delete)
  must protect pins, workspace/persistent tiers, and lineage roots. The
  tuple constants historically live per module
  (``gc._PROTECTED_TIERS``, ``artifact_registry._GC_PROTECTED_TIERS``,
  ``vocabulary`` tiers); parity is pinned by
  ``tests/data/test_quota_retention_v5.py`` (planner == executor, same
  protected set across all mechanisms). This module re-exports the blob
  predicate so retention and future callers consume ONE definition.

Orphan hygiene also lives here: orphan ``artifact_revisions`` rows whose
artifact row is gone (FK-less drift) get a bounded plan/execute sweep, and a
read-only helper discloses workspace snapshot manifest pointers that point at
deleted blobs (integration point: ``describe_workspace`` integrity block,
owned by the workspace module).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Single protection surface ─────────────────────────────────────────────
# 持久层保护元组的事实定义历史上分散在 gc.py / artifact_registry.py / 本模块
# 的消费方 —— 值必须完全一致；parity 测试把四个常量钉死为相等。
PROTECTED_PERSISTENCE_TIERS = ("workspace", "persistent")


def promotion_blob_protection(*args: Any, **kwargs: Any) -> Optional[str]:
    """Single blob-level protection predicate (re-export, W1 discipline).

    Delegates to ``artifact_lifecycle._promotion_blob_protection`` — 引用计数
    （revision location/sha）+ Artifact head 指针 + pin + 宽限期的**唯一**
    blob 保护判定；plan 与 execute 双侧共用。绝不在此模块 fork 第二份。
    """
    from app.services.artifact_lifecycle import _promotion_blob_protection

    return _promotion_blob_protection(*args, **kwargs)


# ── Env discipline（与 artifact_lifecycle._retention_days 同款）───────────


def _env_float(env_name: str, default: float) -> float:
    raw = os.environ.get(env_name, "")
    try:
        val = float(raw)
        return val if val > 0 else default
    except (TypeError, ValueError):
        return default


def _env_int(env_name: str, default: int) -> int:
    raw = os.environ.get(env_name, "")
    try:
        val = int(float(raw))
        return val if val > 0 else default
    except (TypeError, ValueError):
        return default


# ── Quota policy / decision ───────────────────────────────────────────────

#: 0 / unset = unlimited（默认部署行为与 W12 之前完全一致）
_ENV_MAX_BYTES = "WEBGIS_PROJECT_ARTIFACT_MAX_BYTES"
_ENV_MAX_COUNT = "WEBGIS_PROJECT_ARTIFACT_MAX_COUNT"
_ENV_MAX_REVISION_BYTES = "WEBGIS_PROJECT_ARTIFACT_MAX_REVISION_BYTES"


class ProjectQuotaPolicy(BaseModel):
    """Per-project durable-artifact quota policy (env-driven; 0 = unlimited)."""

    max_bytes: int = 0
    max_artifact_count: int = 0
    max_revision_bytes_per_artifact: int = 0

    @classmethod
    def from_env(cls) -> "ProjectQuotaPolicy":
        return cls(
            max_bytes=_env_int(_ENV_MAX_BYTES, 0),
            max_artifact_count=_env_int(_ENV_MAX_COUNT, 0),
            max_revision_bytes_per_artifact=_env_int(_ENV_MAX_REVISION_BYTES, 0),
        )

    @property
    def unlimited(self) -> bool:
        return not (self.max_bytes or self.max_artifact_count
                    or self.max_revision_bytes_per_artifact)


class QuotaUsage(BaseModel):
    bytes: int = 0
    artifact_count: int = 0
    revision_bytes: int = 0


class ProjectQuotaLimits(BaseModel):
    max_bytes: int = 0
    max_artifact_count: int = 0
    max_revision_bytes_per_artifact: int = 0


class QuotaDecision(BaseModel):
    """check_quota 结果：allowed + 使用量 + 限额 +（超限时）有界 details。"""

    allowed: bool = True
    usage: QuotaUsage = QuotaUsage()
    limits: ProjectQuotaLimits = ProjectQuotaLimits()
    reason: str = ""
    details: Dict[str, Any] = {}

    def to_exception(self) -> "ProjectQuotaExceededError":
        return ProjectQuotaExceededError(
            reason=self.reason,
            project_id=str(self.details.get("project_id") or ""),
            limit_bytes=int(self.details.get("limit_bytes") or 0),
            usage_bytes=int(self.details.get("usage_bytes") or 0),
            incoming_bytes=int(self.details.get("incoming_bytes") or 0),
        )


class ProjectQuotaExceededError(Exception):
    """Typed over-quota signal（details 有界；promotion 把它们如实写进
    ``metadata_json.quota`` —— 行存活（metadata-only），字节不落盘）。"""

    def __init__(
        self,
        *,
        reason: str,
        project_id: str = "",
        limit_bytes: int = 0,
        usage_bytes: int = 0,
        incoming_bytes: int = 0,
    ) -> None:
        super().__init__(reason)
        self.reason = str(reason)[:96]
        self.project_id = str(project_id)[:64]
        self.limit_bytes = int(limit_bytes)
        self.usage_bytes = int(usage_bytes)
        self.incoming_bytes = int(incoming_bytes)

    def to_metadata(self) -> Dict[str, Any]:
        """Bounded metadata projection（≤8 键；值全部短整型/短串）。"""
        return {
            "reason": self.reason,
            "project_id": self.project_id,
            "limit_bytes": self.limit_bytes,
            "usage_bytes": self.usage_bytes,
            "incoming_bytes": self.incoming_bytes,
        }


def check_quota(
    db,
    project_id: str,
    incoming_bytes: int = 0,
    *,
    artifact_id: Optional[str] = None,
    incoming_artifacts: int = 0,
    policy: Optional[ProjectQuotaPolicy] = None,
) -> QuotaDecision:
    """Quota 判定（只读，绝不写库）：项目级 byte/count/per-artifact 检查。

    Accounting is project-scoped via ``Artifact.project_id``. CAS 去重命中
    （同 content 已在 BlobStore）时调用方应传 ``incoming_bytes=0`` ——
    去重/指针克隆不产生新字节，绝不收费。
    """
    from app.services.artifact_revisions import project_quota_usage

    policy = policy or ProjectQuotaPolicy.from_env()
    usage_dict = project_quota_usage(db, project_id)
    usage = QuotaUsage(
        bytes=int(usage_dict.get("bytes", 0)),
        artifact_count=int(usage_dict.get("artifact_count", 0)),
        revision_bytes=int(usage_dict.get("revision_bytes", 0)),
    )
    limits = ProjectQuotaLimits(
        max_bytes=policy.max_bytes,
        max_artifact_count=policy.max_artifact_count,
        max_revision_bytes_per_artifact=policy.max_revision_bytes_per_artifact,
    )
    decision = QuotaDecision(usage=usage, limits=limits)
    if policy.unlimited:
        return decision

    incoming = max(0, int(incoming_bytes or 0))
    if policy.max_bytes and usage.bytes + incoming > policy.max_bytes:
        decision.allowed = False
        decision.reason = "project artifact byte quota exceeded"
        decision.details = ProjectQuotaExceededError(
            reason=decision.reason,
            project_id=project_id,
            limit_bytes=policy.max_bytes,
            usage_bytes=usage.bytes,
            incoming_bytes=incoming,
        ).to_metadata()
        return decision
    if policy.max_artifact_count and (
        usage.artifact_count + max(0, int(incoming_artifacts or 0))
        > policy.max_artifact_count
    ):
        decision.allowed = False
        decision.reason = "project artifact count quota exceeded"
        decision.details = {
            "reason": decision.reason,
            "project_id": str(project_id)[:64],
            "limit_artifacts": policy.max_artifact_count,
            "artifact_count": usage.artifact_count,
        }
        return decision
    if policy.max_revision_bytes_per_artifact:
        if artifact_id:
            per_art = int(usage_dict.get("revision_bytes_by_artifact", {}).get(
                str(artifact_id), 0))
            projected = per_art + incoming
        else:
            per_art = int(usage_dict.get("max_per_artifact_revision_bytes", 0))
            projected = per_art
        if projected > policy.max_revision_bytes_per_artifact:
            decision.allowed = False
            decision.reason = "per-artifact revision byte quota exceeded"
            decision.details = {
                "reason": decision.reason,
                "project_id": str(project_id)[:64],
                "artifact_id": str(artifact_id or "")[:64],
                "limit_bytes": policy.max_revision_bytes_per_artifact,
                "usage_bytes": per_art,
                "incoming_bytes": incoming,
            }
            return decision
    return decision


# ── Retention age policy ──────────────────────────────────────────────────

#: 0 / unset = keep forever（默认部署不做任何保留删除）
_ENV_RETENTION_MAX_AGE_DAYS = "WEBGIS_RETENTION_MAX_AGE_DAYS"
#: 宽限期复用 PROMOTION_STORE_GC_GRACE_HOURS 的纪律（同款保守默认 168h）
_ENV_RETENTION_GRACE_HOURS = "WEBGIS_RETENTION_GRACE_HOURS"


def _default_retention_grace_hours() -> float:
    from app.services.artifact_lifecycle import _DEFAULT_PROMOTION_GC_GRACE_HOURS

    return _env_float(
        _ENV_RETENTION_GRACE_HOURS, _DEFAULT_PROMOTION_GC_GRACE_HOURS
    )


class RetentionPolicy(BaseModel):
    """Unpinned-revision age policy（0 天 = 永久保留）。"""

    max_age_days: float = 0.0
    grace_hours: float = 0.0

    @classmethod
    def from_env(cls) -> "RetentionPolicy":
        return cls(
            max_age_days=_env_float(_ENV_RETENTION_MAX_AGE_DAYS, 0.0),
            grace_hours=_default_retention_grace_hours(),
        )


def _retention_revision_protection(
    db,
    revision,
    artifact,  # Optional[Artifact] — 孤儿修订（artifact 行已消失）无层/血缘保护
    *,
    head_ids: set,
    lineage_parent_ids: set,
) -> Optional[str]:
    """Revision 级保护判定（plan 与 execute **双侧共用同一函数**）。

    - pinned_at NOT NULL → 永不（用户显式 pin）；
    - head 修订（该 artifact 的当前内容）→ 永不；
    - artifact 带 workspace/persistent 层标记 → 该 artifact 的修订永不；
    - 血缘根保留：仍有下游（其它 artifact 以它为 parent）→ 永不。
    返回保护原因；None = 可删。
    """
    if revision.pinned_at is not None:
        return "pinned"
    if revision.id in head_ids:
        return "head revision (current content)"
    meta = (artifact.metadata_json if artifact is not None else None) or {}
    meta = meta if isinstance(meta, dict) else {}
    tier = str(meta.get("persistence_tier") or "")
    if tier in PROTECTED_PERSISTENCE_TIERS:
        return f"persistence_tier={tier}"
    if revision.artifact_id in lineage_parent_ids:
        return "lineage root (has downstream)"
    return None


def _retention_scan_state(db) -> Tuple[set, set]:
    """head 修订 id 集合 + 有下游的 artifact id 集合（单查询口径）。"""
    from sqlalchemy import func, select

    from app.models.project import ArtifactLineage, ArtifactRevision

    maxima = {
        aid: int(no)
        for aid, no in db.execute(
            select(
                ArtifactRevision.artifact_id,
                func.max(ArtifactRevision.revision_no),
            ).group_by(ArtifactRevision.artifact_id)
        ).all()
    }
    head_ids: set = set()
    if maxima:
        for rid, aid, no in db.execute(
            select(ArtifactRevision.id, ArtifactRevision.artifact_id,
                   ArtifactRevision.revision_no).where(
                ArtifactRevision.artifact_id.in_(list(maxima.keys())))
        ).all():
            if maxima.get(aid) == int(no):
                head_ids.add(rid)
    lineage_parent_ids = set(
        db.execute(
            select(ArtifactLineage.parent_artifact_id).where(
                ArtifactLineage.parent_artifact_id.isnot(None)
            )
        ).scalars().all()
    )
    return head_ids, lineage_parent_ids


def plan_retention_cleanup(
    db,
    project_id: Optional[str] = None,
    *,
    policy: Optional[RetentionPolicy] = None,
    now: Optional[float] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    """Dry-run 保留清扫计划（只读，绝不删任何行/字节）。

    候选修订 = unpinned AND age > max_age_days（head/pin/workspace 层/
    血缘根保护见 ``_retention_revision_protection``，双侧同函数）。
    候选 blob = 候选修订删除后**零存活引用**且通过 W1 单一 blob 保护
    谓词（``promotion_blob_protection``：引用计数 / head 指针 / pin /
    blob mtime 宽限期）的物理 blob。默认策略（无 env）= keep-forever，
    返回空计划（disabled）。
    """
    from sqlalchemy import select

    from app.models.project import Artifact, ArtifactRevision
    from app.services.artifact_revisions import (
        artifact_content_locations,
        pinned_content_sha256s,
        referencing_counts,
    )
    from app.services.durable_blob_store import get_filesystem_blob_store

    policy = policy or RetentionPolicy.from_env()
    now = float(now if now is not None else time.time())
    plan: Dict[str, Any] = {
        "policy": policy.model_dump(),
        "now": now,
        "project_id": project_id,
        "disabled": policy.max_age_days <= 0,
        "candidate_revisions": [],
        "candidate_blobs": [],
        "protected_counts": {},
        "candidate_revision_count": 0,
        "candidate_blob_count": 0,
        "candidate_blob_bytes": 0,
    }
    if policy.max_age_days <= 0:
        return plan

    cutoff = datetime.now(timezone.utc) - timedelta(days=policy.max_age_days)
    # 注意：pinned 不在 SQL 层预过滤 —— 保护判定交给
    # ``_retention_revision_protection``（双侧同函数），pin 保护在计划里
    # 如实披露（protected_counts），绝不静默吞掉。
    stmt = (
        select(ArtifactRevision, Artifact)
        .join(Artifact, Artifact.id == ArtifactRevision.artifact_id)
        .where(ArtifactRevision.created_at < cutoff)
    )
    if project_id:
        stmt = stmt.where(Artifact.project_id == project_id)
    rows = db.execute(stmt).all()

    head_ids, lineage_parent_ids = _retention_scan_state(db)
    protected: Dict[str, int] = {}
    candidates = []
    for revision, artifact in rows:
        reason = _retention_revision_protection(
            db, revision, artifact,
            head_ids=head_ids, lineage_parent_ids=lineage_parent_ids,
        )
        if reason is not None:
            protected[reason] = protected.get(reason, 0) + 1
            continue
        candidates.append(revision)
    candidates.sort(key=lambda r: (r.created_at or datetime.now(timezone.utc),
                                   r.id))
    candidates = candidates[: max(1, int(limit or 200))]
    plan["protected_counts"] = dict(sorted(protected.items()))
    plan["candidate_revisions"] = [
        {
            "revision_id": r.id,
            "artifact_id": r.artifact_id,
            "revision_no": int(r.revision_no or 0),
            "content_sha256": r.content_sha256,
            "content_location": r.content_location,
            "byte_size": int(r.byte_size or 0),
        }
        for r in candidates
    ]
    plan["candidate_revision_count"] = len(plan["candidate_revisions"])

    # ── 候选 blob：删除候选修订后零存活引用，且通过单一 blob 保护谓词 ──
    if not candidates:
        return plan
    store = get_filesystem_blob_store()
    shas = list({r.content_sha256 for r in candidates})
    locations = list({r.content_location for r in candidates if r.content_location})
    # 存活引用 = 全部引用 − 候选修订自身（其余修订行 / head 指针 / pin 全算）
    all_by_loc = referencing_counts(db, locations)
    from sqlalchemy import func as _func

    sha_counts = {
        sha: int(cnt)
        for sha, cnt in db.execute(
            select(ArtifactRevision.content_sha256,
                   _func.count(ArtifactRevision.id))
            .where(ArtifactRevision.content_sha256.in_(shas))
            .group_by(ArtifactRevision.content_sha256)
        ).all()
    }
    candidate_loc = {r.content_location for r in candidates}
    candidate_sha = {r.content_sha256 for r in candidates}
    surviving_by_loc = {
        loc: cnt - (1 if loc in candidate_loc else 0)
        for loc, cnt in all_by_loc.items()
    }
    surviving_by_sha = {
        sha: cnt - (1 if sha in candidate_sha else 0)
        for sha, cnt in sha_counts.items()
    }
    snap = {
        "refcounts_by_location": surviving_by_loc,
        "refcounts_by_sha": surviving_by_sha,
        "artifact_locations": set(artifact_content_locations(db)),
        "pinned_shas": set(pinned_content_sha256s(db)),
    }
    seen_blobs: set = set()
    candidate_blobs: List[Dict[str, Any]] = []
    blob_protected: Dict[str, int] = {}
    for r in candidates:
        key = r.content_sha256
        loc = r.content_location
        if not key or not loc or key in seen_blobs:
            continue
        seen_blobs.add(key)
        path = store.root / loc
        reason = promotion_blob_protection(
            key, loc, store.blob_mtime(path),
            now=now, grace_hours=policy.grace_hours, **snap,
        )
        if reason is not None:
            blob_protected[reason] = blob_protected.get(reason, 0) + 1
            continue
        try:
            size = path.stat().st_size if path.is_file() else 0
        except OSError:
            size = 0
        candidate_blobs.append({"key": key, "location": loc, "bytes": size})
    plan["candidate_blobs"] = candidate_blobs
    plan["candidate_blob_count"] = len(candidate_blobs)
    plan["candidate_blob_bytes"] = sum(int(b["bytes"]) for b in candidate_blobs)
    plan["blob_protected_counts"] = dict(sorted(blob_protected.items()))
    return plan


def execute_retention_cleanup(
    plan: Dict[str, Any],
    *,
    db=None,
) -> Dict[str, Any]:
    """执行保留计划：每个候选以**新鲜 DB 状态 + 同一保护谓词**复检后删除。

    plan → execute 之间新落地的 pin / 修订 / 下游血缘即刻受保护（与 W1
    promotion GC 同纪律：同 DB 状态 ⇒ 同判定，绝不删除受保护对象）。
    """
    from sqlalchemy import select

    from app.models.project import Artifact, ArtifactRevision
    from app.services.durable_blob_store import get_filesystem_blob_store

    result: Dict[str, Any] = {
        "deleted_revisions": [],
        "skipped_protected": [],
        "deleted_blobs": [],
        "bytes_freed": 0,
        "failed": [],
    }
    candidates = list((plan or {}).get("candidate_revisions") or [])
    blob_candidates = list((plan or {}).get("candidate_blobs") or [])
    policy_d = dict((plan or {}).get("policy") or {})
    now = float((plan or {}).get("now") or time.time())
    grace_hours = float(policy_d.get("grace_hours") or 0.0)
    if not candidates and not blob_candidates:
        return result

    owned = db is None
    if db is None:
        from app.core.database import SessionLocal

        db = SessionLocal()
    try:
        head_ids, lineage_parent_ids = _retention_scan_state(db)
        deleted_rows: List[Any] = []
        for cand in candidates:
            rid = str(cand.get("revision_id") or "")
            row = db.execute(
                select(ArtifactRevision).where(ArtifactRevision.id == rid)
            ).scalar_one_or_none()
            if row is None:
                continue  # 已不存在：无事可做（非本执行删除）
            artifact = db.execute(
                select(Artifact).where(Artifact.id == row.artifact_id)
            ).scalar_one_or_none()
            reason = _retention_revision_protection(
                db, row, artifact,
                head_ids=head_ids, lineage_parent_ids=lineage_parent_ids,
            )
            if reason is not None:
                result["skipped_protected"].append(
                    {"revision_id": rid, "reason": reason})
                continue
            deleted_rows.append(row)
            db.delete(row)
            result["deleted_revisions"].append(rid)
        db.commit()

        # blob 阶段：删除后以新鲜引用计数 + 单一 blob 谓词复检
        if blob_candidates and deleted_rows:
            from app.services.artifact_revisions import (
                artifact_content_locations,
                pinned_content_sha256s,
                referencing_counts,
            )

            store = get_filesystem_blob_store()
            locations = [str(b.get("location") or "") for b in blob_candidates]
            snap = {
                "refcounts_by_location": referencing_counts(db, locations),
                "artifact_locations": set(artifact_content_locations(db)),
                "pinned_shas": set(pinned_content_sha256s(db)),
            }
            from sqlalchemy import func as _func

            shas = [str(b.get("key") or "") for b in blob_candidates]
            snap["refcounts_by_sha"] = {
                sha: int(cnt)
                for sha, cnt in db.execute(
                    select(ArtifactRevision.content_sha256,
                           _func.count(ArtifactRevision.id))
                    .where(ArtifactRevision.content_sha256.in_(shas))
                    .group_by(ArtifactRevision.content_sha256)
                ).all()
            }
            for b in blob_candidates:
                key = str(b.get("key") or "")
                loc = str(b.get("location") or "")
                if not key or not loc:
                    continue
                path = store.root / loc
                reason = promotion_blob_protection(
                    key, loc, store.blob_mtime(path),
                    now=now, grace_hours=grace_hours, **snap,
                )
                if reason is not None:
                    result["skipped_protected"].append(
                        {"key": key, "reason": reason})
                    continue
                try:
                    size = int(path.stat().st_size)
                except OSError:
                    size = 0
                if store.delete_blob(key):
                    result["deleted_blobs"].append(key)
                    result["bytes_freed"] += size
                else:
                    result["failed"].append(key)
    finally:
        if owned:
            db.close()
    return result


# ── Orphan artifact_revisions rows（FK-less drift protection）────────────

_ORPHAN_REVISION_LIMIT = 500


def plan_orphan_revision_cleanup(db, *, limit: int = _ORPHAN_REVISION_LIMIT) -> Dict[str, Any]:
    """孤儿修订行计划：artifact 行已消失的 ``artifact_revisions`` 漂移行。

    NOT EXISTS 反连接（sqlite/pg 可移植，同 uploads 孤儿口径）；只读。
    """
    from sqlalchemy import exists, select

    from app.models.project import Artifact, ArtifactRevision

    orphan_stmt = (
        select(ArtifactRevision.id, ArtifactRevision.artifact_id)
        .where(
            ~exists(
                select(Artifact.id).where(
                    Artifact.id == ArtifactRevision.artifact_id
                )
            )
        )
        .limit(max(1, int(limit or _ORPHAN_REVISION_LIMIT)))
    )
    rows = db.execute(orphan_stmt).all()
    return {
        "candidate_revision_ids": [str(rid) for rid, _aid in rows],
        "candidate_count": len(rows),
        "limit": int(limit or _ORPHAN_REVISION_LIMIT),
    }


def execute_orphan_revision_cleanup(plan: Dict[str, Any], *, db=None) -> Dict[str, Any]:
    """执行孤儿修订清理：新鲜状态复检（artifact 行仍缺失）后有界删除。"""
    from sqlalchemy import select

    from app.models.project import Artifact, ArtifactRevision

    result = {"deleted_revision_ids": [], "deleted_count": 0}
    candidate_ids = [str(r) for r in ((plan or {}).get("candidate_revision_ids") or [])]
    if not candidate_ids:
        return result
    owned = db is None
    if db is None:
        from app.core.database import SessionLocal

        db = SessionLocal()
    try:
        deleted = 0
        for rid in candidate_ids:
            row = db.execute(
                select(ArtifactRevision).where(ArtifactRevision.id == rid)
            ).scalar_one_or_none()
            if row is None:
                continue
            artifact_row = db.execute(
                select(Artifact.id).where(Artifact.id == row.artifact_id).limit(1)
            ).first()
            if artifact_row is not None:
                continue  # plan 后 artifact 行回来了 → 不再是孤儿
            db.delete(row)
            deleted += 1
            result["deleted_revision_ids"].append(rid)
        db.commit()
        result["deleted_count"] = deleted
    finally:
        if owned:
            db.close()
    return result


# ── Workspace snapshot manifest pointer integrity（read-only disclosure）──
#
# Integration point (NOT wired here — the workspace module owns
# describe_workspace): ``WorkspaceSnapshotService.describe_workspace``
# (app/services/workspace/snapshot.py) can merge
# ``snapshot_pointer_integrity(project_id)`` into its integrity disclosure so
# manifests whose durable_pointers point at deleted blobs are surfaced
# read-only. Kept in this module to avoid forking the manifest reader.


def snapshot_pointer_integrity(project_id: str, *, limit: int = 50) -> Dict[str, Any]:
    """披露项目域快照 manifest 中指向已删 blob 的持久指针（只读）。

    每项目 ≤50 份小 JSON（同 describe_workspace 的有界读取纪律）；blob
    存在性走 BlobStore（唯一持久内容后端）。
    """
    from app.services.durable_blob_store import get_filesystem_blob_store
    from app.services.workspace.snapshot import (
        _MAX_SNAPSHOT_LIST,
        _project_snapshots_dir,
        _read_json,
    )

    out: Dict[str, Any] = {
        "project_id": project_id,
        "snapshots_checked": 0,
        "pointers_missing_total": 0,
        "items": [],
    }
    if not project_id:
        return out
    pdir = _project_snapshots_dir(project_id)
    if pdir is None or not pdir.is_dir():
        return out
    store = get_filesystem_blob_store()
    checked = 0
    missing_total = 0
    items: List[Dict[str, Any]] = []
    for f in sorted(pdir.glob("*.json"))[:_MAX_SNAPSHOT_LIST]:
        data = _read_json(f)
        pointers = data.get("durable_pointers") if isinstance(data, dict) else None
        if not isinstance(pointers, dict):
            continue
        checked += 1
        missing: List[str] = []
        for artifact_id, pointer in list(pointers.items())[:128]:
            loc = ""
            if isinstance(pointer, dict):
                loc = str(pointer.get("content_location") or "")
            elif isinstance(pointer, str):
                loc = pointer
            if loc and not (store.root / loc).is_file():
                missing.append(str(artifact_id)[:96])
        if missing:
            missing_total += len(missing)
            items.append({
                "snapshot_id": str(data.get("snapshot_id") or f.stem)[:96],
                "missing_pointers": missing[:64],
                "missing_count": len(missing),
            })
        if len(items) >= max(1, int(limit or 50)):
            break
    out["snapshots_checked"] = checked
    out["pointers_missing_total"] = missing_total
    out["items"] = items
    return out
