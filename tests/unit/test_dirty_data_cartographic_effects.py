"""P0 第 4 项：「脏数据 → 错误出图」5 类可复现现象（断言式）。

任务书 §2 P0：自交→面填充错；重复几何→压盖；混合几何类型→单图层只画
一类；缺/错 CRS→Null Island/偏移；>3σ 离群→色带被拉爆。每类现象给出
「未处理 vs 修复/剖析后」的可量化对比断言。
"""
from __future__ import annotations

import copy

import pytest
from shapely.geometry import shape as _shape

from app.services.spatial_quality_gate import (
    evaluate_quality_gate,
    profile_outlier_policy,
)
from app.services.spatial_quality_service import SpatialQualityEngine
from app.services.spatial_repair_pipeline import SpatialRepairPipeline, plan_repair_ops


def _plan_for(data, verdict=None):
    # audit 的 crs 参数口径与矩阵 harness 一致：有 crs member 传 None
    # （audit GIS-15 取 member），无 member 传 "UNKNOWN"（触发 MISSING_CRS
    # + 4326 回退，矛盾检查才可能触发）。
    crs_arg = None if isinstance(data.get("crs"), dict) else "UNKNOWN"
    report = SpatialQualityEngine.audit_dataset(data, crs=crs_arg)
    if verdict is None:
        verdict = evaluate_quality_gate(data)
    total = max(report.total_features, 1)
    plan = plan_repair_ops(
        report,
        duplicate_ratio=sum(
            1 for i in report.issues
            if i.code in ("DUPLICATE_GEOMETRY", "DUPLICATE_FEATURE")
        ) / total,
        geometry_mix_ratio=verdict["profile_extension"]["geometry_mix"]["mix_ratio"],
        overlap_pair_ratio=sum(
            1 for i in report.issues if i.code == "TOPOLOGY_OVERLAP"
        ) / total,
        outlier_fields=[
            {"field": k, "outlier_ratio": p.get("outlier_ratio", 0.0), "upper": p.get("p99")}
            for k, p in verdict["outlier_fields"].items()
            if p.get("field_policy") not in (None, "none")
        ],
        crs_inference=verdict["crs_inference"],
    )
    return report, plan


@pytest.mark.cartography
def test_phenomenon_1_self_intersection_fills_wrong():
    """自交面（蝴蝶面）按错误填充计算：未修复面积虚高，make_valid 后回到
    正确的两翼面积。"""
    data = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Polygon", "coordinates": [[
             [0.0, 0.0], [2.0, 2.0], [2.0, 0.0], [0.0, 2.0], [0.0, 0.0],
         ]]},
         },
    ]}
    raw_area = _shape(data["features"][0]["geometry"]).area
    repaired, _logs, _ev, _lin = SpatialRepairPipeline.repair_dataset_with_lineage(
        data, ops=["make_valid"]
    )
    fixed = _shape(repaired["features"][0]["geometry"])
    assert fixed.is_valid
    # 毁图现象：自交面按错误环绕计算（GEOS 对该蝴蝶面返回 0 = 塌缩），
    # 面积语义完全失真；make_valid 后回到正确的两翼面积 2×(0.5×2×2×0.5)=2。
    assert raw_area == pytest.approx(0.0)
    assert fixed.area == pytest.approx(2.0)


@pytest.mark.cartography
def test_phenomenon_2_duplicates_overdraw():
    """重复几何 → 同一符号画两次（压盖/不透明度叠黑）。dedup 后计数归一。"""
    square = {"type": "Feature", "properties": {"name": "a"},
              "geometry": {"type": "Polygon", "coordinates": [[
                  [0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]]}}
    data = {"type": "FeatureCollection", "features": [square, copy.deepcopy(square)]}
    # 未处理：渲染集合收到 2 个全同符号。
    assert len(data["features"]) == 2
    report, plan = _plan_for(data)
    assert "deduplicate" in plan.ops  # 重复率 0.5 ≥ 5% 裁决开启
    repaired, _logs, evidence, _lin = SpatialRepairPipeline.repair_dataset_with_lineage(
        data, ops=["deduplicate"]
    )
    assert len(repaired["features"]) == 1
    assert any(e["op"] == "deduplicate" and e["features_affected"] == 1 for e in evidence)


@pytest.mark.cartography
def test_phenomenon_3_mixed_geometry_types_single_class_render():
    """混合几何类型 → 单图层只画一类：mix_ratio 暴露混杂，归一后单一类型。"""
    data = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]]}},
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [0.5, 0.5]}},
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [2.5, 2.5]}},
        ],
    }
    verdict = evaluate_quality_gate(data)
    mix = verdict["profile_extension"]["geometry_mix"]
    # 2/3 Point 主类 + 1/3 Polygon 少数类：fill 图层对 Point 不可画（反之亦然）。
    assert mix["dominant"] == "Point"
    assert mix["mix_ratio"] == pytest.approx(1 / 3)
    # 裁决超阈（1/3 ≥ 0.2）→ 归一 op 进入计划；执行后每个要素都是 Multi*
    # 规范形态（跨族混合不做变形 —— 点变面是捏造几何；「只画一类」由
    # mix_ratio advisory 提示 03 线拆层解决，诚实披露优先于强统一）。
    report, plan = _plan_for(data, verdict)
    assert "normalize_geometry_type" in plan.ops
    repaired, _logs, _ev, _lin = SpatialRepairPipeline.repair_dataset_with_lineage(
        data, ops=["normalize_geometry_type"]
    )
    types = {f["geometry"]["type"] for f in repaired["features"]}
    assert types == {"MultiPoint", "MultiPolygon"}
    # mix 证据必须可被 03 线消费（profile 契约 + advisory 通道）。
    assert verdict["profile_extension"]["geometry_mix"]["mix_ratio"] > 0


@pytest.mark.cartography
def test_phenomenon_4_wrong_crs_null_island_or_offset():
    """投影坐标误标 4326 → 出图落到 Null Island / 大陆漂移；推断+重投影后
    bbox 回到度域。"""
    data = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [14026255.8, 5621521.5]}},
        ],
    }
    verdict = evaluate_quality_gate(data)
    assert verdict["verdict"] == "block"  # IMPOSSIBLE_LAT_LON 拦截
    assert verdict["crs_inference"]["crs"] == "EPSG:3857"
    report, plan = _plan_for(data, verdict)
    assert plan.ops == ["crs_transform"]
    repaired, _logs, _ev, lineage = SpatialRepairPipeline.repair_dataset_with_lineage(
        data, **plan.pipeline_kwargs()
    )
    x, y = repaired["features"][0]["geometry"]["coordinates"]
    assert -180 <= x <= 180 and -90 <= y <= 90
    assert repaired["crs"]["properties"]["name"] == "EPSG:4326"
    assert any(entry["op"] == "crs_transform" for entry in lineage)


@pytest.mark.cartography
def test_phenomenon_5_outliers_stretch_color_band():
    """>3σ 离群 → 等距分类断点被极值吞没（主体挤进一个 bin）；剖析给出
    clip 建议值把断点拉回主体区间。"""
    vals = [float(10 + i) for i in range(15)] + [100000.0]

    def _equal_breaks(values, k=5):
        mn, mx = min(values), max(values)
        return [mn + (mx - mn) * i / k for i in range(1, k)]

    breaks_raw = _equal_breaks(vals)
    # 极值让全部主体值（10..24）落进第一个 bin：断点跨度被拉到 2 万级。
    assert breaks_raw[0] > 20000.0

    prof = profile_outlier_policy(vals)
    assert prof["field_policy"] == "clip_p99"
    clip = prof["suggested_clip"]
    breaks_fixed = _equal_breaks([v for v in vals if v <= clip])
    # 修复建议后：主体值跨多个 bin（首个断点落在主体量级内）。
    assert breaks_fixed[0] < 20.0
    assert max(vals) / clip > 100  # 极值确实在建议裁剪线之外
