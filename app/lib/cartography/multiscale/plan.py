"""带计划构建器（M4）—— importance × generalize × 带词表 → 全档案 + 工件。

单一编排点（确定性纯函数，零 IO）::

    feature_collection + source 时点 + importance 裁决 + user overrides
        ↓ build_scale_profile()
    ProfileBuildResult
      .profile    ScaleRepresentationProfile（4 带计划 + 统计 + 披露 + 检查）
      .artifacts  {tier: GeneralizationResult}（仅 generalized 带；内存态，
                   由服务层存为新 ref 后经 profile_with_artifact_refs 回填）

策略缺省（家族 × 带词表；全部可被调用方覆盖）：
- 线族：world/province 带有界简化（粗带容差 = 0.75 × 带计算基准米/像素），
  city 带轻简化，street 带零简化；
- 面族：world/province 带共享边界保护（行政/专题边界不裂缝），city 带
  有界简化，street 带零简化；无重要性证据时不做要素取舍（全保留）；
- 点族：几何策略恒 none（点综合 = 去密通道，非几何改写）；密集判定与
  候选序复用 SCALE_TIERS.dense_threshold / point_dense_candidates
  （representation 协议同源权威）；
- 语义取舍：仅当 importance.selected 在场（D4）；kept == 全词表值的带
  视为平凡取舍（suppressed=0 不触发 generalized）。

统计口径（D7）：original 恒为输入要素数；displayed = 该带保留数
（decluttering 是视觉聚合不改统计口径 —— 聚合语义披露沿用
representation.VISUAL_AGGREGATION_DISCLOSURE 固定文案）。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict

from app.lib.cartography.multiscale.generalize import (
    GeneralizationResult,
    generalize_features,
)
from app.lib.cartography.multiscale.importance import (
    ImportanceDerivation,
    kept_values_for_band,
)
from app.lib.cartography.multiscale.profile import (
    SCALE_REPRESENTATION_PROFILE_VERSION,
    BandDisplayStats,
    DeclutteringPolicy,
    FeatureSelectionPolicy,
    GeometryGeneralizationPolicy,
    ScaleBandPlan,
    ScaleRepresentationProfile,
    SemanticCheckRecord,
    SourceRevision,
    UserScaleOverride,
    build_scale_range,
    profile_fingerprint,
    validate_profile,
)
from app.lib.cartography.representation import (
    VISUAL_AGGREGATION_DISCLOSURE,
    normalize_geometry_family,
)
from app.lib.cartography.scale_rules import SCALE_TIERS

#: 粗带几何容差系数（× 带计算基准米/像素 @ 带最粗端 zoom）。
#: 0.75px ≈ 亚像素简化 —— 视觉无损阈值下的顶点削减（与 MVT 半像素
#: 同量级惯例，独立系数：工件是整图级而非瓦片级，稍保守）。
COARSE_TOLERANCE_PX = 0.75
CITY_TOLERANCE_PX = 0.5

#: 点族去密：渲染端可执行模式（raw 聚合词表外；aggregate_grid 需格网工件，本期不产）。
_RENDERER_DENSITY_MODES = ("point_cluster", "visual_heatmap")


class ProfileBuildResult(BaseModel):
    """构建产物：档案 + 按带工件（内存态）。"""

    model_config = ConfigDict(extra="allow")

    profile: ScaleRepresentationProfile
    artifacts: Dict[str, GeneralizationResult] = {}


def _tier_geometry_policy(
    geometry_family: str, tier_name: str, m_per_px: float
) -> GeometryGeneralizationPolicy:
    """家族 × 带 → 几何策略缺省（容差由带计算基准推导）。"""
    if geometry_family == "line":
        if tier_name in ("world", "province"):
            return GeometryGeneralizationPolicy(
                strategy="bounded_simplify",
                tolerance=round(COARSE_TOLERANCE_PX * m_per_px, 6),
                tolerance_units="meters_ground",
                topology_risk="low",
                reason_codes=[f"MSP.GEOM.LINE.{tier_name.upper()}"],
            )
        if tier_name == "city":
            return GeometryGeneralizationPolicy(
                strategy="bounded_simplify",
                tolerance=round(CITY_TOLERANCE_PX * m_per_px, 6),
                tolerance_units="meters_ground",
                topology_risk="low",
                reason_codes=["MSP.GEOM.LINE.CITY"],
            )
        return GeometryGeneralizationPolicy(strategy="none")
    if geometry_family == "polygon":
        if tier_name in ("world", "province"):
            return GeometryGeneralizationPolicy(
                strategy="shared_boundary_protect",
                tolerance=round(COARSE_TOLERANCE_PX * m_per_px, 6),
                tolerance_units="meters_ground",
                topology_risk="medium",
                reason_codes=[f"MSP.GEOM.POLYGON.{tier_name.upper()}"],
            )
        if tier_name == "city":
            return GeometryGeneralizationPolicy(
                strategy="bounded_simplify",
                tolerance=round(CITY_TOLERANCE_PX * m_per_px, 6),
                tolerance_units="meters_ground",
                topology_risk="medium",
                reason_codes=["MSP.GEOM.POLYGON.CITY"],
            )
        return GeometryGeneralizationPolicy(strategy="none")
    return GeometryGeneralizationPolicy(strategy="none")


def _tier_decluttering(
    geometry_family: str, tier, feature_count: int
) -> DeclutteringPolicy:
    """点族去密（SCALE_TIERS 候选序 + dense_threshold 同源权威）。"""
    if geometry_family != "point":
        return DeclutteringPolicy(mode="none")
    dense = feature_count > tier.dense_threshold
    if not dense:
        return DeclutteringPolicy(
            mode="none",
            reason_codes=[f"MSP.DENSITY.BELOW.{tier.name.upper()}"],
        )
    for candidate in tier.point_dense_candidates:
        # 候选 id（model_library 词表）→ 协议模式：renderer 可执行者胜出
        if candidate == "point_cluster" and "point_cluster" in _RENDERER_DENSITY_MODES:
            return DeclutteringPolicy(
                mode="point_cluster", threshold_feature_count=tier.dense_threshold,
                reason_codes=[f"MSP.DENSITY.{tier.name.upper()}.POINT_CLUSTER"])
        if candidate == "visual_heatmap" and "visual_heatmap" in _RENDERER_DENSITY_MODES:
            return DeclutteringPolicy(
                mode="visual_heatmap", threshold_feature_count=tier.dense_threshold,
                reason_codes=[f"MSP.DENSITY.{tier.name.upper()}.VISUAL_HEATMAP"])
    return DeclutteringPolicy(
        mode="none",
        reason_codes=[f"MSP.DENSITY.{tier.name.upper()}.NO_EXECUTABLE_CANDIDATE"])


def _mandatory_ids_from_overrides(
    overrides: Sequence[UserScaleOverride],
) -> Tuple[frozenset, List[str]]:
    ids = {o.value for o in overrides if o.kind == "mandatory_feature"}
    values: List[str] = []
    for o in overrides:
        if o.kind == "band_keep_values" and isinstance(o.detail, dict):
            tier = o.detail.get("tier")
            vals = o.detail.get("values")
            if isinstance(tier, str) and isinstance(vals, list):
                values.append(tier)
    return frozenset(ids), values


def build_scale_profile(
    *,
    layer_id: str,
    feature_collection: Dict[str, Any],
    source: SourceRevision,
    geometry_family: Optional[str] = None,
    latitude_hint: float = 0.0,
    importance: Optional[ImportanceDerivation] = None,
    user_overrides: Sequence[UserScaleOverride] = (),
    keep_ranks: Optional[Dict[str, int]] = None,
    generated_by: str = "",
) -> ProfileBuildResult:
    """要素 + 证据 → 全域带计划 + 工件（确定性）。"""
    family = normalize_geometry_family(geometry_family or source.geometry_family)
    features = list(
        (feature_collection or {}).get("features") or [])
    feature_count = source.feature_count or len(features)
    model = importance.selected if importance is not None else None
    pin_raw = any(o.kind == "pin_mode" and o.value == "raw" for o in user_overrides)
    mandatory_ids, band_keep_overrides = _mandatory_ids_from_overrides(user_overrides)

    bands: List[ScaleBandPlan] = []
    artifacts: Dict[str, GeneralizationResult] = {}
    disclosures: List[str] = []
    checks: List[SemanticCheckRecord] = []

    if model is None and importance is not None:
        disclosures.append(
            "无可信重要性字段（" + ";".join(importance.reason_codes[:3]) +
            "）——不做要素取舍，保留原始几何/聚合表达（不伪造主干）")

    stats_all_match = True
    for tier in SCALE_TIERS:
        scale = build_scale_range(
            tier.min_zoom, tier.max_zoom, tier.name, latitude_hint)
        geom_policy = _tier_geometry_policy(
            family, tier.name, scale.computation_m_per_px_at_min)
        # 高风险豁免：用户显式声明不可动几何（safe_no_generalization pin）
        if any(o.kind == "simplify_tolerance" and o.value == "none"
               for o in user_overrides):
            geom_policy = GeometryGeneralizationPolicy(
                strategy="safe_no_generalization",
                topology_risk="high",
                reason_codes=["MSP.GEOM.USER_SAFE_NO_GENERALIZATION"])

        sel_policy = FeatureSelectionPolicy(importance_evidence="none")
        if model is not None:
            mandatory_values = []
            for o in user_overrides:
                if o.kind == "band_keep_values" and isinstance(o.detail, dict) \
                        and o.detail.get("tier") == tier.name \
                        and isinstance(o.detail.get("values"), list):
                    mandatory_values = list(o.detail["values"])
            kept = kept_values_for_band(
                model, tier.name, keep_ranks, mandatory_values=mandatory_values)
            sel_policy = FeatureSelectionPolicy(
                importance_field=model.field,
                value_order=list(model.value_order),
                kept_values=kept,
                importance_evidence=model.evidence,
                mandatory_features=sorted(mandatory_ids),
                suppress_reason_codes=["MSP.SELECT.TIER_KEEP_TOP_K"],
            )
        declutter = _tier_decluttering(family, tier, feature_count)

        gen: Optional[GeneralizationResult] = None
        selection_or_geometry_active = bool(
            sel_policy.kept_values) or geom_policy.strategy in (
            "bounded_simplify", "shared_boundary_protect")
        if selection_or_geometry_active and not pin_raw:
            gen = generalize_features(
                features,
                geometry_policy=geom_policy,
                selection_policy=sel_policy,
                latitude_hint=latitude_hint,
                band_zoom=tier.min_zoom,
                mandatory_ids=mandatory_ids,
            )
            nontrivial = (
                gen.receipt["suppressed_count"] > 0
                or gen.receipt["geometry_simplified"] > 0)
            if not nontrivial:
                gen = None  # 平凡带（零取舍零简化）不落工件

        mode = "generalized" if gen is not None else "raw"
        displayed = len(gen.feature_collection["features"]) if gen else feature_count
        suppressed = feature_count - displayed
        degrade: List[str] = []
        if gen is not None:
            degrade = [
                f"{k}:{v}" for k, v in gen.receipt["reason_counts"].items()
                if k.startswith(("suppressed", "geometry"))
            ][:6]
        stats = BandDisplayStats(
            original_feature_count=feature_count,
            displayed_feature_count=displayed,
            suppressed_feature_count=suppressed,
            geometry_modified_count=(
                gen.receipt["geometry_simplified"] if gen else 0),
            degrade_reasons=degrade,
        )
        if gen is not None and not gen.receipt["stats_match"]:
            stats_all_match = False

        bands.append(ScaleBandPlan(
            scale=scale,
            representation_mode=mode,
            geometry=geom_policy,
            selection=sel_policy,
            decluttering=declutter,
            stats=stats,
            reason_codes=[f"MSP.BAND.{tier.name.upper()}.{mode.upper()}"],
        ))
        if gen is not None:
            artifacts[tier.name] = gen
        if declutter.mode in ("point_cluster", "visual_heatmap") and (
                VISUAL_AGGREGATION_DISCLOSURE not in disclosures):
            disclosures.append(VISUAL_AGGREGATION_DISCLOSURE)

    # 带级统计披露（M4：displayed ≠ original 必须可解释）
    for b in bands:
        if b.stats.suppressed_feature_count > 0:
            disclosures.append(
                f"{b.scale.tier} 带显示 {b.stats.displayed_feature_count}/"
                f"原始 {b.stats.original_feature_count} 条"
                f"（取舍依据 {b.selection.importance_evidence}；"
                "统计/图例计算口径仍为 original_source）")
            break  # 一条汇总即可（逐带数值在 bands 结构化面）

    profile = ScaleRepresentationProfile(
        schema_version=SCALE_REPRESENTATION_PROFILE_VERSION,
        profile_id="msp:pending",
        layer_id=layer_id,
        source=source,
        bands=bands,
        user_overrides=list(user_overrides),
        semantic_checks=checks,
        disclosures=disclosures[:12],
        generated_by=generated_by,
    )
    profile.profile_id = f"msp:{layer_id}:{profile_fingerprint(profile)[:16]}"

    # 语义检查（结构规则以中间态口径复验 —— 工件 ref 落库回填前允许缺席；
    # 最终档案落 MapSpec 时由消费方以严格模式复验）
    struct_errors = validate_profile(profile, require_artifact_refs=False)
    checks.append(SemanticCheckRecord(
        check="bands_cover_full_domain",
        status="pass" if not any("cover" in e or "contiguous" in e for e in struct_errors) else "fail",
        detail={"errors": struct_errors},
    ))
    checks.append(SemanticCheckRecord(
        check="displayed_not_exceeding_original",
        status="pass" if not any("displayed_exceeds" in e for e in struct_errors) else "fail",
    ))
    checks.append(SemanticCheckRecord(
        check="artifact_stats_match", status="pass" if stats_all_match else "fail"))
    checks.append(SemanticCheckRecord(
        check="importance_evidence_valid",
        status="pass" if (model is not None or importance is None) else "warning",
        detail={"reason_codes": importance.reason_codes[:6] if importance else []},
    ))
    checks.append(SemanticCheckRecord(
        check="classification_basis_declared", status="pass",
        detail={"classification_basis": "original_source"},
    ))
    profile.semantic_checks = checks
    return ProfileBuildResult(profile=profile, artifacts=artifacts)


def profile_with_artifact_refs(
    profile: ScaleRepresentationProfile,
    ref_map: Dict[str, Tuple[str, str]],
) -> ScaleRepresentationProfile:
    """工件落库后回填 (artifact_ref, artifact_source_id) —— 深拷贝不改原档。"""
    out = copy.deepcopy(profile)
    for band in out.bands:
        entry = ref_map.get(band.scale.tier)
        if entry is not None and band.representation_mode == "generalized":
            band.artifact_ref, band.artifact_source_id = entry[0], entry[1]
    out.profile_id = f"msp:{out.layer_id}:{profile_fingerprint(out)[:16]}"
    return out


__all__ = [
    "COARSE_TOLERANCE_PX",
    "CITY_TOLERANCE_PX",
    "ProfileBuildResult",
    "build_scale_profile",
    "profile_with_artifact_refs",
]
