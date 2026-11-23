"""Regression coverage for data-access pack loading after descriptor merges."""
from __future__ import annotations


def test_data_access_pack_keeps_adjacent_descriptors_separate():
    from app.lib.gis.capabilities.data_access import CAPABILITIES
    from app.lib.gis.capability_registry import CapabilityDescriptor

    assert all(isinstance(capability, CapabilityDescriptor) for capability in CAPABILITIES)
    by_id = {capability.id: capability for capability in CAPABILITIES}
    assert len(by_id) == len(CAPABILITIES)
    assert {"linked_exploration", "coordinate_operations", "data_join_authoring"} <= by_id.keys()
    assert by_id["coordinate_operations"].category == "data_access"
    assert by_id["coordinate_operations"].output_artifact_types == ["feature_collection"]


def test_data_join_authoring_declares_enriched_layer_output():
    """data_join_authoring 的 durable 产物有 GeoJSON 富化图层与行集两形
    （assemble_feature_collection / assemble_rows），能力面只声明
    stats_table（几何族 table）会把富化图层误判成纯表。"""
    from app.lib.gis.capabilities.data_access import CAPABILITIES

    cap = next(c for c in CAPABILITIES if c.id == "data_join_authoring")
    assert "stats_table" in cap.output_artifact_types
    assert "feature_collection" in cap.output_artifact_types


def test_builtin_registry_loads_data_access_and_downstream_packs():
    from app.lib.gis.capability_registry import CapabilityRegistry

    registry = CapabilityRegistry()
    registry.load_builtins()
    for capability_id in (
        "linked_exploration", "coordinate_operations", "data_join_authoring",
        "sar_speckle_filtering", "sar_radiometric_calibration", "raster_change_detection",
    ):
        assert registry.has(capability_id), capability_id
