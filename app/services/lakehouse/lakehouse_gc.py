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
#: raw 对象枚举上限（≫ blob 数 —— 截断即 typed abort，绝不部分视图，
#: 评审 R2-1：截断的部分视图 = 活引用漏保护 = 静默数据丢失）。
MAX_SCAN_OBJECTS = 100_000
#: manifest 数量上限（解析/保护图的节点上界）。
MAX_SCAN_MANIFESTS = 10_000
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


def _iter_bounded(store: Any):
    """有界枚举：超过 raw 上限 = typed abort（绝不静默部分视图 —— R2-1）。"""
    seen = 0
    for item in store.iter_objects(limit=MAX_SCAN_OBJECTS + 1):
        seen += 1
        if seen > MAX_SCAN_OBJECTS:
            raise GCError(
                f"object store enumeration exceeds {MAX_SCAN_OBJECTS} "
                "objects — GC refuses to run on a truncated view "
                "(raise the cap or shard the store)"
            )
        yield item


def _scan_manifests() -> Dict[str, Dict[str, Any]]:
    """BlobStore 枚举 → {manifest_id: 解析缓存}（**单次解析** —— 评审
    R2-4：kind/children/content_blobs 在扫描期读一次并缓存，plan/execute
    的保护与候选阶段全部复用，消除 ~7× manifest 重读放大）。"""
    import json as _json

    store = _object_store()
    if not getattr(store, "supports_enumeration", False):
        raise GCError(
            "GC requires an enumerable object store; the selected backend "
            "does not support enumeration"
        )
    manifests: Dict[str, Dict[str, Any]] = {}
    for item in _iter_bounded(store):
        # 路径分隔符归一化（FS 后端在 Windows 上产出反斜杠相对路径；
        # S3 key 恒为 '/' —— 归一化对两者无歧义）。
        rel = str(item["key"]).replace("\\", "/")
        if not rel.endswith(".json"):
            continue
        name = rel.rsplit("/", 1)[-1][: -len(".json")]
        if len(name) != 64 or not all(c in "0123456789abcdef" for c in name):
            continue
        if len(manifests) >= MAX_SCAN_MANIFESTS:
            raise GCError(
                f"manifest count exceeds {MAX_SCAN_MANIFESTS} — GC refuses "
                "to run on a truncated view"
            )
        raw = store.get_blob(name)
        kind = ""
        children: List[str] = []
        blobs: List[str] = []
        if raw is not None:
            try:
                parsed = _json.loads(raw.decode("utf-8"))
                kind = str(parsed.get("kind") or "")
                children = [
                    str(c) for c in
                    ((parsed.get("payload") or {}).get("virtual", {}) or {}
                     ).get("children", [])
                ]
                blobs = [
                    str(b.get("sha256") or "")
                    for b in parsed.get("content_blobs") or []
                ]
            except (ValueError, UnicodeDecodeError):
                kind = ""
        manifests[name] = {
            # 消费侧归一化（S3 yield epoch float；防御性兼容 datetime）。
            "mtime": _as_epoch(item.get("last_modified")),
            "kind": kind,
            "children": children,
            "blobs": blobs,
        }
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
        # 4. V8 dataset registry（ADR-0130）：数据集描述符 / 版本 commit
        #    manifest / 版本内容 —— 版本历史是引用；仅被历史引用的
        #    manifest/blob 绝不回收（tag/branch 指向的 version 行已覆盖
        #    —— 指针不直接进 root，经由版本行传递，与 union 规则正交）。
        from app.models.lakehouse_datasets import (
            LakehouseDataset,
            LakehouseDatasetVersion,
        )

        for (did,) in db.execute(select(LakehouseDataset.dataset_id)):
            if did and len(str(did)) == 64:
                protected.add(str(did))
        for (vid,) in db.execute(select(LakehouseDatasetVersion.version_id)):
            if vid and len(str(vid)) == 64:
                protected.add(str(vid))
        for (oid,) in db.execute(
            select(LakehouseDatasetVersion.data_object_id)
        ):
            if oid and len(str(oid)) == 64:
                protected.add(str(oid))
    return protected


def _live_blob_protection(
    manifests: Mapping[str, Mapping[str, Any]],
    protected_manifests: Set[str],
    store: Any,
) -> Set[str]:
    """活 manifest 引用的 blob 并集（缓存的解析结果 —— 零额外 IO）。"""
    protected_blobs: Set[str] = set()
    for mid in protected_manifests:
        meta = manifests.get(mid)
        if meta is None:
            continue
        for digest in meta.get("blobs", []):
            if len(digest) == 64:
                protected_blobs.add(digest)
    return protected_blobs


def _registry_ttl_floor() -> float:
    """registry TTL 下限（R0-19/R2-8：宽限 < TTL = 可回收仍登记的
    manifest）。读 session 数据 TTL 同源；不可得 = 0（无 Redis 后端）。"""
    try:
        from app.services.artifact_registry import _default_ttl

        return float(_default_ttl() or 0.0) / 3600.0
    except Exception:  # noqa: BLE001 — TTL 不可得即不设下限
        return 0.0


def plan_gc(
    *,
    grace_hours: float = DEFAULT_GRACE_HOURS,
    now: Optional[float] = None,
    ttl_floor: Optional[float] = None,
) -> Dict[str, Any]:
    """dry-run 计划（确定性；只读）。

    ``ttl_floor``：显式覆盖 registry TTL 下限（测试用）；生产路径不传
    —— 宽限自动抬到 ≥ registry TTL。
    """
    import time as _time

    effective_grace = max(
        float(grace_hours),
        _registry_ttl_floor() if ttl_floor is None else float(ttl_floor),
    )
    store = _object_store()
    now = now if now is not None else _time.time()
    manifests = _scan_manifests()
    db_protected = _protected_references()
    # 宽限保护 + DB 根保护 + 被活 manifest 引用为 child 的 virtual 父链。
    fresh = {
        mid for mid, meta in manifests.items()
        if meta["mtime"] and meta["mtime"] > now - effective_grace * 3600.0
    }
    protected: Set[str] = set(db_protected) | fresh
    # 可达性（方向：根**向下**沿引用边）——受保护 manifest 的 children
    # 可达 ⇒ 保护；**绝不向上**（引用受保护对象的孤儿父仍是垃圾）。
    # BFS O(V+E)（评审 R2-5）。
    queue: List[str] = list(protected)
    while queue:
        current = queue.pop()
        meta = manifests.get(current)
        if meta is None:
            continue
        for child in meta.get("children", []):
            if child not in protected and child in manifests:
                protected.add(child)
                queue.append(child)
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
            for digest in manifests[mid].get("blobs", []):
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
        "grace_hours": effective_grace,
        "requested_grace_hours": float(grace_hours),
        "registry_ttl_floor_hours": (
            _registry_ttl_floor() if ttl_floor is None else float(ttl_floor)
        ),
        "token": token,
        "_scan": manifests,
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

    # plan 形状校验（R2-22：admin 面也不裸 float()）。
    if not isinstance(plan, Mapping) or not isinstance(
        plan.get("token"), str
    ) or not isinstance(plan.get("candidates"), list) \
            or not isinstance(plan.get("deletable_blobs"), list):
        raise GCError("malformed GC plan — re-plan required")
    try:
        requested_grace = float(plan.get("grace_hours", DEFAULT_GRACE_HOURS))
    except (TypeError, ValueError) as e:
        raise GCError(f"malformed grace_hours in plan: {e}") from e
    store = _object_store()
    recheck = plan_gc(
        grace_hours=requested_grace,
        ttl_floor=plan.get("registry_ttl_floor_hours"),
    )
    scan = recheck.pop("_scan")
    if recheck["token"] != plan.get("token"):
        raise GCStalePlan(
            "GC plan is stale (state changed since plan) — re-plan required; "
            f"plan had {len(plan.get('candidates', []))} candidates, "
            f"current has {len(recheck['candidates'])}"
        )
    candidates = [
        c for c in plan.get("candidates", [])
        if isinstance(c, str) and len(c) == 64
    ][:max_deletions]
    deleted_blobs: List[str] = []
    skipped_protected: List[str] = []
    # 「非候选 manifest」引用集（blobs + virtual children id）直接从
    # recheck 的**单次解析缓存**导出（评审 R2-4：零重枚举/重读）。
    live_refs: Set[str] = set()
    for mid, meta in scan.items():
        if mid in set(candidates):
            continue
        live_refs.update(d for d in meta.get("blobs", []) if len(d) == 64)
        live_refs.update(meta.get("children", []))
    final_blob_plan = []
    for digest in plan.get("deletable_blobs", []):
        if not isinstance(digest, str) or len(digest) != 64:
            continue
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
        if deleted_manifests:
            rows = db.execute(
                select(LakehouseCatalogItem).where(
                    LakehouseCatalogItem.status == "active",
                    LakehouseCatalogItem.object_id.in_(deleted_manifests)
                    | LakehouseCatalogItem.content_sha256.in_(
                        deleted_manifests
                    ),
                )
            ).scalars().all()
            for row in rows:
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
