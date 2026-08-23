"""TemplateSelector —— 确定性模板评分与选择（§Phase H）。

输入是 Intent + Recipe + Profile（artifact/几何事实）+ 输出目标，而不是
query string。评分因素：subject 亲和、task 亲和、输出/导出重叠、
MapModel/artifact/几何兼容（软罚分，不吞掉 finalize 阶段的 eligibility
回退证据）、项目偏好、模板 priority、稳定 id tie-break。
LLM/记忆只能通过 preference hint 参与，最终裁决是本模块的确定性评分。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Set

from pydantic import BaseModel, Field

from app.services.gis_harness.intent import MapRequestIntent
from app.services.gis_harness.template_catalog import (
    TemplateCatalog,
    get_template_catalog,
)

_MAX_CANDIDATES = 8          # evidence 有界
_MAX_REASONS = 6


class ScoredCandidate(BaseModel):
    template_id: str
    score: int
    reasons: List[str] = Field(default_factory=list)


class ProductTemplateSelection(BaseModel):
    status: Literal["selected", "none"] = "none"
    template_id: str = ""
    candidates: List[ScoredCandidate] = Field(default_factory=list)
    decision: Dict[str, Any] = Field(default_factory=dict)


class StyleTemplateSelection(BaseModel):
    status: Literal["selected", "none"] = "none"
    template_id: str = ""
    candidates: List[ScoredCandidate] = Field(default_factory=list)


def _dominant_geometry(profile: Optional[Dict[str, Any]]) -> str:
    types = (profile or {}).get("geometryTypes")
    if not isinstance(types, list) or not types:
        return "unknown"
    s = {str(t) for t in types}
    if s & {"Point", "MultiPoint"}:
        return "point"
    if s & {"Polygon", "MultiPolygon"}:
        return "polygon"
    if s & {"LineString", "MultiLineString"}:
        return "line"
    return "unknown"


class TemplateSelector:
    """确定性模板选择器（纯函数式，无 LLM/IO/DB）。"""

    def __init__(self, catalog: Optional[TemplateCatalog] = None) -> None:
        self.catalog = catalog or get_template_catalog()

    # ── 产品模板选择 ─────────────────────────────────────────────────
    def select_product(
        self,
        *,
        intent: MapRequestIntent,
        recipe_id: str,
        profile: Optional[Dict[str, Any]] = None,
        preferred_template_ids: Optional[Set[str]] = None,
    ) -> ProductTemplateSelection:
        candidates = self.catalog.find_product_candidates(recipe_id)
        if not candidates:
            return ProductTemplateSelection(
                status="none",
                decision={"reason": "no_product_template_for_recipe"},
            )

        from app.lib.cartography.model_library import get_map_model_registry
        models = get_map_model_registry()
        profile_geom = _dominant_geometry(profile)

        scored: List[ScoredCandidate] = []
        for tpl in candidates:
            score = 0
            reasons: List[str] = []
            if tpl.deprecated:
                continue
            # subject 亲和（#719 语义）
            subject = intent.subject.category
            if subject and subject in tpl.subject_categories:
                score += 40
                reasons.append(f"subject_match:{subject}")
            # task 亲和（simple_view → 轻量产品；非亲和任务罚分防过度分析）
            if intent.task in tpl.task_affinity:
                score += 50
                reasons.append(f"task_affinity:{intent.task}")
            elif intent.task == "simple_view":
                score -= 30
                reasons.append("overanalysis_for_simple_view")
            # 输出/导出重叠
            wanted_outputs = set(intent.output_intents) or {"map"}
            overlap_out = wanted_outputs & set(tpl.outputs)
            if overlap_out:
                score += min(10, 5 * len(overlap_out))
                reasons.append(f"outputs:{','.join(sorted(overlap_out))}")
            if intent.export_intents:
                overlap_exp = set(intent.export_intents) & set(tpl.exports)
                if overlap_exp:
                    score += min(10, 5 * len(overlap_exp))
                    reasons.append(f"exports:{','.join(sorted(overlap_exp))}")
            # 通用模板 tie-break（id == recipe_id 的模板是该 recipe 的缺省产品）
            if tpl.id == recipe_id:
                score += 15
                reasons.append("default_for_recipe")
            # 项目偏好（记忆只加分，不跳过 eligibility/QA）
            if preferred_template_ids and tpl.id in preferred_template_ids:
                score += 15
                reasons.append("project_preference")
            # profile 兼容（软罚分：finalize 阶段的 eligibility 才是硬裁决，
            # 这里只让明显不兼容的模板排在后面，保留 fallback 证据链）
            if profile is not None and profile_geom != "unknown":
                primary = next(
                    (r for r in tpl.layer_roles if r.role == "primary"), None)
                if primary is not None:
                    model = models.resolve(primary.resolved_map_model)
                    if model is not None and \
                            model.geometry_kinds and \
                            profile_geom not in model.geometry_kinds:
                        score -= 25
                        reasons.append(
                            f"geometry_mismatch:{profile_geom}_on_{model.id}")
            score -= tpl.priority  # priority 小者优先
            reasons.append(f"priority:{tpl.priority}")
            scored.append(ScoredCandidate(
                template_id=tpl.id, score=score, reasons=reasons[:_MAX_REASONS]))

        if not scored:
            return ProductTemplateSelection(
                status="none",
                decision={"reason": "all_candidates_deprecated"},
            )
        # 稳定排序：score 降序，id 升序 tie-break
        scored.sort(key=lambda c: (-c.score, c.template_id))
        chosen = scored[0]
        return ProductTemplateSelection(
            status="selected",
            template_id=chosen.template_id,
            candidates=scored[:_MAX_CANDIDATES],
            decision={
                "reason": "; ".join(chosen.reasons),
                "rejected": [
                    {"template_id": c.template_id,
                     "reason": "; ".join(c.reasons),
                     "score": c.score}
                    for c in scored[1:3]
                ],
            },
        )

    # ── 样式模板选择（layer role 的 style 候选，进 evidence）──────────
    def select_style_for_layer(
        self,
        *,
        map_model: str,
        geometry: str = "",
        artifact_type: str = "",
        limit: int = 3,
    ) -> StyleTemplateSelection:
        entries = self.catalog.find_style_candidates(
            map_model=map_model, geometry=geometry, limit=20)
        if not entries:
            return StyleTemplateSelection(status="none")
        scored: List[ScoredCandidate] = []
        for entry in entries:
            compat = self.catalog.style_compatibility(entry)
            score = 30 if map_model in compat.compatible_map_models else 0
            if artifact_type and artifact_type in compat.accepted_artifact_types:
                score += 20
            if compat.deprecated:
                score -= 100
            scored.append(ScoredCandidate(
                template_id=str(entry.get("id")), score=score))
        scored.sort(key=lambda c: (-c.score, c.template_id))
        top = scored[:limit]
        return StyleTemplateSelection(
            status="selected" if top else "none",
            template_id=top[0].template_id if top else "",
            candidates=top,
        )


__all__ = [
    "TemplateSelector",
    "ProductTemplateSelection",
    "StyleTemplateSelection",
    "ScoredCandidate",
]
