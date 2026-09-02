"""Semantic GIS Intelligence tests (ADR-0092 Phase C).

C1/C2: evidence-graded semantic field roles (name alone never reaches
rule_derived; unknown stays unknown).
C3/C4: pattern library + honest disclosures (Scenario F: equity claims
without a population denominator must disclose, never conclude).
"""

from app.lib.gis.analysis_patterns import all_patterns, get_pattern
from app.lib.gis.dataset_profile import DatasetProfile
from app.lib.gis.pattern_projection import project_patterns
from app.lib.gis.semantic_profile import (
    RoleConfidence,
    SemanticFieldRole,
    derive_semantic_profile,
)


def _profile(fields: dict) -> DatasetProfile:
    return DatasetProfile(
        source="synthetic",
        fields=fields,
        fields_status="explicit" if fields else "unknown",
    )


# ── C1/C2 semantic roles ──────────────────────────────────────────────────


def test_admin_dimension_metadata_only_on_name_and_dtype():
    sem = derive_semantic_profile(_profile({"district": "string", "school_name": "string"}))
    row = next(fr for fr in sem.field_roles if fr.field == "district")
    assert SemanticFieldRole.ADMIN_DIMENSION.value in row.roles
    assert row.confidence == RoleConfidence.METADATA_DERIVED, \
        "name+dtype without samples must not exceed metadata_derived"


def test_name_alone_cannot_reach_rule_confidence():
    """A field named 'count' with boolean values must NOT become a count measure."""
    sem = derive_semantic_profile(
        _profile({"count": "boolean"}),
        value_samples={"count": [True, False, True]},
    )
    row = next(fr for fr in sem.field_roles if fr.field == "count")
    assert SemanticFieldRole.COUNT_MEASURE.value not in row.roles


def test_value_samples_promote_to_rule_derived():
    sem = derive_semantic_profile(
        _profile({"schools_total": "integer"}),
        value_samples={"schools_total": [3, 12, 7, 0, 25]},
    )
    row = next(fr for fr in sem.field_roles if fr.field == "schools_total")
    assert SemanticFieldRole.COUNT_MEASURE.value in row.roles
    assert row.confidence == RoleConfidence.RULE_DERIVED
    assert "value_sample" in row.evidence


def test_ratio_via_unit_interval_samples():
    sem = derive_semantic_profile(
        _profile({"green_share": "number"}),
        value_samples={"green_share": [0.12, 0.55, 0.99, 0.0]},
    )
    row = next(fr for fr in sem.field_roles if fr.field == "green_share")
    assert SemanticFieldRole.RATIO_MEASURE.value in row.roles


def test_population_implies_normalization_denominator():
    sem = derive_semantic_profile(
        _profile({"resident_population": "integer"}),
        value_samples={"resident_population": [100000, 250000]},
    )
    assert sem.has_role(SemanticFieldRole.NORMALIZATION_DENOMINATOR)
    assert sem.has_role(SemanticFieldRole.POPULATION_MEASURE)


def test_unknown_field_stays_unknown():
    sem = derive_semantic_profile(_profile({"zzz_mystery": "string"}))
    row = next(fr for fr in sem.field_roles if fr.field == "zzz_mystery")
    assert row.roles == []
    assert row.confidence == RoleConfidence.UNKNOWN


def test_user_declaration_wins():
    sem = derive_semantic_profile(
        _profile({"zone": "string"}),
        user_roles={"zone": "admin_dimension"},
    )
    row = next(fr for fr in sem.field_roles if fr.field == "zone")
    assert row.confidence == RoleConfidence.USER_DECLARED
    assert SemanticFieldRole.ADMIN_DIMENSION.value in row.roles


def test_temporal_detection_via_samples():
    sem = derive_semantic_profile(
        _profile({"the_day": "string"}),
        value_samples={"the_day": ["2025-01-01", "2025-02-01", "2025-03-01"]},
    )
    assert sem.has_role(SemanticFieldRole.TEMPORAL_DIMENSION)


# ── C3 pattern library ────────────────────────────────────────────────────


def test_pattern_library_complete():
    pats = {p.id for p in all_patterns()}
    assert {
        "distribution", "density", "administrative_comparison", "accessibility",
        "service_coverage", "spatial_equity", "site_selection", "risk_exposure",
        "temporal_change", "mobility_flow", "suitability",
    } <= pats
    for p in all_patterns():
        assert p.recommended_capabilities, f"{p.id} must recommend capabilities"
        assert p.normalization_guidance, f"{p.id} must carry normalization guidance"
        assert p.common_pitfalls, f"{p.id} must list pitfalls"


def test_equity_pattern_requires_denominator():
    equity = get_pattern("spatial_equity")
    assert equity is not None
    assert SemanticFieldRole.NORMALIZATION_DENOMINATOR in equity.required_roles


# ── C4 projection / Scenario F ────────────────────────────────────────────


def test_equity_without_population_discloses_honestly():
    """Scenario F: only school data → must disclose the missing denominator,
    never silently reduce to a count comparison presented as fairness."""
    projection = project_patterns(
        "分析各区学校资源是否均衡",
        intent_task="administrative_statistic",
        semantic_profile=None,
    )
    ids = [m.pattern_id for m in projection.matches]
    assert "spatial_equity" in ids
    match = next(m for m in projection.matches if m.pattern_id == "spatial_equity")
    assert SemanticFieldRole.NORMALIZATION_DENOMINATOR.value in match.missing_roles
    assert any("分母" in d or "人口" in d for d in match.disclosures), \
        "disclosure must state the denominator gap in plain language"


def test_equity_with_population_satisfied():
    sem = derive_semantic_profile(
        _profile({"population": "integer", "district": "string"}),
        value_samples={"population": [100000, 200000]},
    )
    projection = project_patterns(
        "分析各区学校资源是否均衡（有区县人口）",
        intent_task="administrative_statistic",
        semantic_profile=sem,
    )
    match = next(m for m in projection.matches if m.pattern_id == "spatial_equity")
    assert SemanticFieldRole.NORMALIZATION_DENOMINATOR.value in match.satisfied_roles
    assert not match.disclosures


def test_projection_never_recommends_execution():
    """Advisory only: projection output must not carry tool ids or claim
    execution — capabilities go through the planner, and the pattern text
    must say so at the tool layer."""
    projection = project_patterns("成都小学密度", intent_task="analytical_density")
    for m in projection.matches:
        for cap in m.recommended_capabilities:
            assert not cap.startswith("query_"), "pattern must recommend capabilities, not tools"
