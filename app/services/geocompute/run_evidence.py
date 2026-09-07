"""run 终态证据快照的持久化与回放（audit 06 §6.1 step 2，V5）。

run 注册表是进程内存（``executor._runs``）—— 进程重启后 ``GET /runs`` 一律
404。V5 在 run 进入终态时把**有界（≤16KB）**的 ExecutionRun 摘要写入
``geocompute_run_evidence``（owner 域隔离）；``get_run`` 内存未命中时按
owner 校验回放 —— 读取不再 404（快照来源在 ``ExecutionRun.source`` 诚实
标注为 ``"snapshot"``）。

边界：
- 快照只有 summary/evidence（状态、行数、错误码、ref 指针），**绝无节点
  载荷**；超限按确定性阶梯降级（完整 → 压缩 evidence → 截断 evidence 集），
  截断事实随快照记录（``evidence_truncated`` / ``evidence_total``）。
- append-once/upsert-on-terminal，无状态迁移 —— 不是第二 run 注册表；
  写入 fail-open（DB 不可用 → 不落快照，绝不倒灌执行路径）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: 快照 JSON 字符预算（字节）。超出按确定性阶梯降级。
MAX_SNAPSHOT_BYTES = 16 * 1024

#: evidence 条目错误文本的快照截断（完整 300 字符证据仍在内存注册表里）。
_SNAPSHOT_ERROR_CHARS = 120


def _default_session_factory():
    from app.core.database import SessionLocal

    return SessionLocal


#: 可注入的会话工厂（测试替换为临时 SQLite 工厂）。
session_factory: Callable[[], Any] = _default_session_factory


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _evidence_full(ev: Any) -> dict[str, Any]:
    """完整 evidence 投影（错误文本按快照预算截断）。"""
    dump = ev.model_dump()
    msg = dump.get("error_message")
    if isinstance(msg, str) and len(msg) > _SNAPSHOT_ERROR_CHARS:
        dump["error_message"] = msg[:_SNAPSHOT_ERROR_CHARS]
    summary = dump.get("output_summary")
    if isinstance(summary, dict) and len(summary) > 16:
        dump["output_summary"] = dict(list(summary.items())[:16])
    return dump


def _evidence_compact(ev: Any) -> dict[str, Any]:
    """压缩 evidence 投影（L2：丢摘要/错误文本/指纹，保留判型字段）。"""
    return {
        "status": ev.status,
        "attempts": ev.attempts,
        "rows_emitted": ev.rows_emitted,
        "error_code": ev.error_code,
        "output_ref": ev.output_ref,
        "duration_s": ev.duration_s,
        "checkpoint_verified": ev.checkpoint_verified,
        "backend_variant": ev.backend_variant,
        "reuse_source": ev.reuse_source,
        "reuse_skipped_reason": ev.reuse_skipped_reason,
    }


def build_snapshot(run: Any) -> dict[str, Any]:
    """ExecutionRun → 有界（≤ ``MAX_SNAPSHOT_BYTES``）JSON dict（确定性）。

    阶梯：L1 完整 evidence → L2 压缩 evidence → L3 截断 evidence 集合
    （按计划节点序保留前 K 个，截断事实随快照记录）。任何一级超限都会
    继续降级；L3 的单条 evidence 是有界投影，16KB 内必然收敛。
    """
    run_dict = {
        "run_id": run.run_id,
        "plan_id": run.plan_id,
        "plan_fingerprint": run.plan_fingerprint,
        "status": run.status.value if hasattr(run.status, "value") else str(run.status),
        "wall_time_s": run.wall_time_s,
        "error_code": run.error_code,
        "error_message": (run.error_message or "")[:300] or None,
    }
    evidence = {nid: _evidence_full(ev) for nid, ev in run.evidence.items()}
    snapshot: dict[str, Any] = {"run": run_dict, "evidence": evidence}
    if _fits(snapshot):
        return snapshot

    # L2：压缩 evidence 条目
    snapshot = {
        "run": run_dict,
        "evidence": {nid: _evidence_compact(ev) for nid, ev in run.evidence.items()},
        "evidence_detail": "compact",
    }
    if _fits(snapshot):
        return snapshot

    # L3：按节点序截断 evidence 集合（有界投影，必然收敛）
    compact = snapshot["evidence"]
    total = len(compact)
    keep = total
    while keep > 1:
        keep //= 2
        candidate = {
            "run": run_dict,
            "evidence": dict(list(compact.items())[:keep]),
            "evidence_detail": "compact",
            "evidence_truncated": True,
            "evidence_total": total,
        }
        if _fits(candidate):
            return candidate
    return {
        "run": run_dict,
        "evidence": {},
        "evidence_detail": "compact",
        "evidence_truncated": True,
        "evidence_total": total,
    }


def _fits(snapshot: dict[str, Any]) -> bool:
    from app.services.geocompute.normalization import canonical_dumps

    return len(canonical_dumps(snapshot).encode("utf-8")) <= MAX_SNAPSHOT_BYTES


def save_snapshot(run: Any, owner_scope: str) -> bool:
    """run 终态快照 upsert（run_id 主键）；fail-open，返回是否落库。"""
    if not run or not owner_scope:
        return False
    try:
        snapshot = build_snapshot(run)
        status = (
            run.status.value if hasattr(run.status, "value") else str(run.status)
        )
        with session_factory() as db:
            from app.models.db_model import GeoComputeRunEvidence

            existing = (
                db.query(GeoComputeRunEvidence)
                .filter(GeoComputeRunEvidence.run_id == run.run_id)
                .first()
            )
            if existing is not None:
                existing.owner_scope = owner_scope[:40]
                existing.status = status
                existing.snapshot = snapshot
            else:
                db.add(GeoComputeRunEvidence(
                    run_id=run.run_id[:64],
                    owner_scope=owner_scope[:40],
                    status=status,
                    snapshot=snapshot,
                    created_at=_utcnow(),
                ))
            db.commit()
            return True
    except Exception:  # noqa: BLE001 - 快照绝不倒灌执行路径
        logger.debug("[geocompute] run evidence snapshot not saved", exc_info=True)
        return False


def load_snapshot(
    run_id: str, *, owner_scope: Optional[str] = None
) -> Optional[Any]:
    """按 run_id 回放快照为 ExecutionRun（``source="snapshot"``）。

    ``owner_scope`` 给定时做读隔离：归属不符一律 None（调用方 404，不区分
    「不存在」与「他人 run」）。DB 不可用/未落快照 → None（诚实 404）。
    """
    if not run_id:
        return None
    try:
        with session_factory() as db:
            from app.models.db_model import GeoComputeRunEvidence

            row = (
                db.query(GeoComputeRunEvidence)
                .filter(GeoComputeRunEvidence.run_id == run_id)
                .first()
            )
            if row is None:
                return None
            if owner_scope is not None and row.owner_scope != owner_scope:
                return None
            return _run_from_snapshot(row.snapshot)
    except Exception:  # noqa: BLE001 - 回放 fail-open
        logger.debug("[geocompute] run evidence snapshot not loaded", exc_info=True)
        return None


def _run_from_snapshot(snapshot: Any) -> Optional[Any]:
    """快照 dict → ExecutionRun（缺字段/形状漂移 → None，绝不虚构）。"""
    if not isinstance(snapshot, dict):
        return None
    from app.services.geocompute.plan import ExecutionRun, ExecutionRunStatus, NodeEvidence

    run_dict = snapshot.get("run")
    if not isinstance(run_dict, dict):
        return None
    try:
        status = ExecutionRunStatus(run_dict.get("status", "pending"))
        evidence = {
            nid: NodeEvidence(**ev)
            for nid, ev in (snapshot.get("evidence") or {}).items()
            if isinstance(ev, dict)
        }
        return ExecutionRun(
            run_id=str(run_dict.get("run_id", "")),
            plan_id=str(run_dict.get("plan_id", "")),
            plan_fingerprint=str(run_dict.get("plan_fingerprint", "")),
            status=status,
            evidence=evidence,
            wall_time_s=run_dict.get("wall_time_s"),
            error_code=run_dict.get("error_code"),
            error_message=run_dict.get("error_message"),
            source="snapshot",
        )
    except Exception:  # noqa: BLE001 - 形状漂移按未命中处理
        logger.debug("[geocompute] snapshot replay failed", exc_info=True)
        return None
