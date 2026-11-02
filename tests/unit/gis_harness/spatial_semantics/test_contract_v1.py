"""SpatialSemanticContract v1 —— 契约 schema/序列化/词表门测试（M1）。

锁定（验收 Oracle #1）：可序列化/验证/版本化；未知 CRS/单位/分母绝不静默
伪造 —— 越词表构造失败、版本 fail-closed、未知即 unknown。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.spatial_semantics import (
    SPATIAL_SEMANTIC_CONTRACT_VERSION,
    ScopeEvidence,
    SpatialSemanticContract,
)


def _contract(**kw) -> SpatialSemanticContract:
    return SpatialSemanticContract(**kw)


class TestContractSchema:
    def test_defaults_are_unknown_not_fabricated(self) -> None:
        c = _contract()
        assert c.schema_version == SPATIAL_SEMANTIC_CONTRACT_VERSION == 1
        assert c.source_crs == ""
        assert c.crs_source == "unknown"
        assert c.crs_class == "unknown"
        assert c.axis_order == "unknown"
        assert c.calculation_mode == "unknown"
        assert c.distance_unit == "unknown"
        assert c.stat_denominator == "none"
        assert c.geometry_type == "unknown"

    def test_vocabulary_gate_crs_source(self) -> None:
        with pytest.raises(Exception):
            _contract(crs_source="guessed")  # 越词表即拒，绝不静默

    def test_vocabulary_gate_axis_order(self) -> None:
        with pytest.raises(Exception):
            _contract(axis_order="xy")

    def test_vocabulary_gate_calculation_mode(self) -> None:
        with pytest.raises(Exception):
            _contract(calculation_mode="mercator_magic")

    def test_vocabulary_gate_denominator(self) -> None:
        with pytest.raises(Exception):
            _contract(stat_denominator="volume")

    def test_vocabulary_gate_distance_unit(self) -> None:
        with pytest.raises(Exception):
            _contract(distance_unit="degrees")

    def test_scope_overlap_gate(self) -> None:
        with pytest.raises(Exception):
            _contract(requested_scope=ScopeEvidence(overlap_with_data="partial"))

    def test_bounded_reason_codes(self) -> None:
        from app.services.gis_harness.spatial_semantics.contract import (
            ContractReasonCode,
        )
        too_many = [ContractReasonCode(code="SSC_CRS_ASSUMED", detail="x")
                    for _ in range(20)]
        with pytest.raises(Exception):
            _contract(reason_codes=too_many)


class TestContractSerialization:
    def test_roundtrip(self) -> None:
        c = _contract(
            dataset_ref="ref:abc123",
            source_crs="EPSG:4326",
            crs_source="declared",
            crs_class="geographic",
            working_crs="EPSG:32648",
            stat_denominator="area",
            denominator_unit="km2",
            density_unit="个/km²",
            datum_normalization=["bd09→gcj02→wgs84"],
        )
        d = c.to_dict()
        assert d["schema_version"] == 1
        c2 = SpatialSemanticContract.from_dict(d)
        assert c2 == c

    def test_from_dict_rejects_unknown_version(self) -> None:
        with pytest.raises(ValueError, match="schema_version"):
            SpatialSemanticContract.from_dict({"schema_version": 99})

    def test_from_dict_rejects_non_dict(self) -> None:
        with pytest.raises(ValueError):
            SpatialSemanticContract.from_dict("nope")

    def test_to_dict_drops_none_bbox_and_bounds_lists(self) -> None:
        c = _contract(data_bbox=None)
        d = c.to_dict()
        assert d["data_bbox"] is None
        c2 = _contract(data_bbox=[1.23456789, 2.0, 3.0, 4.0])
        assert c2.to_dict()["data_bbox"] == [1.234568, 2.0, 3.0, 4.0]
