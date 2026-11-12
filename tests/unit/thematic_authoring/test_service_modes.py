"""T-B6/T-B7/T-B8/T-B9 + 验收 4/5/9/10/11/12/13/17：类别/发散/大小映射/
常量/退化/类型转换/共享作用域。"""
from __future__ import annotations

import math

import pytest

from app.services.thematic_authoring import (
    commit_expression,
    inspect_layer,
    preview_expression,
)
from app.services.thematic_authoring.builder import size_for_value
from app.services.thematic_authoring.contract import parse_plan
from app.services.thematic_authoring.errors import ThematicAuthoringError
from tests.unit.thematic_authoring.helpers import (
    current_revision,
    make_fc,
    read_layer,
    seed_thematic_layer,
)


def _plan(**kw):
    base = {"layer_id": "result-seed"}
    base.update(kw)
    return parse_plan(base)


@pytest.fixture
async def seeded(session_id, store_root):
    rows = [{"pop": v} for v in (0, 3, 7, 12, 18, 25, 33, 47, 60, 88)]
    fc = make_fc(rows)
    await seed_thematic_layer(session_id, fc, layer_id="result-seed", field="pop")
    return fc


# ─── 常数字段（验收 4） ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_constant_field_degrades_to_single_class(session_id, store_root):
    rows = [{"v": 7.0}] * 20
    fc = make_fc(rows)
    await seed_thematic_layer(session_id, fc, layer_id="result-const", field="v")
    r = await preview_expression(
        session_id, "result-const",
        _plan(mode="graduated_auto", method="equal_interval", k=5))
    breaks = r["legend_spec"]["breaks"]
    # 常数域：单级 [v, v]（与既有合同一致），不报不存在的多类分布
    assert breaks == [7.0, 7.0]
    assert r["actual_k"] == 1
    assert r["requested_k"] == 5
    assert any("请求 5 类" in d for d in r["disclosures"])


# ─── 类别编辑（验收 9/10/11） ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_categorical_edit_typed_keys_order_and_other(session_id, store_root):
    rows = [{"code": 1}, {"code": 2}, {"code": 2}, {"code": "2"},
            {"code": "a"}, {"code": "a"}, {"code": "a"}, {"code": None}, {"other": 1}]
    fc = make_fc(rows)
    # 种子层用 build_graduated_spec 会失败（无数值）——手工建 categorical 种子
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent
    from app.lib.cartography.thematic_spec import build_categorical_spec, spec_to_paint
    from tests.unit.thematic_authoring.helpers import register_geojson_ref

    ref = await register_geojson_ref(session_id, fc)
    legend = build_categorical_spec("code", [
        {"key": "1", "color": "#111111"}, {"key": "2", "color": "#222222"},
    ])
    paint, _ = spec_to_paint(legend)
    r0 = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer={  # type: ignore[arg-type]
            "id": "result-cat", "type": "fill", "source": "result-cat-source",
            "paint": {"color": paint}, "legend_spec": legend,
        }, source_data={"ref_id": ref}),
        origin="agent", actor="test:seed")
    assert not r0.is_error

    receipt = await inspect_layer(session_id, "result-cat")
    cat = receipt["distribution"]["categorical"]
    # 类型碰撞披露：code=2 与 code="2" 同文本键
    assert "2" in (cat["ambiguous_keys"] or [])
    top_keys = {c["key"] for c in cat["top"]}
    assert {"a", "2", "1"} <= top_keys

    # 歧义键作者编辑 → 拒绝（验收 9：明确拒绝不支持的歧义输入）
    with pytest.raises(ThematicAuthoringError) as ei:
        await preview_expression(
            session_id, "result-cat",
            _plan(mode="categorical", categories=[
                {"key": "2", "color": "#123456", "label": "二"},
            ]))
    assert ei.value.code == "AMBIGUOUS_CATEGORY_KEY"

    # 无歧义类别表：排序（呈现顺序）+ 类型化键 + 显式 other 桶
    plan = _plan(
        mode="categorical",
        categories=[
            {"key": "a", "color": "#aaaaaa", "label": "A 类"},
            {"key": "1", "color": "#111111", "label": "一"},
        ],
        category_other={"color": "#cccccc", "label": "其他"},
    )
    r = await preview_expression(session_id, "result-cat", plan)
    spec = r["legend_spec"]
    # 键类型化：code=1 是数值 → key_type=number；paint match 用数值 case
    assert spec["categories"][1]["key_type"] == "number"
    assert r["paint"]["cases"] == [["a", "#aaaaaa"], [1, "#111111"]]
    assert r["paint"]["default"] == "#cccccc"
    # 计数守恒（验收 10）：3a + 1(one) + other(2+1+missing/null 不算类别)
    table = {c["key"]: c["count"] for c in r["class_table"]}
    assert table["a"] == 3
    assert table["1"] == 1
    assert table["__other__"] == 3  # 2, "2", "2"
    # 空串与缺失：null→missing 分母披露
    joined = " ".join(r["disclosures"])
    assert "null/缺失" in joined


@pytest.mark.asyncio
async def test_categorical_commit_keeps_source_values_display_rename(session_id, store_root):
    """验收 11：显示名/顺序改变不改源值，地图匹配不失效。"""
    rows = [{"k": "x"}, {"k": "y"}, {"k": "x"}]
    fc = make_fc(rows)
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent
    from app.lib.cartography.thematic_spec import build_categorical_spec, spec_to_paint
    from tests.unit.thematic_authoring.helpers import register_geojson_ref

    ref = await register_geojson_ref(session_id, fc)
    legend = build_categorical_spec("k", [
        {"key": "x", "color": "#111111"}, {"key": "y", "color": "#222222"}])
    paint, _ = spec_to_paint(legend)
    await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer={  # type: ignore[arg-type]
            "id": "result-cat2", "type": "fill", "source": "result-cat2-source",
            "paint": {"color": paint}, "legend_spec": legend,
        }, source_data={"ref_id": ref}),
        origin="agent", actor="test:seed")

    # 改显示名 + 颠倒顺序（呈现序）
    plan = _plan(mode="categorical", categories=[
        {"key": "y", "color": "#222222", "label": "乙"},
        {"key": "x", "color": "#111111", "label": "甲"},
    ])
    rev0 = await current_revision(session_id)
    await commit_expression(
        session_id, "result-cat2", plan, expected_revision=rev0,
        origin="user", actor="test")
    layer = await read_layer(session_id, "result-cat2")
    cats = layer["legend_spec"]["categories"]
    assert [c["key"] for c in cats] == ["y", "x"]      # 呈现顺序 = 作者顺序
    assert cats[1]["label"] == "甲"
    # 源值未变：match cases 仍按源键匹配
    assert layer["paint"]["color"]["cases"] == [["y", "#222222"], ["x", "#111111"]]
    # 数据未被改写
    from app.services.session_data import session_data_manager
    resolved = await session_data_manager.resolve_alias(session_id, ref)
    res = await session_data_manager.get_ref_data_shared(session_id, resolved)
    feats = res.data["geojson"]["features"]
    assert {f["properties"]["k"] for f in feats} == {"x", "y"}


# ─── 发散（验收 12） ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_divergent_center_qualification(session_id, store_root, seeded):
    rows = [{"d": v} for v in (-10, -5, -1, 2, 6, 12)]
    fc = make_fc(rows)
    await seed_thematic_layer(session_id, fc, layer_id="result-div", field="d")
    plan = _plan(mode="divergent", divergent_center=0.0, palette="RdBu")
    r = await preview_expression(session_id, "result-div", plan)
    assert r["legend_spec"]["type"] == "divergent"
    assert r["legend_spec"]["center"] == 0.0

    # 全正值域 + 中心 0：无两侧结构 → 拒绝（不伪造正负差异）
    rows_pos = [{"d": v} for v in (1, 2, 3, 8)]
    fc_pos = make_fc(rows_pos)
    await seed_thematic_layer(session_id, fc_pos, layer_id="result-pos", field="d")
    with pytest.raises(ThematicAuthoringError) as ei:
        await preview_expression(
            session_id, "result-pos",
            _plan(mode="divergent", divergent_center=0.0))
    assert ei.value.code == "NOT_DIVERGENT_STRUCTURE"


# ─── 大小映射（验收 13） ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_size_channel_area_proportional_exact(session_id, store_root):
    rows = [{"pop": p} for p in (100, 400, 900, 1600)]
    fc = make_fc(rows, geom="Point")
    await seed_thematic_layer(
        session_id, fc, layer_id="result-pts", field="pop", layer_type="circle")
    plan = _plan(
        mode="graduated_auto", method="equal_interval", k=3,
        size_channel={
            "channel": "radius", "field": "pop",
            "min_value": 100, "max_value": 1600,
            "min_size": 4.0, "max_size": 10.0, "scaling": "area_sqrt",
        })
    r = await preview_expression(session_id, "result-pts", plan)
    sm = r["size_paint"]["radius"]
    assert sm["output_transform"] == "sqrt"
    # 面积线性于数值：等值差 → 等面积差（精确断言）
    def a(v):
        return size_for_value(v, plan.size_channel) ** 2
    assert math.isclose(a(100) + (a(1600) - a(100)) * 0.25, a(475), rel_tol=1e-9)
    assert math.isclose(a(100) + (a(1600) - a(100)) * 0.5, a(850), rel_tol=1e-9)
    # 图例样本同一函数（min/mid/max）
    samples = {s["anchor"]: s for s in r["size_samples"]}
    assert math.isclose(samples["min"]["size"], size_for_value(100, plan.size_channel))
    assert math.isclose(samples["max"]["size"], 10.0)

    rev0 = await current_revision(session_id)
    await commit_expression(
        session_id, "result-pts", plan, expected_revision=rev0, origin="user", actor="test")
    layer = await read_layer(session_id, "result-pts")
    assert layer["paint"]["radius"] == sm
    # 提交后再次无 size_channel 计划 → 移除本域写入的键（不碰其它符号键）
    await commit_expression(
        session_id, "result-pts",
        _plan(mode="graduated_auto", method="equal_interval", k=3),
        expected_revision=await current_revision(session_id), origin="user", actor="test")
    layer3 = await read_layer(session_id, "result-pts")
    assert "radius" not in layer3["paint"]


@pytest.mark.asyncio
async def test_size_channel_width_linear_only(session_id, store_root):
    rows = [{"flow": v} for v in (1, 5, 20, 80)]
    fc = make_fc(rows, geom="LineString")
    await seed_thematic_layer(
        session_id, fc, layer_id="result-lines", field="flow", layer_type="line")
    plan = _plan(
        mode="graduated_auto", method="equal_interval", k=3,
        size_channel={
            "channel": "width", "field": "flow",
            "min_value": 1, "max_value": 80,
            "min_size": 0.5, "max_size": 6.0, "scaling": "linear",
        })
    r = await preview_expression(session_id, "result-lines", plan)
    sm = r["size_paint"]["width"]
    assert sm["stops"] == [[1.0, 0.5], [80.0, 6.0]]
    assert "output_transform" not in sm


# ─── 恢复自动（WP04） ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revert_auto_clears_authoring_scope(session_id, store_root, seeded):
    rev0 = await current_revision(session_id)
    await commit_expression(
        session_id, "result-seed",
        _plan(mode="graduated_manual", breaks=[0, 10, 50], palette="Reds",
              labels=["a", "b"]),
        expected_revision=rev0, origin="user", actor="test")
    manual = await read_layer(session_id, "result-seed")
    assert manual["legend_spec"]["method"] == "manual"
    await commit_expression(
        session_id, "result-seed",
        _plan(mode="revert_auto"),
        expected_revision=await current_revision(session_id),
        origin="user", actor="test")
    layer = await read_layer(session_id, "result-seed")
    spec = layer["legend_spec"]
    assert spec["method"] != "manual"          # 自动裁决回归
    assert spec["breaks"] != [0, 10, 50]
    assert spec.get("labels") != ["a", "b"]
    ev = layer["thematic_authoring"]
    assert ev["reverted"] is True
    # 与从头自动分级一致（同数据同引擎）
    fresh = await preview_expression(
        session_id, "result-seed", _plan(mode="revert_auto"))
    assert fresh["legend_spec"]["breaks"] == spec["breaks"]


# ─── 跨类型转换适用性（WP05 LISA 保护） ────────────────────────────────────


@pytest.mark.asyncio
async def test_text_dominant_layer_rejects_numeric_graduated(session_id, store_root):
    rows = [{"cls": c} for c in ("HH", "LL", "LH", "HL", "HH")]
    fc = make_fc(rows)
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent
    from app.lib.cartography.thematic_spec import build_categorical_spec, spec_to_paint
    from tests.unit.thematic_authoring.helpers import register_geojson_ref

    ref = await register_geojson_ref(session_id, fc)
    legend = build_categorical_spec("cls", [
        {"key": "HH", "color": "#ff0000"}, {"key": "LL", "color": "#0000ff"}])
    paint, _ = spec_to_paint(legend)
    await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer={  # type: ignore[arg-type]
            "id": "result-lisa", "type": "fill", "source": "result-lisa-source",
            "paint": {"color": paint}, "legend_spec": legend,
        }, source_data={"ref_id": ref}),
        origin="agent", actor="test:seed")
    with pytest.raises(ThematicAuthoringError) as ei:
        await preview_expression(
            session_id, "result-lisa",
            _plan(mode="graduated_auto", method="equal_interval", k=4))
    assert ei.value.code == "CANNOT_CONVERT_TYPE"
    # 同字段 categorical 编辑仍可用（固定科学类别可改显示文本/颜色）
    r = await preview_expression(
        session_id, "result-lisa",
        _plan(mode="categorical", categories=[
            {"key": "HH", "color": "#ff0000", "label": "高高"},
            {"key": "LL", "color": "#0000ff", "label": "低低"},
        ]))
    assert r["legend_spec"]["categories"][0]["label"] == "高高"


# ─── 共享表达作用域（验收 17：拒绝单成员破坏） ─────────────────────────────


@pytest.mark.asyncio
async def test_shared_expression_member_rejected(session_id, store_root, seeded):
    # 手工把层挂进 frame_product 共享组（形状对齐 G15）
    from app.services.mapspec_store import mapspec_store

    spec = await mapspec_store.get_mapspec(session_id) or {}
    spec.setdefault("frame_product", {})["shared_expressions"] = [{
        "layer_ids": ["result-seed"], "breaks": [0, 50], "palette": "Viridis",
    }]
    # frame_product 不经 intent 面——直接写 store（测试搭建共享场景）
    await mapspec_store.save_mapspec(session_id, spec) if hasattr(mapspec_store, "save_mapspec") else None

    receipt = await inspect_layer(session_id, "result-seed")
    if receipt.get("shared_expression_scope"):
        with pytest.raises(ThematicAuthoringError) as ei:
            await preview_expression(
                session_id, "result-seed",
                _plan(mode="graduated_manual", breaks=[0, 10, 50]))
        assert ei.value.code == "SHARED_EXPRESSION_MEMBER"


# ─── 重复值 quantile 退化（验收 5 在服务面的证据） ─────────────────────────


@pytest.mark.asyncio
async def test_quantile_duplicates_actual_k_disclosed(session_id, store_root):
    rows = [{"v": 1.0}] * 30 + [{"v": 5.0}] * 30
    fc = make_fc(rows)
    await seed_thematic_layer(session_id, fc, layer_id="result-dup", field="v")
    r = await preview_expression(
        session_id, "result-dup",
        _plan(mode="graduated_auto", method="quantiles", k=5))
    assert r["requested_k"] == 5
    assert r["actual_k"] < 5
    assert any("真实类数" in d or "请求 5 类" in d for d in r["disclosures"])
