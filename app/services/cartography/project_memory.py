"""项目级制图事实账本（ADR-0069 / cartographic-quality-rules-and-memory-spec P2）。

纯同步 SQLAlchemy 服务：账本的写入点在评审结论产生**之后**（gate 通过、
用户修正、recipe 完成），所以记忆永远滞后于证据一个身位，不可能被用来短路
评审（ADR-0069 决策 2）。

三条铁律在本模块内实现：

1. 作用域：所有查询恒带 ``project_id`` 谓词，无跨项目复用（决策 1）；
2. 冲突：``shared_classification`` 指纹不一致时转 ``conflicted``，绝不静默
   覆盖；只有显式升级（``supersede=True``，调用方已确认新方案自身评审通过）
   才改写（决策 3）；
3. 有界：每项目 ``MAX_FACTS_PER_PROJECT`` 条上限，按 ``last_verified_at``
   LRU 淘汰；渲染块有字符预算（决策 4）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project import CartoProjectFact

logger = logging.getLogger(__name__)

#: 每项目事实条数上限（LRU 淘汰最旧的 last_verified_at）。
MAX_FACTS_PER_PROJECT = 200

#: 注入块字符预算，与 [CARTOGRAPHY_VERDICT] 块同量级（决策 4）。
MEMORY_BLOCK_CHAR_BUDGET = 1200

#: 注入块标记：与 [CARTOGRAPHY_VERDICT] 同款式，便于前端/日志识别。
MEMORY_MARKER = "CARTOGRAPHY_MEMORY"

FACT_KINDS = ("preference", "recipe_outcome", "data_profile", "shared_classification")
FACT_STATUSES = ("active", "stale", "conflicted", "retired")

#: 注入优先级：共享分类方案 > 偏好 > recipe 成效（data_profile 不注入——
#: 它是漂移判定的锚点，不是作图起点的先验）。
_INJECT_KINDS = ("shared_classification", "preference", "recipe_outcome")


def classification_fingerprint(payload: Dict[str, Any]) -> str:
    """分类方案指纹：仅由**分类语义**决定（断点/类别键/类数），不含颜色。

    颜色是呈现，换色带不应让共享方案失配（Phase 1 的 change_palette 修复
    正是只换颜色）。断点/类别变了才是另一个方案。
    """
    if not isinstance(payload, dict):
        payload = {}
    semantic = {
        "type": payload.get("type"),
        "field": payload.get("field"),
        "breaks": payload.get("breaks"),
        "categories": sorted(
            str(key) for key in (payload.get("category_keys") or [])
        ) or None,
        "class_count": payload.get("class_count"),
    }
    blob = json.dumps(
        semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _evict_if_needed(db: Session, project_id: str) -> int:
    """把项目事实数压回上限内，淘汰最旧的 ``last_verified_at``。"""
    total = db.query(CartoProjectFact).filter(
        CartoProjectFact.project_id == project_id
    ).count()
    overflow = total - MAX_FACTS_PER_PROJECT
    if overflow <= 0:
        return 0
    victims = (
        db.query(CartoProjectFact)
        .filter(CartoProjectFact.project_id == project_id)
        .order_by(CartoProjectFact.last_verified_at.asc())
        .limit(overflow)
        .all()
    )
    for victim in victims:
        db.delete(victim)
    return len(victims)


def record_fact(
    db: Session,
    project_id: str,
    kind: str,
    subject: str,
    payload: Dict[str, Any],
    *,
    fingerprint: Optional[str] = None,
    validity_tier: Optional[str] = None,
    evidence_digest: Optional[str] = None,
    supersede: bool = False,
) -> Optional[CartoProjectFact]:
    """Upsert 一条项目制图事实，返回落库后的行（非法 kind 返回 None）。

    冲突语义（决策 3）：已存在同 ``(kind, subject)`` 的 active 事实且
    ``fingerprint`` 不一致时——

    - ``supersede=False``（默认）：**不覆盖**，把既有事实标为 ``conflicted``
      并在其 payload 记录分歧摘要，由用户/下一轮修复裁决；
    - ``supersede=True``：调用方已确认新方案自身评审通过，显式升级为新的
      active 事实。

    调用方负责事务提交/回滚（与既有 project_service 同款约定）。
    """
    if kind not in FACT_KINDS:
        logger.warning("[CartoMemory] rejected unknown fact kind %r", kind)
        return None
    if not project_id or not subject:
        return None

    existing = db.execute(
        select(CartoProjectFact).where(
            CartoProjectFact.project_id == project_id,
            CartoProjectFact.kind == kind,
            CartoProjectFact.subject == subject,
        )
    ).scalar_one_or_none()

    if existing is None:
        fact = CartoProjectFact(
            project_id=project_id,
            kind=kind,
            subject=subject,
            payload=dict(payload or {}),
            fingerprint=fingerprint,
            validity_tier=validity_tier,
            evidence_digest=evidence_digest,
            status="active",
            created_at=_now(),
            last_verified_at=_now(),
        )
        db.add(fact)
        db.flush()
        _evict_if_needed(db, project_id)
        return fact

    conflicting = (
        existing.status == "active"
        and existing.fingerprint is not None
        and fingerprint is not None
        and existing.fingerprint != fingerprint
    )
    if conflicting and not supersede:
        # 显式化分歧：既有事实不再注入（conflicted 不进注入集），但保留可审计。
        existing.status = "conflicted"
        existing.payload = {
            **(existing.payload or {}),
            "_conflict": {
                "held_fingerprint": existing.fingerprint,
                "incoming_fingerprint": fingerprint,
                "detected_at": _now().isoformat(),
            },
        }
        existing.last_verified_at = _now()
        db.flush()
        logger.info(
            "[CartoMemory] conflict on %s/%s (project=%s): held=%s incoming=%s",
            kind, subject, project_id, existing.fingerprint, fingerprint,
        )
        return existing

    existing.payload = dict(payload or {})
    existing.fingerprint = fingerprint
    existing.validity_tier = validity_tier
    existing.evidence_digest = evidence_digest
    existing.status = "active"
    existing.last_verified_at = _now()
    db.flush()
    _evict_if_needed(db, project_id)
    return existing


def get_active_facts(
    db: Session,
    project_id: str,
    kinds: Optional[Sequence[str]] = None,
    limit: int = 40,
) -> List[CartoProjectFact]:
    """项目的 active 事实（最近验证优先）。``stale``/``conflicted`` 永不返回。"""
    if not project_id:
        return []
    stmt = select(CartoProjectFact).where(
        CartoProjectFact.project_id == project_id,
        CartoProjectFact.status == "active",
    )
    if kinds:
        stmt = stmt.where(CartoProjectFact.kind.in_(list(kinds)))
    stmt = stmt.order_by(CartoProjectFact.last_verified_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())


def get_shared_classification(
    db: Session, project_id: str, subject: str
) -> Optional[CartoProjectFact]:
    """项目内某主题字段的共享分类方案（仅 active）。"""
    if not project_id or not subject:
        return None
    return db.execute(
        select(CartoProjectFact).where(
            CartoProjectFact.project_id == project_id,
            CartoProjectFact.kind == "shared_classification",
            CartoProjectFact.subject == subject,
            CartoProjectFact.status == "active",
        )
    ).scalar_one_or_none()


def mark_stale(
    db: Session,
    project_id: str,
    kinds: Optional[Sequence[str]] = None,
    subjects: Optional[Sequence[str]] = None,
) -> int:
    """把匹配的 active 事实标为 ``stale``（Phase 3 的环境事件入口）。

    返回失效条数。``stale`` 事实不注入，但保留证据以便复验后重新激活。
    """
    if not project_id:
        return 0
    stmt = select(CartoProjectFact).where(
        CartoProjectFact.project_id == project_id,
        CartoProjectFact.status == "active",
    )
    if kinds:
        stmt = stmt.where(CartoProjectFact.kind.in_(list(kinds)))
    if subjects:
        stmt = stmt.where(CartoProjectFact.subject.in_(list(subjects)))
    facts = list(db.execute(stmt).scalars().all())
    for fact in facts:
        fact.status = "stale"
    if facts:
        db.flush()
    return len(facts)


def _render_fact(fact: CartoProjectFact) -> Optional[str]:
    payload = fact.payload if isinstance(fact.payload, dict) else {}
    if fact.kind == "shared_classification":
        breaks = payload.get("breaks")
        detail = (
            f"断点 {breaks}" if isinstance(breaks, list) and breaks
            else f"类别 {payload.get('class_count')} 类"
        )
        return (
            f"- 共享分类方案 · 字段 {fact.subject}: {payload.get('type', '?')}，"
            f"{detail}（系列图请复用以保证跨图可比）"
        )
    if fact.kind == "preference":
        value = payload.get("value")
        if value is None:
            return None
        return f"- 项目偏好 · {fact.subject}: {value}"
    if fact.kind == "recipe_outcome":
        tier = fact.validity_tier or "?"
        return f"- recipe 成效 · {fact.subject}: 上次达到 {tier}"
    return None


def render_memory_block(
    facts: Sequence[CartoProjectFact],
    *,
    char_budget: int = MEMORY_BLOCK_CHAR_BUDGET,
) -> str:
    """把 active 事实渲染为有界的 ``[CARTOGRAPHY_MEMORY]`` 块。

    注入的是**先验而非证据**（ADR-0069 决策 2）——块内文字明确声明这一点，
    以免模型把"上次验证过"当成本次的通行证。空事实集返回空串（不注入空块）。
    """
    if not facts:
        return ""
    ordered: List[CartoProjectFact] = []
    for kind in _INJECT_KINDS:
        ordered.extend(f for f in facts if f.kind == kind)
    lines: List[str] = []
    for fact in ordered:
        rendered = _render_fact(fact)
        if rendered:
            lines.append(rendered)
    if not lines:
        return ""

    header = (
        f"[{MEMORY_MARKER}] 本项目已确认的制图先验（作图起点，不是本次的"
        f"评审结论——每张图仍须自证）：\n"
    )
    ellipsis = "- …（更多项目记忆已按预算省略）"
    body: List[str] = []
    used = len(header)
    for index, line in enumerate(lines):
        # 预算必须为省略行本身留出位置（除最后一行外），否则截断反而超预算。
        reserve = len(ellipsis) + 1 if index < len(lines) - 1 else 0
        if used + len(line) + 1 + reserve > char_budget:
            body.append(ellipsis)
            break
        body.append(line)
        used += len(line) + 1
    return header + "\n".join(body) + "\n"


def harvest_facts_from_review(
    db: Session,
    project_id: str,
    mapspec: Dict[str, Any],
    review: Optional[Dict[str, Any]],
) -> int:
    """从一次**已通过**的制图评审收割共享分类方案事实，返回写入条数。

    ADR-0069 决策 2 的落点：只在 gate 已经给出结论之后写入，所以记忆永远
    滞后于证据一个身位。未通过/证据不足的评审不写入——不把不确定的分类
    方案变成下一张图的先验。

    ``review`` 形态是 ``_cartographic_review``（``{cartography: {...},
    overall_passed: bool}``）或 desired-state loop 的 ``to_dict()``。
    """
    if not project_id or not isinstance(mapspec, dict):
        return 0
    if not _review_is_trustworthy(review):
        return 0
    tier = _review_tier(review)
    written = 0
    for layer in mapspec.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        legend_spec = layer.get("legend_spec")
        if not isinstance(legend_spec, dict):
            continue
        field = legend_spec.get("field")
        if not isinstance(field, str) or not field:
            continue
        payload = _classification_payload(legend_spec)
        fingerprint = classification_fingerprint(payload)
        fact = record_fact(
            db, project_id, "shared_classification", field, payload,
            fingerprint=fingerprint, validity_tier=tier,
        )
        if fact is not None:
            written += 1
    return written


def _review_is_trustworthy(review: Optional[Dict[str, Any]]) -> bool:
    """只有明确通过的评审可以产出先验（fail-closed：缺证据即不写）。"""
    if not isinstance(review, dict):
        return False
    if review.get("overall_passed") is True:
        return True
    cartography = review.get("cartography")
    status = (
        cartography.get("status") if isinstance(cartography, dict)
        else review.get("status")
    )
    return status in ("passed", "passed_with_warnings")


def _review_tier(review: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(review, dict):
        return None
    cartography = review.get("cartography")
    if isinstance(cartography, dict):
        tier = cartography.get("validity") or cartography.get("status")
        if isinstance(tier, str):
            return tier
    status = review.get("status")
    return status if isinstance(status, str) else None


def _classification_payload(legend_spec: Dict[str, Any]) -> Dict[str, Any]:
    """从 legend_spec 提取**分类语义**投影（不含颜色，见指纹说明）。"""
    categories = legend_spec.get("categories")
    category_keys = [
        str(item.get("key")) for item in categories
        if isinstance(item, dict) and item.get("key") is not None
    ] if isinstance(categories, list) else []
    breaks = legend_spec.get("breaks")
    payload: Dict[str, Any] = {
        "type": legend_spec.get("type"),
        "field": legend_spec.get("field"),
    }
    if isinstance(breaks, list) and breaks:
        payload["breaks"] = breaks
        payload["class_count"] = max(len(breaks) - 1, 1)
    if category_keys:
        payload["category_keys"] = category_keys
        payload["class_count"] = len(category_keys)
    for key in ("min", "max"):
        if legend_spec.get(key) is not None:
            payload[key] = legend_spec[key]
    return payload


def apply_distribution_drift(
    db: Session,
    project_id: str,
    field_profiles: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """按新的字段分布证据失效过期先验，返回环境事件列表（spec P3）。

    对每个主题字段：把账本里 ``data_profile`` 记住的分布证据与新 profile 比较——

    - 漂移：该字段的 ``shared_classification`` 与 ``data_profile`` 转 ``stale``
      （断点是分布的函数，分布变了断点就不再成立），并产出一条环境事件；
    - 稳定：刷新 ``last_verified_at``（先验重新确认，LRU 上也更年轻）；
    - 证据不足（无分位）：**什么都不做**——不可判定不等于未漂移
      （fail-closed）。

    新字段（账本还没记过分布）只记录基线，不产出事件：第一次见到不是变化。
    调用方负责提交。
    """
    if not project_id or not isinstance(field_profiles, dict):
        return []
    from app.services.cartography.distribution_drift import (
        detect_distribution_drift,
        distribution_evidence,
    )

    events: List[Dict[str, Any]] = []
    for field, profile in field_profiles.items():
        incoming = distribution_evidence(profile)
        if incoming is None:
            continue
        held_fact = db.execute(
            select(CartoProjectFact).where(
                CartoProjectFact.project_id == project_id,
                CartoProjectFact.kind == "data_profile",
                CartoProjectFact.subject == str(field),
            )
        ).scalar_one_or_none()
        if held_fact is None:
            record_fact(
                db, project_id, "data_profile", str(field), incoming,
                fingerprint=None,
            )
            continue
        verdict = detect_distribution_drift(held_fact.payload, incoming)
        if not verdict.get("evaluated"):
            continue
        if not verdict.get("drifted"):
            held_fact.payload = incoming
            held_fact.status = "active"
            held_fact.last_verified_at = _now()
            db.flush()
            continue
        # 漂移：断点先验过期。保留新证据作为基线，但标记为 stale —— 复验
        # （用户/下一轮重新分类并通过 gate）才会重新 active。
        held_fact.payload = incoming
        held_fact.status = "stale"
        held_fact.last_verified_at = _now()
        stale_count = mark_stale(
            db, project_id, kinds=["shared_classification"], subjects=[str(field)],
        )
        db.flush()
        event = {
            "subject": str(field),
            "reason": verdict.get("reason"),
            "deviation": verdict.get("max_relative_deviation"),
            "invalidated_classifications": stale_count,
        }
        # 事件必须能被**下一个 turn** 读到，而 turn 与摄取是不同请求，所以
        # 事件随 stale 事实一起落库（payload 里的 ``_env_change``），注入时
        # 由 stale 的 data_profile 反推——不需要第二张事件表。
        held_fact.payload = {**incoming, "_env_change": event}
        db.flush()
        events.append(event)
        logger.info(
            "[CartoMemory] distribution drift on %s/%s: %s (dev=%s)",
            project_id, field, verdict.get("reason"),
            verdict.get("max_relative_deviation"),
        )
    return events


def get_verified_recipe_ids(db: Session, project_id: str) -> set:
    """本项目验证过（recipe_outcome ACTIVE）的 recipe id 集合。

    推荐排序的读取口（spec 开放问题 3）：单一推荐路径 + 记忆加成，
    不存在第二套带记忆的推荐器。
    """
    if not project_id:
        return set()
    facts = get_active_facts(db, project_id, kinds=("recipe_outcome",))
    return {fact.subject for fact in facts}


def list_project_facts(
    db: Session, project_id: str, limit: int = 200
) -> List[CartoProjectFact]:
    """项目全部事实（含 stale/conflicted/retired）——管理视图用。

    注入视图用 :func:`get_active_facts`；本函数是 UI/审计入口，按最近
    验证排序，遵守同一 LRU 上限量级。
    """
    if not project_id:
        return []
    stmt = (
        select(CartoProjectFact)
        .where(CartoProjectFact.project_id == project_id)
        .order_by(CartoProjectFact.last_verified_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def retire_fact(db: Session, project_id: str, fact_id: str) -> Optional[CartoProjectFact]:
    """撤销一条事实（开放问题 2 的入口）：``retired`` 软删，不物理删除。

    撤销偏好 = 用户改主意；撤销共享分类 = 项目不再复用该方案。行保留可
    审计（evidence_digest/fingerprint 不丢）。
    """
    fact = db.get(CartoProjectFact, fact_id)
    if fact is None or fact.project_id != project_id:
        return None
    fact.status = "retired"
    fact.last_verified_at = _now()
    db.flush()
    return fact


def activate_fact(db: Session, project_id: str, fact_id: str) -> Optional[CartoProjectFact]:
    """显式（重）激活一条事实——ADR-0069 决策 3 的裁决入口。

    适用场景：用户在 UI 上确认 conflicted 的方案之一、或复活一条 stale
    事实（“我知道数据变了，但就要这套断点”）。激活是**显式动作**：清除
    分歧/环境事件标记，刷新验证时间。调用方负责提交。
    """
    fact = db.get(CartoProjectFact, fact_id)
    if fact is None or fact.project_id != project_id:
        return None
    if isinstance(fact.payload, dict):
        fact.payload = {
            k: v for k, v in fact.payload.items()
            if k not in ("_conflict", "_env_change")
        }
    fact.status = "active"
    fact.last_verified_at = _now()
    db.flush()
    return fact


def get_pending_env_changes(
    db: Session, project_id: str, limit: int = 10
) -> List[Dict[str, Any]]:
    """待告知 agent 的环境变化事件（由 stale ``data_profile`` 事实携带）。

    读取即消费：返回后清除 ``_env_change``，避免同一条漂移在每个 turn 重复
    刷屏。事实本身保持 ``stale``——真正的复活条件是重新分类并通过 gate。
    调用方负责提交。
    """
    if not project_id:
        return []
    facts = list(db.execute(
        select(CartoProjectFact).where(
            CartoProjectFact.project_id == project_id,
            CartoProjectFact.kind == "data_profile",
            CartoProjectFact.status == "stale",
        ).order_by(CartoProjectFact.last_verified_at.desc()).limit(limit)
    ).scalars().all())
    events: List[Dict[str, Any]] = []
    for fact in facts:
        payload = fact.payload if isinstance(fact.payload, dict) else {}
        event = payload.get("_env_change")
        if not isinstance(event, dict):
            continue
        events.append(event)
        fact.payload = {k: v for k, v in payload.items() if k != "_env_change"}
    if events:
        db.flush()
    return events


__all__ = [
    "MAX_FACTS_PER_PROJECT",
    "MEMORY_BLOCK_CHAR_BUDGET",
    "MEMORY_MARKER",
    "FACT_KINDS",
    "FACT_STATUSES",
    "apply_distribution_drift",
    "classification_fingerprint",
    "list_project_facts",
    "record_fact",
    "retire_fact",
    "activate_fact",
    "get_active_facts",
    "get_pending_env_changes",
    "get_shared_classification",
    "get_verified_recipe_ids",
    "harvest_facts_from_review",
    "mark_stale",
    "render_memory_block",
]
