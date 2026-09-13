"""ADR-0153 P2：plan_repair_ops 编排器 —— 固定顺序、破坏性裁决、确定性。"""
from __future__ import annotations

import pytest
from shapely.geometry import shape as _shape

from app.services.spatial_quality_service import (
    QualityIssue,
    SpatialQualityReport,
    SpatialQualityEngine,
)
from app.services.spatial_repair_pipeline import (
    CANONICAL_OP_ORDER,
    ATTR_TYPE_MIX_RATIO_THRESHOLD,
    DEDUP_RATIO_THRESHOLD,
    GEOMETRY_MIX_RATIO_THRESHOLD,
    SpatialRepairPipeline,
    plan_repair_ops,
)


def _report(codes_levels):
    """合成 audit 报告（planner 只消费 issues[].code）。"""
    issues = [
        QualityIssue(dimension="geometry", code=c, level=lv, message="synthetic")
        for c, lv in codes_levels
    ]
    return SpatialQualityReport(
        total_features=10,
        issues=issues,
        overall_status="blocking",
    )


def _canonical_subsequence(ops):
    idx = [CANONICAL_OP_ORDER.index(op) for op in ops]
    return idx == sorted(idx)


class TestFixedOrder:
    def test_plan_order_is_canonical_subsequence(self):
        report = _report([
            ("MISSING_CRS", "info"), ("EMPTY_GEOMETRY", "blocking"),
            ("SELF_INTERSECTION", "blocking"), ("DUPLICATE_GEOMETRY", "warning"),
            ("TOPOLOGY_OVERLAP", "error"),
            ("NUMERIC_OUTLIER", "warning"), ("HIGH_NULL_RATIO", "warning"),
        ])
        plan = plan_repair_ops(
            report,
            duplicate_ratio=0.5,
            overlap_pair_ratio=0.5,
            crs_inference={"crs": "EPSG:3857", "confidence": "medium", "low_confidence": False},
            allow_destructive=True,
        )
        assert _canonical_subsequence(plan.ops)
        assert plan.ops == [
            op for op in CANONICAL_OP_ORDER
            if op in {
                "remove_empty", "make_valid", "deduplicate", "crs_transform",
                "fix_topology_overlap", "drop_outliers_or_flag", "attribute_drop_or_flag",
            }
        ]

    def test_ring_check_failed_maps_to_make_valid(self):
        # RING_CHECK_FAILED 经标准 GeoJSON 解析不可达（见 fixtures 注记），
        # planner 映射用合成 issue 钉死。
        plan = plan_repair_ops(_report([("RING_CHECK_FAILED", "error")]))
        assert plan.ops == ["make_valid"]
        assert plan.reasons["make_valid"] == ["RING_CHECK_FAILED"]

    def test_plan_is_deterministic(self):
        report = _report([
            ("SELF_INTERSECTION", "blocking"), ("TOPOLOGY_GAP", "warning"),
        ])
        p1 = plan_repair_ops(report, crs_inference={"crs": "EPSG:4326", "confidence": "high"})
        p2 = plan_repair_ops(report, crs_inference={"crs": "EPSG:4326", "confidence": "high"})
        assert p1.to_bounded_dict() == p2.to_bounded_dict()

    def test_planner_never_executes(self):
        # plan-only 红线：planner 的输出必须可整体作为计划序列化（无闭包/
        # 数据载荷），且不携带任何 geojson 数据。
        report = _report([("EMPTY_GEOMETRY", "blocking")])
        plan = plan_repair_ops(report)
        assert "features" not in plan.to_bounded_dict()


class TestDestructiveAdjudication:
    def test_dedup_below_threshold_disabled(self):
        plan = plan_repair_ops(_report([("DUPLICATE_GEOMETRY", "warning")]), duplicate_ratio=0.01)
        assert "deduplicate" not in plan.ops
        assert plan.destructive_decisions["deduplicate"].startswith("disabled_below_threshold")
        assert any(s["op"] == "deduplicate" for s in plan.skipped_ops)

    def test_dedup_at_threshold_enabled(self):
        plan = plan_repair_ops(
            _report([("DUPLICATE_GEOMETRY", "warning")]),
            duplicate_ratio=DEDUP_RATIO_THRESHOLD,
        )
        assert "deduplicate" in plan.ops
        assert "enabled_by_adjudication" in plan.destructive_decisions["deduplicate"]

    def test_geometry_mix_adjudication_enables_normalize(self):
        plan = plan_repair_ops(
            _report([]), geometry_mix_ratio=GEOMETRY_MIX_RATIO_THRESHOLD
        )
        assert "normalize_geometry_type" in plan.ops

    def test_attribute_normalization_below_threshold_disabled(self):
        plan = plan_repair_ops(
            _report([("TYPE_INCONSISTENCY", "warning")]), attribute_type_mix_ratio=0.1
        )
        assert "attribute_type_normalization" not in plan.ops

    def test_attribute_normalization_at_threshold_enabled(self):
        plan = plan_repair_ops(
            _report([("TYPE_INCONSISTENCY", "warning")]),
            attribute_type_mix_ratio=ATTR_TYPE_MIX_RATIO_THRESHOLD,
        )
        assert "attribute_type_normalization" in plan.ops

    def test_overlap_below_threshold_flag_mode(self):
        plan = plan_repair_ops(
            _report([("TOPOLOGY_OVERLAP", "error")]), overlap_pair_ratio=0.001
        )
        assert plan.op_params["fix_topology_overlap"]["mode"] == "flag"

    def test_allow_destructive_forces_all(self):
        plan = plan_repair_ops(
            _report([
                ("DUPLICATE_GEOMETRY", "warning"), ("TYPE_INCONSISTENCY", "warning"),
                ("TOPOLOGY_GAP", "warning"), ("NUMERIC_OUTLIER", "warning"),
                ("HIGH_NULL_RATIO", "warning"),
            ]),
            duplicate_ratio=0.0,
            attribute_type_mix_ratio=0.0,
            outlier_fields=[{"field": "v", "outlier_ratio": 0.5, "upper": 100.0}],
            allow_destructive=True,
        )
        assert "deduplicate" in plan.ops
        assert "attribute_type_normalization" in plan.ops
        assert plan.op_params["fix_gaps"]["mode"] == "snap"
        # outlier_ratio 0.5 > 2% 上限 → drop 仍降级为 flag（安全阀）。
        assert plan.op_params["drop_outliers_or_flag"]["mode"] == "flag"


class TestCrsHandling:
    def test_inference_feeds_crs_transform(self):
        report = _report([("IMPOSSIBLE_LAT_LON", "blocking")])
        plan = plan_repair_ops(
            report,
            crs_inference={"crs": "EPSG:3857", "confidence": "medium", "low_confidence": False},
        )
        assert plan.source_crs == "EPSG:3857"

    def test_noop_crs_honestly_skipped(self):
        report = _report([("MISSING_CRS", "info")])
        plan = plan_repair_ops(
            report,
            crs_inference={"crs": "EPSG:4326", "confidence": "high", "low_confidence": False},
        )
        assert "crs_transform" not in plan.ops
        assert any(s["op"] == "crs_transform" for s in plan.skipped_ops)

    def test_low_confidence_inference_emits_advisory(self):
        report = _report([("IMPOSSIBLE_LAT_LON", "blocking")])
        plan = plan_repair_ops(
            report,
            crs_inference={"crs": "EPSG:3857", "confidence": "low", "low_confidence": True},
        )
        assert any(a["code"] == "CRS_INFERENCE_LOW_CONFIDENCE" for a in plan.advisories)


@pytest.mark.cartography
def test_pipeline_kwargs_roundtrip_with_real_audit():
    """plan → pipeline 全链：audit 真实报告 → plan → 修复可执行。"""
    data = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"v": 1}, "geometry": {"type": "Polygon",
             "coordinates": [[[0.0, 0.0], [2.0, 2.0], [2.0, 0.0], [0.0, 2.0], [0.0, 0.0]]]}},
            {"type": "Feature", "properties": {"v": 2}, "geometry": None},
        ],
    }
    report = SpatialQualityEngine.audit_dataset(data, crs="UNKNOWN")
    plan = plan_repair_ops(report)
    assert _canonical_subsequence(plan.ops)
    repaired, logs, evidence, lineage = SpatialRepairPipeline.repair_dataset_with_lineage(
        data, **plan.pipeline_kwargs()
    )
    repaired_features = repaired["features"]
    # remove_empty + make_valid 都实际生效。
    assert len(repaired_features) == 1
    g = _shape(repaired_features[0]["geometry"])
    assert g.is_valid
    assert evidence and lineage
