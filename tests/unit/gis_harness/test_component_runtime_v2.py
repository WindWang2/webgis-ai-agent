"""map-product-runtime-v2 — 组件 schema / 突变 / placement / payload 契约。

覆盖（Design D1-D3）：
- ComponentPlacement anchor/floating 校验与 position 双写一致化；
- chart_panel / statistics_panel 工厂与 MapSpec 序列化（ChartData 同契约）；
- validate_chart_payload / validate_stats_payload 边界；
- mutate_component upsert（Agent 加面板同一入口）+ placement + variant 走
  descriptor registry（不再有第二套 variant 字符串表）；
- PatchComponentIntent 经 lifecycle engine 事务提交 + 乐观并发（CAS）。
"""
import shutil
import uuid

import pytest
from pydantic import ValidationError

from app.services.gis_harness.components import (
    CartographyComponent,
    ComponentPlacement,
    build_default_components,
    chart_panel_component,
    coerce_variant,
    mutate_component,
    normalize_placement,
    statistics_panel_component,
    valid_variants_for_type,
    validate_chart_payload,
    validate_stats_payload,
)
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    PatchComponentIntent,
    SetLayoutIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"comp-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _spec_components(**kwargs) -> list:
    return build_default_components(primary_cartography="visual_heatmap", **kwargs)


# ── ComponentPlacement ────────────────────────────────────────────────────


def test_placement_anchor_requires_anchor_slot():
    with pytest.raises(ValidationError):
        ComponentPlacement(mode="anchor", anchor=None)


def test_placement_floating_requires_xy():
    with pytest.raises(ValidationError):
        ComponentPlacement(mode="floating", x=10)


def test_placement_floating_bounds_reject_out_of_range():
    with pytest.raises(ValidationError):
        ComponentPlacement(mode="floating", x=10, y=10, width=10)  # < 120
    with pytest.raises(ValidationError):
        ComponentPlacement(mode="floating", x=10, y=10, zIndex=999)


def test_placement_anchor_syncs_position():
    pos, placement = normalize_placement(
        "top-left", ComponentPlacement(mode="anchor", anchor="bottom-right"),
    )
    assert pos == "bottom-right"
    assert placement is not None and placement.anchor == "bottom-right"


def test_placement_floating_keeps_position():
    pos, placement = normalize_placement(
        "top-left", ComponentPlacement(mode="floating", x=24, y=48),
    )
    assert pos == "top-left"
    assert placement is not None and placement.x == 24


def test_component_to_mapspec_serializes_placement():
    comp = chart_panel_component(
        placement=ComponentPlacement(mode="floating", x=100, y=80, width=320, height=260),
    )
    spec = comp.to_mapspec()
    assert spec["placement"] == {
        "mode": "floating", "x": 100, "y": 80, "width": 320, "height": 260,
    }
    # 旧字段仍在（向后兼容：只读 position 的消费者不漂移）
    assert spec["position"] == "top-left"


def test_legacy_component_without_placement_roundtrips():
    comp = CartographyComponent.model_validate(
        {"id": "t", "type": "title", "position": "top-center", "options": {"text": "x"}},
    )
    assert comp.placement is None
    assert "placement" not in comp.to_mapspec()


# ── chart / statistics payload 契约 ───────────────────────────────────────


def test_validate_chart_payload_accepts_valid_bar():
    assert validate_chart_payload(
        {"type": "bar", "title": "各区学校数", "data": [{"name": "武侯区", "value": 88}]},
    ) is None


def test_validate_chart_payload_rejects_bad_type_and_points():
    assert validate_chart_payload({"type": "rose", "title": "t", "data": [{"name": "a", "value": 1}]})
    assert validate_chart_payload({"type": "bar", "title": "t", "data": [{"name": "a"}]})
    assert validate_chart_payload({"type": "bar", "title": "t", "data": []})
    assert validate_chart_payload("not-a-dict")


def test_validate_chart_payload_enforces_point_cap():
    data = [{"name": f"n{i}", "value": i} for i in range(501)]
    err = validate_chart_payload({"type": "bar", "title": "t", "data": data})
    assert err and "500" in err


def test_validate_stats_payload_contract():
    assert validate_stats_payload(
        {"items": [{"label": "总数", "value": 123, "unit": "所"}]},
    ) is None
    assert validate_stats_payload({"items": []})
    assert validate_stats_payload({"items": [{"label": "", "value": 1}]})
    assert validate_stats_payload({"items": [{"label": "x", "value": {"bad": 1}}]})
    assert validate_stats_payload("nope")
    too_many = {"items": [{"label": f"l{i}", "value": i} for i in range(25)]}
    assert validate_stats_payload(too_many)


def test_chart_panel_component_factory_inline_and_ref():
    chart = {"type": "pie", "title": "占比", "data": [{"name": "a", "value": 1}]}
    inline = chart_panel_component(chart=chart, position="bottom-right")
    assert inline.options["chart"] == chart
    ref_backed = chart_panel_component(chart_ref="ref:chart-abc123", variant="compact")
    assert ref_backed.options["chartRef"] == "ref:chart-abc123"
    assert ref_backed.variant == "compact"


def test_statistics_panel_component_factory():
    stats = {"title": "成都小学", "items": [{"label": "学校总数", "value": "432"}]}
    comp = statistics_panel_component(stats=stats)
    assert comp.options["stats"] == stats
    assert comp.type == "statistics_panel"


# ── variant 单一权威（descriptor registry）───────────────────────────────


def test_valid_variants_from_descriptor_registry():
    assert set(valid_variants_for_type("north_arrow")) == {
        "compass_minimal_black", "compass_needle", "compass_rose", "arrow_simple",
    }
    assert "horizontal" in valid_variants_for_type("continuous_colorbar")


def test_coerce_variant_falls_back_to_default():
    assert coerce_variant("north_arrow", "compass_rose") == "compass_rose"
    assert coerce_variant("north_arrow", "nonexistent") == "compass_minimal_black"
    # 未知类型（目录缺失）宽松回退：返回原值
    assert coerce_variant("mystery_type", "anything") == "anything"


# ── mutate_component：placement / variant / upsert ────────────────────────


def test_mutate_component_placement_floating():
    components = _spec_components()
    mutated, change = mutate_component(
        components, component_type="north_arrow",
        placement={"mode": "floating", "x": 12, "y": 34, "collapsed": True},
    )
    assert change is not None
    target = next(c for c in mutated if c.type == "north_arrow")
    assert target.placement is not None
    assert (target.placement.x, target.placement.y, target.placement.collapsed) == (12, 34, True)
    # position 保留旧值（旧消费者兜底不漂移）
    assert target.position == "top-right"
    # 原列表不被原地修改
    original = next(c for c in components if c.type == "north_arrow")
    assert original.placement is None


def test_mutate_component_anchor_placement_syncs_position():
    components = _spec_components()
    mutated, change = mutate_component(
        components, component_type="scale_bar",
        placement={"mode": "anchor", "anchor": "bottom-left"},
    )
    target = next(c for c in mutated if c.type == "scale_bar")
    assert target.position == "bottom-left"
    assert target.placement is not None and target.placement.anchor == "bottom-left"


def test_mutate_component_invalid_placement_rejected():
    components = _spec_components()
    with pytest.raises(ValidationError):
        mutate_component(
            components, component_type="north_arrow",
            placement={"mode": "floating", "x": 5},  # 缺 y
        )
    # 校验失败不落半更新状态（原列表未动）
    assert next(c for c in components if c.type == "north_arrow").placement is None


def test_mutate_component_variant_via_registry():
    components = _spec_components()
    mutated, _ = mutate_component(
        components, component_type="north_arrow", variant="compass_rose",
    )
    target = next(c for c in mutated if c.type == "north_arrow")
    assert target.variant == "compass_rose"
    assert target.options["variant"] == "compass_rose"


def test_mutate_component_upsert_creates_chart_panel():
    components = _spec_components()
    assert not any(c.type == "chart_panel" for c in components)
    chart = {"type": "bar", "title": "各区学校数", "data": [{"name": "a", "value": 3}]}
    mutated, change = mutate_component(
        components, component_id="chart-districts", component_type="chart_panel",
        options={"chart": chart}, upsert=True,
    )
    assert change is not None and change.get("created") is True
    created = next(c for c in mutated if c.id == "chart-districts")
    assert created.type == "chart_panel"
    assert created.options["chart"] == chart


def test_mutate_component_upsert_requires_factory_type():
    components = _spec_components()
    # title 有工厂默认吗——不在 _FACTORY_BY_TYPE → 不创建，返回未命中
    mutated, change = mutate_component(
        components, component_id="nope", component_type="title", upsert=True,
    )
    assert change is None
    assert len(mutated) == len(components)


def test_mutate_component_without_upsert_missing_is_noop():
    components = _spec_components()
    mutated, change = mutate_component(
        components, component_id="does-not-exist", enabled=False,
    )
    assert change is None
    assert len(mutated) == len(components)


# ── PatchComponentIntent（lifecycle engine 事务）─────────────────────────


@pytest.mark.asyncio
async def test_patch_component_intent_commits_placement(clean_session):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    await engine.apply_mutation(
        clean_session,
        SetLayoutIntent(
            components=[c.to_mapspec() for c in _spec_components()],
        ),
    )
    res = await engine.apply_mutation(
        clean_session,
        PatchComponentIntent(
            component_id="north-arrow",
            placement={"mode": "floating", "x": 40, "y": 60},
        ),
        origin="agent",
    )
    assert res.is_error is False, res.error_msg
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    target = next(
        c for c in stored["layout"]["components"]
        if c["id"] == "north-arrow"
    )
    assert target["placement"]["x"] == 40
    # 其余组件不动（单组件突变，非整表重排）
    assert any(c["id"] == "scale-bar" for c in stored["layout"]["components"])


@pytest.mark.asyncio
async def test_patch_component_intent_missing_component_errors(clean_session):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    await engine.apply_mutation(
        clean_session,
        SetLayoutIntent(components=[c.to_mapspec() for c in _spec_components()]),
    )
    res = await engine.apply_mutation(
        clean_session,
        PatchComponentIntent(component_id="ghost", enabled=False),
        origin="agent",
    )
    assert res.is_error is True
    assert "ghost" in (res.error_msg or "")


@pytest.mark.asyncio
async def test_patch_component_intent_cas_superseded(clean_session):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    await engine.apply_mutation(
        clean_session,
        SetLayoutIntent(components=[c.to_mapspec() for c in _spec_components()]),
    )
    first = await engine.apply_mutation(
        clean_session,
        PatchComponentIntent(component_id="north-arrow", enabled=False),
        origin="user",
        expected_revision=0,
    )
    assert first.is_error is False
    # 旧 revision 重放 → superseded（用户更新的交互优先于旧决策）
    stale = await engine.apply_mutation(
        clean_session,
        PatchComponentIntent(component_id="north-arrow", enabled=True),
        origin="agent",
        expected_revision=first.mutation_revision - 1,
    )
    assert stale.superseded is True

@pytest.mark.asyncio
async def test_patch_component_intent_upsert_chart_panel(clean_session):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    await engine.apply_mutation(
        clean_session,
        SetLayoutIntent(components=[c.to_mapspec() for c in _spec_components()]),
    )
    chart = {"type": "bar", "title": "各区学校数", "data": [{"name": "a", "value": 1}]}
    res = await engine.apply_mutation(
        clean_session,
        PatchComponentIntent(
            component_id="chart-districts",
            component_type="chart_panel",
            options={"chart": chart},
            placement={"mode": "floating", "x": 20, "y": 20, "width": 300},
            upsert=True,
        ),
        origin="agent",
    )
    assert res.is_error is False, res.error_msg
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    created = next(
        (c for c in stored["layout"]["components"] if c["id"] == "chart-districts"),
        None,
    )
    assert created is not None
    assert created["type"] == "chart_panel"
    assert created["options"]["chart"] == chart


# ── layout_set 组件载荷上限（QA-2026-08-26：LLM 直塞 FeatureCollection）───


@pytest.mark.asyncio
async def test_layout_set_rejects_oversized_component_payload(clean_session):
    """大数据不得经 layout.components 进入 MapSpec（图表走 ref artifact）。"""
    from app.services.mapspec.lifecycle_engine import (
        MapSpecLifecycleEngine, SetLayoutIntent,
    )
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    big_fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
             "properties": {"blob": "x" * 512}}
            for _ in range(400)
        ],
    }
    res = await engine.apply_mutation(
        clean_session,
        SetLayoutIntent(components=[{
            "id": "statistics", "type": "statistics_panel",
            "options": {"data_source": big_fc},
        }]),
        origin="agent",
    )
    assert res.is_error is True
    assert "96KB" in (res.error_msg or "") or "exceed" in (res.error_msg or "")
    # 拒绝不留半更新状态
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    comps = (stored.get("layout") or {}).get("components") if stored else None
    assert not comps or all(
        not (c.get("options") or {}).get("data_source") for c in comps
    )


@pytest.mark.asyncio
async def test_layout_set_accepts_normal_component_payload(clean_session):
    from app.services.mapspec.lifecycle_engine import (
        MapSpecLifecycleEngine, SetLayoutIntent,
    )
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    res = await engine.apply_mutation(
        clean_session,
        SetLayoutIntent(components=[
            {"id": "stats", "type": "statistics_panel",
             "options": {"stats": {"items": [{"label": "总数", "value": 42}]}}},
        ]),
        origin="agent",
    )
    assert res.is_error is False
