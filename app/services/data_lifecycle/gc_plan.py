"""Data Lifecycle V9 —— data-gc 闭环（P5）：dry-run 树 → 审批状态机 →
durable job 执行（staging 二段式）→ 回滚。

状态机（GcPlan.status，迁移 0047 词表）：

    pending_approval ──approve──▶ approved ──execute──▶ executing ──┬─▶ done
          │                     │                  │                   │
          ├──reject──▶ rejected │              rollback──────────────┘
          └──cancel──▶ cancelled└──cancel──▶ cancelled    │（物理删前可回滚）
                                       executing/done ──rollback──▶ rolled_back

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
from datetime import datetime, timedelta
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
    "rollback": {"executing": "rolled_back", "done": "rolled_back",
                 "failed": "rolled_back"},
}

#: staging 区根（与被治理目录同一卷，rename 原子）。
STAGING_DIR = Path("data") / ".gc-staging"

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
) -> GcPlan:
    """评估 → 候选树 → GcPlan（pending_approval）。同 digest 未终态计划复用。"""
    from app.models.data_lifecycle import LifecyclePolicy

    now = now or datetime.utcnow()
    kinds = kinds or ["lakehouse_dataset", "fabric_materialization",
                      "artifact_cache", "cog_output", "worker_cache"]
    tiers = tiers or ["cold"]
    for k in kinds:
        if k in OBSERVE_ONLY_KINDS:
            raise GcPlanError(
                f"kind '{k}' 在 V9 只支持 observe（真实删除走 lakehouse 自有 GC 保护面）"
            )

    summary = assess(db, persist=True, now=now)
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
        .filter(GcPlan.plan_digest == digest, GcPlan.status.in_(open_states))
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


def execute_plan(db: Any, plan: GcPlan, *, actor: Optional[str] = None) -> Dict[str, Any]:
    """approved → executing：按固化的 plan_tree 把对象移入 staging。

    - 文件类（cog_output / artifact_cache / fabric spill）：``move`` 进
      ``data/.gc-staging/<plan_id>/``（保留相对路径以便回滚）；
    - worker_cache：删除注册表行（fail-open 语义 = miss 重物化）；
    - 中断恢复：重新调用本函数（approved 态重入）按清单跳过已处理条目
      （幂等）—— 中断在 executing 态时先 rollback 再重新 approve 执行，
      或由 stale 清扫兜底。
    """
    transition(plan, "start_execute", actor=actor)
    db.commit()
    tree = plan.plan_tree or {}
    staged: List[Dict[str, Any]] = []
    errors: List[str] = []
    plan_dir = STAGING_DIR / plan.id
    for entry in tree.get("objects", []):
        kind = entry.get("kind")
        object_id = str(entry.get("object_id") or "")
        try:
            if kind in ("cog_output", "artifact_cache", "fabric_materialization"):
                _stage_file(kind, object_id, plan_dir, staged)
            elif kind == "worker_cache":
                _reclaim_worker_cache_row(db, object_id)
                staged.append({"kind": kind, "object_id": object_id,
                               "mode": "registry_row_deleted"})
            else:
                errors.append(f"{kind}:{object_id}: kind not executable")
        except Exception as exc:  # noqa: BLE001 — 单对象失败不中断整批
            errors.append(f"{kind}:{object_id}: {type(exc).__name__}")
    result = {
        "staged_count": len(staged),
        "staged": staged[:64],
        "errors": errors[:16],
        "ok": not errors,
        "staging_dir": str(plan_dir),
        "staging_expires_at": plan.staging_expires_at.isoformat()
        if plan.staging_expires_at else None,
    }
    transition(plan, "finish_done" if not errors else "finish_failed",
               actor=actor, result=result)
    db.commit()
    lc_metrics.observe_executed(plan.candidate_count, plan.candidate_bytes)
    return result


def _resolve_file_path(kind: str, object_id: str) -> Optional[Path]:
    from app.services.data_lifecycle.adapters import (
        _artifact_root,
        _cog_root,
        _data_root,
        _spill_root,
    )

    if kind == "artifact_cache":
        return _artifact_root() / object_id
    if kind == "cog_output":
        return _cog_root() / object_id
    if kind == "fabric_materialization":
        candidate = _spill_root() / object_id
        if candidate.exists():
            return candidate
        return _data_root() / object_id
    return None


def _stage_file(kind: str, object_id: str, plan_dir: Path,
                staged: List[Dict[str, Any]]) -> None:
    src = _resolve_file_path(kind, object_id)
    if src is None or not src.exists():
        staged.append({"kind": kind, "object_id": object_id,
                       "mode": "already_gone"})
        return
    dst = plan_dir / kind / object_id
    if dst.exists():
        staged.append({"kind": kind, "object_id": object_id,
                       "mode": "already_staged"})
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    staged.append({"kind": kind, "object_id": object_id, "mode": "moved_to_staging"})


def _reclaim_worker_cache_row(db: Any, object_id: str) -> None:
    from app.models.db_model import GeoComputeWorkerCache

    worker_id, _, cache_key = object_id.partition(":")
    row = db.query(GeoComputeWorkerCache).filter_by(
        worker_id=worker_id, cache_key=cache_key
    ).one_or_none()
    if row is not None:
        db.delete(row)
        db.commit()


def rollback_plan(db: Any, plan: GcPlan, *, actor: Optional[str] = None) -> Dict[str, Any]:
    """staging 区对象搬回原位（观察期内）；DB 行类无法回滚（诚实披露）。"""
    plan_dir = STAGING_DIR / plan.id
    restored: List[Dict[str, Any]] = []
    errors: List[str] = []
    if plan_dir.exists():
        for kind_dir in plan_dir.iterdir():
            kind = kind_dir.name
            for path in kind_dir.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(kind_dir)
                dst = _resolve_file_path(kind, str(rel))
                if dst is None:
                    errors.append(f"{kind}:{rel}: unresolvable path")
                    continue
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(dst))
                    restored.append({"kind": kind, "object_id": str(rel)})
                except OSError as exc:
                    errors.append(f"{kind}:{rel}: {exc}")
    result = {
        "restored_count": len(restored),
        "restored": restored[:64],
        "errors": errors[:16],
        "note": "worker_cache 注册表行为逻辑回收（fail-open），不参与文件回滚",
    }
    transition(plan, "rollback", actor=actor, result=result)
    db.commit()
    lc_metrics.observe_rolled_back(len(restored))
    return result


def purge_staging(plan: GcPlan) -> Dict[str, Any]:
    """观察期后物理删除 staging 区（显式调用；无后台 sweeper——部署自主调度）。"""
    plan_dir = STAGING_DIR / plan.id
    if not plan_dir.exists():
        return {"purged": False, "reason": "no staging dir"}
    shutil.rmtree(plan_dir, ignore_errors=True)
    return {"purged": True, "staging_dir": str(plan_dir)}


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
