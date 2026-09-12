"""Data Lifecycle V9 —— 统一生命周期策略引擎（P3，ADR-0140 核心）。

五类对象 → 统一登记 → 分级（hot/warm/cold）→ 到期判定 → GC 计划（dry-run/
审批/执行/回滚，P5）→ 复活（引用即预热）。

**安全不动点（行为保持）**：默认策略对五类对象全部 ``observe`` —— 引擎
只登记与分级，不改变任何既有机制的行为（lakehouse retention、spill TTL、
artifact LRU、coordinator purge、COG 现状无清理）。任何删除动作必须：
运营显式启用策略 → GC 计划 dry-run → 审批 → 执行（P5）→ staging 观察期
→ 物理删。等价性验证记录见 ADR-0140 附录与 tests/data/test_lifecycle_policy_v9.py。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.data_lifecycle import metrics as lc_metrics
from app.services.data_lifecycle.adapters import enumerate_all

logger = logging.getLogger(__name__)

#: 默认分级阈值（秒）：24h 未用 → warm；7d 未用 → cold。
DEFAULT_TIER_THRESHOLDS = {"warm_after_s": 86_400, "cold_after_s": 604_800}

#: 单次 assess 的 upsert 总帽（防御巨型目录把事务拖爆）。
MAX_UPSERTS_PER_ASSESS = 2000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def default_policies() -> List[Dict[str, Any]]:
    """五类对象的默认策略（全部 observe = 行为等价现状）。"""
    kinds = ("lakehouse_dataset", "fabric_materialization", "artifact_cache",
             "cog_output", "worker_cache")
    return [
        {
            "name": f"default.{kind}",
            "kind": kind,
            "action": "observe",
            "tier_thresholds": dict(DEFAULT_TIER_THRESHOLDS),
            "staging_hours": 72,
            "enabled": False,
            "params": {},
        }
        for kind in kinds
    ]


def classify_tier(
    last_used_at: Optional[datetime],
    thresholds: Optional[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> str:
    """last_used → hot/warm/cold（无时间证据 → hot：不冤枉无证据对象）。

    时间统一到 aware-UTC 再相减（DB 行是 naive、文件 mtime 是 aware，混用
    是常态）。
    """
    if last_used_at is None:
        return "hot"
    th = thresholds or DEFAULT_TIER_THRESHOLDS
    now = now or datetime.now(timezone.utc)
    try:
        ref = last_used_at if last_used_at.tzinfo else last_used_at.replace(tzinfo=timezone.utc)
        ref = ref.astimezone(timezone.utc)
        now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        age_s = (now_aware - ref).total_seconds()
    except (TypeError, ValueError, OSError):
        return "hot"
    if age_s >= float(th.get("cold_after_s", DEFAULT_TIER_THRESHOLDS["cold_after_s"])):
        return "cold"
    if age_s >= float(th.get("warm_after_s", DEFAULT_TIER_THRESHOLDS["warm_after_s"])):
        return "warm"
    return "hot"


def ensure_defaults(db: Any) -> int:
    """默认策略种子（幂等；按 name 缺失补种）。返回新建行数。"""
    from app.models.data_lifecycle import LifecyclePolicy

    created = 0
    for spec in default_policies():
        exists = db.query(LifecyclePolicy).filter(
            LifecyclePolicy.name == spec["name"]
        ).one_or_none()
        if exists is not None:
            continue
        db.add(LifecyclePolicy(
            name=spec["name"],
            kind=spec["kind"],
            action=spec["action"],
            tier_thresholds=spec["tier_thresholds"],
            staging_hours=spec["staging_hours"],
            enabled=spec["enabled"],
            params=spec["params"],
        ))
        created += 1
    db.commit()
    return created


def _policy_map(db: Any) -> Dict[str, Any]:
    from app.models.data_lifecycle import LifecyclePolicy

    # enabled 策略后写覆盖同 kind 的默认（确定序：enabled asc → created asc）
    rows = db.query(LifecyclePolicy).order_by(
        LifecyclePolicy.enabled.asc(), LifecyclePolicy.created_at.asc()
    ).all()
    return {p.kind: p for p in rows}


def _tier_of(policy: Any, obj: Any) -> str:
    th = getattr(policy, "tier_thresholds", None) or DEFAULT_TIER_THRESHOLDS
    return classify_tier(obj.last_used_at, th)


def plan_digest_of(tree: Dict[str, Any]) -> str:
    canonical = json.dumps(tree, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def assess(
    db: Any,
    *,
    persist: bool = True,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """全景评估：枚举五机制 → upsert 登记表 → 分级 → 动作判定（只读安全）。

    返回 per-kind 统计（对象数/字节/分级分布/候选与原因）—— 报告本身
    就是 ``GET /data-lifecycle/assess`` 的响应体与 GC dry-run 的输入树。
    """

    # now 归一为 naive-UTC（DB DateTime 无 tz；classify_tier 内部自行处理混合时区）
    if now is not None and now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    ensure_defaults(db)
    policies = _policy_map(db)
    reports = enumerate_all(db)
    now = now or _utcnow()

    per_kind: Dict[str, Any] = {}
    upserted = 0
    for kind, report in reports.items():
        policy = policies.get(kind)
        thresholds = getattr(policy, "tier_thresholds", None) or DEFAULT_TIER_THRESHOLDS
        action = getattr(policy, "action", "observe") if policy is not None else "observe"
        tiers = {"hot": 0, "warm": 0, "cold": 0}
        candidates: List[Dict[str, Any]] = []
        total_bytes = 0
        for obj in report.objects[:MAX_UPSERTS_PER_ASSESS]:
            tier = classify_tier(obj.last_used_at, thresholds, now=now)
            tiers[tier] = tiers.get(tier, 0) + 1
            total_bytes += int(obj.byte_size or 0)
            due = (
                action in ("stage_delete", "delete")
                and getattr(policy, "enabled", False)
                and tier == "cold"
            )
            if due:
                reason = f"tier=cold,action={action},policy={getattr(policy, 'name', '?')}"
                candidates.append({
                    "kind": kind,
                    "object_id": obj.object_id,
                    "owner_scope": obj.owner_scope,
                    "byte_size": int(obj.byte_size or 0),
                    "tier": tier,
                    "reason": reason,
                    "last_used_at": obj.last_used_at.isoformat() if obj.last_used_at else None,
                })
            if persist and upserted < MAX_UPSERTS_PER_ASSESS:
                _upsert_object(db, obj, tier, now)
                upserted += 1
        if persist:
            db.commit()
        per_kind[kind] = {
            "available": report.available,
            "diagnostics": report.diagnostics[:8],
            "action": action,
            "policy_enabled": bool(getattr(policy, "enabled", False)) if policy else False,
            "policy_name": getattr(policy, "name", None) if policy else None,
            "total": len(report.objects),
            "total_bytes": total_bytes,
            "tiers": tiers,
            "candidates": candidates[:64],
            "candidate_count": len(candidates),
            "candidate_bytes": sum(c["byte_size"] for c in candidates),
        }
        lc_metrics.observe_assess(kind, len(report.objects), total_bytes,
                                  len(candidates))
    summary = {
        "assessed_at": now.isoformat(),
        "kinds": per_kind,
        "total_objects": sum(v["total"] for v in per_kind.values()),
        "total_bytes": sum(v["total_bytes"] for v in per_kind.values()),
        "candidate_count": sum(v["candidate_count"] for v in per_kind.values()),
        "candidate_bytes": sum(v["candidate_bytes"] for v in per_kind.values()),
    }
    summary["plan_digest"] = plan_digest_of(summary)
    return summary


def _upsert_object(db: Any, obj: Any, tier: str, now: datetime) -> None:
    from app.models.data_lifecycle import LifecycleObject

    row = db.query(LifecycleObject).filter_by(
        kind=obj.kind, object_id=obj.object_id
    ).one_or_none()
    if row is None:
        row = LifecycleObject(
            kind=obj.kind,
            object_id=obj.object_id,
            owner_scope=obj.owner_scope[:128],
            byte_size=int(obj.byte_size or 0),
            tier=tier,
            last_used_at=obj.last_used_at,
            first_seen_at=now,
            updated_at=now,
            info=obj.bounded_info(),
        )
        db.add(row)
        return
    row.tier = tier
    row.byte_size = int(obj.byte_size or 0)
    row.last_used_at = obj.last_used_at or row.last_used_at
    row.info = obj.bounded_info()
    row.updated_at = now


# ── 复活（引用即预热） ───────────────────────────────────────────────


def revive_object(db: Any, kind: str, object_id: str) -> bool:
    """对象被引用（命中）→ last_used 刷新 + tier 回 hot（引用即预热）。

    各机制在命中路径调用（当前接线：assess 的 next touch）；返回是否命中。
    """
    from app.models.data_lifecycle import LifecycleObject

    row = db.query(LifecycleObject).filter_by(
        kind=kind, object_id=object_id
    ).one_or_none()
    if row is None:
        return False
    row.last_used_at = _utcnow()
    row.tier = "hot"
    row.updated_at = row.last_used_at
    db.commit()
    lc_metrics.observe_revive(kind)
    return True


__all__ = [
    "DEFAULT_TIER_THRESHOLDS",
    "default_policies",
    "classify_tier",
    "ensure_defaults",
    "assess",
    "revive_object",
    "plan_digest_of",
]
