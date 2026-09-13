"""Self-Heal Policy — 自愈策略库与修复效果归因（V11 W7.2/7.3，ADR-0167）。

任务书：
- W7.2「自愈策略学习：``selfheal_actions``（现接线仅 1 处）扩为策略库：
  记录「上下文 → 动作 → 效果」，按历史成功率排序候选；与 W1 的反馈信号
  **共用存储**」；
- W7.3「修复效果归因：每次修复记录 ``expected_effect`` 与 ``actual_delta``，
  形成归因表；无效动作自动降权」。

设计（与 W1 纪律同源）：

- **存储复用**：全部落 ``carto_feedback_signals``（迁移 0057）——
  ``target = "selfheal:{action_id}"``、``signal_type = repair_success /
  repair_failure``、``payload = {context, expected_effect, actual_delta,
  surface, risk}``。不新建表（W1 承诺的「共用存储」兑付）；
- **成功率表**：按动作聚合（计数 + 半衰期衰减后的有效权重），确定性排序
  （成功率降序 → 样本数降序 → action_id 字典序）；
- **归因表**：逐条 (expected, actual) 对 + 判定（effective = actual ≥
  expected × 0.5，保守半量线；ineffective 自动记一次负信号 → 降权）；
- **fail-safe**：写入异常不阻断修复主链路（与 intent_learning 同纪律）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.intent_learning import CartoFeedbackSignal

#: 归因判定线：actual ≥ expected × 0.5 视为有效（保守半量线，首轮 provisional）。
EFFECTIVE_RATIO = 0.5


def record_repair_outcome(
    db: Session,
    *,
    action_id: str,
    context: str,
    expected_effect: float,
    actual_delta: float,
    success: bool,
    surface: str = "",
    risk: str = "",
    decay_days: float = 30.0,
) -> Optional[int]:
    """记一条修复成效（成功/失败 + 归因对；fail-safe）。"""
    try:
        row = CartoFeedbackSignal(
            created_at=datetime.now(timezone.utc),
            signal_type="repair_success" if success else "repair_failure",
            weight=1.0,
            decay_days=decay_days,
            target=f"selfheal:{action_id}"[:128],
            payload={
                "context": context[:240],
                "expected_effect": float(expected_effect),
                "actual_delta": float(actual_delta),
                "surface": surface,
                "risk": risk,
            },
        )
        db.add(row)
        db.commit()
        return int(row.id)
    except Exception:  # noqa: BLE001 —— 记账失败不阻断修复主链路
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def attribution_rows(
    db: Session, *, limit: int = 100,
) -> List[Dict[str, Any]]:
    """归因表：逐条 (context, action, expected, actual, verdict)。

    ``verdict = effective``（actual ≥ expected×0.5）/ ``ineffective``（否则）
    —— ineffective 是 W7.3「自动降权」的信号源。
    """
    rows = db.execute(
        select(CartoFeedbackSignal)
        .where(CartoFeedbackSignal.target.like("selfheal:%"))
        .order_by(CartoFeedbackSignal.created_at.desc())
        .limit(max(1, min(limit, 500)))
    ).scalars()
    out: List[Dict[str, Any]] = []
    for r in rows:
        payload = r.payload or {}
        expected = float(payload.get("expected_effect") or 0.0)
        actual = float(payload.get("actual_delta") or 0.0)
        effective = actual >= expected * EFFECTIVE_RATIO
        out.append({
            "actionId": r.target.split(":", 1)[1] if ":" in r.target else r.target,
            "context": payload.get("context", ""),
            "expectedEffect": expected,
            "actualDelta": actual,
            "success": r.signal_type == "repair_success",
            "verdict": "effective" if effective else "ineffective",
            "createdAt": r.created_at.isoformat() if r.created_at else "",
        })
    return out


def action_success_table(db: Session) -> List[Dict[str, Any]]:
    """按动作的成功率表（计数 + 衰减有效权重；确定性排序）。

    排序：成功率降序 → 样本数降序 → action_id 字典序。
    """
    from app.services.cartography.intent_learning import (
        effective_signal_weight,
    )

    rows = list(db.execute(
        select(CartoFeedbackSignal)
        .where(CartoFeedbackSignal.target.like("selfheal:%"))
        .limit(1000)
    ).scalars())
    now = datetime.now(timezone.utc)
    by_action: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        action = r.target.split(":", 1)[1] if ":" in r.target else r.target
        entry = by_action.setdefault(action, {
            "actionId": action, "success": 0, "failure": 0,
            "expectedSum": 0.0, "actualSum": 0.0,
        })
        if r.signal_type == "repair_success":
            entry["success"] += 1
        else:
            entry["failure"] += 1
        payload = r.payload or {}
        entry["expectedSum"] += float(payload.get("expected_effect") or 0.0)
        entry["actualSum"] += float(payload.get("actual_delta") or 0.0)
    table: List[Dict[str, Any]] = []
    for action, entry in by_action.items():
        samples = entry["success"] + entry["failure"]
        entry["samples"] = samples
        entry["successRate"] = round(entry["success"] / samples, 4) if samples else 0.0
        entry["meanExpected"] = round(entry["expectedSum"] / samples, 4) if samples else 0.0
        entry["meanActual"] = round(entry["actualSum"] / samples, 4) if samples else 0.0
        eff = effective_signal_weight(db, target=f"selfheal:{action}", now=now)
        entry["effectiveWeight"] = round(eff["positive"] - eff["negative"], 4)
        del entry["expectedSum"], entry["actualSum"]
        table.append(entry)
    table.sort(key=lambda e: (-e["successRate"], -e["samples"], e["actionId"]))
    return table


def rank_actions_by_history(
    db: Session, candidates: Sequence[str],
) -> List[str]:
    """按历史成功率重排候选动作（先验只重排，不增删候选 —— 与 W1 同纪律）。

    无历史（冷启动）保持原序；成功率并列按 action_id 稳定序。
    """
    table = {e["actionId"]: e for e in action_success_table(db)}
    indexed = list(enumerate(candidates))
    indexed.sort(key=lambda t: (
        -table.get(t[1], {}).get("successRate", -1.0),
        t[0],
    ))
    return [c for _, c in indexed]


__all__ = [
    "EFFECTIVE_RATIO",
    "record_repair_outcome",
    "attribution_rows",
    "action_success_table",
    "rank_actions_by_history",
]
