"""审阅决定持久化（plan 级；pair 稳定键唯一；CAS 乐观并发）。

语义：
- 决定挂 plan（不挂 run）：增量刷新新 run 自动继承全部锁定决定；
- ``pair_key`` = "fp:<fa>:<fb>"（实体内容指纹）—— 与快照 index 无关，
  来源刷新后按内容重挂靠；``idx_a/idx_b`` 只是决定时展示锚；
- upsert 走 revision CAS：审阅冲突 typed stale_review_revision（绝不
  覆盖他人决定）；
- ``locked`` 决定（人工锁定）只能显式删除 —— 匹配/刷新绝不无声推翻。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select

from app.services.data_fusion_authoring import contracts
from app.services.data_fusion_authoring.errors import DataFusionError

logger = logging.getLogger("data_fusion.review")

_MAX_FIELD_VALUES_BYTES = 16 * 1024
_MAX_NOTE_LEN = 500


def _decision_view(row: Any) -> Dict[str, Any]:
    return {
        "decision_id": row.id,
        "plan_id": row.plan_id,
        "pair_key": row.pair_key,
        "action": row.action,
        "field_values": row.field_values or {},
        "geometry_side": row.geometry_side,
        "decision_run_id": row.decision_run_id,
        "idx_a": row.idx_a,
        "idx_b": row.idx_b,
        "reviewer": row.reviewer,
        "note": row.note,
        "locked": bool(row.locked),
        "revision": row.revision,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _validate_field_values(field_values: Optional[Dict[str, Any]]) -> None:
    if not field_values:
        return
    blob = json.dumps(field_values, ensure_ascii=False, default=str)
    if len(blob.encode("utf-8")) > _MAX_FIELD_VALUES_BYTES:
        raise DataFusionError(
            "invalid_plan",
            f"field_values 超过有界上限 {_MAX_FIELD_VALUES_BYTES} B",
        )
    for k, v in field_values.items():
        if not isinstance(k, str) or not k or len(k) > 128:
            raise DataFusionError(
                "invalid_plan", f"field_values 键非法：{k!r}")


def load_decisions_by_key(
    plan_id: str,
) -> Dict[str, Dict[str, Any]]:
    """plan 全部决定 → {pair_key: decision}（executor 组装共用）。"""
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionDecision

    with SessionLocal() as db:
        rows = db.execute(
            select(DataFusionDecision).where(
                DataFusionDecision.plan_id == plan_id)
        ).scalars().all()
        return {r.pair_key: _decision_view(r) for r in rows}


def upsert_decision(
    session_id: str,
    plan_id: str,
    *,
    pair_key: str,
    action: str,
    decision_run_id: Optional[str],
    idx_a: Optional[int],
    idx_b: Optional[int],
    field_values: Optional[Dict[str, Any]],
    geometry_side: Optional[str],
    reviewer: Optional[str],
    note: Optional[str],
    expected_revision: int,
) -> Dict[str, Any]:
    """创建/更新决定（CAS；新 pair 走 expected_revision=0）。"""
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionDecision

    if action not in ("accept", "reject"):
        raise DataFusionError(
            "invalid_plan", f"action 必须是 accept|reject：{action!r}")
    if geometry_side is not None and geometry_side not in ("a", "b"):
        raise DataFusionError(
            "invalid_plan", f"geometry_side 必须是 a|b：{geometry_side!r}")
    if note and len(note) > _MAX_NOTE_LEN:
        raise DataFusionError("invalid_plan", "note 超过 500 字符上限")
    _validate_field_values(field_values)
    if not pair_key.startswith("fp:") or len(pair_key) > 160:
        raise DataFusionError(
            "invalid_plan", "pair_key 必须是内容指纹键（fp:…）")

    with SessionLocal() as db:
        row = db.execute(
            select(DataFusionDecision).where(
                DataFusionDecision.plan_id == plan_id,
                DataFusionDecision.pair_key == pair_key,
            )
        ).scalar_one_or_none()
        if row is None:
            if expected_revision not in (0, None):
                raise DataFusionError(
                    "stale_review_revision",
                    f"决定不存在而声明了修订 {expected_revision}"
                    "（请先读取当前决定）",
                )
            row = DataFusionDecision(
                plan_id=plan_id,
                session_id=session_id,
                pair_key=pair_key,
                action=action,
                field_values=field_values,
                geometry_side=geometry_side,
                decision_run_id=decision_run_id,
                idx_a=idx_a,
                idx_b=idx_b,
                reviewer=(reviewer or "")[:255] or None,
                note=note,
                locked=1,
                revision=1,
            )
            db.add(row)
            db.commit()
            return _decision_view(row)
        if row.revision != int(expected_revision):
            raise DataFusionError(
                "stale_review_revision",
                f"决定修订冲突：expected {expected_revision}，"
                f"当前 {row.revision}（他人已更新；请刷新后重试）",
                current_revision=row.revision,
            )
        row.action = action
        # 部分更新语义：field_values=None = 保留已有人工值（显式 {} 才清空）
        # —— CAS 复核（补备注/换 action）不得无声抹掉先前的人工决定。
        if field_values is not None:
            row.field_values = field_values
        if geometry_side is not None:
            row.geometry_side = geometry_side
        row.decision_run_id = decision_run_id
        row.idx_a = idx_a
        row.idx_b = idx_b
        row.reviewer = (reviewer or "")[:255] or None
        row.note = note
        row.locked = 1
        row.revision += 1
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        return _decision_view(row)


def delete_decision(
    session_id: str,
    plan_id: str,
    pair_key: str,
    *,
    expected_revision: int,
) -> Dict[str, Any]:
    """显式解除决定（唯一推翻人工锁定的路径；typed 记录）。"""
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionDecision

    with SessionLocal() as db:
        row = db.execute(
            select(DataFusionDecision).where(
                DataFusionDecision.plan_id == plan_id,
                DataFusionDecision.pair_key == pair_key,
            )
        ).scalar_one_or_none()
        if row is None:
            raise DataFusionError(
                "decision_not_found", f"决定不存在：{pair_key[:24]}…",
                status=404)
        if row.revision != int(expected_revision):
            raise DataFusionError(
                "stale_review_revision",
                f"决定修订冲突：expected {expected_revision}，"
                f"当前 {row.revision}",
                current_revision=row.revision,
            )
        view = _decision_view(row)
        db.delete(row)
        db.commit()
        return {"deleted": True, "decision": view}


def list_decisions(
    plan_id: str, *, limit: int = 200, cursor: int = 0
) -> Dict[str, Any]:
    """决定分页（按 created_at 稳定序；有界）。"""
    from app.core.database import SessionLocal
    from app.models.data_fusion_authoring import DataFusionDecision

    lim = max(1, min(int(limit), contracts.REVIEW_PAGE_MAX_ITEMS))
    with SessionLocal() as db:
        rows = db.execute(
            select(DataFusionDecision)
            .where(DataFusionDecision.plan_id == plan_id)
            .order_by(DataFusionDecision.created_at, DataFusionDecision.id)
            .offset(max(0, int(cursor))).limit(lim + 1)
        ).scalars().all()
        has_more = len(rows) > lim
        rows = rows[:lim]
        return {
            "decisions": [_decision_view(r) for r in rows],
            "cursor": (int(cursor) + lim) if has_more else None,
            "has_more": has_more,
        }
