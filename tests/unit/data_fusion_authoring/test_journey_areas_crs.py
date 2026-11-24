"""验收旅程 2/3 补充：跨 CRS typed 拒绝（G45 提示）+ 面实体仅关联端到端。

- 旅程 2：同一物理区域两个来源在不同坐标帧（EPSG:4326 vs 投影米）
  → 计划期 typed `crs_unresolvable`，提示走 G45；绝不静默混算；
- 旅程 2（面实体）：重叠行政区配对 → commit 后保留采纳侧几何，
  另一侧进 provenance（绝不 dissolve）；未配对面原样保留；
- 旅程 3（质量披露）：质量报告区分缺失/冲突/有效数据与单源不足。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_tables")

from app.services.data_fusion_authoring import service
from app.services.data_fusion_authoring.errors import DataFusionError
from tests.unit.data_fusion_authoring.conftest import (
    fusion_plan,
    seed_session_source,
)


def _area_fc(lon: float, lat: float, half: float, name: str,
             cat: str, crs: str = "EPSG:4326") -> dict:
    return {
        "type": "FeatureCollection", "crs": crs,
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Polygon", "coordinates": [[
                 [lon - half, lat - half], [lon + half, lat - half],
                 [lon + half, lat + half], [lon - half, lat + half],
                 [lon - half, lat - half]]]},
             "properties": {"code": f"Q-{name}", "osm_id": f"O-{name}",
                            "checked": "2026-01-01", "name": name,
                            "cat": cat, "addr": "", "tel": ""}},
        ],
    }


def _area_plan(a_ref: str, b_ref: str, sid: str) -> dict:
    plan = fusion_plan(a_ref, b_ref, sid)
    plan["entity_kind"] = "area_link"
    plan["source_a"]["category_map"] = {"行政区": "district"}
    plan["source_b"]["category_map"] = {"行政区": "district"}
    plan["strategy"]["weights"] = {
        "external_id": 0.0, "name": 0.20, "address": 0.0,
        "category": 0.10, "distance": 0.70}
    plan["strategy"]["overlap_min"] = 0.3
    plan["strategy"]["overlap_full"] = 0.6
    plan["strategy"]["name_normalizers"] = [
        "trim", "casefold", "collapse_spaces", "remove_bracketed"]
    plan["commit_policy"] = "allow_unresolved"
    return plan


async def test_cross_crs_typed_rejected_with_g45_hint():
    """EPSG:4326 vs Web Mercator：不可比帧 → typed 拒绝 + G45 提示。"""
    sid = "sess-fusion-crs"
    a = await seed_session_source(
        sid, _area_fc(104.06, 30.65, 0.002, "一街区", "行政区"), prefix="crsa")
    # Web Mercator（projected，非局部度量）下的同一区域（合成坐标）
    fc_b = {
        "type": "FeatureCollection", "crs": "EPSG:3857",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Polygon", "coordinates": [[
                 [11582608.0, 3567019.0], [11583424.0, 3567019.0],
                 [11583424.0, 3567836.0], [11582608.0, 3567836.0],
                 [11582608.0, 3567019.0]]]},
             "properties": {"code": "R-一街区", "osm_id": "O-修测一街区",
                            "checked": "2026-02-01", "name": "一街区（修测）",
                            "cat": "行政区", "addr": "", "tel": ""}},
        ],
    }
    b = await seed_session_source(sid, fc_b, prefix="crsb")
    actor = {"trusted_session_id": sid}
    view = await service.create_plan(
        sid, _area_plan(a, b, sid), actor=actor)
    with pytest.raises(DataFusionError) as ei:
        await service.execute_plan(sid, view["plan_id"], expected_revision=1,
                                   actor=actor, background=False)
    assert ei.value.code == "crs_unresolvable"
    assert "G45" in (ei.value.correction_hint or ei.value.message)


async def test_area_link_end_to_end_keeps_adopted_geometry():
    """面实体端到端：近全重叠自动配对 → commit 采纳 a 侧几何（provenance
    记录 b 侧证据），未配对面原样保留，绝不 dissolve。"""
    sid = "sess-fusion-area"
    a = await seed_session_source(
        sid, _area_fc(104.06, 30.65, 0.002, "一街区", "行政区")
        | {"features": [
            _area_fc(104.06, 30.65, 0.002, "一街区", "行政区")["features"][0],
            _area_fc(104.08, 30.66, 0.002, "二街区", "行政区")["features"][0],
        ]}, prefix="areaa")
    b = await seed_session_source(
        sid, {"type": "FeatureCollection", "crs": "EPSG:4326", "features": [
            # 与一街区近全重叠（IoU≥0.6）
            _area_fc(104.0602, 30.6501, 0.002, "一街区（修测）",
                     "行政区")["features"][0],
        ]}, prefix="areab")
    actor = {"trusted_session_id": sid}
    view = await service.create_plan(sid, _area_plan(a, b, sid), actor=actor)
    run = await service.execute_plan(sid, view["plan_id"], expected_revision=1,
                                     actor=actor, background=False)
    assert run["status"] == "completed"
    assert run["receipt"]["match_stats"]["pairs_matched"] == 1
    done = await service.commit_run(sid, run["run_id"], actor=actor)
    counts = done["commit_receipt"]["counts"]
    assert counts["merged"] == 1
    assert counts["single_a"] == 1        # 二街区原样保留
    page = await service.run_result_page(sid, run["run_id"], limit=10)
    merged = [f for f in page["features"]
              if f["properties"]["_fusion"]["origin"] == "merged"]
    assert len(merged) == 1
    prov = merged[0]["properties"]["_fusion"]
    # 几何采纳：primary_source → a 侧；重叠证据入档（绝不 dissolve）
    assert prov["geometry"]["side"] == "a"
    assert prov["geometry"]["adoption"] == "primary_source"
    assert prov["geometry"]["evidence"]["kind"] == "overlap_ratio"
    ring = merged[0]["geometry"]["coordinates"][0]
    xs = [pt[0] for pt in ring]
    assert abs(min(xs) - (104.06 - 0.002)) < 1e-9   # a 侧原始边界
    single = [f for f in page["features"]
              if f["properties"]["_fusion"]["origin"] == "single_a"]
    assert len(single) == 1 and single[0]["properties"]["name"] == "二街区"


async def test_quality_report_distinguishes_missing_conflict():
    """旅程 3：质量报告区分缺失/冲突/有效数据与未决披露。"""
    from tests.unit.data_fusion_authoring.conftest import chengdu_pois_a, \
        chengdu_pois_b
    sid = "sess-fusion-quality"
    a = await seed_session_source(sid, chengdu_pois_a(), prefix="qa")
    b = await seed_session_source(sid, chengdu_pois_b(), prefix="qb")
    actor = {"trusted_session_id": sid}
    view = await service.create_plan(
        sid, fusion_plan(a, b, sid) | {"commit_policy": "allow_unresolved"},
        actor=actor)
    run = await service.execute_plan(sid, view["plan_id"], expected_revision=1,
                                     actor=actor, background=False)
    done = await service.commit_run(sid, run["run_id"], actor=actor)
    cr = done["commit_receipt"]
    # 未决别名对 → withheld 2；自动括号对 → merged 1；单源 2
    assert cr["counts"]["withheld_review"] == 2
    assert cr["counts"]["merged"] == 1
    assert cr["unresolved_field_count"] >= 0
    # 质量报告分项（终态包内）
    from app.services.data_fusion_authoring import outputs

    import json

    q = json.load(open(outputs.final_dir(run["run_id"]) / "quality_report.json",
                       encoding="utf-8"))
    assert q["coverage"]["source_a"]["rows"] == 3
    assert q["matching"]["hard_blocked"] == 0
    assert q["review"]["withheld_features"] == 2
    assert any(item["item"] == "withheld_review"
               for item in q["limitations"])
    assert q["fields"]["missing_semantics"]
    assert q["trust"]["reason_a"] and q["trust"]["reason_b"]
