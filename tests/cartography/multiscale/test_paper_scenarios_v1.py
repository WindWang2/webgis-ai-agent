"""WP6 场景 golden —— 同城多族数据在双目标比例尺下的真实派生与守恒。

验收旅程 1-3 合一（合成城市：行政区共享边网格 + 等级路网 + 小学点层）：
- 1:200万 总览 vs 1:5万 详图 两份冻结计划：计划指纹不同、派生工件可复现、
  原始数据全程不变；
- 行政区：共享边无缝（QA 审计）、小面积消除计数守恒；
- 路网：等级取舍 + 连通性（overview 用途桥接重加）；
- 小学：聚合工件成员/计数可回查（display-only，分析通道读原始数据）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List


from app.lib.cartography.multiscale.paper_scale import (
    FrameViewport,
    PublicationScaleContract,
    HardConstraint,
)
from app.lib.cartography.multiscale.profile import SourceRevision
from app.lib.cartography.multiscale.publication_compile import (
    compile_paper_scale_plan,
)
from app.lib.cartography.multiscale.qa_pack import (
    audit_polygon_pairwise_seams,
    audit_sum_conservation,
)

LAT = 30.66


def _districts() -> List[Dict[str, Any]]:
    """4×4 共享边行政区网格 + 一个 <0.5km² 小县（会消除）。"""
    feats: List[Dict[str, Any]] = []
    d = 0.05   # ≈27.6 km²
    for gx in range(4):
        for gy in range(4):
            x0 = 103.9 + gx * d
            y0 = LAT + gy * d
            ring = [[x0, y0], [x0 + d, y0], [x0 + d, y0 + d], [x0, y0 + d],
                    [x0, y0]]
            feats.append({
                "type": "Feature", "id": f"dist-{gx}-{gy}",
                "properties": {"name": f"区{gx}{gy}", "人口": 1000 + gx * 10},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            })
    # 小县贴在 (0,0) 格右侧（共享边消除进邻居）
    ring = [[103.95, LAT], [103.954, LAT], [103.954, LAT + 0.004],
            [103.95, LAT + 0.004], [103.95, LAT]]
    feats.append({
        "type": "Feature", "id": "tiny-county",
        "properties": {"name": "小县", "人口": 30},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    })
    return feats


def _roads() -> List[Dict[str, Any]]:
    """3 行链路 + 2 条纵向国道（行链经桥接可全连通）。"""
    feats: List[Dict[str, Any]] = []
    for i in range(15):
        col = i % 5
        x = 103.9 + col * 0.05
        y = LAT + (i // 5) * 0.05
        cls = "国道" if i % 5 == 0 else "乡道"
        feats.append({
            "type": "Feature", "id": f"road-{i}",
            "properties": {"道路等级": cls},
            "geometry": {"type": "LineString",
                         "coordinates": [[x, y], [x + 0.05, y]]},
        })
    for r in range(2):
        y = LAT + r * 0.05
        feats.append({
            "type": "Feature", "id": f"vlink-{r}",
            "properties": {"道路等级": "国道"},
            "geometry": {"type": "LineString",
                         "coordinates": [[104.15, y], [104.15, y + 0.05]]},
        })
    return feats


def _schools(n: int = 60) -> List[Dict[str, Any]]:
    feats = []
    side = 10
    for i in range(n):
        lon = 103.91 + (i % side) * 0.004
        lat = LAT + 0.004 + (i // side) * 0.004
        feats.append({
            "type": "Feature", "id": f"school-{i}",
            "properties": {"name": f"小学{i}", "学生数": 300 + (i % 20) * 10},
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        })
    return feats


def _contract(denominator: int, purpose: str) -> PublicationScaleContract:
    return PublicationScaleContract(
        target_denominator=denominator, purpose=purpose,
        frame=FrameViewport(width_mm=260.0, height_mm=170.0,
                            scale_reference_lat=LAT,
                            data_bbox=[103.9, LAT, 104.2, LAT + 0.25]),
    )


_SRC = SourceRevision(ref="ref:city", feature_count=0,
                      geometry_family="unknown")


class TestCityJourneys:
    def test_districts_two_scales_seamless_and_conserved(self):
        feats = _districts()
        src = SourceRevision(ref="ref:city", feature_count=len(feats),
                             geometry_family="polygon")
        results = {}
        for denom, purpose in ((2_000_000, "overview"), (50_000, "reference")):
            out = compile_paper_scale_plan(
                layer_id="districts",
                feature_collection={"type": "FeatureCollection",
                                    "features": feats},
                source=src, contract=_contract(denom, purpose),
                latitude_hint=LAT, sum_fields=["人口"])
            results[denom] = out
        overview, detail = results[2_000_000], results[50_000]
        # 概览：小县消除、QA 无阻断
        assert overview.plan["pipeline"][0]["eliminated"] == 1
        assert overview.plan["qa_summary"]["blocking"] == 0
        # 详图：50_000 阈值≈0.005km²，小县≈0.0072km² 保留（未消除）
        assert detail.plan["pipeline"][0]["eliminated"] == 0
        # 两计划指纹不同（内容敏感）
        assert overview.fingerprint != detail.fingerprint
        # 无缝审计独立通过
        seam = audit_polygon_pairwise_seams(
            overview.artifact["features"], latitude_hint=LAT)
        assert seam.get("within_tolerance") is True
        # 人口 Σ 守恒（幸存者承载体 + 被消除者不消失于对账）
        sums = audit_sum_conservation(
            feats, overview.artifact["features"], sum_fields=["人口"])
        # 幸存者人口 Σ = 原始 Σ − 被消除小县（display 语义：小县并入邻居
        # 后其值并入邻居）→ 幸存者合计应等于原始合计
        assert sums["conserved"] is True

    def test_road_network_overview_keeps_connectivity(self):
        feats = _roads()
        src = SourceRevision(ref="ref:city", feature_count=len(feats),
                             geometry_family="line")
        model = _road_model()
        out = compile_paper_scale_plan(
            layer_id="roads",
            feature_collection={"type": "FeatureCollection", "features": feats},
            source=src, contract=_contract(2_000_000, "overview"),
            importance=model, latitude_hint=LAT)
        step = out.plan["pipeline"][0]
        assert step["step"] == "network_select"
        assert step["fully_connected"] is True
        assert out.plan["qa_summary"]["blocking"] == 0
        # 乡道被抑制后经桥接重加（整图连通）
        ids = [f["id"] for f in out.artifact["features"]]
        assert len(ids) == len(feats)

    def test_schools_aggregate_traceable_and_display_only(self):
        feats = _schools(60)
        src = SourceRevision(ref="ref:city", feature_count=len(feats),
                             geometry_family="point")
        out = compile_paper_scale_plan(
            layer_id="schools",
            feature_collection={"type": "FeatureCollection", "features": feats},
            source=src, contract=_contract(2_000_000, "thematic_exhibition"),
            latitude_hint=LAT, sum_fields=["学生数"])
        step = out.plan["pipeline"][0]
        assert step["step"] == "aggregate_points"
        # 代表要素 display-only 标记 + 成员可回查
        reps = [f for f in out.artifact["features"]
                if str(f.get("id", "")).startswith("agg:")]
        assert reps and all(
            f["properties"].get("display_only") is True for f in reps)
        member_total = sum(f["properties"]["agg_member_count"] for f in reps)
        passthrough = out.artifact["features"]
        assert member_total + (len(passthrough) - len(reps)) == 60
        # 学生数 Σ 守恒（独立 oracle）
        sums = audit_sum_conservation(
            feats, out.artifact["features"], sum_fields=["学生数"])
        assert sums["conserved"] is True
        # 分析通道语义：原始数据未被触碰（入参不被修改）
        assert feats[0]["properties"]["学生数"] == 300

    def test_dirty_overlap_input_refused_by_qa(self):
        # 面状统计专题：重叠脏数据 → seam fail → 编译级 QA blocking（上版
        # 由服务 fail-closed 拒绝 —— 旅程 3 的「不可保真拒绝」）
        feats = _districts()
        feats[1]["geometry"]["coordinates"][0][1][0] += 0.01   # 侵入邻居
        src = SourceRevision(ref="ref:city", feature_count=len(feats),
                             geometry_family="polygon")
        out = compile_paper_scale_plan(
            layer_id="districts",
            feature_collection={"type": "FeatureCollection", "features": feats},
            source=src, contract=_contract(2_000_000, "thematic_exhibition"),
            latitude_hint=LAT)
        assert out.plan["qa_summary"]["blocking"] >= 1

    def test_hard_constraint_keeps_small_county(self):
        feats = _districts()
        src = SourceRevision(ref="ref:city", feature_count=len(feats),
                             geometry_family="polygon")
        contract = _contract(2_000_000, "overview")
        contract.hard_constraints = [
            HardConstraint(kind="keep_feature_ids", feature_ids=["tiny-county"])]
        out = compile_paper_scale_plan(
            layer_id="districts",
            feature_collection={"type": "FeatureCollection", "features": feats},
            source=src, contract=contract, latitude_hint=LAT)
        assert out.plan["pipeline"][0]["eliminated"] == 0
        ids = [f["id"] for f in out.artifact["features"]]
        assert "tiny-county" in ids

    def test_determinism_across_full_pipeline(self):
        feats = _districts()
        src = SourceRevision(ref="ref:city", feature_count=len(feats),
                             geometry_family="polygon")
        kwargs = dict(
            layer_id="districts",
            feature_collection={"type": "FeatureCollection", "features": feats},
            source=src, contract=_contract(2_000_000, "overview"),
            latitude_hint=LAT, sum_fields=["人口"])
        a = compile_paper_scale_plan(**kwargs)
        b = compile_paper_scale_plan(**kwargs)
        assert a.fingerprint == b.fingerprint
        assert json.dumps(a.artifact, sort_keys=True) == \
            json.dumps(b.artifact, sort_keys=True)


def _road_model():
    from app.lib.cartography.multiscale.importance import (
        ImportanceDerivation,
        ImportanceModel,
    )
    return ImportanceDerivation(selected=ImportanceModel(
        field="道路等级",
        value_order=["高速公路", "国道", "省道", "县道", "乡道"],
        matched_vocabulary="road_class"))


# 旅程 6（大图层/断连重连/取消恢复）为既有 MVT/预算 job/durable 通道能力，
# 本模块通过 artifact 数据量缩减受益；浏览器侧证据标 not_evaluated（见文档）。
