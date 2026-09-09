"""Lakehouse GC — Spatial Lakehouse V7 (ADR-0119, Scope G).

**dereference-based 回收**（元数据级，绝无字节物化）：

- 可达性：protected roots → manifests → content blobs / virtual children；
- **blob 保护集 = 全部活 manifest 引用的并集**（R0-17：fork 链共享 blob
  —— 孤儿父 + 活子共享 99% blob ⇒ 子的 blob 全保护）；blob 可删 ⇔
  没有任何活 manifest 引用它；
- protected roots（可枚举、有界）：catalog active 行、artifact_revisions
  （内容历史引用）、running/pending workflow runs 的 run manifest 引用
  （R0-18）、宽限期内的所有 manifest（R0-19：GRACE ≥ registry TTL 耦合，
  默认 72h ≥ 48h TTL）；
- **plan/execute 分离**（R0-12）：plan token = 候选集 digest + publication
  watermark（扫描期最大 manifest mtime）—— execute 前逐 blob 重跑保护
  扫描（计划期新发布引用候选 blob → 重验拦截），watermark 漂移 → 整体
  拒绝（typed STALE）→ 重新规划（中断续跑 = 幂等重规划）；
- 证据确定性：候选/删除清单排序稳定；分批 cap 有界。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Mapping, Optional, Set

logger = logging.getLogger(__name__)

#: 扫描/批处理有界 cap。
MAX_SCAN_OBJECTS = 10_000
MAX_BATCH_DELETIONS = 5_000

#: 宽限默认（≥ artifact_registry TTL —— R0-19 耦合；env 可调只可更大）。
DEFAULT_GRACE_HOURS = 72.0


class GCError(ValueError):
    code = "LAKEHOUSE_GC_INVALID"


class GCStalePlan(GCError):
    """plan 与当前状态漂移（execute 拒绝执行 —— 重新规划）。"""

    code = "LAKEHOUSE_GC_PLAN_STALE"


def _as_epoch(value: Any) -> float:
    """last_modified 归一化（datetime/数值 → epoch 秒；None → 0.0）。"""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(value.timestamp())


def _object_store() -> Any:
    from app.services.s3_blob_store import get_object_store

    return get_object_store()


def _scan_manifests(*, now: float, grace_hours: float) -> Dict[str, Dict[str, Any]]:
    """BlobStore 枚举 → {manifest_id: {mtime, kind}}（元数据级；有界）。"""
    import json as _json

    store = _object_store()
    if not getattr(store, "supports_enumeration", False):
        raise GCError(
            "GC requires an enumerable object store; the selected backend "
            "does not support enumeration"
        )
    manifests: Dict[str, Dict[str, Any]] = {}
    for item in store.iter_objects(limit=MAX_SCAN_OBJECTS):
        rel = str(item["key"])
        if not rel.endswith(".json"):
            continue
        name = rel.rsplit("/", 1)[-1][: -len(".json")]
        if len(name) != 64 or not all(c in "0123456789abcdef" for c in name):
            continue
        manifests[name] = {
            # 消费侧同样归一化（S3 后端 yield epoch float，但防御性兼容
            # datetime —— 评审 R1-1）。
            "mtime": _as_epoch(item.get("last_modified")),
        }
        if len(manifests) >= MAX_SCAN_OBJECTS:
            break
    # 解析 kind（保护判定需要 virtual 递归；单次有界重读）。
    for mid in list(manifests):
        raw = store.get_blob(mid)
        kind = ""
        children: List[str] = []
        if raw is not None:
            try:
                parsed = _json.loads(raw.decode("utf-8"))
                kind = str(parsed.get("kind") or "")
                children = [
                    str(c) for c in
                    ((parsed.get("payload") or {}).get("virtual", {}) or {}
                     ).get("children", [])
                ]
            except (ValueError, UnicodeDecodeError):
                kind = ""
        manifests[mid]["kind"] = kind
        manifests[mid]["children"] = children
    return manifests


def _protected_references() -> Set[str]:
    """DB 根源引用集（catalog/revision/run manifest —— 全部 id 级）。"""
    protected: Set[str] = set()
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.lakehouse_catalog import LakehouseCatalogItem
    from app.models.project import Artifact, ArtifactRevision, WorkflowRun

    with SessionLocal() as db:
        # 1. catalog active 投影（project published / session 注册）。
        for (oid,) in db.execute(
            select(LakehouseCatalogItem.object_id).where(
                LakehouseCatalogItem.status == "active"
            )
        ):
            oid = str(oid)
            if len(oid) == 64:
                protected.add(oid)
        for (sha,) in db.execute(
            select(LakehouseCatalogItem.content_sha256).where(
                LakehouseCatalogItem.status == "active"
            )
        ):
            if sha and len(str(sha)) == 64:
                protected.add(str(sha))
        # 2. artifact_revisions：内容历史引用（GC refcount 真相）。
        for (sha,) in db.execute(select(ArtifactRevision.content_sha256)):
            if sha and len(str(sha)) == 64:
                protected.add(str(sha))
        # 3. running/pending runs 的 run manifest artifact 引用（R0-18）。
        live_run_ids = [
            rid for (rid,) in db.execute(
                select(WorkflowRun.id).where(
                    WorkflowRun.status.in_(["running", "pending", "queued"])
                )
            )
        ]
        if live_run_ids:
            from app.models.project import Workflow

            for (storage_ref,) in db.execute(
                select(Artifact.storage_ref).where(
                    Artifact.project_id.in_(
                        select(Workflow.project_id).where(
                            Workflow.id.in_(
                                select(WorkflowRun.workflow_id).where(
                                    WorkflowRun.id.in_(live_run_ids)
                                )
                            )
                        )
                    ),
                    Artifact.storage_ref.is_not(None),
                )
            ):
                ref = str(storage_ref)
                if len(ref) == 64:
                    protected.add(ref)
    return protected


def _live_blob_protection(
    manifests: Mapping[str, Mapping[str, Any]],
    protected_manifests: Set[str],
    store: Any,
) -> Set[str]:
    """活 manifest 引用的 blob 并集（含 virtual children 展开一层 ——
    child manifest 自身若活，其 blobs 由其自身条目并入）。"""
    protected_blobs: Set[str] = set()
    for mid in protected_manifests:
        meta = manifests.get(mid)
        if meta is None:
            continue
        raw = store.get_blob(mid)
        if raw is None:
            continue
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        for blob in parsed.get("content_blobs") or []:
            digest = str(blob.get("sha256") or "")
            if len(digest) == 64:
                protected_blobs.add(digest)
    return protected_blobs


def plan_gc(
    *,
    grace_hours: float = DEFAULT_GRACE_HOURS,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """dry-run 计划（确定性；只读）。"""
    import time as _time

    store = _object_store()
    now = now if now is not None else _time.time()
    manifests = _scan_manifests(now=now, grace_hours=grace_hours)
    db_protected = _protected_references()
    # 宽限保护 + DB 根保护 + 被活 manifest 引用为 child 的 virtual 父链。
    fresh = {
        mid for mid, meta in manifests.items()
        if meta["mtime"] and meta["mtime"] > now - grace_hours * 3600.0
    }
    protected: Set[str] = set(db_protected) | fresh
    # 可达性传播（方向：根**向下**沿引用边）——被受保护 manifest 引用的
    # 对象可达 ⇒ 保护（fork/virtual 链的 child 由此存活）。定点迭代
    # （菱形/多跳收敛；manifest 集有界）。
    changed = True
    while changed:
        changed = False
        protected_snapshot = set(protected)  # 迭代中不变集合（R1-14）
        for mid, meta in manifests.items():
            if mid in protected:
                continue
            for pid in protected_snapshot:
                pid_meta = manifests.get(pid)
                if pid_meta is not None and mid in pid_meta.get(
                    "children", []
                ):
                    protected.add(mid)
                    changed = True
                    break
    watermark = max(
        (float(meta["mtime"]) for meta in manifests.values()),
        default=0.0,
    )
    candidates = sorted(set(manifests) - protected)
    deletable_blobs: List[str] = []
    if candidates:
        protected_blobs = _live_blob_protection(
            manifests, set(manifests) - set(candidates), store,
        )
        for mid in candidates:
            raw = store.get_blob(mid)
            if raw is None:
                continue  # manifest 缺失本身不可再删 blob
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            for blob in parsed.get("content_blobs") or []:
                digest = str(blob.get("sha256") or "")
                if len(digest) == 64 and digest not in protected_blobs:
                    deletable_blobs.append(digest)
    deletable_blobs = sorted(set(deletable_blobs))
    token_payload = json.dumps(
        {"candidates": candidates, "blobs": deletable_blobs,
         "watermark": watermark},
        sort_keys=True,
    )
    token = hashlib.sha256(token_payload.encode()).hexdigest()
    return {
        "candidates": candidates,
        "deletable_blobs": deletable_blobs,
        "protected_count": len(protected),
        "scanned_manifests": len(manifests),
        "watermark": watermark,
        "grace_hours": grace_hours,
        "token": token,
    }


def execute_gc(
    plan: Mapping[str, Any], *, max_deletions: int = MAX_BATCH_DELETIONS,
) -> Dict[str, Any]:
    """执行计划（重验优先 —— R0-12）：

    1. 重新扫描 → token 不匹配（候选集/水位漂移）→ typed STALE；
    2. 逐 blob 当前引用重验（计划期新发布引用候选 blob → 剔除并报告）；
    3. 删除有界批；manifest 最后删；catalog 行由 reconcile 收敛。
    """
    from sqlalchemy import select

    from app.models.lakehouse_catalog import LakehouseCatalogItem

    store = _object_store()
    recheck = plan_gc(grace_hours=float(plan.get("grace_hours",
                                                    DEFAULT_GRACE_HOURS)))
    if recheck["token"] != plan.get("token"):
        raise GCStalePlan(
            "GC plan is stale (state changed since plan) — re-plan required; "
            f"plan had {len(plan.get('candidates', []))} candidates, "
            f"current has {len(recheck['candidates'])}"
        )
    candidates = list(plan.get("candidates", []))[:max_deletions]
    deleted_blobs: List[str] = []
    skipped_protected: List[str] = []
    # 单次扫描建立「非候选 manifest」的引用集（blobs + virtual children
    # id），再对每个待删 blob 做 O(1) 成员检查 —— R0-12 的最终引用重验
    # 以「此刻」引用集为准（计划期新发布 → token 已拦截；残余竞态 →
    # 此处剔除）。
    live_refs = _current_live_references(skip=set(candidates))
    final_blob_plan = []
    for digest in plan.get("deletable_blobs", []):
        if digest in live_refs:
            skipped_protected.append(digest)
        else:
            final_blob_plan.append(digest)
    for digest in final_blob_plan[:max_deletions]:
        if store.delete_blob(digest):
            deleted_blobs.append(digest)
    deleted_manifests: List[str] = []
    for mid in candidates:
        if len(deleted_manifests) >= max_deletions:
            break
        if store.delete_blob(mid):
            deleted_manifests.append(mid)
    # catalog 对账（投影漂移收敛 —— R0-10）。
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        for oid in deleted_manifests:
            for row in db.execute(
                select(LakehouseCatalogItem).where(
                    LakehouseCatalogItem.object_id == oid,
                    LakehouseCatalogItem.status == "active",
                )
            ).scalars().all():
                row.status = "revoked"
        db.commit()
    return {
        "deleted_manifests": sorted(deleted_manifests),
        "deleted_blobs": sorted(deleted_blobs),
        "skipped_protected_blobs": sorted(set(skipped_protected)),
        "skipped_stale": max(
            0, len(plan.get("candidates", [])) - len(candidates)
        ),
    }


def _current_live_references(*, skip: Set[str]) -> Set[str]:
    """此刻全部非候选 manifest 引用的 digest/child id 并集（单次有界扫描）。"""
    store = _object_store()
    refs: Set[str] = set()
    for item in store.iter_objects(limit=MAX_SCAN_OBJECTS):
        rel = str(item["key"])
        if not rel.endswith(".json"):
            continue
        name = rel.rsplit("/", 1)[-1][: -len(".json")]
        if len(name) != 64 or name in skip:
            continue
        raw = store.get_blob(name)
        if raw is None:
            continue
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        for blob in parsed.get("content_blobs") or []:
            digest = str(blob.get("sha256") or "")
            if len(digest) == 64:
                refs.add(digest)
        children = [
            str(c) for c in
            ((parsed.get("payload") or {}).get("virtual", {}) or {}
             ).get("children", [])
        ]
        refs.update(children)
    return refs
