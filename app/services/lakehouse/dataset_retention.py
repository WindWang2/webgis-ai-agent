"""Dataset retention — Versioned Lakehouse V8 (ADR-0130 §5).

**元数据级**保留策略：per-dataset 的版本行 prune（plan/execute 分离，
token 重验）。字节删除唯一入口仍是 ``lakehouse_gc.execute_gc``
（dereference-based）—— retention 裁剪版本行 → 内容失去版本引用 →
自然回到 GC 候选。两层组合排除「retention 误删被引用 blob」整类事故
（quota sweeper 与 lakehouse GC 的双入口问题不再扩散）。

谓词（全部有界、确定性）：

- **受指针保护**：任何 branch/tag 指针行指向的版本绝不 prune
  （tag pin 语义 —— R0-17 union 规则在指针层的推广）；
- **min_age_hours**：新于该年龄的版本绝不 prune（防误删新鲜实验）；
- **max_versions**：每 dataset 保留最新 N 个版本（超出者，且满足
  上述两条 → prune 候选）。

链断裂披露：prune 中间版本会使 parent 链断裂 —— ``version_lineage``
以 ``truncated: true`` 诚实披露（台账 append-only 优先，绝不改写
幸存行来"缝合"历史）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 默认策略（保守：多留不少删）。
DEFAULT_MAX_VERSIONS = 64
DEFAULT_MIN_AGE_HOURS = 72.0

#: 批量 prune 上限（单次 execute 有界）。
MAX_BATCH_PRUNES = 5_000


class RetentionError(ValueError):
    code = "LAKEHOUSE_RETENTION_INVALID"


class RetentionStalePlan(RetentionError):
    """plan 与当前状态漂移（execute 拒绝 —— 重新规划）。"""

    code = "LAKEHOUSE_RETENTION_PLAN_STALE"


def _version_row_dict(row) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "version_id": str(row.version_id),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def plan_retention(
    db,
    dataset_row,
    *,
    max_versions: int = DEFAULT_MAX_VERSIONS,
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS,
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    """dry-run 计划（确定性；只读 —— 逐条候选 + token）。

    返回候选（新→旧序中超出保留窗口、且受指针/年龄保护之外的版本）、
    保护计数与 token；绝不修改任何行。
    """
    from datetime import datetime, timezone

    from app.services.lakehouse.dataset_registry import list_refs, list_versions

    if max_versions < 1:
        raise RetentionError("max_versions must be >= 1")
    if min_age_hours < 0:
        raise RetentionError("min_age_hours must be >= 0")
    versions = list_versions(
        db, dataset_row.id, limit=10_000  # 有界扫描（台账有界前提）
    )
    protected_by_refs = {
        str(r.version_id) for r in list_refs(db, dataset_row.id)
    }
    now = now or datetime.now(timezone.utc)
    cutoff = None
    if min_age_hours:
        from datetime import timedelta

        cutoff = now - timedelta(hours=float(min_age_hours))

    def _aware(value):
        # SQLite 方言返回 naive datetime（无 tzinfo）—— 统一按 UTC 解释。
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    # 新→旧；前 max_versions 个受保留窗口保护。
    candidates: List[Dict[str, Any]] = []
    for position, row in enumerate(versions):
        vid = str(row.version_id)
        if position < int(max_versions):
            continue
        if vid in protected_by_refs:
            continue
        created = _aware(row.created_at)
        if cutoff is not None and created and created > cutoff:
            continue
        candidates.append(_version_row_dict(row))
    candidates.sort(key=lambda c: c["version_id"])  # 确定性排序
    token_payload = json.dumps(
        {"dataset": str(dataset_row.id), "candidates": candidates,
         "max_versions": int(max_versions)},
        sort_keys=True,
    )
    token = hashlib.sha256(token_payload.encode()).hexdigest()
    return {
        "dataset_row_id": str(dataset_row.id),
        "dataset_id": str(dataset_row.dataset_id),
        "candidates": [c["version_id"] for c in candidates],
        "candidate_count": len(candidates),
        "protected_by_refs": len(protected_by_refs),
        "protected_by_window": min(len(versions), int(max_versions)),
        "scanned_versions": len(versions),
        "max_versions": int(max_versions),
        "min_age_hours": float(min_age_hours),
        "token": token,
    }


def execute_retention(db, plan: Dict[str, Any], *,
                      max_prunes: int = MAX_BATCH_PRUNES) -> Dict[str, Any]:
    """执行 prune（重验优先）：token 漂移 → typed STALE；只删版本行。

    指针保护重验：plan 期后新指到候选版本的 ref → 该候选剔除并披露
    （skipped_protected）—— 与 GC execute 的逐引用重验同纪律。
    只 flush 不 commit（调用方控制事务）。
    """
    from sqlalchemy import select

    from app.models.lakehouse_datasets import LakehouseDatasetVersion
    from app.services.lakehouse.dataset_registry import list_refs

    dataset_row_id = str(plan.get("dataset_row_id")
                         or plan.get("dataset_id") or "")
    token = str(plan.get("token") or "")
    if not dataset_row_id or not token:
        raise RetentionError("plan must carry dataset_row_id and token")
    from app.services.lakehouse.dataset_registry import get_dataset

    dataset_row = get_dataset(db, dataset_row_id)
    if dataset_row is None:
        raise RetentionError(f"dataset not found: {dataset_row_id[:12]}")
    max_versions = int(plan.get("max_versions") or DEFAULT_MAX_VERSIONS)
    min_age_hours = float(plan.get("min_age_hours") or 0.0)
    recheck = plan_retention(
        db, dataset_row, max_versions=max_versions,
        min_age_hours=min_age_hours,
    )
    if recheck["token"] != token:
        raise RetentionStalePlan(
            "retention plan is stale — re-plan (state moved since planning)"
        )
    protected_now = {
        str(r.version_id) for r in list_refs(db, dataset_row.id)
    }
    pruned: List[str] = []
    skipped_protected: List[str] = []
    for vid in list(plan.get("candidates") or []):
        if len(pruned) >= int(max_prunes):
            break
        if vid in protected_now:
            skipped_protected.append(str(vid))
            continue
        row = db.execute(
            select(LakehouseDatasetVersion).where(
                LakehouseDatasetVersion.dataset_row_id == dataset_row.id,
                LakehouseDatasetVersion.version_id == str(vid),
            )
        ).scalar_one_or_none()
        if row is None:
            continue  # 已被并发 prune（幂等）
        db.delete(row)
        pruned.append(str(vid))
    db.flush()
    return {
        "dataset_row_id": str(dataset_row.id),
        "dataset_id": str(dataset_row.dataset_id),
        "pruned": sorted(pruned),
        "pruned_count": len(pruned),
        "skipped_protected": sorted(skipped_protected),
    }
