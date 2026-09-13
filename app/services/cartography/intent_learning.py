"""Intent Learning Services（V11 W1，ADR-0161，缺口 G11）。

把意图裁决、用户反馈、配方成效从「fire-and-forget」变成**可查询、可回放、
可聚合**的学习信号。四条纪律（与 ADR-0069 同源）：

1. **先验而非证据**：本模块的任何输出只影响下一次裁决/求解的起点（排序
   先验、注入块），永不参与 verdict 计算，永不让检查跳过（writable 之前
   先保证 honest）；
2. **fail-safe 写入**：证据落库失败只记日志，绝不打断主链路（裁决/出图
   优先于记账）；
3. **有界**：查询恒带上限；信号读取按半衰期衰减（旧信号自动失权）；
4. **确定性聚合**：亲和权重是纯公式（Laplace 平滑），同状态恒同权重 ——
   规则裁决的 tie-break 语义保持可复现。

会话作用域：意图证据按 session_id 记（会话记忆维度）；配方亲和全局
（配方手艺不分项目 —— 与 ADR-0069 的 project 作用域**不同层**，后者是
项目事实，这里是引擎级手艺先验）。
"""
from __future__ import annotations

import hashlib
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.intent_learning import (
    CartoFeedbackSignal,
    CartoIntentEvidence,
    CartoRecipeAffinity,
)

logger = logging.getLogger(__name__)

#: 查询文本落库上限（回放足够，无界载荷不落库）。
QUERY_TEXT_MAX = 512

#: 单次查询返回上限（有界载荷）。
QUERY_LIMIT_MAX = 200

#: 反馈信号半衰期缺省（天）。
DEFAULT_DECAY_DAYS = 30.0

#: 亲和权重 Laplace 平滑常数（s+f=0 时恒 0；样本越多越接近 (s-f)/(s+f)）。
AFFINITY_SMOOTHING = 2.0

#: 反馈信号类型词表（W1.2 契约；闭集，新增走 ADR）。
FEEDBACK_SIGNAL_TYPES = (
    "palette_change",      # 用户换色带（对上一张配色投反对票）
    "chart_kind_change",   # 用户换图型（对图型裁决投反对票）
    "rephrase",            # 用户重发问（对澄清/裁决投反对票）
    "accept",              # 用户接受结果（正信号）
    "reject",              # 用户拒绝结果（负信号）
    "repair_success",      # 自愈修复成功（W7 归因共用存储）
    "repair_failure",      # 自愈修复失败
    "clarification_answered",  # 澄清被回答（正信号：问对了）
)

#: 信号极性（单一事实源；effective_signal_weight 按此聚合 —— 新词必须
#: 显式选边，评审 finding：不再隐式默认负向）。
FEEDBACK_SIGNAL_POLARITY: Dict[str, str] = {
    "palette_change": "negative",
    "chart_kind_change": "negative",
    "rephrase": "negative",
    "accept": "positive",
    "reject": "negative",
    "repair_success": "positive",
    "repair_failure": "negative",
    "clarification_answered": "positive",
}
assert set(FEEDBACK_SIGNAL_POLARITY) == set(FEEDBACK_SIGNAL_TYPES)


# ── 生产缝（独立短会话；与 memory_harvest 同模式）────────────────────────

_session_local_factory: Optional[Any] = None


def _get_session_local() -> Any:
    """Return the sync session factory, honoring the test override."""
    if _session_local_factory is not None:
        return _session_local_factory
    from app.core.database import SessionLocal
    return SessionLocal


def set_session_local_factory(factory: Optional[Any]) -> None:
    """Override the sync session factory (tests). Pass ``None`` to reset."""
    global _session_local_factory
    _session_local_factory = factory


def capture_intent_adjudication(
    *,
    query: str,
    intent: Any,
    session_id: Optional[str] = None,
) -> Optional[int]:
    """生产缝：MapRequestIntent 契约 → 证据行（fail-safe，绝不阻断主链路）。

    从 intent 契约抽取：task / fallback_decision / slots / confidence /
    clarification / degraded_reason / lang。DB 不可用时静默降级（日志）。
    """
    try:
        fallback_decision = getattr(intent, "fallback_decision", None) or {}
        if isinstance(fallback_decision, dict):
            matched_rules = list(fallback_decision.get("matched_rules") or [])
            fallback = bool(fallback_decision.get("fallback"))
            candidates = list(fallback_decision.get("candidates") or [])
        else:
            matched_rules = list(getattr(fallback_decision, "matched_rules", []) or [])
            fallback = bool(getattr(fallback_decision, "fallback", False))
            candidates = [
                c if isinstance(c, dict) else getattr(c, "model_dump", lambda: c)()
                for c in (getattr(fallback_decision, "candidates", []) or [])
            ]
        slots = getattr(intent, "slots", None)
        if slots is not None and not isinstance(slots, dict):
            slots = slots.model_dump()
        evidence_pack = getattr(intent, "intent_evidence", None) or {}
        if not isinstance(evidence_pack, dict):
            evidence_pack = {}
        clarification = getattr(intent, "clarification", None) or {}
        if not isinstance(clarification, dict):
            clarification = clarification.model_dump()
    except Exception as exc:  # noqa: BLE001 —— 抽取失败不影响落库其它字段
        logger.warning("intent evidence 字段抽取异常: %s", exc)
        slots, evidence_pack, clarification = {}, {}, {}

    try:
        SessionLocal = _get_session_local()
        with SessionLocal() as db:
            return record_intent_adjudication(
                db,
                query=query,
                task=getattr(intent, "task", None),
                fallback=fallback,
                matched_rules=matched_rules,
                task_candidates=candidates,
                confidence=getattr(intent, "confidence", None),
                confidence_components=(evidence_pack.get("confidence_components") or {}),
                clarification=clarification,
                degraded_reason=(getattr(intent, "degraded_reason", "") or None),
                lang=getattr(intent, "lang", None),
                source="rule",
                session_id=session_id,
            )
    except Exception as exc:  # noqa: BLE001 —— fail-safe：DB 不可用不打断主链路
        logger.warning("intent evidence capture 失败（不阻断主链路）: %s", exc)
        return None


# ── W1.1 意图证据库 ──────────────────────────────────────────────────────

def _query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:32]


def record_intent_adjudication(
    db: Session,
    *,
    query: str,
    slots: Optional[Dict[str, Any]] = None,
    task: Optional[str] = None,
    fallback: bool = False,
    matched_rules: Optional[Sequence[str]] = None,
    task_candidates: Optional[Sequence[Dict[str, Any]]] = None,
    confidence: Optional[float] = None,
    confidence_components: Optional[Dict[str, Any]] = None,
    clarification: Optional[Dict[str, Any]] = None,
    degraded_reason: Optional[str] = None,
    lang: Optional[str] = None,
    source: str = "rule",
    session_id: Optional[str] = None,
    app_version: Optional[str] = None,
) -> Optional[int]:
    """一次意图语义裁决 → 证据行（fail-safe：异常只记日志，返回 None）。"""
    try:
        row = CartoIntentEvidence(
            created_at=datetime.now(timezone.utc),
            session_id=(session_id or None),
            lang=lang,
            query_text=(query or "")[:QUERY_TEXT_MAX] if isinstance(query, str) else None,
            query_hash=_query_hash(query) if isinstance(query, str) and query else None,
            task=task,
            fallback=bool(fallback),
            matched_rules=list(matched_rules or []),
            task_candidates=list(task_candidates or []),
            slots=dict(slots or {}),
            confidence=confidence,
            confidence_components=dict(confidence_components or {}),
            clarification=dict(clarification or {}),
            degraded_reason=degraded_reason,
            source=("llm" if source == "llm" else "rule"),
            app_version=app_version,
        )
        db.add(row)
        db.commit()
        return int(row.id)
    except Exception as exc:  # noqa: BLE001 —— fail-safe（纪律 2）
        logger.warning("intent evidence 落库失败（不阻断主链路）: %s", exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def query_intent_evidence(
    db: Session,
    *,
    session_id: Optional[str] = None,
    task: Optional[str] = None,
    fallback_only: bool = False,
    since: Optional[datetime] = None,
    limit: int = 50,
) -> List[CartoIntentEvidence]:
    """证据查询（有界；支持按会话/任务/回退/时间过滤 —— 台账数据源）。"""
    limit = max(1, min(int(limit), QUERY_LIMIT_MAX))
    stmt = select(CartoIntentEvidence).order_by(CartoIntentEvidence.created_at.desc())
    if session_id:
        stmt = stmt.where(CartoIntentEvidence.session_id == session_id)
    if task:
        stmt = stmt.where(CartoIntentEvidence.task == task)
    if fallback_only:
        stmt = stmt.where(CartoIntentEvidence.fallback.is_(True))
    if since is not None:
        stmt = stmt.where(CartoIntentEvidence.created_at >= since)
    return list(db.execute(stmt.limit(limit)).scalars())


def replay_intent_adjudication(
    db: Session, evidence_id: int
) -> Dict[str, Any]:
    """回放：对落库的 query 重新裁决，与当时的证据逐字段对照。

    返回 ``{"evidence": ..., "replayed": ..., "diff": {...}}`` —— 规则引擎
    演进后是否改变既有裁决，一目了然（防「改规则悄悄改变历史语义」）。
    规则引擎抛错时 replayed 为 None 并如实记录 error。
    """
    row = db.get(CartoIntentEvidence, evidence_id)
    if row is None:
        raise ValueError(f"evidence {evidence_id} 不存在")
    from app.services.gis_harness.intent import resolve_map_request_intent

    evidence_snapshot = {
        "task": row.task,
        "fallback": row.fallback,
        "matched_rules": row.matched_rules,
        "confidence": row.confidence,
    }
    try:
        replayed_intent = resolve_map_request_intent(
            row.query_text or "", record_metrics=False
        )
        fallback_now = replayed_intent.fallback_decision or {}
        if not isinstance(fallback_now, dict):
            fallback_now = {"fallback": getattr(fallback_now, "fallback", False)}
        replayed = {
            "task": getattr(replayed_intent, "task", None),
            "fallback": bool(fallback_now.get("fallback", False)),
            "matched_rules": list(getattr(replayed_intent, "matched_rules", []) or []),
            "confidence": float(getattr(replayed_intent, "confidence", 0.0) or 0.0),
        }
    except Exception as exc:  # noqa: BLE001 —— 回放失败如实记录
        replayed = None
        return {"evidence": evidence_snapshot, "replayed": None, "diff": {}, "error": str(exc)}
    diff = {k: {"was": evidence_snapshot[k], "now": replayed[k]}
            for k in evidence_snapshot if evidence_snapshot[k] != replayed[k]}
    return {"evidence": evidence_snapshot, "replayed": replayed, "diff": diff}


# ── W1.2 反馈信号 ────────────────────────────────────────────────────────

def record_feedback_signal(
    db: Session,
    *,
    signal_type: str,
    target: str,
    weight: float = 1.0,
    decay_days: float = DEFAULT_DECAY_DAYS,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """记账一次用户反馈行为（fail-safe；词表闭集校验）。"""
    if signal_type not in FEEDBACK_SIGNAL_TYPES:
        raise ValueError(f"未知反馈信号类型: {signal_type}（词表见 FEEDBACK_SIGNAL_TYPES）")
    if weight <= 0:
        raise ValueError("weight 必须为正（衰减在读取侧做，不靠负权重）")
    try:
        row = CartoFeedbackSignal(
            created_at=datetime.now(timezone.utc),
            session_id=session_id,
            project_id=project_id,
            signal_type=signal_type,
            weight=float(weight),
            decay_days=max(0.0, float(decay_days)),
            target=target[:128],
            payload=dict(payload or {}),
        )
        db.add(row)
        db.commit()
        return int(row.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("feedback signal 落库失败（不阻断主链路）: %s", exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def signal_decay_factor(age_days: float, decay_days: float) -> float:
    """半衰期衰减因子：0.5^(age/decay)；decay<=0 视为不衰减（恒 1）。"""
    if decay_days <= 0:
        return 1.0
    return math.pow(0.5, max(0.0, age_days) / decay_days)


def _as_aware(dt: datetime) -> datetime:
    """SQLite 读回的 naive datetime 按 UTC 补齐（仓库统一 UTC 语义）。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def effective_signal_weight(
    db: Session,
    *,
    target: str,
    now: Optional[datetime] = None,
    signal_types: Optional[Sequence[str]] = None,
) -> Dict[str, float]:
    """目标的衰减后信号强度：正/负信号分开聚合（读取侧衰减）。"""
    now = now or datetime.now(timezone.utc)
    stmt = select(CartoFeedbackSignal).where(CartoFeedbackSignal.target == target)
    if signal_types:
        stmt = stmt.where(CartoFeedbackSignal.signal_type.in_(list(signal_types)))
    positive = 0.0
    negative = 0.0
    for row in db.execute(stmt.limit(QUERY_LIMIT_MAX)).scalars():
        age_days = max(0.0, (now - _as_aware(row.created_at)).total_seconds() / 86400.0)
        factor = signal_decay_factor(age_days, row.decay_days)
        weighted = row.weight * factor
        polarity = FEEDBACK_SIGNAL_POLARITY.get(row.signal_type, "negative")
        if polarity == "positive":
            positive += weighted
        else:
            negative += weighted
    return {"positive": round(positive, 6), "negative": round(negative, 6)}


# ── W1.3 配方亲和（fallback 链学习）─────────────────────────────────────

def recipe_affinity_weight(weight_tuple: tuple[int, int]) -> float:
    """Laplace 平滑亲和权重（纯函数，确定性）：(s-f)/(s+f+2) ∈ (-1, 1)。"""
    success, failure = weight_tuple
    return (success - failure) / (success + failure + AFFINITY_SMOOTHING)


def record_recipe_outcome(
    db: Session, *, recipe_id: str, success: bool
) -> Dict[str, Any]:
    """配方成效记账（upsert 计数 + 重算权重；确定性公式）。"""
    row = db.get(CartoRecipeAffinity, recipe_id)
    if row is None:
        # 显式初始化（flush 前读不到列默认值；权重公式要求 int 而非 None）
        row = CartoRecipeAffinity(
            recipe_id=recipe_id, success_count=0, failure_count=0, weight=0.0,
        )
        db.add(row)
    if success:
        row.success_count = int(row.success_count or 0) + 1
    else:
        row.failure_count = int(row.failure_count or 0) + 1
    row.weight = recipe_affinity_weight((row.success_count, row.failure_count))
    row.last_outcome = "success" if success else "failure"
    row.last_updated = datetime.now(timezone.utc)
    db.commit()
    return {
        "recipe_id": recipe_id,
        "success_count": row.success_count,
        "failure_count": row.failure_count,
        "weight": row.weight,
        "last_outcome": row.last_outcome,
    }


def recipe_affinity_weights(
    db: Session, recipe_ids: Iterable[str]
) -> Dict[str, float]:
    """查询一批配方的学习权重（未登记的配方 → 冷启动默认 0.0 中性）。"""
    ids = [r for r in dict.fromkeys(recipe_ids) if r]
    if not ids:
        return {}
    rows = db.execute(
        select(CartoRecipeAffinity).where(CartoRecipeAffinity.recipe_id.in_(ids))
    ).scalars()
    learned: Dict[str, float] = {}
    cold_start: Dict[str, float] = {}
    for r in rows:
        counts = int(r.success_count or 0) + int(r.failure_count or 0)
        if counts > 0:
            learned[r.recipe_id] = float(r.weight)
        else:
            # 零样本行：权重无据 → 冷启动先验（否则该列是死字段，两处默认
            # 可能分歧 —— 评审 finding）。
            cold_start[r.recipe_id] = float(r.cold_start_weight or 0.0)
    return {
        rid: learned.get(rid, cold_start.get(rid, 0.0))
        for rid in ids
    }


def recipe_affinity_weights_sync(recipe_ids: Iterable[str]) -> Optional[Dict[str, float]]:
    """会话自管的亲和权重读取（公共缝；planner 等跨模块调用方使用）。

    独立短会话 + fail-safe：DB 不可用 → None（调用方退回无先验）。
    取代跨模块 import 私有 seam ``_get_session_local`` 的旧形态。
    """
    ids = [r for r in dict.fromkeys(recipe_ids) if r]
    if not ids:
        return {}
    try:
        SessionLocal = _get_session_local()
        with SessionLocal() as db:
            return recipe_affinity_weights(db, ids)
    except Exception:  # noqa: BLE001 —— 先验不可得 ≠ 失败，诚实退回 None
        return None


def reorder_by_affinity(
    db: Session, candidates: Sequence[str]
) -> List[str]:
    """按亲和权重稳定重排候选（权重同值保持原序 —— tie-break 契约）。

    只允许**重排**，不允许增删候选：规则裁决的可行集不变，先验只改道。
    """
    weights = recipe_affinity_weights(db, candidates)
    ordered = sorted(
        enumerate(candidates), key=lambda t: (-weights.get(t[1], 0.0), t[0])
    )
    return [candidate for _, candidate in ordered]


# ── W1.6 澄清台账 ────────────────────────────────────────────────────────

def clarification_metrics(
    db: Session,
    *,
    since: Optional[datetime] = None,
    limit: int = QUERY_LIMIT_MAX,
) -> Dict[str, Any]:
    """澄清命中率与误触发率台账（证据库聚合）。

    - ``clarified``：裁决带澄清请求的证据行数；
    - ``clarify_hit``：其中用户给予了回答（clarification.answers 非空）；
    - ``hit_rate`` = hit / clarified（无澄清 → 1.0 语义：未误触）；
    - ``false_trigger_rate`` = clarified / total（澄清占比 —— 越低越不打扰）。
    """
    rows = query_intent_evidence(db, since=since, limit=limit)
    total = len(rows)
    clarified = [r for r in rows if (r.clarification or {}).get("request")]
    hit = [
        r for r in clarified
        if (r.clarification or {}).get("answers")
    ]
    return {
        "total": total,
        "clarified": len(clarified),
        "clarify_hit": len(hit),
        "hit_rate": (len(hit) / len(clarified)) if clarified else 1.0,
        "false_trigger_rate": (len(clarified) / total) if total else 0.0,
    }
