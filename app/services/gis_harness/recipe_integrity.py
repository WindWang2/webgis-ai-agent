"""Static integrity of executable eligibility and element-level fallback declarations."""
from __future__ import annotations

from typing import Any


def produced_reason_codes(recipe: Any) -> set[str]:
    codes = {"GEOMETRY_NOT_SUPPORTED"} if recipe.required_geometry or recipe.allowed_geometry else set()
    for rule in recipe.eligibility:
        if rule.requires_geometry:
            codes.add(rule.reason_code or "GEOMETRY_NOT_SUPPORTED")
        if rule.check_points or rule.min_points is not None:
            codes.add(rule.reason_code or "INSUFFICIENT_POINTS")
        if rule.requires_fields:
            codes.add(rule.reason_code or "MISSING_FIELDS")
        if rule.min_samples is not None:
            codes.update(("SAMPLE_BELOW_FLOOR", "SAMPLE_INSUFFICIENT"))
        if rule.field_expectations:
            codes.update(("FIELD_NOT_CATEGORICAL", "FIELD_NOT_CONTINUOUS", "FIELD_MISSING_RATIO_HIGH"))
        if rule.allowed_distribution_shapes:
            codes.add("DISTRIBUTION_UNFIT")
        if rule.require_projected_crs:
            codes.add("PROJECTED_CRS_REQUIRED")
        if rule.min_point_density is not None:
            codes.add("SPARSE_FOR_AGGREGATION")
        if rule.requires_temporal or rule.min_temporal_coverage is not None:
            codes.update(("TEMPORAL_FIELD_ABSENT", "TEMPORAL_COVERAGE_INSUFFICIENT"))
    return codes


def declaration_issues(recipe: Any, models: Any) -> list[str]:
    elements = {recipe.primary_cartography, *recipe.secondary_cartography}
    issues = [f"eligibility element {rule.element!r} is not rendered" for rule in recipe.eligibility if rule.element not in elements]
    codes = produced_reason_codes(recipe)
    for fallback in recipe.fallbacks:
        if fallback.use and models.resolve(fallback.use) is None:
            issues.append(f"unknown fallback element {fallback.use!r}")
        if fallback.reason_code not in codes:
            issues.append(f"unreachable fallback reason {fallback.reason_code!r}")
    return issues
