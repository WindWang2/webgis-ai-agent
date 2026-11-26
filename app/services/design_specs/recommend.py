"""Design Spec 推荐 —— 数据驱动的规范/模板匹配（WP2，确定性零 LLM）。

输入是**结构化事实**（用途 / 几何 / 值语义 / 单位 / 范围 / 输出目标），
不是关键词字符串 —— 禁止以关键词强行热力图、对缺分母的 count 标密度、
对不兼容字段使用顺序色带。三条纪律：

1. **排序确定性**：同一输入同一次序（分数降序 → id 字典序）；分数是
   可解释的维度命中数，不是黑盒;
2. **适配证据**：每个候选携带 ``matched``（命中的维度）与 ``reasons``；
   不推荐也要说 ``unavailable_reason``；
3. **科学门控**：density/per-capita 语义无分母证据 → 不推荐密度族模型，
   给最小澄清问题（DENOMINATOR_MISSING），绝不静默降级成假成功。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.design_specs.contract import (
    GEOMETRY_KINDS,
    VALUE_SEMANTICS,
)

#: 推荐器版本（回执可追溯）。
RECOMMENDER_VERSION = "design_spec_recommend.v1"

#: 用途词表（比 standards MAP_PURPOSES 更贴近制图选题的封闭投影）。
INTENT_PURPOSES: tuple = (
    "distribution",    # 设施/现象分布
    "statistic",       # 区级统计（同分母/同指标对比）
    "flow",            # 流动/OD
    "change",          # 时间变化/双期对比
    "comparison",      # 多地区/多指标对比
    "composition",     # 多图综合版面
)

_MAX_CANDIDATES = 5
_MAX_CLARIFICATIONS = 3

#: 需要分母证据的测量语义（缺证 → 拒荐密度/比率族并澄清）。
_DENOMINATOR_SEMANTICS = ("density", "rate")

#: 密度族模型词（model_library 词表的子集投影；只用于拒绝面的判定）。
_DENSITY_FAMILY_TOKENS = ("density", "heatmap", "kde")


def _denominator_gate(statistic: str, denominator: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """分母纪律：density/rate 无分母证据 → 澄清问题（不推荐密度族）。"""
    if statistic not in _DENOMINATOR_SEMANTICS:
        return None
    if isinstance(denominator, dict) and denominator.get("available"):
        return None
    return {
        "code": "DENOMINATOR_MISSING",
        "question": (
            "指标是密度/比率，但未提供分母字段（面积/人口等）。"
            "请指明分母字段，或确认改用原始计数表达（需接受面积偏误披露）"
        ),
    }


def _spec_score(spec: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    """规范 × 事实 → 命中维度与分数（可解释）。"""
    matched: List[str] = []
    applicability = spec.get("applicability") or {}
    purposes = facts.get("purposes") or []
    spec_purposes = applicability.get("purposes") or []
    if not spec_purposes or (set(purposes) & set(spec_purposes)):
        matched.append("purpose")
    geometry = facts.get("geometry_kind")
    spec_geometry = applicability.get("geometry_kinds") or []
    if not spec_geometry or (geometry and geometry in spec_geometry):
        matched.append("geometry")
    models = facts.get("map_models") or []
    spec_models = applicability.get("map_models") or []
    if not spec_models or (set(models) & set(spec_models)):
        matched.append("map_model")
    mediums = facts.get("mediums") or []
    spec_mediums = applicability.get("mediums") or []
    if not spec_mediums or (set(mediums) & set(spec_mediums)):
        matched.append("medium")
    # 分类策略与值语义的相容性（负门）
    classification = spec.get("classification") or {}
    statistic = facts.get("statistic")
    method = classification.get("default_method")
    negative: List[str] = []
    if statistic == "category" and method in ("quantiles", "equal_interval", "std_dev", "head_tail"):
        negative.append("categorical_data_with_numeric_classification")
    return {
        "score": len(matched) - 2 * len(negative),
        "matched": matched,
        "negative": negative,
    }


def recommend_design_specs(
    specs: List[Dict[str, Any]],
    facts: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """可见规范 × 结构化事实 → 排序候选（≤5；确定性别序）。"""
    scored: List[Dict[str, Any]] = []
    for spec in specs:
        s = _spec_score(spec, facts)
        if s["negative"]:
            scored.append({
                "spec_id": spec.get("id"),
                "name": spec.get("name"),
                "score": s["score"],
                "matched": s["matched"],
                "unavailable_reason": s["negative"][0],
            })
            continue
        scored.append({
            "spec_id": spec.get("id"),
            "name": spec.get("name"),
            "score": s["score"],
            "matched": s["matched"],
        })
    scored.sort(key=lambda c: (-c["score"], str(c.get("spec_id") or "")))
    return scored[:_MAX_CANDIDATES]


def recommend_map_models(facts: Dict[str, Any]) -> Dict[str, Any]:
    """事实 → model_library 候选（含科学门控与降级证据）。"""
    from app.lib.cartography.model_library import get_map_model_registry

    registry = get_map_model_registry()
    geometry = facts.get("geometry_kind")
    statistic = facts.get("statistic")
    purposes = facts.get("purposes") or []

    gate = _denominator_gate(str(statistic or ""), facts.get("denominator"))

    candidates: List[Dict[str, Any]] = []
    unavailable: List[Dict[str, str]] = []
    for model_id in registry.native_ids():
        model = registry.resolve(model_id)
        if model is None:
            continue
        geo_types = list(getattr(model, "geometry_layer_types", None) or [])
        if geometry and geo_types and geometry not in {
            g.replace("_layer", "") for g in geo_types
        } and geometry not in geo_types:
            continue
        preconditions = " ".join(
            str(x) for x in (getattr(model, "data_preconditions_zh", None) or [])
        )
        reasons: List[str] = []
        if gate is not None and any(tok in model_id for tok in _DENSITY_FAMILY_TOKENS):
            unavailable.append({
                "model_id": model_id,
                "reason": "DENOMINATOR_MISSING",
                "evidence": "密度/热度族模型要求分母证据或足够点密度；当前事实缺分母",
            })
            continue
        if geometry and geo_types and geometry in {
            g.replace("_layer", "") for g in geo_types
        }:
            reasons.append(f"geometry:{geometry}")
        if statistic == "category" and "categor" in model_id:
            reasons.append("statistic:category")
        if statistic == "density" and any(t in model_id for t in _DENSITY_FAMILY_TOKENS) and gate is None:
            reasons.append("statistic:density_with_denominator")
        if "choropleth" in model_id and geometry == "polygon":
            reasons.append("polygon_statistics")
        if any(p in ("change",) for p in purposes) and any(
            t in model_id for t in ("change", "temporal")
        ):
            reasons.append("purpose:change")
        if any(p in ("flow",) for p in purposes) and "flow" in model_id:
            reasons.append("purpose:flow")
        if preconditions:
            reasons.append("preconditions_declared")
        if reasons or not geometry:
            candidates.append({
                "model_id": model_id,
                "runtime_status": str(getattr(model, "runtime_status", "native")),
                "score": len(reasons),
                "reasons": reasons[:6],
                "fallback_model_id": getattr(model, "fallback_model_id", None),
            })
    candidates.sort(key=lambda c: (-c["score"], c["model_id"]))
    return {
        "candidates": candidates[:_MAX_CANDIDATES],
        "unavailable": unavailable[:_MAX_CANDIDATES],
    }


def recommend_design(facts: Dict[str, Any], *, db: Any = None,
                     user_id: Optional[str] = None, org_id: Any = None) -> Dict[str, Any]:
    """总入口：结构化事实 → 规范候选 + 模型候选 + 澄清问题（有界）。

    ``facts``（typed 键，未知键忽略）：
    ``purposes``（INTENT_PURPOSES 子集）、``geometry_kind``（GEOMETRY_KINDS）、
    ``statistic``（VALUE_SEMANTICS）、``denominator``（{available: bool, field?}）、
    ``map_models``、``mediums``、``extent``、``output_targets``。
    """
    from app.core.database import SessionLocal

    from app.services.design_specs.versioning import design_spec_scope_clause

    # 词表校验（未知值如实进 clarifications，不静默丢弃）
    clarifications: List[Dict[str, str]] = []
    bad_purposes = [p for p in (facts.get("purposes") or []) if p not in INTENT_PURPOSES]
    if bad_purposes:
        clarifications.append({
            "code": "UNKNOWN_PURPOSE",
            "question": f"未知用途 {bad_purposes}；可用词表：{INTENT_PURPOSES}",
        })
    geometry = facts.get("geometry_kind")
    if geometry and geometry not in GEOMETRY_KINDS:
        clarifications.append({
            "code": "UNKNOWN_GEOMETRY",
            "question": f"未知几何形态 {geometry}；可用词表：{GEOMETRY_KINDS}",
        })
    statistic = facts.get("statistic")
    if statistic and statistic not in VALUE_SEMANTICS:
        clarifications.append({
            "code": "UNKNOWN_STATISTIC",
            "question": f"未知值语义 {statistic}；可用词表：{VALUE_SEMANTICS}",
        })

    gate = _denominator_gate(str(statistic or ""), facts.get("denominator"))
    if gate is not None:
        clarifications.insert(0, gate)
    clarifications = clarifications[:_MAX_CLARIFICATIONS]

    spec_candidates: List[Dict[str, Any]] = []
    with SessionLocal() as session:
        from app.models.design_spec import CartographyDesignSpec

        rows = (
            session.query(CartographyDesignSpec)
            .filter(design_spec_scope_clause(user_id, org_id))
            .order_by(CartographyDesignSpec.updated_at.desc())
            .limit(100)
            .all()
        )
        spec_docs = [
            {"id": r.id, "name": r.name, **(r.payload or {})}
            for r in rows
            if r.status in ("draft", "published")
        ]
    spec_candidates = recommend_design_specs(spec_docs, facts)
    models = recommend_map_models(facts)

    return {
        "recommender_version": RECOMMENDER_VERSION,
        "spec_candidates": spec_candidates,
        "model_candidates": models["candidates"],
        "model_unavailable": models["unavailable"],
        "clarifications": clarifications,
        "facts_seen": {
            "purposes": list(facts.get("purposes") or [])[:8],
            "geometry_kind": geometry,
            "statistic": statistic,
            "denominator_declared": bool(
                isinstance(facts.get("denominator"), dict)
                and facts["denominator"].get("available")
            ),
        },
    }


__all__ = [
    "RECOMMENDER_VERSION",
    "INTENT_PURPOSES",
    "recommend_design",
    "recommend_design_specs",
    "recommend_map_models",
]
