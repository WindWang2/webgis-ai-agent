"""Grammar 表达资格契约测试（F10 M2，design D6）.

锁定：check_eligibility 的 grammar_representation 检查面——**纯 advisory**
（review 独立评审定案：表达裁决权在 recipe 契约 + 既有降级链，grammar
只记证据，永不 gate）。覆盖：数值驱动表达 × 无数值字段证据披露、计数型
聚合不误报、字段缺席 unknown 放行、密集点 advisory、全注册 recipe 冒烟。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.services.gis_harness.recipes import (  # noqa: E402
    check_eligibility,
    get_recipe_registry,
)


def _recipe_with_primary(primary: str):
    """构造以指定 primary_cartography 的最小 recipe。"""
    from app.services.gis_harness.recipes import CartographyRecipe
    return CartographyRecipe(
        id="f10_test_recipe",
        name="F10 test",
        domain="test",
        tasks=["distribution_overview"],
        primary_cartography=primary,
    )


_PROFILE_EXPLICIT_NON_NUMERIC = {
    "geometryTypes": ["Polygon"], "featureCount": 30,
    "fields": {"name": {"kind": "string"}, "cat": {"kind": "text"}},
}
_PROFILE_NUMERIC = {
    "geometryTypes": ["Polygon"], "featureCount": 30,
    "fields": {"v": {"kind": "number", "numeric": True}},
}


def _grammar_check(report):
    return next(c for c in report.checks
                if c["check"] == "grammar_representation")


class TestGrammarRepresentationAdvisory:
    @pytest.mark.parametrize("primary", [
        "administrative_choropleth", "proportional_symbol"])
    def test_value_driven_primary_without_numeric_disclosed(self, primary):
        # 明确非数值字段 + 数值驱动主表达 → 证据披露（不 gate）。
        recipe = _recipe_with_primary(primary)
        report = check_eligibility(recipe, profile=_PROFILE_EXPLICIT_NON_NUMERIC)
        check = _grammar_check(report)
        assert check["passed"] is True  # advisory：永不禁用
        advisory = check["numeric_field_advisory"]
        assert advisory["reason_code"] == "GRAMMAR.REP.NO_NUMERIC_FIELD"
        assert advisory["numeric_field_present"] is False
        assert report.eligible  # 不因 grammar 证据失格

    @pytest.mark.parametrize("primary", [
        "administrative_choropleth", "proportional_symbol",
        "visual_heatmap", "aggregate_grid"])
    def test_numeric_field_present_no_disclosure(self, primary):
        recipe = _recipe_with_primary(primary)
        report = check_eligibility(recipe, profile=_PROFILE_NUMERIC)
        check = _grammar_check(report)
        assert check["passed"] is True
        assert "numeric_field_advisory" not in check

    def test_unknown_field_facts_no_disclosure(self):
        # 字段无 kind/numeric 事实（如 {'name': {}}）= unknown 放行，
        # 不虚构「无数值字段」证据（review 回归定案）。
        recipe = _recipe_with_primary("administrative_choropleth")
        report = check_eligibility(
            recipe, profile={"geometryTypes": ["Polygon"],
                             "featureCount": 200,
                             "fields": {"name": {}}})
        check = _grammar_check(report)
        assert "numeric_field_advisory" not in check
        assert report.eligible

    def test_dense_points_advisory_not_gate(self):
        recipe = _recipe_with_primary("simple_point_map")
        profile = {"geometryTypes": ["Point"], "featureCount": 9000,
                   "fields": {"v": {"kind": "number", "numeric": True}}}
        report = check_eligibility(recipe, profile=profile)
        check = _grammar_check(report)
        assert check["passed"] is True
        advisory = check.get("dense_points_advisory")
        assert advisory
        assert "GRAMMAR.SCALE.DENSE_POINTS_AGGREGATE" in advisory["reason_codes"]
        assert "visual_heatmap" in advisory["candidates"]
        assert report.eligible  # advisory 不 gate

    def test_registry_wide_smoke_never_gates(self):
        """全注册 recipe 上检查不抛异常且永不改变 eligible（166 冒烟）。"""
        reg = get_recipe_registry()
        count = 0
        for rid in reg.all_ids:
            recipe = reg.get(rid)
            if recipe is None:
                continue
            report = check_eligibility(recipe,
                                       profile=_PROFILE_EXPLICIT_NON_NUMERIC)
            grammar_checks = [c for c in report.checks
                              if c["check"] == "grammar_representation"]
            assert grammar_checks
            assert grammar_checks[0]["passed"] is True
            count += 1
        assert count > 50
