"""专家输出契约（ADR-0188/0189 contracts.py）冻结纪律测试。

- ``SpatialProfileRef`` / ``MapSpecDeliveryRef`` / ``DeliveryAuditReport``
  的 extra=forbid（追加式冻结：新字段必须显式声明）；
- 8KB 序列化硬闸的度量口径（``to_json_bytes``）；
- 诚实失败类型（``SpatialProfileTooLargeError``）。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.agent_swarm.contracts import (
    CONTRACTS_VERSION,
    SERIALIZATION_BUDGET_BYTES,
    ComputeRequest,
    ComputeSubmission,
    DataScoutReport,
    DeliveryAuditReport,
    MapSpecDeliveryRef,
    SpatialProfileRef,
    SpatialProfileTooLargeError,
    VolumeEstimate,
)


class TestBudgets:
    """有界常量（口径变化即契约变化）。"""

    def test_serialization_budget_is_8kb(self):
        assert SERIALIZATION_BUDGET_BYTES == 8 * 1024

    def test_contracts_version_is_v1(self):
        assert CONTRACTS_VERSION == "1.0"

    def test_too_large_error_is_value_error(self):
        assert issubclass(SpatialProfileTooLargeError, ValueError)


class TestSpatialProfileRef:
    """GeoCompute 结果提货券。"""

    def test_defaults_are_submitted_without_refs(self):
        ref = SpatialProfileRef(plan_id="gc-1")
        assert ref.status == "submitted"
        assert ref.ref_id is None
        assert ref.truncated is False
        assert ref.notes == ()
        assert ref.metadata == {}

    def test_json_bytes_measurement_matches_budget(self):
        ref = SpatialProfileRef(plan_id="gc-1", summary="s" * 100)
        payload = ref.to_json_bytes()
        assert isinstance(payload, bytes)
        assert len(payload) < SERIALIZATION_BUDGET_BYTES

    def test_status_vocabulary_enforced(self):
        with pytest.raises(ValidationError):
            SpatialProfileRef(plan_id="gc-1", status="finished")

    def test_volume_estimate_honest_unknown_default(self):
        estimate = VolumeEstimate()
        assert estimate.rows is None
        assert estimate.bytes is None
        assert estimate.area_km2 is None
        assert estimate.method == "unknown"


class TestComputeContracts:
    """编排请求 / 提交回执的 extra=forbid。"""

    def test_compute_request_forbids_unknown_fields(self):
        with pytest.raises(ValidationError):
            ComputeRequest(operation="buffer_analysis", dataset_ref="ref:d1", rogue=1)

    def test_compute_request_minimal_shape(self):
        req = ComputeRequest(operation="clip", dataset_ref="ref:d1")
        assert req.operation_params == {}
        assert req.bbox is None
        assert req.descriptor is None

    def test_compute_submission_forbids_unknown_fields(self):
        ref = SpatialProfileRef(plan_id="gc-1")
        with pytest.raises(ValidationError):
            ComputeSubmission(
                plan_id="gc-1", node_count=1, ref=ref, extra_field="x"
            )


class TestMapSpecDeliveryRef:
    """Cartographer 交付券（ADR-0189 D3）。"""

    def test_defaults_and_capability_literal(self):
        delivery = MapSpecDeliveryRef()
        assert delivery.capability == "thematic_map"
        assert delivery.revision == 0
        assert delivery.ref_id is None
        assert delivery.truncated is False

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            MapSpecDeliveryRef(payload={"type": "FeatureCollection"})

    def test_capability_literal_enforced(self):
        with pytest.raises(ValidationError):
            MapSpecDeliveryRef(capability="raster_tiles")

    def test_json_bytes_bounded(self):
        delivery = MapSpecDeliveryRef(components=["title"] * 12, summary="s" * 600)
        assert len(delivery.to_json_bytes()) < SERIALIZATION_BUDGET_BYTES


class TestDeliveryAuditReport:
    """审计单契约：三值 verdict + 派生口径披露。"""

    def test_verdict_literal_three_values(self):
        for verdict in ("pass", "fail", "not_evaluated"):
            assert DeliveryAuditReport(verdict=verdict).verdict == verdict
        with pytest.raises(ValidationError):
            DeliveryAuditReport(verdict="maybe")

    def test_default_not_evaluated(self):
        report = DeliveryAuditReport()
        assert report.verdict == "not_evaluated"
        assert report.goal_score is None
        assert report.goal_score_derivation == ""
        assert report.vetoes == []

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            DeliveryAuditReport(verdict="pass", independent_verdict="pass")

    def test_json_bytes_bounded(self):
        report = DeliveryAuditReport(
            verdict="fail",
            vetoes=[{"veto_id": f"V{i}"} for i in range(8)],
            improvement_notes=["n" * 240 for _ in range(8)],
        )
        assert len(report.to_json_bytes()) < SERIALIZATION_BUDGET_BYTES


class TestDataScoutReport:
    """Data Scout 统一报告的诚实失败面。"""

    def test_ok_false_requires_nothing_but_error_is_convention(self):
        report = DataScoutReport(ok=False, error="源未注册: x")
        assert report.ok is False
        assert report.descriptor is None
        assert report.decisions == []

    def test_ok_true_carries_descriptor(self):
        report = DataScoutReport(ok=True, source_used="osm", aligned_fields={"名称": "name"})
        assert report.ok is True
        assert report.aligned_fields == {"名称": "name"}
