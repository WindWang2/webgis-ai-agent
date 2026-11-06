"""Data Lifecycle V9 —— data-gc 闭环（P5）：dry-run 树 → 审批状态机 →
durable job 执行（staging 二段式）→ 回滚。

状态机（GcPlan.status，迁移 0047 词表）：

    pending_approval ──approve──▶ approved ──execute──▶ executing ──┬─▶ done
          │                     │                  │                   │
          ├──reject──▶ rejected │              rollback──────────────┘
          └──cancel──▶ cancelled└──cancel──▶ cancelled    │（物理删前可回滚）
                                       failed/done ──rollback──▶ rolled_back

安全红线：

- **计划 = 证据**：dry-run 树（对象 → 依赖 → 回收原因 → 预估释放量）在
  创建时固化为 plan_tree（有界 ≤64 对象），执行器绝不重新枚举、绝不扩圈
  （防「审批的是 A 删的是 B」）；
- **staging 二段式**：执行 = 移入 staging 区（文件改名进 ``.staging/<plan>/``，
  DB 行删除为逻辑回收），观察期（staging_expires_at）内可回滚；物理删只经
  显式 purge（观察期之后）；
- **行为保持**：``lakehouse_dataset`` 类只支持 observe（真实删除必须走
  lakehouse 自有 GC 的保护面）——对它启用删除动作在创建计划时即被拒绝；
- **幂等**：同 plan_digest 的未终态计划复用同一行；执行穿 durable job
  （eager 模式下同步完成），重复 execute 拒绝。
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models.data_lifecycle import GcPlan
from app.services.data_lifecycle import metrics as lc_metrics
from app.services.data_lifecycle.policy import assess, plan_digest_of

logger = logging.getLogger(__name__)

#: 计划树内对象上限（有界证据；超出部分计数披露、绝不静默扩圈）。
MAX_TREE_OBJECTS = 64

#: 状态机合法转移表（动作 → {from: to}）。
_TRANSITIONS: Dict[str, Dict[str, str]] = {
    "approve": {"pending_approval": "approved"},
    "reject": {"pending_approval": "rejected"},
    "cancel": {"pending_approval": "cancelled", "approved": "cancelled"},
    "start_execute": {"approved": "executing"},
    "finish_done": {"executing": "done"},
    "finish_failed": {"executing": "failed"},
    "rollback": {"done": "rolled_back",
                 "failed": "rolled_back"},
}

#: staging 区根（与被治理目录同一卷，rename 原子）。
STAGING_DIR = Path("data") / ".gc-staging"
_DEFAULT_STAGING_DIR = STAGING_DIR


def _staging_root() -> Path:
    from app.services.data_lifecycle.adapters import _data_root
    return _data_root() / ".gc-staging" if STAGING_DIR == _DEFAULT_STAGING_DIR else STAGING_DIR

#: 只读对象类：V9 内不允许任何删除动作（行为保持，ADR-0140 附录）。
OBSERVE_ONLY_KINDS = frozenset({"lakehouse_dataset"})


class GcPlanError(Exception):
    """计划操作非法（状态机/范围/词表）——路由层映射 4xx。"""


# ── 计划创建（dry-run 树固化） ────────────────────────────────────────


def create_gc_plan(
    db: Any,
    *,
    kinds: Optional[List[str]] = None,
    tiers: Optional[List[str]] = None,
    created_by: Optional[str] = None,
    org_id: Optional[int] = None,
    now: Optional[datetime] = None,
    owner_scopes: Optional[List[str]] = None,
) -> GcPlan:
    """评估 → 候选树 → GcPlan（pending_approval）。同 digest 未终态计划复用。

    ``owner_scopes`` 非 None 时评估/候选树只含该作用域对象（路由层对
    非 admin 传自己的 scope，防跨 owner 路径进 plan_tree）。
    """
    from app.models.data_lifecycle import LifecyclePolicy

    now = now or datetime.now(timezone.utc)
    if not kinds:
        # DATA-09：默认 kinds 必须剔除 observe-only 类 —— 此前默认列表含
        # lakehouse_dataset，随后又被下面的守卫拒绝，默认建计划恒抛。
        # 调用方显式请求 observe-only 类时仍然拒绝（行为保持红线）。
        kinds = [k for k in ("lakehouse_dataset", "fabric_materialization",
                             "artifact_cache", "cog_output", "worker_cache")
                 if k not in OBSERVE_ONLY_KINDS]
    tiers = tiers or ["cold"]
    for k in kinds:
        if k in OBSERVE_ONLY_KINDS:
            raise GcPlanError(
                f"kind '{k}' 在 V9 只支持 observe（真实删除走 lakehouse 自有 GC 保护面）"
            )

    summary = assess(db, persist=True, now=now, owner_scopes=owner_scopes)
    tree_objects: List[Dict[str, Any]] = []
    total_bytes = 0
    total_count = 0
    for kind, seg in summary["kinds"].items():
        if kind not in kinds:
            continue
        for cand in seg["candidates"]:
            if cand.get("tier") not in tiers:
                continue
            total_count += 1
            total_bytes += int(cand.get("byte_size") or 0)
            if len(tree_objects) < MAX_TREE_OBJECTS:
                tree_objects.append({
                    "kind": kind,
                    "object_id": cand["object_id"],
                    "owner_scope": cand.get("owner_scope") or "",
                    "byte_size": int(cand.get("byte_size") or 0),
                    "tier": cand.get("tier"),
                    "reason": cand.get("reason"),
                    "dependencies": _dependencies_of(kind, cand),
                    "lane": cand.get("lane"),
                })

    scope = {"kinds": sorted(kinds), "tiers": sorted(tiers)}
    plan_tree = {
        "objects": tree_objects,
        "truncated": total_count > len(tree_objects),
        "candidate_count": total_count,
        "candidate_bytes": total_bytes,
        "assessed_at": summary["assessed_at"],
    }
    # 指纹只取稳定内容（时间戳不入指纹：同对象集 → 同 digest → 幂等复用）
    digest = plan_digest_of({
        "scope": scope,
        "objects": plan_tree["objects"],
        "truncated": plan_tree["truncated"],
        "candidate_count": total_count,
        "candidate_bytes": total_bytes,
    })

    # 幂等：存在同 digest 的未终态计划 → 复用
    open_states = ("pending_approval", "approved", "executing")
    existing = (
        db.query(GcPlan)
        .filter(GcPlan.plan_digest == digest, GcPlan.status.in_(open_states),
                GcPlan.org_id == org_id, GcPlan.created_by == created_by)
        .order_by(GcPlan.created_at.desc())
        .first()
    )
    if existing is not None:
        return existing

    policy_rows = db.query(LifecyclePolicy).filter(
        LifecyclePolicy.kind.in_([k for k in kinds if k not in OBSERVE_ONLY_KINDS])
    ).all()
    staging_hours = max(
        (int(p.staging_hours or 0) for p in policy_rows if p.action != "observe"),
        default=72,
    )
    scope["staging_hours"] = staging_hours
    plan = GcPlan(
        org_id=org_id,
        created_by=(str(created_by)[:255] if created_by else None),
        status="pending_approval",
        scope=scope,
        plan_tree=plan_tree,
        candidate_count=total_count,
        candidate_bytes=total_bytes,
        staging_expires_at=now + timedelta(hours=staging_hours),
        plan_digest=digest,
    )
    db.add(plan)
    db.commit()
    lc_metrics.observe_plan_created(total_count, total_bytes)
    return plan


def _dependencies_of(kind: str, cand: Dict[str, Any]) -> Dict[str, Any]:
    """回收原因旁边的依赖披露（有界；诚实——V9 不做跨对象依赖图）。"""
    deps: Dict[str, Any] = {"owner_scope": (cand.get("owner_scope") or "")[:64]}
    if kind == "worker_cache":
        deps["fail_open"] = True   # 注册表行删除 = miss → 重物化（无损）
    if kind == "fabric_materialization":
        deps["note"] = "spill 文件删除后对应 ref 无法再持久重载"
    if kind == "artifact_cache":
        deps["recomputable"] = True  # 内容寻址缓存，纯可再生
    return deps


# ── 状态机 ───────────────────────────────────────────────────────────


def transition(plan: GcPlan, action: str, *, actor: Optional[str] = None,
               result: Optional[Dict[str, Any]] = None) -> GcPlan:
    """按转移表推进状态机；非法转移抛 GcPlanError（路由层映射 409）。

    事务纪律：只改内存态，commit 归调用方（与 DurableJobStore.transition
    同约定）。
    """
    table = _TRANSITIONS.get(action)
    if table is None:
        raise GcPlanError(f"unknown action '{action}'")
    target = table.get(plan.status)
    if target is None:
        raise GcPlanError(f"action '{action}' 不允许自状态 '{plan.status}'")
    plan.status = target
    if action == "approve":
        plan.approved_by = str(actor)[:255] if actor else None
    if action == "start_execute":
        plan.executed_by = str(actor)[:255] if actor else None
    if result is not None:
        plan.result = result
    return plan


def get_plan(db: Any, plan_id: str) -> Optional[GcPlan]:
    return db.get(GcPlan, plan_id)


# ── 执行（staging 二段式） ────────────────────────────────────────────


def _lock_plan(db: Any, plan: GcPlan) -> None:
    # A write obtains a row lock on PostgreSQL and the writer lock on SQLite.
    # Refresh only after ownership so a waiter evaluates the committed state.
    from sqlalchemy import update

    db.execute(update(GcPlan).where(GcPlan.id == plan.id).values(id=GcPlan.id))
    db.refresh(plan)


def execute_plan(db: Any, plan: GcPlan, *, actor: Optional[str] = None,
                 job_id: Optional[str] = None) -> dict[str, object]:
    from app.services.data_lifecycle.gc_recovery import plan_guard

    with plan_guard(db, plan.id, _staging_root()):
        return _execute_plan_locked(db, plan, actor=actor, job_id=job_id)


def _require_recoverable(plan: GcPlan) -> None:
    if ((plan.result or {}).get("_execution_protocol") == 2
            and not (plan.result or {}).get("purged_at")
            and not (_staging_root() / plan.id / ".recovery.json").is_file()):
        raise GcPlanError("GC recovery evidence is unavailable on this storage host; retain plan and reconcile the original staging volume")
    if plan.status == "executing" and (plan.result or {}).get("_execution_protocol") != 2:
        raise GcPlanError("legacy executing plan liveness is unverified; retain staging and reconcile the original worker")


def _check_execution_fence(db: Any, plan: GcPlan, token: str) -> None:
    _lock_plan(db, plan)
    if plan.status != "executing" or (plan.result or {}).get("_execution_token") != token:
        db.rollback()
        raise GcPlanError("GC execution ownership lost")


def _execute_plan_locked(db: Any, plan: GcPlan, *, actor: Optional[str] = None,
                         job_id: Optional[str] = None) -> Dict[str, Any]:
    """approved → executing：按固化的 plan_tree 把对象移入 staging。

    - 文件类（cog_output / artifact_cache / fabric spill）：``move`` 进
      ``data/.gc-staging/<plan_id>/``（保留相对路径以便回滚）；
    - worker_cache：删除注册表行（fail-open 语义 = miss 重物化）；
    - approved → executing CAS excludes simultaneous execution. An executing
      plan cannot be rolled back; interruption requires job reconciliation to
      reach a terminal state before rollback is allowed.
    """
    import uuid
    from app.services.data_lifecycle.gc_revalidation import candidate_still_cold
    from app.services.data_lifecycle.gc_recovery import prepare_origins

    _lock_plan(db, plan)
    if plan.status == "done":
        return dict(plan.result or {})
    _require_recoverable(plan)
    if plan.status not in ("approved", "executing"):
        raise GcPlanError("plan is not approved for execution")
    recovered = plan.status == "executing"
    plan_dir = _staging_root() / plan.id
    origins = prepare_origins(db, plan, plan_dir)
    origin_lanes = {(item['kind'], item['object_id']): item.get('lane') for item in origins}
    token = uuid.uuid4().hex
    plan.status = "executing"
    plan.executed_by = str(actor)[:255] if actor else None
    plan.result = {**(plan.result or {}), "_execution_protocol": 2,
                   "_execution_token": token, "recovered_execution": recovered}
    if job_id:
        plan.job_id = str(job_id)[:64]
    if not recovered:
        plan.staging_expires_at = datetime.now(timezone.utc) + timedelta(hours=int((plan.scope or {}).get("staging_hours", 72)))
    db.commit()
    tree = plan.plan_tree or {}
    staged: List[Dict[str, Any]] = []
    errors: List[str] = []
    skipped: list[dict[str, str]] = []
    plan_dir = _staging_root() / plan.id
    for raw_entry in tree.get("objects", [])[:MAX_TREE_OBJECTS]:
        _check_execution_fence(db, plan, token)
        entry = dict(raw_entry)
        kind = entry.get("kind")
        object_id = str(entry.get("object_id") or "")
        entry["lane"] = origin_lanes.get((kind, object_id), entry.get("lane"))
        try:
            staged_path = plan_dir / str(kind) / (entry.get("lane") or "default") / object_id
            if kind in ("cog_output", "artifact_cache", "fabric_materialization") and staged_path.is_file():
                staged.append({"kind": kind, "object_id": object_id, "lane": entry.get("lane"),
                               "mode": "already_staged", "bytes": staged_path.stat().st_size})
            else:
                path = _resolve_file_path(kind, object_id, lane=entry.get("lane")) if kind != "worker_cache" else None
                eligible, reason = candidate_still_cold(db, entry, path)
                if not eligible:
                    skipped.append({"kind": kind, "object_id": object_id, "reason": reason})
                elif kind in ("cog_output", "artifact_cache", "fabric_materialization"):
                    _stage_file(kind, object_id, plan_dir, staged, lane=entry.get("lane"))
                elif kind == "worker_cache":
                    _reclaim_worker_cache_row(db, object_id)
                    staged.append({"kind": kind, "object_id": object_id, "mode": "registry_row_deleted"})
                else:
                    errors.append(f"{kind}:{object_id}: kind not executable")
        except Exception as exc:  # noqa: BLE001 — preserve per-object failures
            errors.append(f"{kind}:{object_id}: {type(exc).__name__}")
        plan.result = {**(plan.result or {}), "staged": staged[:64], "errors": errors[:16]}
        db.commit()
    _check_execution_fence(db, plan, token)
    result = {
        "_execution_protocol": 2, "_execution_token": token, "recovered_execution": recovered,
        "staged_count": len(staged),
        "skipped_count": len(skipped),
        "skipped": skipped[:64],
        "moved_count": sum(item.get("mode") == "moved_to_staging" for item in staged),
        "warnings": ["all candidates were already absent"] if staged and all(item.get("mode") == "already_gone" for item in staged) else [],
        "staged": staged[:64],
        "errors": errors[:16],
        "ok": not errors,
        "staging_expires_at": plan.staging_expires_at.isoformat()
        if plan.staging_expires_at else None,
    }
    if skipped:
        result["warnings"].append(f"{len(skipped)} candidates skipped after fresh assessment")
    transition(plan, "finish_done" if not errors else "finish_failed",
               actor=actor, result=result)
    db.commit()
    moved = [item for item in staged if item.get("mode") == "moved_to_staging"]
    lc_metrics.observe_executed(len(moved), sum(int(item.get("bytes") or 0) for item in moved))
    return result


def _resolve_file_path(kind: str, object_id: str, *, lane: Optional[str] = None) -> Optional[Path]:
    from app.services.data_lifecycle.adapters import (
        _artifact_root,
        _cog_root,
        _data_root,
        _spill_root,
    )

    if not object_id or Path(object_id).is_absolute() or ".." in Path(object_id).parts:
        raise GcPlanError("invalid GC object path")
    if kind == "artifact_cache":
        return _artifact_root() / (object_id if Path(object_id).suffix else object_id + ".tif")
    if kind == "cog_output":
        return _cog_root() / object_id
    if kind == "fabric_materialization":
        if lane == "spill":
            return _spill_root() / object_id
        if lane == "data":
            return _data_root() / object_id
        raise GcPlanError("fabric origin needs trusted spill/data evidence; preserve this plan and staging")
    return None


def _stage_file(kind: str, object_id: str, plan_dir: Path,
                staged: List[Dict[str, Any]], *, lane: Optional[str] = None) -> None:
    src = _resolve_file_path(kind, object_id, lane=lane)
    if src is None or not src.exists():
        staged.append({"kind": kind, "object_id": object_id,
                       "mode": "already_gone"})
        return
    dst = plan_dir / kind / (lane or "default") / object_id
    if dst.exists():
        staged.append({"kind": kind, "object_id": object_id,
                       "mode": "already_staged"})
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    size = src.stat().st_size
    shutil.move(str(src), str(dst))
    if kind == "artifact_cache" and src.with_suffix(".meta").exists():
        shutil.move(str(src.with_suffix(".meta")), str(dst.with_suffix(".meta")))
    staged.append({"kind": kind, "object_id": object_id, "lane": lane, "mode": "moved_to_staging", "bytes": size})


def _reclaim_worker_cache_row(db: Any, object_id: str) -> None:
    from app.models.db_model import GeoComputeWorkerCache

    worker_id, _, cache_key = object_id.partition(":")
    row = db.query(GeoComputeWorkerCache).filter_by(
        worker_id=worker_id, cache_key=cache_key
    ).one_or_none()
    if row is not None:
        db.delete(row)
        db.flush()


def rollback_plan(db: Any, plan: GcPlan, *, actor: Optional[str] = None) -> dict[str, object]:
    from app.services.data_lifecycle.gc_recovery import plan_guard

    with plan_guard(db, plan.id, _staging_root()):
        return _rollback_plan_locked(db, plan, actor=actor)


def _rollback_plan_locked(db: Any, plan: GcPlan, *, actor: Optional[str] = None) -> Dict[str, Any]:
    """staging 区对象搬回原位（观察期内）；DB 行类无法回滚（诚实披露）。"""
    _lock_plan(db, plan)
    from app.services.data_lifecycle.gc_recovery import restore_target
    _require_recoverable(plan)
    if plan.status == "executing":
        # Exclusive liveness locks prove the protocol-2 executor has ended.
        plan.status = "failed"
        db.commit()
        _lock_plan(db, plan)
    if (plan.result or {}).get("purged_at"):
        raise GcPlanError("purged plan cannot be rolled back")
    if plan.status not in _TRANSITIONS["rollback"]:
        raise GcPlanError(f"rollback not allowed from {plan.status}")
    plan_dir = _staging_root() / plan.id
    restored: List[Dict[str, Any]] = []
    errors: List[str] = []
    if plan_dir.exists():
        for kind_dir in plan_dir.iterdir():
            if not kind_dir.is_dir():
                continue  # bounded recovery manifest is not an object payload
            for path in kind_dir.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    dst = restore_target(db, plan, plan_dir, path)
                    if dst.exists():
                        raise GcPlanError("original target is occupied; preserved staged bytes")
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(dst))
                    restored.append({"kind": kind_dir.name, "object_id": str(path.relative_to(kind_dir))})
                except (OSError, GcPlanError) as exc:
                    errors.append(f"{kind_dir.name}:{path.name}: {exc}")
    result = {
        "rollback_partial": bool(errors),
        "restored_count": len(restored),
        "restored": restored[:64],
        "errors": errors[:16],
        "note": "worker_cache 注册表行为逻辑回收（fail-open），不参与文件回滚",
    }
    if errors:
        # A partial/ambiguous restore remains recoverable on the SAME plan.
        plan.status = "failed"
        plan.result = {**(plan.result or {}), **result, "rollback_partial": True}
    else:
        transition(plan, "rollback", actor=actor, result={**(plan.result or {}), **result})
    db.commit()
    lc_metrics.observe_rolled_back(len(restored))
    return result


def purge_staging(plan: GcPlan, *, db: Any = None) -> dict[str, object]:
    from sqlalchemy.orm import object_session
    from app.services.data_lifecycle.gc_recovery import plan_guard

    db = db if db is not None else object_session(plan)
    if db is None:
        raise GcPlanError("purge requires a persistent transaction")
    with plan_guard(db, plan.id, _staging_root()):
        return _purge_staging_locked(plan, db=db)


def _purge_staging_locked(plan: GcPlan, *, db: Any = None) -> Dict[str, Any]:
    """Permanently purge an expired terminal plan; serialize with rollback."""
    if db is None:
        from sqlalchemy.orm import object_session
        db = object_session(plan)
    if db is None:
        raise GcPlanError("purge requires a persistent transaction")
    _lock_plan(db, plan)
    if plan.status not in ("done", "failed"):
        raise GcPlanError(f"purge not allowed from {plan.status}")
    if (plan.result or {}).get("rollback_partial"):
        raise GcPlanError("partial recovery must be resolved before purge")
    _require_recoverable(plan)
    if (plan.result or {}).get("purged_at"):
        return {"purged": True, "already_purged": True}
    expires = plan.staging_expires_at
    if expires is not None:
        expires = expires.replace(tzinfo=timezone.utc) if expires.tzinfo is None else expires.astimezone(timezone.utc)
        if expires > datetime.now(timezone.utc):
            raise GcPlanError("staging observation period has not expired")
    plan_dir = _staging_root() / plan.id
    existed = plan_dir.exists()
    if existed:
        shutil.rmtree(plan_dir)
    result = {**(plan.result or {}), "purged_at": datetime.now(timezone.utc).isoformat(), "rollback_available": False}
    plan.result = result
    db.commit()
    return {"purged": True, "had_staging": existed, "purged_at": result["purged_at"]}


__all__ = [
    "GcPlanError",
    "OBSERVE_ONLY_KINDS",
    "create_gc_plan",
    "transition",
    "get_plan",
    "execute_plan",
    "rollback_plan",
    "purge_staging",
    "STAGING_DIR",
    "MAX_TREE_OBJECTS",
]
