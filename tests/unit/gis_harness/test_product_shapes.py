"""Product Shapes（ADR-0183 M3）单元测试。

不变式：shape 只引用既有词表（PRODUCT_ARCHETYPES / output_intents /
chart_kinds）；builder 纯函数、同输入同 spec；required 只来自显式信号。
"""

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.product_shapes import (
    GENERIC_SHAPE,
    PRODUCT_ARCHETYPES_SHAPE_KEYS,
    build_product_spec_from_plan,
    shape_claims,
    shape_for_archetype,
    shape_summary,
)
from app.services.gis_harness.product_spec import (
    DELIVERY_TARGETS,
    spec_digest,
    validate_product_spec,
)
from app.services.gis_harness.product_templates import (
    PRODUCT_ARCHETYPES,
    get_product_template_registry,
)


def _plan_for(query: str):
    from app.services.gis_harness.planner_runtime import get_planner_runtime

    intent = resolve_map_request_intent(query)
    planner = get_planner_runtime()
    plan = planner.plan_from_intent(
        intent, recipe_id=_recipe_for(intent), use_memo=False)
    return intent, plan


def _recipe_for(intent):
    from app.services.gis_harness.planner_runtime import get_planner_runtime

    candidates = get_planner_runtime().recipes.select_candidates(intent)
    return candidates[0].id if candidates else ""


def _template_for(plan, intent):
    reg = get_product_template_registry()
    tpl = reg.find_for_recipe(plan.recipe_id, intent.subject.category or "")
    return tpl


# ── 词表同源（防第二份产品类型目录）────────────────────────────────────


def test_shapes_cover_registered_archetypes():
    assert PRODUCT_ARCHETYPES_SHAPE_KEYS == set(PRODUCT_ARCHETYPES)
    for a in PRODUCT_ARCHETYPES:
        s = shape_for_archetype(a)
        assert s.archetype == a
        assert "map" in s.required_view_kinds


def test_unknown_archetype_falls_to_generic():
    s = shape_for_archetype("hologram_poster")
    assert s is GENERIC_SHAPE
    assert s.required_view_kinds == ("map",)
    assert shape_for_archetype("") is GENERIC_SHAPE


# ── builder：确定性 / 词表一致 / 信号驱动 ────────────────────────────────


def test_builder_flagship_district_statistics():
    """旗舰验收：『成都小学分布情况，各区统计』→ 地图 + 统计（admin 产品）。"""
    intent, plan = _plan_for("成都小学分布情况，各区统计")
    tpl = _template_for(plan, intent)
    spec = build_product_spec_from_plan(plan, intent, tpl)
    errors = validate_product_spec(spec)
    assert errors == [], errors
    assert spec.product_type in set(PRODUCT_ARCHETYPES)
    kinds = {v.kind for v in spec.views}
    assert "map" in kinds
    # 统计信号（output_intents.statistics）→ 统计视图必需
    stats = [v for v in spec.views if v.kind == "stats_panel"]
    assert stats and stats[0].required is True
    # 关系端点全部已登记
    ids = {v.view_id for v in spec.views}
    assert all(r.src in ids and r.dst in ids for r in spec.relations)
    # 交付词表合法
    assert set(spec.delivery.targets) <= set(DELIVERY_TARGETS)
    assert spec.recipe_id == plan.recipe_id


def test_builder_chart_signal_makes_required_chart_view():
    intent, plan = _plan_for("成都小学分布，配个柱状图")
    tpl = _template_for(plan, intent)
    spec = build_product_spec_from_plan(plan, intent, tpl)
    charts = [v for v in spec.views if v.kind == "chart"]
    assert charts, "output_intents.chart → chart 视图必须在场"
    assert charts[0].required is True
    # chart_kind 若非空必须来自 chart_kinds 词表；词表外别名（如 admin_bar）
    # 由编译器 fallback 披露，builder 不猜
    if charts[0].chart_kind:
        from app.lib.cartography.chart_kinds import CHART_KINDS

        assert charts[0].chart_kind in {k.id for k in CHART_KINDS}


def test_builder_deterministic_same_digest():
    intent, plan = _plan_for("成都小学分布情况，各区统计")
    tpl = _template_for(plan, intent)
    s1 = build_product_spec_from_plan(plan, intent, tpl)
    s2 = build_product_spec_from_plan(plan, intent, tpl)
    assert spec_digest(s1) == spec_digest(s2)
    assert s1.model_dump() == s2.model_dump()


def test_builder_simple_view_minimal():
    intent, plan = _plan_for("看看成都的大学")
    tpl = _template_for(plan, intent)
    spec = build_product_spec_from_plan(plan, intent, tpl)
    assert validate_product_spec(spec) == []
    kinds = [v.kind for v in spec.views]
    assert kinds.count("map") == 1
    assert not any(v.required and v.kind != "map" for v in spec.views)
    assert "interactive" in spec.delivery.targets


def test_builder_no_template_still_valid_generic():
    intent, plan = _plan_for("成都小学分布情况，各区统计")
    spec = build_product_spec_from_plan(plan, intent, None)
    assert validate_product_spec(spec) == []
    assert spec.product_type == ""
    assert spec.plan_id == plan.plan_id


# ── shape 投影 ────────────────────────────────────────────────────────


def test_shape_claims_subject_substitution_and_summary():
    shape = shape_for_archetype("regional_comparison")
    claims = shape_claims(shape, "小学")
    assert claims and "小学" in claims[0]
    summary = shape_summary(shape)
    assert summary["archetype"] == "regional_comparison"
    assert ("comparison", "chart", "stats_panel") in [
        tuple(g) for g in summary["required_any_kinds"]
    ]
