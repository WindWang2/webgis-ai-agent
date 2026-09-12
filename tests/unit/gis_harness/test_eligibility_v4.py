"""V4 多维度资格裁决单测（ADR-0151 / AC-02 P1+P2）。

覆盖：
- EligibilityContext.from_profile 兜底派生（旧 profile 形态 → unknown 放行）；
- 六个新检查器（样本量分档/字段基数/缺失率/分布形态/CRS 尺度/时间覆盖）
  的判定与边界（空集/单值/全 null/超大 n）；
- check_eligibility 的 AND 语义（旧三维 fast-fail + 新维度，unknown 放行
  时既有行为逐位保留）；
- resolve_fallback_chain（原地 eligible / 声明链 / 原因码门 / 排序键仲裁 /
  环守卫 / 通用兜底 auto_generated / 深度上限）；
- registry_validation 悬空 fallback 引用 → 启动期报错。
"""
from __future__ import annotations

from typing import Any, Dict

from app.services.gis_harness.recipes import (
    DEFAULT_FALLBACK_CHAIN,
    EligibilityContext,
    EligibilityRule,
    CartographyRecipe,
    FieldExpectation,
    FallbackLink,
    ChainResolution,
    check_crs_and_scale,
    check_distribution_shape,
    check_eligibility,
    check_field_cardinality,
    check_missing_ratio,
    check_sample_size,
    check_temporal_coverage,
    get_recipe_registry,
    render_fallback_for_llm,
    resolve_fallback_chain,
)


# ── 构造辅助 ─────────────────────────────────────────────────────────────

def _ctx(**kw: Any) -> EligibilityContext:
    return EligibilityContext(**kw)


def _recipe(**kw: Any) -> CartographyRecipe:
    base: Dict[str, Any] = dict(id="t_recipe", name="t")
    base.update(kw)
    return CartographyRecipe(**base)


def _rule(**kw: Any) -> EligibilityRule:
    base: Dict[str, Any] = dict(element="el")
    base.update(kw)
    return EligibilityRule(**base)


# ── EligibilityContext.from_profile ─────────────────────────────────────

class TestEligibilityContext:
    def test_from_empty_profile_all_unknown(self) -> None:
        ctx = EligibilityContext.from_profile(None)
        assert ctx.n is None
        assert ctx.geometry == "unknown"
        assert ctx.sample_tier() == "unknown"
        assert ctx.distribution_shape() == "unknown"

    def test_from_legacy_profile_known_geometry_and_count(self) -> None:
        ctx = EligibilityContext.from_profile({
            "geometryTypes": ["Point", "MultiPoint"], "featureCount": 1260.0,
            "fields": {"name": {}},
        })
        assert ctx.geometry == "point"
        assert ctx.n == 1260
        assert "name" in ctx.fields
        assert ctx.sample_tier() == "large"

    def test_sample_tiers(self) -> None:
        assert _ctx(n=7).sample_tier() == "below_floor"
        assert _ctx(n=8).sample_tier() == "small"
        assert _ctx(n=30).sample_tier() == "medium"
        assert _ctx(n=500).sample_tier() == "large"
        assert _ctx(n=100000).sample_tier() == "large"


# ── 检查器边界 ──────────────────────────────────────────────────────────

class TestCheckSampleSize:
    def test_below_floor_rejected_even_without_declaration(self) -> None:
        r = check_sample_size(_ctx(n=5), None)
        assert r.ok is False and r.reason_code == "SAMPLE_BELOW_FLOOR"

    def test_declared_min_gate(self) -> None:
        assert check_sample_size(_ctx(n=10), 30).ok is False
        assert check_sample_size(_ctx(n=40), 30).ok is True

    def test_unknown_passes_with_absent_evidence(self) -> None:
        r = check_sample_size(_ctx(), None)
        assert r.ok is True and r.evidence["tier"] == "unknown"

    def test_huge_n_ok(self) -> None:
        assert check_sample_size(_ctx(n=10**9), None).ok is True


class TestCheckFieldCardinality:
    def _exp(self, **kw: Any) -> FieldExpectation:
        return FieldExpectation(field="v", **kw)

    def test_categorical_violation(self) -> None:
        ctx = _ctx(fields={"v": {"unique_ratio": 0.95}})
        r = check_field_cardinality(ctx, self._exp(kind="categorical", max_unique_ratio=0.2))
        assert r.ok is False and r.reason_code == "FIELD_NOT_CATEGORICAL"

    def test_continuous_violation(self) -> None:
        ctx = _ctx(fields={"v": {"unique_ratio": 0.01}})
        r = check_field_cardinality(ctx, self._exp(kind="continuous", min_unique_ratio=0.05))
        assert r.ok is False and r.reason_code == "FIELD_NOT_CONTINUOUS"

    def test_absent_field_unknown_pass(self) -> None:
        r = check_field_cardinality(_ctx(), self._exp(kind="continuous", min_unique_ratio=0.05))
        assert r.ok is True and r.evidence["note"] == "field_facts_absent"

    def test_all_null_ratio_ignored(self) -> None:
        # 全 null 字段：unique_ratio/missing_ratio 缺席 → unknown 放行
        ctx = _ctx(fields={"v": {}})
        assert check_field_cardinality(ctx, self._exp(kind="categorical", max_unique_ratio=0.2)).ok


class TestCheckMissingRatio:
    def test_high_missing_rejected(self) -> None:
        ctx = _ctx(fields={"v": {"missing_ratio": 0.9}})
        r = check_missing_ratio(ctx, FieldExpectation(
            field="v", max_missing_ratio=0.5))
        assert r.ok is False and r.reason_code == "FIELD_MISSING_RATIO_HIGH"

    def test_boundary_equal_passes(self) -> None:
        ctx = _ctx(fields={"v": {"missing_ratio": 0.5}})
        r = check_missing_ratio(ctx, FieldExpectation(
            field="v", max_missing_ratio=0.5))
        assert r.ok is True

    def test_single_value_zero_missing(self) -> None:
        ctx = _ctx(fields={"v": {"missing_ratio": 0.0}})
        assert check_missing_ratio(ctx, FieldExpectation(
            field="v", max_missing_ratio=0.1)).ok


class TestCheckDistributionShape:
    def test_zero_inflated_detected(self) -> None:
        ctx = _ctx(distribution={"zero_ratio": 0.6})
        assert ctx.distribution_shape() == "zero_inflated"
        r = check_distribution_shape(ctx, ["uniform"])
        assert r.ok is False and r.reason_code == "DISTRIBUTION_UNFIT"

    def test_uniform_allowed(self) -> None:
        ctx = _ctx(distribution={"skew": 0.1, "kurtosis": 2.8, "zero_ratio": 0.0})
        assert check_distribution_shape(ctx, ["uniform"]).ok is True

    def test_unknown_shape_passes(self) -> None:
        assert check_distribution_shape(_ctx(), ["uniform"]).ok is True

    def test_skewed_classification(self) -> None:
        assert _ctx(distribution={"skew": 2.5}).distribution_shape() == "skewed"

    def test_heavy_tailed_classification(self) -> None:
        assert _ctx(distribution={"kurtosis": 12.0}).distribution_shape() == "heavy_tailed"


class TestCheckCrsAndScale:
    def _rule(self, **kw: Any) -> EligibilityRule:
        return _rule(**kw)

    def test_geographic_crs_rejected_when_projected_required(self) -> None:
        ctx = _ctx(spatial={"crs_class": "geographic", "crs": "EPSG:4326"})
        r = check_crs_and_scale(ctx, self._rule(require_projected_crs=True))
        assert r.ok is False and r.reason_code == "PROJECTED_CRS_REQUIRED"

    def test_sparse_aggregation_rejected(self) -> None:
        ctx = _ctx(spatial={"crs_class": "projected", "point_density_per_km2": 0.1})
        r = check_crs_and_scale(ctx, self._rule(min_point_density=5.0))
        assert r.ok is False and r.reason_code == "SPARSE_FOR_AGGREGATION"

    def test_absent_spatial_passes(self) -> None:
        assert check_crs_and_scale(_ctx(), self._rule(require_projected_crs=True)).ok


class TestCheckTemporalCoverage:
    def test_missing_temporal_field_rejected(self) -> None:
        r = check_temporal_coverage(_ctx(), _rule(requires_temporal=True))
        assert r.ok is False and r.reason_code == "TEMPORAL_FIELD_ABSENT"

    def test_low_coverage_rejected(self) -> None:
        ctx = _ctx(temporal={"field": "t", "coverage_ratio": 0.2})
        r = check_temporal_coverage(ctx, _rule(
            requires_temporal=True, min_temporal_coverage=0.8))
        assert r.ok is False and r.reason_code == "TEMPORAL_COVERAGE_INSUFFICIENT"

    def test_not_required_passes(self) -> None:
        assert check_temporal_coverage(_ctx(), _rule()).ok is True


# ── check_eligibility 集成（AND 语义 / 旧行为保留）─────────────────────

class TestCheckEligibilityV4Integration:
    def test_legacy_profile_unchanged_behavior(self) -> None:
        """旧三维场景（无新维度声明）：行为与历史逐位一致。

        元素级失败只禁元素、不降 recipe 资格（golden Case B 契约）。
        """
        recipe = _recipe(eligibility=[_rule(element="h", check_points=True,
                                            reason_code="INSUFFICIENT_POINTS")])
        report = check_eligibility(recipe, profile={
            "geometryTypes": ["Point"], "featureCount": 3})
        assert report.eligible is True
        assert report.disabled[0].reason_code == "INSUFFICIENT_POINTS"

    def test_new_dimension_rejects_when_old_passes(self) -> None:
        """新维度为准（更严格）：旧三维全过、min_samples 不过 → 失格。"""
        recipe = _recipe(eligibility=[_rule(element="h", min_samples=50)])
        report = check_eligibility(recipe, profile={
            "geometryTypes": ["Point"], "featureCount": 20})
        assert report.eligible is False
        codes = {d.reason_code for d in report.disabled}
        assert "SAMPLE_INSUFFICIENT" in codes
        v4_checks = [c for c in report.checks if c.get("v4")]
        assert any(not c["passed"] for c in v4_checks)

    def test_v4_disabled_record_carries_dimension(self) -> None:
        recipe = _recipe(eligibility=[_rule(
            element="grid", min_point_density=5.0,
            require_projected_crs=True)])
        report = check_eligibility(recipe, profile={
            "geometryTypes": ["Point"], "featureCount": 100,
            "crsClass": "geographic", "pointDensityPerKm2": 0.5,
        })
        assert report.eligible is False
        dims = {d.evidence.get("dimension") for d in report.disabled}
        assert "crs_scale" in dims

    def test_empty_and_null_profile(self) -> None:
        recipe = _recipe(eligibility=[_rule(element="h", min_samples=10)])
        for profile in ({}, None, {"fields": None, "geometryTypes": None}):
            report = check_eligibility(recipe, profile=profile)
            assert report.eligible is True


# ── resolve_fallback_chain ──────────────────────────────────────────────

class TestResolveFallbackChain:
    def test_origin_eligible_resolves_in_place(self) -> None:
        reg = get_recipe_registry()
        recipe = reg.get("grid_density_aggregate")
        res = resolve_fallback_chain(recipe, profile={
            "geometryTypes": ["Point"], "featureCount": 100}, registry=reg)
        assert res.resolved is True and res.final_recipe == "grid_density_aggregate"
        assert res.attempts[0].note == "origin_eligible"

    def test_declared_chain_with_reason_gate(self) -> None:
        """原因码门：不匹配的 link 落选（eligible=None），匹配的进入复检。"""
        reg = get_recipe_registry()
        a = _recipe(id="chain_a", required_geometry=["Polygon"])
        b = _recipe(id="chain_b")
        c = _recipe(id="chain_c", required_geometry=["LineString"])
        a.fallback_links = [
            FallbackLink(to="chain_b", reason_code="GEOMETRY_NOT_SUPPORTED"),
            FallbackLink(to="chain_c", reason_code="INSUFFICIENT_POINTS"),
        ]
        for r in (a, b, c):
            reg.register(r)
        try:
            res = resolve_fallback_chain(a, profile={
                "geometryTypes": ["Point"], "featureCount": 100}, registry=reg)
            assert res.resolved is True and res.final_recipe == "chain_b"
            skipped = [x for x in res.attempts if x.note == "reason_code_not_matched"]
            assert len(skipped) == 1 and skipped[0].to_recipe == "chain_c"
        finally:
            reg.unregister("chain_a")
            reg.unregister("chain_b")
            reg.unregister("chain_c")

    def test_sort_key_arbitration_records_losers(self) -> None:
        """多条同时 eligible → (priority, id) 取最优，落选者留痕。"""
        reg = get_recipe_registry()
        a = _recipe(id="arb_a", required_geometry=["Polygon"])
        lo = _recipe(id="arb_lo", required_geometry=[], priority=10)
        hi = _recipe(id="arb_hi", required_geometry=[], priority=90)
        a.fallback_links = [FallbackLink(to="arb_hi"), FallbackLink(to="arb_lo")]
        for r in (a, lo, hi):
            reg.register(r)
        try:
            res = resolve_fallback_chain(a, profile={
                "geometryTypes": ["Point"], "featureCount": 50}, registry=reg)
            assert res.final_recipe == "arb_lo"
            demoted = [x for x in res.attempts
                       if x.to_recipe == "arb_hi" and "demoted_by_sort_key" in x.note]
            assert len(demoted) == 1
        finally:
            for rid in ("arb_a", "arb_lo", "arb_hi"):
                reg.unregister(rid)

    def test_generic_fallback_auto_generated(self) -> None:
        """无声明链 → 通用兜底（auto_generated=True）。"""
        reg = get_recipe_registry()
        orphan = _recipe(id="orphan_a", required_geometry=["LineString"])
        reg.register(orphan)
        try:
            res = resolve_fallback_chain(orphan, profile={
                "geometryTypes": ["Point"], "featureCount": 100}, registry=reg)
            assert res.resolved is True
            assert res.auto_generated_used is True
            assert res.final_recipe in DEFAULT_FALLBACK_CHAIN
            assert all(a.auto_generated for a in res.attempts)
        finally:
            reg.unregister("orphan_a")

    def test_cycle_guard_and_depth_limit(self) -> None:
        reg = get_recipe_registry()
        x = _recipe(id="cyc_x", required_geometry=["LineString"])
        y = _recipe(id="cyc_y", required_geometry=["LineString"])
        x.fallback_links = [FallbackLink(to="cyc_y")]
        y.fallback_links = [FallbackLink(to="cyc_x")]
        for r in (x, y):
            reg.register(r)
        try:
            res = resolve_fallback_chain(x, profile={
                "geometryTypes": ["Point"], "featureCount": 10}, registry=reg)
            assert res.resolved is False and res.exhausted is True
            notes = " ".join(a.note for a in res.attempts)
            assert "cycle_guard" in notes or res.auto_generated_used
        finally:
            reg.unregister("cyc_x")
            reg.unregister("cyc_y")

    def test_primary_reason_code(self) -> None:
        res = ChainResolution(origin_recipe="o")
        assert res.primary_reason_code == "RECIPE_INELIGIBLE"
        res2 = ChainResolution(origin_recipe="o", attempts=[
            {"step": 1, "from_recipe": "o", "to_recipe": "t",
             "reason_code": "SAMPLE_BELOW_FLOOR"}])
        assert res2.primary_reason_code == "SAMPLE_BELOW_FLOOR"


class TestRenderFallbackForLlm:
    def test_empty_is_empty(self) -> None:
        assert render_fallback_for_llm([]) == ""

    def test_bounded_lines_with_disclosure(self) -> None:
        from app.services.gis_harness.recipes import FallbackDecision
        d = FallbackDecision(
            from_element="a", to_element="b", reason_code="X",
            downgrade_class="degraded", disclosure="数据不足",
            attempts=[{"step": 1}], auto_generated=True)
        text = render_fallback_for_llm([d])
        assert "a → b" in text and "X" in text and "数据不足" in text
        assert "链尝试=1步" in text and "通用兜底" in text


# ── 悬空引用校验（P2 / §5 门禁）────────────────────────────────────────

class TestStartupDanglingGate:
    """启动闸：load_builtins 注入悬空 fallback 引用即失败（§5 门禁）。"""

    def test_load_builtins_dangling_link_fails_startup(self, monkeypatch) -> None:
        import sys
        import types

        from app.services.gis_harness import recipe_packs as packs_mod
        from app.services.gis_harness import recipes as recipes_mod

        fake_name = packs_mod._BASE + "fake_dangling_pack"
        fake = types.ModuleType(fake_name)
        bad = _recipe(id="fake_dangling_src")
        bad.fallback_links = [FallbackLink(to="ghost_target_xyz")]
        fake.RECIPES = [bad]
        monkeypatch.setitem(sys.modules, fake_name, fake)
        monkeypatch.setattr(
            packs_mod, "PACK_MODULES",
            packs_mod.PACK_MODULES + ("fake_dangling_pack",))
        reg = recipes_mod.RecipeRegistry()
        with __import__("pytest").raises(RuntimeError, match="悬空引用"):
            reg.load_builtins()


class TestContextScreenDensity:
    """任务书 §2-P1 契约字段：screen_density（04 线供给前 unknown 放行）。"""

    def test_passthrough_from_profile(self) -> None:
        ctx = EligibilityContext.from_profile({"screenDensity": 12.5})
        assert ctx.screen_density == 12.5

    def test_absent_is_none(self) -> None:
        assert EligibilityContext.from_profile({}).screen_density is None


class TestFactHintWiring:
    """evidence_hint 随链尝试转录；护栏词表只读消费。"""

    def test_evidence_hint_propagates_to_attempt(self) -> None:
        reg = get_recipe_registry()
        a = _recipe(id="hint_a", required_geometry=["Polygon"])
        b = _recipe(id="hint_b")
        a.fallback_links = [FallbackLink(
            to="hint_b", evidence_hint="点主体可退点图")]
        for r in (a, b):
            reg.register(r)
        try:
            res = resolve_fallback_chain(a, profile={
                "geometryTypes": ["Point"], "featureCount": 50}, registry=reg)
            assert res.resolved
            att = next(x for x in res.attempts if x.to_recipe == "hint_b")
            assert att.evidence.get("evidence_hint") == "点主体可退点图"
        finally:
            reg.unregister("hint_a")
            reg.unregister("hint_b")

    def test_protected_task_marked_readonly(self) -> None:
        from app.services.gis_harness.planner import fact_signals

        from app.services.gis_harness.intent import MapRequestIntent

        ctx = EligibilityContext.from_profile({
            "geometryTypes": ["Point"], "featureCount": 100,
            "crs": "EPSG:4326", "crsClass": "geographic"})
        intent = MapRequestIntent(query="自相关", task="spatial_autocorrelation")
        out = fact_signals(ctx, intent=intent)
        assert out["evidence"].get("protected_task") == "spatial_autocorrelation"
        assert all(c.get("protected_task") for c in out["conflicts"])


class TestDanglingFallbackLinkValidation:
    def test_dangling_link_reported(self) -> None:
        from app.services.gis_harness.registry_validation import validate_gis_library

        reg = get_recipe_registry()
        bad = _recipe(id="dangling_holder")
        bad.fallback_links = [FallbackLink(to="no_such_recipe_xyz")]
        reg.register(bad)
        try:
            issues = [i for i in validate_gis_library() if "dangling_holder" in i]
            assert issues and "no_such_recipe_xyz" in issues[0]
        finally:
            reg.unregister("dangling_holder")

    def test_default_chain_targets_exist(self) -> None:
        """通用兜底链目标必须真实注册（全库校验零相关违规）。"""
        from app.services.gis_harness.registry_validation import validate_gis_library

        reg = get_recipe_registry()
        for target in DEFAULT_FALLBACK_CHAIN:
            assert reg.get(target) is not None, target
        issues = [i for i in validate_gis_library() if "DEFAULT_FALLBACK_CHAIN" in i]
        assert issues == []
