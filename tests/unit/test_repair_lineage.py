"""ADR-0153 P5：修复血缘 —— {op, before/after_count, area_delta, evidence[], ts}。

「为何修」解释链：plan.reasons（码 → op）与 lineage（op → 计数/面积变化）
拼接即完整回放；与 Wave-4 op 级证据键（op/features_affected/failed_count）
同形对齐，不新建第二套 provenance。
"""
from __future__ import annotations

import pytest

from app.services.spatial_repair_pipeline import SpatialRepairPipeline, plan_repair_ops
from app.services.spatial_quality_service import SpatialQualityEngine

LINEAGE_KEYS = {"op", "before_count", "after_count", "area_delta", "evidence", "ts"}


def _bowtie():
    return {
        "type": "Feature", "properties": {"v": 1},
        "geometry": {"type": "Polygon", "coordinates": [[
            [0.0, 0.0], [2.0, 2.0], [2.0, 0.0], [0.0, 2.0], [0.0, 0.0],
        ]]},
    }


def _run(data, ops, op_params=None, **kw):
    return SpatialRepairPipeline.repair_dataset_with_lineage(
        data, ops=ops, op_params=op_params or {}, **kw
    )


def test_lineage_entry_shape_and_ts():
    data = {"type": "FeatureCollection", "features": [
        _bowtie(),
        {"type": "Feature", "properties": {"v": 2}, "geometry": None},
    ]}
    repaired, logs, evidence, lineage = _run(data, ["remove_empty", "make_valid"])
    assert lineage, "expected lineage entries for executed ops"
    for entry in lineage:
        assert set(entry) == LINEAGE_KEYS
        assert isinstance(entry["ts"], str) and "T" in entry["ts"]
        assert isinstance(entry["evidence"], list)
        for ev in entry["evidence"]:
            assert {"op", "features_affected", "failed_count"} == set(ev)


def test_lineage_counts_for_count_changing_ops():
    data = {"type": "FeatureCollection", "features": [
        _bowtie(),
        {"type": "Feature", "properties": {"v": 2}, "geometry": None},
    ]}
    repaired, logs, evidence, lineage = _run(data, ["remove_empty", "make_valid"])
    by_op = {e["op"]: e for e in lineage}
    assert by_op["remove_empty"]["before_count"] == 2
    assert by_op["remove_empty"]["after_count"] == 1
    # make_valid 不改变要素数：before == after == 循环后规模（1）。
    assert by_op["make_valid"]["before_count"] == 1
    assert by_op["make_valid"]["after_count"] == 1


def test_lineage_area_delta_records_make_valid_effect():
    # bowtie 自交：make_valid 后面积改变（蝴蝶面的两部分合并），area_delta ≠ 0。
    data = {"type": "FeatureCollection", "features": [_bowtie()]}
    before_area = _shape_area(_bowtie()["geometry"])
    repaired, logs, evidence, lineage = _run(data, ["make_valid"])
    entry = next(e for e in lineage if e["op"] == "make_valid")
    after_area = _shape_area(repaired["features"][0]["geometry"])
    assert entry["area_delta"] == pytest.approx(after_area - before_area, rel=1e-6, abs=1e-12)
    assert entry["area_delta"] != 0.0


def _shape_area(geometry):
    from shapely.geometry import shape as _shape
    return float(_shape(geometry).area)


def test_lineage_area_delta_for_dedup_and_overlap():
    square = {"type": "Feature", "properties": {"v": 1},
              "geometry": {"type": "Polygon", "coordinates": [[
                  [0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]]}}
    overlap = {"type": "Feature", "properties": {"v": 2},
               "geometry": {"type": "Polygon", "coordinates": [[
                   [1.0, 0.0], [3.0, 0.0], [3.0, 2.0], [1.0, 2.0], [1.0, 0.0]]]}}
    data = {"type": "FeatureCollection", "features": [square, overlap]}
    repaired, logs, evidence, lineage = _run(
        data, ["deduplicate", "fix_topology_overlap"],
        op_params={"fix_topology_overlap": {"mode": "difference"}},
    )
    by_op = {e["op"]: e for e in lineage}
    assert by_op["fix_topology_overlap"]["area_delta"] < 0.0  # 后入面失去重叠区
    assert by_op["fix_topology_overlap"]["before_count"] == 2
    assert by_op["fix_topology_overlap"]["after_count"] == 2


def test_why_repaired_explanation_chain():
    """「为何修」：plan.reasons 给码→op 的解释，lineage 给 op 的落地事实。"""
    data = {"type": "FeatureCollection", "features": [
        _bowtie(),
        {"type": "Feature", "properties": {"v": 2}, "geometry": None},
    ]}
    report = SpatialQualityEngine.audit_dataset(data, crs="UNKNOWN")
    plan = plan_repair_ops(report)
    repaired, logs, evidence, lineage = _run(data, plan.ops, plan.op_params)
    # SELF_INTERSECTION → make_valid 的解释链闭合。
    assert "SELF_INTERSECTION" in plan.reasons.get("make_valid", [])
    assert any(e["op"] == "make_valid" for e in lineage)
    # EMPTY_GEOMETRY → remove_empty 同理。
    assert "EMPTY_GEOMETRY" in plan.reasons.get("remove_empty", [])
    assert any(e["op"] == "remove_empty" for e in lineage)


def test_lineage_evidence_matches_ops_evidence():
    data = {"type": "FeatureCollection", "features": [
        _bowtie(),
        {"type": "Feature", "properties": {"v": 2}, "geometry": None},
    ]}
    _, _, evidence, lineage = _run(data, ["remove_empty", "make_valid"])
    by_op = {e["op"]: e for e in lineage}
    for ev in evidence:
        lin = by_op[ev["op"]]
        assert lin["evidence"][0]["features_affected"] == ev["features_affected"]
        assert lin["evidence"][0]["failed_count"] == ev["failed_count"]
