"""label_plan 契约测试（ac-05，ADR-0154）—— 字段挑选 + 策略编排.

P1/P2 golden 面：确定性（同输入两次求解 model_dump 相等）、10 数据集
ground truth 准确率 ≥ 90%（门禁）、无 name-like 字段 100% 不标注
（禁止 ID 当标注）、密度分档与 zoom 分级编排、spec 契约组装。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))

import labeling_datasets as ld
from app.lib.cartography.label_plan import (
    DENSE_FEATURE_COUNT,
    EXTREME_FEATURE_COUNT,
    LABEL_FIELD_MIN_SCORE,
    build_label_spec,
    choose_label_field,
    field_stats_from_features,
    plan_label_strategy,
    score_label_field,
)
from app.lib.cartography.component_registry import get_component_registry
from app.services.gis_harness.components import (
    label_layer_component,
    mutate_component,
    rebind_component,
)


def _stats(dataset_id: str):
    fc = ld.build_dataset(dataset_id)
    return fc, field_stats_from_features(fc["features"])


def _choice(dataset_id: str):
    fc, stats = _stats(dataset_id)
    profile = {"featureCount": len(fc["features"]), "fields": {}}
    return choose_label_field(profile, field_stats=stats), profile, stats


# ── P7 门禁：10 数据集准确率 ────────────────────────────────────────────
def test_ground_truth_accuracy_at_least_90_percent():
    hits = 0
    misses = []
    for did, entry in ld.GROUND_TRUTH.items():
        choice, _, _ = _choice(did)
        if choice.field == entry["best_field"]:
            hits += 1
        else:
            misses.append((did, choice.field, entry["best_field"]))
    assert hits / len(ld.GROUND_TRUTH) >= 0.90, f"misses={misses}"


def test_all_ten_datasets_hit_currently():
    """当前实现 10/10 —— 回归护栏（准确率门禁之外的强断言）。"""
    for did, entry in ld.GROUND_TRUTH.items():
        choice, _, _ = _choice(did)
        assert choice.field == entry["best_field"], did


# ── 确定性 golden ───────────────────────────────────────────────────────
def test_choose_and_plan_are_deterministic():
    fc, stats = _stats("hotels_dense")
    profile = {"featureCount": len(fc["features"]), "fields": {}}
    a = choose_label_field(profile, field_stats=stats)
    b = choose_label_field(profile, field_stats=stats)
    assert a.model_dump() == b.model_dump()
    pa = plan_label_strategy(profile, field_choice=a, field_stats=stats)
    pb = plan_label_strategy(profile, field_choice=a, field_stats=stats)
    assert pa.model_dump() == pb.model_dump()
    sa = build_label_spec(profile, field_stats=stats)
    sb = build_label_spec(profile, field_stats=stats)
    assert sa == sb


def test_choice_artifact_carries_rejected_and_reasons():
    choice, _, _ = _choice("cn_provinces")
    assert choice.field == "名称"
    assert choice.confidence >= LABEL_FIELD_MIN_SCORE
    assert choice.reasons and "exact_vocab_hit" in choice.reasons[0]
    assert choice.runner_up is not None
    rejected_ids = [r.field for r in choice.rejected]
    assert "OBJECTID" in rejected_ids and "code" in rejected_ids
    # 诱饵字段的一等否决记录带稳定理由码
    by_name = {r.field: r for r in choice.rejected}
    assert "id_like_field_name" in by_name["OBJECTID"].reason
    assert "time_like_field_name" in by_name["更新时间"].reason


# ── 无 name-like 字段 → 100% 不标注（禁止 ID 当标注）───────────────────
def test_no_name_like_field_means_no_label_with_advisory():
    for did, entry in ld.GROUND_TRUTH.items():
        if entry["best_field"] is not None:
            continue
        choice, _, _ = _choice(did)
        assert choice.field is None, did
        assert choice.advisory, did
        assert choice.confidence < LABEL_FIELD_MIN_SCORE
    # sensors 专项：device_id 明确被语义排除，绝不被选中
    choice, _, _ = _choice("sensors")
    by_name = {r.field: r for r in choice.rejected}
    assert "device_id" in by_name
    assert "code_like_values" in by_name["device_id"].reason


def test_numeric_and_code_fields_are_rejected_everywhere():
    """所有数据集里主键/编码/时间戳字段的评分恒为 0（结构性排除）。"""
    decoys = {
        "cn_provinces": ["OBJECTID", "code", "更新时间"],
        "sensors": ["device_id", "last_seen"],
        "hotels_dense": ["id"],
        "metro_stations_en": ["station_id"],
    }
    for did, fields in decoys.items():
        fc, stats = _stats(did)
        by_name = {s.name: s for s in stats}
        for fname in fields:
            score, reasons = score_label_field(by_name[fname])
            assert score == 0.0, (did, fname, score, reasons)


# ── tie-break 与词表次序 ────────────────────────────────────────────────
def test_exact_vocab_beats_substring_and_title_fallback():
    # countries_mixed：name（exact）> 中文名称（子串）
    choice, _, _ = _choice("countries_mixed")
    assert choice.field == "name"
    assert choice.runner_up == "中文名称"
    # metro：无 name 字段 → title 档（词汇次序）
    choice, _, _ = _choice("metro_stations_en")
    assert choice.field == "title"


def test_low_cardinality_categorical_beaten_by_cardinality_penalty():
    # cn_cities_points：类别（值恒“地级市”）有子串词表分，但基数惩罚压过
    fc, stats = _stats("cn_cities_points")
    by_name = {s.name: s for s in stats}
    score, reasons = score_label_field(by_name["类别"])
    assert score == 0.0 or any(r.startswith("low_cardinality") for r in reasons)


# ── P2 策略编排 ─────────────────────────────────────────────────────────
def test_density_tiers_drive_mode():
    # 稀疏 → all（bands 饱和为全量）
    choice, profile, stats = _choice("cn_cities_points")
    st = plan_label_strategy(profile, field_choice=choice, field_stats=stats)
    assert st.mode == "all" and st.top_n is None
    assert len(st.zoom_bands) == 4
    assert all(b.top_ratio == 1.0 for b in st.zoom_bands)
    # 密集（3200 > 2000）→ top_n
    choice, profile, stats = _choice("hotels_dense")
    st = plan_label_strategy(profile, field_choice=choice, field_stats=stats)
    assert st.mode == "top_n"
    assert st.top_n == 400
    assert any(r.startswith("dense(") for r in st.reasons)
    # 极端密度 → hover_only
    profile_extreme = {"featureCount": EXTREME_FEATURE_COUNT + 1, "fields": {}}
    st = plan_label_strategy(profile_extreme, field_choice=choice, field_stats=stats)
    assert st.mode == "hover_only" and st.top_n is None


def test_dense_threshold_constant_and_topn_tiers():
    assert DENSE_FEATURE_COUNT == 2000
    st1 = plan_label_strategy({"featureCount": 2001})
    assert st1.mode == "top_n" and st1.top_n == 400
    st2 = plan_label_strategy({"featureCount": 8001})
    assert st2.mode == "top_n" and st2.top_n == 250
    st3 = plan_label_strategy({"featureCount": 2000})
    assert st3.mode == "all"


def test_priority_field_detection_order():
    # 词表次序：population 在 value/area 之前
    choice, profile, stats = _choice("cn_cities_points")
    st = plan_label_strategy(profile, field_choice=choice, field_stats=stats)
    assert st.priority_field == "population"
    assert st.priority_source == "value_field"
    # 无数值字段（纯名称 line 数据不存在于此 —— bus_routes 有 fleet_count）
    choice, profile, stats = _choice("bus_routes")
    st = plan_label_strategy(profile, field_choice=choice, field_stats=stats)
    assert st.priority_field == "fleet_count"


def test_no_field_strategy_is_hover_only():
    choice, profile, stats = _choice("sensors")
    st = plan_label_strategy(profile, field_choice=choice, field_stats=stats)
    assert st.mode == "hover_only"
    assert st.zoom_bands == []
    assert "no_label_field" in st.reasons


def test_default_zoom_bands_four_tiers():
    from app.lib.cartography.label_plan import DEFAULT_ZOOM_BANDS
    assert len(DEFAULT_ZOOM_BANDS) == 4
    ratios = [b.top_ratio for b in DEFAULT_ZOOM_BANDS]
    assert ratios == sorted(ratios) and ratios[-1] == 1.0


# ── spec 契约组装 ───────────────────────────────────────────────────────
def test_build_label_spec_shape_camel_case():
    fc, stats = _stats("hotels_dense")
    profile = {"featureCount": len(fc["features"]), "fields": {}}
    spec = build_label_spec(profile, field_stats=stats)
    assert spec is not None
    assert spec["field"] == "name"
    assert spec["mode"] == "top_n"
    assert spec["topN"] == 400
    assert spec["priorityField"] == "stars"
    assert spec["haloMode"] == "auto"
    assert spec["sizeRatio"] == 1.0
    assert len(spec["zoomBands"]) == 4
    band = spec["zoomBands"][0]
    assert set(band) == {"minZoom", "maxZoom", "topRatio", "sizeRatio"}


def test_build_label_spec_explicit_field_override():
    fc, stats = _stats("countries_mixed")
    profile = {"featureCount": len(fc["features"]), "fields": {}}
    spec = build_label_spec(profile, field_stats=stats, explicit_field="中文名称")
    assert spec["field"] == "中文名称"
    assert any(r.startswith("explicit_field(") for r in spec["_reasons"])


def test_build_label_spec_none_when_no_candidate():
    fc, stats = _stats("sensors")
    profile = {"featureCount": len(fc["features"]), "fields": {}}
    assert build_label_spec(profile, field_stats=stats) is None


def test_build_label_spec_consumes_real_profiler_shape():
    from app.services.spatial_meta_profiler import profile_geojson_source
    fc = ld.build_dataset("air_quality_stations")
    spec = build_label_spec(profile_geojson_source(fc))
    assert spec is not None and spec["field"] == "站点名称"


# ── P6：label_layer 组件可寻址 ─────────────────────────────────────────
def test_label_layer_descriptor_registered():
    reg = get_component_registry()
    desc = reg.get("label_layer")
    assert desc is not None
    assert desc.category == "content.label_layer"
    assert desc.requires_layer_binding is True
    assert desc.default_variant in desc.variants
    assert reg.validate() == []
    # 分类目录学：content.label_layer 在 taxonomy 有定义且有 descriptor
    from app.lib.cartography.component_taxonomy import get_component_category_registry
    assert get_component_category_registry().has("content.label_layer")


def test_label_layer_component_auto_and_refusal():
    from app.services.spatial_meta_profiler import profile_geojson_source
    c = label_layer_component(profile=profile_geojson_source(ld.build_dataset("cn_cities_points")))
    assert c.type == "label_layer"
    assert c.options["auto"] is True and c.options["field"] == "name"
    assert "label" in c.options and c.options["label"]["mode"] == "all"
    # sensors：诚实空绑定（不用 device_id 凑数）
    c2 = label_layer_component(profile=profile_geojson_source(ld.build_dataset("sensors")))
    assert c2.options["auto"] is False
    assert "field" not in c2.options or c2.options.get("field") is None
    assert c2.options.get("advisory") == "no_name_like_field"


def test_label_layer_rebind_field_is_local_mutation():
    from app.services.spatial_meta_profiler import profile_geojson_source
    c = label_layer_component(profile=profile_geojson_source(ld.build_dataset("countries_mixed")))
    assert c.options["field"] == "name"
    new_list, change, err = rebind_component(
        [c], component_id=c.id, bindings={"field": "中文名称"},
    )
    assert err is None, err
    rebound = new_list[0]
    assert rebound.options["field"] == "中文名称"
    # 局部突变：id/type/其余 options 原样
    assert rebound.id == c.id and rebound.type == c.type
    assert rebound.options.get("auto") == c.options.get("auto")
    # 非白名单绑定键被拒
    _, _, err2 = rebind_component([c], component_id=c.id, bindings={"textStyle": "x"})
    assert err2


def test_label_layer_mutate_component_options_patch():
    from app.services.spatial_meta_profiler import profile_geojson_source
    c = label_layer_component(profile=profile_geojson_source(ld.build_dataset("hotels_dense")))
    new_list, change = mutate_component(
        [c], component_id=c.id, options={"mode": "hover_only"},
    )
    assert change is not None
    assert new_list[0].options["mode"] == "hover_only"
    assert new_list[0].options["field"] == "name"


def test_label_layer_in_component_type_vocabulary():
    from typing import get_args
    from app.services.gis_harness.components import ComponentType
    assert "label_layer" in get_args(ComponentType)


def test_label_layer_to_mapspec_passes_schema_validation():
    """MapSpec 通道闭环：label_layer 组件落 layout.components 必须能过
    mapspec_schema 校验（COMPONENT_TYPES union 含 label_layer；TS 投影同源）。"""
    from app.lib.cartography.mapspec_schema import MapSpecComponent
    from app.services.spatial_meta_profiler import profile_geojson_source
    c = label_layer_component(profile=profile_geojson_source(ld.build_dataset("cn_cities_points")))
    MapSpecComponent.model_validate(c.to_mapspec())
    c2 = label_layer_component(field="name", layer_id="lyr-1")
    MapSpecComponent.model_validate(c2.to_mapspec())


# ── P7 门禁：密集层重叠率下降 ≥50%（报表脚本的确定性度量入测）──────────
def test_dense_overlap_reduction_gate():
    import scripts.label_quality_report as report
    drop = report.overlap_reduction_table(3200)
    assert drop >= 0.50

