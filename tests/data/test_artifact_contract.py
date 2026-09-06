"""Artifact Contract V3 —— 契约模型与桥接器测试。"""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.lib.data import vocabulary as vocab
from app.lib.data.artifact_contract import (
    CONTRACT_VERSION,
    ArtifactContract,
    ContractDiagnostic,
    LineageInfo,
    from_artifact_record,
    from_db_artifact,
    from_raster_descriptor,
    from_ref_descriptor,
)
from app.services.artifact_registry import ArtifactRecord


class TestContractModel:
    def test_minimal_contract(self):
        c = ArtifactContract(artifact_id="a1", artifact_type="vector")
        assert c.contract_version == CONTRACT_VERSION
        assert c.lifecycle is vocab.LifecycleState.AVAILABLE
        assert c.persistence is vocab.PersistenceTier.SESSION

    def test_unknown_category_is_registered_token(self):
        c = ArtifactContract(artifact_id="a1")  # 缺省 = unknown（诚实未知）
        assert c.artifact_type == "unknown"
        assert c.category is None

    def test_unregistered_category_rejected(self):
        with pytest.raises(ValidationError):
            ArtifactContract(artifact_id="a1", artifact_type="definitely_not_a_category")

    def test_extension_category_accepted_after_registration(self):
        vocab.register_category("seismic_trace")
        c = ArtifactContract(artifact_id="a1", artifact_type="seismic_trace")
        assert c.artifact_type == "seismic_trace"

    def test_schema_alias_roundtrip(self):
        c = ArtifactContract(artifact_id="a1", schema={"pop": {"type": "number"}})
        assert c.data_schema == {"pop": {"type": "number"}}

    def test_bounded_collections(self):
        c = ArtifactContract(
            artifact_id="a1",
            statistics={f"f{i}": i for i in range(100)},
            data_schema={f"f{i}": "number" for i in range(100)},
            diagnostics=[ContractDiagnostic(code=f"d{i}") for i in range(20)],
        )
        assert len(c.statistics) <= 32
        assert len(c.data_schema) <= 64
        assert len(c.diagnostics) <= 8

    def test_extent_normalization(self):
        assert ArtifactContract(artifact_id="a", extent=[1, "2", 3.0, 4]).extent == [1.0, 2.0, 3.0, 4.0]
        assert ArtifactContract(artifact_id="a", extent=[1, 2, 3]).extent is None

    def test_summary_bounded_and_stable(self):
        import json

        c = from_artifact_record(
            ArtifactRecord(
                artifact_id="ref:geojson-x",
                artifact_type="polygon_feature_set",
                producer_tool="buffer",
                feature_count=5000,
                bbox=[1, 2, 3, 4],
                metadata={"content_fingerprint": "f" * 64, "size_bytes": 123456},
            )
        )
        s = c.summary()
        assert len(json.dumps(s, ensure_ascii=False)) <= 1600
        assert s["artifact_type"] == "vector"
        assert s["feature_count"] == 5000
        assert s["quality_hint"] == "valid"


class TestArtifactRecordBridge:
    def _record(self, **kw):
        base = dict(
            artifact_id="ref:geojson-abc",
            artifact_type="stats_table",
            session_id="s1",
            producer_tool="aggregate",
            producer_capability="admin_aggregate",
            inputs=["ref:geojson-in-1", "ref:geojson-in-2"],
            status="valid",
            bbox=[116.0, 39.0, 117.0, 40.0],
            crs="EPSG:4326",
            feature_count=42,
            created_at=1700000000.0,
            updated_at=1700000100.0,
            metadata={"content_fingerprint": "cf" * 32},
        )
        base.update(kw)
        return ArtifactRecord(**base)

    def test_full_projection(self):
        c = from_artifact_record(self._record())
        assert c.artifact_id == "ref:geojson-abc"
        assert c.artifact_type == "table"           # 细类型 stats_table → 粗类
        assert c.artifact_subtype == "stats_table"
        assert c.lifecycle is vocab.LifecycleState.READY
        assert c.stable_identity == "cf" * 32       # 内容指纹即稳定身份
        assert c.fingerprint.content == "cf" * 32
        assert c.lineage.parents == ["ref:geojson-in-1", "ref:geojson-in-2"]
        assert c.produced_by.tool == "aggregate"
        assert c.produced_by.capability == "admin_aggregate"
        assert c.crs == "EPSG:4326"
        assert c.extent == [116.0, 39.0, 117.0, 40.0]
        assert c.created_at == datetime.fromtimestamp(1700000000.0, tz=timezone.utc)

    def test_status_projection(self):
        assert from_artifact_record(self._record(status="stale")).lifecycle is vocab.LifecycleState.STALE
        assert from_artifact_record(self._record(status="expired")).lifecycle is vocab.LifecycleState.DELETED
        assert from_artifact_record(self._record(status="failed")).lifecycle is vocab.LifecycleState.ERROR

    def test_empty_payload_diagnostic(self):
        c = from_artifact_record(self._record(empty=True, feature_count=0))
        assert c.is_empty()
        assert c.quality_status_hint() == "warning"

    def test_role_defaults_and_explicit_override(self):
        # 有上游输入的表 → intermediate 缺省
        c = from_artifact_record(self._record())
        assert c.logical_role in (vocab.LogicalRole.INTERMEDIATE, vocab.LogicalRole.DERIVED)
        # 显式角色覆盖
        c2 = from_artifact_record(
            self._record(metadata={"content_fingerprint": "x", "logical_role": "boundary"})
        )
        assert c2.logical_role is vocab.LogicalRole.BOUNDARY

    def test_dict_form_record(self):
        rec = self._record()
        c = from_artifact_record(rec.to_dict())
        assert c.artifact_subtype == "stats_table"
        assert c.lifecycle is vocab.LifecycleState.READY


class TestRefDescriptorBridge:
    def test_vector_projection(self):
        c = from_ref_descriptor(
            {
                "ref_id": "ref:geojson-p",
                "feature_count": 3,
                "geometry_types": ["Point"],
                "bbox": [1, 2, 3, 4],
                "estimated_bytes": 1300,
                "content_revision": 2,
                "field_schema": {"name": {"type": "string"}},
                "field_schema_complete": True,
            }
        )
        assert c.artifact_type == "vector"
        assert c.geometry_kind == "point"
        assert c.logical_role is vocab.LogicalRole.SOURCE
        assert c.version == 2
        assert c.data_schema == {"name": {"type": "string"}}

    def test_empty_payload_flagged(self):
        c = from_ref_descriptor({"ref_id": "ref:geojson-e", "feature_count": 0, "geometry_types": []})
        assert c.is_empty()

    def test_truncated_schema_diagnostic(self):
        c = from_ref_descriptor(
            {"ref_id": "ref:geojson-t", "feature_count": 1, "field_schema_complete": False}
        )
        assert any(d.code == "field_schema_truncated" for d in c.diagnostics)

    def test_raster_capable_projection(self):
        c = from_ref_descriptor(
            {"ref_id": "ref:raster-x", "feature_count": 0, "raster_capable": True}
        )
        assert c.geometry_kind == "raster"
        assert c.artifact_type == "raster"


class TestDbArtifactBridge:
    class _Row:
        id = "art_abc123"
        artifact_type = "raster"
        format = "geotiff"
        crs = "EPSG:4326"
        storage_ref = "ref:geojson-session-1"
        content_fingerprint = "dff" * 20
        layer_id = 7
        upload_record_id = None
        created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        updated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)

    def test_promoted_projection(self):
        c = from_db_artifact(self._Row())
        assert c.artifact_type == "raster"
        assert c.lifecycle is vocab.LifecycleState.READY
        assert c.persistence is vocab.PersistenceTier.WORKSPACE
        assert c.stable_identity == "dff" * 20
        assert "layer_id:7" in c.lineage.dependencies

    def test_unpromoted_warns(self):
        class Row(self._Row):
            content_fingerprint = None

        c = from_db_artifact(Row())
        assert any(d.code == "payload_not_promoted" for d in c.diagnostics)
        assert c.lifecycle is vocab.LifecycleState.AVAILABLE


class TestRasterDescriptorBridge:
    def test_projection(self):
        c = from_raster_descriptor(
            {
                "ref_id": "ref:raster/abc",
                "width": 256,
                "height": 128,
                "band_count": 3,
                "dtype": "uint8",
                "crs": "EPSG:4326",
                "bounds": [0.0, 0.0, 1.0, 1.0],
                "resolution_x": 0.01,
            }
        )
        assert c.artifact_type == "raster"
        assert c.raster.width == 256
        assert c.raster.bands == 3
        assert c.raster.dtype == "uint8"
        assert c.extent == [0.0, 0.0, 1.0, 1.0]


def test_lineage_info_bounded():
    li = LineageInfo(parents=[f"ref:x{i}" for i in range(30)])
    assert len(li.parents) == 16
    assert li.parents_truncated is True
